# games/duel/engine/effects.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import PlayerState
from ..content.balance import DEFAULTS
from .dice import roll
from .rules import base_damage as calc_base_damage, mitigate

EFFECT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "hot_streak": {
        "type": "status",
        "name": "Hot Streak",
        "duration": 999,
        "flags": {"hot_streak": True},
    },
    "die_by_sword": {
        "type": "status",
        "name": "Die by the Sword",
        "duration": 2,
        "flags": {"immune_physical": True},
    },
    "die_by_sword_mitigation": {
        "type": "mitigation",
        "name": "Die by the Sword",
        "duration": 2,
        "value": 0.3,
    },
    "iceblock": {
        "type": "status",
        "name": "Ice Block",
        "duration": 3,
        "flags": {"immune_all": True, "stunned": True},
        "regen": {"hp": 10, "mp": 25},
    },
    "stunned": {
        "type": "status",
        "name": "Stunned",
        "duration": 1,
        "flags": {"stunned": True},
    },
    "stealth": {
        "type": "stealth",
        "name": "Stealth",
        "duration": 3,
        "flags": {"stealthed": True},
        "break_on_damage_over": 5,
    },
    "blink": {
        "type": "status",
        "name": "Blink",
        "duration": 2,
        "flags": {"blinked": True, "untargetable": True},
    },
    "evasion": {
        "type": "status",
        "name": "Evasion",
        "duration": 2,
        "flags": {"evade_all": True},
    },
    "ambush": {
        "type": "status",
        "name": "Ambush",
        "duration": 999,
        "flags": {"ambush_ready": True},
    },
    "thistle_tea": {
        "type": "status",
        "name": "Thistle Tea",
        "duration": 3,
        "regen": {"energy": 30},
    },
    "crusader_empower": {
        "type": "status",
        "name": "Crusader's Might",
        "duration": 999,
        "damage_mult": 1.2,
        "flags": {"empower_next_offense": True},
    },
    "bear_form": {
        "type": "form",
        "name": "Bear Form",
        "duration": 999,
        "flags": {"form": "bear", "bear_form": True},
    },
    "bear_form_stats": {
        "type": "stat_mods",
        "name": "Bear Form",
        "duration": 999,
        "mods": {"atk": 1, "def": 15, "eva": -5, "physical_reduction": 3},
    },
    "cat_form": {
        "type": "form",
        "name": "Cat Form",
        "duration": 999,
        "flags": {"form": "cat", "cat_form": True},
    },
    "cat_form_stats": {
        "type": "stat_mods",
        "name": "Cat Form",
        "duration": 999,
        "mods": {"atk": 3, "def": 1, "crit": 2, "acc": 2, "eva": 2},
    },
    "moonkin_form": {
        "type": "form",
        "name": "Moonkin Form",
        "duration": 999,
        "flags": {"form": "moonkin", "moonkin_form": True},
    },
    "moonkin_form_stats": {
        "type": "stat_mods",
        "name": "Moonkin Form",
        "duration": 999,
        "mods": {"int": 2, "crit": 1, "acc": 5},
    },
    "tree_form": {
        "type": "form",
        "name": "Tree Form",
        "duration": 999,
        "flags": {"form": "tree", "tree_form": True},
    },
    "tree_form_stats": {
        "type": "stat_mods",
        "name": "Tree Form",
        "duration": 999,
        "mods": {"int": 2, "def": 3, "magic_resist": 2},
    },
    "rip_ready": {
        "type": "status",
        "name": "Rip Ready",
        "duration": 999,
        "flags": {"rip_ready": True},
    },
    "starfire_ready": {
        "type": "status",
        "name": "Starfire Ready",
        "duration": 999,
        "flags": {"starfire_ready": True},
    },
    "barkskin": {
        "type": "mitigation",
        "name": "Barkskin",
        "duration": 3,
        "value": 0.35,
    },
    "ironfur": {
        "type": "stat_mods",
        "name": "Ironfur",
        "duration": 4,
        "mods": {"physical_reduction": 3},
    },
    "typhoon_disoriented": {
        "type": "status",
        "name": "Typhoon",
        "duration": 2,
        "flags": {"forced_miss": True},
    },
    "cyclone": {
        "type": "status",
        "name": "Cyclone",
        "duration": 2,
        "flags": {"cycloned": True, "stunned": True, "immune_all": True},
    },
    "frenzied_regeneration": {
        "type": "status",
        "name": "Frenzied Regeneration",
        "duration": 4,
        "regen": {"hp": 0},
    },
    "regrowth": {
        "type": "status",
        "name": "Regrowth",
        "duration": 5,
        "regen": {"hp": 0},
    },
}

FORM_EFFECT_IDS = ("bear_form", "cat_form", "moonkin_form", "tree_form")
FORM_STAT_EFFECT_IDS = (
    "bear_form_stats",
    "cat_form_stats",
    "moonkin_form_stats",
    "tree_form_stats",
)
FORM_STAT_MAP = {
    "bear_form": "bear_form_stats",
    "cat_form": "cat_form_stats",
    "moonkin_form": "moonkin_form_stats",
    "tree_form": "tree_form_stats",
}
FORM_CLEAR_EFFECT_IDS = ("stealth", "rip_ready", "starfire_ready")


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


def build_effect(effect_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if effect_id not in EFFECT_TEMPLATES:
        return {}
    base = EFFECT_TEMPLATES.get(effect_id, {})
    effect = dict(base)
    effect["id"] = effect_id
    if overrides:
        effect.update(overrides)
    return effect


def apply_effect_by_id(
    target: PlayerState,
    effect_id: str,
    log: Optional[List[str]] = None,
    label: str = "",
    log_message: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    effect = build_effect(effect_id, overrides=overrides)
    if not effect:
        return
    target.effects.append(effect)
    if log is not None and log_message:
        prefix = f"{label} " if label else ""
        log.append(f"{prefix}{log_message}")


def has_effect(target: PlayerState, effect_id: str) -> bool:
    return any(effect.get("id") == effect_id for effect in target.effects)


def is_in_form(target: PlayerState, form_id: str) -> bool:
    return has_effect(target, form_id)


def current_form_id(target: PlayerState) -> Optional[str]:
    for form_id in FORM_EFFECT_IDS:
        if has_effect(target, form_id):
            return form_id
    return None


def clear_forms(target: PlayerState) -> None:
    target.effects = [
        effect
        for effect in target.effects
        if effect.get("id") not in FORM_EFFECT_IDS
        and effect.get("id") not in FORM_STAT_EFFECT_IDS
        and effect.get("id") not in FORM_CLEAR_EFFECT_IDS
    ]


def apply_form(
    target: PlayerState,
    form_id: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    clear_forms(target)
    apply_effect_by_id(target, form_id, overrides=overrides)
    stat_effect_id = FORM_STAT_MAP.get(form_id)
    if stat_effect_id:
        apply_effect_by_id(target, stat_effect_id)


def remove_effect(target: PlayerState, effect_id: str) -> None:
    target.effects = [effect for effect in target.effects if effect.get("id") != effect_id]


def remove_stealth(target: PlayerState) -> None:
    remove_effect(target, "stealth")


def break_stealth_on_damage(target: PlayerState, damage: int) -> None:
    if damage <= 0:
        return
    for effect in target.effects:
        if effect.get("id") != "stealth":
            continue
        threshold = effect.get("break_on_damage_over")
        if threshold is None or damage > int(threshold):
            remove_stealth(target)
        return


def has_flag(target: PlayerState, flag: str) -> bool:
    return any(effect.get("flags", {}).get(flag) for effect in target.effects)


def is_stunned(target: PlayerState) -> bool:
    return has_flag(target, "stunned")


def is_stealthed(target: PlayerState) -> bool:
    return has_flag(target, "stealthed")


def is_damage_immune(target: PlayerState, damage_type: str) -> bool:
    if has_flag(target, "immune_all"):
        return True
    if damage_type == "physical" and has_flag(target, "immune_physical"):
        return True
    if damage_type == "magic" and has_flag(target, "immune_magic"):
        return True
    return False


def modify_stat(target: PlayerState, stat: str, base_value: int) -> int:
    """Apply stat modifiers from effects; supports flat + mult."""
    value = base_value
    multiplier = 1.0
    for effect in target.effects:
        effect_type = effect.get("type")
        if effect_type == "stat_mod":
            if effect.get("stat") != stat:
                continue
            value += int(effect.get("flat", 0) or 0)
            multiplier *= float(effect.get("mult", 1.0) or 1.0)
        elif effect_type == "stat_mods":
            mods = effect.get("mods", {}) or {}
            if stat in mods:
                value += int(mods.get(stat, 0) or 0)
    return int(value * multiplier)


def mitigation_multiplier(target: PlayerState) -> float:
    """Sum mitigation effects and cap at 80%. Returns multiplier for damage."""
    total = 0.0
    for effect in target.effects:
        if effect.get("type") == "mitigation":
            total += float(effect.get("value", 0) or 0.0)
    total = max(0.0, min(total, 0.8))
    return 1.0 - total


def apply_burn(
    target: PlayerState,
    value: int,
    source_item: str = "Unknown",
    duration: int = 999,
    source_sid: Optional[str] = None,
) -> None:
    """Attach a burn DoT to the target (matches your existing burn shape)."""
    for effect in target.effects:
        if effect.get("type") == "burn":
            effect["value"] = max(int(effect.get("value", 0) or 0), int(value))
            effect["duration"] = max(int(effect.get("duration", 0) or 0), int(duration))
            effect["source"] = str(source_item)
            if source_sid is not None:
                effect["source_sid"] = source_sid
            return
    target.effects.append(
        {
            "type": "burn",
            "value": int(value),
            "duration": int(duration),
            "source": str(source_item),
            "source_sid": source_sid,
        }
    )


def add_absorb(ps: PlayerState, amount: int, source_item: Optional[str] = None, cap: Optional[int] = None) -> int:
    if not ps.res:
        return 0
    value = int(amount)
    if value == 0:
        return ps.res.absorb
    next_value = ps.res.absorb + value
    if cap is not None:
        next_value = max(0, min(next_value, int(cap)))
    else:
        next_value = max(0, next_value)
    ps.res.absorb = next_value
    if ps.res.absorb_max is not None:
        ps.res.absorb_max = max(ps.res.absorb_max, ps.res.absorb)
    return ps.res.absorb


def consume_absorb(ps: PlayerState, incoming: int) -> tuple[int, int]:
    if not ps.res:
        return 0, incoming
    incoming_value = max(0, int(incoming))
    if incoming_value <= 0 or ps.res.absorb <= 0:
        return 0, incoming_value
    absorbed = min(ps.res.absorb, incoming_value)
    ps.res.absorb -= absorbed
    remaining = incoming_value - absorbed
    return absorbed, remaining


def trigger_on_hit_passives(
    attacker: PlayerState,
    target: PlayerState,
    base_damage: int,
    damage_type: str,
    rng,
    ability: Optional[Dict[str, Any]] = None,
) -> tuple[int, List[str], int]:
    """Run attacker item passives that trigger on_hit."""
    bonus_damage = 0
    bonus_healing = 0
    log_lines: List[str] = []
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
                    source_sid=attacker.sid,
                )
                log_lines.append(
                    f"{attacker.sid[:5]} scorches the target with {effect.get('source_item', 'item')} ({burn_value} damage/turn)."
                )
        elif passive.get("type") == "strike_again":
            chance = float(passive.get("chance", 0) or 0)
            multiplier = float(passive.get("multiplier", 0) or 0)
            if base_damage > 0 and chance > 0 and multiplier > 0 and rng.random() <= chance:
                extra = int(base_damage * multiplier)
                if extra > 0:
                    bonus_damage += extra
                    log_lines.append(
                        f"{attacker.sid[:5]} strikes again with {effect.get('source_item', 'item')} for {extra} bonus damage."
                    )
        elif passive.get("type") == "void_blade":
            if base_damage <= 0:
                continue
            int_multiplier = float(passive.get("int_multiplier", 0.4) or 0.4)
            dice = passive.get("dice", "d4")
            roll_power = roll(dice, rng) if dice else 0
            intellect = modify_stat(attacker, "int", attacker.stats.get("int", 0))
            raw = int(intellect * int_multiplier) + int(roll_power)
            if raw <= 0:
                continue
            reduced = mitigate(raw, modify_stat(target, "def", target.stats.get("def", 0)))
            resist = modify_stat(target, "magic_resist", target.stats.get("magic_resist", 0))
            reduced = max(0, reduced - resist)
            reduced = int(reduced * mitigation_multiplier(target))
            if is_damage_immune(target, "magic"):
                reduced = 0
            if reduced > 0:
                bonus_damage += reduced
                log_lines.append(
                    f"{attacker.sid[:5]} calls upon the void with {effect.get('source_item', 'item')}. Roll {dice} = {roll_power}. Deals {reduced} magic damage."
                )
        elif passive.get("type") == "lightning_blast":
            chance = float(passive.get("chance", 0) or 0)
            scaling = passive.get("scaling", {}) or {}
            dice = passive.get("dice", "d3")
            if chance <= 0 or rng.random() > chance:
                continue
            roll_power = roll(dice, rng) if dice else 0
            raw = 0
            if "atk" in scaling:
                raw = calc_base_damage(
                    modify_stat(attacker, "atk", attacker.stats.get("atk", 0)),
                    scaling["atk"],
                    roll_power,
                )
            elif "int" in scaling:
                raw = calc_base_damage(
                    modify_stat(attacker, "int", attacker.stats.get("int", 0)),
                    scaling["int"],
                    roll_power,
                )
            if raw <= 0:
                continue
            reduced = mitigate(raw, modify_stat(target, "def", target.stats.get("def", 0)))
            resist = modify_stat(target, "magic_resist", target.stats.get("magic_resist", 0))
            reduced = max(0, reduced - resist)
            reduced = int(reduced * mitigation_multiplier(target))
            if is_damage_immune(target, "magic"):
                reduced = 0
            if reduced > 0:
                bonus_damage += reduced
                log_lines.append(
                    f"{attacker.sid[:5]} blasts the target with lightning from {effect.get('source_item', 'item')}. Roll {dice} = {roll_power}. Deals {reduced} magic damage."
                )
        elif passive.get("type") == "heal_on_hit":
            chance = float(passive.get("chance", 0) or 0)
            scaling = passive.get("scaling", {}) or {}
            dice = passive.get("dice", "d3")
            if chance <= 0 or rng.random() > chance:
                continue
            roll_power = roll(dice, rng) if dice else 0
            heal_value = 0
            if "atk" in scaling:
                heal_value = calc_base_damage(
                    modify_stat(attacker, "atk", attacker.stats.get("atk", 0)),
                    scaling["atk"],
                    roll_power,
                )
            elif "int" in scaling:
                heal_value = calc_base_damage(
                    modify_stat(attacker, "int", attacker.stats.get("int", 0)),
                    scaling["int"],
                    roll_power,
                )
            if heal_value > 0 and attacker.res:
                before_hp = attacker.res.hp
                attacker.res.hp = min(attacker.res.hp + heal_value, attacker.res.hp_max)
                bonus_healing += attacker.res.hp - before_hp
                log_lines.append(
                    f"{attacker.sid[:5]} draws strength from {effect.get('source_item', 'item')}, healing {heal_value} HP."
                )
        elif passive.get("type") == "empower_next_offense":
            chance = float(passive.get("chance", 0) or 0)
            effect_id = passive.get("effect_id", "crusader_empower")
            if chance > 0 and rng.random() <= chance and not has_effect(attacker, effect_id):
                overrides = {}
                if passive.get("multiplier") is not None:
                    overrides["damage_mult"] = float(passive.get("multiplier", 1.0) or 1.0)
                apply_effect_by_id(attacker, effect_id, overrides=overrides or None)
                log_lines.append(
                    f"{attacker.sid[:5]} feels empowered by {effect.get('source_item', 'item')}."
                )
        elif passive.get("type") == "duplicate_offensive_spell":
            if base_damage <= 0 or not ability:
                continue
            tags = ability.get("tags") or []
            if "spell" not in tags or "attack" not in tags:
                continue
            chance = float(passive.get("chance", 0) or 0)
            if chance <= 0 or rng.random() > chance:
                continue

            dice_data = ability.get("dice")
            scaling = ability.get("scaling", {}) or {}
            flat_damage = ability.get("flat_damage")

            roll_power = 0
            dice_type = None
            if dice_data:
                dice_type = dice_data.get("type")
                if dice_type:
                    roll_power = roll(dice_type, rng)

            duplicate_raw = 0
            if flat_damage is not None:
                duplicate_raw = int(flat_damage)
            elif "atk" in scaling:
                duplicate_raw = calc_base_damage(
                    modify_stat(attacker, "atk", attacker.stats.get("atk", 0)),
                    scaling["atk"],
                    roll_power,
                )
            elif "int" in scaling:
                duplicate_raw = calc_base_damage(
                    modify_stat(attacker, "int", attacker.stats.get("int", 0)),
                    scaling["int"],
                    roll_power,
                )
            if duplicate_raw <= 0:
                continue

            duplicate_reduced = mitigate(duplicate_raw, modify_stat(target, "def", target.stats.get("def", 0)))
            if damage_type == "physical":
                resist = modify_stat(target, "physical_reduction", target.stats.get("physical_reduction", 0))
                duplicate_reduced = max(0, duplicate_reduced - resist)
            elif damage_type == "magic":
                resist = modify_stat(target, "magic_resist", target.stats.get("magic_resist", 0))
                duplicate_reduced = max(0, duplicate_reduced - resist)

            duplicate_reduced = int(duplicate_reduced * mitigation_multiplier(target))
            if is_damage_immune(target, damage_type):
                duplicate_reduced = 0
            if duplicate_reduced <= 0:
                continue

            bonus_damage += duplicate_reduced
            item_name = effect.get("source_item", "item")
            ability_name = ability.get("name", "spell")
            if dice_type:
                log_lines.append(
                    f"{item_name} duplicates {ability_name}! Roll {dice_type} = {roll_power}. Deals {duplicate_reduced} damage."
                )
            else:
                log_lines.append(
                    f"{item_name} duplicates {ability_name}! Deals {duplicate_reduced} damage."
                )

    return bonus_damage, log_lines, bonus_healing

def damage_multiplier_from_passives(attacker: PlayerState) -> float:
    """Apply conditional damage multipliers from item passives."""
    if not attacker.res:
        return 1.0
    hp_pct = attacker.res.hp / max(1, attacker.res.hp_max)
    multiplier = 1.0
    for effect in attacker.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "on_damage":
            continue
        if passive.get("type") == "damage_bonus_above_hp":
            threshold = float(passive.get("threshold", 0) or 0)
            if hp_pct > threshold:
                multiplier *= float(passive.get("multiplier", 1.0) or 1.0)
        elif passive.get("type") == "damage_bonus_below_hp":
            threshold = float(passive.get("threshold", 0) or 0)
            if hp_pct < threshold:
                multiplier *= float(passive.get("multiplier", 1.0) or 1.0)
    return multiplier

def tick_dots(ps: PlayerState, log: List[str], label: str) -> list[tuple[str, int]]:
    """Apply DoT damage (currently: burn)."""
    damage_sources: list[tuple[str, int]] = []
    for effect in ps.effects:
        if effect.get("type") == "burn":
            burn_dmg = int(effect.get("value", 0) or 0)
            if burn_dmg > 0:
                ps.res.hp -= burn_dmg
                break_stealth_on_damage(ps, burn_dmg)
                log.append(f"{label} burns for {burn_dmg} damage.")
                source_sid = effect.get("source_sid")
                if source_sid:
                    damage_sources.append((source_sid, burn_dmg))
    return damage_sources


def trigger_end_of_turn_passives(ps: PlayerState, log: List[str], label: str) -> int:
    """Run end-of-turn item passives (currently: heal_self)."""
    total_healing = 0
    for effect in ps.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "end_of_turn":
            continue

        if passive.get("type") == "heal_self":
            heal_value = int(passive.get("value", 0) or 0)
            if heal_value > 0:
                before_hp = ps.res.hp
                ps.res.hp = min(ps.res.hp + heal_value, ps.res.hp_max)
                total_healing += ps.res.hp - before_hp
                log.append(
                    f"{label} heals {heal_value} HP from {effect.get('source_item', 'item')}."
                )
        elif passive.get("type") == "absorb_self":
            absorb_value = int(passive.get("value", 0) or 0)
            if absorb_value > 0:
                add_absorb(ps, absorb_value)
                log.append(
                    f"{label} gains {absorb_value} absorb from {effect.get('source_item', 'item')}."
                )
    return total_healing


def trigger_end_of_turn_effects(ps: PlayerState, log: List[str], label: str) -> int:
    """Run end-of-turn status effects such as regeneration from buffs."""
    total_healing = 0
    for effect in ps.effects:
        regen = effect.get("regen", {}) or {}
        if not regen:
            continue
        hp_gain = int(regen.get("hp", 0) or 0)
        mp_gain = int(regen.get("mp", 0) or 0)
        energy_gain = int(regen.get("energy", 0) or 0)
        if hp_gain > 0:
            before_hp = ps.res.hp
            ps.res.hp = min(ps.res.hp + hp_gain, ps.res.hp_max)
            total_healing += ps.res.hp - before_hp
        if mp_gain > 0:
            ps.res.mp = min(ps.res.mp + mp_gain, ps.res.mp_max)
        if energy_gain > 0:
            ps.res.energy = min(ps.res.energy + energy_gain, ps.res.energy_max)
        if (hp_gain > 0 or mp_gain > 0 or energy_gain > 0) and log is not None:
            effect_name = effect.get("name", "an effect")
            log.append(
                f"{label} recovers {hp_gain} HP, {mp_gain} MP, and {energy_gain} Energy from {effect_name}."
            )
    return total_healing


def end_of_turn(ps: PlayerState, log: List[str], label: str) -> dict[str, Any]:
    """End-of-turn pipeline: DoTs, passives, duration tick, regen."""
    if not ps.res:
        return {"damage_sources": [], "healing_done": 0}

    if has_flag(ps, "cycloned"):
        ps.effects = tick_durations(ps.effects)
        return {"damage_sources": [], "healing_done": 0}

    damage_sources = tick_dots(ps, log, label)
    total_healing = 0
    total_healing += trigger_end_of_turn_passives(ps, log, label)
    total_healing += trigger_end_of_turn_effects(ps, log, label)
    ps.effects = tick_durations(ps.effects)

    if ps.res.hp > 0:
        ps.res.mp = min(ps.res.mp + DEFAULTS["mp_regen_per_turn"], ps.res.mp_max)
        ps.res.energy = min(ps.res.energy + DEFAULTS["energy_regen_per_turn"], ps.res.energy_max)
    return {"damage_sources": damage_sources, "healing_done": total_healing}
