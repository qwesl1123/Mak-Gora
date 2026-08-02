"""Central application-layer availability policy and bounded primitives."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Iterable, Mapping


SNAPSHOT_LOG_ENTRY_LIMIT = 30
MIN_SOCKET_BUFFER_BYTES = 4 * 1024


@dataclass(frozen=True)
class EventThrottlePolicy:
    events: int
    window_seconds: float
    burst: int

    def validate(self, name: str) -> None:
        if self.events <= 0:
            raise ValueError(f"{name} throttle events must be positive")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError(f"{name} throttle window must be positive")
        if self.burst <= 0:
            raise ValueError(f"{name} throttle burst must be positive")


@dataclass(frozen=True)
class AvailabilityPolicy:
    max_queued_sids: int = 100
    max_active_rooms: int = 50
    max_retained_log_entries: int = 500

    queue_ttl_seconds: float = 15 * 60
    prep_idle_ttl_seconds: float = 10 * 60
    prep_max_lifetime_seconds: float = 30 * 60
    combat_idle_ttl_seconds: float = 15 * 60
    combat_max_lifetime_seconds: float = 2 * 60 * 60
    ended_ttl_seconds: float = 2 * 60

    cleanup_interval_seconds: float = 30
    socket_max_buffer_bytes: int = 16 * 1024
    throttle_warning_cooldown_seconds: float = 2
    limiter_stale_ttl_seconds: float = 15 * 60
    max_limiter_sids: int = 1000

    queue_throttle: EventThrottlePolicy = EventThrottlePolicy(3, 10, 3)
    lock_throttle: EventThrottlePolicy = EventThrottlePolicy(4, 10, 4)
    prep_throttle: EventThrottlePolicy = EventThrottlePolicy(12, 10, 12)
    action_throttle: EventThrottlePolicy = EventThrottlePolicy(10, 10, 8)
    chat_throttle: EventThrottlePolicy = EventThrottlePolicy(8, 10, 5)

    def validate(self) -> "AvailabilityPolicy":
        positive_capacities = {
            "max_queued_sids": self.max_queued_sids,
            "max_active_rooms": self.max_active_rooms,
            "max_retained_log_entries": self.max_retained_log_entries,
            "max_limiter_sids": self.max_limiter_sids,
        }
        for name, value in positive_capacities.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        if self.max_retained_log_entries < SNAPSHOT_LOG_ENTRY_LIMIT:
            raise ValueError(
                "max_retained_log_entries must be at least "
                f"{SNAPSHOT_LOG_ENTRY_LIMIT} for snapshot delivery"
            )
        if self.socket_max_buffer_bytes < MIN_SOCKET_BUFFER_BYTES:
            raise ValueError(
                "socket_max_buffer_bytes must be at least "
                f"{MIN_SOCKET_BUFFER_BYTES} bytes for legitimate protocol traffic"
            )

        positive_durations = {
            "queue_ttl_seconds": self.queue_ttl_seconds,
            "prep_idle_ttl_seconds": self.prep_idle_ttl_seconds,
            "prep_max_lifetime_seconds": self.prep_max_lifetime_seconds,
            "combat_idle_ttl_seconds": self.combat_idle_ttl_seconds,
            "combat_max_lifetime_seconds": self.combat_max_lifetime_seconds,
            "ended_ttl_seconds": self.ended_ttl_seconds,
            "cleanup_interval_seconds": self.cleanup_interval_seconds,
            "throttle_warning_cooldown_seconds": self.throttle_warning_cooldown_seconds,
            "limiter_stale_ttl_seconds": self.limiter_stale_ttl_seconds,
        }
        for name, value in positive_durations.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive")

        for name, throttle in self.event_throttles.items():
            throttle.validate(name)
        return self

    @property
    def event_throttles(self) -> Mapping[str, EventThrottlePolicy]:
        return {
            "duel_queue": self.queue_throttle,
            "duel_lock_in": self.lock_throttle,
            "duel_prep_submit": self.prep_throttle,
            "duel_action": self.action_throttle,
            "duel_chat": self.chat_throttle,
        }

    def throttle_for(self, event_name: str) -> EventThrottlePolicy:
        try:
            return self.event_throttles[event_name]
        except KeyError as exc:
            raise ValueError(f"Unknown throttled event: {event_name}") from exc


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _load_throttle(
    environ: Mapping[str, str],
    prefix: str,
    default: EventThrottlePolicy,
) -> EventThrottlePolicy:
    return EventThrottlePolicy(
        events=_env_int(environ, f"{prefix}_EVENTS", default.events),
        window_seconds=_env_float(
            environ,
            f"{prefix}_WINDOW_SECONDS",
            default.window_seconds,
        ),
        burst=_env_int(environ, f"{prefix}_BURST", default.burst),
    )


def load_availability_policy(
    environ: Mapping[str, str] | None = None,
) -> AvailabilityPolicy:
    """Load environment overrides and fail clearly on invalid startup policy."""
    source = os.environ if environ is None else environ
    defaults = AvailabilityPolicy()
    policy = AvailabilityPolicy(
        max_queued_sids=_env_int(
            source, "MAKGORA_MAX_QUEUED_SIDS", defaults.max_queued_sids
        ),
        max_active_rooms=_env_int(
            source, "MAKGORA_MAX_ACTIVE_ROOMS", defaults.max_active_rooms
        ),
        max_retained_log_entries=_env_int(
            source,
            "MAKGORA_MAX_RETAINED_LOG_ENTRIES",
            defaults.max_retained_log_entries,
        ),
        queue_ttl_seconds=_env_float(
            source, "MAKGORA_QUEUE_TTL_SECONDS", defaults.queue_ttl_seconds
        ),
        prep_idle_ttl_seconds=_env_float(
            source, "MAKGORA_PREP_IDLE_TTL_SECONDS", defaults.prep_idle_ttl_seconds
        ),
        prep_max_lifetime_seconds=_env_float(
            source,
            "MAKGORA_PREP_MAX_LIFETIME_SECONDS",
            defaults.prep_max_lifetime_seconds,
        ),
        combat_idle_ttl_seconds=_env_float(
            source,
            "MAKGORA_COMBAT_IDLE_TTL_SECONDS",
            defaults.combat_idle_ttl_seconds,
        ),
        combat_max_lifetime_seconds=_env_float(
            source,
            "MAKGORA_COMBAT_MAX_LIFETIME_SECONDS",
            defaults.combat_max_lifetime_seconds,
        ),
        ended_ttl_seconds=_env_float(
            source, "MAKGORA_ENDED_TTL_SECONDS", defaults.ended_ttl_seconds
        ),
        cleanup_interval_seconds=_env_float(
            source,
            "MAKGORA_CLEANUP_INTERVAL_SECONDS",
            defaults.cleanup_interval_seconds,
        ),
        socket_max_buffer_bytes=_env_int(
            source,
            "MAKGORA_SOCKET_MAX_BUFFER_BYTES",
            defaults.socket_max_buffer_bytes,
        ),
        throttle_warning_cooldown_seconds=_env_float(
            source,
            "MAKGORA_THROTTLE_WARNING_COOLDOWN_SECONDS",
            defaults.throttle_warning_cooldown_seconds,
        ),
        limiter_stale_ttl_seconds=_env_float(
            source,
            "MAKGORA_LIMITER_STALE_TTL_SECONDS",
            defaults.limiter_stale_ttl_seconds,
        ),
        max_limiter_sids=_env_int(
            source, "MAKGORA_MAX_LIMITER_SIDS", defaults.max_limiter_sids
        ),
        queue_throttle=_load_throttle(
            source, "MAKGORA_QUEUE_THROTTLE", defaults.queue_throttle
        ),
        lock_throttle=_load_throttle(
            source, "MAKGORA_LOCK_THROTTLE", defaults.lock_throttle
        ),
        prep_throttle=_load_throttle(
            source, "MAKGORA_PREP_THROTTLE", defaults.prep_throttle
        ),
        action_throttle=_load_throttle(
            source, "MAKGORA_ACTION_THROTTLE", defaults.action_throttle
        ),
        chat_throttle=_load_throttle(
            source, "MAKGORA_CHAT_THROTTLE", defaults.chat_throttle
        ),
    )
    return policy.validate()


DEFAULT_AVAILABILITY_POLICY = load_availability_policy()


class BoundedCombatLog(list[str]):
    """Append-only combat log with bounded retention and a global cursor.

    Read access remains list-compatible because snapshots and gameplay checks
    rely on indexing, slicing, iteration, and equality. Mutations other than
    append/extend are rejected: reordering or replacing retained entries would
    make their monotonic sequence positions ambiguous.
    """

    def __init__(
        self,
        values: Iterable[str] = (),
        *,
        max_entries: int = DEFAULT_AVAILABILITY_POLICY.max_retained_log_entries,
        sequence: int | None = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        initial_values = list(values)
        initial_sequence = len(initial_values) if sequence is None else int(sequence)
        if initial_sequence < len(initial_values):
            raise ValueError("sequence cannot be smaller than the supplied log")
        self.max_entries = int(max_entries)
        self.sequence = initial_sequence
        super().__init__(initial_values[-self.max_entries :])

    def _trim(self) -> None:
        overflow = len(self) - self.max_entries
        if overflow > 0:
            super().__delitem__(slice(0, overflow))

    def append(self, value: str) -> None:
        super().append(value)
        self.sequence += 1
        self._trim()

    def extend(self, values: Iterable[str]) -> None:
        additions = list(values) if values is self else values
        for value in additions:
            self.append(value)

    def __iadd__(self, values: Iterable[str]):
        self.extend(values)
        return self

    @staticmethod
    def _reject_non_append_mutation(*_args, **_kwargs):
        raise TypeError("BoundedCombatLog is append-only; use append() or extend()")

    __setitem__ = _reject_non_append_mutation
    __delitem__ = _reject_non_append_mutation
    __imul__ = _reject_non_append_mutation
    insert = _reject_non_append_mutation
    clear = _reject_non_append_mutation
    pop = _reject_non_append_mutation
    remove = _reject_non_append_mutation
    reverse = _reject_non_append_mutation
    sort = _reject_non_append_mutation

    @property
    def first_retained_sequence(self) -> int:
        if not self:
            return self.sequence + 1
        return self.sequence - len(self) + 1
