# games/duel/engine/effects.py
from __future__ import annotations

from typing import Any, Dict, List

from .models import PlayerState
from ..content.balance import DEFAULTS


def is_permanent(effect: Dict[str, Any]) -> bool:
    """Effects we do not tick down with durations (until you add cleanse/removal)."""
    return effect.get("type") in ("item_passive", "burn")


def tick_durations(effects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Decrement duration for non-permanent effects; drop expired ones."""
    new_list: List[Dict[str, Any]] = []
    for e in effects:
        if is_permanent(e):
            new_list.append(e)
            continue
        d = int(e.get("duration", 0) or 0) - 1
        if d > 0:
            e2 = dict(e)
            e2["duration"] = d
            new_list.append(e2)
    return new_list


def mitigation_multiplier(target: PlayerState) -> float:
    """Sum mitigation effects and cap at 80%. Returns multiplier for damage."""
    total = 0.0
    for effect in target.effects:
        if effect.get("type") == "mitigation":
            total += float(effect.get("value", 0) or 0.0)
    total = max(0.0, min(total, 0.8))
    return 1.0 - total


def apply_burn(target: PlayerState, value: int, source_item: str = "Unknown", duration: int = 999) -> None:
    """Attach a burn DoT to the target (matches your existing burn shape)."""
    target.effects.append(
        {
            "type": "burn",
            "value": int(value),
            "duration": int(duration),
            "source": str(source_item),
        }
    )


def trigger_on_hit_passives(attacker: PlayerState, target: PlayerState, log: List[str]) -> None:
    """Run attacker item passives that trigger on_hit (currently: burn)."""
    for effect in attacker.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "on_hit":
            continue

        if passive.get("type") == "burn":
            burn_value = int(passive.get("value", 0) or 0)
            if burn_value > 0:
                apply_burn(
                    target,
                    value=burn_value,
                    source_item=str(effect.get("source_item", "Unknown")),
                    duration=999,
                )
                # This log is separate from the action's one-line log.
                log.append(f"Burns target for {burn_value} damage/turn!")


def tick_dots(ps: PlayerState, log: List[str], label: str) -> None:
    """Apply DoT damage (currently: burn)."""
    for effect in ps.effects:
        if effect.get("type") == "burn":
            burn_dmg = int(effect.get("value", 0) or 0)
            if burn_dmg > 0:
                ps.res.hp -= burn_dmg
                log.append(f"{label} burns for {burn_dmg} damage.")


def trigger_end_of_turn_passives(ps: PlayerState, log: List[str], label: str) -> None:
    """Run end-of-turn item passives (currently: heal_self)."""
    for effect in ps.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "end_of_turn":
            continue

        if passive.get("type") == "heal_self":
            heal_value = int(passive.get("value", 0) or 0)
            if heal_value > 0:
                ps.res.hp = min(ps.res.hp + heal_value, ps.res.hp_max)
                log.append(
                    f"{label} heals {heal_value} HP from {effect.get('source_item', 'item')}."
                )


def end_of_turn(ps: PlayerState, log: List[str], label: str) -> None:
    """End-of-turn pipeline: DoTs, passives, duration tick, regen."""
    if not ps.res:
        return

    tick_dots(ps, log, label)
    trigger_end_of_turn_passives(ps, log, label)
    ps.effects = tick_durations(ps.effects)

    if ps.res.hp > 0:
        ps.res.mp = min(ps.res.mp + DEFAULTS["mp_regen_per_turn"], ps.res.mp_max)
        ps.res.energy = min(ps.res.energy + DEFAULTS["energy_regen_per_turn"], ps.res.energy_max)
