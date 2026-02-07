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
    break_stealth_on_damage,
    has_effect,
    remove_effect,
    remove_stealth,
    modify_stat,
    is_stunned,
    is_stealthed,
    is_damage_immune,
    has_flag,
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
        )
        
        ps = PlayerState(sid=sid, build=build, res=res, stats=stats)

        if build.class_id == "rogue":
            apply_effect_by_id(ps, "stealth")
        
        # Store item references for passive effects
        for item in equipped_items:
            if item.get("passive"):
                ps.effects.append({
                    "type": "item_passive",
                    "source_item": item["name"],
                    "passive": item["passive"],
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

    def consume_costs(ps: PlayerState, costs: Dict[str, int]) -> None:
        res = ps.res
        for key, value in costs.items():
            setattr(res, key, getattr(res, key) - value)

    def set_cooldown(ps: PlayerState, ability_id: str, ability: Dict[str, Any]) -> None:
        cooldown = int(ability.get("cooldown", 0) or 0)
        if cooldown > 0:
            slots = cooldown_slots(ps, ability_id)
            slots.append(cooldown)
            ps.cooldowns[ability_id] = slots

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

    def apply_effect_entries(actor: PlayerState, target: PlayerState, ability: Dict[str, Any], log_parts: list) -> None:
        for entry in ability.get("self_effects", []) or []:
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            apply_effect_by_id(actor, entry["id"], overrides=overrides or None)
            if entry.get("log"):
                log_parts.append(entry["log"])
        for entry in ability.get("target_effects", []) or []:
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            apply_effect_by_id(target, entry["id"], overrides=overrides or None)
            if entry.get("log"):
                log_parts.append(entry["log"])

    def is_offensive_action(ability: Dict[str, Any]) -> bool:
        if "pass" in (ability.get("tags") or []):
            return False
        has_damage = any(value for value in (ability.get("dice"), ability.get("scaling"), ability.get("flat_damage")))
        has_target_effects = bool(ability.get("target_effects"))
        return has_damage or has_target_effects

    def resolve_action(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"damage": 0, "log": f"{actor_sid[:5]} fumbles (unknown ability)."}

        allowed_classes = ability.get("classes")
        if allowed_classes and actor.build.class_id not in allowed_classes:
            return {"damage": 0, "log": f"{actor_sid[:5]} cannot use {ability['name']}."}

        if is_on_cooldown(actor, ability_id, ability):
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tried to use {ability['name']} but it is on cooldown.",
            }

        required_effect = ability.get("requires_effect")
        if required_effect and not has_effect(actor, required_effect):
            return {"damage": 0, "log": f"{ability['name']} requires {effect_name(required_effect)}."}

        target_hp_threshold = ability.get("requires_target_hp_below")
        if target_hp_threshold is not None:
            if target.res.hp / max(1, target.res.hp_max) >= float(target_hp_threshold):
                return {"damage": 0, "log": f"{ability['name']} can only be used as an execute."}

        ok, fail_reason = can_pay_costs(actor, ability.get("cost", {}))
        if not ok:
            if fail_reason == "not enough rage":
                return {"damage": 0, "log": "not enough rage"}
            return {"damage": 0, "log": f"{actor_sid[:5]} tried {ability['name']} but lacked resources."}

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")
        weapon_name = ITEMS.get(weapon_id, {}).get("name", "their bare hands")

        if is_stunned(actor) and not ability.get("allow_while_stunned"):
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tries to use {ability['name']} but is stunned and cannot act.",
            }

        log_parts = [f"{actor_sid[:5]} uses {weapon_name} to cast {ability['name']}."]

        has_target_effects = bool(ability.get("target_effects"))
        has_self_effects = bool(ability.get("self_effects"))
        if "pass" in (ability.get("tags") or []):
            set_cooldown(actor, ability_id, ability)
            log_parts.append("Passes the turn.")
            return {"damage": 0, "log": " ".join(log_parts)}

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
            if stealth_targeting.get(target_sid, False):
                log_parts.append("Target is stealthed — no valid target.")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "log": " ".join(log_parts)}
            if has_flag(target, "untargetable"):
                log_parts.append("Target blinks away — Miss.")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "log": " ".join(log_parts)}
            if has_flag(target, "evade_all"):
                log_parts.append("Evaded!")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "log": " ".join(log_parts)}

        if has_self_effects or has_target_effects:
            apply_effect_entries(actor, target, ability, log_parts)

        if not has_damage:
            set_cooldown(actor, ability_id, ability)
            if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return {"damage": 0, "log": " ".join(log_parts)}

        # Calculate base damage using appropriate stat
        damage_type = ability.get("damage_type", "physical")
        hits = int(ability.get("hits", 1) or 1)
        total_damage = 0
        miss_chance = float(ITEMS.get(weapon_id, {}).get("miss_chance", 0) or 0) if weapon_id else 0.0
        accuracy = hit_chance(
            modify_stat(actor, "acc", actor.stats.get("acc", 90)),
            modify_stat(target, "eva", target.stats.get("eva", 0)),
        )

        extra_logs: list[str] = []
        for hit_index in range(1, hits + 1):
            prefix = f"Hit {hit_index}: " if hits > 1 else ""
            roll_power = 0
            if dice_data:
                roll_power = roll(dice_data["type"], r)
                log_parts.append(f"{prefix}Roll {dice_data['type']} = {roll_power}.")

            if miss_chance > 0 and r.random() <= miss_chance:
                log_parts.append(f"{prefix}Misfire!")
                continue

            if r.randint(1, 100) > accuracy:
                log_parts.append(f"{prefix}Miss!")
                continue

            raw = 0
            if flat_damage is not None:
                raw = int(flat_damage)
            elif "atk" in scaling:
                raw = base_damage(modify_stat(actor, "atk", actor.stats.get("atk", 0)), scaling["atk"], roll_power)
            elif "int" in scaling:
                raw = base_damage(modify_stat(actor, "int", actor.stats.get("int", 0)), scaling["int"], roll_power)

            # Apply critical hit
            if raw > 0 and r.randint(1, 100) <= modify_stat(actor, "crit", actor.stats.get("crit", 0)):
                raw = int(raw * 1.5)
                log_parts.append(f"{prefix}Critical hit!")

            # Apply defense mitigation
            reduced = mitigate(raw, modify_stat(target, "def", target.stats.get("def", 0)))

            # Apply damage type specific resistance
            if damage_type == "physical":
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
                log_parts.append(f"{prefix}Deals {reduced} damage.")

            if reduced > 0:
                for effect in ability.get("on_hit_effects", []):
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

            # Apply on-hit passive effects (weapons/trinkets etc.)
            if reduced > 0:
                bonus_damage, passive_logs = trigger_on_hit_passives(
                    actor,
                    target,
                    reduced,
                    damage_type,
                    r,
                )
                if bonus_damage > 0:
                    reduced += bonus_damage
                if passive_logs:
                    extra_logs.extend(passive_logs)

            heal_on_hit = int(ability.get("heal_on_hit", 0) or 0)
            if reduced > 0 and heal_on_hit > 0:
                actor.res.hp = min(actor.res.hp + heal_on_hit, actor.res.hp_max)
                log_parts.append(f"{prefix}Heals {heal_on_hit} HP.")

            total_damage += reduced

        resource_gain = ability.get("resource_gain", {})
        if total_damage > 0 and resource_gain:
            for resource, gain in resource_gain.items():
                if gain == "damage":
                    gain_value = total_damage
                else:
                    gain_value = int(gain)
                if gain_value > 0 and hasattr(actor.res, resource):
                    current = getattr(actor.res, resource)
                    cap = getattr(actor.res, f"{resource}_max", current)
                    setattr(actor.res, resource, min(current + gain_value, cap))

        if ability.get("consume_effect"):
            remove_effect(actor, ability["consume_effect"])

        set_cooldown(actor, ability_id, ability)
        if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
            remove_stealth(actor)

        return {
            "damage": total_damage,
            "log": " ".join(log_parts),
            "extra_logs": extra_logs,
        }

    def build_immediate_resolution(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"damage": 0, "log": f"{actor_sid[:5]} fumbles (unknown ability).", "resolved": True}

        if stunned_at_start.get(actor_sid, False) and not ability.get("allow_while_stunned"):
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tries to use {ability['name']} but is stunned and cannot act.",
                "resolved": True,
            }

        allowed_classes = ability.get("classes")
        if allowed_classes and actor.build.class_id not in allowed_classes:
            return {"damage": 0, "log": f"{actor_sid[:5]} cannot use {ability['name']}.", "resolved": True}

        if is_on_cooldown(actor, ability_id, ability):
            return {
                "damage": 0,
                "log": f"{actor_sid[:5]} tried to use {ability['name']} but it is on cooldown.",
                "resolved": True,
            }

        required_effect = ability.get("requires_effect")
        if required_effect and not has_effect(actor, required_effect):
            return {
                "damage": 0,
                "log": f"{ability['name']} requires {effect_name(required_effect)}.",
                "resolved": True,
            }

        target_hp_threshold = ability.get("requires_target_hp_below")
        if target_hp_threshold is not None:
            if target.res.hp / max(1, target.res.hp_max) >= float(target_hp_threshold):
                return {"damage": 0, "log": f"{ability['name']} can only be used as an execute.", "resolved": True}

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
        immediate_effects_only = is_defensive or (not has_damage and (has_self_effects or has_target_effects))

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

    def resolve_immediate_effects(actor_sid: str, target_sid: str, ctx: Dict[str, Any]) -> None:
        if ctx.get("resolved") or not ctx.get("immediate_only"):
            return
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability = ctx["ability"]
        ability_id = ctx["ability_id"]

        consume_costs(actor, ability.get("cost", {}))

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")
        weapon_name = ITEMS.get(weapon_id, {}).get("name", "their bare hands")
        log_parts = [f"{actor_sid[:5]} uses {weapon_name} to cast {ability['name']}."]

        if ability.get("target_effects") and stealth_targeting.get(target_sid, False):
            log_parts.append("Target is stealthed — no valid target.")
            set_cooldown(actor, ability_id, ability)
            ctx["damage"] = 0
            ctx["log"] = " ".join(log_parts)
            ctx["resolved"] = True
            if is_offensive_action(ability) and stealth_start_at_turn_begin.get(actor_sid, False):
                remove_stealth(actor)
            return
        if ability.get("target_effects") and has_flag(target, "untargetable"):
            log_parts.append("Target blinks away — Miss.")
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
            apply_effect_entries(actor, target, ability, log_parts)

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
    match.log.append(result1["log"])
    match.log.extend(result1.get("extra_logs", []))
    match.log.append(result2["log"])
    match.log.extend(result2.get("extra_logs", []))

    # Apply damage
    match.state[sids[1]].res.hp -= result1["damage"]
    match.state[sids[0]].res.hp -= result2["damage"]
    if result1["damage"] > 0:
        break_stealth_on_damage(match.state[sids[1]], result1["damage"])
    if result2["damage"] > 0:
        break_stealth_on_damage(match.state[sids[0]], result2["damage"])

    # End of turn processing for both players (DoTs, passives, duration ticks, regen)
    for sid in sids:
        ps = match.state[sid]
        end_of_turn(ps, match.log, sid[:5])
        tick_cooldowns(ps)

    # Check for winners
    p1_alive = match.state[sids[0]].res.hp > 0
    p2_alive = match.state[sids[1]].res.hp > 0
    if not p1_alive or not p2_alive:
        match.phase = "ended"
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

    match.submitted.clear()
    match.turn += 1
