from typing import Callable

from ..content.pets import PETS
from .dice import roll
from .rules import base_damage, hit_chance
from .effects import (
    mitigate_damage,
    modify_stat,
    has_flag,
    get_cant_act_reason,
    is_immune_all,
    is_damage_immune,
    apply_effect_by_id,
)


def _damage_after_reduction(raw: int, enemy, school: str) -> int:
    normalized = "magical" if school == "magic" else school
    if normalized == "physical":
        reduced = mitigate_damage(raw, enemy, "physical")
        if is_damage_immune(enemy, "physical"):
            return 0
    else:
        reduced = mitigate_damage(raw, enemy, "magic")
        if is_damage_immune(enemy, "magic"):
            return 0
    return reduced


def _pet_log(owner_sid, pet, action_text: str, result_text: str | None = None) -> str:
    base = f"{owner_sid[:5]}'s {pet.name} {action_text}"
    if not result_text:
        return f"{base}."
    if result_text.startswith("for "):
        return f"{base} {result_text}"
    return f"{base}. {result_text}"


def _template_action_text(pet, *, action_key: str | None = None, special_id: str | None = None, fallback: str = "attacks") -> str:
    template = PETS.get(pet.template_id, {})
    if special_id:
        special_data = ((template.get("specials") or {}).get(special_id) or {})
        return str(special_data.get("action_text") or fallback)
    if action_key:
        action_data = (template.get(action_key) or {})
        return str(action_data.get("action_text") or fallback)
    return str(template.get("action_text") or fallback)


def _resolve_special_overrides(pet, overrides: dict | None) -> dict:
    resolved = dict(overrides or {})
    for key, value in list(resolved.items()):
        if value == "self.id":
            resolved[key] = pet.id
    return resolved


def _apply_effect_special(owner, pet, owner_sid, match, special_id: str) -> bool:
    template = PETS.get(pet.template_id, {})
    special_data = ((template.get("specials") or {}).get(special_id) or {})
    effect_id = special_data.get("effect_id")
    if not effect_id:
        return False
    overrides = _resolve_special_overrides(pet, special_data.get("effect_overrides"))
    apply_effect_by_id(owner, effect_id, overrides=overrides or None)
    match.log.append(_pet_log(owner_sid, pet, _template_action_text(pet, special_id=special_id, fallback="acts")))
    return True


def trigger_pre_action_special(owner, pet, owner_sid, match, rng, *, consume_action: bool = True) -> bool:
    template = PETS.get(pet.template_id, {})
    special_id = template.get("special_id")
    special_data = ((template.get("specials") or {}).get(special_id) or {})
    if special_data.get("timing") != "pre_action":
        return False

    forced_command = owner.pending_pet_command
    use_special = forced_command == "special"
    if not use_special:
        use_special = rng.random() <= float(template.get("special_chance", 0) or 0)
    if not use_special:
        return False

    if not _apply_effect_special(owner, pet, owner_sid, match, str(special_id)):
        return False

    if consume_action:
        pet.action_consumed = True
    if forced_command == "special":
        owner.pending_pet_command = None
    return True


def _run_imp_firebolt(
    owner,
    enemy,
    pet,
    owner_sid,
    enemy_sid,
    match,
    rng,
    apply_damage,
    absorb_suffix,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
):
    action_text = _template_action_text(pet, fallback="casts Firebolt")
    if is_immune_all(enemy):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Immune!"))
        return
    if should_miss_due_to_stealth(owner, enemy, {"requires_target": True}, stealth_targeting):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target is stealthed — Miss!"))
        return
    if has_flag(enemy, "untargetable"):
        match.log.append(_pet_log(owner_sid, pet, action_text, untargetable_miss_log(enemy)))
        return

    fire_roll = roll("d4", rng)
    raw_fire = base_damage(modify_stat(owner, "int", owner.stats.get("int", 0)), 0.2, fire_roll)
    reduced = _damage_after_reduction(raw_fire, enemy, "magical")
    dealt_data = apply_damage(
        owner,
        enemy,
        reduced,
        enemy_sid,
        "Imp Firebolt",
        school="magical",
        subschool=(PETS.get(pet.template_id, {}) or {}).get("subschool"),
    )
    remaining = int(dealt_data.get("hp_damage", 0) or 0)
    absorbed = int(dealt_data.get("absorbed", 0) or 0)
    breakdown = dealt_data.get("absorbed_breakdown", [])
    if absorbed > 0 or remaining > 0:
        total_incoming = remaining + absorbed
        line = _pet_log(owner_sid, pet, action_text, f"for {total_incoming} damage.")
        if absorbed > 0:
            line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
        match.log.append(line)
    if remaining > 0:
        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
        totals["damage"] += remaining



def _run_shadowfiend_melee_mana(
    owner,
    enemy,
    pet,
    owner_sid,
    enemy_sid,
    match,
    rng,
    apply_damage,
    absorb_suffix,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
):
    ability = {
        "requires_target": True,
        "damage_type": "physical",
        "tags": ["attack", "physical"],
    }
    action_text = _template_action_text(pet, fallback="melees the target")
    misses = False
    if should_miss_due_to_stealth(owner, enemy, ability, stealth_targeting):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target is stealthed — Miss!"))
        misses = True
    elif has_flag(enemy, "untargetable"):
        match.log.append(_pet_log(owner_sid, pet, action_text, untargetable_miss_log(enemy)))
        misses = True
    elif has_flag(enemy, "evade_all") and can_evasion_force_miss(ability, True):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target evades the attack — Miss!"))
        misses = True
    else:
        accuracy = hit_chance(
            modify_stat(owner, "acc", owner.stats.get("acc", 0)),
            modify_stat(enemy, "eva", enemy.stats.get("eva", 0)),
        )
        if rng.randint(1, 100) > accuracy:
            match.log.append(_pet_log(owner_sid, pet, action_text, "Miss!"))
            misses = True

    if misses:
        return

    fiend_roll = roll("d4", rng)
    raw = base_damage(modify_stat(owner, "int", owner.stats.get("int", 0)), 0.6, fiend_roll)
    reduced = _damage_after_reduction(raw, enemy, "physical")
    dealt = apply_damage(owner, enemy, reduced, enemy_sid, "Shadowfiend melee", school="physical")
    remaining = int(dealt.get("hp_damage", 0) or 0)
    absorbed = int(dealt.get("absorbed", 0) or 0)
    breakdown = dealt.get("absorbed_breakdown", [])
    total_incoming = remaining + absorbed
    if total_incoming > 0:
        line = _pet_log(owner_sid, pet, action_text, f"for {total_incoming} damage.")
        if absorbed > 0:
            line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
        match.log.append(line)
    if total_incoming > 0:
        owner.res.mp = min(owner.res.mp + 13, owner.res.mp_max)
        match.log.append(f"Shadowfiend restores 13 mana for {owner_sid[:5]}.")
    if remaining > 0:
        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
        totals["damage"] += remaining



def _run_hunter_basic_plus_special(
    owner,
    enemy,
    pet,
    owner_sid,
    enemy_sid,
    match,
    rng,
    apply_damage,
    absorb_suffix,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
):
    template = PETS.get(pet.template_id, {})
    forced_command = owner.pending_pet_command
    use_special = forced_command == "special"
    special_id = template.get("special_id")
    if not use_special and special_id:
        use_special = rng.random() <= float(template.get("special_chance", 0) or 0)

    if use_special and special_id:
        _run_pet_special(
            special_id,
            owner,
            enemy,
            pet,
            owner_sid,
            enemy_sid,
            match,
            rng,
            apply_damage,
            absorb_suffix,
            stealth_targeting,
            should_miss_due_to_stealth,
            untargetable_miss_log,
            can_evasion_force_miss,
        )
        return

    profile = template.get("basic_attack", {}) or {}
    _run_pet_attack(
        owner,
        enemy,
        pet,
        owner_sid,
        enemy_sid,
        match,
        rng,
        apply_damage,
        absorb_suffix,
        stealth_targeting,
        should_miss_due_to_stealth,
        untargetable_miss_log,
        can_evasion_force_miss,
        stat_key=profile.get("stat", "atk"),
        scaling=float(profile.get("scaling", 1.0) or 1.0),
        dice=profile.get("dice", "d4"),
        school=profile.get("school", "physical"),
        subschool=profile.get("subschool"),
        label=f"{pet.name} attacks",
        action_text=profile.get("action_text", "attacks"),
    )



def _run_pet_attack(
    owner,
    enemy,
    pet,
    owner_sid,
    enemy_sid,
    match,
    rng,
    apply_damage,
    absorb_suffix,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
    *,
    stat_key: str,
    scaling: float,
    dice: str,
    school: str,
    subschool: str | None,
    label: str,
    action_text: str = "attacks",
):
    ability = {
        "requires_target": True,
        "damage_type": school,
        "tags": ["attack", school],
    }
    if should_miss_due_to_stealth(owner, enemy, ability, stealth_targeting):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target is stealthed — Miss!"))
        return
    if has_flag(enemy, "untargetable"):
        match.log.append(_pet_log(owner_sid, pet, action_text, untargetable_miss_log(enemy)))
        return
    if has_flag(enemy, "evade_all") and can_evasion_force_miss(ability, True):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target evades the attack — Miss!"))
        return

    accuracy = hit_chance(
        modify_stat(owner, "acc", owner.stats.get("acc", 0)),
        modify_stat(enemy, "eva", enemy.stats.get("eva", 0)),
    )
    if rng.randint(1, 100) > accuracy:
        match.log.append(_pet_log(owner_sid, pet, action_text, "Miss!"))
        return

    stat_value = int((template := PETS.get(pet.template_id, {})).get(stat_key, 0) or 0)
    rolled = roll(dice, rng) if dice else 0
    raw = base_damage(stat_value, scaling, rolled)
    reduced = _damage_after_reduction(raw, enemy, school)
    dealt = apply_damage(owner, enemy, reduced, enemy_sid, label, school=school, subschool=subschool)
    remaining = int(dealt.get("hp_damage", 0) or 0)
    absorbed = int(dealt.get("absorbed", 0) or 0)
    breakdown = dealt.get("absorbed_breakdown", [])
    total_incoming = remaining + absorbed
    if total_incoming > 0:
        line = _pet_log(owner_sid, pet, action_text, f"for {total_incoming} damage.")
        if absorbed > 0:
            line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
        match.log.append(line)
    if remaining > 0:
        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
        totals["damage"] += remaining



def _run_pet_special(
    special_id,
    owner,
    enemy,
    pet,
    owner_sid,
    enemy_sid,
    match,
    rng,
    apply_damage,
    absorb_suffix,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
):
    if special_id == "bite":
        return _run_pet_attack(
            owner,
            enemy,
            pet,
            owner_sid,
            enemy_sid,
            match,
            rng,
            apply_damage,
            absorb_suffix,
            stealth_targeting,
            should_miss_due_to_stealth,
            untargetable_miss_log,
            can_evasion_force_miss,
            stat_key="atk",
            scaling=2.0,
            dice="d6",
            school="physical",
            subschool=None,
            label="Bite",
            action_text=_template_action_text(pet, special_id=special_id, fallback="bites the target"),
        )

    if special_id == "lightning_breath":
        ability = {
            "requires_target": True,
            "damage_type": "magic",
            "tags": ["attack", "magic"],
        }
        action_text = _template_action_text(pet, special_id=special_id, fallback="breathes lightning")
        if should_miss_due_to_stealth(owner, enemy, ability, stealth_targeting):
            match.log.append(_pet_log(owner_sid, pet, action_text, "Target is stealthed — Miss!"))
            return
        if has_flag(enemy, "untargetable"):
            match.log.append(_pet_log(owner_sid, pet, action_text, untargetable_miss_log(enemy)))
            return
        if is_immune_all(enemy):
            match.log.append(_pet_log(owner_sid, pet, action_text, "Immune!"))
            return
        raw = base_damage(int(PETS.get(pet.template_id, {}).get("int", 0) or 0), 1.5, roll("d6", rng))
        reduced = _damage_after_reduction(raw, enemy, "magical")
        dealt = apply_damage(
            owner,
            enemy,
            reduced,
            enemy_sid,
            "Lightning Breath",
            school="magical",
            subschool=((PETS.get(pet.template_id, {}).get("specials", {}) or {}).get("lightning_breath", {}) or {}).get("subschool"),
        )
        remaining = int(dealt.get("hp_damage", 0) or 0)
        absorbed = int(dealt.get("absorbed", 0) or 0)
        breakdown = dealt.get("absorbed_breakdown", [])
        total_incoming = remaining + absorbed
        if total_incoming > 0:
            line = _pet_log(owner_sid, pet, action_text, f"for {total_incoming} damage.")
            if absorbed > 0:
                line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
            match.log.append(line)
        if remaining > 0:
            heal_value = max(1, remaining // 2)
            before_pet = pet.hp
            pet.hp = min(pet.hp + heal_value, pet.hp_max)
            before_owner = owner.res.hp
            owner.res.hp = min(owner.res.hp + heal_value, owner.res.hp_max)
            match.log.append(
                f"{pet.name} restores {pet.hp - before_pet} HP to itself and {owner.res.hp - before_owner} HP to {owner_sid[:5]}."
            )
            totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
            totals["damage"] += remaining
            totals["healing"] += (pet.hp - before_pet) + (owner.res.hp - before_owner)
        return

    if special_id == "blocking_defence":
        if _apply_effect_special(owner, pet, owner_sid, match, special_id):
            return


BEHAVIOR_RUNNERS: dict[str, Callable] = {
    "imp_firebolt": _run_imp_firebolt,
    "shadowfiend_melee_mana": _run_shadowfiend_melee_mana,
    "hunter_basic_plus_special": _run_hunter_basic_plus_special,
}


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
            owner.pending_pet_command = None
            continue

        pet_ids = sorted((owner.pets or {}).keys())
        for pet_id in pet_ids:
            pet = owner.pets.get(pet_id)
            if not pet or pet.hp <= 0:
                continue
            if pet.action_consumed:
                pet.action_consumed = False
                continue
            reason = get_cant_act_reason(pet)
            if reason:
                match.log.append(f"{owner_sid[:5]}'s {pet.name} is {reason} and cannot act.")
                continue

            template = PETS.get(pet.template_id, {})
            behavior_id = template.get("behavior_id") or template.get("behavior")
            behavior_runner = BEHAVIOR_RUNNERS.get(str(behavior_id))
            if not behavior_runner:
                continue

            behavior_runner(
                owner,
                enemy,
                pet,
                owner_sid,
                enemy_sid,
                match,
                rng,
                apply_damage,
                absorb_suffix,
                stealth_targeting,
                should_miss_due_to_stealth,
                untargetable_miss_log,
                can_evasion_force_miss,
            )
        owner.pending_pet_command = None


def prepare_pet_pre_action_effects(match, rng):
    sids = list(match.players)
    for owner_sid in sids:
        owner = match.state[owner_sid]
        enemy_sid = sids[1] if owner_sid == sids[0] else sids[0]
        enemy = match.state[enemy_sid]
        if not owner.res or owner.res.hp <= 0 or not enemy.res or enemy.res.hp <= 0:
            owner.pending_pet_command = None
            continue

        for pet_id in sorted((owner.pets or {}).keys()):
            pet = owner.pets.get(pet_id)
            if not pet or pet.hp <= 0:
                continue

            trigger_pre_action_special(owner, pet, owner_sid, match, rng, consume_action=True)


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
                template = PETS.get(pet.template_id, {})
                if pet.hp <= 0 and template.get("permanent_death"):
                    ps.dead_hunter_pets[pet.template_id] = True
                    ps.hunter_pet_memory[pet.template_id] = 0
                if ps.active_pet_id == pet_id:
                    ps.active_pet_id = None
                match.log.append(f"{pet.name} dies.")
                del ps.pets[pet_id]
