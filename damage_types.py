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

# Canonical magical-subschool -> resistance-stat mapping. This is the single
# authority the mitigation pipeline consults to add a matching subschool
# resistance on top of general Magic Resistance. Future Fire/Frost/Shadow/
# Holy/Arcane/Nature resistance items need only grant the matching stat key
# below; no gameplay/resolver change is required. Keep the keys aligned with
# the allowed magical subschools and the ``{subschool}_resist`` stat naming
# already used by the champion mouseover payload.
SUBSCHOOL_RESISTANCE_STATS: dict[str, str] = {
    "arcane": "arcane_resist",
    "fire": "fire_resist",
    "frost": "frost_resist",
    "holy": "holy_resist",
    "nature": "nature_resist",
    "shadow": "shadow_resist",
}

_SUBSCHOOL_SET = frozenset(SUBSCHOOL_RESISTANCE_STATS)


def normalize_subschool(value: Any) -> Optional[str]:
    """Return the canonical subschool string for ``value``, else ``None``.

    Tolerates surrounding whitespace and case slop, matching the school
    normalization conventions elsewhere in the engine. ``None``, a missing
    subschool, and any unknown subschool all resolve to ``None`` so callers
    can treat "no supported subschool" uniformly.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _SUBSCHOOL_SET:
            return normalized
    return None


def subschool_resistance_stat(subschool: Any) -> Optional[str]:
    """Return the resistance stat key for a magical subschool, or ``None``.

    Pure lookup: this performs no gameplay/mitigation calculation. Missing and
    unknown subschools return ``None`` so the mitigation pipeline adds no
    specific resistance for them. Adding a new resistance type requires only
    granting the matching stat via item ``mods``; this resolver never changes.
    """
    normalized = normalize_subschool(subschool)
    if normalized is None:
        return None
    return SUBSCHOOL_RESISTANCE_STATS[normalized]


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
