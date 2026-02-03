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

        res_data = class_data["resources"]
        hp_max = res_data.get("hp", DEFAULTS["hp"])
        mp_max = res_data.get("mp", DEFAULTS["mp"])
        energy_max = res_data.get("energy", DEFAULTS["energy"])
        rage_max = res_data.get("rage_max", DEFAULTS["rage_max"])

        for item_id in build.items.values():
            if not item_id:
                continue
            item = ITEMS.get(item_id)
            if not item:
                continue
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
        match.state[sid] = PlayerState(sid=sid, build=build, res=res, stats=stats)

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
        if ability.get("effect"):
            effect = dict(ability["effect"])
            effect["duration"] = int(effect.get("duration", 1))
            actor.effects.append(effect)
            log_parts.append("Defensive stance raised.")
            return {"damage": 0, "log": " ".join(log_parts)}

        roll_power = 0
        dice_data = ability.get("dice")
        if dice_data:
            roll_power = roll(dice_data["type"], r)
            log_parts.append(f"Roll {dice_data['type']} = {roll_power}.")

        accuracy = hit_chance(actor.stats.get("acc", 90), target.stats.get("eva", 0))
        if r.randint(1, 100) > accuracy:
            log_parts.append("Miss!")
            return {"damage": 0, "log": " ".join(log_parts)}

        scaling = ability.get("scaling", {}).get("atk", 1.0)
        raw = base_damage(actor.stats.get("atk", 0), scaling, roll_power)
        if r.randint(1, 100) <= actor.stats.get("crit", 0):
            raw = int(raw * 1.5)
            log_parts.append("Critical hit!")

        reduced = mitigate(raw, target.stats.get("def", 0))
        reduced = int(reduced * mitigation_multiplier(target))
        log_parts.append(f"Deals {reduced} damage.")
        return {"damage": reduced, "log": " ".join(log_parts)}

    result1 = resolve_action(sids[0], sids[1], a1)
    result2 = resolve_action(sids[1], sids[0], a2)
    match.log.append(result1["log"])
    match.log.append(result2["log"])

    match.state[sids[1]].res.hp -= result1["damage"]
    match.state[sids[0]].res.hp -= result2["damage"]

    for sid in sids:
        ps = match.state[sid]
        ps.effects = [
            {**effect, "duration": effect.get("duration", 0) - 1}
            for effect in ps.effects
            if effect.get("duration", 0) - 1 > 0
        ]
        if ps.res.hp > 0:
            ps.res.mp = min(ps.res.mp + DEFAULTS["mp_regen_per_turn"], ps.res.mp_max)
            ps.res.energy = min(ps.res.energy + DEFAULTS["energy_regen_per_turn"], ps.res.energy_max)

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
