from typing import Callable

from ..content.pets import PETS
from .dice import roll
from .rules import base_damage, hit_chance
from .effects import (
    mitigate_damage,
    modify_stat,
    has_flag,
    has_effect,
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
    base = f"{_owner_label(owner_sid)}'s {pet.name} {action_text}"
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


def _owner_label(owner_sid: str) -> str:
    return owner_sid[:5]


def _resolve_special_overrides(pet, overrides: dict | None) -> dict:
    resolved = dict(overrides or {})
    for key, value in list(resolved.items()):
        if value == "self.id":
            resolved[key] = pet.id
    return resolved


def _append_pet_log(match, line: str, deferred_logs: list[str] | None = None) -> None:
    if deferred_logs is not None:
        deferred_logs.append(line)
    else:
        match.log.append(line)


def _can_pay_pet_cost(pet, costs: dict | None) -> tuple[bool, str]:
    for key, amount in (costs or {}).items():
        cost = int(amount or 0)
        if cost <= 0:
            continue
        if int(getattr(pet, key, 0) or 0) < cost:
            return False, str(key)
    return True, ""


def _pay_pet_cost(pet, costs: dict | None) -> None:
    for key, amount in (costs or {}).items():
        cost = int(amount or 0)
        if cost <= 0:
            continue
        current = int(getattr(pet, key, 0) or 0)
        setattr(pet, key, max(0, current - cost))


def _build_pet_attack_ability(*, school: str, include_spell_tag: bool = False) -> dict:
    damage_type = "magic" if school != "physical" else "physical"
    tags = ["attack", damage_type]
    if include_spell_tag:
        tags.append("spell")
    return {"requires_target": True, "damage_type": damage_type, "tags": tags, "flat_damage": 1}


def _resolve_pet_targeting_and_hit(
    *,
    owner,
    enemy,
    pet,
    owner_sid,
    action_text: str,
    ability: dict,
    rng,
    match,
    stealth_targeting,
    should_miss_due_to_stealth,
    untargetable_miss_log,
    can_evasion_force_miss,
    single_target_miss_active,
    single_target_miss_log,
) -> bool:
    if should_miss_due_to_stealth(owner, enemy, ability, stealth_targeting):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target is stealthed — Miss!"))
        return False
    if has_flag(enemy, "untargetable"):
        match.log.append(_pet_log(owner_sid, pet, action_text, untargetable_miss_log(enemy)))
        return False
    if single_target_miss_active(enemy, ability):
        match.log.append(_pet_log(owner_sid, pet, action_text, single_target_miss_log()))
        return False
    if has_flag(enemy, "evade_all") and can_evasion_force_miss(ability, True):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target evades the attack — Miss!"))
        return False
    accuracy = hit_chance(modify_stat(pet, "acc", _pet_stat(pet, "acc", 95)), modify_stat(enemy, "eva", enemy.stats.get("eva", 0)))
    if rng.randint(1, 100) > accuracy:
        match.log.append(_pet_log(owner_sid, pet, action_text, "Miss!"))
        return False
    return True


def _resolve_pet_damage_and_log(
    *,
    owner,
    enemy,
    pet,
    owner_sid,
    enemy_sid,
    label: str,
    action_text: str,
    school: str,
    subschool: str | None,
    raw_damage: int,
    match,
    apply_damage,
    absorb_suffix,
) -> dict:
    normalized_school = "magical" if school == "magic" else school
    if is_immune_all(enemy):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Immune!"))
        return {"hp_damage": 0, "absorbed": 0, "total_incoming": 0}
    if normalized_school == "physical" and is_damage_immune(enemy, "physical"):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Immune!"))
        return {"hp_damage": 0, "absorbed": 0, "total_incoming": 0}
    if normalized_school != "physical" and (is_damage_immune(enemy, "magic") or has_effect(enemy, "cloak_of_shadows")):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Immune!"))
        return {"hp_damage": 0, "absorbed": 0, "total_incoming": 0}
    reduced = _damage_after_reduction(raw_damage, enemy, normalized_school)
    dealt = apply_damage(owner, enemy, reduced, enemy_sid, label, school=normalized_school, subschool=subschool)
    remaining = int(dealt.get("hp_damage", 0) or 0)
    absorbed = int(dealt.get("absorbed", 0) or 0)
    breakdown = dealt.get("absorbed_breakdown", [])
    total_incoming = remaining + absorbed
    if total_incoming > 0:
        line = _pet_log(owner_sid, pet, action_text, f"for {total_incoming} damage.")
        if absorbed > 0:
            line = f"{line} {absorb_suffix(absorbed, breakdown).strip()}"
        match.log.append(line)
    return {"hp_damage": remaining, "absorbed": absorbed, "total_incoming": total_incoming}


def apply_pet_resource_regen(pet) -> None:
    template = PETS.get(pet.template_id, {})
    resources = template.get("resources", {}) or {}
    mp_regen = int(resources.get("mp_regen", 0) or 0)
    if mp_regen > 0 and int(getattr(pet, "mp_max", 0) or 0) > 0:
        pet.mp = min(int(getattr(pet, "mp", 0) or 0) + mp_regen, int(getattr(pet, "mp_max", 0) or 0))
    energy_regen = int(resources.get("energy_regen", 0) or 0)
    if energy_regen > 0 and int(getattr(pet, "energy_max", 0) or 0) > 0:
        pet.energy = min(int(getattr(pet, "energy", 0) or 0) + energy_regen, int(getattr(pet, "energy_max", 0) or 0))


def _pet_stat(pet, stat_key: str, default: int = 0) -> int:
    stats = getattr(pet, "stats", {}) or {}
    if stat_key in stats:
        return int(stats.get(stat_key, default) or default)
    template = PETS.get(pet.template_id, {})
    return int(template.get(stat_key, default) or default)


def _apply_effect_special(owner, pet, owner_sid, match, special_id: str, *, deferred_logs: list[str] | None = None) -> bool:
    template = PETS.get(pet.template_id, {})
    special_data = ((template.get("specials") or {}).get(special_id) or {})
    effect_id = special_data.get("effect_id")
    if not effect_id:
        return False
    overrides = _resolve_special_overrides(pet, special_data.get("effect_overrides"))
    apply_effect_by_id(owner, effect_id, overrides=overrides or None)
    _append_pet_log(match, _pet_log(owner_sid, pet, _template_action_text(pet, special_id=special_id, fallback="acts")), deferred_logs)
    return True


def trigger_pre_action_special(owner, pet, owner_sid, match, rng, *, consume_action: bool = True, deferred_logs: list[str] | None = None) -> bool:
    template = PETS.get(pet.template_id, {})
    special_id = template.get("special_id")
    special_data = ((template.get("specials") or {}).get(special_id) or {})
    if special_data.get("timing") != "pre_action":
        return False
    if get_cant_act_reason(pet):
        return False

    forced_command = owner.pending_pet_command
    use_special = forced_command == "special"
    if not use_special:
        use_special = rng.random() <= float(template.get("special_chance", 0) or 0)
    if not use_special:
        return False

    costs = special_data.get("cost") or {}
    can_pay, _ = _can_pay_pet_cost(pet, costs)
    if not can_pay:
        return False

    _pay_pet_cost(pet, costs)
    if not _apply_effect_special(owner, pet, owner_sid, match, str(special_id), deferred_logs=deferred_logs):
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
    single_target_miss_active,
    single_target_miss_log,
):
    action_text = _template_action_text(pet, fallback="casts Firebolt")
    template = PETS.get(pet.template_id, {})
    firebolt_cost = ((template.get("basic_attack") or {}).get("cost") or {})
    can_pay_cost, _ = _can_pay_pet_cost(pet, firebolt_cost)
    if not can_pay_cost:
        return
    _pay_pet_cost(pet, firebolt_cost)

    ability = _build_pet_attack_ability(school="magic", include_spell_tag=True)
    if not _resolve_pet_targeting_and_hit(
        owner=owner,
        enemy=enemy,
        pet=pet,
        owner_sid=owner_sid,
        action_text=action_text,
        ability=ability,
        rng=rng,
        match=match,
        stealth_targeting=stealth_targeting,
        should_miss_due_to_stealth=should_miss_due_to_stealth,
        untargetable_miss_log=untargetable_miss_log,
        can_evasion_force_miss=can_evasion_force_miss,
        single_target_miss_active=single_target_miss_active,
        single_target_miss_log=single_target_miss_log,
    ):
        return

    fire_roll = roll("d4", rng)
    raw_fire = base_damage(modify_stat(pet, "int", _pet_stat(pet, "int")), 0.7, fire_roll)
    dealt_data = _resolve_pet_damage_and_log(
        owner=owner, enemy=enemy, pet=pet, owner_sid=owner_sid, enemy_sid=enemy_sid, label="Imp Firebolt",
        action_text=action_text, school="magical", subschool=(PETS.get(pet.template_id, {}) or {}).get("subschool"),
        raw_damage=raw_fire, match=match, apply_damage=apply_damage, absorb_suffix=absorb_suffix,
    )
    remaining = int(dealt_data.get("hp_damage", 0) or 0)
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
    single_target_miss_active,
    single_target_miss_log,
):
    ability = _build_pet_attack_ability(school="physical")
    action_text = _template_action_text(pet, fallback="melees the target")
    misses = False
    if should_miss_due_to_stealth(owner, enemy, ability, stealth_targeting):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target is stealthed — Miss!"))
        misses = True
    elif has_flag(enemy, "untargetable"):
        match.log.append(_pet_log(owner_sid, pet, action_text, untargetable_miss_log(enemy)))
        misses = True
    elif single_target_miss_active(enemy, ability):
        match.log.append(_pet_log(owner_sid, pet, action_text, single_target_miss_log()))
        misses = True
    elif has_flag(enemy, "evade_all") and can_evasion_force_miss(ability, True):
        match.log.append(_pet_log(owner_sid, pet, action_text, "Target evades the attack — Miss!"))
        misses = True
    else:
        accuracy = hit_chance(
            modify_stat(pet, "acc", _pet_stat(pet, "acc", 95)),
            modify_stat(enemy, "eva", enemy.stats.get("eva", 0)),
        )
        if rng.randint(1, 100) > accuracy:
            match.log.append(_pet_log(owner_sid, pet, action_text, "Miss!"))
            misses = True

    if misses:
        return
    fiend_roll = roll("d4", rng)
    raw = base_damage(modify_stat(pet, "atk", _pet_stat(pet, "atk")), 1.0, fiend_roll)
    dealt = _resolve_pet_damage_and_log(
        owner=owner, enemy=enemy, pet=pet, owner_sid=owner_sid, enemy_sid=enemy_sid, label="Shadowfiend melee",
        action_text=action_text, school="physical", subschool=None, raw_damage=raw, match=match, apply_damage=apply_damage,
        absorb_suffix=absorb_suffix,
    )
    remaining = int(dealt.get("hp_damage", 0) or 0)
    total_incoming = int(dealt.get("total_incoming", 0) or 0)
    if total_incoming > 0:
        owner.res.mp = min(owner.res.mp + 13, owner.res.mp_max)
        match.log.append(f"Shadowfiend restores 13 mana for {_owner_label(owner_sid)}.")
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
    single_target_miss_active,
    single_target_miss_log,
):
    template = PETS.get(pet.template_id, {})
    forced_command = owner.pending_pet_command
    special_id = template.get("special_id")
    special_data = ((template.get("specials") or {}).get(special_id) or {}) if special_id else {}
    special_timing = str(special_data.get("timing") or "").strip().lower()
    can_use_runtime_special = bool(special_id) and special_timing != "pre_action"

    use_special = forced_command == "special" and can_use_runtime_special
    if not use_special and can_use_runtime_special:
        use_special = rng.random() <= float(template.get("special_chance", 0) or 0)

    if use_special and special_id:
        special_costs = special_data.get("cost") or {}
        can_pay_special, _ = _can_pay_pet_cost(pet, special_costs)
        if can_pay_special:
            _pay_pet_cost(pet, special_costs)
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
                single_target_miss_active,
                single_target_miss_log,
            )
            return
        use_special = False

    profile = template.get("basic_attack", {}) or {}
    basic_costs = profile.get("cost") or {}
    can_pay_basic, _ = _can_pay_pet_cost(pet, basic_costs)
    if not can_pay_basic:
        return
    _pay_pet_cost(pet, basic_costs)

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
        single_target_miss_active,
        single_target_miss_log,
        stat_key=profile.get("stat", "atk"),
        scaling=float(profile.get("scaling", 1.0) or 1.0),
        dice=profile.get("dice", "d4"),
        school=profile.get("school", "physical"),
        subschool=profile.get("subschool"),
        label=f"{pet.name} attacks",
        action_text=profile.get("action_text", "attacks"),
    )
    if pet.template_id == "barrens_boar":
        pet.rage = min(int(getattr(pet, "rage", 0) or 0) + 5, int(getattr(pet, "rage_max", 0) or 0))


def _run_mana_tide_totem_regen(
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
    single_target_miss_active,
    single_target_miss_log,
):
    if owner.res.hp <= 0:
        return
    before_mp = owner.res.mp
    owner.res.mp = min(owner.res.mp + 10, owner.res.mp_max)
    restored = owner.res.mp - before_mp
    if restored > 0:
        match.log.append(f"{_owner_label(owner_sid)}'s Mana Tide Totem restores {restored} mana.")


def _run_capacitor_totem_discharge(
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
    single_target_miss_active,
    single_target_miss_log,
):
    if bool(getattr(pet, "_capacitor_primed", False)):
        return
    setattr(pet, "_capacitor_primed", True)
    match.log.append(f"{_owner_label(owner_sid)}'s Capacitor Totem is charging.")


def _resolve_capacitor_totem_discharge(owner, enemy, pet, owner_sid, enemy_sid, match) -> None:
    if not bool(getattr(pet, "_capacitor_primed", False)):
        setattr(pet, "_capacitor_primed", True)
        match.log.append(f"{_owner_label(owner_sid)}'s Capacitor Totem is charging.")
        return
    apply_effect_by_id(
        enemy,
        "capacitor_totem_stun",
        overrides={"duration": 2, "source_ability_name": "Capacitor Totem Stun"},
    )
    for enemy_pet_id in sorted((enemy.pets or {}).keys()):
        enemy_pet = enemy.pets.get(enemy_pet_id)
        if not enemy_pet or enemy_pet.hp <= 0:
            continue
        apply_effect_by_id(
            enemy_pet,
            "capacitor_totem_stun",
            overrides={"duration": 2, "source_ability_name": "Capacitor Totem Stun"},
        )
    match.log.append(f"{_owner_label(owner_sid)}'s Capacitor Totem discharges!")
    pet.hp = 0




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
    single_target_miss_active,
    single_target_miss_log,
    *,
    stat_key: str,
    scaling: float,
    dice: str,
    school: str,
    subschool: str | None,
    label: str,
    action_text: str = "attacks",
):
    ability = _build_pet_attack_ability(school=school)
    if not _resolve_pet_targeting_and_hit(
        owner=owner, enemy=enemy, pet=pet, owner_sid=owner_sid, action_text=action_text, ability=ability, rng=rng, match=match,
        stealth_targeting=stealth_targeting, should_miss_due_to_stealth=should_miss_due_to_stealth,
        untargetable_miss_log=untargetable_miss_log, can_evasion_force_miss=can_evasion_force_miss,
        single_target_miss_active=single_target_miss_active, single_target_miss_log=single_target_miss_log,
    ):
        return

    stat_value = _pet_stat(pet, str(stat_key), 0)
    rolled = roll(dice, rng) if dice else 0
    raw = base_damage(stat_value, scaling, rolled)
    dealt = _resolve_pet_damage_and_log(
        owner=owner, enemy=enemy, pet=pet, owner_sid=owner_sid, enemy_sid=enemy_sid, label=label, action_text=action_text,
        school=school, subschool=subschool, raw_damage=raw, match=match, apply_damage=apply_damage, absorb_suffix=absorb_suffix,
    )
    remaining = int(dealt.get("hp_damage", 0) or 0)
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
    single_target_miss_active,
    single_target_miss_log,
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
            single_target_miss_active,
            single_target_miss_log,
            stat_key="atk",
            scaling=2.0,
            dice="d6",
            school="physical",
            subschool=None,
            label="Bite",
            action_text=_template_action_text(pet, special_id=special_id, fallback="bites the target"),
        )

    if special_id == "lightning_breath":
        ability = _build_pet_attack_ability(school="magic")
        action_text = _template_action_text(pet, special_id=special_id, fallback="breathes lightning")
        if not _resolve_pet_targeting_and_hit(
            owner=owner, enemy=enemy, pet=pet, owner_sid=owner_sid, action_text=action_text, ability=ability, rng=rng, match=match,
            stealth_targeting=stealth_targeting, should_miss_due_to_stealth=should_miss_due_to_stealth,
            untargetable_miss_log=untargetable_miss_log, can_evasion_force_miss=can_evasion_force_miss,
            single_target_miss_active=single_target_miss_active, single_target_miss_log=single_target_miss_log,
        ):
            return
        raw = base_damage(modify_stat(pet, "int", _pet_stat(pet, "int")), 1.5, roll("d6", rng))
        dealt = _resolve_pet_damage_and_log(
            owner=owner, enemy=enemy, pet=pet, owner_sid=owner_sid, enemy_sid=enemy_sid, label="Lightning Breath", action_text=action_text,
            school="magical",
            subschool=((PETS.get(pet.template_id, {}).get("specials", {}) or {}).get("lightning_breath", {}) or {}).get("subschool"),
            raw_damage=raw, match=match, apply_damage=apply_damage, absorb_suffix=absorb_suffix,
        )
        remaining = int(dealt.get("hp_damage", 0) or 0)
        if remaining > 0:
            heal_value = remaining // 2
            before_pet = pet.hp
            pet.hp = min(pet.hp + heal_value, pet.hp_max)
            before_owner = owner.res.hp
            owner.res.hp = min(owner.res.hp + heal_value, owner.res.hp_max)
            match.log.append(
                f"{pet.name} restores {pet.hp - before_pet} HP to itself and {owner.res.hp - before_owner} HP to {_owner_label(owner_sid)}."
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
    "mana_tide_totem_regen": _run_mana_tide_totem_regen,
    "capacitor_totem_discharge": _run_capacitor_totem_discharge,
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
    single_target_miss_active,
    single_target_miss_log,
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
                match.log.append(f"{_owner_label(owner_sid)}'s {pet.name} is {reason} and cannot act.")
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
                single_target_miss_active,
                single_target_miss_log,
            )
        owner.pending_pet_command = None


def prepare_pet_pre_action_effects(match, rng) -> list[str]:
    deferred_logs: list[str] = []
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
            if pet.template_id == "capacitor_totem":
                _resolve_capacitor_totem_discharge(owner, enemy, pet, owner_sid, enemy_sid, match)
                continue

            trigger_pre_action_special(owner, pet, owner_sid, match, rng, consume_action=True, deferred_logs=deferred_logs)
    return deferred_logs


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
                    ps.hunter_pet_memory[pet.template_id] = {"hp": 0, "mp": 0, "energy": 0, "rage": 0}
                if ps.active_pet_id == pet_id:
                    ps.active_pet_id = None
                match.log.append(f"{pet.name} dies.")
                del ps.pets[pet_id]
