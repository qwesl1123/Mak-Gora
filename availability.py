"""Validated resource limits for retained duel logs and inbound socket packets."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


SNAPSHOT_LOG_ENTRY_LIMIT = 30
MIN_SOCKET_MAX_BUFFER_BYTES = 4 * 1024
MAX_RETAINED_LOG_ENTRIES_ENV = "MAKGORA_MAX_RETAINED_LOG_ENTRIES"
SOCKET_MAX_BUFFER_BYTES_ENV = "MAKGORA_SOCKET_MAX_BUFFER_BYTES"


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
