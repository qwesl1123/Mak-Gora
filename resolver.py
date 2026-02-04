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
    end_of_turn,
    apply_effect_by_id,
    has_effect,
    remove_effect,
    modify_stat,
)

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

    def consume_costs(ps: PlayerState, costs: Dict[str, int]) -> Tuple[bool, str]:
        res = ps.res
        for key, value in costs.items():
            current = getattr(res, key)
            if current < value:
                if key == "rage":
                    return False, "not enough rage"
                return False, f"not enough {key}"
        for key, value in costs.items():
            setattr(res, key, getattr(res, key) - value)
        return True, ""

    def set_cooldown(ps: PlayerState, ability_id: str, cooldown: int) -> None:
        if cooldown > 0:
            ps.cooldowns[ability_id] = cooldown

    def tick_cooldowns(ps: PlayerState) -> None:
        updated = {}
        for ability_id, remaining in ps.cooldowns.items():
            remaining = int(remaining) - 1
            if remaining > 0:
                updated[ability_id] = remaining
        ps.cooldowns = updated

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

        if actor.cooldowns.get(ability_id, 0) > 0:
            return {"damage": 0, "log": "ability is on cooldown"}

        required_effect = ability.get("requires_effect")
        if required_effect and not has_effect(actor, required_effect):
            return {"damage": 0, "log": f"{ability['name']} requires Hot Streak."}

        ok, fail_reason = consume_costs(actor, ability.get("cost", {}))
        if not ok:
            if fail_reason == "not enough rage":
                return {"damage": 0, "log": "not enough rage"}
            return {"damage": 0, "log": f"{actor_sid[:5]} tried {ability['name']} but lacked resources."}

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")
        weapon_name = ITEMS.get(weapon_id, {}).get("name", "Unarmed")

        log_parts = [f"{actor_sid[:5]} uses {weapon_name} to cast {ability['name']}."]
        
        # Handle defensive abilities (non-damaging)
        if ability.get("effect"):
            effect = dict(ability["effect"])
            effect["duration"] = int(effect.get("duration", 1))
            actor.effects.append(effect)
            log_parts.append("Defensive stance raised.")
            set_cooldown(actor, ability_id, int(ability.get("cooldown", 0) or 0))
            return {"damage": 0, "log": " ".join(log_parts)}

        # Roll dice for power
        roll_power = 0
        dice_data = ability.get("dice")
        if dice_data:
            roll_power = roll(dice_data["type"], r)
            log_parts.append(f"Roll {dice_data['type']} = {roll_power}.")

        # Check accuracy
        accuracy = hit_chance(
            modify_stat(actor, "acc", actor.stats.get("acc", 90)),
            modify_stat(target, "eva", target.stats.get("eva", 0)),
        )
        if r.randint(1, 100) > accuracy:
            log_parts.append("Miss!")
            set_cooldown(actor, ability_id, int(ability.get("cooldown", 0) or 0))
            if ability.get("consume_effect"):
                remove_effect(actor, ability["consume_effect"])
            return {"damage": 0, "log": " ".join(log_parts)}

        # Calculate base damage using appropriate stat
        damage_type = ability.get("damage_type", "physical")
        scaling = ability.get("scaling", {})
        
        raw = 0
        if "atk" in scaling:
            raw = base_damage(modify_stat(actor, "atk", actor.stats.get("atk", 0)), scaling["atk"], roll_power)
        elif "int" in scaling:
            raw = base_damage(modify_stat(actor, "int", actor.stats.get("int", 0)), scaling["int"], roll_power)
        
        # Apply critical hit
        if r.randint(1, 100) <= modify_stat(actor, "crit", actor.stats.get("crit", 0)):
            raw = int(raw * 1.5)
            log_parts.append("Critical hit!")

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
        
        log_parts.append(f"Deals {reduced} damage.")

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
        # NOTE: these log directly into match.log (separate from the action's one-line log).
        if reduced > 0:
            trigger_on_hit_passives(actor, target, match.log)

        resource_gain = ability.get("resource_gain", {})
        if reduced > 0 and resource_gain:
            for resource, gain in resource_gain.items():
                if gain == "damage":
                    gain_value = reduced
                else:
                    gain_value = int(gain)
                if gain_value > 0 and hasattr(actor.res, resource):
                    current = getattr(actor.res, resource)
                    cap = getattr(actor.res, f"{resource}_max", current)
                    setattr(actor.res, resource, min(current + gain_value, cap))

        if ability.get("consume_effect"):
            remove_effect(actor, ability["consume_effect"])

        set_cooldown(actor, ability_id, int(ability.get("cooldown", 0) or 0))

        return {"damage": reduced, "log": " ".join(log_parts)}

    # Resolve both actions
    result1 = resolve_action(sids[0], sids[1], a1)
    result2 = resolve_action(sids[1], sids[0], a2)
    match.log.append(result1["log"])
    match.log.append(result2["log"])

    # Apply damage
    match.state[sids[1]].res.hp -= result1["damage"]
    match.state[sids[0]].res.hp -= result2["damage"]

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

    match.submitted.clear()
    match.turn += 1
