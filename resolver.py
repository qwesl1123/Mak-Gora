# games/duel/engine/resolver.py
from typing import Dict, Any, Tuple
from .models import MatchState, PlayerState, PlayerBuild, Resources
from .dice import rng_for, roll
from .rules import base_damage, mitigate, hit_chance, clamp
from ..content.abilities import ABILITIES
from ..content.classes import CLASSES
from ..content.items import ITEMS
from ..content.balance import DEFAULTS, CAPS

# Centralized mechanics (passives/DoTs/mitigation/regen) live here.
from .effects import (
    mitigation_multiplier,
    trigger_on_hit_passives,
    damage_multiplier_from_passives,
    end_of_turn,
    apply_effect_by_id,
    apply_form,
    break_stealth_on_damage,
    has_effect,
    remove_effect,
    remove_stealth,
    modify_stat,
    is_stunned,
    get_cant_act_reason,
    is_stealthed,
    is_immune_all,
    is_damage_immune,
    has_flag,
    add_absorb,
    consume_absorb,
    build_effect,
    dispel_effects,
    refresh_dot_effect,
    tick_durations,
    FORM_EFFECT_IDS,
    current_form_id,
    get_effect,
    outgoing_damage_multiplier,
)

def cooldown_slots(ps: PlayerState, ability_id: str) -> list:
    stored = ps.cooldowns.get(ability_id, [])
    if isinstance(stored, int):
        return [stored] if stored > 0 else []
    return list(stored or [])


def ability_charges(ability: Dict[str, Any]) -> int:
    return max(1, int(ability.get("charges", 1) or 1))


def is_on_cooldown(ps: PlayerState, ability_id: str, ability: Dict[str, Any]) -> bool:
    charges = ability_charges(ability)
    return len(cooldown_slots(ps, ability_id)) >= charges


def cooldown_remaining(ps: PlayerState, ability_id: str, ability: Dict[str, Any]) -> int:
    charges = ability_charges(ability)
    slots = cooldown_slots(ps, ability_id)
    if len(slots) < charges:
        return 0
    return min(slots) if slots else 0

def apply_prep_build(match: MatchState) -> None:
    """
    Called once when both players have selected class + items.
    Creates PlayerState.res/stats from content + item modifiers.
    """
    match.combat_totals = {sid: {"damage": 0, "healing": 0} for sid in match.players}
    for sid in match.players:
        pick = match.picks.get(sid)
        if isinstance(pick, PlayerBuild):
            build = pick
        else:
            payload = pick or {}
            build = PlayerBuild(class_id=payload.get("class_id"))
            build.items.update(payload.get("items", {}))

        class_data = CLASSES.get(build.class_id, next(iter(CLASSES.values())))
        stats = dict(class_data["base_stats"])
        stats.setdefault("int", 0)
        
        # Initialize damage reduction stats
        stats["physical_reduction"] = 0
        stats["magic_resist"] = 0

        res_data = class_data["resources"]
        hp_max = res_data.get("hp", DEFAULTS["hp"])
        mp_max = res_data.get("mp", DEFAULTS["mp"])
        energy_max = res_data.get("energy", DEFAULTS["energy"])
        rage_max = res_data.get("rage_max", DEFAULTS["rage_max"])

        # Store equipped items for passive effects
        equipped_items = []
        
        for slot, item_id in build.items.items():
            if not item_id:
                continue
            item = ITEMS.get(item_id)
            if not item:
                continue
            allowed_classes = item.get("classes")
            if allowed_classes and build.class_id not in allowed_classes:
                continue
            
            equipped_items.append(item)
            
            for stat, delta in item.get("mods", {}).items():
                if stat in ("hp", "hp_max"):
                    hp_max += delta
                elif stat in ("mp", "mp_max"):
                    mp_max += delta
                elif stat in ("energy", "energy_max"):
                    energy_max += delta
                elif stat in ("rage", "rage_max"):
                    rage_max += delta
                else:
                    stats[stat] = stats.get(stat, 0) + delta

        stats["crit"] = clamp(stats.get("crit", 0), CAPS["crit_min"], CAPS["crit_max"])
        stats["acc"] = clamp(stats.get("acc", 0), CAPS["acc_min"], CAPS["acc_max"])

        res = Resources(
            hp=hp_max,
            hp_max=hp_max,
            mp=mp_max,
            mp_max=mp_max,
            energy=energy_max,
            energy_max=energy_max,
            rage=res_data.get("rage", DEFAULTS["rage"]),
            rage_max=rage_max,
            absorb=0,
            absorb_max=None,
        )
        
        ps = PlayerState(sid=sid, build=build, res=res, stats=stats, minions={"imp": 0})

        if build.class_id == "rogue":
            apply_effect_by_id(ps, "stealth")
        
        # Store item references for passive effects
        for item in equipped_items:
            passives = item.get("passive")
            if not passives:
                continue
            if isinstance(passives, list):
                passive_list = passives
            else:
                passive_list = [passives]
            for passive in passive_list:
                ps.effects.append({
                    "type": "item_passive",
                    "source_item": item["name"],
                    "passive": passive,
                    "duration": 999,  # Permanent passive
                })
        
        match.state[sid] = ps

def submit_action(match: MatchState, sid: str, action: Dict[str, Any]) -> None:
    match.submitted[sid] = action

def ready_to_resolve(match: MatchState) -> bool:
    return len(match.submitted) == 2

def resolve_turn(match: MatchState) -> None:
    """
    Resolves both submitted actions simultaneously.
    Appends to match.log and updates match.state.
    Clears submissions and increments match.turn.
    """
    r = rng_for(match.seed, match.turn)
    sids = match.players
    a1 = match.submitted.get(sids[0], {})
    a2 = match.submitted.get(sids[1], {})
    stunned_at_start = {sid: is_stunned(match.state[sid]) for sid in sids}
    stealth_start_at_turn_begin = {sid: is_stealthed(match.state[sid]) for sid in sids}
    stealth_targeting = dict(stealth_start_at_turn_begin)
    match.log.append(f"Turn {match.turn + 1}")

    def can_pay_costs(ps: PlayerState, costs: Dict[str, int]) -> Tuple[bool, str]:
        res = ps.res
        for key, value in costs.items():
            current = getattr(res, key)
            if current < value:
                if key == "rage":
                    return False, "not enough rage"
                return False, f"not enough {key}"
        return True, ""

    def effect_name(effect_id: str) -> str:
        return effect_id.replace("_", " ").title()

    def has_circle(ps: PlayerState) -> bool:
        return has_flag(ps, "demonic_circle") or has_effect(ps, "demonic_circle")

    def consume_costs(ps: PlayerState, costs: Dict[str, int]) -> None:
        res = ps.res
        for key, value in costs.items():
            new_value = getattr(res, key) - value
            cap = getattr(res, f"{key}_max", None)
            if cap is not None:
                new_value = max(0, min(new_value, cap))
            setattr(res, key, new_value)

    def set_cooldown(ps: PlayerState, ability_id: str, ability: Dict[str, Any]) -> None:
        cooldown = int(ability.get("cooldown", 0) or 0)
        if cooldown <= 0:
            return

        slots = cooldown_slots(ps, ability_id)
        slots.append(cooldown)
        applied_slots = list(slots)
        ps.cooldowns[ability_id] = applied_slots

        shared = ability.get("shared_cooldown_with") or []
        for linked_ability_id in shared:
            if not linked_ability_id or linked_ability_id == ability_id:
                continue
            ps.cooldowns[linked_ability_id] = list(applied_slots)

    def tick_cooldowns(ps: PlayerState) -> None:
        updated = {}
        for ability_id, remaining in ps.cooldowns.items():
            slots = [remaining] if isinstance(remaining, int) else list(remaining or [])
            next_slots = []
            for slot in slots:
                remaining_turns = int(slot) - 1
                if remaining_turns > 0:
                    next_slots.append(remaining_turns)
            if next_slots:
                updated[ability_id] = next_slots
        ps.cooldowns = updated

    def untargetable_miss_log(target: PlayerState) -> str:
        for effect in reversed(target.effects):
            flags = effect.get("flags", {}) or {}
            if not flags.get("untargetable"):
                continue
            custom = effect.get("miss_log") or effect.get("overrides", {}).get("miss_log")
            if custom:
                return str(custom)
        return "Target blinks away — Miss."

    def target_immune_log(log_parts: list) -> None:
        log_parts.append("Target is immune!")

    def apply_effect_entries(
        actor: PlayerState,
        target: PlayerState,
        ability: Dict[str, Any],
        log_parts: list,
        skip_self_effect_ids: set[str] | None = None,
    ) -> None:
        skip_self_effect_ids = skip_self_effect_ids or set()
        for entry in ability.get("self_effects", []) or []:
            if entry.get("type") == "dispel":
                removed = dispel_effects(
                    actor,
                    category=entry.get("category"),
                    school=entry.get("school"),
                )
                if removed > 0:
                    school = entry.get("school")
                    school_text = f" {school}" if school else ""
                    log_parts.append(f"{actor.sid[:5]} dispels {removed}{school_text} effects.")
                continue
            effect_id = entry.get("id")
            if not effect_id:
                continue
            if effect_id in skip_self_effect_ids:
                if entry.get("log"):
                    log_parts.append(entry["log"])
                continue
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            if effect_id in FORM_EFFECT_IDS:
                apply_form(actor, effect_id, overrides=overrides or None)
            else:
                apply_effect_by_id(actor, effect_id, overrides=overrides or None)
            if entry.get("log"):
                log_parts.append(entry["log"])
        for entry in ability.get("target_effects", []) or []:
            if is_immune_all(target):
                target_immune_log(log_parts)
                continue
            if target_effect_requires_visible_target(ability, entry) and is_stealthed(target):
                log_parts.append("Target is stealthed — no valid target. Miss!")
                continue
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            if entry["id"] in FORM_EFFECT_IDS:
                apply_form(target, entry["id"], overrides=overrides or None)
            else:
                apply_effect_by_id(target, entry["id"], overrides=overrides or None)
            if entry.get("log"):
                log_parts.append(entry["log"])

    def is_offensive_action(ability: Dict[str, Any]) -> bool:
        if "pass" in (ability.get("tags") or []):
            return False
        has_damage = any(value for value in (ability.get("dice"), ability.get("scaling"), ability.get("flat_damage")))
        has_target_effects = bool(ability.get("target_effects"))
        return has_damage or has_target_effects

    def should_miss_due_to_stealth(
        attacker: PlayerState,
        target: PlayerState,
        ability: Dict[str, Any] | None,
        stealth_snapshot: Dict[str, bool],
    ) -> bool:
        if not ability:
            return False
        is_aoe = "aoe" in (ability.get("tags") or []) or not ability.get("requires_target", True)
        if is_aoe:
            return False
        return bool(stealth_snapshot.get(target.sid, False) or is_stealthed(target))

    def is_aoe_ability(ability: Dict[str, Any]) -> bool:
        if "is_aoe" in ability:
            return bool(ability.get("is_aoe"))
        return "aoe" in (ability.get("tags") or []) or not ability.get("requires_target", True)

    def is_single_target_ability(ability: Dict[str, Any]) -> bool:
        if "is_single_target" in ability:
            return bool(ability.get("is_single_target"))
        return not is_aoe_ability(ability)

    def target_effect_requires_visible_target(ability: Dict[str, Any], entry: Dict[str, Any]) -> bool:
        if not is_single_target_ability(ability):
            return False
        effect = build_effect(entry["id"], overrides=entry.get("overrides"))
        flags = effect.get("flags", {}) or {}
        if flags.get("stunned"):
            return True
        reason = effect.get("cant_act_reason")
        return reason in {"stunned", "feared", "frozen"}

    def can_evasion_force_miss(ability: Dict[str, Any], has_damage: bool) -> bool:
        if not has_damage:
            return False
        damage_type = ability.get("damage_type", "physical")
        return (
            damage_type == "physical"
            and not is_aoe_ability(ability)
            and is_single_target_ability(ability)
        )

    def can_cast_while_cc(ability: Dict[str, Any]) -> bool:
        return bool(ability.get("allow_while_stunned") or ability.get("priority_defensive"))

    def resolve_action(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"damage": 0, "healing": 0, "log": f"{actor_sid[:5]} fumbles (unknown ability)."}

        allowed_classes = ability.get("classes")
        if allowed_classes and actor.build.class_id not in allowed_classes:
            return {"damage": 0, "healing": 0, "log": f"{actor_sid[:5]} cannot use {ability['name']}."}

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")

        if is_on_cooldown(actor, ability_id, ability):
            return {
                "damage": 0,
                "healing": 0,
                "log": f"{actor_sid[:5]} tried to use {ability['name']} but it is on cooldown.",
            }

        required_form = ability.get("requires_form")
        if required_form and current_form_id(actor) != required_form:
            return {
                "damage": 0,
                "healing": 0,
                "log": ability.get("requires_form_log", "Must be in the correct form."),
            }

        required_effect = ability.get("requires_effect")
        if required_effect and not has_effect(actor, required_effect):
            return {"damage": 0, "healing": 0, "log": f"{ability['name']} requires {effect_name(required_effect)}."}

        required_weapon = ability.get("requires_weapon")
        if required_weapon and weapon_id != required_weapon:
            return {
                "damage": 0,
                "healing": 0,
                "log": ability.get("requires_weapon_log", "The required weapon is not equipped."),
            }

        target_hp_threshold = ability.get("requires_target_hp_below")
        if target_hp_threshold is not None:
            if target.res.hp / max(1, target.res.hp_max) >= float(target_hp_threshold):
                return {"damage": 0, "healing": 0, "log": f"{ability['name']} can only be used as an execute."}

        if ability.get("requires_circle") and not has_circle(actor):
            return {"damage": 0, "healing": 0, "log": "Demonic Circle is required."}

        if ability_id == "agony" and has_effect(target, "agony"):
            return {"damage": 0, "healing": 0, "log": "Agony is not stackable."}

        if ability_id == "summon_imp" and int((actor.minions or {}).get("imp", 0)) >= 3:
            return {"damage": 0, "healing": 0, "log": "3 Imps Maximum"}

        ok, fail_reason = can_pay_costs(actor, ability.get("cost", {}))
        if not ok:
            if fail_reason == "not enough rage":
                return {"damage": 0, "healing": 0, "log": "not enough rage"}
            return {
                "damage": 0,
                "healing": 0,
                "log": f"{actor_sid[:5]} tried {ability['name']} but lacked resources.",
            }

        weapon_name = ITEMS.get(weapon_id, {}).get("name", "their bare hands")

        if is_stunned(actor) and not can_cast_while_cc(ability):
            reason = get_cant_act_reason(actor)
            if reason:
                reason_text = f"is {reason} and cannot act"
            else:
                reason_text = "cannot act"
            return {
                "damage": 0,
                "healing": 0,
                "log": f"{actor_sid[:5]} tries to use {ability['name']} but {reason_text}.",
            }

        log_parts = [f"{actor_sid[:5]} uses {weapon_name} to cast {ability['name']}."]

        has_target_effects = bool(ability.get("target_effects"))
        has_self_effects = bool(ability.get("self_effects"))
        is_aoe = is_aoe_ability(ability)
        if "pass" in (ability.get("tags") or []):
            set_cooldown(actor, ability_id, ability)
            log_parts.append("Passes the turn.")
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        was_stealthed = has_effect(actor, "stealth")
        offensive_action = is_offensive_action(ability)

        consume_costs(actor, ability.get("cost", {}))

        dice_data = ability.get("dice")
        scaling = ability.get("scaling", {}) or {}
        flat_damage = ability.get("flat_damage")
        stealth_bonus = ability.get("stealth_bonus") if was_stealthed else None
        if stealth_bonus:
            dice_data = stealth_bonus.get("dice", dice_data)
            scaling = stealth_bonus.get("scaling", scaling)
            flat_damage = stealth_bonus.get("flat_damage", flat_damage)
            stealth_log = stealth_bonus.get("log")
            if stealth_log:
                log_parts.append(stealth_log)

        has_damage = any(value for value in (dice_data, scaling, flat_damage))

        if has_damage or has_target_effects:
            if should_miss_due_to_stealth(actor, target, ability, stealth_targeting):
                log_parts.append("Target is stealthed — Miss!")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}
            if has_flag(target, "untargetable"):
                log_parts.append(untargetable_miss_log(target))
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}
            if has_flag(target, "evade_all") and can_evasion_force_miss(ability, has_damage):
                log_parts.append("Evaded!")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        if has_self_effects or has_target_effects:
            apply_effect_entries(actor, target, ability, log_parts)

        if ability_id == "healthstone":
            heal_value = max(1, int(actor.res.hp_max * 0.25))
            if has_effect(actor, "mindgames"):
                actor.res.hp = max(0, actor.res.hp - heal_value)
                log_parts.append(f"Mindgames twists healing into {heal_value} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
            healed = actor.res.hp - before_hp
            log_parts.append(f"Healthstone restores {healed} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "summon_imp":
            actor.minions["imp"] = int((actor.minions or {}).get("imp", 0)) + 1
            imp_count = actor.minions["imp"]
            log_parts.append(f"summons an Imp ({imp_count}/3).")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id in ("corruption", "unstable_affliction"):
            if is_immune_all(target):
                target_immune_log(log_parts)
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            roll_power = roll("d4", r)
            scale = float((ability.get("scaling") or {}).get("int", 0.0) or 0.0)
            total_dot = int(intellect * scale) + int(roll_power)
            dot_data = ability.get("dot", {})
            duration = int(dot_data.get("duration", 1) or 1)
            tick_damage = max(1, total_dot // max(1, duration))
            dot_id = dot_data.get("id")
            if dot_id and refresh_dot_effect(target, dot_id, duration=duration, tick_damage=tick_damage, source_sid=actor.sid):
                log_parts.append(f"refreshes {effect_name(dot_id)}.")
            elif dot_id:
                apply_effect_by_id(target, dot_id, overrides={"duration": duration, "tick_damage": tick_damage, "source_sid": actor.sid})
                log_parts.append(f"applies {effect_name(dot_id)}.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "agony":
            if should_miss_due_to_stealth(actor, target, ability, stealth_targeting):
                log_parts.append("Target is stealthed — Miss!")
                set_cooldown(actor, ability_id, ability)
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            if is_immune_all(target):
                target_immune_log(log_parts)
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            apply_effect_by_id(target, "agony", overrides={"duration": 15, "tick": 1, "source_sid": actor.sid})
            log_parts.append("inflicts Agony.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "ice_barrier":
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            roll_power = roll("d6", r)
            absorb_value = int(intellect * 0.9) + int(roll_power)
            add_absorb(actor, absorb_value, source_name="Ice Barrier")
            log_parts.append(f"Ice Barrier grants {absorb_value} absorb.")
            set_cooldown(actor, ability_id, ability)
            if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        if ability_id == "frenzied_regeneration":
            if actor.res.rage <= 0:
                return {"damage": 0, "healing": 0, "log": "not enough rage"}
            total_heal = int(actor.res.rage)
            per_tick = total_heal // 4
            actor.res.rage = 0
            apply_effect_by_id(
                actor,
                "frenzied_regeneration",
                overrides={"duration": 4, "regen": {"hp": per_tick}},
            )
            log_parts.append("channels Frenzied Regeneration.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        if ability_id == "wild_growth":
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            attack = modify_stat(actor, "atk", actor.stats.get("atk", 0))
            roll_power = roll("d8", r)
            heal_value = int((intellect + attack) * 1.6) + int(roll_power)
            if has_effect(actor, "mindgames"):
                actor.res.hp = max(0, actor.res.hp - heal_value)
                log_parts.append(f"Mindgames twists healing into {heal_value} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}
            healing_done = 0
            if not has_flag(actor, "cycloned"):
                before_hp = actor.res.hp
                actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
                healing_done = actor.res.hp - before_hp
            log_parts.append(f"Wild Growth heals {heal_value} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healing_done, "log": " ".join(log_parts)}

        if ability_id == "regrowth":
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            attack = modify_stat(actor, "atk", actor.stats.get("atk", 0))
            roll_power = roll("d4", r)
            total_heal = int((intellect + attack) * 1.5) + int(roll_power)
            per_tick = total_heal // 5
            apply_effect_by_id(
                actor,
                "regrowth",
                overrides={"duration": 5, "regen": {"hp": per_tick}},
            )
            log_parts.append("Healing over time for 5 turns.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        if ability_id == "innervate":
            actor.res.mp = actor.res.mp_max
            log_parts.append("restores their mana to full.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        if ability_id == "holy_light":
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            heal_value = int(intellect * 2.0) + int(roll("d4", r))
            if has_effect(actor, "mindgames"):
                actor.res.hp = max(0, actor.res.hp - heal_value)
                log_parts.append(f"Mindgames twists healing into {heal_value} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
            healed = actor.res.hp - before_hp
            log_parts.append(f"Holy Light restores {healed} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "lay_on_hands":
            if has_effect(actor, "mindgames"):
                backlash = actor.res.hp
                actor.res.hp = 0
                log_parts.append(f"Mindgames twists healing into {backlash} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = actor.res.hp_max
            healed = actor.res.hp - before_hp
            log_parts.append("Lay on Hands restores health to full.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "shield_of_vengeance":
            attack = modify_stat(actor, "atk", actor.stats.get("atk", 0))
            absorb_value = int(attack * 0.8) + int(roll("d6", r))
            add_absorb(actor, absorb_value, source_name="Shield of Vengeance")
            remove_effect(actor, "shield_of_vengeance")
            apply_effect_by_id(actor, "shield_of_vengeance", overrides={"duration": 3, "absorb_total": 0})
            log_parts.append(f"Shield of Vengeance grants {absorb_value} absorb.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id in ("vampiric_touch", "devouring_plague"):
            if is_immune_all(target):
                target_immune_log(log_parts)
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            dice_type = "d6" if ability_id == "vampiric_touch" else "d4"
            roll_power = roll(dice_type, r)
            scale = float((ability.get("scaling") or {}).get("int", 0.0) or 0.0)
            total_dot = int(intellect * scale) + int(roll_power)
            dot_data = ability.get("dot", {})
            duration = int(dot_data.get("duration", 1) or 1)
            tick_damage = max(1, total_dot // max(1, duration))
            dot_id = dot_data.get("id")
            if dot_id and refresh_dot_effect(target, dot_id, duration=duration, tick_damage=tick_damage, source_sid=actor.sid):
                for fx in target.effects:
                    if fx.get("id") == dot_id:
                        fx["lifesteal_pct"] = 0.4 if dot_id == "vampiric_touch" else 0.3
                        fx["school"] = "magical"
                        fx["dispellable"] = (dot_id == "vampiric_touch")
                        break
                log_parts.append(f"refreshes {effect_name(dot_id)}.")
            elif dot_id:
                apply_effect_by_id(target, dot_id, overrides={
                    "duration": duration,
                    "tick_damage": tick_damage,
                    "source_sid": actor.sid,
                    "school": "magical",
                    "dispellable": (dot_id == "vampiric_touch"),
                    "lifesteal_pct": 0.4 if dot_id == "vampiric_touch" else 0.3,
                })
                log_parts.append(f"applies {effect_name(dot_id)}.")
            if ability.get("consume_effect"):
                remove_effect(actor, ability["consume_effect"])
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "penance_self":
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            healing = 0
            for hit_index in range(1, 4):
                roll_power = roll("d4", r)
                heal_value = base_damage(intellect, 0.4, roll_power)
                if has_effect(actor, "mindgames"):
                    actor.res.hp = max(0, actor.res.hp - heal_value)
                    log_parts.append(f"Hit {hit_index}: Mindgames turns healing into {heal_value} self-damage.")
                    continue
                before_hp = actor.res.hp
                actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
                gained = actor.res.hp - before_hp
                healing += gained
                log_parts.append(f"Hit {hit_index}: Restores {gained} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healing, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "shadowfiend":
            remove_effect(actor, "shadowfiend")
            apply_effect_by_id(actor, "shadowfiend", overrides={"duration": 5})
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        absorb_data = ability.get("absorb") or {}
        if absorb_data:
            absorb_scaling = absorb_data.get("scaling", {}) or {}
            absorb_dice = absorb_data.get("dice") or {}
            absorb_roll = 0
            if absorb_dice.get("type"):
                absorb_roll = roll(absorb_dice["type"], r)
            absorb_value = int(absorb_data.get("flat", 0) or 0)
            if "atk" in absorb_scaling:
                absorb_value += base_damage(
                    modify_stat(actor, "atk", actor.stats.get("atk", 0)),
                    absorb_scaling["atk"],
                    absorb_roll,
                )
            elif "int" in absorb_scaling:
                absorb_value += base_damage(
                    modify_stat(actor, "int", actor.stats.get("int", 0)),
                    absorb_scaling["int"],
                    absorb_roll,
                )
            absorb_value = max(0, int(absorb_value))
            if absorb_value > 0:
                add_absorb(actor, absorb_value, source_name=absorb_data.get("source_name") or ability.get("name") or "Shield")
            log_parts.append(f"{ability.get('name', 'Shield')} grants {absorb_value} absorb.")
            set_cooldown(actor, ability_id, ability)
            if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if not has_damage:
            set_cooldown(actor, ability_id, ability)
            if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        # Calculate base damage using appropriate stat
        damage_type = ability.get("damage_type", "physical")
        hits = int(ability.get("hits", 1) or 1)
        total_damage = 0
        total_healing = 0
        empower_multiplier = 1.0
        consume_empower = False
        empower_logged = False
        outgoing_mult = outgoing_damage_multiplier(actor)
        if offensive_action and has_damage:
            for effect in actor.effects:
                if effect.get("flags", {}).get("empower_next_offense"):
                    empower_multiplier = float(effect.get("damage_mult", 1.0) or 1.0)
                    consume_empower = True
                    break
        miss_chance = float(ITEMS.get(weapon_id, {}).get("miss_chance", 0) or 0) if weapon_id else 0.0
        accuracy = hit_chance(
            modify_stat(actor, "acc", actor.stats.get("acc", 90)),
            modify_stat(target, "eva", target.stats.get("eva", 0)),
        )

        extra_logs: list[str] = []
        ability_hit_landed = False
        on_hit_base_damage = 0
        per_hit_damage_values: list[int] = []
        death_doubled = (
            ability_id == "death"
            and target.res.hp / max(1, target.res.hp_max) < 0.2
        )
        for hit_index in range(1, hits + 1):
            prefix = f"Hit {hit_index}: " if hits > 1 else ""
            roll_power = 0
            if dice_data:
                roll_power = roll(dice_data["type"], r)
                log_parts.append(f"{prefix}Roll {dice_data['type']} = {roll_power}.")

            if has_flag(actor, "forced_miss"):
                log_parts.append(f"{prefix}Miss!")
                continue

            if miss_chance > 0 and r.random() <= miss_chance:
                log_parts.append(f"{prefix}Misfire!")
                continue

            if r.randint(1, 100) > accuracy:
                log_parts.append(f"{prefix}Miss!")
                continue

            ability_hit_landed = True

            raw = 0
            local_scaling = dict(scaling)
            if ability_id == "final_verdict" and has_effect(actor, "paladin_final_verdict_empowered"):
                local_scaling = {"atk": 2.0}
            elif ability_id == "crusader_strike" and has_effect(actor, "avenging_wrath"):
                local_scaling = {"atk": 1.0}
            elif ability_id == "judgment" and has_effect(actor, "avenging_wrath"):
                local_scaling = {"atk": 1.4}

            if ability_id == "mind_blast" and has_effect(actor, "mind_blast_empowered"):
                local_scaling = {"int": 1.3}
                roll_power = roll("d8", r)
                log_parts.append(f"{prefix}Empowered Roll d8 = {roll_power}.")
            if flat_damage is not None:
                raw = int(flat_damage)
            elif "atk" in local_scaling:
                raw = base_damage(modify_stat(actor, "atk", actor.stats.get("atk", 0)), local_scaling["atk"], roll_power)
            elif "int" in local_scaling:
                raw = base_damage(modify_stat(actor, "int", actor.stats.get("int", 0)), local_scaling["int"], roll_power)

            # Apply critical hit
            crit_chance = modify_stat(actor, "crit", actor.stats.get("crit", 0))
            did_crit = bool(ability.get("always_crit")) or (raw > 0 and r.randint(1, 100) <= crit_chance)
            if raw > 0 and did_crit:
                raw = int(raw * 1.5)
                log_parts.append(f"{prefix}Critical hit!")

            # Apply defense mitigation
            reduced = mitigate(raw, modify_stat(target, "def", target.stats.get("def", 0)))

            # Apply damage type specific resistance
            if damage_type == "physical":
                resist = 0
                if not ability.get("ignore_physical_reduction"):
                    resist = modify_stat(target, "physical_reduction", target.stats.get("physical_reduction", 0))
                reduced = max(0, reduced - resist)
            elif damage_type == "magic":
                resist = modify_stat(target, "magic_resist", target.stats.get("magic_resist", 0))
                reduced = max(0, reduced - resist)

            # Apply defensive buff mitigation
            reduced = int(reduced * mitigation_multiplier(target))

            if is_damage_immune(target, damage_type):
                reduced = 0
                log_parts.append(f"{prefix}Immune!")
            else:
                multiplier = damage_multiplier_from_passives(actor)
                if multiplier != 1.0:
                    reduced = int(reduced * multiplier)
                if empower_multiplier != 1.0:
                    reduced = int(reduced * empower_multiplier)
                    if not empower_logged:
                        log_parts.append(f"{prefix}Empowered strike!")
                        empower_logged = True
                if outgoing_mult != 1.0:
                    reduced = int(reduced * outgoing_mult)
                if death_doubled and reduced > 0:
                    reduced *= 2
                    log_parts.append(f"{prefix}Damage Doubled!")
                if reduced > 0:
                    log_parts.append(f"{prefix}Deals __DMG_{len(per_hit_damage_values)}__ damage.")
                else:
                    log_parts.append(f"{prefix}Deals 0 damage.")

            if reduced > 0 and on_hit_base_damage == 0:
                on_hit_base_damage = reduced
            if reduced > 0:
                per_hit_damage_values.append(reduced)

            if reduced > 0:
                effects_on_hit = ability.get("stealth_on_hit_effects") if was_stealthed else None
                if not effects_on_hit:
                    effects_on_hit = ability.get("on_hit_effects", [])
                for effect in effects_on_hit:
                    chance = float(effect.get("chance", 0) or 0)
                    if chance > 0 and r.random() <= chance:
                        if not has_effect(actor, effect["id"]):
                            apply_effect_by_id(
                                actor,
                                effect["id"],
                                log=match.log,
                                label=actor_sid[:5],
                                log_message=effect.get("log"),
                            )

            heal_on_hit = int(ability.get("heal_on_hit", 0) or 0)
            heal_scaling = ability.get("heal_scaling", {}) or {}
            heal_dice = ability.get("heal_dice")
            if heal_scaling or heal_dice:
                roll_power = 0
                if heal_dice:
                    roll_power = roll(heal_dice.get("type", "d0"), r)
                if "atk" in heal_scaling:
                    heal_on_hit = base_damage(
                        modify_stat(actor, "atk", actor.stats.get("atk", 0)),
                        heal_scaling["atk"],
                        roll_power,
                    )
                elif "int" in heal_scaling:
                    heal_on_hit = base_damage(
                        modify_stat(actor, "int", actor.stats.get("int", 0)),
                        heal_scaling["int"],
                        roll_power,
                    )
            if reduced > 0 and heal_on_hit > 0:
                before_hp = actor.res.hp
                actor.res.hp = min(actor.res.hp + heal_on_hit, actor.res.hp_max)
                total_healing += actor.res.hp - before_hp
                log_parts.append(f"{prefix}Heals {heal_on_hit} HP.")

            total_damage += reduced

        # Apply non-strike-again on-hit passive effects once per ability execution.
        if ability_hit_landed:
            bonus_damage, passive_logs, bonus_healing = trigger_on_hit_passives(
                actor,
                target,
                on_hit_base_damage,
                damage_type,
                r,
                ability=ability,
                include_strike_again=False,
            )
            if bonus_damage > 0:
                total_damage += bonus_damage
            if bonus_healing > 0:
                total_healing += bonus_healing
            if passive_logs:
                extra_logs.extend(passive_logs)

        # Strike-again passives can proc per successful damaging strike.
        for strike_damage in per_hit_damage_values:
            strike_bonus_damage, strike_logs, _ = trigger_on_hit_passives(
                actor,
                target,
                strike_damage,
                damage_type,
                r,
                ability=ability,
                include_strike_again=True,
                only_strike_again=True,
            )
            if strike_bonus_damage > 0:
                total_damage += strike_bonus_damage
            if strike_logs:
                extra_logs.extend(strike_logs)

        resource_gain = ability.get("resource_gain", {})
        if total_damage > 0 and resource_gain:
            for resource, gain in resource_gain.items():
                if gain == "damage":
                    gain_value = total_damage
                elif gain == "damage_x3":
                    gain_value = total_damage * 3
                else:
                    gain_value = int(gain)
                if gain_value > 0 and hasattr(actor.res, resource):
                    current = getattr(actor.res, resource)
                    cap = getattr(actor.res, f"{resource}_max", current)
                    setattr(actor.res, resource, max(0, min(current + gain_value, cap)))

        if has_effect(actor, "mindgames") and total_damage > 0:
            before_hp = target.res.hp
            target.res.hp = min(target.res.hp + total_damage, target.res.hp_max)
            healed = target.res.hp - before_hp
            log_parts.append(f"Mindgames flips damage into {healed} healing for the target.")
            total_damage = 0

        if ability.get("consume_effect"):
            remove_effect(actor, ability["consume_effect"])
        if ability_id == "final_verdict" and has_effect(actor, "paladin_final_verdict_empowered"):
            remove_effect(actor, "paladin_final_verdict_empowered")
        if ability_id == "mind_blast" and has_effect(actor, "mind_blast_empowered"):
            remove_effect(actor, "mind_blast_empowered")

        if consume_empower and empower_multiplier != 1.0:
            remove_effect(actor, "crusader_empower")

        set_cooldown(actor, ability_id, ability)
        if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
            remove_stealth(actor)

        damage_instances = [value for value in per_hit_damage_values if value > 0]
        if total_damage > sum(damage_instances):
            damage_instances.append(total_damage - sum(damage_instances))

        return {
            "damage": total_damage,
            "damage_instances": damage_instances,
            "healing": total_healing,
            "log": " ".join(log_parts),
            "extra_logs": extra_logs,
            "ability_id": ability_id,
        }

    def build_immediate_resolution(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"damage": 0, "log": f"{actor_sid[:5]} fumbles (unknown ability).", "resolved": True}

        if stunned_at_start.get(actor_sid, False) and not can_cast_while_cc(ability):
            reason = get_cant_act_reason(actor)
            if reason:
                reason_text = f"is {reason} and cannot act"
            else:
                reason_text = "cannot act"
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tries to use {ability['name']} but {reason_text}.",
                "resolved": True,
            }

        allowed_classes = ability.get("classes")
        if allowed_classes and actor.build.class_id not in allowed_classes:
            return {"damage": 0, "log": f"{actor_sid[:5]} cannot use {ability['name']}.", "resolved": True}

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")

        if is_on_cooldown(actor, ability_id, ability):
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tried to use {ability['name']} but it is on cooldown.",
                "resolved": True,
            }

        required_form = ability.get("requires_form")
        if required_form and current_form_id(actor) != required_form:
            return {
                "damage": 0,
                "log": ability.get("requires_form_log", "Must be in the correct form."),
                "resolved": True,
            }

        required_effect = ability.get("requires_effect")
        if required_effect and not has_effect(actor, required_effect):
            return {
                "damage": 0,
                "log": f"{ability['name']} requires {effect_name(required_effect)}.",
                "resolved": True,
            }

        required_weapon = ability.get("requires_weapon")
        if required_weapon and weapon_id != required_weapon:
            return {
                "damage": 0,
                "log": ability.get("requires_weapon_log", "The required weapon is not equipped."),
                "resolved": True,
            }

        target_hp_threshold = ability.get("requires_target_hp_below")
        if target_hp_threshold is not None:
            if target.res.hp / max(1, target.res.hp_max) >= float(target_hp_threshold):
                return {"damage": 0, "log": f"{ability['name']} can only be used as an execute.", "resolved": True}

        if ability.get("requires_circle") and not has_circle(actor):
            return {"damage": 0, "log": "Demonic Circle is required.", "resolved": True}

        if ability_id == "agony" and has_effect(target, "agony"):
            return {"damage": 0, "log": "Agony is not stackable.", "resolved": True}

        if ability_id == "summon_imp" and int((actor.minions or {}).get("imp", 0)) >= 3:
            return {"damage": 0, "log": "3 Imps Maximum", "resolved": True}

        ok, fail_reason = can_pay_costs(actor, ability.get("cost", {}))
        if not ok:
            if fail_reason == "not enough rage":
                return {"damage": 0, "log": "not enough rage", "resolved": True}
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tried {ability['name']} but lacked resources.",
                "resolved": True,
            }

        has_damage = any(
            value
            for value in (
                ability.get("dice"),
                ability.get("scaling"),
                ability.get("flat_damage"),
            )
        )
        has_target_effects = bool(ability.get("target_effects"))
        has_self_effects = bool(ability.get("self_effects"))
        is_defensive = bool(ability.get("effect"))
        immediate_effects_only = bool(ability.get("priority_control")) or is_defensive or (not has_damage and (has_self_effects or has_target_effects))

        return {
            "damage": 0,
            "log": "",
            "resolved": False,
            "ability": ability,
            "ability_id": ability_id,
            "immediate_only": immediate_effects_only,
        }

    contexts = {
        sids[0]: build_immediate_resolution(sids[0], sids[1], a1),
        sids[1]: build_immediate_resolution(sids[1], sids[0], a2),
    }

    def apply_pre_resolution_defensive(actor_sid: str, ctx: Dict[str, Any]) -> None:
        if ctx.get("resolved"):
            return
        ability = ctx.get("ability")
        if not ability or not ability.get("priority_defensive"):
            return
        actor = match.state[actor_sid]
        pre_applied: set[str] = set()
        for entry in ability.get("self_effects", []) or []:
            if entry.get("type") == "dispel":
                continue
            effect_id = entry.get("id")
            if not effect_id:
                continue
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            if effect_id in FORM_EFFECT_IDS:
                apply_form(actor, effect_id, overrides=overrides or None)
            else:
                apply_effect_by_id(actor, effect_id, overrides=overrides or None)
            pre_applied.add(effect_id)
        if pre_applied:
            ctx["pre_resolved_self_effects"] = pre_applied

    apply_pre_resolution_defensive(sids[0], contexts[sids[0]])
    apply_pre_resolution_defensive(sids[1], contexts[sids[1]])
    stealth_targeting = {sid: is_stealthed(match.state[sid]) for sid in sids}

    def immediate_action_can_stun(actor_sid: str, target_sid: str, ctx: Dict[str, Any]) -> bool:
        if ctx.get("resolved") or not ctx.get("immediate_only"):
            return False
        ability = ctx.get("ability") or {}
        target_effects = ability.get("target_effects") or []
        if not target_effects:
            return False
        is_aoe = "aoe" in (ability.get("tags") or [])
        target = match.state[target_sid]
        if should_miss_due_to_stealth(match.state[actor_sid], target, ability, stealth_targeting):
            return False
        if has_flag(target, "untargetable") and not is_aoe:
            return False
        for entry in target_effects:
            effect = build_effect(entry["id"], overrides=entry.get("overrides"))
            flags = effect.get("flags", {}) or {}
            if flags.get("stunned"):
                return True
        return False

    incoming_immediate_stun = {
        sids[0]: immediate_action_can_stun(sids[1], sids[0], contexts[sids[1]]),
        sids[1]: immediate_action_can_stun(sids[0], sids[1], contexts[sids[0]]),
    }

    def resolve_immediate_effects(actor_sid: str, target_sid: str, ctx: Dict[str, Any]) -> None:
        if ctx.get("resolved") or not ctx.get("immediate_only"):
            return
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability = ctx["ability"]
        ability_id = ctx["ability_id"]
        skip_self_effect_ids = ctx.get("pre_resolved_self_effects", set())
        is_aoe = "aoe" in (ability.get("tags") or [])

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")
        weapon_name = ITEMS.get(weapon_id, {}).get("name", "their bare hands")
        log_parts = [f"{actor_sid[:5]} uses {weapon_name} to cast {ability['name']}."]

        actor_stunned = is_stunned(actor) or incoming_immediate_stun.get(actor_sid, False)
        if actor_stunned and not can_cast_while_cc(ability):
            reason = get_cant_act_reason(actor)
            if reason:
                reason_text = f"is {reason} and cannot act"
            else:
                reason_text = "cannot act"
            ctx["damage"] = 0
            ctx["log"] = f"{actor_sid[:5]} tries to use {ability['name']} but {reason_text}."
            ctx["resolved"] = True
            return

        consume_costs(actor, ability.get("cost", {}))

        if ability.get("target_effects") and should_miss_due_to_stealth(actor, target, ability, stealth_targeting):
            log_parts.append("Target is stealthed — Miss!")
            set_cooldown(actor, ability_id, ability)
            ctx["damage"] = 0
            ctx["log"] = " ".join(log_parts)
            ctx["resolved"] = True
            if is_offensive_action(ability) and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return
        if ability_id == "mindgames" and is_immune_all(target):
            log_parts.append("Immune!")
            set_cooldown(actor, ability_id, ability)
            ctx["damage"] = 0
            ctx["log"] = " ".join(log_parts)
            ctx["resolved"] = True
            return

        if ability.get("target_effects") and has_flag(target, "untargetable"):
            log_parts.append(untargetable_miss_log(target))
            set_cooldown(actor, ability_id, ability)
            ctx["damage"] = 0
            ctx["log"] = " ".join(log_parts)
            ctx["resolved"] = True
            if is_offensive_action(ability) and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return

        if ability.get("effect"):
            effect = dict(ability["effect"])
            effect["duration"] = int(effect.get("duration", 1))
            actor.effects.append(effect)
            log_parts.append("Defensive stance raised.")
        else:
            apply_effect_entries(
                actor,
                target,
                ability,
                log_parts,
                skip_self_effect_ids=skip_self_effect_ids,
            )

        absorb_data = ability.get("absorb") or {}
        if absorb_data:
            absorb_scaling = absorb_data.get("scaling", {}) or {}
            absorb_dice = absorb_data.get("dice") or {}
            absorb_roll = 0
            if absorb_dice.get("type"):
                absorb_roll = roll(absorb_dice["type"], r)
            absorb_value = int(absorb_data.get("flat", 0) or 0)
            if "atk" in absorb_scaling:
                absorb_value += base_damage(
                    modify_stat(actor, "atk", actor.stats.get("atk", 0)),
                    absorb_scaling["atk"],
                    absorb_roll,
                )
            elif "int" in absorb_scaling:
                absorb_value += base_damage(
                    modify_stat(actor, "int", actor.stats.get("int", 0)),
                    absorb_scaling["int"],
                    absorb_roll,
                )
            absorb_value = max(0, int(absorb_value))
            if absorb_value > 0:
                add_absorb(
                    actor,
                    absorb_value,
                    source_name=absorb_data.get("source_name") or ability.get("name") or "Shield",
                )
            log_parts.append(f"{ability.get('name', 'Shield')} grants {absorb_value} absorb.")

        set_cooldown(actor, ability_id, ability)
        ctx["damage"] = 0
        ctx["log"] = " ".join(log_parts)
        ctx["resolved"] = True
        if is_offensive_action(ability) and stealth_start_at_turn_begin.get(actor_sid, False):
            remove_stealth(actor)

    resolve_immediate_effects(sids[0], sids[1], contexts[sids[0]])
    resolve_immediate_effects(sids[1], sids[0], contexts[sids[1]])
    stealth_targeting = {sid: is_stealthed(match.state[sid]) for sid in sids}

    def finalize_action(actor_sid: str, target_sid: str, action: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if ctx.get("resolved"):
            return ctx
        return resolve_action(actor_sid, target_sid, action)

    # Resolve both actions
    result1 = finalize_action(sids[0], sids[1], a1, contexts[sids[0]])
    result2 = finalize_action(sids[1], sids[0], a2, contexts[sids[1]])
    for sid, result in ((sids[0], result1), (sids[1], result2)):
        totals = match.combat_totals.setdefault(sid, {"damage": 0, "healing": 0})
        totals["damage"] += int(result.get("damage", 0) or 0)
        totals["healing"] += int(result.get("healing", 0) or 0)

    def absorb_suffix(absorbed: int, absorb_source_name: str) -> str:
        if absorbed <= 0:
            return ""
        return f" ({absorbed} absorbed by {absorb_source_name})"

    def format_damage_log(log_text: str, damage_breakdown: Dict[str, Any]) -> str:
        if not log_text:
            return ""
        instances = list(damage_breakdown.get("instances", []) or [])
        if not instances:
            absorbed = int(damage_breakdown.get("absorbed", 0) or 0)
            if absorbed > 0:
                source_name = damage_breakdown.get("absorb_source") or "Shield"
                return f"{log_text}{absorb_suffix(absorbed, source_name)}"
            return log_text
        updated = log_text
        for idx, instance in enumerate(instances):
            token = f"__DMG_{idx}__"
            token_with_damage = f"{token} damage"
            hp_damage = int(instance.get("hp_damage", 0) or 0)
            absorbed = int(instance.get("absorbed", 0) or 0)
            source_name = instance.get("absorb_source") or damage_breakdown.get("absorb_source") or "Shield"
            replacement = f"{hp_damage} damage{absorb_suffix(absorbed, source_name)}"
            if token_with_damage in updated:
                updated = updated.replace(token_with_damage, replacement, 1)
            else:
                updated = updated.replace(token, str(hp_damage), 1)
        return updated

    # Apply damage
    def apply_damage(
        target: PlayerState,
        incoming: int,
        target_sid: str,
        source_ability_name: str,
        damage_instances: list[int] | None = None,
    ) -> Dict[str, Any]:
        if incoming <= 0 or not target.res:
            return {"hp_damage": 0, "absorbed": 0, "absorb_source": "Shield", "instances": []}
        if is_immune_all(target):
            match.log.append(f"{target_sid[:5]} is immune and takes no damage.")
            return {"hp_damage": 0, "absorbed": 0, "absorb_source": "Shield", "instances": []}
        if has_flag(target, "cycloned"):
            match.log.append(f"{target_sid[:5]} is cycloned and takes no damage.")
            return {"hp_damage": 0, "absorbed": 0, "absorb_source": "Shield", "instances": []}

        absorb_source_name = (target.res.absorb_source if target.res else None) or "Shield"
        instance_values = [max(0, int(value or 0)) for value in (damage_instances or []) if int(value or 0) > 0]
        accounted = sum(instance_values)
        if incoming > accounted:
            instance_values.append(incoming - accounted)
        if not instance_values:
            instance_values = [incoming]

        instance_results: list[Dict[str, Any]] = []
        total_absorbed = 0
        total_remaining = 0
        for value in instance_values:
            source_name = (target.res.absorb_source if target.res else None) or absorb_source_name
            absorbed, remaining = consume_absorb(target, value)
            total_absorbed += absorbed
            total_remaining += remaining
            instance_results.append({"absorbed": absorbed, "hp_damage": remaining, "absorb_source": source_name})

        if total_remaining > 0:
            target.res.hp -= total_remaining
            was_stealthed = is_stealthed(target)
            break_stealth_on_damage(target, total_remaining)
            if was_stealthed and not is_stealthed(target):
                match.log.append(f"{target_sid[:5]} stealth broken by {source_ability_name}.")
            if current_form_id(target) == "bear_form":
                current = target.res.rage
                cap = target.res.rage_max
                target.res.rage = min(current + total_remaining, cap)
        return {
            "hp_damage": max(0, total_remaining),
            "absorbed": total_absorbed,
            "absorb_source": absorb_source_name,
            "instances": instance_results,
        }

    def trigger_shield_of_vengeance_explosion(owner_sid: str, enemy_sid: str) -> None:
        owner = match.state[owner_sid]
        enemy = match.state[enemy_sid]
        shield_fx = get_effect(owner, "shield_of_vengeance")
        if not shield_fx:
            return
        if owner.res.absorb > 0 and int(shield_fx.get("duration", 0) or 0) > 1:
            return
        absorbed_total = int(shield_fx.get("absorb_total", 0) or 0)
        remove_effect(owner, "shield_of_vengeance")
        if absorbed_total <= 0:
            return
        if is_immune_all(enemy):
            match.log.append(f"{enemy_sid[:5]} is immune to Shield of Vengeance explosion.")
            return
        enemy.res.hp -= absorbed_total
        match.log.append(f"Shield of Vengeance explodes for {absorbed_total} magic damage.")
        imp_count = int((enemy.minions or {}).get("imp", 0))
        if imp_count > 0:
            enemy.minions["imp"] = 0
            match.log.append(f"{enemy_sid[:5]}'s Imps are destroyed by Shield of Vengeance.")
        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
        totals["damage"] += absorbed_total

    source_name_1 = ABILITIES.get(result1.get("ability_id", ""), {}).get("name", "attack")
    source_name_2 = ABILITIES.get(result2.get("ability_id", ""), {}).get("name", "attack")
    dealt1_data = apply_damage(
        match.state[sids[1]],
        result1["damage"],
        sids[1],
        source_name_1,
        result1.get("damage_instances"),
    )
    dealt2_data = apply_damage(
        match.state[sids[0]],
        result2["damage"],
        sids[0],
        source_name_2,
        result2.get("damage_instances"),
    )
    dealt1 = int(dealt1_data.get("hp_damage", 0) or 0)
    dealt2 = int(dealt2_data.get("hp_damage", 0) or 0)

    match.log.append(format_damage_log(result1["log"], dealt1_data))
    match.log.extend(result1.get("extra_logs", []))
    match.log.append(format_damage_log(result2["log"], dealt2_data))
    match.log.extend(result2.get("extra_logs", []))

    for actor_sid, dealt, result in ((sids[0], dealt1, result1), (sids[1], dealt2, result2)):
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        if dealt > 0 and ability.get("heal_from_dealt_damage"):
            actor = match.state[actor_sid]
            if actor.res and actor.res.hp > 0:
                before_hp = actor.res.hp
                actor.res.hp = min(actor.res.hp + dealt, actor.res.hp_max)
                gained = actor.res.hp - before_hp
                if gained > 0:
                    match.log.append(f"{actor_sid[:5]} heals {gained} HP from {ability.get('name', 'their attack')}.")
                    totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
                    totals["healing"] += gained

    for actor_sid, target_sid, dealt, result in ((sids[0], sids[1], dealt1, result1), (sids[1], sids[0], dealt2, result2)):
        if result.get("ability_id") != "death":
            continue
        target = match.state[target_sid]
        actor = match.state[actor_sid]
        if target.res.hp > 0 and dealt > 0:
            backlash = int(dealt * 1.0)
            if backlash > 0:
                actor.res.hp = max(0, actor.res.hp - backlash)
                match.log.append(f"{actor_sid[:5]} suffers {backlash} backlash from Shadow Word: Death.")

    for actor_sid, dealt, result in ((sids[0], dealt1, result1), (sids[1], dealt2, result2)):
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        lifesteal = float(ability.get("heal_from_damage", 0) or 0)
        if dealt > 0 and lifesteal > 0:
            actor = match.state[actor_sid]
            if actor.res and actor.res.hp > 0:
                heal_value = int(dealt * lifesteal)
                before_hp = actor.res.hp
                actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
                gained = actor.res.hp - before_hp
                if gained > 0:
                    match.log.append(f"{actor_sid[:5]} drains {gained} life.")
                    totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
                    totals["healing"] += gained

        dot_data = ability.get("dot") or {}
        if dealt > 0 and dot_data.get("from_dealt_damage"):
            target_sid = sids[1] if actor_sid == sids[0] else sids[0]
            target = match.state[target_sid]
            dot_id = dot_data.get("id")
            duration = int(dot_data.get("duration", 1) or 1)
            school = dot_data.get("school", "magical")
            tick_damage = max(1, int(dealt // max(1, duration)))
            if dot_id and refresh_dot_effect(target, dot_id, duration=duration, tick_damage=tick_damage, source_sid=actor_sid):
                for effect in target.effects:
                    if effect.get("id") == dot_id:
                        effect["school"] = school
                        break
                match.log.append(f"{actor_sid[:5]} refreshes {effect_name(dot_id)} for {tick_damage} per turn.")
            elif dot_id:
                apply_effect_by_id(
                    target,
                    dot_id,
                    overrides={"duration": duration, "tick_damage": tick_damage, "source_sid": actor_sid, "school": school},
                )
                match.log.append(f"{actor_sid[:5]} applies {effect_name(dot_id)} for {tick_damage} per turn.")

    for source_sid, target_sid, dealt, result in ((sids[0], sids[1], dealt1, result1), (sids[1], sids[0], dealt2, result2)):
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        if "aoe" not in (ability.get("tags") or []) or dealt <= 0:
            continue
        target = match.state[target_sid]
        imp_count = int((target.minions or {}).get("imp", 0))
        if imp_count > 0:
            target.minions["imp"] = 0
            source_name = ability.get("name", "AoE damage")
            match.log.append(f"{target_sid[:5]}'s Imps are destroyed by {source_name}.")

    # End of turn processing for both players (DoTs, passives, duration ticks, regen)
    for sid in sids:
        ps = match.state[sid]
        opponent_sid = sids[1] if sid == sids[0] else sids[0]
        opponent = match.state[opponent_sid]
        end_summary = end_of_turn(ps, match.log, sid[:5])
        for source in end_summary.get("damage_sources", []):
            source_sid = source.get("source_sid")
            damage = int(source.get("damage", 0) or 0)
            if not source_sid or damage <= 0:
                continue
            totals = match.combat_totals.setdefault(source_sid, {"damage": 0, "healing": 0})
            totals["damage"] += damage
            lifesteal_pct = float(source.get("lifesteal_pct", 0) or 0)
            if lifesteal_pct > 0 and source_sid in match.state:
                healer = match.state[source_sid]
                heal_value = int(damage * lifesteal_pct)
                before_hp = healer.res.hp
                healer.res.hp = min(healer.res.hp + heal_value, healer.res.hp_max)
                gained = healer.res.hp - before_hp
                if gained > 0:
                    effect_id = source.get("effect_id")
                    source_name = effect_name(effect_id) if effect_id else "DoT"
                    match.log.append(f"{source_sid[:5]} heals {gained} HP from {source_name}.")
                    totals["healing"] += gained

        imp_count = int((ps.minions or {}).get("imp", 0))
        if imp_count > 0 and ps.res and ps.res.hp > 0 and opponent.res and opponent.res.hp > 0:
            for _ in range(imp_count):
                if is_immune_all(opponent):
                    match.log.append(f"{sid[:5]}'s Imp casts Firebolt. Target is immune!")
                    continue
                if should_miss_due_to_stealth(ps, opponent, {"requires_target": True}, stealth_targeting) or is_stealthed(opponent):
                    match.log.append(f"{sid[:5]}'s Imp casts Firebolt. Target is stealthed — Miss!")
                    continue
                if has_flag(opponent, "untargetable"):
                    match.log.append(f"{sid[:5]}'s Imp casts Firebolt. {untargetable_miss_log(opponent)}")
                    continue
                fire_roll = roll("d4", r)
                raw_fire = base_damage(modify_stat(ps, "int", ps.stats.get("int", 0)), 0.2, fire_roll)
                reduced = mitigate(raw_fire, modify_stat(opponent, "def", opponent.stats.get("def", 0)))
                resist = modify_stat(opponent, "magic_resist", opponent.stats.get("magic_resist", 0))
                reduced = max(0, reduced - resist)
                reduced = int(reduced * mitigation_multiplier(opponent))
                if is_damage_immune(opponent, "magic"):
                    reduced = 0
                absorb_source_name = (opponent.res.absorb_source if opponent.res else None) or "Shield"
                absorbed, remaining = consume_absorb(opponent, reduced)
                if remaining > 0:
                    opponent.res.hp -= remaining
                    if current_form_id(opponent) == "bear_form":
                        opponent.res.rage = min(opponent.res.rage + remaining, opponent.res.rage_max)
                    was_stealthed = is_stealthed(opponent)
                    break_stealth_on_damage(opponent, remaining)
                    if was_stealthed and not is_stealthed(opponent):
                        match.log.append(f"{opponent_sid[:5]} stealth broken by Imp Firebolt.")
                if absorbed > 0 or remaining > 0:
                    match.log.append(
                        f"{sid[:5]}'s Imp casts Firebolt for {remaining} damage"
                        f"{absorb_suffix(absorbed, absorb_source_name)}."
                    )
                if remaining > 0:
                    totals = match.combat_totals.setdefault(sid, {"damage": 0, "healing": 0})
                    totals["damage"] += remaining

        if has_effect(ps, "shadowfiend") and ps.res and ps.res.hp > 0 and opponent.res and opponent.res.hp > 0:
            fiend_roll = roll("d4", r)
            fiend_raw = base_damage(modify_stat(ps, "int", ps.stats.get("int", 0)), 0.6, fiend_roll)
            fiend_reduced = mitigate(fiend_raw, modify_stat(opponent, "def", opponent.stats.get("def", 0)))
            fiend_reduced = max(0, fiend_reduced - modify_stat(opponent, "physical_reduction", opponent.stats.get("physical_reduction", 0)))
            fiend_reduced = int(fiend_reduced * mitigation_multiplier(opponent))
            if is_damage_immune(opponent, "physical"):
                fiend_reduced = 0
            if fiend_reduced > 0:
                absorb_source_name = (opponent.res.absorb_source if opponent.res else None) or "Shield"
                absorbed, remaining = consume_absorb(opponent, fiend_reduced)
                if remaining > 0:
                    opponent.res.hp -= remaining
                    match.log.append(
                        f"Shadowfiend melee attacks {opponent_sid[:5]} for {remaining} damage"
                        f"{absorb_suffix(absorbed, absorb_source_name)}"
                    )
                    fiend_totals = match.combat_totals.setdefault(sid, {"damage": 0, "healing": 0})
                    fiend_totals["damage"] += remaining
                elif absorbed > 0:
                    match.log.append(
                        f"Shadowfiend melee attacks {opponent_sid[:5]} for 0 damage"
                        f"{absorb_suffix(absorbed, absorb_source_name)}"
                    )
                if absorbed > 0 or remaining > 0:
                    ps.res.mp = min(ps.res.mp + 13, ps.res.mp_max)
                    match.log.append(f"Shadowfiend restores 13 mana for {sid[:5]}.")

        totals = match.combat_totals.setdefault(sid, {"damage": 0, "healing": 0})
        totals["healing"] += int(end_summary.get("healing_done", 0) or 0)

    trigger_shield_of_vengeance_explosion(sids[0], sids[1])
    trigger_shield_of_vengeance_explosion(sids[1], sids[0])

    for sid in sids:
        ps = match.state[sid]
        ps.effects = tick_durations(ps.effects)
        tick_cooldowns(ps)

    # Check for winners
    p1_alive = match.state[sids[0]].res.hp > 0
    p2_alive = match.state[sids[1]].res.hp > 0
    if not p1_alive or not p2_alive:
        match.phase = "ended"
        match.log.append(
            "Post-Combat Summary|FD:{friendly_damage}|FH:{friendly_healing}|"
            "ED:{enemy_damage}|EH:{enemy_healing}"
        )
        if p1_alive and not p2_alive:
            match.winner = sids[0]
            match.log.append(f"{sids[0][:5]} wins the duel.")
        elif p2_alive and not p1_alive:
            match.winner = sids[1]
            match.log.append(f"{sids[1][:5]} wins the duel.")
        else:
            match.winner = None
            match.log.append("Double KO. No winner.")

    execute_ability = ABILITIES.get("execute", {})
    execute_threshold = execute_ability.get("requires_target_hp_below")
    if execute_threshold is not None and match.phase != "ended":
        for sid in sids:
            ps = match.state[sid]
            opponent_sid = sids[1] if sid == sids[0] else sids[0]
            opponent = match.state[opponent_sid]
            if ps.build.class_id != "warrior":
                continue
            if is_on_cooldown(ps, "execute", execute_ability):
                continue
            if is_stunned(ps):
                continue
            if opponent.res.hp / max(1, opponent.res.hp_max) >= float(execute_threshold):
                continue
            ok, _ = can_pay_costs(ps, execute_ability.get("cost", {}))
            if ok:
                match.log.append(f"{sid[:5]} Can Use Execute!")

    death_ability = ABILITIES.get("death", {})
    death_threshold = 0.2
    if death_ability and match.phase != "ended":
        for sid in sids:
            ps = match.state[sid]
            opponent_sid = sids[1] if sid == sids[0] else sids[0]
            opponent = match.state[opponent_sid]
            if ps.build.class_id != "priest":
                continue
            if is_on_cooldown(ps, "death", death_ability):
                continue
            if is_stunned(ps):
                continue
            if opponent.res.hp / max(1, opponent.res.hp_max) >= death_threshold:
                continue
            ok, _ = can_pay_costs(ps, death_ability.get("cost", {}))
            if ok:
                match.log.append(f"{sid[:5]} Shadow Word: Death Damage Doubled!")

    match.submitted.clear()
    match.turn += 1
