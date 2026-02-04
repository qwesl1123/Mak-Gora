# games/duel/engine/resolver.py
from typing import Dict, Any
from .models import MatchState, PlayerState, PlayerBuild, Resources
from .dice import rng_for, roll
from .rules import base_damage, mitigate, hit_chance, clamp
from ..content.abilities import ABILITIES
from ..content.classes import CLASSES
from ..content.items import ITEMS
from ..content.balance import DEFAULTS, CAPS

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

    def consume_costs(ps: PlayerState, costs: Dict[str, int]) -> bool:
        res = ps.res
        for key, value in costs.items():
            current = getattr(res, key)
            if current < value:
                return False
        for key, value in costs.items():
            setattr(res, key, getattr(res, key) - value)
        return True

    def mitigation_multiplier(target: PlayerState) -> float:
        total = 0.0
        for effect in target.effects:
            if effect.get("type") == "mitigation":
                total += float(effect.get("value", 0))
        total = max(0.0, min(total, 0.8))
        return 1.0 - total

    def resolve_action(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"damage": 0, "log": f"{actor_sid[:5]} fumbles (unknown ability)."}

        if not consume_costs(actor, ability.get("cost", {})):
            return {"damage": 0, "log": f"{actor_sid[:5]} tried {ability['name']} but lacked resources."}

        log_parts = [f"{actor_sid[:5]} uses {ability['name']}."]
        
        # Handle defensive abilities (non-damaging)
        if ability.get("effect"):
            effect = dict(ability["effect"])
            effect["duration"] = int(effect.get("duration", 1))
            actor.effects.append(effect)
            log_parts.append("Defensive stance raised.")
            return {"damage": 0, "log": " ".join(log_parts)}

        # Roll dice for power
        roll_power = 0
        dice_data = ability.get("dice")
        if dice_data:
            roll_power = roll(dice_data["type"], r)
            log_parts.append(f"Roll {dice_data['type']} = {roll_power}.")

        # Check accuracy
        accuracy = hit_chance(actor.stats.get("acc", 90), target.stats.get("eva", 0))
        if r.randint(1, 100) > accuracy:
            log_parts.append("Miss!")
            return {"damage": 0, "log": " ".join(log_parts)}

        # Calculate base damage using appropriate stat
        damage_type = ability.get("damage_type", "physical")
        scaling = ability.get("scaling", {})
        
        raw = 0
        if "atk" in scaling:
            raw = base_damage(actor.stats.get("atk", 0), scaling["atk"], roll_power)
        elif "int" in scaling:
            raw = base_damage(actor.stats.get("int", 0), scaling["int"], roll_power)
        
        # Apply critical hit
        if r.randint(1, 100) <= actor.stats.get("crit", 0):
            raw = int(raw * 1.5)
            log_parts.append("Critical hit!")

        # Apply defense mitigation
        reduced = mitigate(raw, target.stats.get("def", 0))
        
        # Apply damage type specific resistance
        if damage_type == "physical":
            resist = target.stats.get("physical_reduction", 0)
            reduced = max(0, reduced - resist)
        elif damage_type == "magic":
            resist = target.stats.get("magic_resist", 0)
            reduced = max(0, reduced - resist)
        
        # Apply defensive buff mitigation
        reduced = int(reduced * mitigation_multiplier(target))
        
        log_parts.append(f"Deals {reduced} damage.")
        
        # Apply on-hit passive effects (like burn from Wand of Fire)
        if reduced > 0:  # Only if hit landed
            for effect in actor.effects:
                if effect.get("type") == "item_passive":
                    passive = effect.get("passive", {})
                    if passive.get("trigger") == "on_hit" and passive.get("type") == "burn":
                        # Apply burn to target
                        burn_value = passive.get("value", 0)
                        target.effects.append({
                            "type": "burn",
                            "value": burn_value,
                            "duration": 999,  # Permanent until cleansed
                            "source": effect.get("source_item", "Unknown"),
                        })
                        log_parts.append(f"Burns target for {burn_value} damage/turn!")
        
        return {"damage": reduced, "log": " ".join(log_parts)}

    # Resolve both actions
    result1 = resolve_action(sids[0], sids[1], a1)
    result2 = resolve_action(sids[1], sids[0], a2)
    match.log.append(result1["log"])
    match.log.append(result2["log"])

    # Apply damage
    match.state[sids[1]].res.hp -= result1["damage"]
    match.state[sids[0]].res.hp -= result2["damage"]

    # End of turn processing for both players
    for sid in sids:
        ps = match.state[sid]
        
        # Apply DoT effects (burns, bleeds, etc.)
        for effect in ps.effects:
            if effect.get("type") == "burn":
                burn_dmg = effect.get("value", 0)
                ps.res.hp -= burn_dmg
                match.log.append(f"{sid[:5]} burns for {burn_dmg} damage.")
        
        # Apply healing passives (end of turn)
        for effect in ps.effects:
            if effect.get("type") == "item_passive":
                passive = effect.get("passive", {})
                if passive.get("trigger") == "end_of_turn" and passive.get("type") == "heal_self":
                    heal_value = passive.get("value", 0)
                    ps.res.hp = min(ps.res.hp + heal_value, ps.res.hp_max)
                    match.log.append(f"{sid[:5]} heals {heal_value} HP from {effect.get('source_item', 'item')}.")
        
        # Tick down effect durations (but keep permanent passives)
        ps.effects = [
            {**effect, "duration": effect.get("duration", 0) - 1}
            for effect in ps.effects
            if effect.get("duration", 0) - 1 > 0 or effect.get("type") == "item_passive" or effect.get("type") == "burn"
        ]
        
        # Regenerate resources
        if ps.res.hp > 0:
            ps.res.mp = min(ps.res.mp + DEFAULTS["mp_regen_per_turn"], ps.res.mp_max)
            ps.res.energy = min(ps.res.energy + DEFAULTS["energy_regen_per_turn"], ps.res.energy_max)

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
