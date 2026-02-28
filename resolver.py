# games/duel/engine/resolver.py
from typing import Dict, Any, Tuple
from .models import MatchState, PlayerState, PlayerBuild, Resources, PetState
from .dice import rng_for, roll
from .rules import base_damage, mitigate, hit_chance, clamp
from ..content.abilities import ABILITIES
from ..content.classes import CLASSES
from ..content.items import ITEMS
from ..content.balance import DEFAULTS, CAPS
from ..content.pets import PETS
from .pet_ai import run_pet_phase, cleanup_pets

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
    consume_absorbs,
    absorb_total,
    build_effect,
    dispel_effects,
    refresh_dot_effect,
    tick_player_effects,
    FORM_EFFECT_IDS,
    current_form_id,
    get_effect,
    outgoing_damage_multiplier,
    is_dispellable_by,
    effect_template,
    is_magical_harmful_effect,
    normalize_school,
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
            absorbs={},
        )
        
        ps = PlayerState(sid=sid, build=build, res=res, stats=stats, pets={})

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
                apply_effect_by_id(
                    ps,
                    "item_passive_template",
                    overrides={
                        "type": "item_passive",
                        "name": item["name"],
                        "source_item": item["name"],
                        "passive": passive,
                        "duration": 999,
                    },
                )
        
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
        target: PlayerState | PetState,
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
            effect = build_effect(entry["id"], overrides=overrides or None)
            if has_effect(target, "cloak_of_shadows") and is_magical_harmful_effect(effect):
                log_parts.append("Immune!")
                continue
            if entry["id"] in FORM_EFFECT_IDS:
                apply_form(target, entry["id"], overrides=overrides or None)
            else:
                apply_effect_by_id(target, entry["id"], overrides=overrides or None)
            if entry.get("log"):
                log_parts.append(entry["log"])

    def apply_hp_sacrifice_absorb(
        actor: PlayerState,
        ability: Dict[str, Any],
        log_parts: list[str],
    ) -> None:
        hp_sacrifice = ability.get("hp_sacrifice") or {}
        absorb_from_sacrifice = ability.get("grant_absorb_from_sacrifice") or {}
        if not hp_sacrifice or not absorb_from_sacrifice:
            return

        pct = float(hp_sacrifice.get("pct", 0) or 0)
        if pct <= 0:
            return

        min_hp_leave = max(0, int(hp_sacrifice.get("min_hp_leave", 0) or 0))
        # Uses current HP so sacrifice scales with the actor's present health pool.
        base_hp_for_sacrifice = max(0, int(actor.res.hp))
        desired_sacrifice = max(0, int(base_hp_for_sacrifice * pct))
        max_sacrifice = max(0, int(actor.res.hp) - min_hp_leave)
        sacrificed_hp = min(desired_sacrifice, max_sacrifice)

        if sacrificed_hp > 0:
            actor.res.hp = max(min_hp_leave, int(actor.res.hp) - sacrificed_hp)

        mult = float(absorb_from_sacrifice.get("mult", 0) or 0)
        absorb_value = max(0, int(sacrificed_hp * mult))
        effect_id = absorb_from_sacrifice.get("effect_id")
        duration = absorb_from_sacrifice.get("duration")

        if effect_id:
            overrides = {}
            if duration is not None:
                overrides["duration"] = int(duration)
            apply_effect_by_id(actor, effect_id, overrides=overrides or None)
            if absorb_value > 0:
                add_absorb(
                    actor,
                    absorb_value,
                    source_name=ability.get("name") or "Shield",
                    effect_id=effect_id,
                )
        elif absorb_value > 0:
            add_absorb(
                actor,
                absorb_value,
                source_name=ability.get("name") or "Shield",
            )

        class_name = (actor.build.class_id or "Actor").title()
        source_name = ability.get("name") or "ability"
        log_parts.append(f"{class_name} sacrifices {sacrificed_hp} HP and gains {absorb_value} absorb from {source_name}.")

    def is_offensive_action(ability: Dict[str, Any]) -> bool:
        if "pass" in (ability.get("tags") or []):
            return False
        has_damage = any(value for value in (ability.get("dice"), ability.get("scaling"), ability.get("flat_damage")))
        has_target_effects = bool(ability.get("target_effects"))
        return has_damage or has_target_effects

    def should_miss_due_to_stealth(
        attacker: PlayerState,
        target: PlayerState | PetState,
        ability: Dict[str, Any] | None,
        stealth_snapshot: Dict[str, bool],
    ) -> bool:
        if not ability:
            return False
        is_aoe = is_aoe_ability(ability) or not ability.get("requires_target", True)
        if is_aoe:
            return False
        return bool(stealth_snapshot.get(target.sid, False) or is_stealthed(target))

    def ability_target_mode(ability: Dict[str, Any]) -> str:
        return str(ability.get("target_mode") or "enemy")

    def is_aoe_ability(ability: Dict[str, Any]) -> bool:
        if ability_target_mode(ability) == "aoe_enemy":
            return True
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

        if ability_id == "summon_imp" and len([p for p in (actor.pets or {}).values() if p.template_id == "imp"]) >= int(PETS["imp"].get("max_count", 3)):
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
                apply_damage(actor, actor, heal_value, actor_sid, "Mindgames", school="magical")
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
            imp_template = PETS["imp"]
            owner_idx = match.players.index(actor.sid) + 1
            next_idx = 1
            while f"p{owner_idx}_imp_{next_idx}" in actor.pets:
                next_idx += 1
            pet_id = f"p{owner_idx}_imp_{next_idx}"
            actor.pets[pet_id] = PetState(
                id=pet_id,
                template_id="imp",
                name=imp_template["name"],
                owner_sid=actor.sid,
                hp=int(imp_template["hp"]),
                hp_max=int(imp_template["hp"]),
                effects=[],
                duration=None,
            )
            imp_count = len([p for p in actor.pets.values() if p.template_id == "imp"])
            log_parts.append(f"summons an Imp ({imp_count}/3).")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id in ("corruption", "unstable_affliction"):
            if is_immune_all(target):
                target_immune_log(log_parts)
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            dot_template = build_effect((ability.get("dot") or {}).get("id", ""))
            if has_effect(target, "cloak_of_shadows") and is_magical_harmful_effect(dot_template):
                log_parts.append("Immune!")
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
            agony_template = build_effect("agony")
            if has_effect(target, "cloak_of_shadows") and is_magical_harmful_effect(agony_template):
                log_parts.append("Immune!")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            apply_effect_by_id(
                target,
                "agony",
                overrides={"duration": 15, "tick_damage": 1, "source_sid": actor.sid, "dot_mode": "ramp"},
            )
            log_parts.append("inflicts Agony.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

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
                apply_damage(actor, actor, heal_value, actor_sid, "Mindgames", school="magical")
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
                apply_damage(actor, actor, heal_value, actor_sid, "Mindgames", school="magical")
                log_parts.append(f"Mindgames twists healing into {heal_value} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
            healed = actor.res.hp - before_hp
            log_parts.append(f"Holy Light restores {healed} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "flash_heal":
            intellect = modify_stat(actor, "int", actor.stats.get("int", 0))
            heal_value = int(intellect * 0.9) + int(roll("d8", r))
            if has_effect(actor, "mindgames"):
                apply_damage(actor, actor, heal_value, actor_sid, "Mindgames", school="magical")
                log_parts.append(f"Mindgames twists healing into {heal_value} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
            healed = actor.res.hp - before_hp
            log_parts.append(f"Flash Heal restores {healed} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "mass_dispel":
            removed_names = []
            self_removed = 0
            enemy_removed = 0

            def dispel_name(effect: Dict[str, Any]) -> str:
                effect_id = effect.get("id")
                template_name = effect_template(effect_id).get("name") if effect_id else None
                return str(effect.get("name") or template_name or "Effect")

            actor_to_remove = []
            for effect in list(actor.effects):
                effect_id = effect.get("id")
                if not effect_id:
                    continue
                if not is_dispellable_by(effect, dispel_type="mass_dispel", kind="magical"):
                    continue
                effect_category = str(effect.get("category") or effect_template(effect_id).get("category") or "").lower()
                if effect_category not in {"dot", "debuff"}:
                    continue
                actor_to_remove.append(effect)

            for effect in actor_to_remove:
                effect_id = effect.get("id")
                if not effect_id:
                    continue
                removed_names.append(dispel_name(effect))
                remove_effect(actor, effect_id)
                self_removed += 1

            enemy_to_remove = []
            for effect in list(target.effects):
                effect_id = effect.get("id")
                if not effect_id:
                    continue
                if not is_dispellable_by(effect, dispel_type="mass_dispel", kind="magical"):
                    continue
                effect_category = str(effect.get("category") or effect_template(effect_id).get("category") or "").lower()
                if effect_category in {"dot", "debuff"}:
                    continue
                enemy_to_remove.append(effect)

            for effect in enemy_to_remove:
                effect_id = effect.get("id")
                if not effect_id:
                    continue
                removed_names.append(dispel_name(effect))
                remove_effect(target, effect_id)
                enemy_removed += 1

            if removed_names:
                log_parts.append(f"{', '.join(removed_names)} removed by Mass Dispel!")
            log_parts.append(f"Dispels {self_removed} effects on self and {enemy_removed} effects on enemy")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "lay_on_hands":
            if has_effect(actor, "mindgames"):
                backlash = actor.res.hp
                apply_damage(actor, actor, backlash, actor_sid, "Mindgames", school="magical")
                log_parts.append(f"Mindgames twists healing into {backlash} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = actor.res.hp_max
            healed = actor.res.hp - before_hp
            log_parts.append("Lay on Hands restores health to full.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id in ("vampiric_touch", "devouring_plague"):
            if is_immune_all(target):
                target_immune_log(log_parts)
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            dot_template = build_effect((ability.get("dot") or {}).get("id", ""))
            if has_effect(target, "cloak_of_shadows") and is_magical_harmful_effect(dot_template):
                log_parts.append("Immune!")
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
                    apply_damage(actor, actor, heal_value, actor_sid, "Mindgames", school="magical")
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
            existing_id = next((pid for pid, pet in actor.pets.items() if pet.template_id == "shadowfiend"), None)
            template = PETS["shadowfiend"]
            duration = int(template.get("duration", 5))
            if existing_id:
                actor.pets[existing_id].duration = duration
                actor.pets[existing_id].hp = actor.pets[existing_id].hp_max
                log_parts.append("refreshes Shadowfiend.")
            else:
                owner_idx = match.players.index(actor.sid) + 1
                pet_id = f"p{owner_idx}_shadowfiend"
                actor.pets[pet_id] = PetState(
                    id=pet_id,
                    template_id="shadowfiend",
                    name=template["name"],
                    owner_sid=actor.sid,
                    hp=int(template["hp"]),
                    hp_max=int(template["hp"]),
                    effects=[],
                    duration=duration,
                )
            remove_effect(actor, "shadowfiend")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        apply_hp_sacrifice_absorb(actor, ability, log_parts)

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
                absorb_effect_id = absorb_data.get("effect_id") or (ability.get("self_effects") or [{}])[0].get("id")
                add_absorb(
                    actor,
                    absorb_value,
                    source_name=absorb_data.get("source_name") or ability.get("name") or "Shield",
                    effect_id=absorb_effect_id,
                )
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
        aoe_incoming_damage = 0
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
        aoe_damage_instances: list[int] = []
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

            incoming_for_hit = reduced
            multiplier = damage_multiplier_from_passives(actor)
            if multiplier != 1.0:
                incoming_for_hit = int(incoming_for_hit * multiplier)
            if empower_multiplier != 1.0:
                incoming_for_hit = int(incoming_for_hit * empower_multiplier)
            if outgoing_mult != 1.0:
                incoming_for_hit = int(incoming_for_hit * outgoing_mult)
            if death_doubled and incoming_for_hit > 0:
                incoming_for_hit *= 2

            if ability_target_mode(ability) == "aoe_enemy" and incoming_for_hit > 0:
                aoe_incoming_damage += incoming_for_hit
                aoe_damage_instances.append(incoming_for_hit)

            if is_damage_immune(target, damage_type):
                reduced = 0
                log_parts.append(f"{prefix}Immune!")
            else:
                reduced = incoming_for_hit
                if empower_multiplier != 1.0:
                    if not empower_logged:
                        log_parts.append(f"{prefix}Empowered strike!")
                        empower_logged = True
                if death_doubled and reduced > 0:
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

        mindgames_flip_damage = bool(has_effect(actor, "mindgames") and total_damage > 0)

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

        outgoing_damage = aoe_incoming_damage if ability_target_mode(ability) == "aoe_enemy" else total_damage
        outgoing_instances = aoe_damage_instances if ability_target_mode(ability) == "aoe_enemy" else damage_instances

        return {
            "damage": outgoing_damage,
            "damage_instances": outgoing_instances,
            "aoe_incoming_damage": aoe_incoming_damage,
            "damage_type": damage_type,
            "healing": total_healing,
            "log": " ".join(log_parts),
            "extra_logs": extra_logs,
            "ability_id": ability_id,
            "mindgames_flip_damage": mindgames_flip_damage,
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

        if ability_id == "summon_imp" and len([p for p in (actor.pets or {}).values() if p.template_id == "imp"]) >= int(PETS["imp"].get("max_count", 3)):
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
        is_aoe = is_aoe_ability(ability)
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
        is_aoe = is_aoe_ability(ability)

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
            apply_effect_by_id(
                actor,
                "item_passive_template",
                overrides={"type": effect.get("type", "status"), **effect},
            )
            log_parts.append("Defensive stance raised.")
        else:
            apply_effect_entries(
                actor,
                target,
                ability,
                log_parts,
                skip_self_effect_ids=skip_self_effect_ids,
            )

        apply_hp_sacrifice_absorb(actor, ability, log_parts)

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
                absorb_effect_id = absorb_data.get("effect_id") or (ability.get("self_effects") or [{}])[0].get("id")
                add_absorb(
                    actor,
                    absorb_value,
                    source_name=absorb_data.get("source_name") or ability.get("name") or "Shield",
                    effect_id=absorb_effect_id,
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
        if action.get("ability_id") == "mass_dispel":
            return {
                "damage": 0,
                "healing": 0,
                "log": "",
                "ability_id": "mass_dispel",
                "deferred": True,
            }
        return resolve_action(actor_sid, target_sid, action)

    # Resolve both actions
    result1 = finalize_action(sids[0], sids[1], a1, contexts[sids[0]])
    result2 = finalize_action(sids[1], sids[0], a2, contexts[sids[1]])

    if result1.get("deferred"):
        result1 = resolve_action(sids[0], sids[1], a1)
    if result2.get("deferred"):
        result2 = resolve_action(sids[1], sids[0], a2)

    for sid, result in ((sids[0], result1), (sids[1], result2)):
        totals = match.combat_totals.setdefault(sid, {"damage": 0, "healing": 0})
        totals["damage"] += int(result.get("damage", 0) or 0)
        totals["healing"] += int(result.get("healing", 0) or 0)

    def absorb_suffix(absorbed: int, absorbed_breakdown: list[Dict[str, Any]] | None = None) -> str:
        if absorbed <= 0:
            return ""
        parts = []
        for entry in absorbed_breakdown or []:
            amount = int(entry.get("amount", 0) or 0)
            if amount <= 0:
                continue
            parts.append(f"{amount} absorbed by {entry.get('name') or 'Shield'}")
        if not parts:
            return f" ({absorbed} absorbed by Shield)"
        return f" ({', '.join(parts)})"

    def format_damage_log(log_text: str, damage_breakdown: Dict[str, Any]) -> str:
        if not log_text:
            return ""
        instances = list(damage_breakdown.get("instances", []) or [])
        absorb_notes: list[str] = []
        if not instances:
            absorbed = int(damage_breakdown.get("absorbed", 0) or 0)
            if absorbed > 0:
                absorb_notes.append(absorb_suffix(absorbed, damage_breakdown.get("absorbed_breakdown", [])).strip())
            if absorb_notes:
                return f"{log_text} {' '.join(absorb_notes)}"
            return log_text
        updated = log_text
        for idx, instance in enumerate(instances):
            token = f"__DMG_{idx}__"
            token_with_damage = f"{token} damage"
            hp_damage = int(instance.get("hp_damage", 0) or 0)
            absorbed = int(instance.get("absorbed", 0) or 0)
            total_incoming = hp_damage + absorbed
            if absorbed > 0:
                absorb_notes.append(absorb_suffix(absorbed, instance.get("absorbed_breakdown", [])).strip())
            replacement = f"{total_incoming} damage"
            if token_with_damage in updated:
                updated = updated.replace(token_with_damage, replacement, 1)
            else:
                updated = updated.replace(token, str(total_incoming), 1)
        if absorb_notes:
            updated = f"{updated} {' '.join(absorb_notes)}"
        return updated

    # Apply damage
    def apply_damage(
        source: PlayerState,
        target: PlayerState | PetState,
        incoming: int,
        target_sid: str,
        source_ability_name: str,
        mindgames_flip_damage: bool = False,
        damage_instances: list[int] | None = None,
        school: str = "physical",
    ) -> Dict[str, Any]:
        is_player_target = hasattr(target, "res") and target.res is not None
        if incoming <= 0:
            return {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0}
        if is_player_target:
            if is_immune_all(target):
                match.log.append(f"{target_sid[:5]} is immune and takes no damage.")
                return {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0}
            if has_flag(target, "cycloned"):
                match.log.append(f"{target_sid[:5]} is cycloned and takes no damage.")
                return {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0}
            if normalize_school(school) == "magical" and has_effect(target, "cloak_of_shadows"):
                match.log.append(f"{target_sid[:5]} is immune to magical harm under Cloak of Shadows.")
                return {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0}
        instance_values = [max(0, int(value or 0)) for value in (damage_instances or []) if int(value or 0) > 0]
        accounted = sum(instance_values)
        if incoming > accounted:
            instance_values.append(incoming - accounted)
        if not instance_values:
            instance_values = [incoming]

        if is_player_target and mindgames_flip_damage and source.sid != target.sid:
            before_hp = target.res.hp
            target.res.hp = min(target.res.hp + incoming, target.res.hp_max)
            instance_results = [{"absorbed": 0, "hp_damage": value, "absorbed_breakdown": []} for value in instance_values]
            return {
                "hp_damage": 0,
                "absorbed": 0,
                "absorbed_breakdown": [],
                "instances": instance_results,
                "mindgames_healing": incoming,
            }

        instance_results: list[Dict[str, Any]] = []
        total_absorbed = 0
        total_remaining = 0
        total_breakdown: list[Dict[str, Any]] = []
        for value in instance_values:
            if is_player_target:
                remaining, absorbed, breakdown = consume_absorbs(target, value)
            else:
                remaining, absorbed, breakdown = value, 0, []
            total_absorbed += absorbed
            total_remaining += remaining
            instance_results.append({"absorbed": absorbed, "hp_damage": remaining, "absorbed_breakdown": breakdown})
            total_breakdown.extend(breakdown)

        if total_remaining > 0:
            if is_player_target:
                target.res.hp -= total_remaining
                was_stealthed = is_stealthed(target)
                break_stealth_on_damage(target, total_remaining)
                if was_stealthed and not is_stealthed(target):
                    match.log.append(f"{target_sid[:5]} stealth broken by {source_ability_name}.")
                if current_form_id(target) == "bear_form":
                    current = target.res.rage
                    cap = target.res.rage_max
                    target.res.rage = min(current + total_remaining, cap)
            else:
                target.hp -= total_remaining
        return {
            "hp_damage": max(0, total_remaining),
            "absorbed": total_absorbed,
            "absorbed_breakdown": total_breakdown,
            "instances": instance_results,
            "mindgames_healing": 0,
        }

    def trigger_shield_of_vengeance_explosion(owner_sid: str, enemy_sid: str) -> None:
        owner = match.state[owner_sid]
        enemy = match.state[enemy_sid]
        shield_fx = get_effect(owner, "shield_of_vengeance")
        if not shield_fx:
            return
        if shield_fx.get("exploded"):
            return
        if absorb_total(owner) > 0 and int(shield_fx.get("duration", 0) or 0) > 1:
            return
        absorbed_total = int(shield_fx.get("absorbed", 0) or 0)
        shield_fx["exploded"] = True
        remove_effect(owner, "shield_of_vengeance")
        if absorbed_total <= 0:
            return
        dealt_data = apply_damage(
            owner,
            enemy,
            absorbed_total,
            enemy_sid,
            "Shield of Vengeance",
            school="magical",
        )
        hp_damage = int(dealt_data.get("hp_damage", 0) or 0)
        if hp_damage <= 0:
            match.log.append(f"{enemy_sid[:5]} is immune to Shield of Vengeance explosion.")
            return
        match.log.append(
            format_damage_log(
                "Shield of Vengeance explodes for __DMG_0__ magic damage.",
                dealt_data,
            )
        )
        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
        totals["damage"] += hp_damage

    def resolve_dot_tick(source_sid: str, target_sid: str, source: Dict[str, Any]) -> int:
        source_ps = match.state.get(source_sid)
        target_ps = match.state.get(target_sid)
        if not source_ps or not target_ps or not target_ps.res:
            return 0
        incoming = int(source.get("incoming", 0) or 0)
        if incoming <= 0:
            return 0
        dot_name = source.get("effect_name") or effect_name(source.get("effect_id") or "dot")
        dealt_data = apply_damage(
            source_ps,
            target_ps,
            incoming,
            target_sid,
            dot_name,
            bool(has_effect(source_ps, "mindgames")),
            [incoming],
            school=source.get("school") or "magical",
        )
        formatted = format_damage_log(
            f"{target_sid[:5]} suffers __DMG_0__ damage from {dot_name}.",
            dealt_data,
        )
        flipped_heal = int(dealt_data.get("mindgames_healing", 0) or 0)
        if flipped_heal > 0:
            formatted = f"{formatted} Mindgames flips damage into {flipped_heal} healing for the target."
        match.log.append(formatted)
        return int(dealt_data.get("hp_damage", 0) or 0)

    source_name_1 = ABILITIES.get(result1.get("ability_id", ""), {}).get("name", "attack")
    source_name_2 = ABILITIES.get(result2.get("ability_id", ""), {}).get("name", "attack")
    dealt1_data = apply_damage(
        match.state[sids[0]],
        match.state[sids[1]],
        result1["damage"],
        sids[1],
        source_name_1,
        bool(result1.get("mindgames_flip_damage")),
        result1.get("damage_instances"),
        school=result1.get("damage_type") or "physical",
    )
    dealt2_data = apply_damage(
        match.state[sids[1]],
        match.state[sids[0]],
        result2["damage"],
        sids[0],
        source_name_2,
        bool(result2.get("mindgames_flip_damage")),
        result2.get("damage_instances"),
        school=result2.get("damage_type") or "physical",
    )
    dealt1 = int(dealt1_data.get("hp_damage", 0) or 0)
    dealt2 = int(dealt2_data.get("hp_damage", 0) or 0)

    result1_log = format_damage_log(result1["log"], dealt1_data)
    if int(dealt1_data.get("mindgames_healing", 0) or 0) > 0:
        result1_log = (
            f"{result1_log} Mindgames flips damage into "
            f"{int(dealt1_data.get('mindgames_healing', 0) or 0)} healing for the target."
        )
    match.log.append(result1_log)
    match.log.extend(result1.get("extra_logs", []))
    result2_log = format_damage_log(result2["log"], dealt2_data)
    if int(dealt2_data.get("mindgames_healing", 0) or 0) > 0:
        result2_log = (
            f"{result2_log} Mindgames flips damage into "
            f"{int(dealt2_data.get('mindgames_healing', 0) or 0)} healing for the target."
        )
    match.log.append(result2_log)
    match.log.extend(result2.get("extra_logs", []))

    for actor_sid, target_sid, result in ((sids[0], sids[1], result1), (sids[1], sids[0], result2)):
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        if ability_target_mode(ability) != "aoe_enemy":
            continue
        # AoE policy: roll/compute damage once per cast (already in result["damage"])
        # and apply that same incoming value to enemy champion first, then enemy pets.
        incoming = int(result.get("aoe_incoming_damage", result.get("damage", 0)) or 0)
        if incoming <= 0:
            continue
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        school = result.get("damage_type") or "physical"
        source_name = ability.get("name", "AoE")
        pet_targets = [target.pets[pid] for pid in sorted(target.pets.keys())]
        for pet in pet_targets:
            if not pet or pet.hp <= 0:
                continue
            pet_result = apply_damage(actor, pet, incoming, pet.name, source_name, school=school)
            remaining = int(pet_result.get("hp_damage", 0) or 0)
            absorbed = int(pet_result.get("absorbed", 0) or 0)
            breakdown = pet_result.get("absorbed_breakdown", [])
            total_incoming = remaining + absorbed
            if total_incoming > 0:
                pet_log = f"{source_name} hits {pet.name} ({pet.id}) for {total_incoming} damage."
                if absorbed > 0:
                    pet_log = f"{pet_log} {absorb_suffix(absorbed, breakdown).strip()}"
                match.log.append(pet_log)
            if remaining > 0:
                totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
                totals["damage"] += remaining
            if pet.hp <= 0:
                del target.pets[pet.id]
                match.log.append(f"{pet.name} dies.")

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
                apply_damage(actor, actor, backlash, actor_sid, "Shadow Word: Death backlash", school="magical")
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
        if dealt > 0 and dot_data:
            target_sid = sids[1] if actor_sid == sids[0] else sids[0]
            target = match.state[target_sid]
            dot_id = dot_data.get("id")
            duration = int(dot_data.get("duration", 1) or 1)
            school = dot_data.get("school", "magical")
            if dot_data.get("from_dealt_damage"):
                tick_damage = max(1, int(dealt // max(1, duration)))
            else:
                dot_scaling = dot_data.get("scaling", {}) or {}
                dot_dice = dot_data.get("dice", {}) or {}
                dot_roll = roll(dot_dice.get("type", "d0"), r) if dot_dice.get("type") else 0
                if "atk" in dot_scaling:
                    tick_damage = base_damage(
                        modify_stat(match.state[actor_sid], "atk", match.state[actor_sid].stats.get("atk", 0)),
                        dot_scaling["atk"],
                        dot_roll,
                    )
                elif "int" in dot_scaling:
                    tick_damage = base_damage(
                        modify_stat(match.state[actor_sid], "int", match.state[actor_sid].stats.get("int", 0)),
                        dot_scaling["int"],
                        dot_roll,
                    )
                else:
                    tick_damage = int(dot_data.get("tick_damage", 0) or 0)
                tick_damage = max(1, int(tick_damage))
            if dot_id and refresh_dot_effect(target, dot_id, duration=duration, tick_damage=tick_damage, source_sid=actor_sid):
                for effect in target.effects:
                    if effect.get("id") == dot_id:
                        effect["school"] = school
                        break
                if dot_id == "dragon_roar_bleed":
                    target_class = CLASSES.get(target.build.class_id, {}).get("name", "target")
                    match.log.append(f"Dragon Roar applies bleed on {target_class}.")
                else:
                    match.log.append(f"{actor_sid[:5]} refreshes {effect_name(dot_id)} for {tick_damage} per turn.")
            elif dot_id:
                apply_effect_by_id(
                    target,
                    dot_id,
                    overrides={"duration": duration, "tick_damage": tick_damage, "source_sid": actor_sid, "school": school},
                )
                if dot_id == "dragon_roar_bleed":
                    target_class = CLASSES.get(target.build.class_id, {}).get("name", "target")
                    match.log.append(f"Dragon Roar applies bleed on {target_class}.")
                else:
                    match.log.append(f"{actor_sid[:5]} applies {effect_name(dot_id)} for {tick_damage} per turn.")

    run_pet_phase(
        match,
        r,
        apply_damage,
        absorb_suffix,
        stealth_targeting,
        should_miss_due_to_stealth,
        untargetable_miss_log,
        can_evasion_force_miss,
    )

    # End of turn processing for both players (DoTs, passives, duration ticks, regen)
    for sid in sids:
        ps = match.state[sid]
        opponent_sid = sids[1] if sid == sids[0] else sids[0]
        opponent = match.state[opponent_sid]
        end_summary = end_of_turn(ps, match.log, sid[:5])
        for source in end_summary.get("damage_sources", []):
            source_sid = source.get("source_sid")
            if not source_sid:
                continue
            damage = resolve_dot_tick(source_sid, sid, source)
            if damage <= 0:
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

        for source in end_summary.get("self_damage_sources", []):
            source_sid = source.get("source_sid")
            if not source_sid:
                continue
            damage = resolve_dot_tick(source_sid, sid, source)
            if damage <= 0:
                continue
            totals = match.combat_totals.setdefault(source_sid, {"damage": 0, "healing": 0})
            totals["damage"] += damage

        totals = match.combat_totals.setdefault(sid, {"damage": 0, "healing": 0})
        totals["healing"] += int(end_summary.get("healing_done", 0) or 0)

    trigger_shield_of_vengeance_explosion(sids[0], sids[1])
    trigger_shield_of_vengeance_explosion(sids[1], sids[0])

    for sid in sids:
        ps = match.state[sid]
        tick_player_effects(ps)
        tick_cooldowns(ps)

    cleanup_pets(match)

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


def debug_simulate_swipe_aoe_on_imps() -> Dict[str, Any]:
    """
    Debug/integration helper for the pets+AoE pipeline.

    Scenario:
    - p1 warlock summons 3 imps
    - p2 druid enters bear form and uses Swipe

    Verifies:
    - Swipe applies to champion + all imps
    - imps lose HP (not instantly removed)
    - logs are in deterministic order (champion line first, then imp_1..imp_3)
    - pets are removed only when hp <= 0 (checked by repeatedly swiping until at least one imp dies)
    """
    p1 = "p1_warlock"
    p2 = "p2_druid"
    match = MatchState(room_id="debug", players=[p1, p2], phase="combat", seed=1337)
    match.picks[p1] = PlayerBuild(class_id="warlock")
    match.picks[p2] = PlayerBuild(class_id="druid")
    apply_prep_build(match)

    scripted_turns = [
        ({"ability_id": "summon_imp"}, {"ability_id": "bear"}),
        ({"ability_id": "summon_imp"}, {"ability_id": "bear"}),
        ({"ability_id": "summon_imp"}, {"ability_id": "bear"}),
        ({"ability_id": "healthstone"}, {"ability_id": "swipe"}),
    ]

    for a1, a2 in scripted_turns:
        submit_action(match, p1, a1)
        submit_action(match, p2, a2)
        resolve_turn(match)

    warlock = match.state[p1]
    assert len(warlock.pets) == 3, "Expected 3 imps after first swipe (no instant AoE removal)."

    imp_ids = sorted(warlock.pets.keys())
    imp_hps = [warlock.pets[pid].hp for pid in imp_ids]
    imp_max = [warlock.pets[pid].hp_max for pid in imp_ids]
    assert all(hp < mx for hp, mx in zip(imp_hps, imp_max)), "Expected each imp to lose HP from Swipe."

    swipe_hits = [line for line in match.log if "Swipe hits" in line]
    assert swipe_hits, "Expected Swipe per-target pet hit logs."
    expected_order = [f"p1_imp_{idx}" for idx in (1, 2, 3)]
    observed = []
    for line in swipe_hits:
        for pet_id in expected_order:
            if f"({pet_id})" in line:
                observed.append(pet_id)
    assert observed[:3] == expected_order, "Expected deterministic pet hit order: imp_1, imp_2, imp_3."

    # Keep swiping with cooldown reset until at least one imp dies.
    # This validates removal only at hp <= 0 through damage application.
    imp_died = False
    for _ in range(20):
        druid = match.state[p2]
        druid.cooldowns["swipe"] = []
        submit_action(match, p1, {"ability_id": "healthstone"})
        submit_action(match, p2, {"ability_id": "swipe"})
        resolve_turn(match)
        if any("Imp dies." in line for line in match.log[-20:]):
            imp_died = True
            break

    assert imp_died, "Expected at least one imp death after repeated Swipe damage."

    return {
        "turn": match.turn,
        "remaining_imps": sorted(match.state[p1].pets.keys()),
        "recent_logs": match.log[-20:],
    }


def debug_simulate_swipe_vs_immune_champion_pets() -> Dict[str, Any]:
    """
    Debug helper validating AoE routing when the champion is immune_all.

    Scenario:
    - p1 warlock summons 3 imps
    - p2 druid enters bear form
    - p1 champion receives Ice Block (immune_all)
    - p2 uses Swipe

    Verifies:
    - champion takes 0 HP damage from Swipe due to immune_all
    - each imp takes the same AoE incoming damage for that cast
    - deterministic pet ordering and no premature pet removal
    """
    p1 = "p1_warlock"
    p2 = "p2_druid"
    match = MatchState(room_id="debug", players=[p1, p2], phase="combat", seed=4242)
    match.picks[p1] = PlayerBuild(class_id="warlock")
    match.picks[p2] = PlayerBuild(class_id="druid")
    apply_prep_build(match)

    for _ in range(3):
        submit_action(match, p1, {"ability_id": "summon_imp"})
        submit_action(match, p2, {"ability_id": "bear"})
        resolve_turn(match)

    warlock = match.state[p1]
    assert len(warlock.pets) == 3, "Expected 3 imps before immune AoE test."

    apply_effect_by_id(warlock, "iceblock", overrides={"duration": 1})
    hp_before = warlock.res.hp
    imp_ids = sorted(warlock.pets.keys())
    imp_hp_before = {pid: warlock.pets[pid].hp for pid in imp_ids}

    submit_action(match, p1, {"ability_id": "healthstone"})
    submit_action(match, p2, {"ability_id": "swipe"})
    resolve_turn(match)

    warlock_after = match.state[p1]
    assert warlock_after.res.hp == hp_before, "Expected champion HP damage to be 0 under immune_all."

    imp_deltas = []
    for pid in imp_ids:
        assert pid in warlock_after.pets, "Expected imps to remain unless hp <= 0."
        delta = imp_hp_before[pid] - warlock_after.pets[pid].hp
        imp_deltas.append(delta)

    assert all(delta > 0 for delta in imp_deltas), "Expected each imp to take AoE damage."
    assert len(set(imp_deltas)) == 1, "Expected each imp to take the same AoE incoming damage."

    swipe_hits = [line for line in match.log if "Swipe hits" in line]
    observed = []
    for line in swipe_hits:
        for pet_id in imp_ids:
            if f"({pet_id})" in line:
                observed.append(pet_id)
    assert observed[:3] == imp_ids, "Expected deterministic pet hit order."

    return {
        "champion_hp_before": hp_before,
        "champion_hp_after": warlock_after.res.hp,
        "pet_damage": {pid: imp_deltas[idx] for idx, pid in enumerate(imp_ids)},
        "recent_logs": match.log[-20:],
    }
