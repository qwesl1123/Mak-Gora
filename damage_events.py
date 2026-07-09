# games/duel/engine/damage_events.py
"""Factories for the shared damage-event dictionary shapes.

Two dict schemas flow through the passive-proc damage pipeline:

* Producer events — built by ``effects.trigger_on_hit_passives()`` branches
  (strike_again, void_blade, lightning_blast, duplicate_offensive_spell) and
  returned to the resolver. Shape: ``incoming``, optional ``raw_incoming``,
  ``source_kind``, ``school``, ``subschool``, ``log_template``, optional
  ``damage_instances`` / ``raw_damage_instances``.
* Queued events — built by ``resolver.queue_passive_damage_events()`` and
  appended to ``extra_logs`` for deferred application. Shape adds
  ``type: "damage_event"``, ``source_name`` and
  ``requires_player_mitigation``.

Historically both shapes were ad-hoc dict literals; these factories are the
single place that builds them so the key schema cannot drift per callsite.
They intentionally return plain dicts — every existing consumer treats these
events as dicts (``event.get(...)``), and this module must not change any
gameplay behavior, damage math, or event ordering.

Normalization contract (mirrors the pre-factory inline code exactly):

* ``incoming`` / ``raw_incoming`` are coerced to non-negative ints.
* ``source_kind`` goes through ``normalize_damage_source_kind()``; untagged
  legacy events default to ``DAMAGE_SOURCE_ON_HIT_PROC``.
* ``school`` / ``subschool`` are stored as given — callers keep applying
  ``effects.normalize_school`` where they always did (this module cannot
  import effects.py without creating an import cycle).
* Optional keys are OMITTED when absent, never stored as ``None``:
  ``queue_passive_damage_events()`` distinguishes raw events by
  ``event.get("raw_incoming") is not None``, so a literal ``None`` entry
  would change behavior.

Zero/invalid filtering of whole events stays at the callsites: producers only
append events with positive damage, and the queue loop skips events without
positive incoming or a log template. The factories do not silently drop
events themselves.
"""

from __future__ import annotations

from typing import Any, List, Optional, TypedDict

from .damage_types import DAMAGE_SOURCE_ON_HIT_PROC, normalize_damage_source_kind


class PassiveDamageEvent(TypedDict, total=False):
    """Producer-side on-hit passive damage event (plain dict at runtime).

    Built by :func:`make_passive_damage_event` and returned from
    ``effects.trigger_on_hit_passives()`` to the resolver. This TypedDict is
    documentation only — construction stays a plain dict and every consumer
    reads keys with ``event.get(...)``.

    ``total=False`` because ``raw_incoming`` and the per-hit lists are
    conditional: they must be OMITTED when absent, never stored as ``None``
    (``queue_passive_damage_events()`` distinguishes raw events by
    ``event.get("raw_incoming") is not None``).
    """

    incoming: int
    raw_incoming: int  # pre-mitigation damage; presence => re-mitigate at apply time
    source_kind: str
    school: Any
    subschool: Any
    log_template: str
    damage_instances: List[int]
    raw_damage_instances: List[int]


class QueuedDamageEvent(TypedDict, total=False):
    """Resolver-side queued ``"damage_event"`` extra-log entry (plain dict).

    Built by :func:`make_queued_damage_event`, appended to an action result's
    ``extra_logs`` and applied later by ``resolver.append_extra_logs()``.
    Documentation only — runtime construction and consumption stay plain
    dicts. ``total=False`` because ``damage_instances`` is only attached when
    there is real per-hit data; consumers must keep using ``.get(...)``.
    """

    type: str  # always "damage_event"
    source_name: str
    incoming: int
    requires_player_mitigation: bool
    source_kind: str
    school: Any
    subschool: Any
    log_template: str
    damage_instances: List[int]


def _coerce_non_negative_int(value: Any) -> int:
    """Coerce ``value`` to a non-negative int, treating junk/None as 0."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_damage_instances(values: Any) -> Optional[List[int]]:
    """Normalize a per-hit damage list to positive ints, or ``None``.

    Mirrors the inline normalization from ``queue_passive_damage_events()``:
    non-list inputs yield ``None``; non-coercible entries are skipped; zero
    and negative entries are dropped; an empty result yields ``None`` so
    callers only attach the ``damage_instances`` key when there is real
    per-hit data to format.
    """
    if not isinstance(values, list):
        return None
    normalized: List[int] = []
    for value in values:
        try:
            normalized_value = max(0, int(value or 0))
        except (TypeError, ValueError):
            continue
        if normalized_value > 0:
            normalized.append(normalized_value)
    return normalized or None


def make_passive_damage_event(
    *,
    incoming: Any,
    source_kind: Any,
    school: Any,
    subschool: Any,
    log_template: str,
    raw_incoming: Any = None,
    damage_instances: Optional[List[int]] = None,
    raw_damage_instances: Optional[List[int]] = None,
) -> PassiveDamageEvent:
    """Build a producer-side damage event for ``trigger_on_hit_passives()``.

    ``raw_incoming`` marks the event as pre-mitigation: the resolver re-runs
    target mitigation when the queued event lands. Pass it ONLY for raw proc
    events (void_blade, lightning_blast, duplicate_offensive_spell); fully
    resolved events like strike-again must leave it unset so the key is
    absent from the dict.
    """
    event: PassiveDamageEvent = {
        "incoming": _coerce_non_negative_int(incoming),
        "source_kind": normalize_damage_source_kind(
            source_kind, default=DAMAGE_SOURCE_ON_HIT_PROC
        ),
        "school": school,
        "subschool": subschool,
        "log_template": str(log_template),
    }
    if raw_incoming is not None:
        event["raw_incoming"] = _coerce_non_negative_int(raw_incoming)
    # Per-hit lists are stored as given (copied); they are normalized once at
    # queue time so producer output stays byte-for-byte with the old literals.
    if damage_instances is not None:
        event["damage_instances"] = list(damage_instances)
    if raw_damage_instances is not None:
        event["raw_damage_instances"] = list(raw_damage_instances)
    return event


def make_queued_damage_event(
    *,
    source_name: str,
    incoming: Any,
    requires_player_mitigation: bool,
    log_template: str,
    source_kind: Any = None,
    school: Any = "physical",
    subschool: Any = None,
    damage_instances: Any = None,
) -> QueuedDamageEvent:
    """Build a resolver-side queued ``"damage_event"`` extra-log entry.

    Producers tag their own ``source_kind`` (strike-again vs raw proc);
    untagged legacy events default to ``DAMAGE_SOURCE_ON_HIT_PROC``.
    ``damage_instances`` accepts the raw producer list and is normalized via
    :func:`normalize_damage_instances`; the key is only attached when the
    normalized list is non-empty.
    """
    event: QueuedDamageEvent = {
        "type": "damage_event",
        "source_name": source_name,
        "incoming": _coerce_non_negative_int(incoming),
        "requires_player_mitigation": bool(requires_player_mitigation),
        "source_kind": normalize_damage_source_kind(
            source_kind, default=DAMAGE_SOURCE_ON_HIT_PROC
        ),
        "school": school,
        "subschool": subschool,
        "log_template": str(log_template),
    }
    normalized_instances = normalize_damage_instances(damage_instances)
    if normalized_instances:
        event["damage_instances"] = normalized_instances
    return event
