"""Deterministic regressions for application-layer availability containment."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import importlib.util
from pathlib import Path
import sys
import threading
import types
from typing import Any, Iterator

from harness import MatchState, PlayerBuild, SOCKETS, make_match, resolver
from games.duel.availability import (
    AvailabilityPolicy,
    BoundedCombatLog,
    DEFAULT_AVAILABILITY_POLICY,
    EventThrottlePolicy,
    MIN_SOCKET_BUFFER_BYTES,
    SNAPSHOT_LOG_ENTRY_LIMIT,
    load_availability_policy,
)


STATE = SOCKETS.state
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EQUIPMENT_SLOTS = ("weapon", "armor", "trinket")
_P1_TEST_ITEMS = {
    "weapon": "steel_long_sword",
    "armor": "leather_armor",
    "trinket": "unstable_arcanocrystal",
}
_P2_TEST_ITEMS = {
    "weapon": "steel_long_sword",
    "armor": "cloth_armor",
    "trinket": "unstable_arcanocrystal",
}
_TICK = 0.001


class _FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def set(self, now: float) -> None:
        self.now = float(now)

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _ThreadLocalRequest:
    """Minimal request proxy for deterministic concurrent handler tests."""

    def __init__(self) -> None:
        self._local = threading.local()

    @property
    def sid(self) -> str | None:
        return getattr(self._local, "sid", None)

    @sid.setter
    def sid(self, value: str | None) -> None:
        self._local.sid = value


class _FakeSocketIO:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.emitted: list[tuple[str, Any, dict[str, Any]]] = []
        self.joined: list[tuple[str, str | None]] = []
        self.left: list[tuple[str, str | None]] = []
        self.closed_rooms: list[str] = []
        self.background_tasks: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.sleeps: list[float] = []

    def on(self, event: str):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register

    def emit(self, event: str, payload: Any = None, **kwargs: Any) -> None:
        self.emitted.append((event, payload, kwargs))

    def close_room(self, room_id: str) -> None:
        self.closed_rooms.append(room_id)

    def start_background_task(self, target: Any, *args: Any, **kwargs: Any) -> object:
        self.background_tasks.append((target, args, kwargs))
        return object()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))


@dataclass
class _ServerHarness:
    socketio: _FakeSocketIO
    clock: _FakeClock
    direct_emits: list[tuple[str | None, str, Any, dict[str, Any]]]


def _policy(**overrides: Any) -> AvailabilityPolicy:
    return replace(AvailabilityPolicy(), **overrides).validate()


def _generous_policy(**overrides: Any) -> AvailabilityPolicy:
    generous = EventThrottlePolicy(events=1000, window_seconds=1, burst=1000)
    return _policy(
        queue_throttle=generous,
        lock_throttle=generous,
        prep_throttle=generous,
        action_throttle=generous,
        chat_throttle=generous,
        **overrides,
    )


def _clear_state() -> None:
    with STATE.state_lock:
        STATE.duel_queue.clear()
        STATE.queued_at_by_sid.clear()
        STATE.duel_rooms.clear()
        STATE.sid_to_room.clear()
        STATE.limiter_records.clear()


@contextmanager
def _thread_local_socket_request() -> Iterator[None]:
    original_request = SOCKETS.request
    SOCKETS.request = _ThreadLocalRequest()
    try:
        yield
    finally:
        SOCKETS.request = original_request


def _start_worker(target: Any) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            target()
        except BaseException as exc:  # pragma: no cover - asserted by the caller
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, errors


def _join_worker(
    worker: threading.Thread,
    errors: list[BaseException],
    *,
    label: str,
) -> None:
    worker.join(timeout=2)
    assert not worker.is_alive(), f"Timed out waiting for {label}"
    assert not errors, f"{label} raised {errors[0]!r}"


@contextmanager
def _isolated_server(
    policy: AvailabilityPolicy | None = None,
    *,
    now: float = 1000.0,
) -> Iterator[_ServerHarness]:
    socketio = _FakeSocketIO()
    clock = _FakeClock(now)
    direct_emits: list[tuple[str | None, str, Any, dict[str, Any]]] = []

    with STATE.state_lock:
        original_queue = list(STATE.duel_queue)
        original_queue_times = dict(STATE.queued_at_by_sid)
        original_rooms = dict(STATE.duel_rooms)
        original_sid_to_room = dict(STATE.sid_to_room)
        original_limiter_records = dict(STATE.limiter_records)
        original_sweeper_started = STATE._sweeper_started
        STATE.duel_queue.clear()
        STATE.queued_at_by_sid.clear()
        STATE.duel_rooms.clear()
        STATE.sid_to_room.clear()
        STATE.limiter_records.clear()
        STATE._sweeper_started = False

    original_policy = STATE.availability_policy
    original_clock = STATE.monotonic_clock
    original_emit = SOCKETS.emit
    original_join_room = SOCKETS.join_room
    original_leave_room = SOCKETS.leave_room
    original_sid = SOCKETS.request.sid
    original_wall_clock = SOCKETS.time.time

    def record_direct_emit(
        event: str,
        payload: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        direct_emits.append((SOCKETS.request.sid, event, payload, kwargs))

    STATE.availability_policy = (policy or AvailabilityPolicy()).validate()
    STATE.monotonic_clock = clock
    SOCKETS.emit = record_direct_emit
    SOCKETS.join_room = lambda room_id, **kwargs: socketio.joined.append(
        (room_id, kwargs.get("sid"))
    )
    SOCKETS.leave_room = lambda room_id, **kwargs: socketio.left.append(
        (room_id, kwargs.get("sid"))
    )
    SOCKETS.time.time = lambda: 123456.789
    SOCKETS.register_duel_socket_handlers(socketio)

    try:
        yield _ServerHarness(socketio, clock, direct_emits)
    finally:
        STATE.availability_policy = original_policy
        STATE.monotonic_clock = original_clock
        SOCKETS.emit = original_emit
        SOCKETS.join_room = original_join_room
        SOCKETS.leave_room = original_leave_room
        SOCKETS.request.sid = original_sid
        SOCKETS.time.time = original_wall_clock
        with STATE.state_lock:
            STATE.duel_queue[:] = original_queue
            STATE.queued_at_by_sid.clear()
            STATE.queued_at_by_sid.update(original_queue_times)
            STATE.duel_rooms.clear()
            STATE.duel_rooms.update(original_rooms)
            STATE.sid_to_room.clear()
            STATE.sid_to_room.update(original_sid_to_room)
            STATE.limiter_records.clear()
            STATE.limiter_records.update(original_limiter_records)
            STATE._sweeper_started = original_sweeper_started


def _dispatch(
    harness: _ServerHarness,
    event: str,
    sid: str,
    *payload_args: Any,
) -> None:
    SOCKETS.request.sid = sid
    try:
        harness.socketio.handlers[event](*payload_args)
    except Exception as exc:  # pragma: no cover - surfaced as a scenario failure
        raise AssertionError(
            f"{event} leaked {type(exc).__name__} for SID {sid}"
        ) from exc


def _canonical_pick(class_id: str) -> dict[str, Any]:
    return {
        "class_id": class_id,
        "items": {slot: None for slot in _EQUIPMENT_SLOTS},
    }


def _create_prep_room(
    p1: str,
    p2: str,
    *,
    seed: int = 8010,
) -> MatchState:
    return STATE.create_room(p1, p2, seed, now=STATE.current_time())


def _create_combat_room(
    p1: str,
    p2: str,
    *,
    p1_class: str = "warrior",
    p2_class: str = "mage",
    seed: int = 8011,
) -> MatchState:
    match = _create_prep_room(p1, p2, seed=seed)
    match.picks[p1] = PlayerBuild(class_id=p1_class)
    match.picks[p2] = PlayerBuild(class_id=p2_class)
    resolver.apply_prep_build(match)
    match.mark_phase_started("combat", STATE.current_time())
    return match


def _queue_and_pair(
    harness: _ServerHarness,
    p1: str,
    p2: str,
) -> MatchState:
    _dispatch(harness, "duel_queue", p1)
    _dispatch(harness, "duel_queue", p2)
    match = STATE.get_match_by_sid(p1)
    assert match is not None and STATE.get_match_by_sid(p2) is match
    assert match.players == [p1, p2]
    return match


def _submit_builds_and_lock(
    harness: _ServerHarness,
    match: MatchState,
    p1_class: str = "warrior",
    p2_class: str = "mage",
) -> None:
    p1, p2 = match.players
    _dispatch(harness, "duel_prep_submit", p1, {"class_id": p1_class})
    _dispatch(harness, "duel_prep_submit", p2, {"class_id": p2_class})
    _dispatch(harness, "duel_prep_submit", p1, {"items": dict(_P1_TEST_ITEMS)})
    _dispatch(harness, "duel_prep_submit", p2, {"items": dict(_P2_TEST_ITEMS)})
    _dispatch(harness, "duel_lock_in", p1)
    _dispatch(harness, "duel_lock_in", p2)
    assert match.phase == "combat"


def _resolve_direct(match: MatchState, p1_action: str, p2_action: str) -> None:
    p1, p2 = match.players
    resolver.submit_action(match, p1, {"ability_id": p1_action})
    resolver.submit_action(match, p2, {"ability_id": p2_action})
    resolver.resolve_turn(match)


def _warning_count(harness: _ServerHarness, sid: str) -> int:
    return sum(
        actor_sid == sid
        and event == "duel_system"
        and payload == "Too many requests. Slow down."
        for actor_sid, event, payload, _kwargs in harness.direct_emits
    )


def _ordered_combat_projection(match: MatchState) -> list[dict[str, Any]]:
    projection: list[dict[str, Any]] = []
    for sid in match.players:
        player = match.state[sid]
        resources = player.res
        projection.append(
            {
                "hp": resources.hp,
                "mp": resources.mp,
                "energy": resources.energy,
                "rage": resources.rage,
                "absorbs": dict(resources.absorbs),
                "stats": dict(player.stats),
                "cooldowns": {key: list(value) for key, value in player.cooldowns.items()},
                "effects": [dict(effect) for effect in player.effects],
                "totals": dict(match.combat_totals.get(sid, {})),
            }
        )
    return projection


def _normalized_log(match: MatchState) -> list[str]:
    normalized = []
    p1, p2 = match.players
    for line in match.log:
        normalized.append(line.replace(p1[:5], "P1").replace(p2[:5], "P2"))
    return normalized


def scenario_availability_bounded_combat_log_and_cursor() -> bool:
    cap = SNAPSHOT_LOG_ENTRY_LIMIT
    with _isolated_server(_generous_policy(max_retained_log_entries=cap)) as harness:
        match = _create_combat_room("bound_p1", "bound_p2", seed=8101)
        generated = [f"synthetic-log-{index:03d}" for index in range(cap * 3 + 7)]
        match.log.extend(generated)

        assert len(match.log) == cap
        assert match.log == generated[-cap:]
        assert match.log_sequence == len(generated)
        assert match.log.first_retained_sequence == len(generated) - cap + 1

        snapshot = SOCKETS.snapshot_for(match, match.players[0])
        assert len(snapshot["log"]) == SNAPSHOT_LOG_ENTRY_LIMIT
        assert snapshot["log"] == generated[-SNAPSHOT_LOG_ENTRY_LIMIT:]
        assert snapshot["log_length"] == match.log_sequence
        assert snapshot["log_sequence"] == match.log_sequence
        assert snapshot["log_start_sequence"] == match.log.first_retained_sequence

        prior_sequence = match.log_sequence
        _resolve_direct(match, "pass_turn", "pass_turn")
        _resolve_direct(match, "pass_turn", "pass_turn")
        assert match.turn == 2
        assert len(match.log) == cap
        assert match.log_sequence > prior_sequence
        assert "Turn 2" in match.log

        short = MatchState(
            room_id="short-log",
            players=["short_p1", "short_p2"],
            phase="combat",
            seed=8102,
            max_retained_log_entries=cap,
            monotonic_clock=harness.clock,
        )
        short.picks["short_p1"] = PlayerBuild(class_id="warrior")
        short.picks["short_p2"] = PlayerBuild(class_id="mage")
        resolver.apply_prep_build(short)
        short.log.extend(["ordinary first line", "ordinary second line"])
        short_snapshot = SOCKETS.snapshot_for(short, "short_p1")
        assert short_snapshot["log"] == ["ordinary first line", "ordinary second line"]
        assert short_snapshot["log_sequence"] == 2
    return True


def scenario_availability_queue_ttl() -> bool:
    policy = _generous_policy(queue_ttl_seconds=10)
    with _isolated_server(policy) as harness:
        assert STATE.enqueue("expired_sid", now=harness.clock())
        harness.clock.advance(5)
        assert STATE.enqueue("fresh_sid", now=harness.clock())
        fresh_timestamp = STATE.queued_at_by_sid["fresh_sid"]

        harness.clock.advance(5)
        actions = STATE.collect_expired_resources(harness.clock())
        assert actions.expired_queue_sids == ["expired_sid"]
        assert STATE.duel_queue == ["fresh_sid"]
        assert "expired_sid" not in STATE.queued_at_by_sid
        assert STATE.queued_at_by_sid["fresh_sid"] == fresh_timestamp

        paired = STATE.queue_sid_for_match("partner_sid", 8103, now=harness.clock())
        assert paired.status == "matched" and paired.match is not None
        assert paired.match.players == ["fresh_sid", "partner_sid"]
        assert "expired_sid" not in paired.match.players
        assert not STATE.queued_at_by_sid

        assert STATE.enqueue("expired_sid", now=harness.clock())
        assert STATE.queued_at_by_sid["expired_sid"] == harness.clock()
        assert STATE.queued_at_by_sid["expired_sid"] != 1000.0
    return True


def scenario_availability_prep_idle_expiration() -> bool:
    policy = _generous_policy(prep_idle_ttl_seconds=5, prep_max_lifetime_seconds=20)
    with _isolated_server(policy) as harness:
        match = _create_prep_room("idleP1", "idleP2", seed=8104)
        room_id = match.room_id
        harness.clock.advance(5)
        actions = STATE.sweep_expired_resources(harness.socketio, harness.clock())

        assert [(entry.room_id, entry.reason) for entry in actions.cleaned_rooms] == [
            (room_id, "prep_idle")
        ]
        assert room_id not in STATE.duel_rooms
        assert all(sid not in STATE.sid_to_room for sid in match.players)
        assert all(sid not in STATE.duel_queue for sid in match.players)
        assert all(sid not in STATE.queued_at_by_sid for sid in match.players)
        assert match.availability_closed is True
        notices = [
            (payload, kwargs.get("to"))
            for event, payload, kwargs in harness.socketio.emitted
            if event == "duel_system"
        ]
        assert notices.count(("Duel setup closed due to inactivity.", "idleP1")) == 1
        assert notices.count(("Duel setup closed due to inactivity.", "idleP2")) == 1

        emitted_before = len(harness.socketio.emitted)
        repeated = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert not repeated.cleaned_rooms
        assert len(harness.socketio.emitted) == emitted_before

        notice_a = _create_prep_room("noticeA1", "noticeA2", seed=8204)
        notice_b = _create_prep_room("noticeB1", "noticeB2", seed=8205)
        harness.clock.advance(policy.prep_idle_ttl_seconds)
        notification_actions = STATE.collect_expired_resources(harness.clock())
        assert {entry.room_id for entry in notification_actions.cleaned_rooms} == {
            notice_a.room_id,
            notice_b.room_id,
        }

        attempted_targets: list[str | None] = []
        original_socket_emit = harness.socketio.emit

        def fail_first_cleanup_notice(
            event: str,
            payload: Any = None,
            **kwargs: Any,
        ) -> None:
            attempted_targets.append(kwargs.get("to"))
            if len(attempted_targets) == 1:
                raise RuntimeError("simulated cleanup-notice delivery failure")
            original_socket_emit(event, payload, **kwargs)

        harness.socketio.emit = fail_first_cleanup_notice
        closed_before = list(harness.socketio.closed_rooms)
        try:
            STATE.apply_cleanup_actions(harness.socketio, notification_actions)
        finally:
            harness.socketio.emit = original_socket_emit

        assert attempted_targets == [
            "noticeA1",
            "noticeA2",
            "noticeB1",
            "noticeB2",
        ]
        assert harness.socketio.closed_rooms[len(closed_before) :] == [
            notice_a.room_id,
            notice_b.room_id,
        ]
        assert {
            kwargs.get("to")
            for event, payload, kwargs in harness.socketio.emitted
            if event == "duel_system"
            and payload == "Duel setup closed due to inactivity."
        } >= {"noticeA2", "noticeB1", "noticeB2"}
    return True


def scenario_availability_prep_absolute_expiration() -> bool:
    policy = _generous_policy(prep_idle_ttl_seconds=4, prep_max_lifetime_seconds=10)
    with _isolated_server(policy) as harness:
        match = _create_prep_room("maxPr1", "maxPr2", seed=8105)
        start = float(match.phase_started_at)
        for offset, class_id in ((3, "warrior"), (6, "mage"), (9, "warrior")):
            harness.clock.set(start + offset)
            _dispatch(
                harness,
                "duel_prep_submit",
                "maxPr1",
                {"class_id": class_id},
            )
            assert match.last_gameplay_activity_at == start + offset
            assert match.room_id in STATE.duel_rooms

        harness.clock.set(start + 10)
        actions = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert [entry.reason for entry in actions.cleaned_rooms] == ["prep_max_lifetime"]
        assert match.room_id not in STATE.duel_rooms
        assert any(
            payload == "Duel setup reached the server time limit."
            for event, payload, _kwargs in harness.socketio.emitted
            if event == "duel_system"
        )
    return True


def scenario_availability_combat_idle_activity_rules() -> bool:
    policy = _policy(
        max_active_rooms=1,
        combat_idle_ttl_seconds=5,
        combat_max_lifetime_seconds=30,
    )
    with _isolated_server(policy) as harness:
        match = _create_combat_room("idleC1", "idleC2", seed=8106)
        start = float(match.phase_started_at)
        harness.clock.set(start + 4)
        _dispatch(harness, "duel_action", "idleC1", {"ability_id": "pass_turn"})
        accepted_activity = match.last_gameplay_activity_at
        assert accepted_activity == start + 4
        assert match.submitted == {"idleC1": {"ability_id": "pass_turn"}}
        log_sequence = match.log_sequence

        harness.clock.set(start + 5)
        _dispatch(harness, "duel_action", "idleC1", {"ability_id": []})
        action_burst = policy.action_throttle.burst
        for _ in range(action_burst):
            _dispatch(harness, "duel_action", "idleC2", None)
        _dispatch(harness, "duel_action", "idleC2", {"ability_id": "pass_turn"})
        assert _warning_count(harness, "idleC2") == 1

        harness.clock.set(start + 6)
        _dispatch(harness, "duel_chat", "idleC2", "Waiting for the other player")
        assert match.last_gameplay_activity_at == accepted_activity
        assert match.submitted == {"idleC1": {"ability_id": "pass_turn"}}
        assert match.turn == 0 and match.log_sequence == log_sequence

        harness.clock.set(float(accepted_activity) + policy.combat_idle_ttl_seconds - _TICK)
        before = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert not before.cleaned_rooms and match.room_id in STATE.duel_rooms

        harness.clock.set(float(accepted_activity) + policy.combat_idle_ttl_seconds)
        at_deadline = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert [entry.reason for entry in at_deadline.cleaned_rooms] == ["combat_idle"]
        assert match.room_id not in STATE.duel_rooms

        _clear_state()
        with _thread_local_socket_request():
            resolving = _create_combat_room("leaseA1", "leaseA2", seed=8280)
            _dispatch(
                harness,
                "duel_action",
                "leaseA1",
                {"ability_id": "pass_turn"},
            )
            resolver_entered = threading.Event()
            release_resolver = threading.Event()
            original_resolve_turn = resolver.resolve_turn

            def blocking_resolve_turn(target: MatchState) -> None:
                assert target is resolving
                resolver_entered.set()
                assert release_resolver.wait(timeout=2), "resolver was not released"
                original_resolve_turn(target)

            resolver.resolve_turn = blocking_resolve_turn
            resolution_worker, resolution_errors = _start_worker(
                lambda: _dispatch(
                    harness,
                    "duel_action",
                    "leaseA2",
                    {"ability_id": "pass_turn"},
                )
            )
            try:
                assert resolver_entered.wait(timeout=2), "resolver did not block"
                assert resolving.turn == 0
                assert resolving.turn_in_progress is True
                assert resolving.availability_resolution_in_progress is True
                submitted_before_rejection = {
                    sid: dict(action) for sid, action in resolving.submitted.items()
                }
                accepted_activity = resolving.last_gameplay_activity_at
                accepted_sequence = resolving.log_sequence

                harness.clock.advance(1)
                _dispatch(
                    harness,
                    "duel_action",
                    "leaseA1",
                    {"ability_id": "basic_attack"},
                )
                assert resolving.submitted == submitted_before_rejection
                assert resolving.last_gameplay_activity_at == accepted_activity
                assert resolving.log_sequence == accepted_sequence
                assert resolving.turn == 0
                assert any(
                    actor_sid == "leaseA1"
                    and event == "duel_system"
                    and payload
                    == "Turn resolution is in progress. Try again shortly."
                    for actor_sid, event, payload, _kwargs in harness.direct_emits
                )

                queued = STATE.queue_sid_for_match(
                    "leaseWait1",
                    8281,
                    now=harness.clock(),
                )
                blocked = STATE.queue_sid_for_match(
                    "leaseWait2",
                    8281,
                    now=harness.clock(),
                )
                assert queued.status == "queued"
                assert blocked.status == "room_full"
                assert STATE.duel_queue == ["leaseWait1", "leaseWait2"]

                disconnect_worker, disconnect_errors = _start_worker(
                    lambda: _dispatch(
                        harness,
                        "disconnect",
                        "leaseA1",
                        "client disconnect",
                    )
                )
                _join_worker(
                    disconnect_worker,
                    disconnect_errors,
                    label="concurrent disconnect",
                )
                assert resolving.room_id in STATE.duel_rooms
                assert resolving.availability_pending_cleanup_reason == "disconnect"
                assert all(
                    STATE.sid_to_room.get(sid) == resolving.room_id
                    for sid in resolving.players
                )
                delivery_boundary = len(harness.socketio.emitted)
            finally:
                release_resolver.set()
                try:
                    _join_worker(
                        resolution_worker,
                        resolution_errors,
                        label="blocked turn resolution",
                    )
                finally:
                    resolver.resolve_turn = original_resolve_turn

            assert resolving.turn == 1
            assert resolving.room_id not in STATE.duel_rooms
            assert resolving.availability_closed is True
            assert resolving.availability_resolution_in_progress is False
            assert resolving.turn_in_progress is False
            assert all(sid not in STATE.sid_to_room for sid in resolving.players)
            assert not any(
                event == "duel_snapshot"
                for event, _payload, _kwargs in harness.socketio.emitted[
                    delivery_boundary:
                ]
            )
            recovered = STATE.queue_sid_for_match(
                "leaseWait2",
                8282,
                now=harness.clock(),
            )
            assert recovered.status == "matched" and recovered.match is not None
            assert recovered.match.players == ["leaseWait1", "leaseWait2"]
    return True


def scenario_availability_combat_absolute_expiration() -> bool:
    policy = _generous_policy(
        combat_idle_ttl_seconds=3,
        combat_max_lifetime_seconds=10,
    )
    with _isolated_server(policy) as harness:
        match = _create_combat_room("maxCo1", "maxCo2", seed=8107)
        start = float(match.phase_started_at)
        for offset in (2, 4, 6, 8):
            harness.clock.set(start + offset)
            _dispatch(harness, "duel_action", "maxCo1", {"ability_id": "pass_turn"})
            _dispatch(harness, "duel_action", "maxCo2", {"ability_id": "pass_turn"})
            assert match.last_gameplay_activity_at == start + offset
            assert match.phase == "combat"

        assert match.turn == 4 and match.winner is None
        harness.clock.set(start + policy.combat_max_lifetime_seconds)
        actions = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert [entry.reason for entry in actions.cleaned_rooms] == [
            "combat_max_lifetime"
        ]
        assert match.winner is None
        assert match.room_id not in STATE.duel_rooms
    return True


def scenario_availability_ended_room_grace_period() -> bool:
    policy = _generous_policy(ended_ttl_seconds=5)
    with _isolated_server(policy) as harness:
        match = _create_combat_room("grace1", "grace2", seed=8108)
        ended_at = harness.clock()
        match.mark_phase_started("ended", ended_at)
        assert STATE.mark_match_ended(match, ended_at)
        harness.clock.advance(1)
        assert STATE.mark_match_ended(match, harness.clock())
        assert match.ended_at == ended_at

        final_snapshot = SOCKETS.snapshot_for(match, "grace1")
        assert final_snapshot["phase"] == "ended"
        harness.clock.set(ended_at + policy.ended_ttl_seconds - _TICK)
        before = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert not before.cleaned_rooms and match.room_id in STATE.duel_rooms

        harness.clock.set(ended_at + policy.ended_ttl_seconds)
        at_deadline = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert [entry.reason for entry in at_deadline.cleaned_rooms] == ["ended_ttl"]
        assert all(sid not in STATE.sid_to_room for sid in match.players)
        assert not STATE.sweep_expired_resources(
            harness.socketio, harness.clock()
        ).cleaned_rooms

        disconnected = _create_combat_room("discE1", "discE2", seed=8109)
        disconnected.mark_phase_started("ended", harness.clock())
        STATE.mark_match_ended(disconnected, harness.clock())
        STATE.consume_event_token("discE1", "duel_chat", now=harness.clock())
        _dispatch(harness, "disconnect", "discE1", "client disconnect")
        assert disconnected.room_id not in STATE.duel_rooms
        assert all(sid not in STATE.sid_to_room for sid in disconnected.players)
        assert "discE1" not in STATE.limiter_records
        _dispatch(harness, "disconnect", "discE1", "client disconnect")
    return True


def scenario_availability_queue_capacity() -> bool:
    policy = _generous_policy(max_queued_sids=3, queue_ttl_seconds=5)
    with _isolated_server(policy) as harness:
        for sid in ("capQ1", "capQ2", "capQ3"):
            assert STATE.enqueue(sid, now=harness.clock())
        assert STATE.duel_queue == ["capQ1", "capQ2", "capQ3"]
        assert STATE.enqueue("capQ2", now=harness.clock())
        assert STATE.duel_queue.count("capQ2") == 1
        assert not STATE.enqueue("capQ4", now=harness.clock())
        assert "capQ4" not in STATE.duel_queue
        assert "capQ4" not in STATE.queued_at_by_sid

        paired = STATE.queue_sid_for_match("capQ1", 8125, now=harness.clock())
        assert paired.status == "matched" and paired.match is not None
        assert paired.match.players == ["capQ1", "capQ2"]
        assert STATE.duel_queue == ["capQ3"]
        assert STATE.enqueue("capQ4", now=harness.clock())
        assert STATE.enqueue("capQ5", now=harness.clock())
        assert len(STATE.duel_queue) == policy.max_queued_sids
        assert STATE.dequeue("capQ4")
        assert STATE.enqueue("capQ6", now=harness.clock())
        assert len(STATE.duel_queue) == policy.max_queued_sids

        harness.clock.advance(policy.queue_ttl_seconds)
        actions = STATE.collect_expired_resources(harness.clock())
        assert len(actions.expired_queue_sids) == policy.max_queued_sids
        assert not STATE.duel_queue and not STATE.queued_at_by_sid
        assert STATE.enqueue("capQ5", now=harness.clock())
    return True


def scenario_availability_room_capacity() -> bool:
    policy = _generous_policy(max_active_rooms=1, max_queued_sids=5)
    with _isolated_server(policy) as harness:
        for index, retained_phase in enumerate(("prep", "combat", "ended"), start=1):
            _clear_state()
            first_p1 = f"r{index}A1"
            first_p2 = f"r{index}A2"
            waiting_p1 = f"r{index}B1"
            waiting_p2 = f"r{index}B2"
            if retained_phase == "prep":
                retained = _create_prep_room(first_p1, first_p2, seed=8110 + index)
            else:
                retained = _create_combat_room(
                    first_p1,
                    first_p2,
                    seed=8110 + index,
                )
                if retained_phase == "ended":
                    retained.mark_phase_started("ended", harness.clock())
                    STATE.mark_match_ended(retained, harness.clock())
            assert retained.phase == retained_phase
            assert len(STATE.duel_rooms) == policy.max_active_rooms

            assert STATE.queue_sid_for_match(
                waiting_p1, 8120 + index, now=harness.clock()
            ).status == "queued"
            blocked = STATE.queue_sid_for_match(
                waiting_p2, 8120 + index, now=harness.clock()
            )
            assert blocked.status == "room_full" and blocked.match is None
            assert STATE.duel_queue == [waiting_p1, waiting_p2]
            assert all(
                sid not in STATE.sid_to_room for sid in (waiting_p1, waiting_p2)
            )
            assert set(STATE.queued_at_by_sid) == {waiting_p1, waiting_p2}

            STATE.cleanup_room(retained.room_id, reason="capacity_test")
            recovered = STATE.queue_sid_for_match(
                waiting_p2, 8120 + index, now=harness.clock()
            )
            assert recovered.status == "matched" and recovered.match is not None
            assert recovered.match.players == [waiting_p1, waiting_p2]
            assert not STATE.duel_queue and not STATE.queued_at_by_sid
            assert STATE.sid_to_room[waiting_p1] == recovered.match.room_id
            assert STATE.sid_to_room[waiting_p2] == recovered.match.room_id

        _clear_state()
        default_join_room = SOCKETS.join_room
        _dispatch(harness, "duel_queue", "setupFail1")
        join_attempts: list[tuple[str, str | None]] = []

        def fail_second_transport_join(room_id: str, **kwargs: Any) -> None:
            entry = (room_id, kwargs.get("sid"))
            join_attempts.append(entry)
            harness.socketio.joined.append(entry)
            if len(join_attempts) == 2:
                raise RuntimeError("simulated second transport join failure")

        SOCKETS.join_room = fail_second_transport_join
        try:
            _dispatch(harness, "duel_queue", "setupFail2")
        finally:
            SOCKETS.join_room = default_join_room

        failed_room_id = join_attempts[0][0]
        assert [sid for _room_id, sid in join_attempts] == [
            "setupFail1",
            "setupFail2",
        ]
        assert failed_room_id not in STATE.duel_rooms
        assert all(
            sid not in STATE.sid_to_room
            for sid in ("setupFail1", "setupFail2")
        )
        assert all(
            sid not in STATE.duel_queue and sid not in STATE.queued_at_by_sid
            for sid in ("setupFail1", "setupFail2")
        )
        assert failed_room_id in harness.socketio.closed_rooms
        assert {
            kwargs.get("to")
            for event, payload, kwargs in harness.socketio.emitted
            if event == "duel_system"
            and payload == "Match setup failed. Queue again to continue."
        } >= {"setupFail1", "setupFail2"}

        _clear_state()
        with _thread_local_socket_request():
            _dispatch(harness, "duel_queue", "setupRace1")
            join_entered = threading.Event()
            release_join = threading.Event()
            transport_members: set[tuple[str, str]] = set()
            transport_events: list[tuple[str, str]] = []
            default_close_room = harness.socketio.close_room

            def blocking_transport_join(room_id: str, **kwargs: Any) -> None:
                sid = kwargs.get("sid")
                assert isinstance(sid, str)
                transport_events.append(("join_enter", room_id))
                join_entered.set()
                assert release_join.wait(timeout=2), "transport join was not released"
                transport_members.add((room_id, sid))
                harness.socketio.joined.append((room_id, sid))
                transport_events.append(("join_commit", room_id))

            def tracked_close_room(room_id: str) -> None:
                transport_events.append(("close", room_id))
                transport_members.difference_update(
                    {
                        member
                        for member in transport_members
                        if member[0] == room_id
                    }
                )
                default_close_room(room_id)

            SOCKETS.join_room = blocking_transport_join
            harness.socketio.close_room = tracked_close_room
            queue_worker, queue_errors = _start_worker(
                lambda: _dispatch(harness, "duel_queue", "setupRace2")
            )
            try:
                assert join_entered.wait(timeout=2), "transport setup did not block"
                setup_match = STATE.get_match_by_sid("setupRace1")
                assert setup_match is not None
                assert setup_match.availability_transport_setup_in_progress is True

                _dispatch(
                    harness,
                    "disconnect",
                    "setupRace1",
                    "client disconnect",
                )
                assert setup_match.room_id in STATE.duel_rooms
                assert setup_match.availability_pending_cleanup_reason == "disconnect"
                assert not transport_members
            finally:
                release_join.set()
                try:
                    _join_worker(queue_worker, queue_errors, label="transport setup")
                finally:
                    SOCKETS.join_room = default_join_room
                    harness.socketio.close_room = default_close_room

            assert setup_match.room_id not in STATE.duel_rooms
            assert all(
                sid not in STATE.sid_to_room
                for sid in ("setupRace1", "setupRace2")
            )
            assert not transport_members
            assert transport_events[-1] == ("close", setup_match.room_id)

        recovered = STATE.queue_sid_for_match(
            "setupRecovered1",
            8290,
            now=harness.clock(),
        )
        assert recovered.status == "queued"
        recovered = STATE.queue_sid_for_match(
            "setupRecovered2",
            8290,
            now=harness.clock(),
        )
        assert recovered.status == "matched" and recovered.match is not None
    return True


def scenario_availability_per_sid_event_throttling() -> bool:
    policy = AvailabilityPolicy().validate()
    with _isolated_server(policy) as harness:
        prep = _create_prep_room("thrPr1", "thrPr2", seed=8112)
        lock = _create_prep_room("thrLk1", "thrLk2", seed=8113)
        lock.picks["thrLk1"] = _canonical_pick("warrior")
        action = _create_combat_room("thrAc1", "thrAc2", seed=8114)
        chat = _create_combat_room("thrCh1", "thrCh2", seed=8115)

        cases = (
            (
                "duel_queue",
                "thrQu1",
                (),
                lambda: (
                    list(STATE.duel_queue),
                    dict(STATE.queued_at_by_sid),
                ),
                lambda: "thrQu1" in STATE.duel_queue,
            ),
            (
                "duel_prep_submit",
                "thrPr1",
                ({"class_id": "warrior"},),
                lambda: (
                    dict(prep.picks),
                    prep.last_gameplay_activity_at,
                    prep.log_sequence,
                ),
                lambda: prep.picks.get("thrPr1", {}).get("class_id") == "warrior",
            ),
            (
                "duel_lock_in",
                "thrLk1",
                (),
                lambda: (
                    dict(lock.locked_in),
                    lock.last_gameplay_activity_at,
                    lock.log_sequence,
                ),
                lambda: lock.locked_in.get("thrLk1") is True,
            ),
            (
                "duel_action",
                "thrAc1",
                ({"ability_id": "pass_turn"},),
                lambda: (
                    dict(action.submitted),
                    action.last_gameplay_activity_at,
                    action.turn,
                    action.log_sequence,
                ),
                lambda: action.submitted.get("thrAc1") == {
                    "ability_id": "pass_turn"
                },
            ),
            (
                "duel_chat",
                "thrCh1",
                ("bounded chat",),
                lambda: (
                    len(
                        [entry for entry in harness.socketio.emitted if entry[0] == "duel_chat"]
                    ),
                    chat.last_gameplay_activity_at,
                    chat.log_sequence,
                ),
                lambda: any(
                    event == "duel_chat" and payload.get("message") == "bounded chat"
                    for event, payload, _kwargs in harness.socketio.emitted
                ),
            ),
        )

        for event_name, sid, payload_args, state_view, accepted_after_refill in cases:
            throttle = policy.throttle_for(event_name)
            for _ in range(throttle.burst):
                assert STATE.consume_event_token(
                    sid, event_name, now=harness.clock()
                ).allowed
            preserved = state_view()

            _dispatch(harness, event_name, sid, *payload_args)
            assert state_view() == preserved
            assert _warning_count(harness, sid) == 1
            for _ in range(100):
                _dispatch(harness, event_name, sid, *payload_args)
            assert state_view() == preserved
            assert _warning_count(harness, sid) == 1

            harness.clock.advance(throttle.window_seconds)
            _dispatch(harness, event_name, sid, *payload_args)
            assert accepted_after_refill()

        isolated_sid = "isolatedA"
        for _ in range(policy.queue_throttle.burst):
            assert STATE.consume_event_token(
                isolated_sid, "duel_queue", now=harness.clock()
            ).allowed
        assert not STATE.consume_event_token(
            isolated_sid, "duel_queue", now=harness.clock()
        ).allowed
        assert STATE.consume_event_token(
            "isolatedB", "duel_queue", now=harness.clock()
        ).allowed
        assert STATE.consume_event_token(
            isolated_sid, "duel_chat", now=harness.clock()
        ).allowed
    return True


def scenario_availability_limiter_cleanup() -> bool:
    policy = _generous_policy(limiter_stale_ttl_seconds=5, max_limiter_sids=10)
    with _isolated_server(policy) as harness:
        for sid in ("disconnect_limiter", "stale_limiter", "recent_limiter"):
            assert STATE.consume_event_token(
                sid, "duel_chat", now=harness.clock()
            ).allowed

        assert STATE.disconnect_sid("disconnect_limiter") is None
        assert "disconnect_limiter" not in STATE.limiter_records
        harness.clock.advance(4)
        assert STATE.consume_event_token(
            "recent_limiter", "duel_chat", now=harness.clock()
        ).allowed
        harness.clock.advance(1)

        actions = STATE.collect_expired_resources(harness.clock())
        assert actions.expired_limiter_sids == ["stale_limiter"]
        assert "recent_limiter" in STATE.limiter_records
        assert not STATE.collect_expired_resources(
            harness.clock()
        ).expired_limiter_sids

        assert STATE.start_lifecycle_sweeper(harness.socketio) is True
        assert STATE.start_lifecycle_sweeper(harness.socketio) is False
        assert len(harness.socketio.background_tasks) == 1
    return True


def scenario_availability_socketio_buffer_configuration() -> bool:
    class FakeFlask:
        def __init__(self, import_name: str) -> None:
            self.import_name = import_name
            self.config: dict[str, Any] = {}

        def route(self, _path: str):
            def decorate(handler):
                return handler

            return decorate

    class RecordingSocketIO:
        instances: list["RecordingSocketIO"] = []

        def __init__(self, app: Any, *args: Any, **kwargs: Any) -> None:
            self.app = app
            self.args = args
            self.kwargs = kwargs
            self.__class__.instances.append(self)

        def run(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    fake_flask = types.ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.render_template = lambda template: template
    fake_socketio = types.ModuleType("flask_socketio")
    fake_socketio.SocketIO = RecordingSocketIO

    original_flask = sys.modules.get("flask")
    original_socketio = sys.modules.get("flask_socketio")
    duel_package = sys.modules["games.duel"]
    had_init_duel = hasattr(duel_package, "init_duel")
    original_init_duel = getattr(duel_package, "init_duel", None)
    init_calls: list[tuple[Any, Any]] = []
    module_name = "_availability_app_initialization_test"
    try:
        sys.modules["flask"] = fake_flask
        sys.modules["flask_socketio"] = fake_socketio
        duel_package.init_duel = lambda app, socketio: init_calls.append((app, socketio))
        spec = importlib.util.spec_from_file_location(module_name, _REPO_ROOT / "app.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
        if original_flask is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = original_flask
        if original_socketio is None:
            sys.modules.pop("flask_socketio", None)
        else:
            sys.modules["flask_socketio"] = original_socketio
        if had_init_duel:
            duel_package.init_duel = original_init_duel
        else:
            delattr(duel_package, "init_duel")

    assert len(RecordingSocketIO.instances) == 1
    recorded = RecordingSocketIO.instances[0]
    expected = DEFAULT_AVAILABILITY_POLICY.socket_max_buffer_bytes
    assert recorded.kwargs["max_http_buffer_size"] == expected
    assert recorded.kwargs["async_mode"] == "eventlet"
    assert len(init_calls) == 1 and init_calls[0][1] is recorded
    overridden = load_availability_policy(
        {"MAKGORA_SOCKET_MAX_BUFFER_BYTES": str(24 * 1024)}
    )
    assert overridden.socket_max_buffer_bytes == 24 * 1024
    return True


def scenario_availability_two_tab_same_pc_flow() -> bool:
    with _isolated_server(AvailabilityPolicy()) as harness:
        p1, p2 = "tabA_same_pc", "tabB_same_pc"
        match = _queue_and_pair(harness, p1, p2)
        _submit_builds_and_lock(harness, match)
        _dispatch(harness, "duel_action", p1, {"ability_id": "pass_turn"})
        _dispatch(harness, "duel_action", p2, {"ability_id": "pass_turn"})

        assert match.turn == 1 and match.phase == "combat"
        assert len(STATE.duel_rooms) == 1
        assert STATE.sid_to_room[p1] == match.room_id
        assert STATE.sid_to_room[p2] == match.room_id
        assert p1 in STATE.limiter_records and p2 in STATE.limiter_records
        assert STATE.limiter_records[p1] is not STATE.limiter_records[p2]
        assert "duel_action" in STATE.limiter_records[p1].buckets
        assert "duel_action" in STATE.limiter_records[p2].buckets
        snapshots = [entry for entry in harness.socketio.emitted if entry[0] == "duel_snapshot"]
        assert any(kwargs.get("to") == p1 for _event, _payload, kwargs in snapshots)
        assert any(kwargs.get("to") == p2 for _event, _payload, kwargs in snapshots)
    return True


def scenario_availability_full_ordinary_duel_flow() -> bool:
    policy = AvailabilityPolicy()
    with _isolated_server(policy) as harness:
        p1, p2 = "fullA_sid", "fullB_sid"
        match = _queue_and_pair(harness, p1, p2)
        _submit_builds_and_lock(harness, match)
        control = make_match(
            "warrior",
            "mage",
            p1_items=_P1_TEST_ITEMS,
            p2_items=_P2_TEST_ITEMS,
            seed=match.seed,
        )

        for expected_turn in range(1, 4):
            _dispatch(harness, "duel_action", p1, {"ability_id": "basic_attack"})
            _dispatch(harness, "duel_action", p2, {"ability_id": "basic_attack"})
            _resolve_direct(control, "basic_attack", "basic_attack")
            assert match.turn == expected_turn
            assert _ordered_combat_projection(match) == _ordered_combat_projection(control)
            assert _normalized_log(match) == _normalized_log(control)

        match.state[p2].res.hp = 0
        _dispatch(harness, "duel_action", p1, {"ability_id": "pass_turn"})
        _dispatch(harness, "duel_action", p2, {"ability_id": "pass_turn"})
        assert match.phase == "ended" and match.winner == p1
        assert match.ended_at == harness.clock()
        final_snapshots = [
            (payload, kwargs.get("to"))
            for event, payload, kwargs in harness.socketio.emitted
            if event == "duel_snapshot" and payload.get("phase") == "ended"
        ]
        assert {target for _payload, target in final_snapshots} == {p1, p2}
        assert any(
            event == "duel_system"
            and payload == "Duel ended."
            and kwargs.get("to") == match.room_id
            for event, payload, kwargs in harness.socketio.emitted
        )

        ended_at = float(match.ended_at)
        harness.clock.set(ended_at + policy.ended_ttl_seconds - _TICK)
        assert not STATE.sweep_expired_resources(
            harness.socketio, harness.clock()
        ).cleaned_rooms
        harness.clock.set(ended_at + policy.ended_ttl_seconds)
        actions = STATE.sweep_expired_resources(harness.socketio, harness.clock())
        assert [entry.reason for entry in actions.cleaned_rooms] == ["ended_ttl"]
        assert match.room_id not in STATE.duel_rooms

        _clear_state()
        delivery_match = _create_combat_room(
            "delivery1",
            "delivery2",
            seed=8295,
        )
        _dispatch(
            harness,
            "duel_action",
            "delivery1",
            {"ability_id": "pass_turn"},
        )
        original_socket_emit = harness.socketio.emit
        original_resolve_turn = resolver.resolve_turn
        resolver_calls: list[MatchState] = []
        delivery_failures: list[str] = []
        direct_emit_boundary = len(harness.direct_emits)

        def counted_resolve_turn(target: MatchState) -> None:
            resolver_calls.append(target)
            original_resolve_turn(target)

        def fail_first_result_delivery(
            event: str,
            payload: Any = None,
            **kwargs: Any,
        ) -> None:
            if event == "duel_snapshot" and not delivery_failures:
                delivery_failures.append(str(kwargs.get("to")))
                raise RuntimeError("simulated committed-result delivery failure")
            original_socket_emit(event, payload, **kwargs)

        resolver.resolve_turn = counted_resolve_turn
        harness.socketio.emit = fail_first_result_delivery
        try:
            _dispatch(
                harness,
                "duel_action",
                "delivery2",
                {"ability_id": "pass_turn"},
            )
        finally:
            harness.socketio.emit = original_socket_emit
            resolver.resolve_turn = original_resolve_turn

        assert resolver_calls == [delivery_match]
        assert delivery_failures == ["delivery1"]
        assert delivery_match.turn == 1
        assert not delivery_match.submitted
        assert delivery_match.turn_in_progress is False
        assert delivery_match.availability_resolution_in_progress is False
        assert delivery_match.room_id in STATE.duel_rooms
        assert not any(
            event == "duel_system"
            and payload
            == "Turn resolution failed. Please submit your action again."
            for _actor_sid, event, payload, _kwargs in harness.direct_emits[
                direct_emit_boundary:
            ]
        )

        _dispatch(
            harness,
            "duel_action",
            "delivery1",
            {"ability_id": "pass_turn"},
        )
        _dispatch(
            harness,
            "duel_action",
            "delivery2",
            {"ability_id": "pass_turn"},
        )
        assert delivery_match.turn == 2
    return True


def scenario_availability_boundary_values() -> bool:
    policy = _policy(
        max_queued_sids=2,
        max_active_rooms=1,
        max_retained_log_entries=SNAPSHOT_LOG_ENTRY_LIMIT,
        queue_ttl_seconds=5,
        prep_idle_ttl_seconds=5,
        prep_max_lifetime_seconds=20,
        combat_idle_ttl_seconds=6,
        combat_max_lifetime_seconds=25,
        ended_ttl_seconds=7,
        limiter_stale_ttl_seconds=8,
        queue_throttle=EventThrottlePolicy(2, 10, 2),
    )
    with _isolated_server(policy) as harness:
        bounded = BoundedCombatLog(max_entries=SNAPSHOT_LOG_ENTRY_LIMIT)
        bounded.extend(f"entry-{index}" for index in range(SNAPSHOT_LOG_ENTRY_LIMIT))
        assert len(bounded) == SNAPSHOT_LOG_ENTRY_LIMIT
        bounded.append("one-beyond")
        assert len(bounded) == SNAPSHOT_LOG_ENTRY_LIMIT
        assert bounded[0] == "entry-1" and bounded[-1] == "one-beyond"
        retained_before_rejections = list(bounded)
        sequence_before_rejections = bounded.sequence
        rejected_mutations = (
            lambda log: log.__setitem__(0, "replacement"),
            lambda log: log.__setitem__(slice(0, 2), ["replacement"]),
            lambda log: log.__delitem__(0),
            lambda log: log.__delitem__(slice(0, 2)),
            lambda log: log.__imul__(2),
            lambda log: log.insert(0, "inserted"),
            lambda log: log.clear(),
            lambda log: log.pop(),
            lambda log: log.remove(log[0]),
            lambda log: log.reverse(),
            lambda log: log.sort(),
        )
        for mutation in rejected_mutations:
            try:
                mutation(bounded)
            except TypeError as exc:
                assert "append-only" in str(exc)
            else:
                raise AssertionError("A non-append combat-log mutation was accepted")
            assert list(bounded) == retained_before_rejections
            assert bounded.sequence == sequence_before_rejections

        assert STATE.enqueue("boundQ1", now=harness.clock())
        assert STATE.enqueue("boundQ2", now=harness.clock())
        assert len(STATE.duel_queue) == policy.max_queued_sids
        assert not STATE.enqueue("boundQ3", now=harness.clock())
        _clear_state()

        room = STATE.create_room("boundR1", "boundR2", 8116, now=harness.clock())
        assert len(STATE.duel_rooms) == policy.max_active_rooms
        try:
            STATE.create_room("boundR3", "boundR4", 8117, now=harness.clock())
        except RuntimeError as exc:
            assert "capacity" in str(exc)
        else:
            raise AssertionError("Room capacity plus one was accepted")
        STATE.cleanup_room(room.room_id, reason="boundary_reset")

        assert STATE.consume_event_token(
            "token_boundary", "duel_queue", now=harness.clock()
        ).allowed
        assert STATE.consume_event_token(
            "token_boundary", "duel_queue", now=harness.clock()
        ).allowed
        assert not STATE.consume_event_token(
            "token_boundary", "duel_queue", now=harness.clock()
        ).allowed

        base = 2000.0
        room_cases = (
            ("prep_idle", "prep", policy.prep_idle_ttl_seconds, False),
            ("prep_max_lifetime", "prep", policy.prep_max_lifetime_seconds, True),
            ("combat_idle", "combat", policy.combat_idle_ttl_seconds, False),
            (
                "combat_max_lifetime",
                "combat",
                policy.combat_max_lifetime_seconds,
                True,
            ),
            ("ended_ttl", "ended", policy.ended_ttl_seconds, False),
        )
        for expected_reason, phase, ttl, keep_idle_fresh in room_cases:
            for offset, expired in ((-_TICK, False), (0.0, True), (_TICK, True)):
                now = base + ttl + offset
                match = MatchState(
                    room_id=f"boundary-{expected_reason}-{offset}",
                    players=["bp1", "bp2"],
                    phase=phase,
                    seed=8118,
                    created_at=base,
                    phase_started_at=base,
                    last_gameplay_activity_at=now if keep_idle_fresh else base,
                    ended_at=base if phase == "ended" else None,
                    monotonic_clock=harness.clock,
                )
                expiration = STATE._room_expiration(match, now)
                if expired:
                    assert expiration is not None and expiration[0] == expected_reason
                else:
                    assert expiration is None

        for offset, expired in ((-_TICK, False), (0.0, True), (_TICK, True)):
            _clear_state()
            assert STATE.enqueue("queue_boundary", now=base)
            actions = STATE.collect_expired_resources(
                base + policy.queue_ttl_seconds + offset
            )
            assert ("queue_boundary" in actions.expired_queue_sids) is expired
            assert ("queue_boundary" not in STATE.duel_queue) is expired

        for offset, expired in ((-_TICK, False), (0.0, True), (_TICK, True)):
            _clear_state()
            assert STATE.consume_event_token(
                "limiter_boundary", "duel_chat", now=base
            ).allowed
            actions = STATE.collect_expired_resources(
                base + policy.limiter_stale_ttl_seconds + offset
            )
            assert ("limiter_boundary" in actions.expired_limiter_sids) is expired

        invalid_policies = (
            replace(policy, max_queued_sids=0),
            replace(policy, max_active_rooms=0),
            replace(policy, queue_ttl_seconds=0),
            replace(policy, prep_idle_ttl_seconds=-1),
            replace(policy, max_retained_log_entries=SNAPSHOT_LOG_ENTRY_LIMIT - 1),
            replace(policy, socket_max_buffer_bytes=MIN_SOCKET_BUFFER_BYTES - 1),
            replace(policy, queue_throttle=EventThrottlePolicy(0, 10, 1)),
            replace(policy, action_throttle=EventThrottlePolicy(1, 0, 1)),
            replace(policy, chat_throttle=EventThrottlePolicy(1, 10, 0)),
        )
        for invalid in invalid_policies:
            try:
                invalid.validate()
            except ValueError:
                pass
            else:
                raise AssertionError(f"Invalid availability policy was accepted: {invalid}")
        try:
            load_availability_policy({"MAKGORA_QUEUE_TTL_SECONDS": "not-a-number"})
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid environment availability override was accepted")
    return True


AVAILABILITY_SCENARIOS = (
    scenario_availability_bounded_combat_log_and_cursor,
    scenario_availability_queue_ttl,
    scenario_availability_prep_idle_expiration,
    scenario_availability_prep_absolute_expiration,
    scenario_availability_combat_idle_activity_rules,
    scenario_availability_combat_absolute_expiration,
    scenario_availability_ended_room_grace_period,
    scenario_availability_queue_capacity,
    scenario_availability_room_capacity,
    scenario_availability_per_sid_event_throttling,
    scenario_availability_limiter_cleanup,
    scenario_availability_socketio_buffer_configuration,
    scenario_availability_two_tab_same_pc_flow,
    scenario_availability_full_ordinary_duel_flow,
    scenario_availability_boundary_values,
)

assert len(AVAILABILITY_SCENARIOS) == 15
