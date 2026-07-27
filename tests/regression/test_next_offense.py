"""Deterministic integration coverage for generic next-offense empowerment."""
from __future__ import annotations

import copy
import re

from contextlib import contextmanager
from typing import Any, Iterator

from harness import (
    ABILITIES,
    _detect_duel_html_path,
    _turn_lines,
    effects,
    make_match,
    resolver,
    submit_turn,
)

from games.duel.engine import periodic_items

from .helpers import _DEF_PASS, _add_pet


_CRUSADER_EFFECT_ID = "crusader_empower"
_DEATH_EFFECT_ID = "deaths_bargain"
_NEXT_EFFECT_IDS = {_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID}
_CRUSADER_LOG = "Empowered strike!"
_DEATH_LOG = "Death's Bargain empowers the attack!"


@contextmanager
def _patched_ability(ability_id: str, **updates: Any) -> Iterator[None]:
    original = dict(ABILITIES[ability_id])
    ABILITIES[ability_id] = dict(original, **updates)
    try:
        yield
    finally:
        ABILITIES[ability_id] = original


def _disable_item_proc(actor: Any, passive_type: str) -> None:
    for effect in actor.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive") or {}
        if passive.get("type") == passive_type:
            passive = copy.deepcopy(passive)
            passive["chance"] = 0.0
            effect["passive"] = passive


def _force_item_proc(actor: Any, passive_type: str) -> None:
    for effect in actor.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive") or {}
        if passive.get("type") == passive_type:
            passive = copy.deepcopy(passive)
            passive["chance"] = 1.0
            effect["passive"] = passive


def _apply_next_effects(
    actor: Any,
    order: tuple[str, ...] = (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
) -> None:
    for effect_id in order:
        effects.apply_effect_by_id(actor, effect_id)


def _active_next_effect_ids(actor: Any) -> set[str]:
    return {
        str(effect.get("id"))
        for effect in actor.effects
        if (effect.get("flags") or {}).get("empower_next_offense")
    }


def _assert_canonical_empowerment_logs(
    lines: list[str],
    *,
    expected: bool,
    label: str,
) -> None:
    combined = "\n".join(lines)
    expected_count = 1 if expected else 0
    assert combined.count(_CRUSADER_LOG) == expected_count, \
        f"{label}: Crusader empowerment log count drifted"
    assert combined.count(_DEATH_LOG) == expected_count, \
        f"{label}: Death's Bargain empowerment log count drifted"
    if expected:
        assert combined.index(_CRUSADER_LOG) < combined.index(_DEATH_LOG), \
            f"{label}: empowerment logs must be ordered by effect ID"


def _prepare_direct_match(
    actor_class: str = "warrior",
    target_class: str = "mage",
    *,
    weapon: str = "crusaders_greatsword",
    armor: str = "scourgelord_chestplate",
    trinket: str | None = None,
    seed: int,
):
    items = {"weapon": weapon, "armor": armor}
    if trinket:
        items["trinket"] = trinket
    match = make_match(
        actor_class,
        target_class,
        p1_items=items,
        seed=seed,
    )
    actor_sid, target_sid = match.players
    actor = match.state[actor_sid]
    target = match.state[target_sid]
    actor.stats.update({"acc": 999, "crit": 0})
    target.stats.update(
        {
            "def": 0,
            "eva": 0,
            "physical_reduction": 0,
            "magic_resist": 0,
            "damage_reduction": 0,
        }
    )
    _disable_item_proc(actor, "empower_next_offense")
    return match, actor_sid, target_sid, actor, target


def _direct_hit_values(lines: list[str]) -> list[int]:
    action_line = next(
        line
        for line in lines
        if " uses " in line and "Deals " in line
    )
    return [int(value) for value in re.findall(r"Deals (\d+) damage", action_line)]


def _strike_again_values(lines: list[str]) -> list[int]:
    return [
        int(match.group(1))
        for line in lines
        if (match := re.search(r"strikes again .* for (\d+) bonus damage", line))
    ]


def scenario_next_offense_multiple_effects_stack_on_same_cast() -> bool:
    results: dict[str, tuple[int, int, list[str], set[str]]] = {}
    variants = {
        "baseline": (),
        "death": (_DEATH_EFFECT_ID,),
        "crusader": (_CRUSADER_EFFECT_ID,),
        "both": (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
    }
    with _patched_ability(
        "basic_attack",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ):
        for index, (label, effect_ids) in enumerate(variants.items()):
            match, actor_sid, _, actor, target = _prepare_direct_match(
                seed=9700 + index
            )
            _apply_next_effects(actor, effect_ids)
            hp_before = target.res.hp
            submit_turn(match, "basic_attack", _DEF_PASS)
            lines = _turn_lines(match, 1)
            results[label] = (
                hp_before - target.res.hp,
                match.combat_totals[actor_sid]["damage"],
                lines,
                _active_next_effect_ids(actor),
            )

    assert results["baseline"][0] == 20
    assert results["death"][0] == int(20 * 1.15)
    assert results["crusader"][0] == int(20 * 1.20)
    assert results["both"][0] == int(20 * 1.20 * 1.15)
    for label, (damage, total, lines, active_ids) in results.items():
        assert total == damage, f"{label}: combat totals must equal direct HP damage"
        assert not active_ids, f"{label}: all active next-offense effects must consume"
        if label in {"baseline", "both"}:
            _assert_canonical_empowerment_logs(
                lines,
                expected=label == "both",
                label=label,
            )
    assert "\n".join(results["death"][2]).count(_DEATH_LOG) == 1
    assert "\n".join(results["death"][2]).count(_CRUSADER_LOG) == 0
    assert "\n".join(results["crusader"][2]).count(_CRUSADER_LOG) == 1
    assert "\n".join(results["crusader"][2]).count(_DEATH_LOG) == 0
    return True


def scenario_next_offense_multiple_effects_are_order_independent() -> bool:
    outcomes = []
    orders = (
        (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
        (_DEATH_EFFECT_ID, _CRUSADER_EFFECT_ID),
    )
    with _patched_ability(
        "basic_attack",
        dice=None,
        scaling={},
        flat_damage=23,
        cannot_miss=True,
    ):
        for index, order in enumerate(orders):
            match, actor_sid, _, actor, target = _prepare_direct_match(
                seed=9710 + index
            )
            _apply_next_effects(actor, order)
            hp_before = target.res.hp
            submit_turn(match, "basic_attack", _DEF_PASS)
            lines = _turn_lines(match, 1)
            _assert_canonical_empowerment_logs(
                lines,
                expected=True,
                label=f"order {order}",
            )
            outcomes.append(
                (
                    hp_before - target.res.hp,
                    copy.deepcopy(match.combat_totals[actor_sid]),
                    _active_next_effect_ids(actor),
                    lines,
                )
            )
    assert outcomes[0] == outcomes[1], \
        "Damage, totals, remaining effects, and logs must ignore insertion order"
    return True


def _assert_committed_failure_consumes_both(
    match: Any,
    action: str,
    *,
    label: str,
) -> None:
    actor = match.state[match.players[0]]
    target = match.state[match.players[1]]
    _disable_item_proc(actor, "empower_next_offense")
    _apply_next_effects(actor)
    submit_turn(match, action, _DEF_PASS)
    lines = _turn_lines(match, 1)
    assert not (_active_next_effect_ids(actor) & _NEXT_EFFECT_IDS), \
        f"{label}: both next-offense effects must consume together"
    _assert_canonical_empowerment_logs(lines, expected=True, label=label)

    # Remove the failure control and prove no multiplier or log leaks into the
    # next legal direct cast.
    actor.effects = [
        effect for effect in actor.effects
        if effect.get("type") == "item_passive"
    ]
    target.effects = [
        effect for effect in target.effects
        if effect.get("type") == "item_passive"
    ]
    target.res.absorbs = {}
    actor.stats.update({"acc": 999, "crit": 0})
    target.stats.update(
        {
            "def": 0,
            "eva": 0,
            "physical_reduction": 0,
            "damage_reduction": 0,
        }
    )
    hp_before = target.res.hp
    submit_turn(match, "basic_attack", _DEF_PASS)
    second_lines = _turn_lines(match, 2)
    assert hp_before - target.res.hp == 20, \
        f"{label}: consumed multipliers leaked into the next cast"
    _assert_canonical_empowerment_logs(
        second_lines,
        expected=False,
        label=f"{label} later cast",
    )


def scenario_next_offense_multiple_effects_consume_on_failed_damage_outcomes() -> bool:
    with _patched_ability(
        "basic_attack",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ), _patched_ability(
        "fireball",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ), _patched_ability(
        "cleave",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ):
        accuracy_miss = _prepare_direct_match(seed=9720)
        effects.apply_effect_by_id(
            accuracy_miss[3],
            "earth_shock",
            overrides={"duration": 2},
        )
        _assert_committed_failure_consumes_both(
            accuracy_miss[0],
            "basic_attack",
            label="accuracy miss",
        )

        evasion = _prepare_direct_match("warrior", "rogue", seed=9721)
        effects.apply_effect_by_id(
            evasion[4],
            "evasion",
            overrides={"duration": 2},
        )
        _assert_committed_failure_consumes_both(
            evasion[0],
            "basic_attack",
            label="forced evasion",
        )

        physical_immunity = _prepare_direct_match(
            "warrior", "paladin", seed=9722
        )
        effects.apply_effect_by_id(
            physical_immunity[4],
            "divine_shield",
            overrides={"duration": 2},
        )
        _assert_committed_failure_consumes_both(
            physical_immunity[0],
            "basic_attack",
            label="physical immunity",
        )

        magical_immunity = _prepare_direct_match("mage", "rogue", seed=9723)
        effects.apply_effect_by_id(
            magical_immunity[4],
            "cloak_of_shadows",
            overrides={"duration": 2},
        )
        _assert_committed_failure_consumes_both(
            magical_immunity[0],
            "fireball",
            label="magical immunity",
        )

        full_absorb = _prepare_direct_match(seed=9724)
        effects.add_absorb(
            full_absorb[4],
            999,
            source_name="Full Test Absorb",
            effect_id="full_test_absorb",
        )
        _assert_committed_failure_consumes_both(
            full_absorb[0],
            "basic_attack",
            label="full absorb",
        )

        zero_damage = _prepare_direct_match("warrior", "mage", seed=9725)
        zero_damage[4].stats.update(
            {"def": 1000, "physical_reduction": 1000}
        )
        _assert_committed_failure_consumes_both(
            zero_damage[0],
            "basic_attack",
            label="zero actual HP damage",
        )

        champion_immune = _prepare_direct_match(
            "warrior", "warlock", seed=9726
        )
        _add_pet(champion_immune[4], "valid_aoe_pet", "imp")
        champion_immune[4].pets["valid_aoe_pet"].hp = 100
        champion_immune[4].pets["valid_aoe_pet"].hp_max = 100
        effects.apply_effect_by_id(
            champion_immune[4],
            "iceblock",
            overrides={"duration": 2},
        )
        _assert_committed_failure_consumes_both(
            champion_immune[0],
            "cleave",
            label="AoE immune champion with valid pet",
        )

        all_immune = _prepare_direct_match(
            "warrior", "warlock", seed=9727
        )
        effects.apply_effect_by_id(
            all_immune[4],
            "iceblock",
            overrides={"duration": 2},
        )
        for pet_id in ("immune_aoe_pet_a", "immune_aoe_pet_b"):
            _add_pet(all_immune[4], pet_id, "imp")
            effects.apply_effect_by_id(
                all_immune[4].pets[pet_id],
                "divine_shield",
                overrides={"duration": 2},
            )
        _assert_committed_failure_consumes_both(
            all_immune[0],
            "cleave",
            label="all AoE recipients immune",
        )
    return True


def _assert_both_preserved(
    match: Any,
    action: str,
    *,
    label: str,
    turn_number: int = 1,
) -> None:
    actor = match.state[match.players[0]]
    _disable_item_proc(actor, "empower_next_offense")
    _apply_next_effects(actor)
    submit_turn(match, action, _DEF_PASS)
    assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(actor), \
        f"{label}: both next-offense effects must remain active"
    _assert_canonical_empowerment_logs(
        _turn_lines(match, turn_number),
        expected=False,
        label=label,
    )


def scenario_next_offense_multiple_effects_preserve_on_rejected_actions() -> bool:
    ordinary_cases = (
        ("Pass", "warrior", "mage", _DEF_PASS),
        ("healing", "priest", "warrior", "flash_heal"),
        ("defense", "warrior", "mage", "die_by_sword"),
        ("utility", "hunter", "rogue", "flare"),
        ("pure crowd control", "rogue", "warrior", "cheap_shot"),
        ("pure DoT", "warlock", "warrior", "corruption"),
        ("rejected execute", "warrior", "mage", "execute"),
    )
    for index, (label, actor_class, target_class, action) in enumerate(
        ordinary_cases
    ):
        match, _, _, actor, _ = _prepare_direct_match(
            actor_class,
            target_class,
            seed=9740 + index,
        )
        if action == "flash_heal":
            actor.res.hp -= 30
        _assert_both_preserved(match, action, label=label)

    unaffordable = _prepare_direct_match("mage", "warrior", seed=9750)
    unaffordable[3].res.mp = 0
    _assert_both_preserved(
        unaffordable[0],
        "fireball",
        label="insufficient resource",
    )

    cooldown = _prepare_direct_match("mage", "warrior", seed=9751)
    cooldown[3].cooldowns["fireball"] = [2]
    _assert_both_preserved(cooldown[0], "fireball", label="cooldown rejection")

    missing_form = _prepare_direct_match("druid", "warrior", seed=9752)
    _assert_both_preserved(
        missing_form[0],
        "mangle",
        label="missing required form",
    )

    missing_effect = _prepare_direct_match("mage", "warrior", seed=9753)
    _assert_both_preserved(
        missing_effect[0],
        "pyroblast",
        label="missing required effect",
    )

    missing_pet = _prepare_direct_match("hunter", "warrior", seed=9754)
    _assert_both_preserved(
        missing_pet[0],
        "kill_command",
        label="missing active pet",
    )

    unable = _prepare_direct_match("warrior", "mage", seed=9755)
    effects.apply_effect_by_id(
        unable[3],
        "ring_of_ice_freeze",
        overrides={"duration": 2},
    )
    _assert_both_preserved(unable[0], "basic_attack", label="inability to act")

    disabled = _prepare_direct_match("hunter", "warrior", seed=9756)
    effects.apply_effect_by_id(
        disabled[3],
        "aspect_of_turtle",
        overrides={"duration": 2},
    )
    _assert_both_preserved(
        disabled[0],
        "basic_attack",
        label="attacks-disabled denial",
    )

    summon_capacity = _prepare_direct_match("warlock", "warrior", seed=9757)
    for pet_id in ("capacity_imp_a", "capacity_imp_b", "capacity_imp_c"):
        _add_pet(summon_capacity[3], pet_id, "imp")
    _assert_both_preserved(
        summon_capacity[0],
        "summon_imp",
        label="summon-capacity rejection",
    )

    autonomous = _prepare_direct_match("warlock", "warrior", seed=9758)
    submit_turn(autonomous[0], "summon_imp", _DEF_PASS)
    _assert_both_preserved(
        autonomous[0],
        _DEF_PASS,
        label="autonomous pet action",
        turn_number=2,
    )

    periodic = _prepare_direct_match(
        "warrior",
        "mage",
        trinket="vial_of_shadows",
        seed=9759,
    )
    for _ in range(4):
        submit_turn(periodic[0], _DEF_PASS, _DEF_PASS)
    _assert_both_preserved(
        periodic[0],
        _DEF_PASS,
        label="periodic item damage",
        turn_number=5,
    )

    shield = _prepare_direct_match("paladin", "warrior", seed=9760)
    effects.apply_effect_by_id(
        shield[3],
        "shield_of_vengeance",
        overrides={"duration": 1, "absorbed": 20},
    )
    _assert_both_preserved(
        shield[0],
        _DEF_PASS,
        label="Shield of Vengeance explosion",
    )

    astral = _prepare_direct_match("shaman", "warlock", seed=9761)
    effects.add_absorb(
        astral[3],
        30,
        source_name="Astral Test Absorb",
        effect_id="astral_test_absorb",
    )
    _add_pet(astral[4], "astral_target_pet", "imp")
    _assert_both_preserved(
        astral[0],
        "astral_explosion",
        label="Astral Explosion",
    )
    return True


def scenario_next_offense_multiple_effects_multihit_contract() -> bool:
    with _patched_ability(
        "arcane_barrage",
        dice=None,
        scaling={},
        flat_damage=20,
        hits=3,
        cannot_miss=True,
    ):
        match, _, _, actor, _ = _prepare_direct_match(
            "mage", "warrior", seed=9770
        )
        _apply_next_effects(actor)
        submit_turn(match, "arcane_barrage", _DEF_PASS)
    lines = _turn_lines(match, 1)
    assert _direct_hit_values(lines) == [27, 27, 27], \
        "Every direct multihit packet must receive one combined 1.38 multiplier"
    assert not (_active_next_effect_ids(actor) & _NEXT_EFFECT_IDS)
    _assert_canonical_empowerment_logs(
        lines,
        expected=True,
        label="multihit",
    )
    return True


def scenario_next_offense_multiple_effects_aoe_contract() -> bool:
    outcomes = []
    with _patched_ability(
        "cleave",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ):
        for order in (
            (
                _CRUSADER_EFFECT_ID,
                _DEATH_EFFECT_ID,
            ),
            (
                _DEATH_EFFECT_ID,
                _CRUSADER_EFFECT_ID,
            ),
        ):
            match, _, _, actor, target = _prepare_direct_match(
                "warrior", "warlock", seed=9780
            )
            for pet_id, pet_def in (
                ("aoe_empower_pet_a", 0),
                ("aoe_empower_pet_b", 8),
            ):
                _add_pet(target, pet_id, "imp")
                pet = target.pets[pet_id]
                pet.hp = pet.hp_max = 100
                pet.stats.update({"def": pet_def, "physical_reduction": 0})
            _apply_next_effects(actor, order)
            hp_before = target.res.hp
            pet_hp_before = {
                pet_id: pet.hp for pet_id, pet in target.pets.items()
            }
            submit_turn(match, "cleave", _DEF_PASS)
            lines = _turn_lines(match, 1)
            damage = (
                hp_before - target.res.hp,
                *(
                    pet_hp_before[pet_id] - target.pets[pet_id].hp
                    for pet_id in sorted(pet_hp_before)
                ),
            )
            assert damage[0] == 27 and damage[1] == 27
            assert 0 < damage[2] < damage[1], \
                "Pet-specific mitigation must still apply after combined empowerment"
            _assert_canonical_empowerment_logs(
                lines,
                expected=True,
                label=f"AoE order {order}",
            )
            outcomes.append(
                (damage, copy.deepcopy(match.combat_totals), lines)
            )
    assert outcomes[0] == outcomes[1], \
        "AoE direct packets and logs must ignore effect insertion order"
    return True


def _twin_blades_result(
    effect_ids: tuple[str, ...],
    *,
    seed: int,
    order: tuple[str, ...] | None = None,
    challenger: bool = False,
    always_crit: bool = False,
) -> tuple[list[int], list[int], int, list[str], set[str]]:
    armor = "challengers_chestplate" if challenger else "scourgelord_chestplate"
    with _patched_ability(
        "basic_attack",
        dice=None,
        scaling={},
        flat_damage=30,
        cannot_miss=True,
        always_crit=always_crit,
    ):
        match, actor_sid, _, actor, _ = _prepare_direct_match(
            "warrior",
            "mage",
            weapon="twin_blades_azzinoth",
            armor=armor,
            seed=seed,
        )
        _force_item_proc(actor, "strike_again")
        if challenger:
            actor.res.rage = actor.res.rage_max
        _apply_next_effects(actor, order or effect_ids)
        submit_turn(match, "basic_attack", _DEF_PASS)
    lines = _turn_lines(match, 1)
    return (
        _direct_hit_values(lines),
        _strike_again_values(lines),
        match.combat_totals[actor_sid]["damage"],
        lines,
        _active_next_effect_ids(actor),
    )


def scenario_next_offense_does_not_scale_strike_again() -> bool:
    baseline = _twin_blades_result((), seed=9790)
    empowered = _twin_blades_result((_DEATH_EFFECT_ID,), seed=9790)
    assert baseline[0] == [30] and empowered[0] == [34]
    assert baseline[1] == empowered[1] == [12], \
        "Death's Bargain must not enter Twin Blades strike-again math"
    assert baseline[2] == 42 and empowered[2] == 46
    assert _DEATH_EFFECT_ID not in empowered[4]
    assert "\n".join(empowered[3]).count(_DEATH_LOG) == 1
    return True


def scenario_multiple_next_offense_effects_do_not_scale_strike_again() -> bool:
    baseline = _twin_blades_result((), seed=9791)
    crusader = _twin_blades_result((_CRUSADER_EFFECT_ID,), seed=9791)
    both = _twin_blades_result(
        (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
        seed=9791,
    )
    assert baseline[0] == [30]
    assert crusader[0] == [36]
    assert both[0] == [41]
    assert baseline[1] == crusader[1] == both[1] == [12]
    assert both[2] == 53 and not (both[4] & _NEXT_EFFECT_IDS)
    _assert_canonical_empowerment_logs(
        both[3],
        expected=True,
        label="Twin Blades both effects",
    )
    return True


def scenario_strike_again_basis_preserves_ordinary_outgoing_modifiers() -> bool:
    plain = _twin_blades_result((), seed=9792)
    challenger = _twin_blades_result((), seed=9792, challenger=True)
    challenger_empowered = _twin_blades_result(
        (_DEATH_EFFECT_ID,),
        seed=9792,
        challenger=True,
    )
    assert plain[1] == [12]
    assert challenger[1] == challenger_empowered[1] == [13], \
        "Challenger must remain in the strike-again basis"
    assert challenger[0] == [33] and challenger_empowered[0] == [37]

    critical = _twin_blades_result((), seed=9793, always_crit=True)
    critical_empowered = _twin_blades_result(
        (_DEATH_EFFECT_ID,),
        seed=9793,
        always_crit=True,
    )
    assert critical[0] == [45] and critical_empowered[0] == [51]
    assert critical[1] == critical_empowered[1] == [18], \
        "Critical contribution must remain while next-offense is excluded"
    return True


def scenario_strike_again_empowerment_order_independence() -> bool:
    first = _twin_blades_result(
        (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
        order=(_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
        seed=9794,
    )
    reverse = _twin_blades_result(
        (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID),
        order=(_DEATH_EFFECT_ID, _CRUSADER_EFFECT_ID),
        seed=9794,
    )
    assert first == reverse
    return True


def scenario_strike_again_multihit_parent_contract() -> bool:
    with _patched_ability(
        "arcane_barrage",
        dice=None,
        scaling={},
        flat_damage=20,
        hits=3,
        cannot_miss=True,
    ):
        match, _, _, actor, _ = _prepare_direct_match(
            "mage",
            "warrior",
            weapon="twin_blades_azzinoth",
            seed=9795,
        )
        _force_item_proc(actor, "strike_again")
        _apply_next_effects(actor)
        submit_turn(match, "arcane_barrage", _DEF_PASS)
    lines = _turn_lines(match, 1)
    assert _direct_hit_values(lines) == [27, 27, 27]
    assert _strike_again_values(lines) == [8, 8, 8], \
        "Each Twin Blades proc must use its unempowered 20-point parent basis"
    _assert_canonical_empowerment_logs(
        lines,
        expected=True,
        label="strike-again multihit",
    )
    return True


def scenario_next_offense_snapshot_validation_rejects_malformed_metadata() -> bool:
    invalid_cases = (
        ("empty id", "id", ""),
        ("string multiplier", "damage_mult", "1.2"),
        ("boolean multiplier", "damage_mult", True),
        ("zero multiplier", "damage_mult", 0),
        ("negative multiplier", "damage_mult", -1.0),
        ("missing multiplier", "damage_mult", None),
    )
    for index, (label, key, value) in enumerate(invalid_cases):
        match = make_match("warrior", "mage", seed=9800 + index)
        actor = match.state[match.players[0]]
        effects.apply_effect_by_id(actor, _DEATH_EFFECT_ID)
        active = effects.get_effect(actor, _DEATH_EFFECT_ID)
        assert active is not None
        active[key] = value
        before = copy.deepcopy(actor.effects)
        try:
            resolver.snapshot_next_offense_empowerments(actor)
        except ValueError as exc:
            assert "empower_next_offense" in str(exc), \
                f"{label}: malformed snapshot failure must be clear"
        else:
            raise AssertionError(f"{label}: malformed effect metadata was accepted")
        assert actor.effects == before, \
            f"{label}: failed snapshot validation must not mutate active effects"

    valid = make_match("warrior", "mage", seed=9807)
    actor = valid.state[valid.players[0]]
    _apply_next_effects(actor)
    before = copy.deepcopy(actor.effects)
    snapshots = resolver.snapshot_next_offense_empowerments(actor)
    assert actor.effects == before, "Snapshotting must not mutate effect dictionaries"
    assert tuple(snapshot.effect_id for snapshot in snapshots) == (
        _CRUSADER_EFFECT_ID,
        _DEATH_EFFECT_ID,
    )
    assert resolver.combined_next_offense_multiplier(snapshots) == 1.2 * 1.15
    effects.get_effect(actor, _DEATH_EFFECT_ID)["damage_mult"] = 99
    assert resolver.combined_next_offense_multiplier(snapshots) == 1.2 * 1.15, \
        "Immutable snapshots must not observe later effect-dictionary mutation"
    return True


def scenario_direct_damage_classifier_inference_and_overrides() -> bool:
    expected_true = (
        "basic_attack",
        "fireball",
        "arcane_barrage",
        "cleave",
        "wildfire_bomb",
    )
    expected_false = (
        "corruption",
        "unstable_affliction",
        "vampiric_touch",
        "devouring_plague",
        "agony",
        "pass_turn",
        "flash_heal",
        "die_by_sword",
        "cheap_shot",
        "astral_explosion",
    )
    for ability_id in expected_true:
        assert resolver.ability_has_direct_damage(ABILITIES[ability_id]), \
            f"{ability_id} must classify as direct damage"
    for ability_id in expected_false:
        assert not resolver.ability_has_direct_damage(ABILITIES[ability_id]), \
            f"{ability_id} must classify as non-direct"
    assert all(
        not resolver.ability_has_direct_damage(ABILITIES[ability_id])
        for ability_id in resolver.SPECIAL_ABILITY_HANDLERS
    ), "Current special handlers must not bypass the direct-packet multiplier path"
    return True


def scenario_direct_damage_classifier_rejects_malformed_metadata() -> bool:
    invalid_values = ("false", 0, 1, 1.0, [], {}, None)
    for value in invalid_values:
        malformed = dict(ABILITIES["basic_attack"], direct_damage=value)
        try:
            resolver.ability_has_direct_damage(malformed)
        except ValueError as exc:
            assert str(exc) == "ability direct_damage must be a boolean"
        else:
            raise AssertionError(
                f"direct_damage={value!r} must fail strict boolean validation"
            )
    return True


def scenario_pure_dot_abilities_preserve_next_offense_effects() -> bool:
    cases = (
        ("warlock", "corruption", "corruption"),
        ("warlock", "unstable_affliction", "unstable_affliction"),
        ("warlock", "agony", "agony"),
        ("priest", "vampiric_touch", "vampiric_touch"),
        ("priest", "devouring_plague", "devouring_plague"),
    )
    for index, (class_id, ability_id, effect_id) in enumerate(cases):
        match, _, _, actor, target = _prepare_direct_match(
            class_id,
            "warrior",
            seed=9810 + index,
        )
        if ability_id == "devouring_plague":
            effects.apply_effect_by_id(actor, "shadowy_insight")
        _apply_next_effects(actor)
        submit_turn(match, ability_id, _DEF_PASS)
        assert effects.has_effect(target, effect_id), \
            f"{ability_id}: pure DoT must still apply"
        assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(actor), \
            f"{ability_id}: pure DoT must preserve both next-offense effects"
        _assert_canonical_empowerment_logs(
            _turn_lines(match, 1),
            expected=False,
            label=ability_id,
        )
    return True


def scenario_direct_plus_dot_ability_consumes_next_offense_effects() -> bool:
    outcomes = []
    variants = ((), (_CRUSADER_EFFECT_ID, _DEATH_EFFECT_ID))
    with _patched_ability(
        "wildfire_bomb",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ):
        for effect_ids in variants:
            match, _, _, actor, target = _prepare_direct_match(
                "hunter", "warrior", seed=9820
            )
            _apply_next_effects(actor, effect_ids)
            submit_turn(match, "wildfire_bomb", _DEF_PASS)
            direct = _direct_hit_values(_turn_lines(match, 1))[0]
            dot = effects.get_effect(target, "wildfire_burn")
            assert dot is not None
            stored = int(dot.get("tick_damage", 0) or 0)
            submit_turn(match, _DEF_PASS, _DEF_PASS)
            tick_line = next(
                line
                for line in _turn_lines(match, 2)
                if "suffers " in line and "Wildfire Burn" in line
            )
            tick = int(re.search(r"suffers (\d+) damage", tick_line).group(1))
            outcomes.append((direct, stored, tick, _active_next_effect_ids(actor)))
    assert outcomes[0][0] == 20 and outcomes[1][0] == 27
    assert outcomes[0][1:3] == outcomes[1][1:3], \
        "Direct-plus-DoT empowerment must not alter stored or future ticks"
    assert not (outcomes[1][3] & _NEXT_EFFECT_IDS)
    return True


def scenario_fixed_value_damage_does_not_qualify_as_direct_offense() -> bool:
    astral = _prepare_direct_match("shaman", "warlock", seed=9830)
    effects.add_absorb(
        astral[3],
        31,
        source_name="Classifier Astral Absorb",
        effect_id="classifier_astral_absorb",
    )
    for pet_id in ("classifier_pet_a", "classifier_pet_b", "classifier_pet_c"):
        _add_pet(astral[4], pet_id, "imp")
        astral[4].pets[pet_id].hp = astral[4].pets[pet_id].hp_max = 100
        astral[4].pets[pet_id].stats.update({"magic_resist": 0, "def": 0})
    _apply_next_effects(astral[3])
    submit_turn(astral[0], "astral_explosion", _DEF_PASS)
    hit_values = [
        int(match.group(1))
        for line in _turn_lines(astral[0], 1)
        if (match := re.search(r"Astral Explosion hits .* for (\d+) damage", line))
    ]
    assert hit_values == [11, 10, 10]
    assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(astral[3])
    _assert_canonical_empowerment_logs(
        _turn_lines(astral[0], 1),
        expected=False,
        label="Astral Explosion classifier",
    )

    shield = _prepare_direct_match("paladin", "warrior", seed=9831)
    effects.apply_effect_by_id(
        shield[3],
        "shield_of_vengeance",
        overrides={"duration": 1, "absorbed": 20},
    )
    _apply_next_effects(shield[3])
    submit_turn(shield[0], _DEF_PASS, _DEF_PASS)
    assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(shield[3])
    _assert_canonical_empowerment_logs(
        _turn_lines(shield[0], 1),
        expected=False,
        label="Shield of Vengeance classifier",
    )
    return True


def scenario_scourgelord_refresh_preserves_other_next_offense() -> bool:
    match, _, _, actor, _ = _prepare_direct_match(seed=9840)
    starting_hp = actor.res.hp
    for _ in range(4):
        submit_turn(match, _DEF_PASS, _DEF_PASS)

    _apply_next_effects(actor)
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    first_cost = max(1, int(starting_hp * 0.10))
    assert actor.res.hp == starting_hp - first_cost
    assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(actor)
    assert sum(
        effect.get("id") == _DEATH_EFFECT_ID for effect in actor.effects
    ) == 1

    for _ in range(4):
        submit_turn(match, _DEF_PASS, _DEF_PASS)
    effects.apply_effect_by_id(actor, _CRUSADER_EFFECT_ID)
    hp_before_turn_ten = actor.res.hp
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    second_cost = max(1, int(hp_before_turn_ten * 0.10))
    assert actor.res.hp == hp_before_turn_ten - second_cost
    assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(actor)
    assert sum(
        effect.get("id") == _DEATH_EFFECT_ID for effect in actor.effects
    ) == 1

    for _ in range(4):
        submit_turn(match, _DEF_PASS, _DEF_PASS)
    effects.apply_effect_by_id(actor, _CRUSADER_EFFECT_ID)
    actor.res.hp = 1
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert actor.res.hp == 1
    assert _NEXT_EFFECT_IDS <= _active_next_effect_ids(actor), \
        "A failed turn-15 sacrifice must preserve both existing effects"
    assert sum(
        effect.get("id") == _DEATH_EFFECT_ID for effect in actor.effects
    ) == 1

    with _patched_ability(
        "basic_attack",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ):
        submit_turn(match, "basic_attack", _DEF_PASS)
    assert not (_active_next_effect_ids(actor) & _NEXT_EFFECT_IDS)
    _assert_canonical_empowerment_logs(
        _turn_lines(match, 16),
        expected=True,
        label="refreshed Scourgelord plus Crusader",
    )
    return True


def scenario_next_offense_secondary_sources_remain_unscaled() -> bool:
    def proc_result(
        *,
        item_id: str,
        actor_class: str,
        ability_id: str,
        empowered: bool,
        seed: int,
        forced_passives: tuple[str, ...] = (),
        actor_hp: int | None = None,
    ) -> tuple[list[str], dict[str, int], Any, Any, Any]:
        match, actor_sid, _, actor, target = _prepare_direct_match(
            actor_class,
            "warrior",
            weapon=item_id,
            seed=seed,
        )
        for passive_type in forced_passives:
            _force_item_proc(actor, passive_type)
        if actor_hp is not None:
            actor.res.hp = actor_hp
        if empowered:
            _apply_next_effects(actor)
        submit_turn(match, ability_id, _DEF_PASS)
        return (
            _turn_lines(match, 1),
            copy.deepcopy(match.combat_totals[actor_sid]),
            actor,
            target,
            match,
        )

    with _patched_ability(
        "basic_attack",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ), _patched_ability(
        "fireball",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
        on_hit_effects=[],
    ), _patched_ability(
        "drain_life",
        dice=None,
        scaling={},
        flat_damage=20,
        cannot_miss=True,
    ):
        void_baseline = proc_result(
            item_id="void_blades",
            actor_class="mage",
            ability_id="basic_attack",
            empowered=False,
            seed=9850,
        )
        void_empowered = proc_result(
            item_id="void_blades",
            actor_class="mage",
            ability_id="basic_attack",
            empowered=True,
            seed=9850,
        )
        void_pattern = r"calls upon the void .* Deals (\d+) magic damage"
        void_base_proc = int(
            re.search(void_pattern, "\n".join(void_baseline[0])).group(1)
        )
        void_empowered_proc = int(
            re.search(void_pattern, "\n".join(void_empowered[0])).group(1)
        )
        assert _direct_hit_values(void_baseline[0]) == [20]
        assert _direct_hit_values(void_empowered[0]) == [27]
        assert void_base_proc == void_empowered_proc, \
            "Void Blade proc damage must not inherit next-offense multipliers"

        thunder_baseline = proc_result(
            item_id="thunderfury",
            actor_class="warrior",
            ability_id="basic_attack",
            empowered=False,
            seed=9851,
            forced_passives=("lightning_blast", "heal_on_hit"),
            actor_hp=40,
        )
        thunder_empowered = proc_result(
            item_id="thunderfury",
            actor_class="warrior",
            ability_id="basic_attack",
            empowered=True,
            seed=9851,
            forced_passives=("lightning_blast", "heal_on_hit"),
            actor_hp=40,
        )
        lightning_pattern = r"blasts the target with lightning .* Deals (\d+) magic damage"
        lightning_base = int(
            re.search(lightning_pattern, "\n".join(thunder_baseline[0])).group(1)
        )
        lightning_empowered = int(
            re.search(lightning_pattern, "\n".join(thunder_empowered[0])).group(1)
        )
        assert lightning_base == lightning_empowered, \
            "Lightning Blast must not inherit next-offense multipliers"
        assert (
            thunder_baseline[1]["healing"]
            == thunder_empowered[1]["healing"]
            > 0
        ), "Thunderfury on-hit healing must remain independently calculated"

        dragon_baseline = proc_result(
            item_id="dragonwrath",
            actor_class="mage",
            ability_id="fireball",
            empowered=False,
            seed=9852,
            forced_passives=("duplicate_offensive_spell",),
        )
        dragon_empowered = proc_result(
            item_id="dragonwrath",
            actor_class="mage",
            ability_id="fireball",
            empowered=True,
            seed=9852,
            forced_passives=("duplicate_offensive_spell",),
        )
        duplicate_pattern = r"duplicates Fireball! Deals (\d+) damage"
        duplicate_base = int(
            re.search(duplicate_pattern, "\n".join(dragon_baseline[0])).group(1)
        )
        duplicate_empowered = int(
            re.search(duplicate_pattern, "\n".join(dragon_empowered[0])).group(1)
        )
        assert duplicate_base == duplicate_empowered == 20, \
            "Dragonwrath duplicate-spell packets must not inherit empowerment"

        wand_baseline = proc_result(
            item_id="wand_of_fire",
            actor_class="mage",
            ability_id="fireball",
            empowered=False,
            seed=9853,
        )
        wand_empowered = proc_result(
            item_id="wand_of_fire",
            actor_class="mage",
            ability_id="fireball",
            empowered=True,
            seed=9853,
        )
        baseline_burn = effects.get_effect(wand_baseline[3], "burn")
        empowered_burn = effects.get_effect(wand_empowered[3], "burn")
        assert baseline_burn is not None and empowered_burn is not None
        assert baseline_burn.get("tick_damage") == empowered_burn.get("tick_damage") == 2
        submit_turn(wand_baseline[4], _DEF_PASS, _DEF_PASS)
        submit_turn(wand_empowered[4], _DEF_PASS, _DEF_PASS)
        baseline_future_burn = [
            line
            for line in _turn_lines(wand_baseline[4], 2)
            if "suffers " in line and "from Burn" in line
        ]
        empowered_future_burn = [
            line
            for line in _turn_lines(wand_empowered[4], 2)
            if "suffers " in line and "from Burn" in line
        ]
        assert baseline_future_burn == empowered_future_burn, \
            "Wand of Fire future Burn ticks must remain unempowered"

        drain_baseline = proc_result(
            item_id="crusaders_greatsword",
            actor_class="warlock",
            ability_id="drain_life",
            empowered=False,
            seed=9854,
            actor_hp=40,
        )
        drain_empowered = proc_result(
            item_id="crusaders_greatsword",
            actor_class="warlock",
            ability_id="drain_life",
            empowered=True,
            seed=9854,
            actor_hp=40,
        )
        assert _direct_hit_values(drain_baseline[0]) == [20]
        assert _direct_hit_values(drain_empowered[0]) == [27]
        assert drain_baseline[1]["healing"] == 20
        assert drain_empowered[1]["healing"] == 27, \
            "Drain Life may heal from larger actual direct damage, but not multiply twice"
    return True


def scenario_next_offense_architecture_docs_and_roadmap_contract() -> bool:
    roadmap = (
        _detect_duel_html_path().parent / "ROADMAP.md"
    ).read_text(encoding="utf-8")
    agents = (
        _detect_duel_html_path().parent / "AGENTS.md"
    ).read_text(encoding="utf-8")
    assert "## Recently completed: periodic-item expansion" in roadmap
    assert (
        "- [x] Scourgelord Chestplate: periodic current-HP sacrifice and Death's Bargain\n"
        "  next-offense empowerment"
    ) in roadmap
    assert "## Active phase: remaining classes" in roadmap
    assert "## Active phase: periodic-item expansion" not in roadmap
    assert "- [ ] Scourgelord Chestplate" not in roadmap
    assert "Every class uses exactly one primary combat resource." in roadmap

    required_contract_text = (
        "Every active `empower_next_offense` effect snapshots onto the same next valid",
        "ordered canonically by effect ID",
        "combine multiplicatively into one scalar before the canonical damage stage",
        "All snapshotted effects consume together",
        "exactly once per cast in canonical order",
        "basis that excludes only the generic next-offense multiplier",
        "`direct_damage` is a strict boolean ability-definition property",
        "Offensive-action classification remains broader and separate",
        "Fixed-value shield-derived damage is explicitly non-direct",
    )
    normalized_agents = " ".join(agents.split())
    for text in required_contract_text:
        assert text in normalized_agents, \
            f"AGENTS.md is missing next-offense contract text: {text}"
    return True
