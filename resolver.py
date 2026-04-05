# games/duel/engine/resolver.py
import json
from typing import Dict, Any, Tuple
from .models import MatchState, PlayerState, PlayerBuild, Resources, PetState
from .dice import rng_for, roll
from .rules import base_damage, hit_chance, clamp
from ..content.abilities import ABILITIES
from ..content.classes import CLASSES, class_display_name, normalize_class_id
from ..content.items import ITEMS
from ..content.balance import DEFAULTS, CAPS
from ..content.pets import PETS


def normalize_command_input(text: object) -> str:
    """Normalize player-entered command text to canonical lookup form."""
    if not isinstance(text, str):
        return ""
    return "_".join(text.strip().lower().split())


def normalize_player_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize player-entered action payloads without changing internal canonical ids."""
    if not isinstance(action, dict):
        return {}
    normalized = dict(action)
    if "ability_id" in normalized:
        normalized["ability_id"] = normalize_command_input(normalized.get("ability_id"))
    return normalized
from .pet_ai import run_pet_phase, cleanup_pets, prepare_pet_pre_action_effects, trigger_pre_action_special

# Centralized mechanics (passives/DoTs/mitigation/regen) live here.
from .effects import (
    trigger_on_hit_passives,
    damage_multiplier_from_passives,
    resource_gain_multiplier_from_passives,
    end_of_turn,
    end_of_turn_pet,
    apply_effect_by_id,
    apply_form,
    break_stealth_on_damage,
    has_effect,
    remove_effect,
    remove_stealth,
    modify_stat,
    mitigate_damage,
    is_stunned,
    cannot_act,
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
    effect_has_tag,
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


def validated_class_id(class_id: object) -> str:
    normalized = normalize_class_id(class_id)
    if not normalized:
        attempted = str(class_id).strip() if class_id is not None else ""
        if attempted:
            raise ValueError(f"unknown class_id '{attempted}'")
        raise ValueError("missing class_id")
    return normalized


def validated_class_data(class_id: object) -> tuple[str, Dict[str, Any]]:
    normalized = validated_class_id(class_id)
    return normalized, CLASSES[normalized]

def apply_prep_build(match: MatchState) -> None:
    """
    Called once when both players have selected class + items.
    Creates PlayerState.res/stats from content + item modifiers.
    """
    prepared_builds: Dict[str, tuple[PlayerBuild, Dict[str, Any]]] = {}
    for sid in match.players:
        pick = match.picks.get(sid)
        if isinstance(pick, PlayerBuild):
            build = pick
        else:
            payload = pick or {}
            build = PlayerBuild(class_id=payload.get("class_id"))
            build.items.update(payload.get("items", {}))

        build.class_id, class_data = validated_class_data(build.class_id)
        prepared_builds[sid] = (build, class_data)

    match.combat_totals = {sid: {"damage": 0, "healing": 0} for sid in match.players}
    match.state = {}
    for sid in match.players:
        build, class_data = prepared_builds[sid]
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
    match.submitted[sid] = normalize_player_action(action)

def ready_to_resolve(match: MatchState) -> bool:
    return len(match.submitted) == 2

def resolution_key(match: MatchState) -> str:
    sids = match.players
    return json.dumps(
        {
            "turn": match.turn,
            "actions": {
                sid: match.submitted.get(sid, {})
                for sid in sids
            },
        },
        sort_keys=True,
    )

def resolve_turn(match: MatchState) -> None:
    """
    Resolves both submitted actions simultaneously.
    Appends to match.log and updates match.state.
    Clears submissions and increments match.turn.
    """
    sids = match.players
    payload_key = resolution_key(match)
    if match.last_resolved_key == payload_key:
        return
    if len(match.submitted) < len(sids):
        return

    match.turn_in_progress = True

    r = rng_for(match.seed, match.turn)
    a1 = match.submitted.get(sids[0], {})
    a2 = match.submitted.get(sids[1], {})

    # Resolution layer: pre_action_state
    def _capture_pre_action_state() -> tuple[Dict[str, bool], Dict[str, bool], Dict[str, bool]]:
        stunned_snapshot = {sid: is_stunned(match.state[sid]) for sid in sids}
        stealth_snapshot = {sid: is_stealthed(match.state[sid]) for sid in sids}
        return stunned_snapshot, stealth_snapshot, dict(stealth_snapshot)

    stunned_at_start, stealth_start_at_turn_begin, stealth_targeting = _capture_pre_action_state()
    match.log.append(f"Turn {match.turn + 1}")

    def can_pay_costs(ps: PlayerState, costs: Dict[str, int]) -> Tuple[bool, str]:
        res = ps.res
        for key, value in costs.items():
            current = getattr(res, key)
            if current < value:
                return False, str(key)
        return True, ""

    def resource_failure_log(actor_sid: str, ability_name: str, resource_key: str) -> str:
        resource_name = str(resource_key or "resource").replace("_", " ").lower()
        if resource_name == "mp":
            resource_name = "mana"
        return f"{sid_token(actor_sid)} tried to use {ability_name} but didn't have enough {resource_name}"

    def sid_token(sid: str) -> str:
        return sid[:5]

    def effect_name(effect_id: str) -> str:
        return effect_id.replace("_", " ").title()

    def break_on_damage_effect_name(effect: Dict[str, Any]) -> str:
        source_ability_name = effect.get("source_ability_name")
        if source_ability_name:
            return str(source_ability_name)
        effect_id = effect.get("id")
        template = effect_template(effect_id) if effect_id else {}
        return str(effect.get("name") or template.get("name") or effect_name(effect_id or "effect"))

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

    def aoe_untargetable_resolution(target: PlayerState) -> tuple[bool, str | None]:
        if not has_flag(target, "untargetable"):
            return False, None
        return True, untargetable_miss_log(target)

    def grant_resource(player: PlayerState, resource: str, base_amount: int) -> int:
        if not player.res or not hasattr(player.res, resource):
            return 0
        amount = max(0, int(base_amount or 0))
        if amount <= 0:
            return 0
        multiplier = resource_gain_multiplier_from_passives(player, resource)
        adjusted = int(amount * multiplier)
        if adjusted <= 0:
            return 0
        current = getattr(player.res, resource)
        cap = getattr(player.res, f"{resource}_max", current)
        new_value = max(0, min(current + adjusted, cap))
        gained = new_value - current
        setattr(player.res, resource, new_value)
        return gained

    def entity_log_label(target: PlayerState | PetState) -> str:
        if isinstance(target, PlayerState):
            return sid_token(target.sid)
        return getattr(target, "name", "Target")

    def flag_source_name(target: PlayerState | PetState, flag: str, fallback: str = "that effect") -> str:
        for effect in reversed(target.effects):
            flags = effect.get("flags", {}) or {}
            if not flags.get(flag):
                continue
            if effect.get("name"):
                return str(effect["name"])
            effect_id = effect.get("id")
            if effect_id:
                return effect_name(effect_id)
            break
        return fallback

    def target_immune_log(log_parts: list) -> None:
        log_parts.append("Target is immune!")

    def apply_self_inflicted_magical_damage(ps: PlayerState, incoming: int) -> int:
        value = max(0, int(incoming or 0))
        if value <= 0:
            return 0
        if is_immune_all(ps):
            return 0
        if has_flag(ps, "cycloned"):
            return 0
        if has_effect(ps, "cloak_of_shadows"):
            return 0
        remaining, _, _ = consume_absorbs(ps, value)
        if remaining <= 0:
            return 0
        ps.res.hp -= remaining
        was_stealthed = is_stealthed(ps)
        break_stealth_on_damage(ps, remaining)
        if was_stealthed and not is_stealthed(ps):
            match.log.append(f"{sid_token(ps.sid)} stealth broken by Mindgames.")
        if current_form_id(ps) == "bear_form":
            grant_resource(ps, "rage", remaining)
        return remaining

    def apply_effect_entries(
        actor: PlayerState,
        target: PlayerState | PetState,
        ability: Dict[str, Any],
        log_parts: list[str],
        pre_log_parts: list[str] | None = None,
        extra_log_parts: list[str] | None = None,
        skip_self_effect_ids: set[str] | None = None,
        skip_primary_target: bool = False,
    ) -> None:
        def format_entry_log(message: str | None) -> str | None:
            if not isinstance(message, str):
                return message
            return message.replace("{actor}", sid_token(actor.sid))

        if pre_log_parts is None:
            pre_log_parts = []
        if extra_log_parts is None:
            extra_log_parts = []
        skip_self_effect_ids = skip_self_effect_ids or set()
        target_entries: list[PlayerState | PetState] = []
        if not skip_primary_target:
            target_entries.append(target)
        if ability_target_mode(ability) == "aoe_enemy" and isinstance(target, PlayerState):
            target_entries.extend(target.pets[pet_id] for pet_id in sorted((target.pets or {}).keys()))
        
        def maybe_redirect_target_effect(
            entry_target: PlayerState | PetState,
            entry: Dict[str, Any],
        ) -> tuple[PlayerState | PetState, str | None]:
            if ability_target_mode(ability) == "aoe_enemy":
                return entry_target, None
            if not isinstance(entry_target, PlayerState):
                return entry_target, None
            if entry_target is actor:
                return entry_target, None
            if not is_harmful_target_effect_entry(entry):
                return entry_target, None
            if not has_flag(entry_target, "redirect_single_target_to_pet"):
                return entry_target, None
            redirect_effect = next(
                (
                    fx for fx in reversed(entry_target.effects)
                    if (fx.get("flags", {}) or {}).get("redirect_single_target_to_pet")
                ),
                None,
            )
            pet_id = (redirect_effect or {}).get("redirect_to_pet_id")
            pet = entry_target.pets.get(pet_id) if pet_id else None
            if not pet or pet.hp <= 0:
                return entry_target, None
            return pet, f"{pet.name} intercepts {ability.get('name', 'the effect')} for {sid_token(entry_target.sid)}."
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
                    log_parts.append(f"{sid_token(actor.sid)} dispels {removed}{school_text} effects.")
                continue
            effect_id = entry.get("id")
            if not effect_id:
                continue
            if effect_id in skip_self_effect_ids:
                if entry.get("log"):
                    formatted_log = format_entry_log(entry.get("log"))
                    if entry.get("separate_log"):
                        pre_log_parts.append(formatted_log)
                    else:
                        log_parts.append(formatted_log)
                continue
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            if "source_ability_name" not in overrides:
                overrides["source_ability_name"] = ability.get("name") or effect_name(entry["id"])
            if "school" not in overrides and ability.get("school"):
                overrides["school"] = ability.get("school")
            if "subschool" not in overrides and ability.get("subschool"):
                overrides["subschool"] = ability.get("subschool")
            if effect_id in FORM_EFFECT_IDS:
                apply_form(actor, effect_id, overrides=overrides or None)
            else:
                apply_effect_by_id(actor, effect_id, overrides=overrides or None)
            if entry.get("log"):
                formatted_log = format_entry_log(entry.get("log"))
                if entry.get("separate_log"):
                    pre_log_parts.append(formatted_log)
                else:
                    log_parts.append(formatted_log)
        for entry in ability.get("target_effects", []) or []:
            if entry.get("type") == "remove_effect":
                effect_id = entry.get("effect_id")
                removed_targets: list[PlayerState | PetState] = []
                for entry_target in target_entries:
                    if effect_id and has_effect(entry_target, effect_id):
                        if effect_id == "stealth":
                            remove_stealth(entry_target)
                        else:
                            remove_effect(entry_target, effect_id)
                        removed_targets.append(entry_target)
                if removed_targets and entry.get("log"):
                    log_parts.append(entry["log"])
                removed_log_template = entry.get("removed_log_template")
                if removed_log_template:
                    for removed_target in removed_targets:
                        extra_log_parts.append(
                            str(removed_log_template).format(
                                target=entity_log_label(removed_target),
                                effect=effect_name(effect_id) if effect_id else "Effect",
                                source_ability=ability.get("name") or "Effect",
                            )
                        )
                continue
            overrides = dict(entry.get("overrides", {}) or {})
            if entry.get("duration"):
                overrides["duration"] = int(entry.get("duration"))
            if "source_ability_name" not in overrides:
                overrides["source_ability_name"] = ability.get("name") or effect_name(entry["id"])
            if "school" not in overrides and ability.get("school"):
                overrides["school"] = ability.get("school")
            if "subschool" not in overrides and ability.get("subschool"):
                overrides["subschool"] = ability.get("subschool")
            effect = build_effect(entry["id"], overrides=overrides or None)
            applied_any = False
            for entry_target in target_entries:
                resolved_target, redirect_log = maybe_redirect_target_effect(entry_target, entry)
                if redirect_log:
                    extra_log_parts.append(redirect_log)
                if single_target_miss_active(entry_target, ability) and is_harmful_target_effect_entry(entry):
                    log_parts.append(single_target_miss_log())
                    continue
                if is_immune_all(resolved_target):
                    target_immune_log(log_parts)
                    continue
                if target_effect_requires_visible_target(ability, entry) and is_stealthed(resolved_target):
                    log_parts.append("Target is stealthed — no valid target. Miss!")
                    continue
                if has_effect(resolved_target, "cloak_of_shadows") and is_magical_harmful_effect(effect):
                    log_parts.append("Immune!")
                    continue
                if entry["id"] in FORM_EFFECT_IDS:
                    apply_form(resolved_target, entry["id"], overrides=overrides or None)
                else:
                    apply_effect_by_id(resolved_target, entry["id"], overrides=overrides or None)
                applied_any = True
            if applied_any and entry.get("log"):
                log_parts.append(entry["log"])

    def _resolve_effect_application(
        actor_sid: str,
        actor: PlayerState,
        target: PlayerState | PetState,
        ability_id: str,
        ability: Dict[str, Any],
        log_parts: list[str],
        *,
        pre_log_parts: list[str] | None = None,
        extra_log_parts: list[str] | None = None,
        skip_self_effect_ids: set[str] | None = None,
        skip_primary_target: bool = False,
        apply_entries: bool = True,
        apply_defensive_effect_template: bool = False,
        has_damage: bool = False,
        has_target_effects: bool = False,
        include_non_damaging_summon: bool = False,
        allow_summon_with_target_effects: bool = False,
        set_cooldown_on_summon: bool = True,
        return_on_summon_error: bool = True,
    ) -> Dict[str, Any] | None:
        # effect_application: buff_application / debuff_application / dot_application /
        # hot_application / proc_grant / dispel_application / effect_refresh / effect_removal
        if apply_defensive_effect_template and ability.get("effect"):
            effect = dict(ability["effect"])
            effect["duration"] = int(effect.get("duration", 1))
            apply_effect_by_id(
                actor,
                "item_passive_template",
                overrides={"type": effect.get("type", "status"), **effect},
            )
            log_parts.append("Defensive stance raised.")
        else:
            has_self_effects = bool(ability.get("self_effects"))
            if apply_entries and (has_self_effects or has_target_effects):
                apply_effect_entries(
                    actor,
                    target,
                    ability,
                    log_parts,
                    pre_log_parts=pre_log_parts,
                    extra_log_parts=extra_log_parts,
                    skip_self_effect_ids=skip_self_effect_ids,
                    skip_primary_target=skip_primary_target,
                )

        summon_pet_id = ability.get("summon_pet_id")
        allow_summon = (
            include_non_damaging_summon
            and summon_pet_id
            and (
                allow_summon_with_target_effects
                or (not has_damage and not has_target_effects)
            )
        )
        if allow_summon:
            summoned_pet, refreshed, summon_error = summon_pet_from_template(actor, summon_pet_id)
            template = PETS.get(summon_pet_id, {})
            if summon_error:
                if return_on_summon_error:
                    return {"damage": 0, "healing": 0, "log": summon_error, "ability_id": ability_id}
                log_parts.append(summon_error)
                return None
            max_count = int(template.get("max_count", 1) or 1)
            current = len([p for p in actor.pets.values() if p.template_id == summon_pet_id])
            summon_log = ability.get("summon_log")
            force_call_wording = summon_pet_id in {"barrens_boar", "frostsaber", "emerald_serpent"} and bool(summon_log)
            if refreshed:
                if force_call_wording:
                    log_parts.append(str(summon_log))
                else:
                    log_parts.append(f"refreshes {template.get('name', summon_pet_id.title())}.")
            elif max_count > 1:
                log_parts.append(f"summons a {template.get('name', summon_pet_id.title())} ({current}/{max_count}).")
            elif summoned_pet is not None:
                if summon_log:
                    log_parts.append(str(summon_log))
                else:
                    remembered = actor.hunter_pet_memory.get(summon_pet_id)
                    if remembered is not None:
                        log_parts.append(f"summons {template.get('name', summon_pet_id.title())} with {summoned_pet.hp}/{summoned_pet.hp_max} HP.")
                    else:
                        log_parts.append(f"summons {template.get('name', summon_pet_id.title())}.")
            if summoned_pet is not None and not refreshed:
                trigger_pre_action_special(actor, summoned_pet, actor_sid, match, r, consume_action=True)
            remove_effect(actor, summon_pet_id)
            if set_cooldown_on_summon:
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        return None

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

        class_name = class_display_name(actor.build.class_id, default="Actor")
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
        effect_id = entry.get("id")
        if not effect_id:
            return False
        effect = build_effect(effect_id, overrides=entry.get("overrides"))
        flags = effect.get("flags", {}) or {}
        if flags.get("stunned"):
            return True
        reason = effect.get("cant_act_reason")
        return reason in {"stunned", "feared", "frozen"}

    def can_evasion_force_miss(ability: Dict[str, Any], has_damage: bool) -> bool:
        if not has_damage:
            return False
        school = normalize_school(ability.get("school") or ability.get("damage_type") or "physical") or "physical"
        return (
            school == "physical"
            and not is_aoe_ability(ability)
            and is_single_target_ability(ability)
        )

    def can_cast_while_cc(ability: Dict[str, Any], *, reason: str | None = None, incoming_cc: bool = False) -> bool:
        if ability.get("allow_while_stunned"):
            return True
        if not ability.get("priority_defensive"):
            return False
        return incoming_cc and reason == "stunned"

    def is_harmful_target_effect_entry(entry: Dict[str, Any]) -> bool:
        effect_id = entry.get("id")
        if not effect_id:
            return False
        effect = build_effect(effect_id, overrides=entry.get("overrides"))
        if not effect:
            return False
        flags = effect.get("flags", {}) or {}
        return bool(is_magical_harmful_effect(effect) or effect.get("harmful") or flags.get("stunned") or effect.get("cant_act_reason"))

    def is_harmful_single_target_action(ability: Dict[str, Any]) -> bool:
        if not is_single_target_ability(ability):
            return False
        if any(value for value in (ability.get("dice"), ability.get("scaling"), ability.get("flat_damage"))):
            return True
        return any(is_harmful_target_effect_entry(entry) for entry in (ability.get("target_effects") or []))

    def single_target_miss_active(target: PlayerState, ability: Dict[str, Any]) -> bool:
        return has_flag(target, "incoming_single_target_miss") and is_harmful_single_target_action(ability)

    def single_target_miss_log() -> str:
        return "Target evades the attack — Miss!"

    def dismiss_pet(actor: PlayerState, pet_id: str, *, remember: bool = False) -> None:
        pet = (actor.pets or {}).get(pet_id)
        if not pet:
            return
        template = PETS.get(pet.template_id, {})
        if remember and template.get("persistent_owner_memory"):
            actor.hunter_pet_memory[pet.template_id] = max(0, int(pet.hp))
        if actor.active_pet_id == pet_id:
            actor.active_pet_id = None
        del actor.pets[pet_id]

    def handle_pet_defeat(owner: PlayerState, pet: PetState) -> None:
        template = PETS.get(pet.template_id, {})
        if template.get("permanent_death"):
            owner.dead_hunter_pets[pet.template_id] = True
            owner.hunter_pet_memory[pet.template_id] = 0
        if owner.active_pet_id == pet.id:
            owner.active_pet_id = None
        if pet.id in owner.pets:
            del owner.pets[pet.id]
        match.log.append(f"{pet.name} dies.")

    def summon_pet_from_template(actor: PlayerState, template_id: str) -> tuple[PetState | None, bool, str | None]:
        template = PETS.get(template_id, {})
        max_count = int(template.get("max_count", 1) or 1)
        existing_ids = [pid for pid, pet in (actor.pets or {}).items() if pet.template_id == template_id]
        if existing_ids and max_count <= 1:
            existing = actor.pets[existing_ids[0]]
            existing.hp_max = int(template.get("hp", existing.hp_max))
            duration = template.get("duration")
            existing.duration = int(duration) if duration is not None else None
            existing.name = template.get("name", existing.name)
            actor.active_pet_id = existing.id
            return existing, True, None

        summon_group = template.get("summon_group")
        if template.get("permanent_death") and actor.dead_hunter_pets.get(template_id):
            pet_name = template.get("name", template_id.title())
            return None, False, f"{pet_name} has fallen and cannot be summoned again this match."

        if summon_group:
            for pet_id, pet in list((actor.pets or {}).items()):
                active_template = PETS.get(pet.template_id, {})
                if pet_id == actor.active_pet_id and active_template.get("summon_group") == summon_group:
                    dismiss_pet(actor, pet_id, remember=True)

        owner_idx = match.players.index(actor.sid) + 1
        if template_id == "imp":
            next_idx = 1
            while f"p{owner_idx}_imp_{next_idx}" in actor.pets:
                next_idx += 1
            pet_id = f"p{owner_idx}_imp_{next_idx}"
        else:
            suffix = template_id.replace("-", "_")
            pet_id = f"p{owner_idx}_{suffix}"
            if pet_id in actor.pets:
                next_idx = 2
                while f"{pet_id}_{next_idx}" in actor.pets:
                    next_idx += 1
                pet_id = f"{pet_id}_{next_idx}"

        duration = template.get("duration")
        hp_max = int(template.get("hp", 1))
        remembered_hp = actor.hunter_pet_memory.get(template_id)
        summon_hp = hp_max
        if template.get("persistent_owner_memory") and remembered_hp is not None:
            summon_hp = hp_max if int(remembered_hp) <= 0 else min(hp_max, int(remembered_hp))
        summoned = PetState(
            id=pet_id,
            template_id=template_id,
            name=template.get("name", template_id.title()),
            owner_sid=actor.sid,
            hp=summon_hp,
            hp_max=hp_max,
            effects=[],
            duration=int(duration) if duration is not None else None,
        )
        actor.pets[summoned.id] = summoned
        if summon_group:
            actor.active_pet_id = summoned.id
        return summoned, False, None

    def summon_cap_reached(actor: PlayerState, template_id: str) -> bool:
        template = PETS.get(template_id, {})
        max_count = int(template.get("max_count", 1) or 1)
        current = [p for p in (actor.pets or {}).values() if p.template_id == template_id]
        if max_count <= 1 and current:
            return False
        return len(current) >= max_count

    # Resolution layer: action_selection_modifiers
    def _resolve_action_selection_modifiers(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"resolved": True, "damage": 0, "healing": 0, "log": f"{sid_token(actor_sid)} fumbles (unknown ability)."}

        allowed_classes = ability.get("classes")
        if allowed_classes and actor.build.class_id not in allowed_classes:
            return {"resolved": True, "damage": 0, "healing": 0, "log": f"{sid_token(actor_sid)} cannot use {ability['name']}."}

        weapon_id = None
        if actor.build and actor.build.items:
            weapon_id = actor.build.items.get("weapon")

        if is_on_cooldown(actor, ability_id, ability):
            return {
                "resolved": True,
                "damage": 0,
                "healing": 0,
                "log": f"{sid_token(actor_sid)} tried to use {ability['name']} but it is on cooldown.",
            }

        required_form = ability.get("requires_form")
        if required_form and current_form_id(actor) != required_form:
            required_form_name = effect_name(required_form)
            return {
                "resolved": True,
                "damage": 0,
                "healing": 0,
                "log": f"{class_display_name(actor.build.class_id)} tried to use {ability['name']} but wasn't in {required_form_name}.",
            }

        required_effect = ability.get("requires_effect")
        if required_effect and not has_effect(actor, required_effect):
            return {
                "resolved": True,
                "damage": 0,
                "healing": 0,
                "log": f"{ability['name']} requires {effect_name(required_effect)}.",
            }

        required_weapon = ability.get("requires_weapon")
        if required_weapon and weapon_id != required_weapon:
            return {
                "resolved": True,
                "damage": 0,
                "healing": 0,
                "log": ability.get("requires_weapon_log", "The required weapon is not equipped."),
            }

        target_hp_threshold = ability.get("requires_target_hp_below")
        if target_hp_threshold is not None:
            if target.res.hp / max(1, target.res.hp_max) >= float(target_hp_threshold):
                return {
                    "resolved": True,
                    "damage": 0,
                    "healing": 0,
                    "log": f"{ability['name']} can only be used as an execute.",
                }

        if ability.get("requires_circle") and not has_circle(actor):
            return {"resolved": True, "damage": 0, "healing": 0, "log": "Demonic Circle is required."}

        if ability_id == "agony" and has_effect(target, "agony"):
            return {"resolved": True, "damage": 0, "healing": 0, "log": "Agony is not stackable."}

        summon_pet_id = ability.get("summon_pet_id")
        if summon_pet_id and summon_cap_reached(actor, summon_pet_id):
            template = PETS.get(summon_pet_id, {})
            max_count = int(template.get("max_count", 1) or 1)
            return {
                "resolved": True,
                "damage": 0,
                "healing": 0,
                "log": f"{max_count} {template.get('name', summon_pet_id)} Maximum",
            }

        ok, fail_reason = can_pay_costs(actor, ability.get("cost", {}))
        if not ok:
            return {
                "resolved": True,
                "damage": 0,
                "healing": 0,
                "log": resource_failure_log(actor_sid, ability["name"], fail_reason),
            }

        return {
            "resolved": False,
            "actor": actor,
            "target": target,
            "ability_id": ability_id,
            "ability": ability,
            "weapon_id": weapon_id,
        }

    # Resolution layer: action_denial
    def _resolve_action_denial(
        actor_sid: str,
        ability: Dict[str, Any],
        *,
        incoming_cc: bool,
        start_locked: bool = False,
        include_runtime_cant_act: bool = True,
    ) -> Dict[str, Any] | None:
        actor = match.state[actor_sid]
        actor_cannot_act = start_locked or (include_runtime_cant_act and cannot_act(actor))
        if not actor_cannot_act:
            return None
        reason = get_cant_act_reason(actor)
        if can_cast_while_cc(ability, reason=reason, incoming_cc=incoming_cc):
            return None
        if has_flag(actor, "cycloned"):
            reason_text = "is Cycloned and cannot act"
        elif reason:
            reason_text = f"is {reason} and cannot act"
        else:
            reason_text = "cannot act"
        return {
            "damage": 0,
            "healing": 0,
            "log": f"{sid_token(actor_sid)} tries to use {ability['name']} but {reason_text}.",
        }

    # Resolution layer: pre_resolution_protection
    def _resolve_pre_resolution_protection(
        attacker: PlayerState,
        target: PlayerState | PetState,
        ability: Dict[str, Any],
        *,
        stealth_snapshot: Dict[str, bool],
    ) -> tuple[bool, str | None]:
        if should_miss_due_to_stealth(attacker, target, ability, stealth_snapshot):
            return True, "Target is stealthed — Miss!"
        return False, None

    # Resolution layer: hit_resolution
    def _resolve_hit_resolution(
        target: PlayerState | PetState,
        ability: Dict[str, Any],
        *,
        has_damage: bool,
        is_aoe: bool,
    ) -> tuple[bool, str | None, bool, str | None]:
        aoe_untargetable, aoe_untargetable_log = aoe_untargetable_resolution(target)
        if aoe_untargetable:
            if is_aoe:
                return False, None, True, aoe_untargetable_log
            return True, (aoe_untargetable_log or untargetable_miss_log(target)), False, None
        if not is_aoe and single_target_miss_active(target, ability):
            return True, single_target_miss_log(), False, None
        if has_flag(target, "evade_all") and can_evasion_force_miss(ability, has_damage):
            return True, "Evaded!", False, None
        return False, None, False, None


    def resolve_action(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        selection = _resolve_action_selection_modifiers(actor_sid, target_sid, action)
        if selection.get("resolved"):
            return {
                "damage": int(selection.get("damage", 0) or 0),
                "healing": int(selection.get("healing", 0) or 0),
                "log": str(selection.get("log") or ""),
            }

        actor = selection["actor"]
        target = selection["target"]
        ability_id = selection["ability_id"]
        ability = selection["ability"]
        weapon_id = selection["weapon_id"]

        weapon_name = ITEMS.get(weapon_id, {}).get("name", "their bare hands")

        denial = _resolve_action_denial(actor_sid, ability, incoming_cc=False)
        if denial:
            return denial

        if has_flag(actor, "disable_attacks") and "attack" in (ability.get("tags") or []):
            return {
                "damage": 0,
                "healing": 0,
                "log": f"{sid_token(actor_sid)} cannot attack while {flag_source_name(actor, 'disable_attacks')} is active.",
            }

        log_parts = [f"{sid_token(actor_sid)} uses {weapon_name} to cast {ability['name']}."]
        extra_logs: list[Any] = []
        pre_log_parts: list[str] = []

        has_target_effects = bool(ability.get("target_effects"))
        has_self_effects = bool(ability.get("self_effects"))
        is_aoe = is_aoe_ability(ability)
        if "pass" in (ability.get("tags") or []):
            set_cooldown(actor, ability_id, ability)
            log_parts.append("Passes the turn.")
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "pre_logs": pre_log_parts}

        was_stealthed = has_effect(actor, "stealth")
        offensive_action = is_offensive_action(ability)

        consume_costs(actor, ability.get("cost", {}))

        extra_logs: list[Any] = []
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
        aoe_skip_champion = False
        aoe_champion_log_override: str | None = None

        if has_damage or has_target_effects:
            blocked, protection_log = _resolve_pre_resolution_protection(
                actor,
                target,
                ability,
                stealth_snapshot=stealth_targeting,
            )
            if blocked:
                log_parts.append(protection_log or "Target is stealthed — Miss!")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}
            missed, miss_log, skip_aoe_champion, aoe_immune_log = _resolve_hit_resolution(
                target,
                ability,
                has_damage=has_damage,
                is_aoe=is_aoe,
            )
            if skip_aoe_champion:
                aoe_skip_champion = True
                aoe_champion_log_override = " ".join([*log_parts, aoe_immune_log or untargetable_miss_log(target)])
            if missed:
                log_parts.append(miss_log or "Miss!")
                set_cooldown(actor, ability_id, ability)
                if ability.get("consume_effect"):
                    remove_effect(actor, ability["consume_effect"])
                if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
                    remove_stealth(actor)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts)}

        effect_application_result = _resolve_effect_application(
            actor_sid,
            actor,
            target,
            ability_id,
            ability,
            log_parts,
            pre_log_parts=pre_log_parts,
            extra_log_parts=extra_logs,
            skip_primary_target=aoe_skip_champion,
            has_damage=has_damage,
            has_target_effects=has_target_effects,
            include_non_damaging_summon=True,
        )
        if effect_application_result is not None:
            return effect_application_result

        if ability_id == "healthstone":
            heal_value = max(1, int(actor.res.hp_max * 0.25))
            if has_effect(actor, "mindgames"):
                apply_self_inflicted_magical_damage(actor, heal_value)
                log_parts.append(f"Mindgames twists healing into {heal_value} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
            healed = actor.res.hp - before_hp
            log_parts.append(f"Healthstone restores {healed} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healed, "log": " ".join(log_parts), "ability_id": ability_id}

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
            dot_school = normalize_school(dot_data.get("school") or ability.get("school") or "magical") or "magical"
            dot_subschool = (dot_data.get("subschool") or ability.get("subschool")) if dot_school == "magical" else None
            if dot_id and refresh_dot_effect(
                target,
                dot_id,
                duration=duration,
                tick_damage=tick_damage,
                source_sid=actor.sid,
                school=dot_school,
                subschool=dot_subschool,
            ):
                log_parts.append(f"refreshes {effect_name(dot_id)}.")
            elif dot_id:
                apply_effect_by_id(
                    target,
                    dot_id,
                    overrides={
                        "duration": duration,
                        "tick_damage": tick_damage,
                        "source_sid": actor.sid,
                        "school": dot_school,
                        "subschool": dot_subschool,
                    },
                )
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
                overrides={"duration": 11, "tick_damage": 1, "source_sid": actor.sid, "dot_mode": "ramp", "skip_first_tick": True},
            )
            log_parts.append("inflicts Agony.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}

        if ability_id == "frenzied_regeneration":
            if actor.res.rage <= 0:
                return {"damage": 0, "healing": 0, "log": resource_failure_log(actor_sid, ability["name"], "rage")}
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
                apply_self_inflicted_magical_damage(actor, heal_value)
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
                apply_self_inflicted_magical_damage(actor, heal_value)
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
                apply_self_inflicted_magical_damage(actor, heal_value)
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
            missing_hp = max(0, actor.res.hp_max - actor.res.hp)
            if has_effect(actor, "mindgames"):
                apply_self_inflicted_magical_damage(actor, missing_hp)
                log_parts.append(f"Mindgames twists healing into {missing_hp} self-damage.")
                set_cooldown(actor, ability_id, ability)
                return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "ability_id": ability_id}
            actor.res.hp = actor.res.hp_max
            log_parts.append("Lay on Hands restores health to full.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": missing_hp, "log": " ".join(log_parts), "ability_id": ability_id}

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
            dot_template = effect_template(dot_id) if dot_id else {}
            lifesteal_pct = float(dot_template.get("lifesteal_pct", 0) or 0)
            dispellable = bool(dot_template.get("dispellable", False))
            dot_school = normalize_school(dot_data.get("school") or ability.get("school") or dot_template.get("school") or "magical") or "magical"
            dot_subschool = (
                dot_data.get("subschool") or ability.get("subschool") or dot_template.get("subschool")
            ) if dot_school == "magical" else None
            if dot_id and refresh_dot_effect(
                target,
                dot_id,
                duration=duration,
                tick_damage=tick_damage,
                source_sid=actor.sid,
                school=dot_school,
                subschool=dot_subschool,
            ):
                for fx in target.effects:
                    if fx.get("id") == dot_id:
                        fx["lifesteal_pct"] = lifesteal_pct
                        fx["dispellable"] = dispellable
                        break
                log_parts.append(f"refreshes {effect_name(dot_id)}.")
            elif dot_id:
                apply_effect_by_id(target, dot_id, overrides={
                    "duration": duration,
                    "tick_damage": tick_damage,
                    "source_sid": actor.sid,
                    "school": dot_school,
                    "subschool": dot_subschool,
                    "dispellable": dispellable,
                    "lifesteal_pct": lifesteal_pct,
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
                    apply_self_inflicted_magical_damage(actor, heal_value)
                    log_parts.append(f"Hit {hit_index}: Mindgames turns healing into {heal_value} self-damage.")
                    continue
                before_hp = actor.res.hp
                actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
                gained = actor.res.hp - before_hp
                healing += gained
                log_parts.append(f"Hit {hit_index}: Restores {gained} HP.")
            set_cooldown(actor, ability_id, ability)
            return {"damage": 0, "healing": healing, "log": " ".join(log_parts), "ability_id": ability_id}

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
            return {"damage": 0, "healing": 0, "log": " ".join(log_parts), "extra_logs": extra_logs}

        def _resolve_damage_modification(raw_damage: int) -> int:
            reduced_damage = mitigate_damage(
                raw_damage,
                target,
                ability_school,
                ignore_armor=bool(ability.get("ignore_armor") or ability.get("ignore_physical_reduction")),
                ignore_magic_resist=bool(ability.get("ignore_magic_resist")),
            )
            modified_damage = reduced_damage
            multiplier = damage_multiplier_from_passives(actor)
            if multiplier != 1.0:
                modified_damage = int(modified_damage * multiplier)
            if empower_multiplier != 1.0:
                modified_damage = int(modified_damage * empower_multiplier)
            if outgoing_mult != 1.0:
                modified_damage = int(modified_damage * outgoing_mult)
            if death_doubled and modified_damage > 0:
                modified_damage *= 2
            return modified_damage

        # Calculate base damage using appropriate stat
        ability_school = normalize_school(ability.get("school") or ability.get("damage_type") or "physical") or "physical"
        ability_subschool = ability.get("subschool") if ability_school == "magical" else None
        hits = int(ability.get("hits", 1) or 1)
        total_damage = 0
        passive_bonus_damage_total = 0
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

            if not ability.get("cannot_miss") and r.randint(1, 100) > accuracy:
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

            # Resolution layer: damage_modification
            incoming_for_hit = _resolve_damage_modification(raw)

            if ability_target_mode(ability) == "aoe_enemy" and incoming_for_hit > 0:
                aoe_incoming_damage += incoming_for_hit
                aoe_damage_instances.append(incoming_for_hit)

            if is_damage_immune(target, "physical" if ability_school == "physical" else "magic"):
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
                            if effect.get("separate_log"):
                                apply_effect_by_id(actor, effect["id"])
                                if effect.get("log"):
                                    pre_log_parts.append(str(effect["log"]).replace("{actor}", sid_token(actor_sid)))
                            else:
                                apply_effect_by_id(
                                    actor,
                                    effect["id"],
                                    log=match.log,
                                    label=sid_token(actor_sid),
                                    log_message=str(effect.get("log")).replace("{actor}", sid_token(actor_sid)) if effect.get("log") else None,
                                )
                for gain in ability.get("on_hit_resource_gains", []) or []:
                    chance = float(gain.get("chance", 1.0) or 1.0)
                    if r.random() > chance:
                        continue
                    resource = gain.get("resource")
                    amount = int(gain.get("amount", 0) or 0)
                    if not resource or amount <= 0 or not hasattr(actor.res, resource):
                        continue
                    gained = grant_resource(actor, resource, amount)
                    if gained > 0 and gain.get("log"):
                        log_parts.append(f"{prefix}{gain['log']}")

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

        def queue_passive_damage_events(events: list[Dict[str, Any]]) -> None:
            for event in events:
                incoming = int(event.get("incoming", 0) or 0)
                template = event.get("log_template")
                if incoming <= 0 or not template:
                    continue
                extra_logs.append(
                    {
                        "type": "damage_event",
                        "source_name": ability.get("name", "attack"),
                        "incoming": incoming,
                        "school": event.get("school", "physical"),
                        "subschool": event.get("subschool"),
                        "log_template": str(template),
                    }
                )

        # Apply non-strike-again on-hit passive effects once per ability execution.
        if ability_hit_landed:
            bonus_damage, passive_logs, bonus_healing, passive_damage_events = trigger_on_hit_passives(
                actor,
                target,
                on_hit_base_damage,
                ability_school,
                r,
                ability=ability,
                include_strike_again=False,
            )
            if bonus_damage > 0:
                passive_bonus_damage_total += bonus_damage
            if bonus_healing > 0:
                total_healing += bonus_healing
            if passive_logs:
                extra_logs.extend(passive_logs)
            queue_passive_damage_events(passive_damage_events)

        # Strike-again passives can proc per successful damaging strike.
        for strike_damage in per_hit_damage_values:
            strike_bonus_damage, strike_logs, _, strike_damage_events = trigger_on_hit_passives(
                actor,
                target,
                strike_damage,
                ability_school,
                r,
                ability=ability,
                include_strike_again=True,
                only_strike_again=True,
            )
            if strike_bonus_damage > 0:
                passive_bonus_damage_total += strike_bonus_damage
            if strike_logs:
                extra_logs.extend(strike_logs)
            queue_passive_damage_events(strike_damage_events)

        resource_gain = ability.get("resource_gain", {})
        total_effective_damage_for_resources = total_damage + passive_bonus_damage_total
        if total_effective_damage_for_resources > 0 and resource_gain:
            for resource, gain in resource_gain.items():
                if gain == "damage":
                    gain_value = total_effective_damage_for_resources
                elif gain == "damage_x3":
                    gain_value = total_effective_damage_for_resources * 3
                else:
                    gain_value = int(gain)
                if gain_value > 0 and hasattr(actor.res, resource):
                    grant_resource(actor, resource, gain_value)

        mindgames_flip_damage = bool(has_effect(actor, "mindgames") and total_damage > 0)

        if ability.get("consume_effect"):
            remove_effect(actor, ability["consume_effect"])
        if ability_id == "final_verdict" and has_effect(actor, "paladin_final_verdict_empowered"):
            remove_effect(actor, "paladin_final_verdict_empowered")
        if ability_id == "mind_blast" and has_effect(actor, "mind_blast_empowered"):
            remove_effect(actor, "mind_blast_empowered")

        if consume_empower and empower_multiplier != 1.0:
            remove_effect(actor, "crusader_empower")

        if ability.get("pet_command"):
            actor.pending_pet_command = ability["pet_command"]
        set_cooldown(actor, ability_id, ability)
        if offensive_action and stealth_start_at_turn_begin.get(actor_sid, False):
            remove_stealth(actor)

        damage_instances = [value for value in per_hit_damage_values if value > 0]
        if total_damage > sum(damage_instances):
            damage_instances.append(total_damage - sum(damage_instances))

        outgoing_damage = aoe_incoming_damage if ability_target_mode(ability) == "aoe_enemy" else total_damage
        if aoe_skip_champion and ability_target_mode(ability) == "aoe_enemy":
            outgoing_damage = 0
        outgoing_instances = aoe_damage_instances if ability_target_mode(ability) == "aoe_enemy" else damage_instances

        return {
            "damage": outgoing_damage,
            "damage_instances": outgoing_instances,
            "aoe_incoming_damage": aoe_incoming_damage,
            "damage_type": ability_school,
            "school": ability_school,
            "subschool": ability_subschool,
            "healing": total_healing,
            "log": aoe_champion_log_override or " ".join(log_parts),
            "pre_logs": pre_log_parts,
            "extra_logs": extra_logs,
            "ability_id": ability_id,
            "mindgames_flip_damage": mindgames_flip_damage,
            "skip_direct_target_damage": aoe_skip_champion,
        }

    def build_immediate_resolution(actor_sid: str, target_sid: str, action: Dict[str, Any]) -> Dict[str, Any]:
        ability_id = action.get("ability_id")
        ability = ABILITIES.get(ability_id)
        if not ability:
            return {"damage": 0, "log": f"{sid_token(actor_sid)} fumbles (unknown ability).", "resolved": True}

        denial = _resolve_action_denial(actor_sid, ability, incoming_cc=False)
        if denial:
            return {
                "damage": int(denial.get("damage", 0) or 0),
                "log": str(denial.get("log") or ""),
                "resolved": True,
            }

        selection = _resolve_action_selection_modifiers(actor_sid, target_sid, action)
        if selection.get("resolved"):
            return {
                "damage": int(selection.get("damage", 0) or 0),
                "log": str(selection.get("log") or ""),
                "resolved": True,
            }

        actor = selection["actor"]
        ability_id = selection["ability_id"]
        ability = selection["ability"]

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
    start_of_turn_cant_act = {sid: cannot_act(match.state[sid]) for sid in sids}

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
    for sid in sids:
        ctx = contexts[sid]
        if ctx.get("resolved"):
            continue
        pet_command = (ctx.get("ability") or {}).get("pet_command")
        if pet_command:
            match.state[sid].pending_pet_command = pet_command
    deferred_pet_pre_action_logs = prepare_pet_pre_action_effects(match, r)
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
        if single_target_miss_active(target, ability):
            return False
        if is_immune_all(target):
            return False
        for entry in target_effects:
            effect_id = entry.get("id")
            if not effect_id:
                continue
            effect = build_effect(effect_id, overrides=entry.get("overrides"))
            flags = effect.get("flags", {}) or {}
            if has_effect(target, "cloak_of_shadows") and is_magical_harmful_effect(effect):
                continue
            if flags.get("stunned") or effect.get("cant_act_reason"):
                return True
        return False

    incoming_immediate_stun = {
        sids[0]: immediate_action_can_stun(sids[1], sids[0], contexts[sids[1]]),
        sids[1]: immediate_action_can_stun(sids[0], sids[1], contexts[sids[0]]),
    }

    outgoing_immediate_stun = {
        sid: immediate_action_can_stun(sid, sids[1] if sid == sids[0] else sids[0], contexts[sid])
        for sid in sids
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
        log_parts = [f"{sid_token(actor_sid)} uses {weapon_name} to cast {ability['name']}."]
        extra_logs: list[Any] = []

        incoming_lock = incoming_immediate_stun.get(actor_sid, False) and not outgoing_immediate_stun.get(actor_sid, False)
        denial = _resolve_action_denial(
            actor_sid,
            ability,
            incoming_cc=incoming_lock,
            start_locked=start_of_turn_cant_act.get(actor_sid, False) or incoming_lock,
            include_runtime_cant_act=False,
        )
        if denial:
            ctx["damage"] = int(denial.get("damage", 0) or 0)
            ctx["log"] = str(denial.get("log") or "")
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

        _resolve_effect_application(
            actor_sid,
            actor,
            target,
            ability_id,
            ability,
            log_parts,
            extra_log_parts=extra_logs,
            skip_self_effect_ids=skip_self_effect_ids,
            apply_defensive_effect_template=True,
            has_target_effects=bool(ability.get("target_effects")),
            include_non_damaging_summon=True,
            allow_summon_with_target_effects=True,
            set_cooldown_on_summon=False,
            return_on_summon_error=False,
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
        ctx["extra_logs"] = extra_logs
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
    deferred_break_on_damage_logs: list[str] = []
    deferred_stealth_break_logs: list[str] = []

    def flush_deferred_stealth_break_logs() -> None:
        if not deferred_stealth_break_logs:
            return
        match.log.extend(deferred_stealth_break_logs)
        deferred_stealth_break_logs.clear()

    def break_effects_and_collect_labels(target_entity: PlayerState | PetState) -> list[str]:
        removed_labels: list[str] = []
        for effect in list(getattr(target_entity, "effects", []) or []):
            has_legacy_flag = (effect.get("flags", {}) or {}).get("break_on_damage")
            if not (effect_has_tag(effect, "break_on_damage") or has_legacy_flag):
                continue
            effect_id = effect.get("id")
            if not effect_id:
                continue
            removed_labels.append(break_on_damage_effect_name(effect))
            remove_effect(target_entity, effect_id)
        return removed_labels

    def apply_damage(
        source: PlayerState,
        target: PlayerState | PetState,
        incoming: int,
        target_sid: str,
        source_ability_name: str,
        mindgames_flip_damage: bool = False,
        damage_instances: list[int] | None = None,
        school: str = "physical",
        subschool: str | None = None,
        allow_redirect: bool = True,
    ) -> Dict[str, Any]:
        # Resolution layer: target_resolution
        def _resolve_target_resolution(
            target_entity: PlayerState | PetState,
            target_label: str,
        ) -> tuple[PlayerState | PetState, str, bool, str | None, str | None]:
            is_player_entity = hasattr(target_entity, "res") and target_entity.res is not None
            redirected_pet_name: str | None = None
            redirect_log: str | None = None
            if is_player_entity and allow_redirect and has_flag(target_entity, "redirect_single_target_to_pet"):
                redirect_effect = next(
                    (
                        fx for fx in reversed(target_entity.effects)
                        if (fx.get("flags", {}) or {}).get("redirect_single_target_to_pet")
                    ),
                    None,
                )
                pet_id = (redirect_effect or {}).get("redirect_to_pet_id")
                pet = target_entity.pets.get(pet_id) if pet_id else None
                if pet and pet.hp > 0:
                    redirected_pet_name = pet.name
                    redirect_log = f"{pet.name} intercepts {source_ability_name} for {sid_token(target_label)}."
                    return pet, pet.name, False, redirected_pet_name, redirect_log
            return target_entity, target_label, is_player_entity, redirected_pet_name, redirect_log

        # Resolution layer: pre_resolution_protection
        def _resolve_pre_resolution_protection(
            target_entity: PlayerState,
            target_label: str,
            damage_school: str,
        ) -> tuple[bool, Dict[str, Any] | None]:
            if is_immune_all(target_entity):
                return True, None
            if has_flag(target_entity, "cycloned"):
                match.log.append(f"{sid_token(target_label)} is cycloned and takes no damage.")
                return True, None
            if damage_school == "magical" and has_effect(target_entity, "cloak_of_shadows"):
                match.log.append(f"{sid_token(target_label)} is immune to magical harm under Cloak of Shadows.")
                return True, None
            return False, None

        normalized_school = normalize_school(school) or "physical"
        is_player_target = hasattr(target, "res") and target.res is not None
        if incoming <= 0:
            return {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0, "school": normalized_school, "subschool": subschool, "redirect_log": None}
        target, target_sid, is_player_target, redirected_to, redirect_log = _resolve_target_resolution(target, target_sid)
        if is_player_target:
            blocked, _ = _resolve_pre_resolution_protection(target, target_sid, normalized_school)
            if blocked:
                return {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0, "school": normalized_school, "subschool": subschool, "redirect_log": redirect_log}
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
                "school": normalized_school,
                "subschool": subschool,
                "redirect_log": redirect_log,
            }

        # Resolution layer: damage_application
        def _resolve_damage_application(
            target_entity: PlayerState | PetState,
            instance_damage: list[int],
            is_player_entity: bool,
        ) -> tuple[list[Dict[str, Any]], int, int, list[Dict[str, Any]]]:
            instance_results_local: list[Dict[str, Any]] = []
            total_absorbed_local = 0
            total_remaining_local = 0
            total_breakdown_local: list[Dict[str, Any]] = []
            for value in instance_damage:
                if is_player_entity:
                    remaining, absorbed, breakdown = consume_absorbs(target_entity, value)
                else:
                    remaining, absorbed, breakdown = value, 0, []
                total_absorbed_local += absorbed
                total_remaining_local += remaining
                instance_results_local.append({"absorbed": absorbed, "hp_damage": remaining, "absorbed_breakdown": breakdown})
                total_breakdown_local.extend(breakdown)
            if total_remaining_local > 0:
                if is_player_entity:
                    target_entity.res.hp -= total_remaining_local
                else:
                    target_entity.hp -= total_remaining_local
            return instance_results_local, total_absorbed_local, total_remaining_local, total_breakdown_local

        # Resolution layer: post_damage_reactions
        def _resolve_target_post_damage_reactions(
            target_entity: PlayerState | PetState,
            target_label: str,
            hp_damage: int,
            is_player_entity: bool,
        ) -> None:
            if hp_damage <= 0:
                return
            if is_player_entity:
                was_stealthed = is_stealthed(target_entity)
                break_stealth_on_damage(target_entity, hp_damage)
                broken_effects = break_effects_and_collect_labels(target_entity)
                if was_stealthed and not is_stealthed(target_entity):
                    deferred_stealth_break_logs.append(f"{sid_token(target_label)} stealth broken by {source_ability_name}.")
                for effect_name in broken_effects:
                    deferred_break_on_damage_logs.append(
                        f"{effect_name} on {sid_token(target_label)} breaks on damage."
                    )
                if current_form_id(target_entity) == "bear_form":
                    grant_resource(target_entity, "rage", hp_damage)
                return
            broken_effects = break_effects_and_collect_labels(target_entity)
            for effect_name in broken_effects:
                deferred_break_on_damage_logs.append(
                    f"{effect_name} on {target_entity.name} breaks on damage."
                )

        instance_results, total_absorbed, total_remaining, total_breakdown = _resolve_damage_application(
            target,
            instance_values,
            is_player_target,
        )
        _resolve_target_post_damage_reactions(target, target_sid, total_remaining, is_player_target)
        return {
            "hp_damage": max(0, total_remaining),
            "absorbed": total_absorbed,
            "absorbed_breakdown": total_breakdown,
            "instances": instance_results,
            "mindgames_healing": 0,
            "redirected_to": redirected_to,
            "redirect_log": redirect_log,
            "school": normalized_school,
            "subschool": subschool,
        }

    def resolve_aoe_enemy_attack(
        actor_sid: str,
        target_sid: str,
        incoming: int,
        source_name: str,
        school: str,
        subschool: str | None = None,
        *,
        mindgames_flip_damage: bool = False,
        champion_log_template: str | None = None,
        champion_immune_log: str | None = None,
        skip_champion: bool = False,
    ) -> Dict[str, Any]:
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        if incoming <= 0:
            return {"champion": {"hp_damage": 0}, "pet_total_damage": 0}

        champion_dealt_data = {"hp_damage": 0, "mindgames_healing": 0}
        if skip_champion:
            if champion_immune_log:
                match.log.append(champion_immune_log)
        else:
            champion_dealt_data = apply_damage(
                actor,
                target,
                incoming,
                target_sid,
                source_name,
                bool(mindgames_flip_damage),
                [incoming],
                school=school,
                subschool=subschool,
                allow_redirect=False,
            )
            champion_hp_damage = int(champion_dealt_data.get("hp_damage", 0) or 0)
            if champion_log_template:
                if champion_hp_damage <= 0 and champion_immune_log:
                    match.log.append(champion_immune_log)
                else:
                    champion_log = format_damage_log(champion_log_template, champion_dealt_data)
                    flipped_heal = int(champion_dealt_data.get("mindgames_healing", 0) or 0)
                    if flipped_heal > 0:
                        champion_log = (
                            f"{champion_log} Mindgames flips damage into {flipped_heal} healing for the target."
                        )
                    match.log.append(champion_log)

        pet_total_damage = 0
        pet_hits: list[Dict[str, Any]] = []
        pet_targets = [target.pets[pid] for pid in sorted(target.pets.keys())]
        alive_imp_ids = [pet.id for pet in pet_targets if pet and pet.hp > 0 and pet.template_id == "imp"]
        imp_ordinal_by_id = {pid: idx + 1 for idx, pid in enumerate(alive_imp_ids)}
        target_owner_name = sid_token(target_sid)
        for pet in pet_targets:
            if not pet or pet.hp <= 0:
                continue
            pet_result = apply_damage(actor, pet, incoming, pet.name, source_name, school=school, subschool=subschool)
            remaining = int(pet_result.get("hp_damage", 0) or 0)
            absorbed = int(pet_result.get("absorbed", 0) or 0)
            breakdown = pet_result.get("absorbed_breakdown", [])
            total_incoming = remaining + absorbed
            if total_incoming > 0:
                if pet.template_id == "imp":
                    imp_ordinal = imp_ordinal_by_id.get(pet.id)
                    if imp_ordinal:
                        pet_label = f"{target_owner_name}'s {pet.name} (imp{imp_ordinal})"
                    else:
                        pet_label = f"{target_owner_name}'s {pet.name}"
                else:
                    pet_label = f"{target_owner_name}'s {pet.name}"
                pet_log = f"{source_name} hits {pet_label} for {total_incoming} damage."
                if absorbed > 0:
                    pet_log = f"{pet_log} {absorb_suffix(absorbed, breakdown).strip()}"
                match.log.append(pet_log)
            if remaining > 0:
                pet_total_damage += remaining
                if pet.hp > 0:
                    pet_hits.append({"pet": pet, "damage_data": pet_result})
            if pet.hp <= 0:
                handle_pet_defeat(target, pet)

        return {"champion": champion_dealt_data, "pet_total_damage": pet_total_damage, "pet_hits": pet_hits}

    def trigger_shield_of_vengeance_explosion(owner_sid: str, enemy_sid: str) -> None:
        owner = match.state[owner_sid]
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
        match.log.append("Shield of Vengeance explodes!")
        enemy = match.state[enemy_sid]
        skip_champion, untargetable_log = aoe_untargetable_resolution(enemy)
        if untargetable_log and untargetable_log.startswith("Target "):
            untargetable_log = f"{sid_token(enemy_sid)} {untargetable_log[len('Target '):] }"
        aoe_result = resolve_aoe_enemy_attack(
            owner_sid,
            enemy_sid,
            absorbed_total,
            "Shield of Vengeance",
            "magical",
            "holy",
            champion_log_template=f"Shield of Vengeance hits {sid_token(enemy_sid)} for __DMG_0__ damage.",
            champion_immune_log=untargetable_log or f"{sid_token(enemy_sid)} is immune to Shield of Vengeance explosion.",
            skip_champion=skip_champion,
        )
        champion_hp_damage = int(aoe_result.get("champion", {}).get("hp_damage", 0) or 0)
        totals = match.combat_totals.setdefault(owner_sid, {"damage": 0, "healing": 0})
        totals["damage"] += champion_hp_damage + int(aoe_result.get("pet_total_damage", 0) or 0)

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
            subschool=source.get("subschool"),
            allow_redirect=False,
        )
        formatted = format_damage_log(
            f"{sid_token(target_sid)} suffers __DMG_0__ damage from {dot_name}.",
            dealt_data,
        )
        flipped_heal = int(dealt_data.get("mindgames_healing", 0) or 0)
        if flipped_heal > 0:
            formatted = f"{formatted} Mindgames flips damage into {flipped_heal} healing for the target."
        if not source.get("suppress_log"):
            match.log.append(formatted)
        return int(dealt_data.get("hp_damage", 0) or 0)

    source_name_1 = ABILITIES.get(result1.get("ability_id", ""), {}).get("name", "attack")
    source_name_2 = ABILITIES.get(result2.get("ability_id", ""), {}).get("name", "attack")
    match.log.extend(result1.get("pre_logs", []))
    match.log.extend(result2.get("pre_logs", []))
    match.log.extend(deferred_pet_pre_action_logs)
    dealt1_data = (
        {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0}
        if result1.get("skip_direct_target_damage")
        else apply_damage(
            match.state[sids[0]],
            match.state[sids[1]],
            result1["damage"],
            sids[1],
            source_name_1,
            bool(result1.get("mindgames_flip_damage")),
            result1.get("damage_instances"),
            school=result1.get("school") or "physical",
            subschool=result1.get("subschool"),
            allow_redirect=ability_target_mode(ABILITIES.get(result1.get("ability_id", ""), {})) != "aoe_enemy",
        )
    )
    dealt2_data = (
        {"hp_damage": 0, "absorbed": 0, "absorbed_breakdown": [], "instances": [], "mindgames_healing": 0}
        if result2.get("skip_direct_target_damage")
        else apply_damage(
            match.state[sids[1]],
            match.state[sids[0]],
            result2["damage"],
            sids[0],
            source_name_2,
            bool(result2.get("mindgames_flip_damage")),
            result2.get("damage_instances"),
            school=result2.get("school") or "physical",
            subschool=result2.get("subschool"),
            allow_redirect=ability_target_mode(ABILITIES.get(result2.get("ability_id", ""), {})) != "aoe_enemy",
        )
    )
    dealt1 = int(dealt1_data.get("hp_damage", 0) or 0)
    dealt2 = int(dealt2_data.get("hp_damage", 0) or 0)

    def combatant_label(sid: str) -> str:
        ps = match.state[sid]
        class_id = (ps.build.class_id or "").strip().lower()
        return class_display_name(class_id) if class_id else sid_token(sid)

    def apply_direct_damage_dot(
        actor_sid: str,
        target_sid: str,
        result: Dict[str, Any],
        damage_data: Dict[str, Any],
        *,
        target_entity: Any | None = None,
        target_label: str | None = None,
    ) -> None:
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        dot_data = ability.get("dot") or {}
        if not dot_data:
            return
        trigger_total = int(damage_data.get("hp_damage", 0) or 0) + int(damage_data.get("mindgames_healing", 0) or 0)
        if trigger_total <= 0:
            return
        actor = match.state[actor_sid]
        target = target_entity or match.state[target_sid]
        dot_id = dot_data.get("id")
        duration = int(dot_data.get("duration", 1) or 1)
        dot_template = effect_template(dot_id) if dot_id else {}
        school = normalize_school(dot_data.get("school") or ability.get("school") or dot_template.get("school") or "magical") or "magical"
        subschool = (
            dot_data.get("subschool")
            or ability.get("subschool")
            or dot_template.get("subschool")
        ) if school == "magical" else None
        if dot_data.get("from_dealt_damage"):
            tick_damage = max(1, int(trigger_total // max(1, duration)))
        else:
            dot_scaling = dot_data.get("scaling", {}) or {}
            dot_dice = dot_data.get("dice", {}) or {}
            dot_roll = roll(dot_dice.get("type", "d0"), r) if dot_dice.get("type") else 0
            if "atk" in dot_scaling:
                tick_damage = base_damage(
                    modify_stat(actor, "atk", actor.stats.get("atk", 0)),
                    dot_scaling["atk"],
                    dot_roll,
                )
            elif "int" in dot_scaling:
                tick_damage = base_damage(
                    modify_stat(actor, "int", actor.stats.get("int", 0)),
                    dot_scaling["int"],
                    dot_roll,
                )
            else:
                tick_damage = int(dot_data.get("tick_damage", 0) or 0)
            tick_damage = max(1, int(tick_damage))
        refreshed = False
        if dot_id and refresh_dot_effect(
            target,
            dot_id,
            duration=duration,
            tick_damage=tick_damage,
            source_sid=actor_sid,
            school=school,
            subschool=subschool,
        ):
            refreshed = True
        elif dot_id:
            apply_effect_by_id(
                target,
                dot_id,
                overrides={"duration": duration, "tick_damage": tick_damage, "source_sid": actor_sid, "school": school, "subschool": subschool},
            )
        if not dot_id:
            return
        resolved_target_label = target_label or sid_token(target_sid)
        if dot_id == "dragon_roar_bleed":
            match.log.append(f"Dragon Roar applies bleed on {resolved_target_label}.")
        elif dot_id == "wildfire_burn":
            verb = "refreshes" if refreshed else "applies"
            match.log.append(f"Wildfire Bomb {verb} Wildfire Burn on {resolved_target_label}.")
        else:
            verb = "refreshes" if refreshed else "applies"
            match.log.append(f"{sid_token(actor_sid)} {verb} {effect_name(dot_id)} for {tick_damage} per turn.")

    def append_extra_logs(actor_sid: str, target_sid: str, result: Dict[str, Any]) -> None:
        for entry in result.get("extra_logs", []) or []:
            if isinstance(entry, str):
                match.log.append(entry)
                continue
            if not isinstance(entry, dict) or entry.get("type") != "damage_event":
                continue
            incoming = int(entry.get("incoming", 0) or 0)
            if incoming <= 0:
                continue
            damage_instances = entry.get("damage_instances")
            if isinstance(damage_instances, list):
                normalized_instances = [max(0, int(value or 0)) for value in damage_instances]
            else:
                normalized_instances = [incoming]
            dealt_data = apply_damage(
                match.state[actor_sid],
                match.state[target_sid],
                incoming,
                target_sid,
                str(entry.get("source_name") or "attack"),
                bool(result.get("mindgames_flip_damage")),
                normalized_instances,
                school=str(entry.get("school") or "physical"),
                subschool=entry.get("subschool"),
                allow_redirect=ability_target_mode(ABILITIES.get(result.get("ability_id", ""), {})) != "aoe_enemy",
            )
            dealt_amount = int(dealt_data.get("hp_damage", 0) or 0)
            if dealt_amount > 0:
                totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
                totals["damage"] += dealt_amount
            formatted = format_damage_log(str(entry.get("log_template") or ""), dealt_data)
            flipped_heal = int(dealt_data.get("mindgames_healing", 0) or 0)
            if flipped_heal > 0:
                formatted = f"{formatted} Mindgames flips damage into {flipped_heal} healing for the target."
            if formatted:
                match.log.append(formatted)

    result1_log = format_damage_log(result1["log"], dealt1_data)
    if int(dealt1_data.get("mindgames_healing", 0) or 0) > 0:
        result1_log = (
            f"{result1_log} Mindgames flips damage into "
            f"{int(dealt1_data.get('mindgames_healing', 0) or 0)} healing for the target."
    )
    match.log.append(result1_log)
    if dealt1_data.get("redirect_log"):
        match.log.append(str(dealt1_data.get("redirect_log")))
    append_extra_logs(sids[0], sids[1], result1)
    apply_direct_damage_dot(
        sids[0],
        sids[1],
        result1,
        dealt1_data,
        target_label=combatant_label(sids[1]),
    )
    result2_log = format_damage_log(result2["log"], dealt2_data)
    if int(dealt2_data.get("mindgames_healing", 0) or 0) > 0:
        result2_log = (
            f"{result2_log} Mindgames flips damage into "
            f"{int(dealt2_data.get('mindgames_healing', 0) or 0)} healing for the target."
    )
    match.log.append(result2_log)
    if dealt2_data.get("redirect_log"):
        match.log.append(str(dealt2_data.get("redirect_log")))
    append_extra_logs(sids[1], sids[0], result2)
    apply_direct_damage_dot(
        sids[1],
        sids[0],
        result2,
        dealt2_data,
        target_label=combatant_label(sids[0]),
    )

    if result1.get("deferred"):
        result1 = resolve_action(sids[0], sids[1], a1)
        if result1.get("log"):
            match.log.append(result1["log"])
    if result2.get("deferred"):
        result2 = resolve_action(sids[1], sids[0], a2)
        if result2.get("log"):
            match.log.append(result2["log"])

    for actor_sid, target_sid, result in ((sids[0], sids[1], result1), (sids[1], sids[0], result2)):
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        if ability_target_mode(ability) != "aoe_enemy":
            continue
        incoming = int(result.get("aoe_incoming_damage", result.get("damage", 0)) or 0)
        if incoming <= 0:
            continue
        aoe_result = resolve_aoe_enemy_attack(
            actor_sid,
            target_sid,
            incoming,
            ability.get("name", "AoE"),
            result.get("school") or "physical",
            result.get("subschool"),
            skip_champion=True,
        )
        pet_damage = int(aoe_result.get("pet_total_damage", 0) or 0)
        if pet_damage > 0:
            totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
            totals["damage"] += pet_damage
        if ability.get("dot"):
            enemy_label = combatant_label(target_sid)
            for pet_hit in aoe_result.get("pet_hits", []):
                pet = pet_hit.get("pet")
                if not pet:
                    continue
                pet_label = f"{enemy_label}'s {pet.name}"
                apply_direct_damage_dot(
                    actor_sid,
                    target_sid,
                    result,
                    pet_hit.get("damage_data", {}),
                    target_entity=pet,
                    target_label=pet_label,
                )

    def _resolve_actor_post_damage_reactions(
        actor_sid: str,
        target_sid: str,
        dealt: int,
        result: Dict[str, Any],
    ) -> None:
        ability = ABILITIES.get(result.get("ability_id", ""), {})
        actor = match.state[actor_sid]
        target = match.state[target_sid]

        if dealt > 0 and ability.get("heal_from_dealt_damage") and actor.res:
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + dealt, actor.res.hp_max)
            gained = actor.res.hp - before_hp
            if gained > 0:
                match.log.append(f"{sid_token(actor_sid)} heals {gained} HP from {ability.get('name', 'their attack')}.")
                totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
                totals["healing"] += gained

        if result.get("ability_id") == "death" and target.res.hp > 0 and dealt > 0:
            backlash = int(dealt * 1.0)
            if backlash > 0:
                apply_damage(actor, actor, backlash, actor_sid, "Shadow Word: Death backlash", school="magical", subschool="shadow", allow_redirect=False)
                match.log.append(f"{sid_token(actor_sid)} suffers {backlash} backlash from Shadow Word: Death.")

        lifesteal = float(ability.get("heal_from_damage", 0) or 0)
        if dealt > 0 and lifesteal > 0 and actor.res:
            heal_value = int(dealt * lifesteal)
            before_hp = actor.res.hp
            actor.res.hp = min(actor.res.hp + heal_value, actor.res.hp_max)
            gained = actor.res.hp - before_hp
            if gained > 0:
                match.log.append(f"{sid_token(actor_sid)} drains {gained} life.")
                totals = match.combat_totals.setdefault(actor_sid, {"damage": 0, "healing": 0})
                totals["healing"] += gained

    _resolve_actor_post_damage_reactions(sids[0], sids[1], dealt1, result1)
    _resolve_actor_post_damage_reactions(sids[1], sids[0], dealt2, result2)

    def _resolve_end_of_turn() -> tuple[bool, bool, bool]:
        # end_of_turn: pet_phase
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
        flush_deferred_stealth_break_logs()

        # end_of_turn: dot_tick / hot_tick / resource_tick
        for sid in sids:
            ps = match.state[sid]
            end_summary = end_of_turn(ps, match.log, sid_token(sid))
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
                        match.log.append(f"{sid_token(source_sid)} heals {gained} HP from {source_name}.")
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

        # end_of_turn: pet_phase
        for sid in sids:
            owner = match.state[sid]
            for pet_id in sorted(list((owner.pets or {}).keys())):
                pet = owner.pets.get(pet_id)
                if not pet or pet.hp <= 0:
                    continue
                pet_summary = end_of_turn_pet(pet, match.log, pet.name)
                for source in pet_summary.get("damage_sources", []):
                    source_sid = source.get("source_sid")
                    source_ps = match.state.get(source_sid)
                    if not source_sid or not source_ps:
                        continue
                    dealt = apply_damage(
                        source_ps,
                        pet,
                        int(source.get("incoming", 0) or 0),
                        pet.name,
                        source.get("effect_name") or "DoT",
                        False,
                        [int(source.get("incoming", 0) or 0)],
                        school=source.get("school") or "magical",
                        subschool=source.get("subschool"),
                        allow_redirect=False,
                    )
                    damage = int(dealt.get("hp_damage", 0) or 0)
                    if damage > 0:
                        match.log.append(f"{pet.name} suffers {damage} damage from {source.get('effect_name') or 'DoT'}.")
                        totals = match.combat_totals.setdefault(source_sid, {"damage": 0, "healing": 0})
                        totals["damage"] += damage
                if pet.hp > 0:
                    next_effects = []
                    for effect in pet.effects:
                        duration = effect.get("duration")
                        if duration is None:
                            next_effects.append(effect)
                            continue
                        next_duration = int(duration) - 1
                        if next_duration > 0:
                            updated = dict(effect)
                            updated["duration"] = next_duration
                            next_effects.append(updated)
                    pet.effects = next_effects

        trigger_shield_of_vengeance_explosion(sids[0], sids[1])
        trigger_shield_of_vengeance_explosion(sids[1], sids[0])
        flush_deferred_stealth_break_logs()

        # end_of_turn: duration_decrement / expiry_cleanup
        for sid in sids:
            ps = match.state[sid]
            tick_player_effects(ps)
            tick_cooldowns(ps)

        # end_of_turn: pet_cleanup
        cleanup_pets(match)

        # end_of_turn: winner_check
        p1_alive = match.state[sids[0]].res.hp > 0
        p2_alive = match.state[sids[1]].res.hp > 0
        return p1_alive, p2_alive, (p1_alive and p2_alive)

    p1_alive, p2_alive, both_alive = _resolve_end_of_turn()

    execute_ability = ABILITIES.get("execute", {})
    execute_threshold = execute_ability.get("requires_target_hp_below")
    if execute_threshold is not None and both_alive:
        for sid in sids:
            ps = match.state[sid]
            opponent_sid = sids[1] if sid == sids[0] else sids[0]
            opponent = match.state[opponent_sid]
            if ps.build.class_id != "warrior":
                continue
            if is_on_cooldown(ps, "execute", execute_ability):
                continue
            if cannot_act(ps):
                continue
            if opponent.res.hp / max(1, opponent.res.hp_max) >= float(execute_threshold):
                continue
            ok, _ = can_pay_costs(ps, execute_ability.get("cost", {}))
            if ok:
                match.log.append(f"{sid_token(sid)} Can Use Execute!")

    death_ability = ABILITIES.get("death", {})
    death_threshold = 0.2
    if death_ability and both_alive:
        for sid in sids:
            ps = match.state[sid]
            opponent_sid = sids[1] if sid == sids[0] else sids[0]
            opponent = match.state[opponent_sid]
            if ps.build.class_id != "priest":
                continue
            if is_on_cooldown(ps, "death", death_ability):
                continue
            if cannot_act(ps):
                continue
            if opponent.res.hp / max(1, opponent.res.hp_max) >= death_threshold:
                continue
            ok, _ = can_pay_costs(ps, death_ability.get("cost", {}))
            if ok:
                match.log.append(f"{sid_token(sid)} Shadow Word: Death Damage will be Doubled!")

    match.log.extend(deferred_break_on_damage_logs)

    # Check for winners after all turn resolution (pet phase, end-of-turn, and deferred logs)
    if not both_alive:
        match.phase = "ended"
        match.log.append(
            "Post-Combat Summary|FD:{friendly_damage}|FH:{friendly_healing}|"
            "ED:{enemy_damage}|EH:{enemy_healing}"
        )
        if p1_alive and not p2_alive:
            match.winner = sids[0]
            match.log.append(f"{sid_token(sids[0])} wins the duel.")
        elif p2_alive and not p1_alive:
            match.winner = sids[1]
            match.log.append(f"{sid_token(sids[1])} wins the duel.")
        else:
            match.winner = None
            match.log.append("Double KO. No winner.")

    match.submitted.clear()
    match.turn += 1
    match.last_resolved_key = payload_key
    match.turn_in_progress = False


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


def debug_simulate_aoe_normalization_suite() -> Dict[str, Any]:
    """
    End-to-end deterministic checks for normalized aoe_enemy routing.

    Verifies:
    - Swipe and Dragon Roar both use champion-first then sorted pet fanout.
    - Champion immune_all does not prevent pet AoE damage.
    - Shield of Vengeance explosion damages enemy pets.
    - Dragon Roar bleed is applied to champion and AoE-hit pets.
    """

    # Scenario 1: Swipe + Dragon Roar both AoE, champion immunity doesn't block pet damage.
    p1 = "p1_warlock"
    p2 = "p2_warrior"
    match = MatchState(room_id="debug_aoe_norm", players=[p1, p2], phase="combat", seed=9001)
    match.picks[p1] = PlayerBuild(class_id="warlock")
    match.picks[p2] = PlayerBuild(class_id="warrior")
    apply_prep_build(match)

    for _ in range(3):
        submit_action(match, p1, {"ability_id": "summon_imp"})
        submit_action(match, p2, {"ability_id": "pass_turn"})
        resolve_turn(match)

    # Swap warrior to druid-like swipe cast via direct ability use is class-gated, so run Swipe with a druid actor in a separate match.
    p1s = "p1_warlock"
    p2s = "p2_druid"
    swipe_match = MatchState(room_id="debug_swipe", players=[p1s, p2s], phase="combat", seed=9002)
    swipe_match.picks[p1s] = PlayerBuild(class_id="warlock")
    swipe_match.picks[p2s] = PlayerBuild(class_id="druid")
    apply_prep_build(swipe_match)
    for _ in range(3):
        submit_action(swipe_match, p1s, {"ability_id": "summon_imp"})
        submit_action(swipe_match, p2s, {"ability_id": "bear"})
        resolve_turn(swipe_match)

    warlock_swipe = swipe_match.state[p1s]
    apply_effect_by_id(warlock_swipe, "iceblock", overrides={"duration": 1})
    swipe_imp_ids = sorted(warlock_swipe.pets.keys())
    swipe_imp_before = {pid: warlock_swipe.pets[pid].hp for pid in swipe_imp_ids}
    hp_before = warlock_swipe.res.hp
    submit_action(swipe_match, p1s, {"ability_id": "healthstone"})
    submit_action(swipe_match, p2s, {"ability_id": "swipe"})
    resolve_turn(swipe_match)
    warlock_swipe_after = swipe_match.state[p1s]
    assert warlock_swipe_after.res.hp == hp_before, "Swipe should not damage immune champion."
    swipe_deltas = [swipe_imp_before[pid] - warlock_swipe_after.pets[pid].hp for pid in swipe_imp_ids]
    assert all(delta > 0 for delta in swipe_deltas), "Swipe should still damage all pets while champion is immune."
    assert len(set(swipe_deltas)) == 1, "Swipe should deal same AoE incoming damage to all pets."

    # Dragon Roar AoE + bleed applies to champion and pets.
    warlock = match.state[p1]
    warrior = match.state[p2]
    warrior.stats["acc"] = 999
    warrior.res.rage = warrior.res.rage_max
    apply_effect_by_id(warlock, "iceblock", overrides={"duration": 1})
    imp_ids = sorted(warlock.pets.keys())
    imp_before = {pid: warlock.pets[pid].hp for pid in imp_ids}

    deltas = [0 for _ in imp_ids]
    warlock_after = warlock
    for _ in range(4):
        warrior = match.state[p2]
        warrior.res.rage = warrior.res.rage_max
        warrior.cooldowns["dragon_roar"] = []
        submit_action(match, p1, {"ability_id": "healthstone"})
        submit_action(match, p2, {"ability_id": "dragon_roar"})
        resolve_turn(match)
        warlock_after = match.state[p1]
        deltas = [imp_before[pid] - warlock_after.pets[pid].hp for pid in imp_ids if pid in warlock_after.pets]
        if deltas and all(delta > 0 for delta in deltas):
            break

    assert deltas and all(delta > 0 for delta in deltas), "Dragon Roar should AoE damage all pets."
    assert len(set(deltas)) == 1, "Dragon Roar should apply same AoE incoming damage to each pet."
    assert has_effect(warlock_after, "dragon_roar_bleed"), "Dragon Roar bleed should be on enemy champion."

    # Ensure bleed was also applied to pets.
    for pid in sorted(warlock_after.pets.keys()):
        pet = warlock_after.pets[pid]
        assert any((fx.get("id") == "dragon_roar_bleed") for fx in (pet.effects or [])), "Dragon Roar bleed should apply to pets."

    # Scenario 2: Shield of Vengeance explosion now uses AoE fanout, respects blink-like untargetability,
    # and still hits pets.
    p1p = "p1_warlock"
    p2p = "p2_paladin"
    sov_match = MatchState(room_id="debug_sov", players=[p1p, p2p], phase="combat", seed=9010)
    sov_match.picks[p1p] = PlayerBuild(class_id="warlock")
    sov_match.picks[p2p] = PlayerBuild(class_id="paladin")
    apply_prep_build(sov_match)

    for _ in range(3):
        submit_action(sov_match, p1p, {"ability_id": "summon_imp"})
        submit_action(sov_match, p2p, {"ability_id": "pass_turn"})
        resolve_turn(sov_match)

    wlk = sov_match.state[p1p]
    imp_ids_sov = sorted(wlk.pets.keys())
    imp_before_sov = {pid: wlk.pets[pid].hp for pid in imp_ids_sov}

    # Force deterministic explosion payload and tick into expiration turn.
    pal = sov_match.state[p2p]
    apply_effect_by_id(wlk, "blink", overrides={"duration": 1, "miss_log": "Target returned to their dark ward — Miss."})
    apply_effect_by_id(pal, "shield_of_vengeance", overrides={"duration": 1, "absorbed": 30})
    sov_fx = get_effect(pal, "shield_of_vengeance")
    assert sov_fx is not None, "Expected Shield of Vengeance effect to be active."
    hp_before_sov = wlk.res.hp

    submit_action(sov_match, p1p, {"ability_id": "pass_turn"})
    submit_action(sov_match, p2p, {"ability_id": "pass_turn"})
    resolve_turn(sov_match)

    wlk_after = sov_match.state[p1p]
    imp_after_sov = {pid: wlk_after.pets[pid].hp for pid in imp_ids_sov if pid in wlk_after.pets}
    sov_damaged_pets = [pid for pid in imp_ids_sov if pid in imp_after_sov and imp_after_sov[pid] < imp_before_sov[pid]]
    assert sov_damaged_pets, "Shield of Vengeance explosion should damage enemy pets."
    assert wlk_after.res.hp == hp_before_sov, "Shield of Vengeance explosion should respect blink-like untargetability."

    # Log-label regression checks: AoE fanout/champion lines should use sid[:5] tokens
    # so snapshot formatting can render viewer-aware "(you)" names.
    dragon_roar_lines = [line for line in match.log if "Dragon Roar" in line]
    assert any((f"Dragon Roar hits {p1[:5]}'s Imp (imp1)" in line) for line in dragon_roar_lines), (
        "Dragon Roar pet fanout should reference sid token owner labels."
    )

    sov_lines = [line for line in sov_match.log if "Shield of Vengeance" in line]
    assert any(("Target returned to their dark ward — Miss." in line) for line in sov_lines), (
        "Shield of Vengeance explosion should log blink-like untargetable misses for the champion."
    )
    assert not any((f"Shield of Vengeance hits {p1p[:5]} for" in line) for line in sov_lines), (
        "Shield of Vengeance champion hit should be skipped while blink-like untargetability is active."
    )
    assert any((f"Shield of Vengeance hits {p1p[:5]}'s Imp (imp1)" in line) for line in sov_lines), (
        "Shield of Vengeance pet fanout should reference sid token owner labels."
    )

    return {
        "swipe_pet_damage": {pid: swipe_deltas[idx] for idx, pid in enumerate(swipe_imp_ids)},
        "dragon_roar_pet_damage": {pid: deltas[idx] for idx, pid in enumerate(imp_ids)},
        "shield_of_vengeance_pets_hit": sov_damaged_pets,
        "dragon_roar_bleed_on_champion": has_effect(warlock_after, "dragon_roar_bleed"),
        "recent_logs": {
            "swipe": swipe_match.log[-12:],
            "dragon_roar": match.log[-12:],
            "shield_of_vengeance": sov_match.log[-18:],
        },
    }
