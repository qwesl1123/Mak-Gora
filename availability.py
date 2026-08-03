"""Validated resource, matchmaking-admission, and Socket.IO rate limits."""
from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from typing import Mapping


SNAPSHOT_LOG_ENTRY_LIMIT = 30
MIN_SOCKET_MAX_BUFFER_BYTES = 4 * 1024
MAX_RETAINED_LOG_ENTRIES_ENV = "MAKGORA_MAX_RETAINED_LOG_ENTRIES"
SOCKET_MAX_BUFFER_BYTES_ENV = "MAKGORA_SOCKET_MAX_BUFFER_BYTES"


def _require_positive_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_positive_finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


@dataclass(frozen=True)
class EventRate:
    events: int
    window_seconds: float
    burst: int

    def __post_init__(self) -> None:
        _require_positive_integer("events", self.events)
        _require_positive_finite("window_seconds", self.window_seconds)
        _require_positive_integer("burst", self.burst)


@dataclass(frozen=True)
class AdmissionPolicy:
    max_queued_sids: int = 100
    max_active_rooms: int = 50
    queue_ttl_seconds: float = 15 * 60
    max_limiter_sids: int = 1000
    throttle_warning_cooldown_seconds: float = 2
    queue_rate: EventRate = EventRate(3, 10, 3)
    prep_rate: EventRate = EventRate(12, 10, 12)
    lock_rate: EventRate = EventRate(4, 10, 4)
    action_rate: EventRate = EventRate(10, 10, 8)
    chat_rate: EventRate = EventRate(8, 10, 5)

    def __post_init__(self) -> None:
        _require_positive_integer("max_queued_sids", self.max_queued_sids)
        _require_positive_integer("max_active_rooms", self.max_active_rooms)
        _require_positive_finite("queue_ttl_seconds", self.queue_ttl_seconds)
        _require_positive_integer("max_limiter_sids", self.max_limiter_sids)
        _require_positive_finite(
            "throttle_warning_cooldown_seconds",
            self.throttle_warning_cooldown_seconds,
        )


@dataclass(frozen=True)
class ResourceLimits:
    max_retained_log_entries: int = 500
    socket_max_buffer_bytes: int = 16 * 1024

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_retained_log_entries, bool)
            or not isinstance(self.max_retained_log_entries, int)
            or self.max_retained_log_entries < SNAPSHOT_LOG_ENTRY_LIMIT
        ):
            raise ValueError(
                "max_retained_log_entries must be an integer greater than or equal "
                f"to the {SNAPSHOT_LOG_ENTRY_LIMIT}-entry snapshot window"
            )
        if (
            isinstance(self.socket_max_buffer_bytes, bool)
            or not isinstance(self.socket_max_buffer_bytes, int)
            or self.socket_max_buffer_bytes < MIN_SOCKET_MAX_BUFFER_BYTES
        ):
            raise ValueError(
                "socket_max_buffer_bytes must be an integer greater than or equal "
                f"to {MIN_SOCKET_MAX_BUFFER_BYTES} bytes"
            )


DEFAULT_RESOURCE_LIMITS = ResourceLimits()
DEFAULT_ADMISSION_POLICY = AdmissionPolicy()
_EVENT_RATE_PREFIXES = {
    "queue_rate": "QUEUE",
    "prep_rate": "PREP",
    "lock_rate": "LOCK",
    "action_rate": "ACTION",
    "chat_rate": "CHAT",
}


def _environment_integer(
    environment: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw_value = environment.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer; received {raw_value!r}") from exc


def _environment_float(
    environment: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw_value = environment.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number; received {raw_value!r}") from exc


def load_admission_policy(
    environment: Mapping[str, str] | None = None,
) -> AdmissionPolicy:
    source = os.environ if environment is None else environment
    defaults = DEFAULT_ADMISSION_POLICY
    try:
        rates = {}
        for field_name, prefix in _EVENT_RATE_PREFIXES.items():
            default = getattr(defaults, field_name)
            rates[field_name] = EventRate(
                _environment_integer(
                    source, f"MAKGORA_{prefix}_RATE_EVENTS", default.events
                ),
                _environment_float(
                    source,
                    f"MAKGORA_{prefix}_RATE_WINDOW_SECONDS",
                    default.window_seconds,
                ),
                _environment_integer(
                    source, f"MAKGORA_{prefix}_RATE_BURST", default.burst
                ),
            )
        return AdmissionPolicy(
            max_queued_sids=_environment_integer(
                source, "MAKGORA_MAX_QUEUED_SIDS", defaults.max_queued_sids
            ),
            max_active_rooms=_environment_integer(
                source, "MAKGORA_MAX_ACTIVE_ROOMS", defaults.max_active_rooms
            ),
            queue_ttl_seconds=_environment_float(
                source, "MAKGORA_QUEUE_TTL_SECONDS", defaults.queue_ttl_seconds
            ),
            max_limiter_sids=_environment_integer(
                source, "MAKGORA_MAX_LIMITER_SIDS", defaults.max_limiter_sids
            ),
            throttle_warning_cooldown_seconds=_environment_float(
                source,
                "MAKGORA_THROTTLE_WARNING_COOLDOWN_SECONDS",
                defaults.throttle_warning_cooldown_seconds,
            ),
            **rates,
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid Mak'Gora admission configuration: {exc}"
        ) from exc


def load_resource_limits(
    environment: Mapping[str, str] | None = None,
) -> ResourceLimits:
    source = os.environ if environment is None else environment
    retained_entries = _environment_integer(
        source,
        MAX_RETAINED_LOG_ENTRIES_ENV,
        DEFAULT_RESOURCE_LIMITS.max_retained_log_entries,
    )
    socket_buffer_bytes = _environment_integer(
        source,
        SOCKET_MAX_BUFFER_BYTES_ENV,
        DEFAULT_RESOURCE_LIMITS.socket_max_buffer_bytes,
    )
    try:
        return ResourceLimits(
            max_retained_log_entries=retained_entries,
            socket_max_buffer_bytes=socket_buffer_bytes,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid Mak'Gora resource limit configuration: {exc}") from exc


# Environment overrides are read and validated while the application imports.
# Invalid explicit values therefore stop startup instead of silently falling back.
RESOURCE_LIMITS = load_resource_limits()
ADMISSION_POLICY = load_admission_policy()
