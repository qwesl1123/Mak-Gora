from typing import Any, Dict

from .dice import roll
from .rules import base_damage, mitigate, hit_chance
from .effects import mitigation_multiplier, modify_stat, has_flag, is_immune_all, is_damage_immune


def run_pet_phase(
    match,
    rng,
    apply_damage,
    absorb_suffix,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
):
    sids = list(match.players)
    for owner_sid in sids:
        owner = match.state[owner_sid]
        enemy_sid = sids[1] if owner_sid == sids[0] else sids[0]
        enemy = match.state[enemy_sid]
        if not owner.res or owner.res.hp <= 0 or not enemy.res or enemy.res.hp <= 0:
            continue

        pet_ids = sorted((owner.pets or {}).keys())
        for pet_id in pet_ids:
            pet = owner.pets.get(pet_id)
            if not pet or pet.hp <= 0:
                continue

            if pet.template_id == "imp":
                if is_immune_all(enemy):
                    match.log.append(f"{owner_sid[:5]}'s {pet.name} casts Firebolt. Target is immune!")
                    continue
                if should_miss_due_to_stealth(owner, enemy, {"requires_target": True}, stealth_targeting):
                    match.log.append(f"{owner_sid[:5]}'s {pet.name} casts Firebolt. Target is stealthed — Miss!")
                    continue
                if has_flag(enemy, "untargetable"):
                    match.log.append(f"{owner_sid[:5]}'s {pet.name} casts Firebolt. {untargetable_miss_log(enemy)}")
                    continue

                fire_roll = roll("d4", rng)
                raw_fire = base_damage(modify_stat(owner, "int", owner.stats.get("int", 0)), 0.2, fire_roll)
                reduced = mitigate(raw_fire, modify_stat(enemy, "def", enemy.stats.get("def", 0)))
                resist = modify_stat(enemy, "magic_resist", enemy.stats.get("magic_resist", 0))
                reduced = max(0, reduced - resist)
                reduced = int(reduced * mitigation_multiplier(enemy))
                if is_damage_immune(enemy, "magic"):
                    reduced = 0
                dealt_data = apply_damage(owner, enemy, reduced, enemy_sid, "Imp Firebolt", school="magical")
                remaining = int(dealt_data.get("hp_damage", 0) or 0)
                absorbed = int(dealt_data.get("absorbed", 0) or 0)
                breakdown = dealt_data.get("absorbed_breakdown", [])
                if absorbed > 0 or remaining > 0:
                    total_incoming = remaining + absorbed
                    line = f"{owner_sid[:5]}'s {pet.name} casts Firebolt for {total_incoming} damage."
                    if absorbed > 0:
                        line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
                    match.log.append(line)
                if remaining > 0:
                    totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
                    totals["damage"] += remaining

            if pet.template_id == "shadowfiend":
                shadowfiend_ability = {
                    "requires_target": True,
                    "damage_type": "physical",
                    "tags": ["attack", "physical"],
                }
                misses = False
                if should_miss_due_to_stealth(owner, enemy, shadowfiend_ability, stealth_targeting):
                    match.log.append("Shadowfiend melee attacks. Target is stealthed — Miss!")
                    misses = True
                elif has_flag(enemy, "untargetable"):
                    match.log.append(f"Shadowfiend melee attacks. {untargetable_miss_log(enemy)}")
                    misses = True
                elif has_flag(enemy, "evade_all") and can_evasion_force_miss(shadowfiend_ability, True):
                    match.log.append("Shadowfiend melee attacks. Target evades the attack — Miss!")
                    misses = True
                else:
                    accuracy = hit_chance(
                        modify_stat(owner, "acc", owner.stats.get("acc", 0)),
                        modify_stat(enemy, "eva", enemy.stats.get("eva", 0)),
                    )
                    if rng.randint(1, 100) > accuracy:
                        match.log.append("Shadowfiend melee attacks. Miss!")
                        misses = True

                if not misses:
                    fiend_roll = roll("d4", rng)
                    raw = base_damage(modify_stat(owner, "int", owner.stats.get("int", 0)), 0.6, fiend_roll)
                    reduced = mitigate(raw, modify_stat(enemy, "def", enemy.stats.get("def", 0)))
                    reduced = max(0, reduced - modify_stat(enemy, "physical_reduction", enemy.stats.get("physical_reduction", 0)))
                    reduced = int(reduced * mitigation_multiplier(enemy))
                    if is_damage_immune(enemy, "physical"):
                        reduced = 0
                    dealt = apply_damage(owner, enemy, reduced, enemy_sid, "Shadowfiend melee", school="physical")
                    remaining = int(dealt.get("hp_damage", 0) or 0)
                    absorbed = int(dealt.get("absorbed", 0) or 0)
                    breakdown = dealt.get("absorbed_breakdown", [])
                    total_incoming = remaining + absorbed
                    if total_incoming > 0:
                        line = f"Shadowfiend melee attacks {enemy_sid[:5]} for {total_incoming} damage."
                        if absorbed > 0:
                            line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
                        match.log.append(line)
                    if remaining > 0:
                        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
                        totals["damage"] += remaining
                        owner.res.mp = min(owner.res.mp + 13, owner.res.mp_max)
                        match.log.append(f"Shadowfiend restores 13 mana for {owner_sid[:5]}.")


def cleanup_pets(match):
    for sid in list(match.players):
        ps = match.state[sid]
        for pet_id in sorted(list((ps.pets or {}).keys())):
            pet = ps.pets.get(pet_id)
            if not pet:
                continue
            if pet.duration is not None:
                pet.duration -= 1
            if pet.hp <= 0 or (pet.duration is not None and pet.duration <= 0):
                match.log.append(f"{pet.name} dies.")
                del ps.pets[pet_id]
