"""Item regression scenarios (Challenger's Chestplate, Rage Crystal, item passives).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import random
import re

from collections import Counter

from harness import (
    ABILITIES,
    EFFECT_TEMPLATES,
    PetState,
    _detect_duel_html_path,
    _turn_lines,
    effects,
    make_match,
    resolver,
    submit_turn,
)

from .helpers import (
    _DEF_PASS,
    _active_pet,
)


def scenario_rage_crystal_increases_all_rage_gain_sources() -> bool:
    baseline = make_match("warrior", "mage", seed=123)
    crystal = make_match("warrior", "mage", p1_items={"trinket": "rage_crystal"}, seed=123)

    baseline_warrior = baseline.state[baseline.players[0]]
    crystal_warrior = crystal.state[crystal.players[0]]

    baseline_warrior.res.rage = 0
    crystal_warrior.res.rage = 0
    submit_turn(baseline, "overpower", _DEF_PASS)
    submit_turn(crystal, "overpower", _DEF_PASS)
    baseline_overpower_rage = baseline_warrior.res.rage
    crystal_overpower_rage = crystal_warrior.res.rage
    assert crystal_overpower_rage == int(baseline_overpower_rage * 1.25), "Rage Crystal should grant 25% more rage from damage-based rage gain"

    baseline_druid = make_match("mage", "druid", seed=123)
    crystal_druid = make_match("mage", "druid", p2_items={"trinket": "rage_crystal"}, seed=123)
    submit_turn(baseline_druid, _DEF_PASS, "bear_form")
    submit_turn(crystal_druid, _DEF_PASS, "bear_form")
    baseline_bear = baseline_druid.state[baseline_druid.players[1]]
    crystal_bear = crystal_druid.state[crystal_druid.players[1]]
    baseline_bear.res.rage = 0
    crystal_bear.res.rage = 0
    submit_turn(baseline_druid, "fireball", _DEF_PASS)
    submit_turn(crystal_druid, "fireball", _DEF_PASS)
    assert crystal_bear.res.rage == int(baseline_bear.res.rage * 1.25), "Rage Crystal should grant 25% more rage from damage taken in Bear Form"

    crystal_warrior.res.hp = max(1, int(crystal_warrior.res.hp_max * 0.2))
    assert effects.damage_multiplier_from_passives(crystal_warrior) == 1.15, "Rage Crystal should grant 15% more damage below 30% HP"
    crystal_warrior.res.hp = int(crystal_warrior.res.hp_max * 0.5)
    assert effects.damage_multiplier_from_passives(crystal_warrior) == 1.0, "Rage Crystal damage bonus should not apply at or above 30% HP"
    return True


def scenario_challengers_chestplate_resource_stance() -> bool:
    high = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6201)
    owner = high.state[high.players[0]]
    assert effects.active_resource_id(owner) == "mp", "Mage active resource should be mana"
    owner.res.mp = 51
    assert effects.active_resource_pct(owner) > 0.5, "51/80 mana should be high-resource"
    assert effects.challenger_resource_stance_mode(owner) == "might", "Challenger mode should be Might above 50% active resource"
    assert effects.damage_multiplier_from_passives(owner) == 1.10, "Challenger should deal 10% more damage above 50% active resource"
    duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
    assert "/item armor challengers_chestplate" in duel_html_text, "Challenger's Chestplate should be documented in the static item list"
    assert '"Challenger\'s Chestplate",' in duel_html_text and "#a335ee" in duel_html_text, "Challenger's Chestplate should be registered on the epic item color path"
    assert "When the fight turns, so do you—either with steel resolve or with bruising consequence." in duel_html_text, "Challenger's Chestplate flavor text should appear in static item docs/tooltips"
    assert '"Challenger\'s Chestplate": {' in duel_html_text, "Challenger's Chestplate should have normal item tooltip data for equipped armor mouseover"
    high_panel = effects.build_effect_panel_payload(owner)
    high_buffs = {entry.get("name") for entry in high_panel["buffs_magical"]}
    high_debuffs = {entry.get("name") for entry in high_panel["debuffs_magical"]}
    assert "Challenger's Might" in high_buffs, "Challenger's Might should show above 50% active resource"
    assert "Challenger's Wrath" not in high_debuffs, "Challenger's Wrath should not show above 50% active resource"

    challenger_effect = next(effect for effect in owner.effects if effect.get("source_item_id") == "challengers_chestplate")
    assert challenger_effect.get("dispellable") is False, "Challenger item passive should remain non-dispellable"
    removed = effects.dispel_effects(owner, school="magical")
    assert removed == 0 and any(effect.get("source_item_id") == "challengers_chestplate" for effect in owner.effects), "Dispel should not remove Challenger item passive"

    owner.res.mp = 40
    assert effects.active_resource_pct(owner) == 0.5, "40/80 mana should be exactly threshold"
    assert effects.challenger_resource_stance_mode(owner) == "wrath", "Challenger mode should be Wrath at exactly 50% active resource"
    assert effects.damage_multiplier_from_passives(owner) == 0.90, "Challenger should use low-resource damage at exactly 50%"
    threshold_panel = effects.build_effect_panel_payload(owner)
    threshold_buffs = {entry.get("name") for entry in threshold_panel["buffs_magical"]}
    threshold_debuffs = {entry.get("name") for entry in threshold_panel["debuffs_magical"]}
    assert "Challenger's Might" not in threshold_buffs, "Challenger's Might should disappear at exactly 50% active resource"
    assert "Challenger's Wrath" in threshold_debuffs, "Challenger's Wrath should show at exactly 50% active resource"
    owner.res.mp = 41
    crossed_panel = effects.build_effect_panel_payload(owner)
    assert effects.challenger_resource_stance_mode(owner) == "might", "Challenger mode should update to Might after crossing above 50%"
    assert "Challenger's Might" in {entry.get("name") for entry in crossed_panel["buffs_magical"]}, "Challenger panel should update back to Might after crossing above 50%"
    assert "Challenger's Wrath" not in {entry.get("name") for entry in crossed_panel["debuffs_magical"]}, "Challenger panel should hide Wrath after crossing above 50%"
    owner.res.mp = 39
    assert effects.damage_multiplier_from_passives(owner) == 0.90, "Challenger should deal 10% less damage below 50% active resource"

    snapshot_match = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6208)
    snapshot_actor = snapshot_match.state[snapshot_match.players[0]]
    snapshot_target = snapshot_match.state[snapshot_match.players[1]]
    snapshot_actor.res.mp = 41
    snapshot_start_hp = snapshot_target.res.hp
    submit_turn(snapshot_match, "fireball", _DEF_PASS)
    snapshot_damage = snapshot_start_hp - snapshot_target.res.hp
    assert snapshot_actor.res.mp <= 40, "Fireball's increased Challenger cost should leave the actor at or below 50% after the action"
    assert effects.challenger_resource_stance_mode(snapshot_actor) == "wrath", "Live Challenger mode should become Wrath after the action spends below threshold"

    low_snapshot_match = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6208)
    low_snapshot_actor = low_snapshot_match.state[low_snapshot_match.players[0]]
    low_snapshot_target = low_snapshot_match.state[low_snapshot_match.players[1]]
    low_snapshot_actor.res.mp = 40
    low_snapshot_start_hp = low_snapshot_target.res.hp
    submit_turn(low_snapshot_match, "fireball", _DEF_PASS)
    low_snapshot_damage = low_snapshot_start_hp - low_snapshot_target.res.hp
    assert snapshot_damage > low_snapshot_damage, "An action that starts in Might should not switch to Wrath damage after its cost drops resource below 50%"

    simultaneous = make_match(
        "mage",
        "mage",
        p1_items={"armor": "challengers_chestplate"},
        p2_items={"armor": "challengers_chestplate"},
        seed=6209,
    )
    sim_p1, sim_p2 = (simultaneous.state[sid] for sid in simultaneous.players)
    sim_p1.res.mp = 41
    sim_p2.res.mp = 41
    assert effects.challenger_resource_stance_mode(sim_p1) == "might", "P1 should start the simultaneous turn in Might"
    assert effects.challenger_resource_stance_mode(sim_p2) == "might", "P2 should start the simultaneous turn in Might"
    original_fireball = dict(ABILITIES["fireball"])
    try:
        ABILITIES["fireball"] = dict(original_fireball, dice=None, scaling={}, flat_damage=30, on_hit_effects=[], cannot_miss=True)
        p1_hp_before = sim_p1.res.hp
        p2_hp_before = sim_p2.res.hp
        submit_turn(simultaneous, "fireball", "fireball")
    finally:
        ABILITIES["fireball"] = original_fireball
    p1_damage_taken = p1_hp_before - sim_p1.res.hp
    p2_damage_taken = p2_hp_before - sim_p2.res.hp
    assert sim_p1.res.mp == 31 and sim_p2.res.mp == 31, "Both players should pay the increased Might cost before dropping to Wrath and end-of-turn mana regen"
    assert effects.challenger_resource_stance_mode(sim_p1) == "wrath", "P1 live mode should be Wrath after spending below threshold"
    assert effects.challenger_resource_stance_mode(sim_p2) == "wrath", "P2 live mode should be Wrath after spending below threshold"
    assert p1_damage_taken == p2_damage_taken, "Simultaneous Chestplate Fireballs should use start-of-turn target Might snapshots symmetrically"

    def redirected_boar_damage_for_hunter_mana(mana: int) -> int:
        redirect_match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6212)
        hunter_sid, warrior_sid = redirect_match.players
        hunter = redirect_match.state[hunter_sid]
        warrior = redirect_match.state[warrior_sid]
        submit_turn(redirect_match, "call_boar", _DEF_PASS)
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Boar should be active for Challenger redirect coverage"
        hunter.res.mp = mana
        effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
        warrior.res.rage = warrior.res.rage_max
        original_mortal_strike = dict(ABILITIES["mortal_strike"])
        try:
            ABILITIES["mortal_strike"] = dict(original_mortal_strike, dice=None, scaling={}, flat_damage=30, cannot_miss=True)
            hunter_hp_before = hunter.res.hp
            boar_hp_before = boar.hp
            submit_turn(redirect_match, _DEF_PASS, "mortal_strike")
        finally:
            ABILITIES["mortal_strike"] = original_mortal_strike
        assert hunter.res.hp == hunter_hp_before, "Blocking Defence should redirect the single-target hit away from the Hunter"
        return boar_hp_before - boar.hp

    might_redirect_damage = redirected_boar_damage_for_hunter_mana(41)
    wrath_redirect_damage = redirected_boar_damage_for_hunter_mana(20)
    assert might_redirect_damage == wrath_redirect_damage, "Redirected pet damage should not change with the Hunter's Challenger stance"

    owner.res.mp = 80
    physical_with_challenger = effects.mitigate_damage(100, owner, "physical")
    magical_with_challenger = effects.mitigate_damage(100, owner, "magic")
    owner.effects = [fx for fx in owner.effects if fx.get("source_item_id") != "challengers_chestplate"]
    physical_without_challenger = effects.mitigate_damage(100, owner, "physical")
    magical_without_challenger = effects.mitigate_damage(100, owner, "magic")
    assert physical_with_challenger == int(physical_without_challenger * 0.90), "Challenger should reduce incoming physical damage by 10% above 50%"
    assert magical_with_challenger == int(magical_without_challenger * 0.90), "Challenger should reduce incoming magical/DoT-path damage by 10% above 50%"

    low = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6202)
    low_owner = low.state[low.players[0]]
    low_owner.res.mp = 40
    low_physical = effects.mitigate_damage(100, low_owner, "physical")
    low_owner.effects = [fx for fx in low_owner.effects if fx.get("source_item_id") != "challengers_chestplate"]
    low_baseline = effects.mitigate_damage(100, low_owner, "physical")
    assert low_physical == int(low_baseline * 1.10), "Challenger should increase incoming damage by 10% at or below 50%"

    cost_owner = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6203).state["p1_sid"]
    cost_owner.res.mp = 80
    assert resolver.adjusted_resource_costs(cost_owner, {"mp": 11, "hp": 7}) == {"mp": 14, "hp": 7}, "Challenger should ceil active-resource costs by 20% and leave non-active costs unchanged"
    cost_owner.res.mp = 50
    ok, fail = resolver.can_pay_costs(cost_owner, {"mp": 42})
    assert not ok and fail == "mp", "can_pay_costs should reject casts made unaffordable by Challenger's surcharge"

    gain_owner = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6204).state["p1_sid"]
    gain_owner.res.mp = 40
    assert effects.resource_gain_multiplier_from_passives(gain_owner, "mp") == 1.30, "Challenger should boost active resource gains by 30% at or below 50%"
    assert effects.resource_gain_multiplier_from_passives(gain_owner, "rage") == 1.0, "Challenger should not boost inactive resource gains"

    pet_owner = make_match("hunter", "mage", p1_items={"armor": "challengers_chestplate"}, seed=6205).state["p1_sid"]
    pet_owner.pets["pet"] = PetState(id="pet", template_id="wolf", name="Wolf", owner_sid="p1_sid", hp=50, hp_max=50, entity_type="beast")
    pet = pet_owner.pets["pet"]
    assert not any(fx.get("source_item_id") == "challengers_chestplate" for fx in pet.effects), "Pets should not inherit Challenger outgoing/incoming/resource passives"

    stacked = make_match("warrior", "mage", p1_items={"armor": "challengers_chestplate", "trinket": "rage_crystal"}, seed=6206).state["p1_sid"]
    stacked.res.rage = 10
    assert round(effects.resource_gain_multiplier_from_passives(stacked, "rage"), 3) == 1.625, "Challenger and Rage Crystal resource gains should stack multiplicatively"
    stacked.res.hp = max(1, int(stacked.res.hp_max * 0.2))
    assert round(effects.damage_multiplier_from_passives(stacked), 3) == 1.035, "Existing damage passives should continue stacking multiplicatively with Challenger"

    focus_warrior = make_match("warrior", "mage", p1_items={"armor": "challengers_chestplate", "trinket": "focus_charm"}, seed=6210).state["p1_sid"]
    focus_warrior.res.rage = 60
    focus_warrior.res.mp = focus_warrior.res.mp_max
    assert effects.active_resource_id(focus_warrior) == "rage", "Warrior should use rage even when Focus Charm grants a mana pool"
    assert effects.challenger_resource_stance_mode(focus_warrior) == "might", "Warrior Challenger stance should key off rage, not item-granted mana"
    assert resolver.adjusted_resource_costs(focus_warrior, {"rage": 11, "mp": 5}) == {"rage": 14, "mp": 5}, "Challenger should surcharge the warrior active rage cost only"

    rage_rogue = make_match("rogue", "mage", p1_items={"armor": "challengers_chestplate", "trinket": "rage_crystal"}, seed=6211).state["p1_sid"]
    rage_rogue.res.energy = 50
    rage_rogue.res.rage = rage_rogue.res.rage_max
    assert effects.active_resource_id(rage_rogue) == "energy", "Rogue should use energy even when Rage Crystal grants a rage pool"
    assert effects.challenger_resource_stance_mode(rage_rogue) == "wrath", "Rogue Challenger stance should key off energy, not item-granted rage"
    assert effects.resource_gain_multiplier_from_passives(rage_rogue, "energy") == 1.30, "Challenger Wrath should boost rogue active energy gains"
    assert effects.resource_gain_multiplier_from_passives(rage_rogue, "rage") == 1.25, "Rage Crystal should still boost rage without inheriting Challenger's active-resource bonus"

    druid = make_match("druid", "mage", p1_items={"armor": "challengers_chestplate"}, seed=6207).state["p1_sid"]
    assert effects.active_resource_id(druid) == "mp", "Druid with no form should use mana"
    effects.apply_form(druid, "bear_form")
    assert effects.active_resource_id(druid) == "rage", "Druid Bear Form should use rage"
    effects.apply_form(druid, "cat_form")
    assert effects.active_resource_id(druid) == "energy", "Druid Cat Form should use energy"
    return True


def scenario_challengers_chestplate_followup_fixes() -> bool:
    # ------------------------------------------------------------------
    # Issue 1: Wrath's resource-gain multiplier must also reach the normal
    # end-of-turn passive regen for the active resource.
    # ------------------------------------------------------------------
    regen_rogue = make_match("rogue", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6301).state["p1_sid"]
    regen_rogue.res.energy = 0
    assert effects.challenger_resource_stance_mode(regen_rogue) == "wrath", "Empty energy should put the rogue in Wrath"
    effects.end_of_turn(regen_rogue, [], "P1")
    assert regen_rogue.res.energy == int(effects.DEFAULTS["energy_regen_per_turn"] * 1.30), \
        "Low-resource rogue should regen int(5 * 1.30) energy from end-of-turn passive regen"

    # A mana user (paladin: base regen 4) makes the 30% boost observable after truncation.
    regen_pal = make_match("paladin", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6302).state["p1_sid"]
    regen_pal.res.mp = 0
    pal_base_regen = effects.DEFAULTS["mp_regen_per_turn"] + effects.mana_regen_from_spirit(regen_pal)
    assert effects.challenger_resource_stance_mode(regen_pal) == "wrath", "Empty mana should put the paladin in Wrath"
    effects.end_of_turn(regen_pal, [], "P1")
    assert regen_pal.res.mp == int(pal_base_regen * 1.30), \
        "Low-resource mana user should get Wrath-multiplied end-of-turn mana regen"

    # Baseline: no Chestplate leaves passive regen untouched.
    regen_plain = make_match("rogue", "warrior", seed=6303).state["p1_sid"]
    regen_plain.res.energy = 0
    effects.end_of_turn(regen_plain, [], "P1")
    assert regen_plain.res.energy == effects.DEFAULTS["energy_regen_per_turn"], \
        "Without Challenger, end-of-turn energy regen should be the unmodified default"

    # Baseline: Might (non-Wrath) does not boost regen either.
    regen_might = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6304).state["p1_sid"]
    regen_might.res.mp = 60
    might_base_regen = effects.DEFAULTS["mp_regen_per_turn"] + effects.mana_regen_from_spirit(regen_might)
    assert effects.challenger_resource_stance_mode(regen_might) == "might", "60/80 mana should be Might"
    before_might = regen_might.res.mp
    effects.end_of_turn(regen_might, [], "P1")
    assert regen_might.res.mp - before_might == might_base_regen, "Might should not boost end-of-turn regen"

    # Status-effect resource regen should use the same active-resource gain multiplier.
    effect_regen_rogue = make_match("rogue", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6330).state["p1_sid"]
    effect_regen_rogue.res.energy = 0
    effect_regen_rogue.effects.append({"id": "test_energy_regen", "name": "Test Energy Regen", "regen": {"energy": 7}})
    effect_log: list[str] = []
    effects.trigger_end_of_turn_effects(effect_regen_rogue, effect_log, "P1")
    assert effect_regen_rogue.res.energy == int(7 * 1.30), \
        "Low-resource rogue should get Wrath-multiplied status-effect energy regen"
    assert any("9 Energy" in line for line in effect_log), "Status-effect Energy log should show the adjusted amount"

    effect_regen_pal = make_match("paladin", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6331).state["p1_sid"]
    effect_regen_pal.res.mp = 0
    effect_regen_pal.effects.append({"id": "test_mana_regen", "name": "Test Mana Regen", "regen": {"mp": 7}})
    effect_log = []
    effects.trigger_end_of_turn_effects(effect_regen_pal, effect_log, "P1")
    assert effect_regen_pal.res.mp == int(7 * 1.30), \
        "Low-resource mana user should get Wrath-multiplied status-effect mana regen"
    assert any("9 Mana" in line for line in effect_log), "Status-effect Mana log should show the adjusted amount"

    effect_regen_might = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6332).state["p1_sid"]
    effect_regen_might.res.mp = 60
    effect_regen_might.effects.append({"id": "test_might_mana_regen", "name": "Test Might Mana Regen", "regen": {"mp": 7}})
    before_effect_might = effect_regen_might.res.mp
    effects.trigger_end_of_turn_effects(effect_regen_might, [], "P1")
    assert effect_regen_might.res.mp - before_effect_might == 7, "Might should not boost status-effect resource regen"

    effect_regen_plain = make_match("rogue", "warrior", seed=6333).state["p1_sid"]
    effect_regen_plain.res.energy = 0
    effect_regen_plain.effects.append({"id": "test_plain_energy_regen", "name": "Test Plain Energy Regen", "regen": {"energy": 7}})
    effects.trigger_end_of_turn_effects(effect_regen_plain, [], "P1")
    assert effect_regen_plain.res.energy == 7, "Without Challenger, status-effect energy regen should remain unchanged"

    hp_regen = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6334).state["p1_sid"]
    hp_regen.res.mp = 0
    hp_regen.res.hp = hp_regen.res.hp_max - 7
    hp_regen.effects.append({"id": "test_hp_regen", "name": "Test HP Regen", "regen": {"hp": 7}})
    effects.trigger_end_of_turn_effects(hp_regen, [], "P1")
    assert hp_regen.res.hp == hp_regen.res.hp_max, "Status-effect HP regen should not be multiplied by Challenger Wrath"

    # ------------------------------------------------------------------
    # Issue 2: the redirect stance helper must only suppress the champion's
    # stance for damage that is actually redirected. AoE / on-hit passive
    # procs bypass Blocking Defence's single-target redirect and still hit
    # the champion, so they must keep the champion's start-of-turn stance.
    # ------------------------------------------------------------------
    def aoe_proc_damage_on_hunter(hunter_mp: int) -> int | None:
        proc_match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6310)
        hunter_sid, warrior_sid = proc_match.players
        hunter = proc_match.state[hunter_sid]
        warrior = proc_match.state[warrior_sid]
        submit_turn(proc_match, "call_boar", _DEF_PASS)
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Boar should be active for the AoE-proc redirect coverage"
        hunter.res.mp = hunter_mp
        effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
        warrior.res.rage = warrior.res.rage_max
        # Deterministic magical on-hit proc so the Hunter's incoming stance is observable.
        warrior.effects.append(
            {
                "type": "item_passive",
                "source_item": "Test Blade",
                "passive": {
                    "type": "lightning_blast",
                    "trigger": "on_hit",
                    "chance": 1.0,
                    "scaling": {"atk": 5.0},
                    "dice": None,
                    "school": "magical",
                },
            }
        )
        original_dragon_roar = dict(ABILITIES["dragon_roar"])
        log_start = len(proc_match.log)
        try:
            ABILITIES["dragon_roar"] = dict(original_dragon_roar, cannot_miss=True)
            submit_turn(proc_match, _DEF_PASS, "dragon_roar")
        finally:
            ABILITIES["dragon_roar"] = original_dragon_roar
        proc_value = None
        for line in proc_match.log[log_start:]:
            match = re.search(r"blasts the target with lightning.*Deals (\d+) magic damage", line)
            if match:
                proc_value = int(match.group(1))
        return proc_value

    might_proc = aoe_proc_damage_on_hunter(50)  # 50/50 mana -> Might
    wrath_proc = aoe_proc_damage_on_hunter(0)   # 0/50 mana -> Wrath
    assert might_proc is not None and wrath_proc is not None, \
        "AoE on-hit proc should still hit the Hunter even while Blocking Defence has a live redirect pet"
    assert might_proc < wrath_proc, \
        "Challenger's incoming modifier must apply to the AoE proc (Might reduces, Wrath increases) despite the redirect pet"

    def queued_single_target_proc_damage(*, boar_hp: int, hunter_mp: int, direct_damage: int) -> tuple[int | None, int, int, int]:
        proc_match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6311)
        hunter_sid, warrior_sid = proc_match.players
        hunter = proc_match.state[hunter_sid]
        warrior = proc_match.state[warrior_sid]
        submit_turn(proc_match, "call_boar", _DEF_PASS)
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Boar should be active for queued single-target proc coverage"
        hunter.res.mp = hunter_mp
        hunter_hp_before = hunter.res.hp
        expected_hunter_might_proc = effects.resolve_incoming_damage(20, hunter, "magical", challenger_mode="might")
        boar.hp = boar_hp
        effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
        warrior.res.rage = warrior.res.rage_max
        warrior.stats["atk"] = 20
        warrior.effects.append(
            {
                "type": "item_passive",
                "source_item": "Test Thunderfury",
                "passive": {
                    "type": "lightning_blast",
                    "trigger": "on_hit",
                    "chance": 1.0,
                    "scaling": {"atk": 1.0},
                    "dice": None,
                    "school": "magical",
                },
            }
        )
        original_mortal_strike = dict(ABILITIES["mortal_strike"])
        log_start = len(proc_match.log)
        try:
            ABILITIES["mortal_strike"] = dict(original_mortal_strike, dice=None, scaling={}, flat_damage=direct_damage, cannot_miss=True)
            submit_turn(proc_match, _DEF_PASS, "mortal_strike")
        finally:
            ABILITIES["mortal_strike"] = original_mortal_strike
        proc_value = None
        for line in proc_match.log[log_start:]:
            match = re.search(r"blasts the target with lightning.*Deals (\d+) magic damage", line)
            if match:
                proc_value = int(match.group(1))
        return proc_value, hunter_hp_before - hunter.res.hp, boar.hp, expected_hunter_might_proc

    killed_proc, killed_hunter_damage, killed_boar_hp, expected_might_proc = queued_single_target_proc_damage(boar_hp=1, hunter_mp=50, direct_damage=30)
    assert killed_boar_hp <= 0, "Direct redirected hit should kill the Boar before queued proc damage lands"
    assert killed_proc == expected_might_proc, \
        "Queued passive damage that falls through to the Hunter must use the Hunter's start-of-turn Challenger Might mitigation"
    assert killed_hunter_damage == expected_might_proc, "Queued proc damage should be applied to the Hunter after the redirect pet dies"

    survived_proc, survived_hunter_damage, survived_boar_hp, _ = queued_single_target_proc_damage(boar_hp=30, hunter_mp=50, direct_damage=1)
    assert survived_proc is not None and survived_proc > 0, "Queued passive damage should still be logged when it redirects to a surviving Boar"
    assert survived_hunter_damage == 0, "Queued passive damage redirected to the Boar must not damage the Hunter"
    assert survived_boar_hp < 30, "Queued passive damage should apply to the surviving Boar"
    assert survived_proc != expected_might_proc, "Pet-redirected queued passive damage must not use the Hunter's Challenger mitigation"

    # ------------------------------------------------------------------
    # Issue 3: same-action resource gains must use the actor's start-of-turn
    # Challenger snapshot, not the live stance after the cost is paid.
    # ------------------------------------------------------------------
    snap = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6305).state["p1_sid"]
    snap.res.mp = 39
    assert effects.challenger_resource_stance_mode(snap) == "wrath", "39/80 mana is live Wrath"
    assert effects.resource_gain_multiplier_from_passives(snap, "mp") == 1.30, "Live Wrath still boosts gains for end-of-turn/external callers"
    assert effects.resource_gain_multiplier_from_passives(snap, "mp", challenger_mode="might") == 1.0, \
        "A Might start-of-turn snapshot must suppress the Wrath gain boost even when live stance is Wrath"
    assert effects.resource_gain_multiplier_from_passives(snap, "mp", challenger_mode="wrath") == 1.30, \
        "A Wrath start-of-turn snapshot keeps the gain boost"

    def crusader_strike_final_mp(start_mp: int) -> int:
        gain_match = make_match("paladin", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6320)
        pal = gain_match.state[gain_match.players[0]]
        pal.res.mp = start_mp
        original = dict(ABILITIES["crusader_strike"])
        try:
            ABILITIES["crusader_strike"] = dict(
                original, cost={"mp": 20}, resource_gain={"mp": 8}, dice=None, scaling={"atk": 0.4}, cannot_miss=True
            )
            submit_turn(gain_match, "crusader_strike", _DEF_PASS)
        finally:
            ABILITIES["crusader_strike"] = original
        return pal.res.mp

    # Might start (36/70): snapshot=Might. Cost 20 * 1.20 (Might surcharge) -> 24, mana 36->12 (live Wrath).
    # Same-action gain of 8 uses the Might snapshot (x1.0) -> 20, then end-of-turn Wrath regen 4*1.30=5 -> 25.
    # The pre-fix live-stance grant would boost the gain (8*1.30=10) and yield 27 instead.
    assert crusader_strike_final_mp(36) == 25, \
        "Same-action gain for a Might-start actor must not receive Wrath's 1.30x even after the cost drops it below threshold"
    # Wrath start (34/70): snapshot=Wrath. Cost 20 (no Wrath surcharge), mana 34->14.
    # Same-action gain 8 * 1.30 = 10 -> 24, then end-of-turn Wrath regen 5 -> 29.
    assert crusader_strike_final_mp(34) == 29, \
        "Same-action gain for a Wrath-start actor should still receive Wrath's 1.30x multiplier"

    return True


def scenario_challengers_chestplate_on_hit_proc_outgoing_stance() -> bool:
    # ------------------------------------------------------------------
    # The attacker's Challenger outgoing damage stance (Might/Wrath) must
    # scale freshly-computed on-hit proc damage (lightning_blast/void_blade,
    # Thunderfury-style, and duplicate_offensive_spell) exactly like the
    # primary hit, while the target's incoming stance stays an independent
    # modifier and strike_again (derived from the resolved primary hit) is
    # never double-multiplied.
    # ------------------------------------------------------------------
    lightning_passive = {
        "type": "item_passive",
        "source_item": "Test Thunderfury",
        "passive": {
            "type": "lightning_blast",
            "trigger": "on_hit",
            "chance": 1.0,
            "scaling": {"atk": 2.0},
            "dice": None,
            "school": "magical",
        },
    }

    # -- Attacker outgoing stance drives proc damage against a fixed target --
    # lightning_blast queues a raw ``damage_event`` (re-mitigated on landing) and
    # therefore no longer reports its speculative value through ``bonus_damage``;
    # the scaled proc damage now lives in the queued event's ``incoming``.
    def lightning_proc(attacker_mode: str) -> int:
        match = make_match("warrior", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6350)
        attacker = match.state[match.players[0]]
        target = match.state[match.players[1]]
        attacker.stats["atk"] = 40
        attacker.effects.append(dict(lightning_passive, passive=dict(lightning_passive["passive"])))
        bonus_damage, _, _, _, damage_events = effects.trigger_on_hit_passives(
            attacker,
            target,
            base_damage=10,
            damage_type="physical",
            rng=random.Random(11),
            ability=None,
            include_strike_again=False,
            attacker_challenger_mode=attacker_mode,
        )
        assert bonus_damage == 0, "Raw queued lightning proc must not contribute speculative bonus_damage"
        return sum(int(event.get("incoming", 0) or 0) for event in damage_events)

    might_proc = lightning_proc("might")
    wrath_proc = lightning_proc("wrath")
    assert might_proc > 0 and wrath_proc > 0, "Deterministic lightning_blast proc should deal damage in both stances"
    assert might_proc > wrath_proc, \
        "Attacker Challenger Might must produce a higher on-hit proc than Wrath against the same target"

    # -- Target incoming stance stays independent of the attacker stance --
    def lightning_proc_vs_challenger_target(target_mode: str) -> int:
        match = make_match(
            "warrior",
            "warrior",
            p1_items={"armor": "challengers_chestplate"},
            p2_items={"armor": "challengers_chestplate"},
            seed=6351,
        )
        attacker = match.state[match.players[0]]
        target = match.state[match.players[1]]
        attacker.stats["atk"] = 40
        attacker.effects.append(dict(lightning_passive, passive=dict(lightning_passive["passive"])))
        _, _, _, _, damage_events = effects.trigger_on_hit_passives(
            attacker,
            target,
            base_damage=10,
            damage_type="physical",
            rng=random.Random(11),
            ability=None,
            include_strike_again=False,
            target_challenger_mode=target_mode,
            attacker_challenger_mode="might",
        )
        return sum(int(event.get("incoming", 0) or 0) for event in damage_events)

    target_might_proc = lightning_proc_vs_challenger_target("might")
    target_wrath_proc = lightning_proc_vs_challenger_target("wrath")
    assert target_might_proc > 0 and target_wrath_proc > 0, "Proc against a Challenger target should still land"
    assert target_might_proc < target_wrath_proc, \
        "Target Challenger Might must reduce incoming proc damage while Wrath increases it, independent of the attacker stance"

    # -- Dragonwrath-style duplicate_offensive_spell honours attacker stance --
    duplicate_passive = {
        "type": "item_passive",
        "source_item": "Test Dragonwrath",
        "passive": {"type": "duplicate_offensive_spell", "trigger": "on_hit", "chance": 1.0},
    }
    duplicate_ability = {
        "name": "Test Bolt",
        "tags": ["attack", "spell"],
        "damage_type": "magic",
        "scaling": {"int": 2.0},
        "dice": None,
    }

    def duplicate_proc(attacker_mode: str) -> int:
        match = make_match("mage", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6352)
        attacker = match.state[match.players[0]]
        target = match.state[match.players[1]]
        attacker.stats["int"] = 40
        attacker.effects.append(dict(duplicate_passive, passive=dict(duplicate_passive["passive"])))
        bonus_damage, _, _, _, damage_events = effects.trigger_on_hit_passives(
            attacker,
            target,
            base_damage=10,
            damage_type="magic",
            rng=random.Random(11),
            ability=duplicate_ability,
            include_strike_again=False,
            attacker_challenger_mode=attacker_mode,
        )
        assert bonus_damage == 0, "Raw queued duplicate proc must not contribute speculative bonus_damage"
        return sum(int(event.get("incoming", 0) or 0) for event in damage_events)

    might_duplicate = duplicate_proc("might")
    wrath_duplicate = duplicate_proc("wrath")
    assert might_duplicate > 0 and wrath_duplicate > 0, "Duplicate spell proc should deal damage in both stances"
    assert might_duplicate > wrath_duplicate, \
        "Attacker Challenger Might must scale duplicated spell damage higher than Wrath"

    # -- strike_again is derived from the resolved hit and must not be re-scaled --
    strike_passive = {
        "type": "item_passive",
        "source_item": "Test Strike Blade",
        "passive": {"type": "strike_again", "trigger": "on_hit", "chance": 1.0, "multiplier": 0.5},
    }

    def strike_again_bonus(attacker_mode: str) -> int:
        match = make_match("warrior", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6353)
        attacker = match.state[match.players[0]]
        target = match.state[match.players[1]]
        attacker.effects.append(dict(strike_passive, passive=dict(strike_passive["passive"])))
        bonus_damage, _, _, _, _ = effects.trigger_on_hit_passives(
            attacker,
            target,
            base_damage=100,
            damage_type="physical",
            rng=random.Random(11),
            ability=None,
            include_strike_again=True,
            only_strike_again=True,
            attacker_challenger_mode=attacker_mode,
        )
        return bonus_damage

    might_strike = strike_again_bonus("might")
    wrath_strike = strike_again_bonus("wrath")
    assert might_strike == 50 and wrath_strike == 50, \
        "strike_again derives from the resolved primary hit and must not receive the attacker Challenger multiplier"

    return True


def scenario_challengers_chestplate_wildfire_dot_outgoing_snapshot() -> bool:
    def cast_from_dealt_damage_dot(*, hunter_mp: int) -> tuple[int, int]:
        ability_id = "test_challenger_from_dealt_dot"
        effect_id = "test_challenger_from_dealt_dot_effect"
        original_ability = ABILITIES.get(ability_id)
        original_effect = EFFECT_TEMPLATES.get(effect_id)
        ABILITIES[ability_id] = {
            "name": "Challenger Rupture Test",
            "requires_target": True,
            "cannot_miss": True,
            "flat_damage": 24,
            "damage_type": "magic",
            "school": "magical",
            "subschool": "fire",
            "dot": {"id": effect_id, "duration": 2, "from_dealt_damage": True},
            "tags": ["attack", "spell"],
            "classes": ["hunter"],
        }
        EFFECT_TEMPLATES[effect_id] = {
            "type": "dot",
            "name": "Challenger Rupture Test DoT",
            "duration": 2,
            "category": "dot",
            "school": "magical",
            "subschool": "fire",
            "tick_damage": 1,
        }
        try:
            match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=9010)
            hunter_sid, warrior_sid = match.players
            hunter = match.state[hunter_sid]
            warrior = match.state[warrior_sid]
            hunter.res.mp = hunter_mp
            hunter.stats["crit"] = 0
            hunter.stats["hit"] = 999

            submit_turn(match, ability_id, _DEF_PASS)

            turn_one_log = "\n".join(_turn_lines(match, 1))
            direct_match = re.search(r"Challenger Rupture Test\. Deals (\d+) damage", turn_one_log)
            dot = next((fx for fx in warrior.effects if fx.get("id") == effect_id), None)
            assert direct_match is not None, "from_dealt_damage Challenger test direct hit should land"
            assert dot is not None, "from_dealt_damage Challenger test should apply its DoT"
            direct_damage = int(direct_match.group(1))
            stored_tick_damage = int(dot.get("tick_damage", 0) or 0)
            assert stored_tick_damage == direct_damage // 2, "from_dealt_damage DoT should store resolved direct damage divided by duration without a second Challenger multiplier"
            return direct_damage, stored_tick_damage
        finally:
            if original_ability is None:
                ABILITIES.pop(ability_id, None)
            else:
                ABILITIES[ability_id] = original_ability
            if original_effect is None:
                EFFECT_TEMPLATES.pop(effect_id, None)
            else:
                EFFECT_TEMPLATES[effect_id] = original_effect

    def cast_focus_independent_dot(*, focus_charm: bool, incoming_after_action: bool) -> tuple[int, int, int]:
        ability_id = "test_focus_independent_dot"
        effect_id = "test_focus_independent_dot_effect"
        counter_ability_id = "test_focus_threshold_hit"
        original_ability = ABILITIES.get(ability_id)
        original_counter_ability = ABILITIES.get(counter_ability_id)
        original_effect = EFFECT_TEMPLATES.get(effect_id)
        ABILITIES[ability_id] = {
            "name": "Focus Ember Test",
            "requires_target": True,
            "cannot_miss": True,
            "flat_damage": 20,
            "damage_type": "magic",
            "school": "magical",
            "subschool": "fire",
            "dot": {"id": effect_id, "duration": 2, "tick_damage": 10, "school": "magical", "subschool": "fire"},
            "tags": ["attack", "spell"],
            "classes": ["hunter"],
        }
        ABILITIES[counter_ability_id] = {
            "name": "Heavy Test Strike",
            "requires_target": True,
            "cannot_miss": True,
            "flat_damage": 45,
            "damage_type": "physical",
            "school": "physical",
            "tags": ["attack"],
            "classes": ["warrior"],
        }
        EFFECT_TEMPLATES[effect_id] = {
            "type": "dot",
            "name": "Focus Ember Test DoT",
            "duration": 2,
            "category": "dot",
            "school": "magical",
            "subschool": "fire",
            "tick_damage": 1,
        }
        try:
            match = make_match(
                "hunter",
                "warrior",
                p1_items={"trinket": "focus_charm"} if focus_charm else None,
                seed=9011,
            )
            hunter_sid, warrior_sid = match.players
            hunter = match.state[hunter_sid]
            warrior = match.state[warrior_sid]
            hunter.stats["crit"] = 0
            hunter.stats["hit"] = 999

            submit_turn(match, ability_id, counter_ability_id if incoming_after_action else _DEF_PASS)

            turn_one_log = "\n".join(_turn_lines(match, 1))
            direct_match = re.search(r"Focus Ember Test\. Deals (\d+) damage", turn_one_log)
            dot = next((fx for fx in warrior.effects if fx.get("id") == effect_id), None)
            assert direct_match is not None, "Focus independent DoT test direct hit should land"
            assert dot is not None, "Focus independent DoT test should apply its DoT"
            return int(direct_match.group(1)), int(dot.get("tick_damage", 0) or 0), hunter.res.hp
        finally:
            if original_ability is None:
                ABILITIES.pop(ability_id, None)
            else:
                ABILITIES[ability_id] = original_ability
            if original_counter_ability is None:
                ABILITIES.pop(counter_ability_id, None)
            else:
                ABILITIES[counter_ability_id] = original_counter_ability
            if original_effect is None:
                EFFECT_TEMPLATES.pop(effect_id, None)
            else:
                EFFECT_TEMPLATES[effect_id] = original_effect

    focus_direct, focus_stored, focus_end_hp = cast_focus_independent_dot(focus_charm=True, incoming_after_action=True)
    baseline_direct, baseline_stored, _ = cast_focus_independent_dot(focus_charm=False, incoming_after_action=False)
    assert focus_end_hp < 70, "Counter hit should move the Focus Charm actor below its HP threshold after action calculation"
    assert focus_direct > baseline_direct, "Independent DoT direct hit should use the action-time Focus Charm outgoing multiplier"
    assert focus_stored == int(baseline_stored * 1.10), "Independent DoT tick_damage should use the same action-time Focus Charm multiplier as the direct hit"

    def cast_challenger_pure_dot(class_id: str, ability_id: str, dot_id: str, *, mp: int) -> tuple[int, int]:
        match = make_match(class_id, "warrior", p1_items={"armor": "challengers_chestplate"}, seed=9020)
        actor_sid, target_sid = match.players
        actor = match.state[actor_sid]
        target = match.state[target_sid]
        actor.res.mp = mp
        actor.stats["acc"] = 999
        actor.stats["crit"] = 0
        if ability_id == "devouring_plague":
            effects.apply_effect_by_id(actor, "shadowy_insight")

        submit_turn(match, ability_id, _DEF_PASS)

        dot = next((fx for fx in target.effects if fx.get("id") == dot_id), None)
        assert dot is not None, f"{ability_id} should apply its pure DoT"
        return int(dot.get("tick_damage", 0) or 0), actor.res.mp

    def challenger_pure_dot_pair(class_id: str, ability_id: str, dot_id: str) -> tuple[int, int]:
        # Derive Might/Wrath test mana from the actor's own max resource. Hard-coded
        # 41/40 only lands above 50% for a 80-max pool; Warlock (100) and Priest
        # (110) treat those as Wrath, hiding the Might/Wrath difference.
        probe = make_match(class_id, "warrior", p1_items={"armor": "challengers_chestplate"}, seed=9020)
        mp_max = probe.state[probe.players[0]].res.mp_max
        might_mp = mp_max // 2 + 1
        wrath_mp = mp_max // 2
        might_tick, might_end_mp = cast_challenger_pure_dot(class_id, ability_id, dot_id, mp=might_mp)
        wrath_tick, _ = cast_challenger_pure_dot(class_id, ability_id, dot_id, mp=wrath_mp)
        assert might_tick > wrath_tick, f"{ability_id} stored tick_damage should use Challenger Might versus Wrath"
        assert might_end_mp <= mp_max // 2, f"{ability_id} should cross from Might into live Wrath after paying its cost"
        return might_tick, wrath_tick

    challenger_pure_dot_pair("warlock", "corruption", "corruption")
    challenger_pure_dot_pair("warlock", "unstable_affliction", "unstable_affliction")
    challenger_pure_dot_pair("priest", "vampiric_touch", "vampiric_touch")
    challenger_pure_dot_pair("priest", "devouring_plague", "devouring_plague")

    might_from_dealt_direct, might_from_dealt_stored = cast_from_dealt_damage_dot(hunter_mp=50)
    wrath_from_dealt_direct, wrath_from_dealt_stored = cast_from_dealt_damage_dot(hunter_mp=25)
    assert might_from_dealt_direct > wrath_from_dealt_direct, "from_dealt_damage direct hit should still reflect Challenger Might versus Wrath"
    assert might_from_dealt_stored == might_from_dealt_direct // 2, "Might from_dealt_damage DoT must not double-scale after resolved direct damage"
    assert wrath_from_dealt_stored == wrath_from_dealt_direct // 2, "Wrath from_dealt_damage DoT must not double-scale after resolved direct damage"

    def cast_wildfire(*, hunter_mp: int, challenger: bool) -> tuple[int, int, int, int, int]:
        match = make_match(
            "hunter",
            "warrior",
            p1_items={"armor": "challengers_chestplate"} if challenger else None,
            seed=9002,
        )
        hunter_sid, warrior_sid = match.players
        hunter = match.state[hunter_sid]
        warrior = match.state[warrior_sid]
        hunter.res.mp = hunter_mp
        hunter.stats["crit"] = 0
        hunter.stats["hit"] = 999

        submit_turn(match, "wildfire_bomb", _DEF_PASS)

        turn_one_log = "\n".join(_turn_lines(match, 1))
        direct_match = re.search(r"Wildfire Bomb\. Roll d8 = \d+\. Deals (\d+) damage", turn_one_log)
        tick_match = re.search(r"suffers (\d+) damage from Wildfire Burn", turn_one_log)
        dot = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
        assert direct_match is not None, "Wildfire Bomb direct hit should land for Challenger DoT coverage"
        assert tick_match is not None, "Wildfire Burn should tick after the direct-damage application turn"
        assert dot is not None, "Wildfire Burn should remain stored for its future tick"
        stored_tick_damage = int(dot.get("tick_damage", 0) or 0)
        expected_mitigated_tick = effects.mitigate_damage(stored_tick_damage, warrior, "magical")
        assert int(tick_match.group(1)) == expected_mitigated_tick, "Same-turn Wildfire Burn tick should use stored damage after target mitigation"

        hp_before_second_tick = warrior.res.hp
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        turn_two_log = "\n".join(_turn_lines(match, 2))
        future_tick_match = re.search(r"suffers (\d+) damage from Wildfire Burn", turn_two_log)
        assert future_tick_match is not None, "Wildfire Burn should tick again on the following turn"
        future_tick_damage = int(future_tick_match.group(1))
        assert future_tick_damage == expected_mitigated_tick, "Future Wildfire Burn tick should follow the stored tick damage after normal target mitigation"
        assert hp_before_second_tick - warrior.res.hp == expected_mitigated_tick, "Future Wildfire Burn HP loss should match the stored mitigated tick"
        return int(direct_match.group(1)), stored_tick_damage, int(tick_match.group(1)), future_tick_damage, hunter.res.mp

    might_direct, might_stored, might_tick, might_future_tick, might_end_mp = cast_wildfire(hunter_mp=50, challenger=True)
    wrath_direct, wrath_stored, wrath_tick, wrath_future_tick, _ = cast_wildfire(hunter_mp=25, challenger=True)
    assert might_direct > wrath_direct, "Wildfire Bomb direct hit should be higher in Challenger Might than Wrath"
    assert might_stored > wrath_stored, "Stored Wildfire Burn tick_damage should be higher in Challenger Might than Wrath"
    assert might_tick > wrath_tick and might_future_tick > wrath_future_tick, "Wildfire Burn ticks should reflect the stored Challenger-adjusted tick_damage"

    crossing_direct, crossing_stored, crossing_tick, _, crossing_end_mp = cast_wildfire(hunter_mp=26, challenger=True)
    assert crossing_end_mp <= 25, "Wildfire Bomb should spend the Hunter from Might into live Wrath for the crossing-threshold case"
    assert crossing_direct == might_direct, "Crossing-threshold Wildfire Bomb direct hit should use the start-of-turn Might snapshot"
    assert crossing_stored == might_stored, "Crossing-threshold Wildfire Burn tick_damage should use the start-of-turn Might snapshot"
    assert crossing_tick == might_tick, "Crossing-threshold Wildfire Burn tick should use the Might-snapshotted stored value"

    baseline_direct, baseline_stored, baseline_tick, baseline_future_tick, _ = cast_wildfire(hunter_mp=50, challenger=False)
    assert baseline_direct == 11, "Baseline non-Challenger Wildfire Bomb direct hit should remain unchanged for the deterministic roll"
    assert baseline_stored == 8, "Baseline non-Challenger Wildfire Burn stored tick_damage should remain unchanged"
    assert baseline_tick == 6 and baseline_future_tick == 6, "Baseline Wildfire Burn ticks should remain unchanged after target mitigation"
    return True


def scenario_item_passive_effect_panel_labels_and_descriptions() -> bool:
    focus_match = make_match("mage", "warrior", p1_items={"trinket": "focus_charm"}, seed=6107)
    focus_owner = focus_match.state[focus_match.players[0]]
    focus_effect = next(effect for effect in focus_owner.effects if effect.get("source_item_id") == "focus_charm")
    assert focus_effect.get("school") == "magical", "Focus Charm item passive should remain in the magical buff bucket"
    assert focus_effect.get("dispellable") is False, "Focus Charm item passive should remain non-dispellable"

    focus_owner.res.hp = int(focus_owner.res.hp_max * 0.8)
    focus_panel_high = effects.build_effect_panel_payload(focus_owner)
    focus_entries_high = {entry.get("name"): entry for entry in focus_panel_high["buffs_magical"]}
    assert focus_entries_high.get("Focus Charm", {}).get("description") == "Deal 10% more damage", "Focus Charm tooltip text should stay separate from its row label"
    assert all(entry.get("name") != "Focus Charm - Deal 10% more damage" for entry in focus_panel_high["buffs_magical"]), "Focus Charm row label must not combine name and description"

    focus_owner.res.hp = int(focus_owner.res.hp_max * 0.7)
    focus_panel_threshold = effects.build_effect_panel_payload(focus_owner)
    assert not any(entry.get("name") == "Focus Charm" for entry in focus_panel_threshold["buffs_magical"]), "Focus Charm should only be visible above 70% HP"

    rage_match = make_match("warrior", "mage", p1_items={"trinket": "rage_crystal"}, seed=6108)
    rage_owner = rage_match.state[rage_match.players[0]]
    rage_effect = next(effect for effect in rage_owner.effects if effect.get("source_item_id") == "rage_crystal")
    assert rage_effect.get("school") == "magical", "Rage Crystal item passive should remain in the magical buff bucket"
    assert rage_effect.get("dispellable") is False, "Rage Crystal item passive should remain non-dispellable"

    rage_owner.res.hp = int(rage_owner.res.hp_max * 0.5)
    rage_panel_mid = effects.build_effect_panel_payload(rage_owner)
    rage_mid_entries = {entry.get("name"): entry for entry in rage_panel_mid["buffs_magical"]}
    assert "Rage Crystal" not in rage_mid_entries, "Rage Crystal damage buff should only be visible below 30% HP"
    assert rage_mid_entries.get("Crystalized Rage", {}).get("description") == "Gain 15% more rage from all sources", "Crystalized Rage should always show while Rage Crystal is equipped"

    rage_owner.res.hp = max(1, int(rage_owner.res.hp_max * 0.2))
    rage_panel_low = effects.build_effect_panel_payload(rage_owner)
    rage_low_entries = {entry.get("name"): entry for entry in rage_panel_low["buffs_magical"]}
    assert rage_low_entries.get("Rage Crystal", {}).get("description") == "Deal 15% more damage", "Rage Crystal tooltip text should stay separate from its row label"
    assert rage_low_entries.get("Crystalized Rage", {}).get("description") == "Gain 15% more rage from all sources", "Crystalized Rage tooltip text should match exact copy"
    assert all(" - " not in str(entry.get("name") or "") for entry in rage_panel_low["buffs_magical"] if entry.get("name") in {"Rage Crystal", "Crystalized Rage"}), "Item passive row labels must remain short labels only"
    return True


def scenario_unstable_arcanocrystal_grants_expected_item_stats() -> bool:
    item = resolver.ITEMS.get("unstable_arcanocrystal")
    assert item is not None, "Unstable Arcanocrystal must be defined in items.py"
    assert item.get("name") == "Unstable Arcanocrystal", "item display name should match"
    assert item.get("slot") == "trinket", "Unstable Arcanocrystal should occupy the trinket slot"
    assert item.get("color") == "#a335ee", "Unstable Arcanocrystal should use the epic color"

    expected_mods = {
        "atk": 3,
        "int": 3,
        "spirit": 3,
        "acc": 3,
        "crit": 3,
        "eva": 3,
        "def": 3,
        "nature_resist": 3,
    }
    assert item.get("mods") == expected_mods, "Unstable Arcanocrystal must grant exactly the specified +3 stats and +3 Nature Resistance"

    # No passive/proc/active and no class restriction (available to every class).
    assert "passive" not in item, "Unstable Arcanocrystal must have no passive"
    assert "classes" not in item, "Unstable Arcanocrystal must be available to every class"

    # Equipping the trinket flows all stats through the shared stat aggregation,
    # and the nature_resist stat is readable through the canonical stat path.
    equipped = make_match("mage", "warrior", p1_items={"trinket": "unstable_arcanocrystal"}, seed=6201)
    owner = equipped.state[equipped.players[0]]
    baseline = make_match("mage", "warrior", seed=6201).state[equipped.players[0]]
    for stat, delta in expected_mods.items():
        assert effects.modify_stat(owner, stat, owner.stats.get(stat, 0)) == effects.modify_stat(baseline, stat, baseline.stats.get(stat, 0)) + delta, f"equipping should grant +{delta} {stat}"
    assert owner.stats.get("nature_resist", 0) == 3, "nature_resist should be aggregated into the equipped owner's stats"
    assert baseline.stats.get("nature_resist", 0) == 0, "absent resistance stats must default to zero for characters without the item"
    return True


def scenario_unstable_arcanocrystal_documented_in_duel_html() -> bool:
    duel_html = _detect_duel_html_path().read_text(encoding="utf-8")

    # Backend item id present in the frontend command surface (id parity).
    assert "unstable_arcanocrystal" in resolver.ITEMS, "backend item id must exist"
    assert "/item trinket unstable_arcanocrystal" in duel_html, "frontend command reference must use the backend item id"

    # Player-facing documentation entry and epic color.
    assert "Unstable Arcanocrystal" in duel_html, "item should appear in duel.html docs"
    assert "#a335ee" in duel_html, "epic color must be present in duel.html"

    # Tooltip metadata: epic color on the item name and Nature Resistance listed.
    assert '"Unstable Arcanocrystal": {' in duel_html, "item tooltip metadata entry should exist"
    assert "+3 Nature Resistance" in duel_html, "tooltip/docs must list +3 Nature Resistance"
    assert "Nature Resistance reduces incoming Nature damage." in duel_html, "docs must explain Nature Resistance without claiming all magic reduction"
    assert "reduces all magical damage" not in duel_html, "docs must not describe Nature Resistance as reducing all magical damage"
    return True
