# games/duel/engine/damage_types.py
"""Canonical damage source-kind taxonomy for Mak'Gora.

AGENTS.md requires damage behavior to be explicit about its source kind so
items/passives can declare which kinds they affect (e.g. fixed reflected or
absorb-explosion damage must not accidentally inherit generic outgoing-damage
modifiers). This module is the single authority for those identifiers:
gameplay code must reference the constants below instead of retyping the raw
strings.

``source_kind`` is metadata only. Carrying it on a damage packet, queued
damage event, or DoT tick source never changes damage math, mitigation,
redirects, absorbs, immunity, or resource gains by itself — consumers opt in
explicitly when a rule needs to filter by kind.

Some kinds are declared ahead of wiring: ``direct_dot_application`` (DoT
application currently deals no damage packet of its own), ``reflect_damage``
and ``environmental_damage`` have no live producers yet. They are part of the
canonical taxonomy so future mechanics use consistent identifiers.
"""

from __future__ import annotations

from typing import Any, Optional

DAMAGE_SOURCE_DIRECT_ABILITY = "direct_ability_damage"
DAMAGE_SOURCE_DIRECT_DOT_APPLICATION = "direct_dot_application"
DAMAGE_SOURCE_DOT_TICK = "dot_tick"
DAMAGE_SOURCE_ON_HIT_PROC = "on_hit_proc_damage"
DAMAGE_SOURCE_STRIKE_AGAIN = "strike_again_damage"
DAMAGE_SOURCE_PET = "pet_damage"
DAMAGE_SOURCE_REFLECT = "reflect_damage"
DAMAGE_SOURCE_ABSORB_EXPLOSION = "absorb_explosion_damage"
DAMAGE_SOURCE_SELF = "self_damage"
DAMAGE_SOURCE_ENVIRONMENTAL = "environmental_damage"

ALL_DAMAGE_SOURCE_KINDS: tuple[str, ...] = (
    DAMAGE_SOURCE_DIRECT_ABILITY,
    DAMAGE_SOURCE_DIRECT_DOT_APPLICATION,
    DAMAGE_SOURCE_DOT_TICK,
    DAMAGE_SOURCE_ON_HIT_PROC,
    DAMAGE_SOURCE_STRIKE_AGAIN,
    DAMAGE_SOURCE_PET,
    DAMAGE_SOURCE_REFLECT,
    DAMAGE_SOURCE_ABSORB_EXPLOSION,
    DAMAGE_SOURCE_SELF,
    DAMAGE_SOURCE_ENVIRONMENTAL,
)

_DAMAGE_SOURCE_KIND_SET = frozenset(ALL_DAMAGE_SOURCE_KINDS)

SUBSCHOOL_RESISTANCE_STAT_BY_SUBSCHOOL: dict[str, str] = {
    "arcane": "arcane_resist",
    "fire": "fire_resist",
    "frost": "frost_resist",
    "holy": "holy_resist",
    "nature": "nature_resist",
    "shadow": "shadow_resist",
}


def is_damage_source_kind(value: Any) -> bool:
    """Return True when ``value`` is exactly one of the canonical kind strings."""
    return isinstance(value, str) and value in _DAMAGE_SOURCE_KIND_SET


def normalize_damage_source_kind(value: Any, default: Optional[str] = None) -> Optional[str]:
    """Return the canonical source kind for ``value``, or ``default`` when unknown.

    Tolerates surrounding whitespace and case slop on otherwise-canonical
    strings. Any other value — including ``None`` and legacy packets that
    predate the taxonomy — falls back to ``default`` so metadata consumers
    never crash on untagged damage data.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _DAMAGE_SOURCE_KIND_SET:
            return normalized
    return default


def subschool_resistance_stat(subschool: Any) -> Optional[str]:
    """Return the matching resistance stat for a normalized magical subschool."""
    if not isinstance(subschool, str):
        return None
    normalized = subschool.strip().lower()
    return SUBSCHOOL_RESISTANCE_STAT_BY_SUBSCHOOL.get(normalized)
