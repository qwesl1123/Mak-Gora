"""Process-local matchmaking, room-registry, and event-rate state."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from eventlet.semaphore import Semaphore

from .availability import ADMISSION_POLICY, AdmissionPolicy, EventRate
from .engine.models import MatchState


QUEUE_EVENT = "duel_queue"
PREP_EVENT = "duel_prep_submit"
LOCK_EVENT = "duel_lock_in"
ACTION_EVENT = "duel_action"
CHAT_EVENT = "duel_chat"
PROTECTED_EVENTS = frozenset({
    QUEUE_EVENT,
    PREP_EVENT,
    LOCK_EVENT,
    ACTION_EVENT,
    CHAT_EVENT,
})
_EVENT_RATE_FIELDS = {
    QUEUE_EVENT: "queue_rate",
    PREP_EVENT: "prep_rate",
    LOCK_EVENT: "lock_rate",
    ACTION_EVENT: "action_rate",
    CHAT_EVENT: "chat_rate",
}


@dataclass(frozen=True)
class MatchmakingResult:
    status: str
    match: MatchState | None = None
    newly_queued: bool = False
    expired_queue_sids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetachedRoom:
    room_id: str
    players: tuple[str, ...]
    reason: str
    message: str


@dataclass
class TokenBucket:
    tokens: float
    last_refill_at: float


@dataclass
class SidLimiterRecord:
    buckets: Dict[str, TokenBucket] = field(default_factory=dict)
    last_seen_at: float = 0.0
    last_warning_at: float | None = None


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    emit_warning: bool = False


# Eventlet's Semaphore is deliberately treated as non-reentrant. Public
# functions acquire it once; private *_locked helpers require ownership and
# never emit, call Socket.IO, resolve turns, or yield intentionally.
state_lock = Semaphore(1)
duel_queue: List[str] = []
queued_at_by_sid: Dict[str, float] = {}
duel_rooms: Dict[str, MatchState] = {}
sid_to_room: Dict[str, str] = {}
limiter_records: Dict[str, SidLimiterRecord] = {}
_next_room_sequence = 0
monotonic_clock = time.monotonic


def _policy(policy: AdmissionPolicy | None) -> AdmissionPolicy:
    return ADMISSION_POLICY if policy is None else policy


def _now(now: float | None) -> float:
    return monotonic_clock() if now is None else now


def current_monotonic_time() -> float:
    return _now(None)


def _expire_queued_sids_locked(now: float, policy: AdmissionPolicy) -> tuple[str, ...]:
    kept: list[str] = []
    expired: list[str] = []
    seen: set[str] = set()
    for sid in duel_queue:
        if sid in seen:
            continue
        seen.add(sid)
        queued_at = queued_at_by_sid.get(sid)
        if queued_at is None or now >= queued_at + policy.queue_ttl_seconds:
            expired.append(sid)
            queued_at_by_sid.pop(sid, None)
        else:
            kept.append(sid)

    duel_queue[:] = kept
    queued = set(kept)
    for sid in tuple(queued_at_by_sid):
        if sid not in queued:
            queued_at_by_sid.pop(sid, None)
    return tuple(expired)


def expire_queued_sids(
    *,
    now: float | None = None,
    policy: AdmissionPolicy | None = None,
) -> tuple[str, ...]:
    active_policy = _policy(policy)
    with state_lock:
        return _expire_queued_sids_locked(_now(now), active_policy)


def _remove_queued_sid_locked(sid: str) -> bool:
    removed = sid in duel_queue or sid in queued_at_by_sid
    if sid in duel_queue:
        duel_queue[:] = [queued_sid for queued_sid in duel_queue if queued_sid != sid]
    queued_at_by_sid.pop(sid, None)
    return removed


def dequeue(sid: str) -> bool:
    with state_lock:
        return _remove_queued_sid_locked(sid)


def _allocate_room_id_locked() -> str:
    global _next_room_sequence
    _next_room_sequence += 1
    return f"duel-{_next_room_sequence:016x}"


def _create_room_locked(
    p1: str,
    p2: str,
    seed: int,
    now: float | None = None,
) -> MatchState:
    room_id = _allocate_room_id_locked()
    created_at = _now(now)
    match = MatchState(
        room_id=room_id,
        players=[p1, p2],
        seed=seed,
        created_at=created_at,
        phase_started_at=created_at,
        last_gameplay_activity_at=created_at,
    )
    duel_rooms[room_id] = match
    sid_to_room[p1] = room_id
    sid_to_room[p2] = room_id
    return match


def _try_pair_waiting_locked(
    seed: int,
    now: float,
    policy: AdmissionPolicy,
) -> MatchState | None:
    _expire_queued_sids_locked(now, policy)
    if len(duel_rooms) >= policy.max_active_rooms or len(duel_queue) < 2:
        return None

    p1, p2 = duel_queue[0], duel_queue[1]
    del duel_queue[:2]
    queued_at_by_sid.pop(p1, None)
    queued_at_by_sid.pop(p2, None)
    return _create_room_locked(p1, p2, seed, now)


def try_pair_waiting(
    seed: int,
    *,
    now: float | None = None,
    policy: AdmissionPolicy | None = None,
) -> MatchState | None:
    active_policy = _policy(policy)
    with state_lock:
        return _try_pair_waiting_locked(seed, _now(now), active_policy)


def request_matchmaking(
    sid: str,
    seed: int,
    *,
    now: float | None = None,
    policy: AdmissionPolicy | None = None,
) -> MatchmakingResult:
    active_policy = _policy(policy)
    current_time = _now(now)
    with state_lock:
        expired_queue_sids = _expire_queued_sids_locked(
            current_time,
            active_policy,
        )
        room_id = sid_to_room.get(sid)
        if room_id in duel_rooms:
            return MatchmakingResult(
                "already_in_duel",
                expired_queue_sids=expired_queue_sids,
            )

        already_queued = sid in duel_queue
        if not already_queued:
            if len(duel_queue) >= active_policy.max_queued_sids:
                return MatchmakingResult(
                    "queue_full",
                    expired_queue_sids=expired_queue_sids,
                )
            duel_queue.append(sid)
            queued_at_by_sid[sid] = current_time

        match = _try_pair_waiting_locked(seed, current_time, active_policy)
        if match is not None:
            if sid in match.players:
                status = "matched"
            else:
                status = "already_queued" if already_queued else "queued"
            return MatchmakingResult(
                status,
                match,
                not already_queued,
                expired_queue_sids,
            )
        if len(duel_rooms) >= active_policy.max_active_rooms and not already_queued:
            return MatchmakingResult(
                "room_full",
                newly_queued=True,
                expired_queue_sids=expired_queue_sids,
            )
        return MatchmakingResult(
            "already_queued" if already_queued else "queued",
            newly_queued=not already_queued,
            expired_queue_sids=expired_queue_sids,
        )


def get_match_by_sid(sid: str) -> Optional[MatchState]:
    with state_lock:
        room_id = sid_to_room.get(sid)
        return duel_rooms.get(room_id) if room_id else None


def is_sid_queued(sid: str) -> bool:
    with state_lock:
        return sid in duel_queue


def is_registered_match(
    match: MatchState,
    *,
    sid: str | None = None,
) -> bool:
    with state_lock:
        if duel_rooms.get(match.room_id) is not match:
            return False
        if sid is not None and sid_to_room.get(sid) != match.room_id:
            return False
        return True


def registered_matches_snapshot() -> tuple[MatchState, ...]:
    with state_lock:
        return tuple(duel_rooms.values())


def detach_match_if_current(
    match: MatchState,
    *,
    reason: str,
    message: str,
) -> DetachedRoom | None:
    """Atomically detach ``match``; caller must hold ``match.turn_lock``."""
    with state_lock:
        if duel_rooms.get(match.room_id) is not match:
            return None
        duel_rooms.pop(match.room_id)
        players = tuple(match.players)
        for sid in players:
            if sid_to_room.get(sid) == match.room_id:
                sid_to_room.pop(sid, None)
            _remove_queued_sid_locked(sid)
        return DetachedRoom(
            room_id=match.room_id,
            players=players,
            reason=reason,
            message=message,
        )


def cleanup_room(room_id: str) -> MatchState | None:
    """Compatibility coordinator for explicit room cleanup."""
    with state_lock:
        match = duel_rooms.get(room_id)
    if match is None:
        return None
    with match.turn_lock:
        detached = detach_match_if_current(
            match,
            reason="explicit_cleanup",
            message="Duel room closed.",
        )
    return match if detached is not None else None


def _remove_limiter_record_locked(sid: str) -> bool:
    return limiter_records.pop(sid, None) is not None


def disconnect_sid(sid: str) -> MatchState | None:
    """Immediately drop queue/limiter state and return any current match."""
    with state_lock:
        _remove_queued_sid_locked(sid)
        _remove_limiter_record_locked(sid)
        room_id = sid_to_room.get(sid)
        return duel_rooms.get(room_id) if room_id else None


def _event_rate(policy: AdmissionPolicy, event: str) -> EventRate:
    field_name = _EVENT_RATE_FIELDS.get(event)
    if field_name is None:
        raise ValueError(f"Unknown protected event category: {event!r}")
    return getattr(policy, field_name)


def consume_event_token(
    sid: str,
    event: str,
    *,
    now: float | None = None,
    policy: AdmissionPolicy | None = None,
) -> ThrottleDecision:
    active_policy = _policy(policy)
    rate = _event_rate(active_policy, event)
    current_time = _now(now)
    with state_lock:
        record = limiter_records.get(sid)
        if record is None:
            if len(limiter_records) >= active_policy.max_limiter_sids:
                evicted_sid = min(
                    limiter_records,
                    key=lambda existing_sid: (
                        limiter_records[existing_sid].last_seen_at,
                        existing_sid,
                    ),
                )
                limiter_records.pop(evicted_sid)
            record = SidLimiterRecord(last_seen_at=current_time)
            limiter_records[sid] = record
        record.last_seen_at = current_time

        bucket = record.buckets.get(event)
        if bucket is None:
            bucket = TokenBucket(float(rate.burst), current_time)
            record.buckets[event] = bucket
        else:
            elapsed = max(0.0, current_time - bucket.last_refill_at)
            refill_rate = rate.events / rate.window_seconds
            bucket.tokens = min(
                float(rate.burst),
                bucket.tokens + elapsed * refill_rate,
            )
            bucket.last_refill_at = max(bucket.last_refill_at, current_time)

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return ThrottleDecision(allowed=True)

        warning_at = record.last_warning_at
        emit_warning = warning_at is None or current_time >= (
            warning_at + active_policy.throttle_warning_cooldown_seconds
        )
        if emit_warning:
            record.last_warning_at = current_time
        return ThrottleDecision(allowed=False, emit_warning=emit_warning)
