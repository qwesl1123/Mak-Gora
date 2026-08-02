"""Process-local duel state with bounded availability controls."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from threading import RLock
import time
from typing import Any, Callable, Dict, List, Optional

from .availability import (
    AvailabilityPolicy,
    DEFAULT_AVAILABILITY_POLICY,
    EventThrottlePolicy,
)
from .engine.models import MatchState


logger = logging.getLogger(__name__)

availability_policy = DEFAULT_AVAILABILITY_POLICY
monotonic_clock: Callable[[], float] = time.monotonic
state_lock = RLock()

duel_queue: List[str] = []
queued_at_by_sid: Dict[str, float] = {}
duel_rooms: Dict[str, MatchState] = {}
sid_to_room: Dict[str, str] = {}


@dataclass
class TokenBucket:
    tokens: float
    last_refill_at: float


@dataclass
class SidLimiterRecord:
    buckets: Dict[str, TokenBucket] = field(default_factory=dict)
    last_seen_at: float = 0.0
    last_warning_at: Optional[float] = None


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    emit_warning: bool = False


@dataclass(frozen=True)
class RoomCleanup:
    room_id: str
    reason: str
    previous_phase: str
    players: tuple[str, ...]
    message: Optional[str] = None


@dataclass
class CleanupActions:
    expired_queue_sids: list[str] = field(default_factory=list)
    cleaned_rooms: list[RoomCleanup] = field(default_factory=list)
    expired_limiter_sids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueueResult:
    status: str
    match: Optional[MatchState]
    cleanup_actions: CleanupActions


limiter_records: Dict[str, SidLimiterRecord] = {}
_sweeper_started = False


def current_time() -> float:
    return float(monotonic_clock())


def _remove_queued_sid_locked(sid: str) -> bool:
    removed = False
    while sid in duel_queue:
        duel_queue.remove(sid)
        removed = True
    queued_at_by_sid.pop(sid, None)
    return removed


def _expire_queue_locked(now: float, actions: CleanupActions) -> None:
    for sid in set(queued_at_by_sid) - set(duel_queue):
        queued_at_by_sid.pop(sid, None)
    expired = [
        sid
        for sid in duel_queue
        if sid not in queued_at_by_sid
        or now >= queued_at_by_sid[sid] + availability_policy.queue_ttl_seconds
    ]
    for sid in expired:
        _remove_queued_sid_locked(sid)
    actions.expired_queue_sids.extend(expired)


def _cleanup_room_locked(
    room_id: str,
    *,
    reason: str,
    message: Optional[str] = None,
) -> Optional[RoomCleanup]:
    match = duel_rooms.pop(room_id, None)
    mapped_sids = [sid for sid, mapped_room in sid_to_room.items() if mapped_room == room_id]
    player_sids = list(match.players) if match is not None else []
    associated_sids = tuple(dict.fromkeys([*player_sids, *mapped_sids]))

    if match is None and not associated_sids:
        return None

    previous_phase = match.phase if match is not None else "missing"
    if match is not None:
        match.availability_closed = True
        match.availability_transport_setup_in_progress = False
        match.availability_resolution_in_progress = False
        match.availability_pending_cleanup_reason = None
        match.availability_pending_cleanup_message = None
    for sid in associated_sids:
        sid_to_room.pop(sid, None)
        _remove_queued_sid_locked(sid)

    return RoomCleanup(
        room_id=room_id,
        reason=reason,
        previous_phase=previous_phase,
        players=associated_sids,
        message=message,
    )


def _match_has_active_operation_locked(match: MatchState) -> bool:
    return bool(
        match.availability_transport_setup_in_progress
        or match.availability_resolution_in_progress
    )


def _request_room_cleanup_locked(
    match: MatchState,
    *,
    reason: str,
    message: Optional[str] = None,
) -> Optional[RoomCleanup]:
    if duel_rooms.get(match.room_id) is not match or match.availability_closed:
        return None

    if match.availability_pending_cleanup_reason is None:
        match.availability_pending_cleanup_reason = reason
        match.availability_pending_cleanup_message = message

    if _match_has_active_operation_locked(match):
        return None

    pending_reason = match.availability_pending_cleanup_reason or reason
    pending_message = match.availability_pending_cleanup_message
    return _cleanup_room_locked(
        match.room_id,
        reason=pending_reason,
        message=pending_message,
    )


def _room_expiration(
    match: MatchState,
    now: float,
) -> tuple[str, str] | None:
    phase_started_at = match.phase_started_at
    if phase_started_at is None:
        phase_started_at = match.created_at
    if phase_started_at is None:
        phase_started_at = now
    last_activity_at = match.last_gameplay_activity_at
    if last_activity_at is None:
        last_activity_at = phase_started_at
    phase_started_at = float(phase_started_at)
    last_activity_at = float(last_activity_at)
    policy = availability_policy

    if match.phase == "prep":
        if now >= phase_started_at + policy.prep_max_lifetime_seconds:
            return "prep_max_lifetime", "Duel setup reached the server time limit."
        if now >= last_activity_at + policy.prep_idle_ttl_seconds:
            return "prep_idle", "Duel setup closed due to inactivity."
        return None

    if match.phase == "combat":
        if now >= phase_started_at + policy.combat_max_lifetime_seconds:
            return "combat_max_lifetime", "Duel closed after reaching the server time limit."
        if now >= last_activity_at + policy.combat_idle_ttl_seconds:
            return "combat_idle", "Duel closed due to inactivity."
        return None

    if match.phase == "ended":
        if match.ended_at is None:
            # Defensive fallback for old/directly-built matches. The first sweep
            # starts the grace period instead of deleting before final delivery.
            match.ended_at = now
            match.phase_started_at = now
            return None
        if now >= float(match.ended_at) + policy.ended_ttl_seconds:
            return "ended_ttl", None
    return None


def collect_expired_resources(now: Optional[float] = None) -> CleanupActions:
    """Atomically reclaim expired process-local state without emitting."""
    timestamp = current_time() if now is None else float(now)
    actions = CleanupActions()
    with state_lock:
        _expire_queue_locked(timestamp, actions)
        room_candidates = list(duel_rooms.items())
        stale_deadline = availability_policy.limiter_stale_ttl_seconds
        stale_sids = [
            sid
            for sid, record in limiter_records.items()
            if timestamp >= record.last_seen_at + stale_deadline
        ]
        for sid in stale_sids:
            limiter_records.pop(sid, None)
        actions.expired_limiter_sids.extend(stale_sids)

    # Match mutation and turn resolution use turn_lock. Acquire it before the
    # global availability lock so cleanup never holds global state while it
    # waits for a resolving turn, and a late handler cannot mutate a detached
    # MatchState between validation and commit.
    for room_id, match in room_candidates:
        with match.turn_lock:
            with state_lock:
                if duel_rooms.get(room_id) is not match:
                    continue
                expiration = _room_expiration(match, timestamp)
                if expiration is None:
                    continue
                reason, message = expiration
                cleanup = _request_room_cleanup_locked(
                    match,
                    reason=reason,
                    message=message,
                )
            if cleanup is not None:
                actions.cleaned_rooms.append(cleanup)
    return actions


def apply_cleanup_actions(socketio: Any, actions: CleanupActions) -> None:
    """Emit cleanup notices after the availability lock has been released."""
    if actions.expired_queue_sids:
        logger.info("Expired %d matchmaking request(s)", len(actions.expired_queue_sids))
        for sid in actions.expired_queue_sids:
            try:
                socketio.emit(
                    "duel_system",
                    "Matchmaking request expired. Queue again to continue.",
                    to=sid,
                )
            except Exception:
                logger.exception(
                    "Failed to deliver queue-expiration notice sid_prefix=%s",
                    sid[:12],
                )

    close_room = getattr(socketio, "close_room", None)
    for cleanup in actions.cleaned_rooms:
        logger.info(
            "Cleaned duel room reason=%s previous_phase=%s",
            cleanup.reason,
            cleanup.previous_phase,
        )
        if cleanup.message:
            for sid in cleanup.players:
                try:
                    socketio.emit("duel_system", cleanup.message, to=sid)
                except Exception:
                    logger.exception(
                        "Failed to deliver room-cleanup notice reason=%s sid_prefix=%s",
                        cleanup.reason,
                        sid[:12],
                    )
        if callable(close_room):
            try:
                close_room(cleanup.room_id)
            except Exception:
                logger.exception(
                    "Failed to close cleaned duel room reason=%s room_id=%s",
                    cleanup.reason,
                    cleanup.room_id,
                )


def sweep_expired_resources(socketio: Any, now: Optional[float] = None) -> CleanupActions:
    actions = collect_expired_resources(now)
    apply_cleanup_actions(socketio, actions)
    return actions


def _lifecycle_sweeper(socketio: Any) -> None:
    while True:
        try:
            socketio.sleep(availability_policy.cleanup_interval_seconds)
            sweep_expired_resources(socketio)
        except Exception:
            logger.exception("Availability lifecycle sweeper failed")


def start_lifecycle_sweeper(socketio: Any) -> bool:
    """Start exactly one Flask-SocketIO background sweeper per process."""
    global _sweeper_started
    with state_lock:
        if _sweeper_started:
            return False
        _sweeper_started = True
    try:
        socketio.start_background_task(_lifecycle_sweeper, socketio)
    except Exception:
        with state_lock:
            _sweeper_started = False
        raise
    return True


def enqueue(sid: str, now: Optional[float] = None) -> bool:
    """Legacy single-SID enqueue surface with the canonical cap and timestamp."""
    timestamp = current_time() if now is None else float(now)
    with state_lock:
        actions = CleanupActions()
        _expire_queue_locked(timestamp, actions)
        if sid in duel_queue:
            return True
        if len(duel_queue) >= availability_policy.max_queued_sids:
            return False
        duel_queue.append(sid)
        queued_at_by_sid[sid] = timestamp
        return True


def dequeue(sid: str) -> bool:
    with state_lock:
        return _remove_queued_sid_locked(sid)


def _create_room_locked(
    p1: str,
    p2: str,
    seed: int,
    now: float,
    *,
    transport_setup_in_progress: bool = False,
) -> MatchState:
    room_id = f"duel-{p1[:5]}-{p2[:5]}"
    if room_id in duel_rooms:
        suffix = 2
        while f"{room_id}-{suffix}" in duel_rooms:
            suffix += 1
        room_id = f"{room_id}-{suffix}"
    match = MatchState(
        room_id=room_id,
        players=[p1, p2],
        seed=seed,
        max_retained_log_entries=availability_policy.max_retained_log_entries,
        created_at=now,
        phase_started_at=now,
        last_gameplay_activity_at=now,
        monotonic_clock=monotonic_clock,
        availability_transport_setup_in_progress=transport_setup_in_progress,
    )
    duel_rooms[room_id] = match
    sid_to_room[p1] = room_id
    sid_to_room[p2] = room_id
    return match


def create_room(
    p1: str,
    p2: str,
    seed: int,
    now: Optional[float] = None,
) -> MatchState:
    timestamp = current_time() if now is None else float(now)
    with state_lock:
        if len(duel_rooms) >= availability_policy.max_active_rooms:
            raise RuntimeError("duel room capacity reached")
        if p1 in sid_to_room or p2 in sid_to_room:
            raise RuntimeError("a player is already mapped to a duel room")
        _remove_queued_sid_locked(p1)
        _remove_queued_sid_locked(p2)
        return _create_room_locked(p1, p2, seed, timestamp)


def queue_sid_for_match(
    sid: str,
    seed: int,
    now: Optional[float] = None,
    *,
    start_transport_setup: bool = False,
) -> QueueResult:
    """Atomically enqueue and, when possible, create one retained duel room."""
    timestamp = current_time() if now is None else float(now)
    actions = CleanupActions()
    with state_lock:
        _expire_queue_locked(timestamp, actions)

        if sid in sid_to_room:
            return QueueResult("already_in_duel", None, actions)

        already_queued = sid in duel_queue
        if not already_queued:
            if len(duel_queue) >= availability_policy.max_queued_sids:
                return QueueResult("queue_full", None, actions)
            duel_queue.append(sid)
            queued_at_by_sid[sid] = timestamp

        if len(duel_queue) < 2:
            status = "already_queued" if already_queued else "queued"
            return QueueResult(status, None, actions)

        if len(duel_rooms) >= availability_policy.max_active_rooms:
            return QueueResult("room_full", None, actions)

        p1, p2 = duel_queue[0], duel_queue[1]
        del duel_queue[:2]
        queued_at_by_sid.pop(p1, None)
        queued_at_by_sid.pop(p2, None)
        match = _create_room_locked(
            p1,
            p2,
            seed,
            timestamp,
            transport_setup_in_progress=start_transport_setup,
        )
        return QueueResult("matched", match, actions)


def get_match_by_sid(sid: str) -> Optional[MatchState]:
    with state_lock:
        room_id = sid_to_room.get(sid)
        if not room_id:
            return None
        match = duel_rooms.get(room_id)
        if match is None or match.availability_closed:
            sid_to_room.pop(sid, None)
            return None
        return match


def match_is_retained(match: MatchState, sid: Optional[str] = None) -> bool:
    with state_lock:
        if (
            duel_rooms.get(match.room_id) is not match
            or match.availability_closed
            or match.availability_pending_cleanup_reason is not None
        ):
            return False
        return sid is None or sid_to_room.get(sid) == match.room_id


def match_accepts_delivery(match: MatchState, sid: Optional[str] = None) -> bool:
    with match.turn_lock:
        return match_is_retained(match, sid)


def begin_match_resolution(match: MatchState, sid: Optional[str] = None) -> bool:
    with match.turn_lock:
        with state_lock:
            if (
                duel_rooms.get(match.room_id) is not match
                or match.availability_closed
                or match.availability_pending_cleanup_reason is not None
                or match.availability_transport_setup_in_progress
                or match.availability_resolution_in_progress
            ):
                return False
            if sid is not None and sid_to_room.get(sid) != match.room_id:
                return False
            match.availability_resolution_in_progress = True
            return True


def request_match_cleanup(
    match: MatchState,
    *,
    reason: str,
    message: Optional[str] = None,
) -> CleanupActions:
    actions = CleanupActions()
    with match.turn_lock:
        with state_lock:
            cleanup = _request_room_cleanup_locked(
                match,
                reason=reason,
                message=message,
            )
    if cleanup is not None:
        actions.cleaned_rooms.append(cleanup)
    return actions


def finalize_match_operation(match: MatchState, operation: str) -> CleanupActions:
    actions = CleanupActions()
    with match.turn_lock:
        with state_lock:
            if operation == "transport_setup":
                match.availability_transport_setup_in_progress = False
            elif operation == "resolution":
                match.availability_resolution_in_progress = False
            else:
                raise ValueError(f"Unknown match operation: {operation}")

            if (
                duel_rooms.get(match.room_id) is match
                and not match.availability_closed
                and match.availability_pending_cleanup_reason is not None
                and not _match_has_active_operation_locked(match)
            ):
                cleanup = _request_room_cleanup_locked(
                    match,
                    reason=match.availability_pending_cleanup_reason,
                    message=match.availability_pending_cleanup_message,
                )
                if cleanup is not None:
                    actions.cleaned_rooms.append(cleanup)
    return actions


def mark_match_ended(match: MatchState, now: Optional[float] = None) -> bool:
    timestamp = current_time() if now is None else float(now)
    with match.turn_lock:
        with state_lock:
            if (
                duel_rooms.get(match.room_id) is not match
                or match.availability_closed
                or match.availability_pending_cleanup_reason is not None
            ):
                return False
            if match.ended_at is None:
                match.ended_at = timestamp
                match.phase_started_at = timestamp
            return True


def cleanup_room(room_id: str, reason: str = "explicit_cleanup") -> Optional[MatchState]:
    cleanup = None
    with state_lock:
        match = duel_rooms.get(room_id)
        if match is None:
            cleanup = _cleanup_room_locked(room_id, reason=reason)
    if match is not None:
        with match.turn_lock:
            with state_lock:
                if duel_rooms.get(room_id) is match:
                    cleanup = _request_room_cleanup_locked(match, reason=reason)
                else:
                    cleanup = None
    if cleanup is not None:
        logger.info(
            "Cleaned duel room reason=%s previous_phase=%s",
            cleanup.reason,
            cleanup.previous_phase,
        )
    return match


def disconnect_sid(sid: str) -> Optional[MatchState]:
    with state_lock:
        _remove_queued_sid_locked(sid)
        limiter_records.pop(sid, None)
        room_id = sid_to_room.get(sid)
        match = duel_rooms.get(room_id) if room_id else None
        if room_id and match is None:
            sid_to_room.pop(sid, None)
            _cleanup_room_locked(room_id, reason="disconnect")
    cleanup = None
    if match is not None:
        with match.turn_lock:
            with state_lock:
                if duel_rooms.get(match.room_id) is match:
                    cleanup = _request_room_cleanup_locked(
                        match,
                        reason="disconnect",
                    )
    if cleanup is not None:
        logger.info("Cleaned duel room reason=disconnect previous_phase=%s", match.phase)
    return match


def _new_limiter_record(now: float) -> SidLimiterRecord:
    if len(limiter_records) >= availability_policy.max_limiter_sids:
        oldest_sid = min(
            limiter_records,
            key=lambda existing_sid: (
                limiter_records[existing_sid].last_seen_at,
                existing_sid,
            ),
        )
        limiter_records.pop(oldest_sid, None)
    return SidLimiterRecord(last_seen_at=now)


def _refill_bucket(
    bucket: TokenBucket,
    throttle: EventThrottlePolicy,
    now: float,
) -> None:
    elapsed = max(0.0, now - bucket.last_refill_at)
    refill_rate = throttle.events / throttle.window_seconds
    bucket.tokens = min(float(throttle.burst), bucket.tokens + elapsed * refill_rate)
    bucket.last_refill_at = max(bucket.last_refill_at, now)


def consume_event_token(
    sid: str,
    event_name: str,
    now: Optional[float] = None,
) -> ThrottleDecision:
    timestamp = current_time() if now is None else float(now)
    throttle = availability_policy.throttle_for(event_name)
    with state_lock:
        record = limiter_records.get(sid)
        if record is None:
            record = _new_limiter_record(timestamp)
            limiter_records[sid] = record
        record.last_seen_at = timestamp

        bucket = record.buckets.get(event_name)
        if bucket is None:
            bucket = TokenBucket(float(throttle.burst), timestamp)
            record.buckets[event_name] = bucket
        else:
            _refill_bucket(bucket, throttle, timestamp)

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return ThrottleDecision(True)

        emit_warning = (
            record.last_warning_at is None
            or timestamp
            >= record.last_warning_at
            + availability_policy.throttle_warning_cooldown_seconds
        )
        if emit_warning:
            record.last_warning_at = timestamp

    if emit_warning:
        logger.warning(
            "Throttled Socket.IO event category=%s sid_prefix=%s",
            event_name,
            sid[:12],
        )
    return ThrottleDecision(False, emit_warning)


def remove_limiter(sid: str) -> None:
    with state_lock:
        limiter_records.pop(sid, None)
