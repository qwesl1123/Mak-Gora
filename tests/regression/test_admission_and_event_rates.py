"""Deterministic regressions for bounded admission and Socket.IO event rates."""
from __future__ import annotations

import ast
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import eventlet

from harness import MatchState, SOCKETS, make_match, submit_turn


availability = sys.modules["games.duel.availability"]
state = SOCKETS.state


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _policy(**overrides: Any):
    rate = availability.EventRate(2, 10, 2)
    values = {
        "max_queued_sids": 8,
        "max_active_rooms": 4,
        "queue_ttl_seconds": 10,
        "max_limiter_sids": 20,
        "throttle_warning_cooldown_seconds": 2,
        "queue_rate": rate,
        "prep_rate": rate,
        "lock_rate": rate,
        "action_rate": rate,
        "chat_rate": rate,
    }
    values.update(overrides)
    return availability.AdmissionPolicy(**values)


@contextmanager
def _isolated_admission(
    policy=None,
    clock: FakeClock | None = None,
) -> Iterator[None]:
    active_policy = policy or _policy()
    active_clock = clock or FakeClock()
    with state.state_lock:
        saved = {
            "queue": list(state.duel_queue),
            "queued_at": dict(state.queued_at_by_sid),
            "rooms": dict(state.duel_rooms),
            "sid_to_room": dict(state.sid_to_room),
            "limiters": dict(state.limiter_records),
            "room_sequence": state._next_room_sequence,
            "policy": state.ADMISSION_POLICY,
            "clock": state.monotonic_clock,
        }
        state.duel_queue.clear()
        state.queued_at_by_sid.clear()
        state.duel_rooms.clear()
        state.sid_to_room.clear()
        state.limiter_records.clear()
        state._next_room_sequence = 0
        state.ADMISSION_POLICY = active_policy
        state.monotonic_clock = active_clock
    try:
        yield
    finally:
        with state.state_lock:
            state.duel_queue[:] = saved["queue"]
            state.queued_at_by_sid.clear()
            state.queued_at_by_sid.update(saved["queued_at"])
            state.duel_rooms.clear()
            state.duel_rooms.update(saved["rooms"])
            state.sid_to_room.clear()
            state.sid_to_room.update(saved["sid_to_room"])
            state.limiter_records.clear()
            state.limiter_records.update(saved["limiters"])
            state._next_room_sequence = saved["room_sequence"]
            state.ADMISSION_POLICY = saved["policy"]
            state.monotonic_clock = saved["clock"]


class _FakeSocketIO:
    def __init__(self, *, assert_unlocked: bool = False) -> None:
        self.handlers: dict[str, Any] = {}
        self.emitted: list[tuple[str, Any, dict[str, Any]]] = []
        self.assert_unlocked = assert_unlocked

    def on(self, event: str):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register

    def emit(self, event: str, payload: Any = None, **kwargs: Any) -> None:
        if self.assert_unlocked:
            assert not state.state_lock.locked(), (
                "Socket.IO transport must run after the admission lock is released"
            )
        self.emitted.append((event, payload, kwargs))


@contextmanager
def _registered_handlers(
    *,
    assert_unlocked: bool = False,
) -> Iterator[
    tuple[
        _FakeSocketIO,
        list[tuple[str, str, Any, dict[str, Any]]],
        list[tuple[str, str]],
        list[tuple[str, str]],
    ]
]:
    socketio = _FakeSocketIO(assert_unlocked=assert_unlocked)
    direct_emits: list[tuple[str, str, Any, dict[str, Any]]] = []
    joins: list[tuple[str, str]] = []
    leaves: list[tuple[str, str]] = []
    original_emit = SOCKETS.emit
    original_join_room = SOCKETS.join_room
    original_leave_room = SOCKETS.leave_room
    original_sid = SOCKETS.request.sid

    def assert_lock_released() -> None:
        if assert_unlocked:
            assert not state.state_lock.locked(), (
                "Transport must not run while the admission lock is held"
            )

    def direct_emit(
        event: str,
        payload: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        assert_lock_released()
        direct_emits.append((SOCKETS.request.sid, event, payload, kwargs))

    def join(room_id: str, *, sid: str) -> None:
        assert_lock_released()
        joins.append((room_id, sid))

    def leave(room_id: str, *, sid: str) -> None:
        assert_lock_released()
        leaves.append((room_id, sid))

    SOCKETS.emit = direct_emit
    SOCKETS.join_room = join
    SOCKETS.leave_room = leave
    SOCKETS.register_duel_socket_handlers(socketio)
    try:
        yield socketio, direct_emits, joins, leaves
    finally:
        SOCKETS.emit = original_emit
        SOCKETS.join_room = original_join_room
        SOCKETS.leave_room = original_leave_room
        SOCKETS.request.sid = original_sid


def _call(socketio: _FakeSocketIO, sid: str, event: str, *payload: Any) -> None:
    SOCKETS.request.sid = sid
    socketio.handlers[event](*payload)


def _occupy_room(policy, suffix: str = "0") -> MatchState:
    return _create_test_room(
        f"occupied-p1-{suffix}",
        f"occupied-p2-{suffix}",
        int(suffix or "0") + 1,
        policy,
    )


def _create_test_room(p1: str, p2: str, seed: int, policy) -> MatchState:
    with state.state_lock:
        if len(state.duel_rooms) >= policy.max_active_rooms:
            raise RuntimeError("Duel room capacity has been reached")
        return state._create_room_locked(p1, p2, seed)


def scenario_admission_queue_cap_boundaries() -> bool:
    clock = FakeClock(100)
    policy = _policy(max_queued_sids=3, max_active_rooms=1)
    with _isolated_admission(policy, clock):
        _occupy_room(policy)
        for sid in ("queue-a", "queue-b", "queue-c"):
            result = state.request_matchmaking(sid, seed=1)
            assert result.status == "room_full"
        assert state.duel_queue == ["queue-a", "queue-b", "queue-c"]
        assert set(state.queued_at_by_sid) == set(state.duel_queue)

        rejected = state.request_matchmaking("queue-d", seed=2)
        assert rejected.status == "queue_full"
        assert "queue-d" not in state.queued_at_by_sid

        original_timestamp = state.queued_at_by_sid["queue-a"]
        clock.advance(1)
        duplicate = state.request_matchmaking("queue-a", seed=3)
        assert duplicate.status == "already_queued"
        assert state.duel_queue.count("queue-a") == 1
        assert state.queued_at_by_sid["queue-a"] == original_timestamp

        assert state.dequeue("queue-a") is True
        assert state.request_matchmaking("queue-d", seed=4).status == "room_full"
        assert state.disconnect_sid("queue-b") is None
        assert state.request_matchmaking("queue-e", seed=5).status == "room_full"

        clock.set(200)
        admitted = state.request_matchmaking("queue-f", seed=6)
        assert admitted.status == "room_full"
        assert state.duel_queue == ["queue-f"]
        assert state.queued_at_by_sid == {"queue-f": 200}
    return True


def scenario_admission_lazy_queue_expiration() -> bool:
    clock = FakeClock(0)
    policy = _policy(max_active_rooms=1, queue_ttl_seconds=10)
    with _isolated_admission(policy, clock):
        _occupy_room(policy)
        state.request_matchmaking("fresh", seed=1)
        assert state.expire_queued_sids(now=9.999) == ()
        assert state.duel_queue == ["fresh"]
        assert state.expire_queued_sids(now=10) == ("fresh",)
        assert state.duel_queue == [] and state.queued_at_by_sid == {}

        clock.set(20)
        state.request_matchmaking("expired", seed=2)
        clock.set(30)
        newcomer = state.request_matchmaking("newcomer", seed=3)
        assert newcomer.match is None
        assert state.duel_queue == ["newcomer"]
        assert "expired" not in state.queued_at_by_sid

        clock.set(31)
        requeued = state.request_matchmaking("expired", seed=4)
        assert requeued.status == "room_full"
        assert state.queued_at_by_sid["expired"] == 31

        clock.set(41.001)
        assert set(state.expire_queued_sids()) == {"newcomer", "expired"}
        assert not state.duel_queue and not state.queued_at_by_sid

        socket_source = Path(SOCKETS.__file__).read_text(encoding="utf-8")
        assert "start_background_task" not in socket_source
    return True


def scenario_admission_room_cap_and_capacity_recovery() -> bool:
    clock = FakeClock(50)
    policy = _policy(max_active_rooms=2, max_queued_sids=6)
    with _isolated_admission(policy, clock):
        first = _occupy_room(policy, "1")
        _occupy_room(policy, "2")
        try:
            _occupy_room(policy, "3")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Room-cap-plus-one creation must fail")

        for sid in ("wait-1", "wait-2", "wait-3", "wait-4"):
            state.request_matchmaking(sid, seed=10)
        assert state.duel_queue == ["wait-1", "wait-2", "wait-3", "wait-4"]
        assert all(sid not in state.sid_to_room for sid in state.duel_queue)

        assert state.cleanup_room(first.room_id) is first
        replacement = state.try_pair_waiting(seed=11)
        assert replacement is not None
        assert replacement.players == ["wait-1", "wait-2"]
        assert state.duel_queue == ["wait-3", "wait-4"]
        assert len(state.duel_rooms) == policy.max_active_rooms
        assert state.sid_to_room["wait-1"] == replacement.room_id
        assert state.sid_to_room["wait-2"] == replacement.room_id
    return True


def scenario_admission_room_ids_are_lifetime_unique() -> bool:
    policy = _policy(max_active_rooms=1)
    with _isolated_admission(policy, FakeClock()):
        room_ids: list[str] = []
        for p1, p2 in (
            ("same-p1", "same-p2"),
            ("same-p1", "same-p2"),
            ("abcde-first", "abcde-second"),
            ("abcde-third", "abcde-fourth"),
        ):
            state.request_matchmaking(p1, seed=1)
            result = state.request_matchmaking(p2, seed=2)
            assert result.match is not None
            room_ids.append(result.match.room_id)
            state.cleanup_room(result.match.room_id)

        assert len(room_ids) == len(set(room_ids))
        sequences = [int(room_id.removeprefix("duel-"), 16) for room_id in room_ids]
        assert sequences == sorted(sequences)
        assert sequences == list(range(1, len(room_ids) + 1))
        assert state._next_room_sequence == len(room_ids)
    return True


def scenario_admission_per_event_token_buckets() -> bool:
    clock = FakeClock(0)
    policy = _policy()
    with _isolated_admission(policy, clock):
        for index, event in enumerate(sorted(state.PROTECTED_EVENTS)):
            sid = f"rate-{index}"
            assert state.consume_event_token(sid, event).allowed
            assert state.consume_event_token(sid, event).allowed
            assert not state.consume_event_token(sid, event).allowed
            clock.advance(5)
            assert state.consume_event_token(sid, event).allowed

        assert state.consume_event_token("isolated-a", state.QUEUE_EVENT).allowed
        assert state.consume_event_token("isolated-a", state.QUEUE_EVENT).allowed
        assert not state.consume_event_token("isolated-a", state.QUEUE_EVENT).allowed
        assert state.consume_event_token("isolated-b", state.QUEUE_EVENT).allowed
        assert state.consume_event_token("isolated-a", state.PREP_EVENT).allowed

    one_event = availability.EventRate(1, 10, 1)
    handler_policy = _policy(
        max_active_rooms=4,
        queue_rate=one_event,
        prep_rate=one_event,
        lock_rate=one_event,
        action_rate=one_event,
        chat_rate=one_event,
    )
    with _isolated_admission(handler_policy, FakeClock()):
        prep_match = _create_test_room("prep-sid", "prep-peer", 1, handler_policy)
        action_match = _create_test_room("action-sid", "action-peer", 2, handler_policy)
        action_match.phase = "combat"
        _create_test_room("chat-sid", "chat-peer", 3, handler_policy)
        with _registered_handlers() as (socketio, direct_emits, _joins, _leaves):
            _call(socketio, "queue-sid", state.QUEUE_EVENT, {"attacker": True})
            _call(socketio, "queue-sid", state.QUEUE_EVENT)
            assert "queue-sid" not in state.duel_queue

            _call(socketio, "unauthorized-sid", state.PREP_EVENT, {"class_id": "warrior"})
            unauthorized_before = len(direct_emits)
            _call(socketio, "unauthorized-sid", state.PREP_EVENT, {"class_id": "warrior"})
            assert len(direct_emits) == unauthorized_before + 1
            assert direct_emits[-1][2] == SOCKETS.THROTTLE_WARNING

            before_pick = dict(prep_match.picks)
            _call(socketio, "prep-sid", state.PREP_EVENT, ["malformed"])
            _call(socketio, "prep-sid", state.PREP_EVENT, {"class_id": "warrior"})
            assert prep_match.picks == before_pick

            _call(socketio, "prep-sid", state.LOCK_EVENT, {"hidden": True})
            _call(socketio, "prep-sid", state.LOCK_EVENT)
            assert not prep_match.locked_in.get("prep-sid")

            _call(socketio, "action-sid", state.ACTION_EVENT, {"hidden": True})
            _call(socketio, "action-sid", state.ACTION_EVENT, {"ability_id": "pass_turn"})
            assert action_match.submitted == {}

            _call(socketio, "chat-sid", state.CHAT_EVENT, {"message": "hidden"})
            socket_before = len(socketio.emitted)
            _call(socketio, "chat-sid", state.CHAT_EVENT, "hello")
            assert len(socketio.emitted) == socket_before
            assert not any(event == "duel_chat" for event, _payload, _kwargs in socketio.emitted)
    return True


def scenario_admission_throttle_warning_suppression() -> bool:
    clock = FakeClock(0)
    rate = availability.EventRate(1, 10, 1)
    policy = _policy(queue_rate=rate, throttle_warning_cooldown_seconds=2)
    with _isolated_admission(policy, clock):
        with _registered_handlers() as (socketio, direct_emits, _joins, _leaves):
            attacker_payload = {"secret": "must-not-echo"}
            _call(socketio, "spam-sid", state.QUEUE_EVENT)
            queued_at = state.queued_at_by_sid["spam-sid"]
            for _ in range(1000):
                _call(socketio, "spam-sid", state.QUEUE_EVENT, attacker_payload)
            warnings = [entry for entry in direct_emits if entry[2] == SOCKETS.THROTTLE_WARNING]
            assert len(warnings) == 1

            clock.set(1.999)
            _call(socketio, "spam-sid", state.QUEUE_EVENT, attacker_payload)
            assert sum(entry[2] == SOCKETS.THROTTLE_WARNING for entry in direct_emits) == 1
            clock.set(2)
            _call(socketio, "spam-sid", state.QUEUE_EVENT, attacker_payload)
            warnings = [entry for entry in direct_emits if entry[2] == SOCKETS.THROTTLE_WARNING]
            assert len(warnings) == 2
            assert all("secret" not in str(payload) for _sid, _event, payload, _kwargs in warnings)
            assert state.queued_at_by_sid["spam-sid"] == queued_at
    return True


def scenario_admission_limiter_record_hard_cap() -> bool:
    clock = FakeClock(5)
    policy = _policy(max_limiter_sids=2)
    with _isolated_admission(policy, clock):
        state.consume_event_token("a-sid", state.QUEUE_EVENT)
        state.consume_event_token("b-sid", state.PREP_EVENT)
        assert len(state.limiter_records) == 2

        state.consume_event_token("c-sid", state.LOCK_EVENT)
        assert len(state.limiter_records) == 2
        assert set(state.limiter_records) == {"b-sid", "c-sid"}
        assert set(state.limiter_records["b-sid"].buckets) <= state.PROTECTED_EVENTS
        assert set(state.limiter_records["c-sid"].buckets) <= state.PROTECTED_EVENTS

        clock.set(6)
        state.consume_event_token("b-sid", state.CHAT_EVENT)
        state.consume_event_token("d-sid", state.ACTION_EVENT)
        assert set(state.limiter_records) == {"b-sid", "d-sid"}

        before = set(state.limiter_records)
        try:
            state.consume_event_token("attacker", "attacker_event")
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown event categories must fail closed")
        assert set(state.limiter_records) == before

        state.disconnect_sid("b-sid")
        state.disconnect_sid("b-sid")
        assert "b-sid" not in state.limiter_records
        state.consume_event_token("disconnect-sid", state.CHAT_EVENT)
        assert "disconnect-sid" in state.limiter_records
        state.disconnect_sid("disconnect-sid")
        state.disconnect_sid("disconnect-sid")
        assert "disconnect-sid" not in state.limiter_records
        assert len(state.limiter_records) <= policy.max_limiter_sids
    return True


def scenario_admission_eventlet_lock_and_concurrency() -> bool:
    clock = FakeClock(0)
    policy = _policy(max_queued_sids=10, max_active_rooms=3)
    with _isolated_admission(policy, clock):
        completed: list[bool] = []
        state.state_lock.acquire()
        try:
            blocked = eventlet.spawn(
                lambda: completed.append(state.dequeue("not-queued"))
            )
            eventlet.sleep(0)
            assert completed == []
        finally:
            state.state_lock.release()
        blocked.wait()
        assert completed == [False]

        attempts = [
            eventlet.spawn(
                state.request_matchmaking,
                f"green-{index:02d}",
                index,
                now=clock(),
                policy=policy,
            )
            for index in range(30)
        ]
        for attempt in attempts:
            attempt.wait()

        assert len(state.duel_rooms) <= policy.max_active_rooms
        assert len(state.duel_queue) <= policy.max_queued_sids
        assert len(state.duel_queue) == len(set(state.duel_queue))
        assert set(state.queued_at_by_sid) == set(state.duel_queue)
        expected_mappings = {
            sid: match.room_id
            for match in state.duel_rooms.values()
            for sid in match.players
        }
        assert state.sid_to_room == expected_mappings
    return True


def scenario_admission_capacity_recovery_transport() -> bool:
    policy = _policy(max_active_rooms=1)
    with _isolated_admission(policy, FakeClock()):
        old_match = _create_test_room("old-p1", "old-p2", 1, policy)
        state.consume_event_token("old-p1", state.CHAT_EVENT)
        state.request_matchmaking("replacement-p1", 2)
        state.request_matchmaking("replacement-p2", 3)
        assert state.duel_queue == ["replacement-p1", "replacement-p2"]

        with _registered_handlers(assert_unlocked=True) as (
            socketio,
            _direct,
            joins,
            leaves,
        ):
            _call(socketio, "old-p1", "disconnect", "client disconnect")

        assert old_match.room_id not in state.duel_rooms
        assert "old-p1" not in state.limiter_records
        assert "old-p1" not in state.sid_to_room and "old-p2" not in state.sid_to_room
        assert len(state.duel_rooms) == 1
        replacement = next(iter(state.duel_rooms.values()))
        assert replacement.players == ["replacement-p1", "replacement-p2"]
        assert replacement.room_id != old_match.room_id
        assert leaves == [(old_match.room_id, "old-p1")]
        assert joins == [
            (replacement.room_id, "replacement-p1"),
            (replacement.room_id, "replacement-p2"),
        ]
        role_targets = {
            kwargs.get("to")
            for event, _payload, kwargs in socketio.emitted
            if event == "duel_role"
        }
        snapshot_targets = {
            kwargs.get("to")
            for event, _payload, kwargs in socketio.emitted
            if event == "duel_snapshot"
        }
        assert role_targets == snapshot_targets == {"replacement-p1", "replacement-p2"}
        assert any(
            event == "duel_prep_options" and kwargs.get("to") == replacement.room_id
            for event, _payload, kwargs in socketio.emitted
        )
    return True


def scenario_admission_two_tab_same_pc_flow() -> bool:
    clock = FakeClock(1000)
    policy = availability.DEFAULT_ADMISSION_POLICY
    with _isolated_admission(policy, clock):
        with _registered_handlers(assert_unlocked=True) as (
            socketio,
            _direct,
            _joins,
            _leaves,
        ):
            tab_one, tab_two = "same-pc-tab-one", "same-pc-tab-two"
            _call(socketio, tab_one, state.QUEUE_EVENT)
            _call(socketio, tab_two, state.QUEUE_EVENT)
            match = state.get_match_by_sid(tab_one)
            assert match is not None and state.get_match_by_sid(tab_two) is match

            _call(socketio, tab_one, state.PREP_EVENT, {
                "class_id": "warrior",
                "items": {"weapon": "steel_long_sword"},
            })
            _call(socketio, tab_two, state.PREP_EVENT, {
                "class_id": "mage",
                "items": {"weapon": "staff_of_immortality"},
            })
            _call(socketio, tab_one, state.LOCK_EVENT)
            _call(socketio, tab_two, state.LOCK_EVENT)
            assert match.phase == "combat"

            _call(socketio, tab_one, state.ACTION_EVENT, {"ability_id": "pass_turn"})
            _call(socketio, tab_two, state.ACTION_EVENT, {"ability_id": "pass_turn"})
            assert match.turn == 1 and match.submitted == {}

            _call(socketio, tab_one, state.CHAT_EVENT, "gg")
            _call(socketio, tab_two, state.CHAT_EVENT, "wp")
            assert any(
                event == "duel_chat"
                and payload.get("message") == "gg"
                and kwargs.get("to") == match.room_id
                for event, payload, kwargs in socketio.emitted
            )
            assert set(state.limiter_records[tab_one].buckets) == state.PROTECTED_EVENTS
            assert set(state.limiter_records[tab_two].buckets) == state.PROTECTED_EVENTS
    return True


def scenario_admission_ordinary_duel_and_source_guardrails() -> bool:
    match = make_match("warrior", "mage", seed=123)
    submit_turn(match, "pass_turn", "pass_turn")
    assert match.turn == 1 and match.winner is None
    assert match.log == [
        "Turn 1",
        "p1_si uses their bare hands to cast Pass Turn. Passes the turn.",
        "p2_si uses their bare hands to cast Pass Turn. Passes the turn.",
    ]
    snapshot = SOCKETS.snapshot_for(match, match.players[0])
    assert snapshot["turn"] == 1 and snapshot["winner"] is None
    assert snapshot["log_length"] == 3
    assert availability.RESOURCE_LIMITS.socket_max_buffer_bytes == 16 * 1024
    assert match.log.capacity == availability.RESOURCE_LIMITS.max_retained_log_entries

    state_source = Path(state.__file__).read_text(encoding="utf-8")
    socket_source = Path(SOCKETS.__file__).read_text(encoding="utf-8")
    assert "from eventlet.semaphore import Semaphore" in state_source
    assert "threading.Lock" not in state_source and "threading.RLock" not in state_source
    assert "start_background_task" not in state_source + socket_source
    forbidden_lifecycle_terms = (
        "prep_idle_ttl",
        "prep_absolute_ttl",
        "combat_idle_ttl",
        "combat_absolute_ttl",
        "ended_room_grace_ttl",
        "cleanup_lease",
        "deferred_cleanup",
    )
    assert not any(term in state_source.lower() for term in forbidden_lifecycle_terms)
    assert not any(
        term in socket_source
        for term in ("remote_addr", "X-Forwarded-For", "ProxyFix")
    )

    socket_tree = ast.parse(socket_source)
    direct_queue_accesses = [
        node
        for node in ast.walk(socket_tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"duel_queue", "queued_at_by_sid"}
        and isinstance(node.value, ast.Name)
        and node.value.id == "state"
    ]
    assert direct_queue_accesses == []
    return True


def scenario_admission_exact_rate_ttl_and_warning_boundaries() -> bool:
    clock = FakeClock(0)
    policy = _policy(queue_ttl_seconds=10, throttle_warning_cooldown_seconds=2)
    with _isolated_admission(policy, clock):
        assert state.consume_event_token("boundary", state.QUEUE_EVENT).allowed
        assert state.limiter_records["boundary"].buckets[state.QUEUE_EVENT].tokens == 1
        assert state.consume_event_token("boundary", state.QUEUE_EVENT).allowed
        assert state.limiter_records["boundary"].buckets[state.QUEUE_EVENT].tokens == 0
        assert not state.consume_event_token("boundary", state.QUEUE_EVENT).allowed

        clock.set(4.999)
        assert not state.consume_event_token("boundary", state.QUEUE_EVENT).allowed
        clock.set(5)
        assert state.consume_event_token("boundary", state.QUEUE_EVENT).allowed
        clock.set(4)
        assert not state.consume_event_token("boundary", state.QUEUE_EVENT).allowed

        state.request_matchmaking("ttl-before", seed=1, now=10)
        assert state.expire_queued_sids(now=19.999) == ()
        assert state.expire_queued_sids(now=20) == ("ttl-before",)
        state.request_matchmaking("ttl-after", seed=2, now=30)
        assert state.expire_queued_sids(now=40.001) == ("ttl-after",)

        clock.set(50)
        assert state.consume_event_token("warning", state.CHAT_EVENT).allowed
        assert state.consume_event_token("warning", state.CHAT_EVENT).allowed
        first = state.consume_event_token("warning", state.CHAT_EVENT)
        assert not first.allowed and first.emit_warning
        clock.set(51.999)
        before = state.consume_event_token("warning", state.CHAT_EVENT)
        assert not before.allowed and not before.emit_warning
        clock.set(52)
        exact = state.consume_event_token("warning", state.CHAT_EVENT)
        assert not exact.allowed and exact.emit_warning
    return True


def scenario_admission_policy_configuration_validation() -> bool:
    defaults = availability.DEFAULT_ADMISSION_POLICY
    assert defaults.max_queued_sids == 100
    assert defaults.max_active_rooms == 50
    assert defaults.queue_ttl_seconds == 15 * 60
    assert defaults.max_limiter_sids == 1000
    assert defaults.throttle_warning_cooldown_seconds == 2
    assert defaults.queue_rate == availability.EventRate(3, 10, 3)
    assert defaults.prep_rate == availability.EventRate(12, 10, 12)
    assert defaults.lock_rate == availability.EventRate(4, 10, 4)
    assert defaults.action_rate == availability.EventRate(10, 10, 8)
    assert defaults.chat_rate == availability.EventRate(8, 10, 5)

    configured = availability.load_admission_policy({
        "MAKGORA_MAX_QUEUED_SIDS": "7",
        "MAKGORA_MAX_ACTIVE_ROOMS": "3",
        "MAKGORA_QUEUE_TTL_SECONDS": "12.5",
        "MAKGORA_MAX_LIMITER_SIDS": "9",
        "MAKGORA_THROTTLE_WARNING_COOLDOWN_SECONDS": "1.25",
        "MAKGORA_CHAT_RATE_EVENTS": "4",
        "MAKGORA_CHAT_RATE_WINDOW_SECONDS": "6.5",
        "MAKGORA_CHAT_RATE_BURST": "2",
    })
    assert configured.max_queued_sids == 7
    assert configured.max_active_rooms == 3
    assert configured.queue_ttl_seconds == 12.5
    assert configured.max_limiter_sids == 9
    assert configured.throttle_warning_cooldown_seconds == 1.25
    assert configured.chat_rate == availability.EventRate(4, 6.5, 2)

    invalid_environments = (
        {"MAKGORA_MAX_QUEUED_SIDS": "0"},
        {"MAKGORA_MAX_ACTIVE_ROOMS": "not-an-integer"},
        {"MAKGORA_QUEUE_TTL_SECONDS": "nan"},
        {"MAKGORA_MAX_LIMITER_SIDS": "-1"},
        {"MAKGORA_THROTTLE_WARNING_COOLDOWN_SECONDS": "inf"},
        {"MAKGORA_QUEUE_RATE_EVENTS": "0"},
        {"MAKGORA_PREP_RATE_WINDOW_SECONDS": "0"},
        {"MAKGORA_LOCK_RATE_BURST": "0"},
    )
    for environment in invalid_environments:
        try:
            availability.load_admission_policy(environment)
        except ValueError as exc:
            assert "Invalid Mak'Gora admission configuration" in str(exc)
        else:
            raise AssertionError(f"Invalid admission policy was accepted: {environment}")
    return True
