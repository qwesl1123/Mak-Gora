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
        self.socketio._assert_registry_unlocked()
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
        self.fail_emit_targets: set[str] = set()
        self.fail_close_rooms: set[str] = set()
        self.fail_setup_sids: set[str] = set()
        self.fail_start = False
        self.server = FakeSocketIOServer(self)

    def _assert_registry_unlocked(self) -> None:
        assert not state.state_lock.locked(), "transport ran while state_lock was held"

    def on(self, event: str) -> Any:
        def register(handler: Any) -> Any:
            self.handlers[event] = handler
            return handler

        return register

    def emit(self, event: str, payload: Any = None, **kwargs: Any) -> None:
        self._assert_registry_unlocked()
        target = kwargs.get("to")
        if target in self.fail_emit_targets:
            raise RuntimeError("forced emit failure")
        self.emitted.append((event, payload, kwargs))

    def close_room(self, room_id: str) -> None:
        self._assert_registry_unlocked()
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
        assert len(state.duel_rooms) == 2
        assert any(sid == "next-3" for _room, sid in socketio.entered_rooms)

        socketio.fail_emit_targets.clear()
        socketio.fail_close_rooms.clear()
        socketio.fail_setup_sids.clear()
        later = SOCKETS.run_lifecycle_sweep(
            socketio,
            now=20,
            lifecycle_policy=lifecycle,
            admission_policy=policy,
        )
        assert len(later.detached_rooms) == 2
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
