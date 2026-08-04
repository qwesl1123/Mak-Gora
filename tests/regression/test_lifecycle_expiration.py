"""Deterministic regressions for queued-player and duel-room expiration."""
from __future__ import annotations

import inspect
import math
import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Iterator

import eventlet
from eventlet.event import Event

from harness import MatchState, SOCKETS, make_match


availability = sys.modules["games.duel.availability"]
state = SOCKETS.state


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = value


def _rate() -> Any:
    return availability.EventRate(100, 10, 100)


def _admission_policy(**overrides: Any) -> Any:
    values = {
        "max_queued_sids": 20,
        "max_active_rooms": 4,
        "queue_ttl_seconds": 10,
        "max_limiter_sids": 100,
        "throttle_warning_cooldown_seconds": 2,
        "queue_rate": _rate(),
        "prep_rate": _rate(),
        "lock_rate": _rate(),
        "action_rate": _rate(),
        "chat_rate": _rate(),
    }
    values.update(overrides)
    return availability.AdmissionPolicy(**values)


def _lifecycle_policy(**overrides: Any) -> Any:
    values = {
        "prep_idle_ttl_seconds": 10,
        "prep_absolute_ttl_seconds": 30,
        "combat_idle_ttl_seconds": 15,
        "combat_absolute_ttl_seconds": 60,
        "ended_grace_seconds": 5,
        "sweep_interval_seconds": 3,
    }
    values.update(overrides)
    return availability.LifecyclePolicy(**values)


@contextmanager
def _isolated_state(
    *,
    clock: FakeClock | None = None,
    admission_policy: Any | None = None,
) -> Iterator[tuple[FakeClock, Any]]:
    active_clock = clock or FakeClock()
    active_policy = admission_policy or _admission_policy()
    with state.state_lock:
        saved = {
            "queue": list(state.duel_queue),
            "queued_at": dict(state.queued_at_by_sid),
            "rooms": dict(state.duel_rooms),
            "sid_to_room": dict(state.sid_to_room),
            "limiters": dict(state.limiter_records),
            "room_sequence": state._next_room_sequence,
            "clock": state.monotonic_clock,
            "policy": state.ADMISSION_POLICY,
        }
        state.duel_queue.clear()
        state.queued_at_by_sid.clear()
        state.duel_rooms.clear()
        state.sid_to_room.clear()
        state.limiter_records.clear()
        state._next_room_sequence = 0
        state.monotonic_clock = active_clock
        state.ADMISSION_POLICY = active_policy
    try:
        yield active_clock, active_policy
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
            state.monotonic_clock = saved["clock"]
            state.ADMISSION_POLICY = saved["policy"]


class FakeSocketIOServer:
    def __init__(self, socketio: "FakeSocketIO") -> None:
        self.socketio = socketio

    def enter_room(self, sid: str, room_id: str, *, namespace: str) -> None:
        self.socketio._assert_transport_locks("enter_room", sid)
        if sid in self.socketio.fail_setup_sids:
            raise RuntimeError("forced setup failure")
        assert namespace == "/"
        self.socketio.entered_rooms.append((room_id, sid))


class FakeSocketIO:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.emitted: list[tuple[str, Any, dict[str, Any]]] = []
        self.direct_emitted: list[tuple[str, str, Any, dict[str, Any]]] = []
        self.entered_rooms: list[tuple[str, str]] = []
        self.closed_rooms: list[str] = []
        self.started_tasks: list[tuple[Any, tuple[Any, ...]]] = []
        self.sleeps: list[float] = []
        self.emit_attempts: list[tuple[str, Any, dict[str, Any]]] = []
        self.close_attempts: list[str] = []
        self.fail_emit_targets: set[str] = set()
        self.fail_emit_once_targets: set[str] = set()
        self.fail_close_rooms: set[str] = set()
        self.fail_setup_sids: set[str] = set()
        self.transport_probe: Any | None = None
        self.fail_start = False
        self.server = FakeSocketIOServer(self)

    def _assert_registry_unlocked(self) -> None:
        assert not state.state_lock.locked(), "transport ran while state_lock was held"

    def _assert_transport_locks(self, operation: str, target: str) -> None:
        self._assert_registry_unlocked()
        if self.transport_probe is not None:
            self.transport_probe(operation, target)

    def on(self, event: str) -> Any:
        def register(handler: Any) -> Any:
            self.handlers[event] = handler
            return handler

        return register

    def emit(self, event: str, payload: Any = None, **kwargs: Any) -> None:
        target = kwargs.get("to")
        self._assert_transport_locks("emit", target)
        self.emit_attempts.append((event, payload, kwargs))
        if target in self.fail_emit_once_targets:
            self.fail_emit_once_targets.remove(target)
            raise RuntimeError("forced one-shot emit failure")
        if target in self.fail_emit_targets:
            raise RuntimeError("forced emit failure")
        self.emitted.append((event, payload, kwargs))

    def close_room(self, room_id: str) -> None:
        self._assert_transport_locks("close_room", room_id)
        self.close_attempts.append(room_id)
        if room_id in self.fail_close_rooms:
            raise RuntimeError("forced close failure")
        self.closed_rooms.append(room_id)

    def start_background_task(self, target: Any, *args: Any) -> object:
        if self.fail_start:
            raise RuntimeError("forced task failure")
        self.started_tasks.append((target, args))
        return object()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        raise _StopLoop()


class _StopLoop(Exception):
    pass


@contextmanager
def _captured_server_failures() -> Iterator[list[str]]:
    messages: list[str] = []
    original_exception = SOCKETS.logger.exception
    SOCKETS.logger.exception = lambda message, *args, **kwargs: messages.append(
        message % args if args else message
    )
    try:
        yield messages
    finally:
        SOCKETS.logger.exception = original_exception


@contextmanager
def _registered_handlers(socketio: FakeSocketIO) -> Iterator[None]:
    original_emit = SOCKETS.emit
    original_sid = SOCKETS.request.sid

    def direct_emit(
        event: str,
        payload: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        socketio._assert_registry_unlocked()
        socketio.direct_emitted.append(
            (SOCKETS.request.sid, event, payload, kwargs)
        )

    SOCKETS.emit = direct_emit
    SOCKETS.register_duel_socket_handlers(socketio)
    try:
        yield
    finally:
        SOCKETS.emit = original_emit
        SOCKETS.request.sid = original_sid


def _call(socketio: FakeSocketIO, sid: str, event: str, *payload: Any) -> None:
    SOCKETS.request.sid = sid
    socketio.handlers[event](*payload)


def _create_room(p1: str, p2: str, seed: int = 1) -> MatchState:
    with state.state_lock:
        return state._create_room_locked(p1, p2, seed, state.current_monotonic_time())


def _register_match(match: MatchState, now: float) -> MatchState:
    match.created_at = now
    match.phase_started_at = now
    match.last_gameplay_activity_at = now
    match.ended_at = None
    with state.state_lock:
        state.duel_rooms[match.room_id] = match
        for sid in match.players:
            state.sid_to_room[sid] = match.room_id
    return match


def scenario_lifecycle_queue_background_expiration() -> bool:
    policy = _admission_policy(max_active_rooms=1, queue_ttl_seconds=10)
    lifecycle = _lifecycle_policy(prep_idle_ttl_seconds=10)
    with _isolated_state(admission_policy=policy) as (clock, _policy):
        old = _create_room("old-a", "old-b")
        state.request_matchmaking("expired", 2, now=0, policy=policy)
        state.request_matchmaking("fresh-a", 3, now=1, policy=policy)
        state.request_matchmaking("fresh-b", 4, now=1, policy=policy)
        socketio = FakeSocketIO()

        before = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=9.999,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert before.expired_queue_sids == ()
        assert old.room_id in state.duel_rooms

        socketio.fail_emit_targets.add("expired")
        clock.set(10)
        with _captured_server_failures() as failures:
            exact = SOCKETS.run_lifecycle_sweep(
                socketio,
                now=10,
                lifecycle_policy=lifecycle,
                admission_policy=policy,
            )
        assert failures == ["Failed queue-expiration notice for SID"]
        assert exact.expired_queue_sids == ("expired",)
        assert "expired" not in state.duel_queue
        assert "expired" not in state.queued_at_by_sid
        assert old.room_id in socketio.closed_rooms
        replacement = next(iter(state.duel_rooms.values()))
        assert replacement.players == ["fresh-a", "fresh-b"]
        assert "expired" not in replacement.players

    with _isolated_state(admission_policy=policy) as (clock, _policy):
        _create_room("occupied-a", "occupied-b")
        state.request_matchmaking("lazy-expired", 5, now=0, policy=policy)
        clock.set(10)
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            _call(socketio, "new-request", state.QUEUE_EVENT)
        assert "lazy-expired" not in state.duel_queue
        assert any(
            event == "duel_system"
            and payload == SOCKETS.QUEUE_EXPIRED_MESSAGE
            and kwargs.get("to") == "lazy-expired"
            for event, payload, kwargs in socketio.emitted
        )

        socketio.emitted.clear()
        clock.set(20)
        _call(socketio, "new-request", state.QUEUE_EVENT)
        assert "new-request" in state.duel_queue
        assert not any(
            event == "duel_system"
            and payload == SOCKETS.QUEUE_EXPIRED_MESSAGE
            and kwargs.get("to") == "new-request"
            for event, payload, kwargs in socketio.emitted
        )
    return True


def scenario_lifecycle_prep_idle_expiration() -> bool:
    lifecycle = _lifecycle_policy()
    with _isolated_state() as (clock, policy):
        match = _create_room("prep-a", "prep-b")
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            clock.set(5)
            _call(socketio, "prep-a", state.PREP_EVENT, {"class_id": "warrior"})
            assert match.last_gameplay_activity_at == 5

            clock.set(6)
            _call(socketio, "prep-a", state.PREP_EVENT, {"class_id": "warrior"})
            assert match.last_gameplay_activity_at == 5, "no-op prep refreshed activity"

            clock.set(7)
            _call(socketio, "prep-a", state.PREP_EVENT, {"class_id": "invalid"})
            clock.set(8)
            _call(socketio, "prep-a", state.PREP_EVENT, ["malformed"])
            clock.set(9)
            _call(socketio, "prep-a", state.CHAT_EVENT, "hello")
            assert match.last_gameplay_activity_at == 5

            original_consume = state.consume_event_token
            state.consume_event_token = lambda *args, **kwargs: state.ThrottleDecision(False)
            try:
                clock.set(10)
                _call(socketio, "prep-a", state.PREP_EVENT, {"class_id": "mage"})
            finally:
                state.consume_event_token = original_consume
            assert match.last_gameplay_activity_at == 5

        assert not SOCKETS.run_lifecycle_sweep(
            socketio,
            now=14.999,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        ).detached_rooms
        exact = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=15,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert exact.detached_rooms[0].reason == "prep_idle"
    return True


def scenario_lifecycle_prep_absolute_expiration() -> bool:
    lifecycle = _lifecycle_policy()
    with _isolated_state() as (clock, policy):
        match = _create_room("absolute-a", "absolute-b")
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            clock.set(29)
            _call(socketio, "absolute-a", state.PREP_EVENT, {"class_id": "warrior"})
        assert match.phase_started_at == 0
        assert match.last_gameplay_activity_at == 29
        assert not SOCKETS.run_lifecycle_sweep(
            socketio,
            now=29.999,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        ).detached_rooms
        exact = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=30,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert exact.detached_rooms[0].reason == "prep_absolute"
    return True


def scenario_lifecycle_combat_idle_expiration() -> bool:
    lifecycle = _lifecycle_policy()
    with _isolated_state() as (clock, policy):
        match = _register_match(make_match("warrior", "mage"), 0)
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            clock.set(5)
            _call(socketio, match.players[0], state.ACTION_EVENT, {"ability_id": "pass_turn"})
            assert match.last_gameplay_activity_at == 5
            clock.set(6)
            _call(socketio, match.players[0], state.ACTION_EVENT, {"ability_id": "pass_turn"})
            clock.set(7)
            _call(socketio, match.players[0], state.ACTION_EVENT, {"ability_id": "unknown"})
            clock.set(8)
            _call(socketio, match.players[0], state.CHAT_EVENT, "still here")
            assert match.last_gameplay_activity_at == 5
        assert not SOCKETS.run_lifecycle_sweep(
            socketio,
            now=19.999,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        ).detached_rooms
        exact = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=20,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert exact.detached_rooms[0].reason == "combat_idle"
    return True


def scenario_lifecycle_combat_absolute_expiration() -> bool:
    lifecycle = _lifecycle_policy(combat_idle_ttl_seconds=15, combat_absolute_ttl_seconds=60)
    with _isolated_state() as (clock, policy):
        match = _register_match(make_match("warrior", "mage"), 0)
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            for timestamp, sid in (
                (10, match.players[0]),
                (20, match.players[1]),
                (30, match.players[0]),
                (40, match.players[1]),
                (50, match.players[0]),
                (59, match.players[1]),
            ):
                clock.set(timestamp)
                _call(socketio, sid, state.ACTION_EVENT, {"ability_id": "pass_turn"})
        assert match.phase_started_at == 0
        assert match.last_gameplay_activity_at == 59
        exact = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=60,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert exact.detached_rooms[0].reason == "combat_absolute"
    return True


def scenario_lifecycle_ended_grace_period() -> bool:
    lifecycle = _lifecycle_policy(ended_grace_seconds=5)
    with _isolated_state() as (clock, policy):
        match = _register_match(make_match("warrior", "mage"), 0)
        socketio = FakeSocketIO()
        original_resolve = SOCKETS.resolver.resolve_turn

        def finish(current: MatchState) -> None:
            assert not state.state_lock.locked()
            current.phase = "ended"
            current.winner = current.players[0]
            current.submitted.clear()
            current.turn += 1
            current.turn_in_progress = False

        SOCKETS.resolver.resolve_turn = finish
        try:
            with _registered_handlers(socketio):
                clock.set(20)
                for sid in match.players:
                    _call(socketio, sid, state.ACTION_EVENT, {"ability_id": "pass_turn"})
                assert match.ended_at == 20
                assert sum(event == "duel_snapshot" for event, _p, _k in socketio.emitted) == 2
                clock.set(22)
                _call(socketio, match.players[0], state.CHAT_EVENT, "gg")
                SOCKETS.snapshot_for(match, match.players[0])
                assert match.ended_at == 20
        finally:
            SOCKETS.resolver.resolve_turn = original_resolve

        assert not SOCKETS.run_lifecycle_sweep(
            socketio,
            now=24.999,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        ).detached_rooms
        exact = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=25,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert exact.detached_rooms[0].reason == "ended_grace"
    return True


def scenario_lifecycle_authoritative_cleanup() -> bool:
    with _isolated_state():
        match = _create_room("cleanup-a", "cleanup-b")
        with state.state_lock:
            state.duel_queue.extend(match.players)
            state.queued_at_by_sid.update({sid: 0 for sid in match.players})
        with match.turn_lock:
            detached = state.detach_match_if_current(
                match,
                reason="test",
                message="Test cleanup.",
            )
            assert state.detach_match_if_current(
                match,
                reason="again",
                message="Again.",
            ) is None
        assert detached == state.DetachedRoom(
            match.room_id,
            tuple(match.players),
            "test",
            "Test cleanup.",
        )
        assert not state.duel_rooms and not state.sid_to_room
        assert not state.duel_queue and not state.queued_at_by_sid
        try:
            detached.reason = "changed"
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError("DetachedRoom must be immutable")

        newer = MatchState(room_id=match.room_id, players=["new-a", "new-b"])
        with state.state_lock:
            state.duel_rooms[newer.room_id] = newer
            state.sid_to_room.update({sid: newer.room_id for sid in newer.players})
        with match.turn_lock:
            assert state.detach_match_if_current(
                match,
                reason="stale",
                message="Stale.",
            ) is None
        assert state.duel_rooms[newer.room_id] is newer

        socketio = FakeSocketIO()
        assert not match.turn_lock.locked() and not state.state_lock.locked()
        SOCKETS.apply_detached_room_cleanup(socketio, detached)
        assert socketio.closed_rooms == [match.room_id]
    return True


def scenario_lifecycle_busy_match_skipped() -> bool:
    lifecycle = _lifecycle_policy()
    with _isolated_state() as (_clock, policy):
        busy = _create_room("busy-a", "busy-b")
        free = _create_room("free-a", "free-b")
        acquired = Event()
        release = Event()

        def hold_busy() -> None:
            with busy.turn_lock:
                acquired.send()
                release.wait()

        holder = eventlet.spawn(hold_busy)
        acquired.wait()
        socketio = FakeSocketIO()
        sweep = eventlet.spawn(
            SOCKETS.run_lifecycle_sweep,
            socketio,
            now=10,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        first = sweep.wait()
        assert first.skipped_busy == 1
        assert busy.room_id in state.duel_rooms
        assert free.room_id not in state.duel_rooms
        release.send()
        holder.wait()
        second = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=10,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert second.skipped_busy == 0
        assert busy.room_id not in state.duel_rooms
        assert not hasattr(busy, "availability_pending_cleanup_reason")
    return True


def scenario_lifecycle_disconnect_waits_cooperatively() -> bool:
    policy = _admission_policy(max_active_rooms=1)
    with _isolated_state(admission_policy=policy) as (clock, _policy):
        match = _create_room("disconnect-a", "disconnect-b")
        state.request_matchmaking("replacement-a", 2, now=0, policy=policy)
        state.request_matchmaking("replacement-b", 3, now=0, policy=policy)
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            acquired = Event()
            release = Event()

            def hold_match() -> None:
                with match.turn_lock:
                    acquired.send()
                    release.wait()

            holder = eventlet.spawn(hold_match)
            acquired.wait()
            SOCKETS.request.sid = "disconnect-a"
            disconnect = eventlet.spawn(socketio.handlers["disconnect"], "client disconnect")
            eventlet.sleep(0)
            assert match.room_id in state.duel_rooms
            assert state.consume_event_token("unrelated", state.CHAT_EVENT).allowed
            assert state.request_matchmaking(
                "unrelated-queue",
                4,
                now=clock(),
                policy=policy,
            ).match is None
            release.send()
            holder.wait()
            disconnect.wait()

        assert match.room_id not in state.duel_rooms
        replacement = next(iter(state.duel_rooms.values()))
        assert replacement.players == ["replacement-a", "replacement-b"]
        assert socketio.closed_rooms == [match.room_id]
    return True


def scenario_lifecycle_registry_lock_not_held_during_work() -> bool:
    policy = _admission_policy(max_active_rooms=2)
    lifecycle = _lifecycle_policy()
    with _isolated_state(admission_policy=policy) as (clock, _policy):
        socketio = FakeSocketIO()
        original_resolve = SOCKETS.resolver.resolve_turn
        resolver_checks: list[bool] = []

        def tracked_resolve(match: MatchState) -> None:
            resolver_checks.append(not state.state_lock.locked())
            original_resolve(match)

        SOCKETS.resolver.resolve_turn = tracked_resolve
        try:
            with _registered_handlers(socketio):
                _call(socketio, "setup-a", state.QUEUE_EVENT)
                _call(socketio, "setup-b", state.QUEUE_EVENT)
                match = state.get_match_by_sid("setup-a")
                assert match is not None
                _call(socketio, "setup-a", state.PREP_EVENT, {"class_id": "warrior"})
                _call(socketio, "setup-b", state.PREP_EVENT, {"class_id": "mage"})
                _call(socketio, "setup-a", state.LOCK_EVENT)
                _call(socketio, "setup-b", state.LOCK_EVENT)
                _call(socketio, "setup-a", state.ACTION_EVENT, {"ability_id": "pass_turn"})
                _call(socketio, "setup-b", state.ACTION_EVENT, {"ability_id": "pass_turn"})
                assert resolver_checks == [True]

                clock.set(match.last_gameplay_activity_at + lifecycle.combat_idle_ttl_seconds)
                result = SOCKETS.run_lifecycle_sweep(
                    socketio,
                    now=clock(),
                    lifecycle_policy=lifecycle,
                    admission_policy=policy,
                )
                assert result.detached_rooms
        finally:
            SOCKETS.resolver.resolve_turn = original_resolve
        assert socketio.entered_rooms
        assert socketio.closed_rooms
    return True


def scenario_lifecycle_one_sweeper_only() -> bool:
    with SOCKETS._lifecycle_sweeper_guard:
        saved_started = SOCKETS._lifecycle_sweeper_started
        SOCKETS._lifecycle_sweeper_started = False
    try:
        socketio = FakeSocketIO()
        assert SOCKETS.start_lifecycle_sweeper_once(socketio) is True
        assert SOCKETS.start_lifecycle_sweeper_once(socketio) is False
        assert len(socketio.started_tasks) == 1
        assert socketio.started_tasks[0][0] is SOCKETS.lifecycle_sweeper

        with SOCKETS._lifecycle_sweeper_guard:
            SOCKETS._lifecycle_sweeper_started = False
        failing = FakeSocketIO()
        failing.fail_start = True
        try:
            SOCKETS.start_lifecycle_sweeper_once(failing)
        except RuntimeError:
            pass
        else:
            raise AssertionError("task creation failure did not propagate")
        assert SOCKETS._lifecycle_sweeper_started is False

        original_sweep = SOCKETS.run_lifecycle_sweep
        iterations: list[bool] = []
        SOCKETS.run_lifecycle_sweep = lambda _socketio: iterations.append(True)
        try:
            loop_socketio = FakeSocketIO()
            try:
                SOCKETS.lifecycle_sweeper(loop_socketio)
            except _StopLoop:
                pass
            assert iterations == [True]
            assert loop_socketio.sleeps == [
                availability.LIFECYCLE_POLICY.sweep_interval_seconds
            ]
        finally:
            SOCKETS.run_lifecycle_sweep = original_sweep
    finally:
        with SOCKETS._lifecycle_sweeper_guard:
            SOCKETS._lifecycle_sweeper_started = saved_started

    source = Path(SOCKETS.__file__).read_text(encoding="utf-8")
    assert source.count("socketio.start_background_task(") == 1
    return True


def scenario_lifecycle_capacity_recovery() -> bool:
    policy = _admission_policy(max_active_rooms=2, queue_ttl_seconds=100)
    lifecycle = _lifecycle_policy()
    with _isolated_state(admission_policy=policy):
        old_rooms = (
            _create_room("old-1a", "old-1b"),
            _create_room("old-2a", "old-2b"),
        )
        for sid in ("wait-1", "wait-2", "wait-3", "wait-4"):
            state.request_matchmaking(sid, 10, now=1, policy=policy)
        socketio = FakeSocketIO()
        result = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=10,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert len(result.detached_rooms) == 2
        assert [match.players for match in state.duel_rooms.values()] == [
            ["wait-1", "wait-2"],
            ["wait-3", "wait-4"],
        ]
        assert len(state.duel_rooms) == policy.max_active_rooms
        assert len(result.replacement_room_ids) == len(set(result.replacement_room_ids)) == 2
        assert not ({room.room_id for room in old_rooms} & set(result.replacement_room_ids))
        assert len(socketio.entered_rooms) == 4
        replacements = list(state.duel_rooms.values())
        assert result.replacement_room_ids == tuple(
            replacement.room_id for replacement in replacements
        )
        for replacement in replacements:
            p1, p2 = replacement.players
            assert [
                entry for entry in socketio.entered_rooms
                if entry[0] == replacement.room_id
            ] == [(replacement.room_id, p1), (replacement.room_id, p2)]
            assert [
                (event, kwargs.get("to"))
                for event, _payload, kwargs in socketio.emitted
                if kwargs.get("to") in {p1, p2, replacement.room_id}
            ] == [
                ("duel_role", p1),
                ("duel_role", p2),
                ("duel_system", replacement.room_id),
                ("duel_prep_options", replacement.room_id),
                ("duel_snapshot", p1),
                ("duel_snapshot", p2),
            ]
        assert not any(
            payload == SOCKETS.MATCH_SETUP_FAILED_MESSAGE
            for _event, payload, _kwargs in socketio.emit_attempts
        )
    return True


def scenario_lifecycle_cleanup_failure_isolation() -> bool:
    policy = _admission_policy(max_active_rooms=2, queue_ttl_seconds=100)
    lifecycle = _lifecycle_policy()
    with _isolated_state(admission_policy=policy):
        first = _create_room("failure-1a", "failure-1b")
        second = _create_room("failure-2a", "failure-2b")
        for sid in ("next-1", "next-2", "next-3", "next-4"):
            state.request_matchmaking(sid, 20, now=1, policy=policy)
        socketio = FakeSocketIO()
        socketio.fail_emit_targets.add(first.room_id)
        socketio.fail_close_rooms.add(first.room_id)
        socketio.fail_setup_sids.add("next-1")
        with _captured_server_failures() as failures:
            result = SOCKETS.run_lifecycle_sweep(
                socketio,
                now=10,
                lifecycle_policy=lifecycle,
                admission_policy=policy,
            )
        assert len(failures) == 3
        assert len(result.detached_rooms) == 2
        assert second.room_id in socketio.closed_rooms
        assert len(state.duel_rooms) == 1
        replacement = state.get_match_by_sid("next-3")
        assert replacement is not None
        assert replacement.players == ["next-3", "next-4"]
        assert state.get_match_by_sid("next-4") is replacement
        assert result.replacement_room_ids == (replacement.room_id,)
        assert state.get_match_by_sid("next-1") is None
        assert state.get_match_by_sid("next-2") is None
        assert all(sid not in state.duel_queue for sid in ("next-1", "next-2"))

        socketio.fail_emit_targets.clear()
        socketio.fail_close_rooms.clear()
        socketio.fail_setup_sids.clear()
        later = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=20,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert len(later.detached_rooms) == 1
    return True


def scenario_lifecycle_partial_setup_failure_detaches_and_retries() -> bool:
    policy = _admission_policy(max_active_rooms=1, queue_ttl_seconds=100)
    lifecycle = _lifecycle_policy()
    with _isolated_state(admission_policy=policy):
        old = _create_room("old-a", "old-b")
        for sid in ("failed-a", "failed-b", "success-a", "success-b"):
            state.request_matchmaking(sid, 30, now=1, policy=policy)

        socketio = FakeSocketIO()
        socketio.fail_setup_sids.add("failed-b")
        failed_matches: list[MatchState] = []
        lock_observations: set[str] = set()
        original_deliver = SOCKETS.deliver_match_setup

        def tracked_deliver(active_socketio: Any, match: MatchState) -> bool:
            if match.players == ["failed-a", "failed-b"]:
                failed_matches.append(match)
            return original_deliver(active_socketio, match)

        def probe(operation: str, target: str) -> None:
            if not failed_matches:
                return
            failed = failed_matches[0]
            observation = None
            if operation == "emit" and target in failed.players:
                observation = "direct_notice"
            elif operation == "close_room" and target == failed.room_id:
                observation = "partial_close"
            elif target in {"success-a", "success-b"}:
                observation = "later_setup"
            if observation is not None:
                assert not state.state_lock.locked()
                assert not failed.turn_lock.locked()
                lock_observations.add(observation)

        SOCKETS.deliver_match_setup = tracked_deliver
        socketio.transport_probe = probe
        try:
            with _captured_server_failures() as failures:
                result = SOCKETS.run_lifecycle_sweep(
                    socketio,
                    now=10,
                    lifecycle_policy=lifecycle,
                    admission_policy=policy,
                )
        finally:
            SOCKETS.deliver_match_setup = original_deliver

        assert failures == [
            f"Failed replacement setup for room {failed_matches[0].room_id}"
        ]
        failed = failed_matches[0]
        assert failed.room_id not in state.duel_rooms
        assert all(sid not in state.sid_to_room for sid in failed.players)
        assert all(sid not in state.duel_queue for sid in failed.players)
        assert socketio.entered_rooms.count((failed.room_id, "failed-a")) == 1
        assert (failed.room_id, "failed-b") not in socketio.entered_rooms
        assert failed.room_id in socketio.closed_rooms
        assert {
            kwargs.get("to")
            for event, payload, kwargs in socketio.emitted
            if event == "duel_system" and payload == SOCKETS.MATCH_SETUP_FAILED_MESSAGE
        } == {"failed-a", "failed-b"}

        success = state.get_match_by_sid("success-a")
        assert success is not None
        assert success.players == ["success-a", "success-b"]
        assert state.get_match_by_sid("success-b") is success
        assert result.replacement_room_ids == (success.room_id,)
        assert len(state.duel_rooms) == policy.max_active_rooms
        assert {
            sid for room_id, sid in socketio.entered_rooms
            if room_id == success.room_id
        } == {"success-a", "success-b"}
        assert old.room_id in socketio.closed_rooms
        assert lock_observations == {
            "direct_notice",
            "partial_close",
            "later_setup",
        }
    return True


def scenario_lifecycle_initial_emit_setup_failure_detaches_and_retries() -> bool:
    policy = _admission_policy(max_active_rooms=1, queue_ttl_seconds=100)
    lifecycle = _lifecycle_policy()
    with _isolated_state(admission_policy=policy):
        _create_room("old-a", "old-b")
        for sid in ("failed-a", "failed-b", "success-a", "success-b"):
            state.request_matchmaking(sid, 31, now=1, policy=policy)

        socketio = FakeSocketIO()
        socketio.fail_emit_once_targets.add("failed-a")
        failed_matches: list[MatchState] = []
        original_deliver = SOCKETS.deliver_match_setup

        def tracked_deliver(active_socketio: Any, match: MatchState) -> bool:
            if match.players == ["failed-a", "failed-b"]:
                failed_matches.append(match)
            return original_deliver(active_socketio, match)

        SOCKETS.deliver_match_setup = tracked_deliver
        try:
            with _captured_server_failures():
                result = SOCKETS.run_lifecycle_sweep(
                    socketio,
                    now=10,
                    lifecycle_policy=lifecycle,
                    admission_policy=policy,
                )
        finally:
            SOCKETS.deliver_match_setup = original_deliver

        failed = failed_matches[0]
        assert [
            entry for entry in socketio.entered_rooms if entry[0] == failed.room_id
        ] == [(failed.room_id, "failed-a"), (failed.room_id, "failed-b")]
        assert failed.room_id not in state.duel_rooms
        assert all(sid not in state.sid_to_room for sid in failed.players)
        assert failed.room_id in socketio.closed_rooms
        assert {
            kwargs.get("to")
            for event, payload, kwargs in socketio.emitted
            if event == "duel_system" and payload == SOCKETS.MATCH_SETUP_FAILED_MESSAGE
        } == {"failed-a", "failed-b"}
        success = state.get_match_by_sid("success-a")
        assert success is not None
        assert state.get_match_by_sid("success-b") is success
        assert result.replacement_room_ids == (success.room_id,)
    return True


def scenario_lifecycle_stale_replacement_setup_retries_same_slot() -> bool:
    policy = _admission_policy(max_active_rooms=1, queue_ttl_seconds=100)
    with _isolated_state(admission_policy=policy):
        old = _create_room("old-a", "old-b")
        for sid in ("stale-a", "stale-b", "success-a", "success-b"):
            state.request_matchmaking(sid, 32, now=1, policy=policy)
        with old.turn_lock:
            assert state.detach_match_if_current(
                old,
                reason="test_release",
                message="Test capacity release.",
            ) is not None

        socketio = FakeSocketIO()
        replacement_ready = Event()
        replacement_detached = Event()
        stale_matches: list[MatchState] = []
        pairing_seeds: list[int] = []
        detach_calls: list[MatchState] = []
        original_pair = state.try_pair_waiting
        original_detach = state.detach_match_if_current

        def tracked_pair(seed: int, **kwargs: Any) -> MatchState | None:
            pairing_seeds.append(seed)
            replacement = original_pair(seed, **kwargs)
            if not stale_matches and replacement is not None:
                stale_matches.append(replacement)
                replacement_ready.send()
                replacement_detached.wait()
            return replacement

        def tracked_detach(match: MatchState, **kwargs: Any) -> Any:
            detach_calls.append(match)
            return original_detach(match, **kwargs)

        def detach_before_setup() -> None:
            replacement_ready.wait()
            stale = stale_matches[0]
            with stale.turn_lock:
                detached = state.detach_match_if_current(
                    stale,
                    reason="test_stale",
                    message="Test stale replacement.",
                )
            assert detached is not None
            replacement_detached.send()

        state.try_pair_waiting = tracked_pair
        state.detach_match_if_current = tracked_detach
        try:
            detacher = eventlet.spawn(detach_before_setup)
            recovery = eventlet.spawn(
                SOCKETS._recover_room_capacity,
                socketio,
                1,
                now=10,
                admission_policy=policy,
            )
            replacement_ids = recovery.wait()
            detacher.wait()
        finally:
            state.try_pair_waiting = original_pair
            state.detach_match_if_current = original_detach

        stale = stale_matches[0]
        assert pairing_seeds == [10000, 10001]
        assert detach_calls == [stale]
        assert stale.room_id not in state.duel_rooms
        assert all(sid not in state.sid_to_room for sid in stale.players)
        assert not any(
            payload == SOCKETS.MATCH_SETUP_FAILED_MESSAGE
            for _event, payload, _kwargs in socketio.emit_attempts
        )
        assert stale.room_id not in socketio.close_attempts
        success = state.get_match_by_sid("success-a")
        assert success is not None
        assert state.get_match_by_sid("success-b") is success
        assert replacement_ids == (success.room_id,)
    return True


def scenario_lifecycle_failed_setup_transport_failure_isolation() -> bool:
    policy = _admission_policy(max_active_rooms=2, queue_ttl_seconds=100)
    lifecycle = _lifecycle_policy()
    with _isolated_state(admission_policy=policy):
        old_rooms = (
            _create_room("old-1a", "old-1b"),
            _create_room("old-2a", "old-2b"),
        )
        for sid in (
            "failed-a",
            "failed-b",
            "success-1a",
            "success-1b",
            "success-2a",
            "success-2b",
        ):
            state.request_matchmaking(sid, 33, now=1, policy=policy)

        socketio = FakeSocketIO()
        socketio.fail_setup_sids.add("failed-b")
        socketio.fail_emit_targets.add("failed-a")
        failed_matches: list[MatchState] = []
        original_deliver = SOCKETS.deliver_match_setup

        def tracked_deliver(active_socketio: Any, match: MatchState) -> bool:
            if match.players == ["failed-a", "failed-b"]:
                failed_matches.append(match)
                socketio.fail_close_rooms.add(match.room_id)
            return original_deliver(active_socketio, match)

        SOCKETS.deliver_match_setup = tracked_deliver
        try:
            with _captured_server_failures() as failures:
                result = SOCKETS.run_lifecycle_sweep(
                    socketio,
                    now=10,
                    lifecycle_policy=lifecycle,
                    admission_policy=policy,
                )
        finally:
            SOCKETS.deliver_match_setup = original_deliver

        failed = failed_matches[0]
        assert len(failures) == 3
        assert failed.room_id not in state.duel_rooms
        assert all(sid not in state.sid_to_room for sid in failed.players)
        assert all(sid not in state.duel_queue for sid in failed.players)
        assert {
            kwargs.get("to")
            for event, payload, kwargs in socketio.emit_attempts
            if event == "duel_system" and payload == SOCKETS.MATCH_SETUP_FAILED_MESSAGE
        } == {"failed-a", "failed-b"}
        assert failed.room_id in socketio.close_attempts
        assert failed.room_id not in socketio.closed_rooms
        assert len(result.replacement_room_ids) == 2
        assert set(result.replacement_room_ids) == set(state.duel_rooms)
        assert [match.players for match in state.duel_rooms.values()] == [
            ["success-1a", "success-1b"],
            ["success-2a", "success-2b"],
        ]
        assert len(state.duel_rooms) == policy.max_active_rooms
        assert all(room.room_id in socketio.closed_rooms for room in old_rooms)

        later = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=20,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert len(later.detached_rooms) == 2
        assert not state.duel_rooms
    return True


def scenario_lifecycle_direct_matchmaking_stale_setup_notifies_requester() -> bool:
    policy = _admission_policy(max_active_rooms=1, queue_ttl_seconds=100)
    with _isolated_state(admission_policy=policy):
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            _call(socketio, "waiting-peer", state.QUEUE_EVENT)
            assert state.duel_queue == ["waiting-peer"]
            socketio.direct_emitted.clear()

            setup_ready = Event()
            resume_setup = Event()
            created_matches: list[MatchState] = []
            matchmaking_calls: list[str] = []
            detach_calls: list[MatchState] = []
            original_deliver = SOCKETS.deliver_match_setup
            original_request_matchmaking = state.request_matchmaking
            original_detach = state.detach_match_if_current

            def paused_deliver(active_socketio: Any, match: MatchState) -> bool:
                created_matches.append(match)
                setup_ready.send()
                resume_setup.wait()
                # The harness uses a SimpleNamespace in place of Flask's
                # greenthread-local request proxy; restore the requester here.
                SOCKETS.request.sid = "requester"
                return original_deliver(active_socketio, match)

            def tracked_request_matchmaking(sid: str, seed: int, **kwargs: Any) -> Any:
                matchmaking_calls.append(sid)
                return original_request_matchmaking(sid, seed, **kwargs)

            def tracked_detach(match: MatchState, **kwargs: Any) -> Any:
                detach_calls.append(match)
                return original_detach(match, **kwargs)

            def request_match() -> None:
                SOCKETS.request.sid = "requester"
                socketio.handlers[state.QUEUE_EVENT]()

            def disconnect_waiting_peer() -> None:
                SOCKETS.request.sid = "waiting-peer"
                socketio.handlers["disconnect"]("client disconnect")

            SOCKETS.deliver_match_setup = paused_deliver
            state.request_matchmaking = tracked_request_matchmaking
            state.detach_match_if_current = tracked_detach
            requester = None
            try:
                requester = eventlet.spawn(request_match)
                eventlet.sleep(0)
                setup_ready.wait()
                match = created_matches[0]
                assert state.get_match_by_sid("requester") is match

                disconnect = eventlet.spawn(disconnect_waiting_peer)
                eventlet.sleep(0)
                disconnect.wait()
                close_attempts_after_disconnect = tuple(socketio.close_attempts)
                assert close_attempts_after_disconnect == (match.room_id,)
                assert any(
                    event == "duel_system"
                    and payload == "Opponent disconnected."
                    and kwargs.get("to") == match.room_id
                    for event, payload, kwargs in socketio.emitted
                )

                SOCKETS.request.sid = "requester"
                resume_setup.send()
                eventlet.sleep(0)
                requester.wait()
            finally:
                if not resume_setup.ready():
                    resume_setup.send()
                SOCKETS.deliver_match_setup = original_deliver
                state.request_matchmaking = original_request_matchmaking
                state.detach_match_if_current = original_detach

            assert match.room_id not in state.duel_rooms
            assert "requester" not in state.duel_queue
            assert "requester" not in state.queued_at_by_sid
            assert "requester" not in state.sid_to_room
            assert state.get_match_by_sid("requester") is None
            assert socketio.direct_emitted == [
                (
                    "requester",
                    "duel_system",
                    SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE,
                    {},
                )
            ]
            assert not any(
                sid == "waiting-peer"
                and payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                for sid, _event, payload, _kwargs in socketio.direct_emitted
            )
            assert not any(
                payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                and kwargs.get("to") == match.room_id
                for _event, payload, kwargs in socketio.emitted
            )
            assert not any(
                event in {"duel_role", "duel_prep_options", "duel_snapshot"}
                for event, _payload, _kwargs in socketio.emitted
            )
            assert not any(
                payload == "Match found. Prep phase: pick class + items."
                for _event, payload, _kwargs in socketio.emitted
            )
            assert not any(
                payload in {
                    "Queued for DUEL...",
                    "Match found. Prep phase: pick class + items.",
                }
                for _sid, _event, payload, _kwargs in socketio.direct_emitted
            )
            assert matchmaking_calls == ["requester"]
            assert detach_calls == [match]
            assert tuple(socketio.close_attempts) == close_attempts_after_disconnect
            assert not state.duel_queue
            assert not state.queued_at_by_sid
            assert not state.sid_to_room

    # A queue request can opportunistically set up two older waiting SIDs.
    # If that unrelated match becomes stale, the current requester keeps its
    # real queued result instead of receiving the stale match's retry notice.
    with _isolated_state(admission_policy=policy):
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            occupied = _create_room("occupied-a", "occupied-b")
            _call(socketio, "older-a", state.QUEUE_EVENT)
            _call(socketio, "older-b", state.QUEUE_EVENT)
            assert state.duel_queue == ["older-a", "older-b"]
            with occupied.turn_lock:
                assert state.detach_match_if_current(
                    occupied,
                    reason="test_capacity_release",
                    message="test",
                ) is not None
            socketio.direct_emitted.clear()

            setup_ready = Event()
            resume_setup = Event()
            created_matches: list[MatchState] = []
            original_deliver = SOCKETS.deliver_match_setup

            def paused_unrelated_deliver(
                active_socketio: Any,
                match: MatchState,
            ) -> bool:
                created_matches.append(match)
                setup_ready.send()
                resume_setup.wait()
                SOCKETS.request.sid = "requester"
                return original_deliver(active_socketio, match)

            def request_match() -> None:
                SOCKETS.request.sid = "requester"
                socketio.handlers[state.QUEUE_EVENT]()

            def disconnect_older_peer() -> None:
                SOCKETS.request.sid = "older-a"
                socketio.handlers["disconnect"]("client disconnect")

            SOCKETS.deliver_match_setup = paused_unrelated_deliver
            requester = None
            try:
                requester = eventlet.spawn(request_match)
                eventlet.sleep(0)
                setup_ready.wait()
                stale_match = created_matches[0]
                assert stale_match.players == ["older-a", "older-b"]
                assert "requester" not in stale_match.players
                assert state.duel_queue == ["requester"]

                disconnect = eventlet.spawn(disconnect_older_peer)
                eventlet.sleep(0)
                disconnect.wait()
                close_attempts_after_disconnect = tuple(socketio.close_attempts)
                assert close_attempts_after_disconnect == (stale_match.room_id,)

                SOCKETS.request.sid = "requester"
                resume_setup.send()
                eventlet.sleep(0)
                requester.wait()
            finally:
                if not resume_setup.ready():
                    resume_setup.send()
                SOCKETS.deliver_match_setup = original_deliver

            assert state.duel_queue == ["requester"]
            assert "requester" in state.queued_at_by_sid
            assert "requester" not in state.sid_to_room
            assert socketio.direct_emitted == [
                ("requester", "duel_system", "Queued for DUEL...", {})
            ]
            assert not any(
                payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                for _sid, _event, payload, _kwargs in socketio.direct_emitted
            )
            assert not any(
                payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                for _event, payload, _kwargs in socketio.emitted
            )
            assert tuple(socketio.close_attempts) == close_attempts_after_disconnect

    # Revalidation also runs after a successful unrelated setup. While that
    # setup yields, another room can release capacity and recovery can match the
    # requester; the original handler must not then append a stale queued ack.
    rematch_policy = _admission_policy(max_active_rooms=2, queue_ttl_seconds=100)
    with _isolated_state(admission_policy=rematch_policy):
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            released_before_request = _create_room("release-a", "release-b")
            released_during_setup = _create_room("during-a", "during-b")
            _call(socketio, "older-a", state.QUEUE_EVENT)
            _call(socketio, "older-b", state.QUEUE_EVENT)
            assert state.duel_queue == ["older-a", "older-b"]
            with released_before_request.turn_lock:
                assert state.detach_match_if_current(
                    released_before_request,
                    reason="test_capacity_release",
                    message="test",
                ) is not None
            socketio.direct_emitted.clear()

            setup_ready = Event()
            resume_setup = Event()
            created_matches: list[MatchState] = []
            original_deliver = SOCKETS.deliver_match_setup

            def paused_first_deliver(
                active_socketio: Any,
                match: MatchState,
            ) -> bool:
                if not created_matches:
                    created_matches.append(match)
                    setup_ready.send()
                    resume_setup.wait()
                    SOCKETS.request.sid = "requester"
                return original_deliver(active_socketio, match)

            def request_match() -> None:
                SOCKETS.request.sid = "requester"
                socketio.handlers[state.QUEUE_EVENT]()

            def disconnect_capacity_room() -> None:
                SOCKETS.request.sid = "during-a"
                socketio.handlers["disconnect"]("client disconnect")

            SOCKETS.deliver_match_setup = paused_first_deliver
            requester = None
            try:
                requester = eventlet.spawn(request_match)
                eventlet.sleep(0)
                setup_ready.wait()
                unrelated_match = created_matches[0]
                assert unrelated_match.players == ["older-a", "older-b"]
                assert state.duel_queue == ["requester"]

                _call(socketio, "later-peer", state.QUEUE_EVENT)
                assert state.duel_queue == ["requester", "later-peer"]
                socketio.direct_emitted.clear()

                disconnect = eventlet.spawn(disconnect_capacity_room)
                eventlet.sleep(0)
                disconnect.wait()
                requester_match = state.get_match_by_sid("requester")
                assert requester_match is not None
                assert requester_match.players == ["requester", "later-peer"]
                assert requester_match is not unrelated_match

                SOCKETS.request.sid = "requester"
                resume_setup.send()
                eventlet.sleep(0)
                requester.wait()
            finally:
                if not resume_setup.ready():
                    resume_setup.send()
                SOCKETS.deliver_match_setup = original_deliver

            assert state.get_match_by_sid("requester") is requester_match
            assert not state.duel_queue
            assert "requester" not in state.queued_at_by_sid
            assert socketio.direct_emitted == []
            assert not any(
                payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                or payload == "Queued for DUEL..."
                for _event, payload, _kwargs in socketio.emitted
            )
            assert socketio.closed_rooms == [released_during_setup.room_id]
    return True


def scenario_lifecycle_ordinary_two_tab_duel() -> bool:
    policy = _admission_policy(max_active_rooms=1)
    lifecycle = _lifecycle_policy(ended_grace_seconds=5)
    with _isolated_state(admission_policy=policy) as (clock, _policy):
        socketio = FakeSocketIO()
        with _registered_handlers(socketio):
            _call(socketio, "tab-a", state.QUEUE_EVENT)
            _call(socketio, "tab-b", state.QUEUE_EVENT)
            match = state.get_match_by_sid("tab-a")
            assert match is not None
            assert state.get_match_by_sid("tab-b") is match
            assert socketio.entered_rooms == [
                (match.room_id, "tab-a"),
                (match.room_id, "tab-b"),
            ]
            assert [
                (event, kwargs.get("to"))
                for event, _payload, kwargs in socketio.emitted
                if kwargs.get("to") in {"tab-a", "tab-b", match.room_id}
            ] == [
                ("duel_role", "tab-a"),
                ("duel_role", "tab-b"),
                ("duel_system", match.room_id),
                ("duel_prep_options", match.room_id),
                ("duel_snapshot", "tab-a"),
                ("duel_snapshot", "tab-b"),
            ]
            assert not any(
                payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                for _sid, _event, payload, _kwargs in socketio.direct_emitted
            )
            assert not any(
                payload == SOCKETS.MATCH_SETUP_INTERRUPTED_MESSAGE
                for _event, payload, _kwargs in socketio.emitted
            )
            _call(socketio, "tab-a", state.PREP_EVENT, {"class_id": "warrior"})
            _call(socketio, "tab-b", state.PREP_EVENT, {"class_id": "mage"})
            _call(socketio, "tab-a", state.LOCK_EVENT)
            _call(socketio, "tab-b", state.LOCK_EVENT)
            assert match.phase == "combat"
            _call(socketio, "tab-a", state.ACTION_EVENT, {"ability_id": "pass_turn"})
            _call(socketio, "tab-b", state.ACTION_EVENT, {"ability_id": "pass_turn"})
            assert match.log[:3] == [
                "Turn 1",
                "tab-a uses their bare hands to cast Pass Turn. Passes the turn.",
                "tab-b uses their bare hands to cast Pass Turn. Passes the turn.",
            ]
            _call(socketio, "tab-a", state.CHAT_EVENT, "gg")
            match.state["tab-b"].res.hp = 0
            clock.set(20)
            _call(socketio, "tab-a", state.ACTION_EVENT, {"ability_id": "pass_turn"})
            _call(socketio, "tab-b", state.ACTION_EVENT, {"ability_id": "pass_turn"})
            assert match.phase == "ended" and match.ended_at == 20
            assert match.winner == "tab-a"

        assert not SOCKETS.run_lifecycle_sweep(
            socketio,
            now=24.999,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        ).detached_rooms
        assert SOCKETS.run_lifecycle_sweep(
            socketio,
            now=25,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        ).detached_rooms
    return True


def scenario_lifecycle_exact_boundaries_and_source_guardrails() -> bool:
    policy = _lifecycle_policy()
    cases = (
        ("prep", 0, 0, None, 9.999, 10, "prep_idle"),
        ("prep", 0, 29, None, 29.999, 30, "prep_absolute"),
        ("combat", 0, 0, None, 14.999, 15, "combat_idle"),
        ("combat", 0, 59, None, 59.999, 60, "combat_absolute"),
        ("ended", 0, 0, 20, 24.999, 25, "ended_grace"),
    )
    for phase, phase_at, activity_at, ended_at, before, exact, reason in cases:
        match = MatchState(room_id=reason, players=["a", "b"], phase=phase)
        match.phase_started_at = phase_at
        match.last_gameplay_activity_at = activity_at
        match.ended_at = ended_at
        assert SOCKETS._match_expiration(match, before, policy) is None
        assert SOCKETS._match_expiration(match, exact, policy)[0] == reason

    configured = availability.load_lifecycle_policy({
        "MAKGORA_PREP_IDLE_TTL_SECONDS": "1",
        "MAKGORA_PREP_ABSOLUTE_TTL_SECONDS": "1",
        "MAKGORA_COMBAT_IDLE_TTL_SECONDS": "2",
        "MAKGORA_COMBAT_ABSOLUTE_TTL_SECONDS": "2",
        "MAKGORA_ENDED_GRACE_SECONDS": "0.5",
        "MAKGORA_LIFECYCLE_SWEEP_INTERVAL_SECONDS": "0.001",
    })
    assert configured.sweep_interval_seconds == 0.001
    invalid = (
        {"MAKGORA_PREP_IDLE_TTL_SECONDS": "0"},
        {"MAKGORA_PREP_IDLE_TTL_SECONDS": "nan"},
        {"MAKGORA_ENDED_GRACE_SECONDS": "inf"},
        {"MAKGORA_PREP_IDLE_TTL_SECONDS": "2", "MAKGORA_PREP_ABSOLUTE_TTL_SECONDS": "1"},
        {"MAKGORA_COMBAT_IDLE_TTL_SECONDS": "3", "MAKGORA_COMBAT_ABSOLUTE_TTL_SECONDS": "2"},
        {"MAKGORA_LIFECYCLE_SWEEP_INTERVAL_SECONDS": "0"},
    )
    for environment in invalid:
        try:
            availability.load_lifecycle_policy(environment)
        except ValueError as exc:
            assert "Invalid Mak'Gora lifecycle configuration" in str(exc)
        else:
            raise AssertionError(f"Invalid lifecycle policy accepted: {environment}")

    model_source = Path(sys.modules[MatchState.__module__].__file__).read_text(encoding="utf-8")
    state_source = Path(state.__file__).read_text(encoding="utf-8")
    socket_source = Path(SOCKETS.__file__).read_text(encoding="utf-8")
    combined = model_source + state_source + socket_source
    assert "from threading import Lock" not in model_source
    assert "from threading import RLock" not in model_source
    assert "eventlet.monkey_patch" not in combined
    assert socket_source.count("socketio.start_background_task(") == 1
    for forbidden in (
        "availability_transport_setup_in_progress",
        "availability_resolution_in_progress",
        "availability_pending_cleanup_reason",
        "availability_pending_cleanup_message",
        "availability_closed",
        "cleanup_lease",
        "deferred_cleanup",
    ):
        assert forbidden not in combined
    assert "time.time" not in inspect.getsource(SOCKETS._match_expiration)
    chat_handler_source = socket_source.split(
        '    @socketio.on("duel_chat")',
        1,
    )[1].split('    @socketio.on("disconnect")', 1)[0]
    assert "last_gameplay_activity_at =" not in chat_handler_source
    assert math.isfinite(availability.LIFECYCLE_POLICY.sweep_interval_seconds)
    return True
