"""Damage pipeline regression scenarios (mitigation, absorbs, item procs, resolution stages, source-kind/subschool plumbing).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import random
import re

from harness import (
    ABILITIES,
    EFFECT_TEMPLATES,
    PETS,
    PET_AI,
    PetState,
    _has_effect,
    _player_states,
    _turn_lines,
    effects,
    make_match,
    resolver,
    submit_turn,
)

from .helpers import (
    _DEF_PASS,
    _add_pet,
    _pet_took_damage_or_died,
    _active_pet,
    _expected_mitigated,
)

from .test_classes_abilities import (
    scenario_shield_of_vengeance_explosion_flushes_stealth_break_log,
)
from .test_dots_hots import (
    scenario_agony_ramp_progression_restored,
)
from .test_pets import (
    scenario_hunter_boar_redirect_same_turn_brace,
    scenario_pet_specials_are_blocked_while_pet_is_ccd,
)
from .test_resources import (
    scenario_recover_log_shows_only_nonzero_resources_and_uses_mana_wording,
)


def scenario_healing_resolves_from_negative_hp_before_winner_check() -> bool:
    match = make_match("priest", "warrior", seed=123)
    priest_sid, warrior_sid = match.players
    priest = match.state[priest_sid]
    warrior = match.state[warrior_sid]

    priest.res.hp = -6
    # Zero mitigation so the fixed 12-damage tick (and its 100% lifesteal)
    # lands exactly and the negative-HP arithmetic can be pinned precisely.
    warrior.stats["def"] = 0
    warrior.stats["magic_resist"] = 0
    effects.apply_effect_by_id(
        warrior,
        "devouring_plague",
        overrides={"duration": 2, "tick_damage": 12, "source_sid": priest_sid, "lifesteal_pct": 1.0},
    )
    warrior_before = warrior.res.hp

    submit_turn(match, _DEF_PASS, _DEF_PASS)

    assert warrior_before - warrior.res.hp == 12, "Setup: the unmitigated tick should deal exactly 12 HP damage"
    assert priest.res.hp == 6, "Healing must apply to the actual negative HP value (-6 + 12 = 6), never lower-clamping transient negative HP to zero first"
    assert priest.res.hp > 0, "DoT lifesteal should revive a source from negative HP in the same turn"
    assert match.phase != "ended", "Winner finalization should happen after same-turn healing/lifesteal resolves"
    assert any("heals 12 HP from Devouring Plague." in line for line in match.log), "Lifesteal log should report the actual healed amount"
    return True


def scenario_partial_healing_keeps_hp_negative_until_winner_check() -> bool:
    """Healing smaller than the HP deficit leaves the champion negative.

    The harness submit_turn() invariant assumes non-negative end-of-turn HP,
    so this lethal-outcome contract drives the same public submit/resolve flow
    directly.
    """
    match = make_match("priest", "warrior", seed=124)
    priest_sid, warrior_sid = match.players
    priest = match.state[priest_sid]
    warrior = match.state[warrior_sid]

    priest.res.hp = -10
    # Zero mitigation so the fixed 4-damage tick (and its 100% lifesteal) lands exactly.
    warrior.stats["def"] = 0
    warrior.stats["magic_resist"] = 0
    effects.apply_effect_by_id(
        warrior,
        "devouring_plague",
        overrides={"duration": 2, "tick_damage": 4, "source_sid": priest_sid, "lifesteal_pct": 1.0},
    )
    warrior_before = warrior.res.hp
    turn_before = match.turn

    resolver.submit_action(match, priest_sid, {"ability_id": _DEF_PASS})
    resolver.submit_action(match, warrior_sid, {"ability_id": _DEF_PASS})
    resolver.resolve_turn(match)

    assert match.turn == turn_before + 1, "Turn should resolve exactly once"
    assert warrior_before - warrior.res.hp == 4, "Setup: the unmitigated tick should deal exactly 4 HP damage"
    assert priest.res.hp == -6, "Healing must apply to the actual negative HP value (-10 + 4 = -6), not to a zero-clamped value"
    assert match.phase == "ended" and match.winner == warrior_sid, "A champion still below zero after all healing resolves must lose at the final winner check"
    return True


def scenario_action_time_healing_applies_before_direct_damage() -> bool:
    """Both champions' action-time healing completes before direct damage lands.

    Near-cap healing makes the order observable: the heal caps at hp_max first
    (crediting only the 5 HP that fit), then the enemy's direct damage applies
    to the capped value. Deferring the heal until after direct damage would
    credit more than 5 and leave a different final HP.
    """
    match = make_match("warrior", "paladin", seed=6509)
    warrior_sid, paladin_sid = match.players
    paladin = match.state[paladin_sid]
    assert int(paladin.stats.get("int", 0) * 2.0) > 5, "Setup: requested Holy Light healing must exceed the missing 5 HP"
    paladin.res.hp = paladin.res.hp_max - 5

    submit_turn(match, "basic_attack", "holy_light")

    dealt = int(match.combat_totals.get(warrior_sid, {}).get("damage", 0) or 0)
    assert dealt > 0, "Setup: the seeded Basic Attack must land for the ordering pin"
    assert match.combat_totals[paladin_sid]["healing"] == 5, "Action-time healing must see pre-damage HP: the near-cap heal credits only the 5 HP that fits"
    assert paladin.res.hp == paladin.res.hp_max - dealt, "Direct damage must apply to the already-healed (capped) HP value"
    turn_lines = _turn_lines(match, 1)
    warrior_idx = next(i for i, line in enumerate(turn_lines) if "cast Basic Attack" in line)
    heal_idx = next(i for i, line in enumerate(turn_lines) if "Holy Light restores 5 HP." in line)
    # Log order is presentation order (p1 action line first), not application
    # order; healing still applies before either side's direct damage.
    assert warrior_idx < heal_idx, "Current action log presentation order (p1 line before p2 line) should remain stable"
    return True


def scenario_absorb_layering() -> bool:
    match = make_match("priest", "warrior", seed=123)
    priest = match.state[match.players[0]]
    effects.apply_effect_by_id(priest, "power_word_shield", overrides={"duration": 5})
    effects.add_absorb(priest, 30, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(priest, "ice_barrier", overrides={"duration": 5})
    effects.add_absorb(priest, 20, source_name="Ice Barrier", effect_id="ice_barrier")

    total_before = effects.absorb_total(priest)
    remaining, absorbed, _ = effects.consume_absorbs(priest, 10)
    assert remaining == 0 and absorbed == 10
    total_after_damage = effects.absorb_total(priest)
    assert total_after_damage == total_before - 10

    before_keys = sorted(priest.res.absorbs.keys())
    effects.remove_effect(priest, "ice_barrier")
    after_keys = sorted(priest.res.absorbs.keys())
    assert before_keys != after_keys, "Expected one absorb layer to be removed"
    assert "ice_barrier" not in priest.res.absorbs
    assert "power_word_shield" in priest.res.absorbs
    assert effects.absorb_total(priest) == total_after_damage - 10
    return True


def scenario_aoe_resolves_targets_independently() -> bool:
    # Champion mitigation should not pre-reduce the AoE packet used for pet resolution.
    mitigation = make_match("hunter", "warlock", seed=4242)
    hunter_sid, warlock_sid = mitigation.players
    warlock = mitigation.state[warlock_sid]
    warlock.stats["def"] = 120
    _add_pet(warlock, "a_imp")
    _add_pet(warlock, "b_imp")
    imp_ids = sorted(warlock.pets.keys())
    champion_hp_before = warlock.res.hp
    pet_hp_before = {pid: warlock.pets[pid].hp for pid in imp_ids}

    submit_turn(mitigation, "multi_shot", _DEF_PASS)

    champion_delta = champion_hp_before - warlock.res.hp
    pet_deltas = {pid: pet_hp_before[pid] - (mitigation.state[warlock_sid].pets[pid].hp if pid in mitigation.state[warlock_sid].pets else 0) for pid in imp_ids}
    assert champion_delta > 0, "AoE should still resolve and damage the enemy champion"
    assert pet_deltas and all(delta > champion_delta for delta in pet_deltas.values()), "Pet AoE damage should resolve from the AoE packet with each pet's own mitigation"
    turn_lines = _turn_lines(mitigation, mitigation.turn)
    champion_log_index = next(i for i, line in enumerate(turn_lines) if "cast Multi-Shot" in line and "Deals" in line)
    pet_log_indices = [i for i, line in enumerate(turn_lines) if "Multi-Shot hits" in line and "Imp" in line]
    assert pet_log_indices and champion_log_index < pet_log_indices[0], "AoE logs should remain champion first, then pets"
    assert len(pet_log_indices) == len(imp_ids), "AoE should apply exactly once to each active pet"

    # A pet/totem immunity should not short-circuit champion resolution or other pet targets.
    pet_immune = make_match("hunter", "warlock", seed=4343)
    _, warlock_sid = pet_immune.players
    warlock = pet_immune.state[warlock_sid]
    _add_pet(warlock, "a_imp")
    _add_pet(warlock, "b_imp")
    immune_pet = warlock.pets["a_imp"]
    effects.apply_effect_by_id(immune_pet, "iceblock", overrides={"duration": 1})
    champion_hp_before = warlock.res.hp
    immune_hp_before = immune_pet.hp
    vulnerable_hp_before = warlock.pets["b_imp"].hp

    submit_turn(pet_immune, "multi_shot", _DEF_PASS)

    assert warlock.res.hp < champion_hp_before, "Pet immunity must not block champion AoE damage"
    assert warlock.pets["a_imp"].hp == immune_hp_before, "The immune pet should resolve independently and take no AoE damage"
    assert _pet_took_damage_or_died(warlock, "b_imp", vulnerable_hp_before), "Another pet should still take AoE damage when one pet is immune"
    assert sum(1 for line in _turn_lines(pet_immune, pet_immune.turn) if "Multi-Shot hits" in line and "Imp" in line) == 1, "Only the non-immune pet should emit a damage hit log"
    return True


def scenario_winner_summary_logs_after_pet_phase_and_end_of_turn_resolution() -> bool:
    match = make_match("hunter", "warlock", seed=123)
    hunter_sid, warlock_sid = match.players
    hunter = match.state[hunter_sid]
    warlock = match.state[warlock_sid]

    submit_turn(match, "call_serpent", _DEF_PASS)
    hunter_pet_turn = _turn_lines(match, 1)
    assert any("Emerald Serpent" in line for line in hunter_pet_turn), "Setup turn should summon a hunter pet"
    assert match.phase != "ended", "Setup turn should keep the duel active for ordering coverage"
    warlock.res.hp = warlock.res.hp_max

    warlock.res.hp = 5
    submit_turn(match, _DEF_PASS, _DEF_PASS)

    latest_turn = _turn_lines(match, 2)
    pet_attack_idx = next((i for i, line in enumerate(latest_turn) if "Emerald Serpent" in line and ("melees the target" in line or "breathes lightning" in line)), -1)
    summary_idx = next((i for i, line in enumerate(latest_turn) if line.startswith("Post-Combat Summary|")), -1)
    winner_idx = next((i for i, line in enumerate(latest_turn) if "wins the duel." in line), -1)
    assert pet_attack_idx != -1, "Pet phase should execute before the duel concludes"
    assert summary_idx != -1 and winner_idx != -1, "Summary and winner logs should be present on lethal turns"
    assert pet_attack_idx < summary_idx < winner_idx, "Pet phase should complete before summary/winner output"
    return True


def scenario_phase_c_pass1_early_resolution_stages_are_preserved() -> bool:
    # pre_action_state: stealth-at-start still causes same-turn stun attempts to miss.
    stealth_match = make_match("rogue", "paladin", seed=606)
    submit_turn(stealth_match, "vanish", "hammer_of_justice")
    rogue = stealth_match.state[stealth_match.players[0]]
    assert _has_effect(rogue, "stealth"), "Vanish stealth should remain when same-turn stun targets stealth-at-start"
    assert not _has_effect(rogue, "stunned"), "Stealth snapshot should keep same-turn stun from landing"

    # action_selection_modifiers: cooldown / resource / proc / form / circle checks unchanged.
    cooldown_match = make_match("paladin", "warrior", seed=607)
    submit_turn(cooldown_match, "lay_on_hands", _DEF_PASS)
    submit_turn(cooldown_match, "lay_on_hands", _DEF_PASS)
    cooldown_turn = cooldown_match.log[cooldown_match.log.index("Turn 2") + 1:]
    assert any("tried to use Lay on Hands but it is on cooldown." in line for line in cooldown_turn), "Cooldown-gated ability should fail with existing log"

    resource_match = make_match("druid", "warrior", seed=608)
    submit_turn(resource_match, "bear", _DEF_PASS)
    submit_turn(resource_match, "frenzied_regeneration", _DEF_PASS)
    resource_turn = resource_match.log[resource_match.log.index("Turn 2") + 1:]
    expected_resource_log = f"{resource_match.players[0][:5]} tried to use Frenzied Regeneration but didn't have enough rage"
    assert any(line == expected_resource_log for line in resource_turn), "Resource-gated ability should include actor, ability name, and missing resource"

    proc_match = make_match("mage", "warrior", seed=609)
    submit_turn(proc_match, "pyroblast", _DEF_PASS)
    proc_turn = proc_match.log[proc_match.log.index("Turn 1") + 1:]
    assert any("Pyroblast requires Hot Streak." in line for line in proc_turn), "Proc-gated ability should preserve requires-effect log"

    form_match = make_match("druid", "warrior", seed=610)
    submit_turn(form_match, "maul", _DEF_PASS)
    form_turn = form_match.log[form_match.log.index("Turn 1") + 1:]
    assert any("Druid tried to use Maul but wasn't in Bear Form." in line for line in form_turn), "Form-gated ability should preserve requires-form behavior with updated wording"

    circle_match = make_match("warlock", "warrior", seed=611)
    submit_turn(circle_match, "teleport", _DEF_PASS)
    circle_turn = circle_match.log[circle_match.log.index("Turn 1") + 1:]
    assert any("Demonic Circle is required." in line for line in circle_turn), "Circle-gated ability should preserve existing log"

    mana_match = make_match("paladin", "warrior", seed=613)
    mana_match.state[mana_match.players[0]].res.mp = 0
    submit_turn(mana_match, "lay_on_hands", _DEF_PASS)
    mana_turn = mana_match.log[mana_match.log.index("Turn 1") + 1:]
    expected_mana_log = f"{mana_match.players[0][:5]} tried to use Lay on Hands but didn't have enough mana"
    assert any(line == expected_mana_log for line in mana_turn), "MP resource failures should display 'mana' in logs"

    # action_denial: feared actors still cannot act; same-turn mutual denial still allows both CC actions.
    fear_match = make_match("warlock", "warrior", seed=612)
    submit_turn(fear_match, "fear", _DEF_PASS)
    submit_turn(fear_match, _DEF_PASS, "basic_attack")
    fear_turn = fear_match.log[fear_match.log.index("Turn 2") + 1:]
    assert any("tries to use Basic Attack but is feared and cannot act." in line for line in fear_turn), "Feared actor should still be denied with existing log"

    mutual_match = make_match("paladin", "rogue", seed=789)
    effects.remove_effect(mutual_match.state[mutual_match.players[1]], "stealth")
    submit_turn(mutual_match, "hammer_of_justice", "kidney_shot")
    paladin_stun = next((fx for fx in mutual_match.state[mutual_match.players[0]].effects if fx.get("id") == "stunned"), None)
    rogue_stun = next((fx for fx in mutual_match.state[mutual_match.players[1]].effects if fx.get("id") == "stunned"), None)
    assert paladin_stun is not None and rogue_stun is not None, "Mutual same-turn denial behavior should keep both stuns applying on that turn"

    # no spillover: representative later-stage damage still applies.
    combat_match = make_match("warrior", "mage", seed=614)
    mage_hp_before = combat_match.state[combat_match.players[1]].res.hp
    submit_turn(combat_match, "overpower", _DEF_PASS)
    mage_hp_after = combat_match.state[combat_match.players[1]].res.hp
    assert mage_hp_after < mage_hp_before, "Representative damage application should remain unchanged after early-stage structuring"
    return True


def scenario_phase_c_prompt1_middle_resolution_stages_are_preserved() -> bool:
    # pre_resolution_protection: full and partial immunity behavior stays unchanged.
    immunity_match = make_match("mage", "warrior", seed=620)
    mage_sid, warrior_sid = immunity_match.players
    submit_turn(immunity_match, "iceblock", "mortal_strike")
    mage = immunity_match.state[mage_sid]
    assert mage.res.hp == mage.res.hp_max, "Ice Block should still prevent incoming single-target damage during pre-resolution protection"

    cloak_match = make_match("rogue", "mage", seed=621)
    rogue_sid, mage_sid = cloak_match.players
    effects.remove_effect(cloak_match.state[rogue_sid], "stealth")
    cloak_match.state[mage_sid].stats["acc"] = 999
    submit_turn(cloak_match, "cloak", "fireball")
    rogue = cloak_match.state[rogue_sid]
    assert rogue.res.hp == rogue.res.hp_max, "Cloak should still block magical damage in pre-resolution protection"

    turtle_match = make_match("hunter", "rogue", seed=622)
    hunter_sid, _ = turtle_match.players
    submit_turn(turtle_match, "turtle", "eviscerate")
    turtle_turn = _turn_lines(turtle_match, 1)
    assert any("Target evades the attack — Miss!" in line for line in turtle_turn), "Turtle single-target miss behavior should remain unchanged"
    hunter = turtle_match.state[hunter_sid]
    assert hunter.res.hp == hunter.res.hp_max, "Turtle should still block same-turn single-target damage"

    stealth_match = make_match("rogue", "warrior", seed=623)
    submit_turn(stealth_match, "vanish", "basic_attack")
    stealth_turn = _turn_lines(stealth_match, 1)
    assert any("Target is stealthed — Miss!" in line for line in stealth_turn), "Stealth target invalidation should remain unchanged"

    # target_resolution: redirects are preserved and AoE still bypasses redirect.
    redirect_match = make_match("hunter", "warrior", seed=624)
    hunter_sid, warrior_sid = redirect_match.players
    submit_turn(redirect_match, "call_boar", _DEF_PASS)
    hunter = redirect_match.state[hunter_sid]
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active for redirect coverage"
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})

    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    submit_turn(redirect_match, _DEF_PASS, "basic_attack")
    redirect_turn = _turn_lines(redirect_match, 2)
    assert hunter.res.hp == hunter_hp_before, "Single-target damage should still redirect away from the hunter"
    assert boar.hp < boar_hp_before, "Barrens Boar should still receive redirected single-target damage"
    assert any("Barrens Boar intercepts Basic Attack" in line for line in redirect_turn), "Redirect intercept log should remain unchanged"

    hunter_hp_before_aoe = hunter.res.hp
    redirect_match.state[warrior_sid].res.rage = 10
    submit_turn(redirect_match, _DEF_PASS, "dragon_roar")
    assert hunter.res.hp < hunter_hp_before_aoe, "AoE damage should still bypass redirect and hit the champion"

    # hit_resolution: blink-like misses, disengage custom miss wording, and evasion are unchanged.
    blink_match = make_match("mage", "warrior", seed=625)
    submit_turn(blink_match, "blink", "basic_attack")
    blink_turn = _turn_lines(blink_match, 1)
    assert any("Target blinks away — Miss." in line for line in blink_turn), "Blink-like miss wording should remain unchanged"

    disengage_match = make_match("hunter", "warrior", seed=626)
    submit_turn(disengage_match, "disengage", "basic_attack")
    disengage_turn = _turn_lines(disengage_match, 1)
    assert any("Target leaps away — Miss." in line for line in disengage_turn), "Disengage custom miss wording should remain unchanged"

    evasion_match = make_match("rogue", "warrior", seed=627)
    effects.remove_effect(evasion_match.state[evasion_match.players[0]], "stealth")
    submit_turn(evasion_match, "evasion", "basic_attack")
    evasion_turn = _turn_lines(evasion_match, 1)
    assert any("Evaded!" in line for line in evasion_turn), "Evasion forced-miss behavior should remain unchanged"

    aoe_blink_match = make_match("mage", "warrior", seed=628)
    aoe_blink_match.state[aoe_blink_match.players[1]].res.rage = 10
    submit_turn(aoe_blink_match, "blink", "dragon_roar")
    mage = aoe_blink_match.state[aoe_blink_match.players[0]]
    assert any("blinks away — Miss." in line for line in _turn_lines(aoe_blink_match, 1)), "AoE should preserve champion blink miss logging"
    assert mage.res.hp == mage.res.hp_max, "AoE should still skip champion damage while blink-like untargetable is active"

    # no spillover: representative later stage remains unchanged.
    no_spill_match = make_match("warrior", "mage", seed=629)
    no_spill_match.state[no_spill_match.players[0]].res.rage = 10
    mage_hp_before = no_spill_match.state[no_spill_match.players[1]].res.hp
    submit_turn(no_spill_match, "dragon_roar", _DEF_PASS)
    mage_hp_after = no_spill_match.state[no_spill_match.players[1]].res.hp
    assert mage_hp_after < mage_hp_before, "Damage application should remain unchanged by middle-stage restructuring"
    return True


def scenario_immediate_path_denial_precedes_selection_failures() -> bool:
    match = make_match("warlock", "warrior", seed=615)
    warlock = match.state[match.players[0]]
    effects.apply_effect_by_id(warlock, "feared", overrides={"duration": 2})

    submit_turn(match, "teleport", _DEF_PASS)
    latest_turn = match.log[match.log.index("Turn 1") + 1:]

    assert any("tries to use Demonic Circle: Teleport but is feared and cannot act." in line for line in latest_turn), "Immediate-path denial should win over selection failures"
    assert not any("Demonic Circle is required." in line for line in latest_turn), "Immediate-path selection checks should not pre-empt denial logs"
    return True


def scenario_passive_secondary_damage_logs_own_absorb_suffix() -> bool:
    for seed in range(1, 500):
        match = make_match("warrior", "priest", p1_items={"weapon": "thunderfury"}, seed=seed)
        target = match.state[match.players[1]]
        effects.add_absorb(target, 999, source_name="Power Word: Shield", effect_id="power_word_shield")
        submit_turn(match, "overpower", _DEF_PASS)
        thunder_line = next((line for line in match.log if "blasts the target with lightning from Thunderfury" in line), None)
        if not thunder_line:
            continue
        overpower_line = next((line for line in match.log if "cast Overpower." in line), "")
        assert "absorbed by Power Word: Shield" in thunder_line, "Lightning absorb should be appended to the lightning line"
        assert overpower_line.count("absorbed by Power Word: Shield") == 1, "Primary ability line should only include its own absorb suffix"
        assert "lightning from Thunderfury" not in overpower_line, "Lightning log text should not be appended to the primary ability line"
        return True
    raise AssertionError("Could not find a deterministic Thunderfury lightning proc seed in range")


def scenario_dragonwrath_duplicate_spell_deals_real_damage() -> bool:
    for seed in range(1, 600):
        baseline = make_match("mage", "warrior", seed=seed)
        baseline_target = baseline.state[baseline.players[1]]
        baseline_before = baseline_target.res.hp
        submit_turn(baseline, "fireball", _DEF_PASS)
        baseline_loss = baseline_before - baseline_target.res.hp

        dragon = make_match("mage", "warrior", p1_items={"weapon": "dragonwrath"}, seed=seed)
        dragon_target = dragon.state[dragon.players[1]]
        dragon_before = dragon_target.res.hp
        submit_turn(dragon, "fireball", _DEF_PASS)

        duplicate_line = next((line for line in dragon.log if "duplicates Fireball" in line), None)
        if not duplicate_line:
            continue

        dragon_loss = dragon_before - dragon_target.res.hp
        assert dragon_loss > baseline_loss, "Dragonwrath duplicate must apply real HP damage, not only text"
        assert "Deals " in duplicate_line, "Duplicate strike should appear as its own damage log line"
        return True
    raise AssertionError("Could not find deterministic Dragonwrath duplicate proc seed in range")


def scenario_dragonwrath_duplicate_log_includes_class_owner_prefix() -> bool:
    for seed in range(1, 800):
        match = make_match("priest", "warrior", p1_items={"weapon": "dragonwrath"}, seed=seed)
        submit_turn(match, "mind_flay", _DEF_PASS)
        duplicate_line = next((line for line in match.log if "duplicates Mind Flay!" in line), None)
        if not duplicate_line:
            continue
        assert duplicate_line.startswith("Priest(you)'s Dragonwrath, Tarecgosa's Rest duplicates Mind Flay!"), (
            "Dragonwrath duplicate log should include class(you)'s weapon owner prefix"
        )
        return True
    raise AssertionError("Could not find deterministic Dragonwrath Mind Flay duplicate proc seed in range")


def scenario_dragonwrath_multihit_duplicate_logs_as_single_line() -> bool:
    for seed in range(1, 900):
        match = make_match("mage", "warrior", p1_items={"weapon": "dragonwrath"}, seed=seed)
        target = match.state[match.players[1]]
        effects.add_absorb(target, 999, source_name="Shield of Vengeance", effect_id="shield_of_vengeance")
        submit_turn(match, "arcane_barrage", _DEF_PASS)
        main_lines = [line for line in match.log if "cast Arcane Barrage." in line]
        duplicate_lines = [line for line in match.log if "duplicates Arcane Barrage!" in line]
        if not duplicate_lines:
            continue
        assert len(duplicate_lines) == 1, "Dragonwrath duplicate should render Arcane Barrage multi-hit text as one line"
        duplicate_line = duplicate_lines[0]
        assert "Hit 1:" in duplicate_line and "Hit 2:" in duplicate_line and "Hit 3:" in duplicate_line, "Combined duplicate line should include all Arcane Barrage hits"
        assert "__DMG_" not in duplicate_line, "Dragonwrath Arcane Barrage duplicate should replace every per-hit placeholder"
        assert "absorbed by Shield of Vengeance" in duplicate_line, "Dragonwrath duplicate should keep absorb suffixes on the duplicate log"
        assert main_lines and all("__DMG_" not in line for line in main_lines), "main Arcane Barrage multi-hit logs should remain formatted"
        return True
    raise AssertionError("Could not find deterministic Dragonwrath Arcane Barrage duplicate proc seed in range")


def scenario_passive_damage_event_preserves_multihit_instances_for_formatting_and_absorbs() -> bool:
    match = make_match("warrior", "priest", seed=123)
    target = match.state[match.players[1]]
    effects.add_absorb(target, 25, source_name="Power Word: Shield", effect_id="power_word_shield")

    original_trigger = resolver.trigger_on_hit_passives

    def fake_trigger_on_hit_passives(*args, include_strike_again=False, only_strike_again=False, **kwargs):
        if include_strike_again or only_strike_again:
            return 0, [], 0, 0, []
        return 0, [], 0, 0, [
            {
                "incoming": 60,
                "school": "magic",
                "log_template": "Passive multi-hit! Hit 1: Deals __DMG_0__ damage. Hit 2: Deals __DMG_1__ damage. Hit 3: Deals __DMG_2__ damage.",
                "damage_instances": [10, 20, 30],
            }
        ]

    resolver.trigger_on_hit_passives = fake_trigger_on_hit_passives
    try:
        submit_turn(match, "overpower", _DEF_PASS)
    finally:
        resolver.trigger_on_hit_passives = original_trigger

    passive_line = next((line for line in match.log if line.startswith("Passive multi-hit!")), None)
    assert passive_line is not None, "passive multi-hit damage event should be logged"
    assert "__DMG_" not in passive_line, "all passive multi-hit damage placeholders should be replaced"
    assert "Hit 1: Deals 10 damage." in passive_line, "first passive damage instance should stay separate"
    assert "Hit 2: Deals 20 damage." in passive_line, "second passive damage instance should stay separate"
    assert "Hit 3: Deals 30 damage." in passive_line, "third passive damage instance should stay separate"
    assert passive_line.count("absorbed by Power Word: Shield") == 2, "absorbs should be reported against the affected instances"

    single_match = make_match("warrior", "priest", seed=124)

    def fake_single_passive(*args, include_strike_again=False, only_strike_again=False, **kwargs):
        if include_strike_again or only_strike_again:
            return 0, [], 0, 0, []
        return 0, [], 0, 0, [{"incoming": 17, "log_template": "Single passive deals __DMG_0__ damage."}]

    resolver.trigger_on_hit_passives = fake_single_passive
    try:
        submit_turn(single_match, "overpower", _DEF_PASS)
    finally:
        resolver.trigger_on_hit_passives = original_trigger

    single_line = next((line for line in single_match.log if line.startswith("Single passive deals")), None)
    assert single_line == "Single passive deals 17 damage.", "single-instance passive damage events should still format correctly"
    return True


def scenario_damage_event_factories_build_normalized_plain_dicts() -> bool:
    """Unit-style checks for damage_events.py factory/normalization helpers.

    Static shape checks only — no gameplay is exercised. The factories must
    keep emitting the exact plain-dict schemas the pre-factory inline literals
    produced (key presence/absence matters: the resolver detects raw events by
    ``event.get("raw_incoming") is not None``).
    """
    import sys

    damage_events = sys.modules["games.duel.engine.damage_events"]
    damage_types = sys.modules["games.duel.engine.damage_types"]

    # Producer event without raw_incoming (strike-again shape): the optional
    # keys must be absent, not None.
    strike = damage_events.make_passive_damage_event(
        incoming=12,
        source_kind=damage_types.DAMAGE_SOURCE_STRIKE_AGAIN,
        school="physical",
        subschool=None,
        log_template="p1 strikes again for __DMG_0__ bonus damage.",
    )
    assert type(strike) is dict, "producer events must stay plain dicts"
    assert set(strike) == {"incoming", "source_kind", "school", "subschool", "log_template"}
    assert strike["incoming"] == 12
    assert strike["source_kind"] == damage_types.DAMAGE_SOURCE_STRIKE_AGAIN
    assert strike["school"] == "physical" and strike["subschool"] is None
    assert strike["log_template"] == "p1 strikes again for __DMG_0__ bonus damage."

    # Producer raw proc event (void_blade/lightning_blast shape) with per-hit
    # instances (duplicate_offensive_spell shape): raw lists are copied as-is.
    raw_event = damage_events.make_passive_damage_event(
        incoming=7,
        raw_incoming=11,
        source_kind=damage_types.DAMAGE_SOURCE_ON_HIT_PROC,
        damage_instances=[3, 4],
        raw_damage_instances=[5, 6],
        school="magical",
        subschool="shadow",
        log_template="p1 calls upon the void. Deals __DMG_0__ magic damage.",
    )
    assert set(raw_event) == {
        "incoming",
        "raw_incoming",
        "source_kind",
        "damage_instances",
        "raw_damage_instances",
        "school",
        "subschool",
        "log_template",
    }
    assert raw_event["raw_incoming"] == 11 and raw_event["incoming"] == 7
    assert raw_event["damage_instances"] == [3, 4]
    assert raw_event["raw_damage_instances"] == [5, 6]

    # Coercion rules: negatives clamp to 0, junk becomes 0, unknown/None
    # source kinds fall back to on-hit proc.
    coerced = damage_events.make_passive_damage_event(
        incoming=-5,
        raw_incoming="junk",
        source_kind="bogus_kind",
        school="physical",
        subschool=None,
        log_template="x",
    )
    assert coerced["incoming"] == 0 and coerced["raw_incoming"] == 0
    assert coerced["source_kind"] == damage_types.DAMAGE_SOURCE_ON_HIT_PROC

    # Queued event: full schema, legacy untagged events default to on-hit
    # proc, and instances are normalized with zero/junk entries dropped.
    queued = damage_events.make_queued_damage_event(
        source_name="attack",
        incoming="9",
        requires_player_mitigation=True,
        log_template="Queued deals __DMG_0__ damage.",
        damage_instances=[4, 0, None, "junk", 5],
    )
    assert type(queued) is dict, "queued events must stay plain dicts"
    assert set(queued) == {
        "type",
        "source_name",
        "incoming",
        "requires_player_mitigation",
        "source_kind",
        "school",
        "subschool",
        "log_template",
        "damage_instances",
    }
    assert queued["type"] == "damage_event"
    assert queued["incoming"] == 9 and queued["requires_player_mitigation"] is True
    assert queued["source_kind"] == damage_types.DAMAGE_SOURCE_ON_HIT_PROC
    assert queued["school"] == "physical" and queued["subschool"] is None
    assert queued["damage_instances"] == [4, 5]

    # Instances that normalize to nothing (or non-lists) must omit the key.
    for empty_instances in (None, [], [0, None], "not-a-list"):
        no_instances = damage_events.make_queued_damage_event(
            source_name="attack",
            incoming=3,
            requires_player_mitigation=False,
            log_template="x",
            damage_instances=empty_instances,
        )
        assert "damage_instances" not in no_instances

    # normalize_damage_instances mirrors the old inline queue normalization.
    assert damage_events.normalize_damage_instances([2, "3", 0, -1, None, "junk"]) == [2, 3]
    assert damage_events.normalize_damage_instances([]) is None
    assert damage_events.normalize_damage_instances((1, 2)) is None
    return True


def scenario_dragonwrath_duplicate_drain_life_heals_from_total_landed_damage() -> bool:
    for seed in range(1, 1000):
        match = make_match("warlock", "warrior", p1_items={"weapon": "dragonwrath"}, seed=seed)
        warlock_sid, warrior_sid = match.players
        warlock = match.state[warlock_sid]
        warrior = match.state[warrior_sid]
        warlock.res.hp = max(1, warlock.res.hp - 80)
        warlock_before = warlock.res.hp
        warrior_before = warrior.res.hp
        submit_turn(match, "drain_life", _DEF_PASS)
        duplicate_line = next((line for line in match.log if "duplicates Drain Life!" in line), None)
        if not duplicate_line:
            continue
        healed = warlock.res.hp - warlock_before
        dealt = warrior_before - warrior.res.hp
        assert dealt > 0, "sanity check requires Dragonwrath duplicate to deal landed damage"
        assert healed == dealt, "Drain Life heal-from-damage should include landed Dragonwrath duplicate damage"
        return True
    raise AssertionError("Could not find deterministic Dragonwrath Drain Life duplicate proc seed in range")


def scenario_dragonwrath_duplicate_drain_life_does_not_heal_from_fully_absorbed_damage() -> bool:
    for seed in range(1, 1000):
        match = make_match("warlock", "warrior", p1_items={"weapon": "dragonwrath"}, seed=seed)
        warlock_sid, warrior_sid = match.players
        warlock = match.state[warlock_sid]
        warrior = match.state[warrior_sid]
        warlock.res.hp = max(1, warlock.res.hp - 80)
        effects.add_absorb(warrior, 999, source_name="Power Word: Shield", effect_id="power_word_shield")
        warlock_before = warlock.res.hp
        warrior_before = warrior.res.hp
        submit_turn(match, "drain_life", _DEF_PASS)
        duplicate_line = next((line for line in match.log if "duplicates Drain Life!" in line), None)
        if not duplicate_line:
            continue
        healed = warlock.res.hp - warlock_before
        dealt = warrior_before - warrior.res.hp
        assert dealt == 0, "fully absorbed base+duplicate damage should deal zero HP damage"
        assert healed == 0, "heal-from-damage should not overcount absorbed duplicate damage"
        return True
    raise AssertionError("Could not find deterministic fully-absorbed Dragonwrath Drain Life duplicate proc seed in range")


def scenario_drain_life_partial_absorb_heals_only_actual_hp_damage() -> bool:
    """Damage-derived healing uses actual dealt HP damage, not raw/incoming.

    A small absorb splits the hit: only the portion that reaches HP feeds
    Drain Life's heal, distinguishing actual HP damage from incoming damage.
    """
    match = make_match("warlock", "warrior", seed=6503)
    warlock_sid, warrior_sid = match.players
    warlock = match.state[warlock_sid]
    warrior = match.state[warrior_sid]
    warlock.stats["acc"] = 999
    warrior.stats["eva"] = 0
    warlock.res.hp = warlock.res.hp - 80
    effects.add_absorb(warrior, 5, source_name="Power Word: Shield", effect_id="power_word_shield")
    warlock_before = warlock.res.hp
    warrior_before = warrior.res.hp

    submit_turn(match, "drain_life", _DEF_PASS)

    healed = warlock.res.hp - warlock_before
    dealt = warrior_before - warrior.res.hp
    assert dealt > 0, "Setup: the seeded Drain Life must land and leave HP damage after the partial absorb"
    assert effects.absorb_total(warrior) == 0, "Setup: the 5-point shield should be fully consumed by the hit"
    assert healed == dealt, "Drain Life must heal from actual dealt HP damage, excluding the absorbed portion"
    assert match.combat_totals[warlock_sid]["healing"] == dealt, "Healing totals must credit the actual healed amount"
    assert any(f"drains {dealt} life." in line for line in match.log), "Drain Life heal log should report the actual healed amount"
    return True


def scenario_fury_of_azzinoth_heal_from_dealt_includes_strike_again_damage() -> bool:
    for seed in range(1, 1000):
        match = make_match("rogue", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=seed)
        rogue_sid, warrior_sid = match.players
        rogue = match.state[rogue_sid]
        warrior = match.state[warrior_sid]
        rogue.res.hp = max(1, rogue.res.hp - 80)
        rogue_before = rogue.res.hp
        warrior_before = warrior.res.hp
        submit_turn(match, "fury_of_azzinoth", _DEF_PASS)
        strike_again_line = next((line for line in match.log if "strikes again with Twin Blades of Azzinoth" in line), None)
        if not strike_again_line:
            continue
        healed = rogue.res.hp - rogue_before
        dealt = warrior_before - warrior.res.hp
        assert dealt > 0, "sanity check requires Fury of Azzinoth to deal landed damage"
        assert healed == dealt, "heal_from_dealt_damage should include landed strike-again damage events"
        return True
    raise AssertionError("Could not find deterministic Fury of Azzinoth strike-again proc seed in range")


def scenario_damage_derived_player_healing_routes_through_shared_helper() -> bool:
    """Damage-derived player healing applies HP through effects.apply_player_healing().

    Migrated paths: Fury of Azzinoth (full heal_from_dealt_damage), Drain Life
    (fractional heal_from_damage), periodic DoT lifesteal_pct (Devouring
    Plague), and apply_damage()'s Mindgames damage-to-healing branch. Every
    heal amount derives from actual resolved HP damage, and the Mindgames
    branch keeps the nominal converted amount for mechanic resolution while
    reporting the actual gain. Effect/HoT regeneration (Healing Stream)
    now routes through the helper as well (full coverage in
    scenario_passive_and_end_of_turn_player_healing_routes_through_shared_helper).
    """
    original = effects.apply_player_healing
    assert resolver.apply_player_healing is original, "resolver should share the effects.apply_player_healing primitive"
    calls: list[tuple[object, int, int]] = []

    def spy(target, amount):
        gained = original(target, amount)
        calls.append((target, int(amount), int(gained)))
        return gained

    # resolver imports the function directly, so patch the symbol resolver
    # actually calls in addition to the effects module attribute.
    effects.apply_player_healing = spy
    resolver.apply_player_healing = spy
    try:
        # Fury of Azzinoth: one post-damage helper call requesting the total
        # actual dealt HP damage (seed 6510 includes a strike-again event).
        fury = make_match("rogue", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=6510)
        rogue_sid, fury_warrior_sid = fury.players
        rogue = fury.state[rogue_sid]
        fury_warrior = fury.state[fury_warrior_sid]
        rogue.res.hp = max(1, rogue.res.hp - 80)
        fury_warrior_before = fury_warrior.res.hp
        calls.clear()
        submit_turn(fury, "fury_of_azzinoth", _DEF_PASS)
        dealt = fury_warrior_before - fury_warrior.res.hp
        assert dealt > 0, "Setup: Fury of Azzinoth should land HP damage"
        assert any("strikes again with Twin Blades of Azzinoth" in line for line in fury.log), "Setup: seed 6510 should include a strike-again damage event"
        assert len(calls) == 1, "Fury of Azzinoth should heal through exactly one apply_player_healing call"
        assert calls[0][0] is rogue, "heal_from_dealt_damage should heal the acting rogue"
        assert calls[0][1] == dealt, "Requested healing should equal the total actual dealt HP damage, including strike-again damage"
        assert calls[0][2] == dealt, "Setup: the 80-HP deficit should keep the full heal below the cap"
        assert fury.combat_totals[rogue_sid]["healing"] == dealt, "Totals should credit the helper's actual gain"
        assert any(f"heals {dealt} HP from Fury of Azzinoth." in line for line in _turn_lines(fury, 1)), "Fury of Azzinoth heal log wording should be unchanged"

        # Drain Life: one helper call requesting int(actual dealt HP damage *
        # heal_from_damage); the absorbed portion contributes nothing.
        drain = make_match("warlock", "warrior", seed=6503)
        warlock_sid, drain_warrior_sid = drain.players
        warlock = drain.state[warlock_sid]
        drain_warrior = drain.state[drain_warrior_sid]
        warlock.stats["acc"] = 999
        drain_warrior.stats["eva"] = 0
        warlock.res.hp = warlock.res.hp - 80
        effects.add_absorb(drain_warrior, 5, source_name="Power Word: Shield", effect_id="power_word_shield")
        drain_warrior_before = drain_warrior.res.hp
        calls.clear()
        submit_turn(drain, "drain_life", _DEF_PASS)
        drain_dealt = drain_warrior_before - drain_warrior.res.hp
        assert drain_dealt > 0, "Setup: the seeded Drain Life should leave HP damage past the partial absorb"
        assert effects.absorb_total(drain_warrior) == 0, "Setup: the 5-point shield should be fully consumed by the hit"
        expected_request = int(drain_dealt * float(ABILITIES["drain_life"]["heal_from_damage"]))
        assert len(calls) == 1, "Drain Life should heal through exactly one apply_player_healing call"
        assert calls[0][0] is warlock, "heal_from_damage should heal the acting warlock"
        assert calls[0][1] == expected_request, "Requested healing should be int(actual dealt * heal_from_damage), excluding the absorbed portion"
        assert calls[0][2] == expected_request, "Setup: the 80-HP deficit should keep the full drain below the cap"
        assert drain.combat_totals[warlock_sid]["healing"] == expected_request, "Totals should credit the helper's actual gain"
        assert any(f"drains {expected_request} life." in line for line in _turn_lines(drain, 1)), "Drain Life log wording should be unchanged"

        # DoT lifesteal: the helper request derives from the actual post-absorb
        # tick HP damage (12 nominal - 5 absorbed = 7), so it is only knowable
        # after resolve_dot_tick(); negative-HP recovery still works.
        dot = make_match("priest", "warrior", seed=123)
        priest_sid, dot_warrior_sid = dot.players
        priest = dot.state[priest_sid]
        dot_warrior = dot.state[dot_warrior_sid]
        priest.res.hp = -6
        dot_warrior.stats["def"] = 0
        dot_warrior.stats["magic_resist"] = 0
        effects.add_absorb(dot_warrior, 5, source_name="Power Word: Shield", effect_id="power_word_shield")
        effects.apply_effect_by_id(
            dot_warrior,
            "devouring_plague",
            overrides={"duration": 2, "tick_damage": 12, "source_sid": priest_sid, "lifesteal_pct": 1.0},
        )
        dot_warrior_before = dot_warrior.res.hp
        calls.clear()
        submit_turn(dot, _DEF_PASS, _DEF_PASS)
        tick_hp_damage = dot_warrior_before - dot_warrior.res.hp
        assert tick_hp_damage == 7, "Setup: the 12-damage tick should leave exactly 7 HP damage after the 5-point absorb"
        assert len(calls) == 1, "DoT lifesteal should heal through exactly one apply_player_healing call"
        assert calls[0][0] is priest, "lifesteal_pct should heal the DoT source player"
        assert calls[0][1] == int(tick_hp_damage * 1.0), "Requested healing should derive from the actual post-absorb tick HP damage, not the nominal tick"
        assert calls[0][2] == 7 and priest.res.hp == 1, "Lifesteal must recover the actual negative HP value (-6 + 7 = 1)"
        assert dot.phase != "ended", "The recovered source must survive the end-of-turn winner check"
        assert dot.combat_totals[priest_sid]["healing"] == 7, "Totals should credit the helper's actual gain"
        assert any("heals 7 HP from Devouring Plague." in line for line in _turn_lines(dot, 1)), "DoT lifesteal log wording should be unchanged"

        # Mindgames damage-to-healing: each flip (the direct Dragon Roar hit
        # and the same-turn Dragon Roar Bleed tick) calls the helper with the
        # positive nominal converted amount; the full-HP target gains 0 actual
        # HP, yet the nominal mindgames_healing packet still lets the
        # direct-damage DoT apply.
        flip = make_match("warrior", "priest", seed=123)
        flip_warrior_sid, flip_priest_sid = flip.players
        flip.state[flip_warrior_sid].res.rage = flip.state[flip_warrior_sid].res.rage_max
        flip_priest = flip.state[flip_priest_sid]
        assert flip_priest.res.hp == flip_priest.res.hp_max, "Setup: the flip target should start at full HP"
        calls.clear()
        submit_turn(flip, "dragon_roar", "mindgames")
        assert len(calls) == 2, "The direct hit and the same-turn bleed tick should each flip through one apply_player_healing call"
        assert all(target is flip_priest for target, _, _ in calls), "Every flip should heal the damage target"
        assert all(requested > 0 and gained == 0 for _, requested, gained in calls), "Each helper call should receive the positive nominal converted amount while the full-HP target gains 0"
        flip_lines = [line for line in _turn_lines(flip, 1) if "damage into healing" in line]
        logged_amounts = [
            (int(m.group(1)), int(m.group(2)))
            for line in flip_lines
            for m in [re.search(r"Mindgames flips (\d+) damage into healing; \w+ restores (\d+) HP\.", line)]
            if m
        ]
        assert [nominal for nominal, _ in logged_amounts] == [requested for _, requested, _ in calls], "Flip log wording should keep reporting exactly the nominal amounts the helper received"
        assert all(gained == 0 for _, gained in logged_amounts), "Flip log wording must report the actual 0 HP the full-HP target gained"
        assert flip_priest.res.hp == flip_priest.res.hp_max, "No ordinary damage may reach the flip target's HP"
        assert _has_effect(flip_priest, "dragon_roar_bleed"), "The nominal mindgames_healing packet should still let the direct-damage DoT apply"

        # Effect/HoT regeneration (Healing Stream) now routes through the helper.
        hot = make_match("shaman", "warrior", seed=7002)
        shaman_sid, _hot_enemy_sid = hot.players
        shaman = hot.state[shaman_sid]
        shaman.res.hp = 50
        submit_turn(hot, "healing_stream", _DEF_PASS)
        hp_after_cast = shaman.res.hp
        hot_regen = next(int((fx.get("regen") or {}).get("hp") or 0) for fx in shaman.effects if fx.get("id") == "healing_stream")
        calls.clear()
        submit_turn(hot, _DEF_PASS, _DEF_PASS)
        assert shaman.res.hp > hp_after_cast, "Setup: the Healing Stream HoT tick should heal"
        assert calls == [(shaman, hot_regen, hot_regen)], "The HoT tick should route its regen['hp'] request through exactly one apply_player_healing call"
    finally:
        effects.apply_player_healing = original
        resolver.apply_player_healing = original
    return True


def scenario_mindgames_converted_damage_is_not_credited_as_damage_done() -> bool:
    """Damage totals credit only actual post-application HP damage.

    A Mindgames-converted hit credits the attacker zero damage while the
    caster receives actual healing plus conversion overheal; absorbed portions
    are likewise excluded from damage done. This pins DPT correctness, not
    only Mindgames: one converted event must never appear as both damage and
    healing.
    """
    flip_pattern = r"Mindgames flips (\d+) damage into healing; \w+ restores (\d+) HP\."

    # Fully converted direct hit: the mindgamed warlock's Drain Life flips into
    # healing for the priest caster, who is missing exactly 3 HP.
    flip = make_match("warlock", "priest", seed=125)
    warlock_sid, priest_sid = flip.players
    warlock = flip.state[warlock_sid]
    priest = flip.state[priest_sid]
    warlock.stats["acc"] = 999
    priest.stats["eva"] = 0
    priest.res.hp = priest.res.hp_max - 3
    warlock_hp_before = warlock.res.hp
    submit_turn(flip, "drain_life", "mindgames")
    flip_line = next(line for line in _turn_lines(flip, 1) if "damage into healing" in line)
    flip_match_obj = re.search(flip_pattern, flip_line)
    nominal = int(flip_match_obj.group(1))
    logged_actual = int(flip_match_obj.group(2))
    assert nominal > 3, "Setup: the nominal conversion must exceed the 3-HP deficit"
    assert logged_actual == 3, "The conversion log must report the actual 3-HP restoration alongside the nominal amount"
    assert priest.res.hp == priest.res.hp_max, "The flip should heal the caster's 3-HP deficit"
    assert flip.combat_totals[warlock_sid]["damage"] == 0, "A fully converted hit must add zero attacker damage credit"
    assert flip.combat_totals[priest_sid]["healing"] == 3, "The caster is credited only the actual gained healing"
    assert flip.combat_totals[priest_sid]["overhealing"] == nominal - 3, "The capped conversion remainder is the caster's overhealing"
    assert warlock.res.hp == warlock_hp_before, "Zero actual dealt damage must yield zero lifesteal for the attacker"

    # Full-HP conversion: the entire nominal amount becomes caster overhealing,
    # the attacker still gets zero damage credit (direct hit AND the flipped
    # same-turn bleed tick), and the nominal conversion still applies the
    # direct-damage DoT.
    full = make_match("warrior", "priest", seed=123)
    warrior_sid, full_priest_sid = full.players
    full.state[warrior_sid].res.rage = full.state[warrior_sid].res.rage_max
    full_priest = full.state[full_priest_sid]
    assert full_priest.res.hp == full_priest.res.hp_max, "Setup: the caster should start at full HP"
    submit_turn(full, "dragon_roar", "mindgames")
    nominal_total = sum(
        int(m.group(1))
        for line in _turn_lines(full, 1)
        for m in [re.search(flip_pattern, line)]
        if m
    )
    assert nominal_total > 0, "Setup: the seeded Dragon Roar must land and convert"
    assert full.combat_totals[warrior_sid]["damage"] == 0, "Converted direct and bleed-tick damage must credit zero attacker damage"
    assert full.combat_totals[full_priest_sid]["healing"] == 0, "A full-HP caster gains no actual healing"
    assert full.combat_totals[full_priest_sid]["overhealing"] == nominal_total, "The full nominal conversion becomes caster overhealing"
    assert _has_effect(full_priest, "dragon_roar_bleed"), "Nominal conversion must still qualify the same-turn direct DoT"

    # Ordinary control: the same Drain Life without Mindgames credits exactly
    # the actual HP loss it caused.
    control = make_match("warlock", "priest", seed=125)
    control_warlock_sid, control_priest_sid = control.players
    control.state[control_warlock_sid].stats["acc"] = 999
    control.state[control_priest_sid].stats["eva"] = 0
    control_before = control.state[control_priest_sid].res.hp
    submit_turn(control, "drain_life", _DEF_PASS)
    control_loss = control_before - control.state[control_priest_sid].res.hp
    assert control_loss > 0, "Setup: the control Drain Life must land"
    assert control.combat_totals[control_warlock_sid]["damage"] == control_loss, "An ordinary hit credits exactly the actual HP damage dealt"

    # Absorb control: a 7-point shield under the same deterministic hit leaves
    # post-absorb HP damage, and only that lands in the damage total.
    absorbed = make_match("warlock", "priest", seed=125)
    absorbed_warlock_sid, absorbed_priest_sid = absorbed.players
    absorbed.state[absorbed_warlock_sid].stats["acc"] = 999
    absorbed.state[absorbed_priest_sid].stats["eva"] = 0
    effects.add_absorb(absorbed.state[absorbed_priest_sid], 7, source_name="Power Word: Shield", effect_id="power_word_shield")
    absorbed_before = absorbed.state[absorbed_priest_sid].res.hp
    submit_turn(absorbed, "drain_life", _DEF_PASS)
    absorbed_loss = absorbed_before - absorbed.state[absorbed_priest_sid].res.hp
    assert absorbed_loss == control_loss - 7, "Setup: the 7-point shield should absorb exactly 7 of the deterministic hit"
    assert absorbed.combat_totals[absorbed_warlock_sid]["damage"] == absorbed_loss, "Damage credit must be the post-absorb HP damage"
    assert absorbed.combat_totals[absorbed_warlock_sid]["damage"] != control_loss, "Damage credit must not use the pre-absorb incoming amount"
    return True


def scenario_thunderfury_lightning_uses_damage_pipeline() -> bool:
    for seed in range(1, 600):
        match = make_match("warrior", "priest", p1_items={"weapon": "thunderfury"}, seed=seed)
        target = match.state[match.players[1]]
        effects.add_absorb(target, 999, source_name="Power Word: Shield", effect_id="power_word_shield")
        before_hp = target.res.hp
        submit_turn(match, "overpower", _DEF_PASS)
        lightning_line = next((line for line in match.log if "blasts the target with lightning from Thunderfury" in line), None)
        if not lightning_line:
            continue

        assert "absorbed by Power Word: Shield" in lightning_line, "Lightning should route through absorb-aware apply_damage path"
        parsed = re.search(r"Deals (\d+) magic damage\. \((\d+) absorbed by Power Word: Shield\)", lightning_line)
        assert parsed is not None, "Lightning log should include both incoming damage and absorb suffix"
        incoming = int(parsed.group(1))
        absorbed = int(parsed.group(2))
        expected_hp_loss = max(0, incoming - absorbed)
        actual_hp_loss = before_hp - target.res.hp
        assert actual_hp_loss >= expected_hp_loss, "Lightning HP change should reflect post-absorb pipeline result"
        return True
    raise AssertionError("Could not find deterministic Thunderfury lightning proc seed in range")


def scenario_thunderfury_heal_proc_restores_expected_amount() -> bool:
    for seed in range(1, 600):
        match = make_match("warrior", "priest", p1_items={"weapon": "thunderfury"}, seed=seed)
        actor = match.state[match.players[0]]
        actor.res.hp = max(1, actor.res.hp - 40)
        before_hp = actor.res.hp
        submit_turn(match, "overpower", _DEF_PASS)
        heal_line = next((line for line in match.log if "draws strength from Thunderfury" in line), None)
        if not heal_line:
            continue
        heal_match = re.search(r"healing (\d+) HP\.", heal_line)
        assert heal_match is not None, "Thunderfury heal line should include the rolled heal amount"
        healed_for = int(heal_match.group(1))
        assert actor.res.hp - before_hp == healed_for, "Thunderfury heal should restore exactly the passive's rolled amount"
        return True
    raise AssertionError("Could not find deterministic Thunderfury heal proc seed in range")


def scenario_azzinoth_strike_again_deals_secondary_damage() -> bool:
    for seed in range(1, 600):
        baseline = make_match("rogue", "warrior", seed=seed)
        effects.remove_effect(baseline.state[baseline.players[0]], "stealth")
        baseline_target = baseline.state[baseline.players[1]]
        baseline_before = baseline_target.res.hp
        submit_turn(baseline, "basic_attack", _DEF_PASS)
        baseline_loss = baseline_before - baseline_target.res.hp

        azzinoth = make_match("rogue", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=seed)
        effects.remove_effect(azzinoth.state[azzinoth.players[0]], "stealth")
        azzinoth_target = azzinoth.state[azzinoth.players[1]]
        azzinoth_before = azzinoth_target.res.hp
        submit_turn(azzinoth, "basic_attack", _DEF_PASS)
        strike_again_line = next((line for line in azzinoth.log if "strikes again with Twin Blades of Azzinoth" in line), None)
        if not strike_again_line:
            continue
        azzinoth_loss = azzinoth_before - azzinoth_target.res.hp
        assert azzinoth_loss > baseline_loss, "Strike Again should produce extra applied damage beyond the primary swing"
        return True
    raise AssertionError("Could not find deterministic Azzinoth strike-again proc seed in range")


def scenario_fury_of_azzinoth_cannot_miss_and_ignores_armor() -> bool:
    low_acc_match = make_match("rogue", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=3201)
    rogue = low_acc_match.state[low_acc_match.players[0]]
    warrior = low_acc_match.state[low_acc_match.players[1]]
    rogue.stats["acc"] = 0
    warrior.stats["eva"] = 999
    before_hp = warrior.res.hp
    submit_turn(low_acc_match, "fury_of_azzinoth", _DEF_PASS)
    latest_turn = low_acc_match.log[low_acc_match.log.index("Turn 1") + 1:]
    assert not any("Miss!" in line for line in latest_turn if "Fury of Azzinoth" in line), "Fury of Azzinoth should not miss even at 0 accuracy"
    assert warrior.res.hp < before_hp, "Fury of Azzinoth should still deal damage at 0 accuracy"

    def_only = make_match("rogue", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=3202)
    armored = make_match("rogue", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=3202)
    def_only_target = def_only.state[def_only.players[1]]
    armored_target = armored.state[armored.players[1]]
    def_only_target.stats["def"] += 999
    armored_target.stats["def"] += 999
    armored_target.stats["physical_reduction"] += 999
    submit_turn(def_only, "fury_of_azzinoth", _DEF_PASS)
    submit_turn(armored, "fury_of_azzinoth", _DEF_PASS)
    baseline_damage = def_only_target.res.hp_max - def_only_target.res.hp
    armored_damage = armored_target.res.hp_max - armored_target.res.hp
    assert armored_damage == baseline_damage, "Fury of Azzinoth should ignore Armor but still respect DEF"
    return True


def scenario_mitigation_physical_uses_def_plus_armor() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4001).state["p2_sid"]

    target.stats["def"] = 20
    target.stats["physical_reduction"] = 0
    def_only = effects.mitigate_damage(raw, target, "physical")
    assert def_only == _expected_mitigated(raw, 20), "physical mitigation should include DEF"

    target.stats["def"] = 0
    target.stats["physical_reduction"] = 20
    armor_only = effects.mitigate_damage(raw, target, "physical")
    assert armor_only == _expected_mitigated(raw, 20), "physical mitigation should include Armor"

    target.stats["def"] = 20
    target.stats["physical_reduction"] = 20
    def_plus_armor = effects.mitigate_damage(raw, target, "physical")
    assert def_plus_armor == _expected_mitigated(raw, 40), "physical mitigation should use DEF + Armor"
    assert def_plus_armor < def_only, "combined DEF + Armor should mitigate more than either stat alone"
    return True


def scenario_mitigation_magic_uses_def_plus_magic_resist() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4002).state["p2_sid"]

    target.stats["def"] = 20
    target.stats["magic_resist"] = 0
    def_only = effects.mitigate_damage(raw, target, "magic")
    assert def_only == _expected_mitigated(raw, 20), "magic mitigation should include DEF"

    target.stats["def"] = 0
    target.stats["magic_resist"] = 20
    mr_only = effects.mitigate_damage(raw, target, "magic")
    assert mr_only == _expected_mitigated(raw, 20), "magic mitigation should include Magic Resist"

    target.stats["def"] = 20
    target.stats["magic_resist"] = 20
    def_plus_mr = effects.mitigate_damage(raw, target, "magic")
    assert def_plus_mr == _expected_mitigated(raw, 40), "magic mitigation should use DEF + Magic Resist"
    assert def_plus_mr < def_only, "combined DEF + Magic Resist should mitigate more than either stat alone"
    return True


def scenario_ignore_armor_bypasses_only_armor_component() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4003).state["p2_sid"]
    target.stats["def"] = 20
    target.stats["physical_reduction"] = 20

    normal = effects.mitigate_damage(raw, target, "physical")
    ignored = effects.mitigate_damage(raw, target, "physical", ignore_armor=True)
    assert normal == _expected_mitigated(raw, 40), "normal physical mitigation should use DEF + Armor"
    assert ignored == _expected_mitigated(raw, 20), "ignore_armor should remove only Armor from mitigation"
    assert ignored > normal, "ignore_armor should increase damage taken compared to normal mitigation"
    return True


def scenario_pet_attacks_use_shared_mitigation_stats() -> bool:
    raw = 100
    target = make_match("warrior", "warrior", seed=4004).state["p2_sid"]
    target.stats["def"] = 20
    target.stats["physical_reduction"] = 15
    target.stats["magic_resist"] = 10

    physical = PET_AI._damage_after_reduction(raw, target, "physical")
    magical = PET_AI._damage_after_reduction(raw, target, "magic")
    assert physical == _expected_mitigated(raw, 35), "physical pet attacks should use DEF + Armor"
    assert magical == _expected_mitigated(raw, 30), "magical pet attacks should use DEF + Magic Resist"
    return True


def scenario_break_on_damage_and_lifesteal_use_post_mitigation_damage() -> bool:
    cc_match = make_match("rogue", "mage", seed=4005)
    rogue_sid, mage_sid = cc_match.players
    mage = cc_match.state[mage_sid]
    effects.apply_effect_by_id(mage, "ring_of_ice_freeze", overrides={"duration": 2})
    mage.stats["def"] = 9999
    mage.stats["physical_reduction"] = 9999
    submit_turn(cc_match, "sinister_strike", _DEF_PASS)
    assert _has_effect(mage, "ring_of_ice_freeze"), "break-on-damage CC should persist when post-mitigation damage is 0"

    life_match = make_match("priest", "warrior", seed=4006)
    priest_sid, warrior_sid = life_match.players
    priest = life_match.state[priest_sid]
    warrior = life_match.state[warrior_sid]
    priest.res.hp = max(1, priest.res.hp - 30)
    warrior.stats["def"] = 35
    warrior.stats["magic_resist"] = 25
    effects.apply_effect_by_id(
        warrior,
        "devouring_plague",
        overrides={"duration": 2, "tick_damage": 20, "source_sid": priest_sid},
    )
    hp_before = priest.res.hp
    enemy_before = warrior.res.hp
    submit_turn(life_match, _DEF_PASS, _DEF_PASS)
    healed = priest.res.hp - hp_before
    dealt = max(0, enemy_before - warrior.res.hp)
    assert dealt > 0, "sanity check requires mitigated damage to still be positive"
    assert healed == dealt, "lifesteal/heal-from-damage should use actual post-mitigation damage dealt"
    return True


def scenario_phase_c_prompt2_no_spillover_to_effect_application_or_end_of_turn() -> bool:
    match = make_match("hunter", "warrior", seed=4007)
    hunter_sid, warrior_sid = match.players
    warrior = match.state[warrior_sid]

    submit_turn(match, "wildfire_bomb", _DEF_PASS)
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None, "Wildfire Bomb should still apply Wildfire Burn during effect application"

    summary = effects.end_of_turn(warrior, [], warrior_sid[:5])
    assert any(
        source.get("effect_id") == "wildfire_burn"
        for source in summary.get("damage_sources", [])
    ), "end_of_turn should still emit wildfire_burn as a damage source"
    return True


def scenario_phase_c_prompt3_effect_application_stage_preserved() -> bool:
    # buff_application + hot_application
    druid_match = make_match("druid", "warrior", seed=4010)
    druid_sid, warrior_sid = druid_match.players
    druid = druid_match.state[druid_sid]
    submit_turn(druid_match, "tree", _DEF_PASS)
    submit_turn(druid_match, "regrowth", _DEF_PASS)
    assert _has_effect(druid, "regrowth"), "Self HoT/buff application should remain unchanged"

    # debuff_application + dot_application + effect_refresh
    refresh_ability_id = "test_effect_application_refresh_dot"
    refresh_effect_id = "test_effect_application_refresh_dot_effect"
    ABILITIES[refresh_ability_id] = {
        "name": "Refresh DoT Test",
        "requires_target": True,
        "cannot_miss": True,
        "flat_damage": 7,
        "damage_type": "magic",
        "school": "magical",
        "subschool": "shadow",
        "dot": {"id": refresh_effect_id, "duration": 2, "from_dealt_damage": True},
        "tags": ["attack", "spell"],
    }
    EFFECT_TEMPLATES[refresh_effect_id] = {
        "type": "dot",
        "name": "Refresh DoT Test Effect",
        "duration": 2,
        "category": "dot",
        "school": "magical",
        "subschool": "shadow",
        "tick_damage": 1,
    }
    try:
        warlock_match = make_match("warlock", "warrior", seed=4011)
        submit_turn(warlock_match, refresh_ability_id, _DEF_PASS)
        first_target = warlock_match.state[warlock_match.players[1]]
        first_dot = next((fx for fx in first_target.effects if fx.get("id") == refresh_effect_id), None)
        assert first_dot is not None, "Enemy DoT/debuff application should remain unchanged"
        submit_turn(warlock_match, refresh_ability_id, _DEF_PASS)
        refreshed_dot = next((fx for fx in first_target.effects if fx.get("id") == refresh_effect_id), None)
        assert refreshed_dot is not None, "DoT should still exist after refresh"
        assert any("refreshes Test Effect Application Refresh Dot Effect" in line for line in _turn_lines(warlock_match, 2)), "DoT refresh timing/log should remain unchanged"
    finally:
        ABILITIES.pop(refresh_ability_id, None)
        EFFECT_TEMPLATES.pop(refresh_effect_id, None)

    # proc_grant + proc_consume
    hunter_proc_match = make_match("hunter", "warrior", seed=4012)
    hunter_sid, _ = hunter_proc_match.players
    hunter = hunter_proc_match.state[hunter_sid]
    submit_turn(hunter_proc_match, "wildfire_bomb", _DEF_PASS)
    assert _has_effect(hunter, "arcane_surge"), "Proc grant should remain unchanged"
    submit_turn(hunter_proc_match, "arcane_shot", _DEF_PASS)
    assert not _has_effect(hunter, "arcane_surge"), "Proc consume timing should remain unchanged"

    # summon_application + pet_command_application
    hunter_pet_match = make_match("hunter", "warrior", seed=4013)
    submit_turn(hunter_pet_match, "call_boar", _DEF_PASS)
    assert _active_pet(hunter_pet_match.state[hunter_pet_match.players[0]], "barrens_boar") is not None, "Summon application should remain unchanged"
    assert any("calls for Barrens Boar." in line for line in _turn_lines(hunter_pet_match, 1)), "Summon logging should remain unchanged"

    # dispel_application + effect_removal (including stealth reveal path)
    dispel_match = make_match("priest", "hunter", seed=4014)
    submit_turn(dispel_match, "mass_dispel", "wildfire_bomb")
    assert not _has_effect(dispel_match.state[dispel_match.players[0]], "wildfire_burn"), "Mass Dispel removal behavior should remain unchanged"

    reveal_match = make_match("hunter", "rogue", seed=4015)
    submit_turn(reveal_match, "flare", _DEF_PASS)
    assert not _has_effect(reveal_match.state[reveal_match.players[1]], "stealth"), "Stealth removal/reveal behavior should remain unchanged"
    reveal_turn = _turn_lines(reveal_match, 1)
    assert reveal_turn[0] == "p1_si uses their bare hands to cast Flare. Flare reveals the target.", "Immediate-path effect log should remain unchanged"
    assert reveal_turn[1] == "p2_si's stealth broken by Flare.", "Immediate-path reveal break log should remain unchanged"

    summon_turn = _turn_lines(hunter_pet_match, 1)
    assert summon_turn[0] == "p1_si uses their bare hands to cast Call Barrens Boar. calls for Barrens Boar.", "Immediate summon action log should remain unchanged"

    # no spillover to damage/post-damage and end_of_turn
    damage_match = make_match("warrior", "warrior", seed=4016)
    p1_sid, p2_sid = damage_match.players
    enemy_before = damage_match.state[p2_sid].res.hp
    submit_turn(damage_match, "overpower", _DEF_PASS)
    assert damage_match.state[p2_sid].res.hp < enemy_before, "Damage/post-damage stages should remain unchanged"

    eot_summary = effects.end_of_turn(first_target, [], warrior_sid[:5])
    assert any(src.get("effect_id") == refresh_effect_id for src in eot_summary.get("damage_sources", [])), "end_of_turn behavior should remain unchanged"
    return True


def scenario_phase_d_end_of_turn_stage_preserved() -> bool:
    # dot_tick (including Agony ramp behavior)
    assert scenario_agony_ramp_progression_restored(), "Agony end-of-turn behavior should remain unchanged"
    # hot_tick + resource_tick logging behavior
    assert scenario_recover_log_shows_only_nonzero_resources_and_uses_mana_wording(), "Resource recovery logs should remain unchanged"
    assert scenario_phase_c_prompt3_effect_application_stage_preserved(), "Effect application behavior should not spill into end-of-turn"
    # pet_phase + pet_cleanup + redirect timing behavior
    assert scenario_hunter_boar_redirect_same_turn_brace(), "Boar redirect timing should remain unchanged"
    assert scenario_pet_specials_are_blocked_while_pet_is_ccd(), "CC-disabled pet behavior should remain unchanged"
    # end-of-turn damage / stealth / SoV ordering behavior
    assert scenario_shield_of_vengeance_explosion_flushes_stealth_break_log(), "Shield of Vengeance / stealth-break timing should remain unchanged"
    # winner_check timing behavior
    assert scenario_winner_summary_logs_after_pet_phase_and_end_of_turn_resolution(), "Winner summary timing should remain unchanged"
    # no spillover guard
    assert scenario_phase_c_prompt2_no_spillover_to_effect_application_or_end_of_turn(), "Earlier migrated stages and end_of_turn contracts should remain unchanged"
    return True


def scenario_subschool_metadata_and_templates() -> bool:
    direct_expectations = {
        "fireball": "fire",
        "arcane_shot": "arcane",
        "wrath": "nature",
        "mind_blast": "shadow",
        "judgment": "holy",
    }
    for ability_id, subschool in direct_expectations.items():
        ability = ABILITIES[ability_id]
        assert ability.get("school") == "magical", f"{ability_id} should be magical"
        assert ability.get("subschool") == subschool, f"{ability_id} should be tagged as {subschool}"

    effect_expectations = {
        "ring_of_ice_freeze": "frost",
        "iceblock": "frost",
        "divine_shield": "holy",
        "power_word_shield": "holy",
        "feared": "shadow",
    }
    for effect_id, subschool in effect_expectations.items():
        effect = EFFECT_TEMPLATES[effect_id]
        assert effect.get("school") == "magical", f"{effect_id} should be magical"
        assert effect.get("subschool") == subschool, f"{effect_id} should be tagged as {subschool}"

    no_subschool_magical = ("turtle", "innervate", "dark_pact", "healthstone", "unending_resolve")
    for ability_id in no_subschool_magical:
        ability = ABILITIES[ability_id]
        assert ability.get("school") == "magical", f"{ability_id} should stay magical"
        assert "subschool" not in ability, f"{ability_id} should intentionally remain without subschool"

    assert PETS["imp"].get("subschool") == "fire", "Imp should be tagged as fire"
    assert PETS["emerald_serpent"].get("subschool") == "nature", "Emerald Serpent should be tagged as nature"
    return True


def scenario_subschool_event_plumbing_for_dots_and_passives() -> bool:
    match = make_match("hunter", "warrior", p1_items={"weapon": "thunderfury"}, seed=5001)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]

    # DoT runtime copies should preserve school + subschool metadata from data templates.
    submit_turn(match, "wildfire_bomb", _DEF_PASS)
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None, "Wildfire Burn should be applied"
    assert burn.get("school") == "magical", "Wildfire Burn should stay magical"
    assert burn.get("subschool") == "fire", "Wildfire Burn should preserve fire subschool"

    # Triggered passive damage events should carry subschool metadata through the shared payload.
    lightning_effect = {
        "type": "item_passive",
        "source_item": "Thunderfury",
        "passive": {
            "type": "lightning_blast",
            "trigger": "on_hit",
            "chance": 1.0,
            "scaling": {"atk": 0.5},
            "dice": "d3",
            "school": "magical",
            "subschool": "nature",
        },
    }
    hunter.effects.append(lightning_effect)
    _, _, _, _, damage_events = effects.trigger_on_hit_passives(
        hunter,
        warrior,
        base_damage=10,
        damage_type="physical",
        rng=random.Random(777),
        ability=ABILITIES["overpower"],
        include_strike_again=False,
    )
    assert any(
        evt.get("school") == "magical" and evt.get("subschool") == "nature"
        for evt in damage_events
    ), "Thunderfury lightning blast event should carry magical/nature"
    return True


def scenario_direct_damage_dot_inherits_ability_subschool() -> bool:
    ability_id = "test_direct_dot_subschool_fallback"
    effect_id = "test_direct_dot_no_subschool"
    ABILITIES[ability_id] = {
        "name": "Fallback Arcane Lash",
        "requires_target": True,
        "cannot_miss": True,
        "flat_damage": 8,
        "damage_type": "magic",
        "school": "magical",
        "subschool": "arcane",
        "dot": {"id": effect_id, "duration": 2, "from_dealt_damage": True},
        "tags": ["attack", "spell"],
    }
    EFFECT_TEMPLATES[effect_id] = {
        "type": "dot",
        "name": "Fallback Arcane Lash DoT",
        "duration": 2,
        "category": "dot",
        "school": "magical",
        "tick_damage": 1,
    }

    try:
        match = make_match("mage", "warrior", seed=5002)
        mage_sid, warrior_sid = match.players
        warrior = match.state[warrior_sid]

        submit_turn(match, ability_id, _DEF_PASS)
        dot = next((fx for fx in warrior.effects if fx.get("id") == effect_id), None)
        assert dot is not None, "Direct-damage ability should apply the configured DoT"
        assert dot.get("school") == "magical", "DoT school should remain magical"
        assert dot.get("subschool") == "arcane", "DoT should inherit ability-level arcane subschool fallback"

        summary = effects.end_of_turn(warrior, [], "Warrior")
        sources = summary.get("damage_sources", [])
        assert any(
            src.get("effect_id") == effect_id
            and src.get("school") == "magical"
            and src.get("subschool") == "arcane"
            for src in sources
        ), "DoT ticks should preserve inherited magical subschool metadata"
        return True
    finally:
        ABILITIES.pop(ability_id, None)
        EFFECT_TEMPLATES.pop(effect_id, None)


def scenario_true_aoe_school_subschool_propagation() -> bool:
    magical_aoe_id = "test_arcane_storm_aoe"
    physical_aoe_id = "test_slam_wave_aoe"
    magical_effect_id = "test_arcane_storm_burn"
    ABILITIES[magical_aoe_id] = {
        "name": "Arcane Storm",
        "requires_target": True,
        "target_mode": "aoe_enemy",
        "cannot_miss": True,
        "flat_damage": 10,
        "damage_type": "magic",
        "school": "magical",
        "subschool": "arcane",
        "dot": {"id": magical_effect_id, "duration": 2, "from_dealt_damage": True},
        "tags": ["attack", "spell", "aoe"],
    }
    ABILITIES[physical_aoe_id] = {
        "name": "Slam Wave",
        "requires_target": True,
        "target_mode": "aoe_enemy",
        "cannot_miss": True,
        "flat_damage": 10,
        "damage_type": "physical",
        "dot": {"id": "dragon_roar_bleed", "duration": 2, "from_dealt_damage": True},
        "tags": ["attack", "physical", "aoe"],
    }
    EFFECT_TEMPLATES[magical_effect_id] = {
        "type": "dot",
        "name": "Arcane Storm Burn",
        "duration": 2,
        "category": "dot",
        "school": "magical",
        "tick_damage": 1,
    }

    try:
        match = make_match("mage", "warlock", seed=5003)
        mage_sid, warlock_sid = match.players
        warlock = match.state[warlock_sid]
        warlock.pets["p2_imp_2"] = PetState(id="p2_imp_2", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)
        warlock.pets["p2_imp_1"] = PetState(id="p2_imp_1", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)

        champion_hp_before = warlock.res.hp
        submit_turn(match, magical_aoe_id, _DEF_PASS)
        turn_one_lines = _turn_lines(match, 1)
        assert warlock.res.hp < champion_hp_before, "AoE should damage champion target"
        action_log_idx = next((i for i, line in enumerate(turn_one_lines) if "cast Arcane Storm" in line), -1)
        pet1_log_idx = next((i for i, line in enumerate(turn_one_lines) if "hits p2_si's Imp (imp1)" in line), -1)
        pet2_log_idx = next((i for i, line in enumerate(turn_one_lines) if "hits p2_si's Imp (imp2)" in line), -1)
        assert action_log_idx >= 0, "AoE action log should be present"
        assert pet1_log_idx >= 0 and pet2_log_idx >= 0, "AoE should fan out to both pets"
        assert action_log_idx < pet1_log_idx < pet2_log_idx, "AoE pet fanout should remain deterministic"

        champion_dot = next((fx for fx in warlock.effects if fx.get("id") == magical_effect_id), None)
        assert champion_dot is not None, "Champion should receive Arcane Storm DoT"
        assert champion_dot.get("school") == "magical"
        assert champion_dot.get("subschool") == "arcane"
        for pet_id in ("p2_imp_1", "p2_imp_2"):
            pet_dot = next((fx for fx in warlock.pets[pet_id].effects if fx.get("id") == magical_effect_id), None)
            assert pet_dot is not None, f"{pet_id} should receive Arcane Storm DoT"
            assert pet_dot.get("school") == "magical"
            assert pet_dot.get("subschool") == "arcane"

        submit_turn(match, physical_aoe_id, _DEF_PASS)
        champion_bleed = next((fx for fx in warlock.effects if fx.get("id") == "dragon_roar_bleed"), None)
        assert champion_bleed is not None, "Physical AoE should apply dragon_roar_bleed to champion"
        assert champion_bleed.get("school") == "physical"
        assert champion_bleed.get("subschool") is None
        for pet_id in ("p2_imp_1", "p2_imp_2"):
            pet_bleed = next((fx for fx in warlock.pets[pet_id].effects if fx.get("id") == "dragon_roar_bleed"), None)
            assert pet_bleed is not None, f"{pet_id} should receive dragon_roar_bleed"
            assert pet_bleed.get("school") == "physical"
            assert pet_bleed.get("subschool") is None
        return True
    finally:
        ABILITIES.pop(magical_aoe_id, None)
        ABILITIES.pop(physical_aoe_id, None)
        EFFECT_TEMPLATES.pop(magical_effect_id, None)


def scenario_high_risk_shields_absorbs_regression_pack() -> bool:
    layered_match = make_match("mage", "priest", seed=8101)
    target = layered_match.state[layered_match.players[1]]
    effects.apply_effect_by_id(target, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(target, 9, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(target, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(target, 6, source_name="Ice Barrier", effect_id="ice_barrier")

    panel_before = effects.build_effect_panel_payload(target)
    assert any(entry.get("name") == "Ice Barrier" for entry in panel_before["buffs_magical"]), "Ice Barrier should be visible in panel before consume"
    assert any(entry.get("name") == "Power Word: Shield" for entry in panel_before["buffs_magical"]), "Power Word: Shield should be visible in panel before consume"

    spill_1, absorbed_1, _ = effects.consume_absorbs(target, 4)
    assert spill_1 == 0 and absorbed_1 == 4, "Partial absorb should consume incoming damage"
    assert int(target.res.absorbs["ice_barrier"]["remaining"]) == 2, "Latest-cast absorb should be consumed first"
    panel_partial = effects.build_effect_panel_payload(target)
    assert any(entry.get("name") == "Ice Barrier" for entry in panel_partial["buffs_magical"]), "Partially consumed shield should remain visible in panel"

    spill_2, absorbed_2, _ = effects.consume_absorbs(target, 4)
    assert spill_2 == 0 and absorbed_2 == 4, "Second consume should continue through stacked absorbs"
    assert "ice_barrier" not in target.res.absorbs, "Fully consumed shield layer should be removed"
    assert int(target.res.absorbs["power_word_shield"]["remaining"]) == 7, "Overflow absorb should continue into the next shield layer"
    panel_after = effects.build_effect_panel_payload(target)
    assert not any(entry.get("name") == "Ice Barrier" for entry in panel_after["buffs_magical"]), "Fully consumed shield should be removed from panel"
    assert any(entry.get("name") == "Power Word: Shield" for entry in panel_after["buffs_magical"]), "Earlier stacked shield should remain visible while it still has absorb"

    multihit_match = make_match("mage", "priest", seed=8102)
    mage_sid, priest_sid = multihit_match.players
    mage = multihit_match.state[mage_sid]
    priest = multihit_match.state[priest_sid]
    mage.stats["acc"] = 999
    effects.apply_effect_by_id(priest, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(priest, 2, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(priest, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(priest, 2, source_name="Ice Barrier", effect_id="ice_barrier")
    submit_turn(multihit_match, "arcane_barrage", _DEF_PASS)
    turn_lines = _turn_lines(multihit_match, 1)
    assert any("Hit 1:" in line and "Hit 2:" in line and "Hit 3:" in line for line in turn_lines), "Arcane Barrage should remain a multi-hit damage path"
    assert "ice_barrier" not in priest.res.absorbs and "power_word_shield" not in priest.res.absorbs, "Multi-hit damage should deplete small stacked absorbs across hits"

    sov_match = make_match("paladin", "warrior", seed=8103)
    pal_sid, war_sid = sov_match.players
    submit_turn(sov_match, "shield_of_vengeance", _DEF_PASS)
    paladin = sov_match.state[pal_sid]
    enemy = sov_match.state[war_sid]
    sov_absorb = int(paladin.res.absorbs.get("shield_of_vengeance", {}).get("remaining", 0) or 0)
    assert sov_absorb > 0, "Shield of Vengeance absorb layer should exist after cast"
    effects.consume_absorbs(paladin, sov_absorb)
    assert "shield_of_vengeance" not in paladin.res.absorbs, "Shield of Vengeance absorb layer should clear when fully consumed"
    assert not any(entry.get("name") == "Shield of Vengeance" for entry in effects.build_effect_panel_payload(paladin)["buffs_magical"]), "Shield of Vengeance should be removed from panel after full consume"
    enemy_hp_before = enemy.res.hp
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    explosion_seen = any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_match, 2))
    if not explosion_seen:
        submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
        explosion_seen = any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_match, 3))
    assert explosion_seen, "Shield of Vengeance explosion should still trigger after full absorb consumption"
    assert enemy.res.hp < enemy_hp_before, "Shield of Vengeance explosion should still deal damage after shield fully absorbs"
    return True


def scenario_step2_absorb_shield_contracts() -> bool:
    layered = make_match("mage", "priest", seed=8611)
    target = layered.state[layered.players[1]]
    effects.apply_effect_by_id(target, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(target, 10, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(target, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(target, 6, source_name="Ice Barrier", effect_id="ice_barrier")

    spill, absorbed, _ = effects.consume_absorbs(target, 4)
    assert spill == 0 and absorbed == 4, "Partial absorb should fully catch incoming damage"
    assert int(target.res.absorbs["ice_barrier"]["remaining"]) == 2, "Latest-cast absorb should be consumed first"
    assert any(entry.get("name") == "Ice Barrier" for entry in effects.build_effect_panel_payload(target)["buffs_magical"]), "Partially consumed shield should remain on the buff panel"

    spill, absorbed, _ = effects.consume_absorbs(target, 4)
    assert spill == 0 and absorbed == 4, "Second absorb consume should continue into the next layer"
    assert "ice_barrier" not in target.res.absorbs, "Fully consumed latest shield should be removed"
    assert int(target.res.absorbs["power_word_shield"]["remaining"]) == 8, "Overflow should continue through stacked shields"
    assert not any(entry.get("name") == "Ice Barrier" for entry in effects.build_effect_panel_payload(target)["buffs_magical"]), "Fully consumed shield should be removed from the buff panel"

    multihit = make_match("mage", "priest", seed=8612)
    mage, priest = _player_states(multihit)
    mage.stats["acc"] = 999
    effects.apply_effect_by_id(priest, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(priest, 3, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(priest, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(priest, 3, source_name="Ice Barrier", effect_id="ice_barrier")
    submit_turn(multihit, "arcane_barrage", _DEF_PASS)
    turn_lines = _turn_lines(multihit, 1)
    assert any("Hit 1:" in line and "Hit 2:" in line and "Hit 3:" in line for line in turn_lines), "Arcane Barrage should remain the multi-hit path for absorb coverage"
    assert "ice_barrier" not in priest.res.absorbs and "power_word_shield" not in priest.res.absorbs, "Multi-hit damage should consume stacked absorbs across hits"

    sov_match = make_match("paladin", "warrior", seed=8613)
    pal_sid, war_sid = sov_match.players
    submit_turn(sov_match, "shield_of_vengeance", _DEF_PASS)
    paladin = sov_match.state[pal_sid]
    enemy = sov_match.state[war_sid]
    sov_absorb = int(paladin.res.absorbs.get("shield_of_vengeance", {}).get("remaining", 0) or 0)
    effects.consume_absorbs(paladin, sov_absorb)
    assert "shield_of_vengeance" not in paladin.res.absorbs, "Fully consumed SoV absorb layer should be removed"
    assert not any(entry.get("name") == "Shield of Vengeance" for entry in effects.build_effect_panel_payload(paladin)["buffs_magical"]), "Fully consumed SoV should be removed from the buff panel"
    enemy_hp_before = enemy.res.hp
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    assert any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_match, 2)), "SoV explosion should still occur after full absorb consumption"
    assert enemy.res.hp < enemy_hp_before, "SoV explosion should still deal damage after shield fully absorbs"
    return True


def scenario_high_risk_end_of_turn_lethal_ordering_pack() -> bool:
    dot_pet_match = make_match("hunter", "warlock", seed=8201)
    hunter_sid, warlock_sid = dot_pet_match.players
    hunter = dot_pet_match.state[hunter_sid]
    warlock = dot_pet_match.state[warlock_sid]
    submit_turn(dot_pet_match, "call_serpent", "agony")
    hunter.res.hp = 1
    warlock.res.hp = warlock.res.hp_max
    submit_turn(dot_pet_match, _DEF_PASS, _DEF_PASS)
    turn_lines = _turn_lines(dot_pet_match, 2)
    pet_idx = next(i for i, line in enumerate(turn_lines) if "Emerald Serpent" in line and ("melees the target" in line or "breathes lightning" in line))
    dot_idx = next(i for i, line in enumerate(turn_lines) if "suffers" in line and "Agony" in line)
    assert pet_idx < dot_idx, "Pet phase should run before DoT ticks in end-of-turn ordering"

    sov_match = make_match("paladin", "warrior", seed=8202)
    pal_sid, war_sid = sov_match.players
    submit_turn(sov_match, "shield_of_vengeance", _DEF_PASS)
    sov_fx = next(fx for fx in sov_match.state[pal_sid].effects if fx.get("id") == "shield_of_vengeance")
    sov_fx["absorbed"] = 20
    sov_match.state[war_sid].res.hp = 20
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    sov_turn = _turn_lines(sov_match, 3)
    explosion_idx = next(i for i, line in enumerate(sov_turn) if "Shield of Vengeance explodes!" in line)
    summary_idx = next((i for i, line in enumerate(sov_turn) if line.startswith("Post-Combat Summary|")), -1)
    if summary_idx != -1:
        winner_idx = next(i for i, line in enumerate(sov_turn) if "wins the duel." in line)
        assert explosion_idx < summary_idx < winner_idx, "Shield of Vengeance explosion lethal should resolve before summary/winner logs"

    double_ko_match = make_match("priest", "warlock", seed=8203)
    p1_sid, p2_sid = double_ko_match.players
    p1 = double_ko_match.state[p1_sid]
    p2 = double_ko_match.state[p2_sid]
    p1.res.hp = 1
    p2.res.hp = 1
    effects.apply_effect_by_id(p1, "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p2_sid, "dot_mode": "fixed"})
    effects.apply_effect_by_id(p2, "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p1_sid, "dot_mode": "fixed"})
    submit_turn(double_ko_match, _DEF_PASS, _DEF_PASS)
    assert double_ko_match.phase == "ended" and double_ko_match.winner is None, "Simultaneous end-of-turn lethals should remain a true double KO"
    assert "Double KO. No winner." in _turn_lines(double_ko_match, 1), "Double KO message should appear only after end-of-turn systems complete"
    return True


def scenario_step2_end_of_turn_lethal_ordering_contracts() -> bool:
    pet_kill = make_match("hunter", "warlock", seed=8621)
    hunter_sid, warlock_sid = pet_kill.players
    submit_turn(pet_kill, "call_serpent", _DEF_PASS)
    pet_kill.state[warlock_sid].res.hp = 4
    submit_turn(pet_kill, _DEF_PASS, _DEF_PASS)
    pet_turn = _turn_lines(pet_kill, 2)
    pet_idx = next(i for i, line in enumerate(pet_turn) if "Emerald Serpent" in line and ("melees the target" in line or "breathes lightning" in line))
    summary_idx = next(i for i, line in enumerate(pet_turn) if line.startswith("Post-Combat Summary|"))
    winner_idx = next(i for i, line in enumerate(pet_turn) if "wins the duel." in line)
    assert pet_idx < summary_idx < winner_idx, "Pet lethal should resolve before summary and winner logs"

    dot_kill = make_match("priest", "warlock", seed=8622)
    priest_sid, warlock_sid = dot_kill.players
    warlock = dot_kill.state[warlock_sid]
    warlock.res.hp = 1
    effects.apply_effect_by_id(warlock, "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": priest_sid, "dot_mode": "fixed"})
    submit_turn(dot_kill, _DEF_PASS, _DEF_PASS)
    dot_turn = _turn_lines(dot_kill, 1)
    dot_idx = next(i for i, line in enumerate(dot_turn) if "suffers" in line and "Agony" in line)
    summary_idx = next(i for i, line in enumerate(dot_turn) if line.startswith("Post-Combat Summary|"))
    winner_idx = next(i for i, line in enumerate(dot_turn) if "wins the duel." in line)
    assert dot_idx < summary_idx < winner_idx, "DoT lethal should resolve before summary and winner logs"

    sov_kill = make_match("paladin", "warrior", seed=8623)
    pal_sid, war_sid = sov_kill.players
    submit_turn(sov_kill, "shield_of_vengeance", _DEF_PASS)
    sov_fx = next(fx for fx in sov_kill.state[pal_sid].effects if fx.get("id") == "shield_of_vengeance")
    sov_fx["absorbed"] = 8
    sov_kill.state[war_sid].res.hp = 8
    submit_turn(sov_kill, _DEF_PASS, _DEF_PASS)
    submit_turn(sov_kill, _DEF_PASS, _DEF_PASS)
    sov_turn = _turn_lines(sov_kill, 3)
    explosion_idx = next(i for i, line in enumerate(sov_turn) if "Shield of Vengeance explodes!" in line)
    summary_idx = next(i for i, line in enumerate(sov_turn) if line.startswith("Post-Combat Summary|"))
    winner_idx = next(i for i, line in enumerate(sov_turn) if "wins the duel." in line)
    assert explosion_idx < summary_idx < winner_idx, "SoV lethal should resolve before summary and winner logs"

    double_ko = make_match("priest", "warlock", seed=8624)
    p1_sid, p2_sid = double_ko.players
    double_ko.state[p1_sid].res.hp = 1
    double_ko.state[p2_sid].res.hp = 1
    effects.apply_effect_by_id(double_ko.state[p1_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p2_sid, "dot_mode": "fixed"})
    effects.apply_effect_by_id(double_ko.state[p2_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p1_sid, "dot_mode": "fixed"})
    submit_turn(double_ko, _DEF_PASS, _DEF_PASS)
    final_turn = _turn_lines(double_ko, 1)
    dot_events = [i for i, line in enumerate(final_turn) if "suffers" in line and "Agony" in line]
    ko_idx = next(i for i, line in enumerate(final_turn) if line == "Double KO. No winner.")
    assert len(dot_events) == 2 and max(dot_events) < ko_idx, "Double KO should be determined after all end-of-turn lethal sources resolve"
    assert double_ko.phase == "ended" and double_ko.winner is None, "Double KO result should remain deterministic"
    return True


def scenario_phase0_early_pipeline_contract_lock() -> bool:
    stealth_match = make_match("rogue", "paladin", seed=9001)
    submit_turn(stealth_match, "vanish", "hammer_of_justice")
    turn_one = _turn_lines(stealth_match, 1)
    rogue = stealth_match.state[stealth_match.players[0]]
    assert _has_effect(rogue, "stealth"), "Stealth-at-start snapshot should preserve Vanish when same-turn stun is attempted"
    assert not _has_effect(rogue, "stunned"), "Same-turn stun should still miss when target is stealthed at turn start"
    assert any("Target is stealthed — Miss!" in line for line in turn_one), "Stealth miss log should remain visible on the action line"

    immediate_denial = make_match("warlock", "warrior", seed=9002)
    effects.apply_effect_by_id(immediate_denial.state[immediate_denial.players[0]], "feared", overrides={"duration": 1})
    submit_turn(immediate_denial, "teleport", _DEF_PASS)
    immediate_turn = _turn_lines(immediate_denial, 1)
    assert any("tries to use Demonic Circle: Teleport but is feared and cannot act." in line for line in immediate_turn), "Immediate-path denial should win over selection checks"
    assert not any("Demonic Circle is required." in line for line in immediate_turn), "Immediate-path selection-failure log should not replace denial"

    normal_denial = make_match("druid", "warrior", seed=9003)
    effects.apply_effect_by_id(normal_denial.state[normal_denial.players[0]], "feared", overrides={"duration": 1})
    submit_turn(normal_denial, "maul", _DEF_PASS)
    normal_turn = _turn_lines(normal_denial, 1)
    assert any("tries to use Maul but is feared and cannot act." in line for line in normal_turn), "Normal-path denial should win over form/selection errors"
    assert not any("wasn't in Bear Form" in line for line in normal_turn), "Normal-path selection-failure log should not replace denial"
    return True


def scenario_phase0_absorb_shield_contract_lock() -> bool:
    layered = make_match("mage", "priest", seed=9021)
    target = layered.state[layered.players[1]]
    effects.apply_effect_by_id(target, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(target, 9, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(target, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(target, 6, source_name="Ice Barrier", effect_id="ice_barrier")

    spill, absorbed, _ = effects.consume_absorbs(target, 4)
    assert spill == 0 and absorbed == 4, "Partial absorb should consume incoming damage without spill"
    assert int(target.res.absorbs["ice_barrier"]["remaining"]) == 2, "Latest-cast shield should be consumed first"
    assert any(entry.get("name") == "Ice Barrier" for entry in effects.build_effect_panel_payload(target)["buffs_magical"]), "Partially consumed shield should remain on panel"

    spill, absorbed, _ = effects.consume_absorbs(target, 4)
    assert spill == 0 and absorbed == 4, "Second absorb consume should continue from latest to earlier layer"
    assert "ice_barrier" not in target.res.absorbs, "Fully consumed latest shield should be removed"
    assert int(target.res.absorbs["power_word_shield"]["remaining"]) == 7, "Earlier shield should continue after latest shield is exhausted"
    assert not any(entry.get("name") == "Ice Barrier" for entry in effects.build_effect_panel_payload(target)["buffs_magical"]), "Fully consumed shield should be removed from panel"

    sov_match = make_match("paladin", "warrior", seed=9022)
    pal_sid, war_sid = sov_match.players
    submit_turn(sov_match, "shield_of_vengeance", _DEF_PASS)
    paladin = sov_match.state[pal_sid]
    enemy = sov_match.state[war_sid]
    sov_absorb = int(paladin.res.absorbs.get("shield_of_vengeance", {}).get("remaining", 0) or 0)
    effects.consume_absorbs(paladin, sov_absorb)
    assert "shield_of_vengeance" not in paladin.res.absorbs, "Fully consumed SoV absorb should be removed"
    assert not any(entry.get("name") == "Shield of Vengeance" for entry in effects.build_effect_panel_payload(paladin)["buffs_magical"]), "Fully consumed SoV should be removed from panel"
    hp_before = enemy.res.hp
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    assert any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_match, 2)), "SoV explosion should still fire after full absorb consumption"
    assert enemy.res.hp < hp_before, "SoV explosion should still deal damage after absorb depletion"
    return True


def scenario_phase0_end_of_turn_ordering_contract_lock() -> bool:
    pet_lethal = make_match("hunter", "warlock", seed=9031)
    hunter_sid, warlock_sid = pet_lethal.players
    submit_turn(pet_lethal, "call_serpent", _DEF_PASS)
    pet_lethal.state[warlock_sid].res.hp = 6
    submit_turn(pet_lethal, _DEF_PASS, _DEF_PASS)
    pet_turn = _turn_lines(pet_lethal, 2)
    pet_idx = next(i for i, line in enumerate(pet_turn) if "Emerald Serpent" in line and ("melees the target" in line or "breathes lightning" in line))
    summary_idx = next(i for i, line in enumerate(pet_turn) if line.startswith("Post-Combat Summary|"))
    winner_idx = next(i for i, line in enumerate(pet_turn) if "wins the duel." in line)
    assert pet_idx < summary_idx < winner_idx, "Pet phase should finish before summary and winner output"

    double_ko = make_match("priest", "warlock", seed=9032)
    p1_sid, p2_sid = double_ko.players
    double_ko.state[p1_sid].res.hp = 1
    double_ko.state[p2_sid].res.hp = 1
    effects.apply_effect_by_id(double_ko.state[p1_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p2_sid, "dot_mode": "fixed"})
    effects.apply_effect_by_id(double_ko.state[p2_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p1_sid, "dot_mode": "fixed"})
    submit_turn(double_ko, _DEF_PASS, _DEF_PASS)
    ko_turn = _turn_lines(double_ko, 1)
    dot_idxs = [i for i, line in enumerate(ko_turn) if "suffers" in line and "Agony" in line]
    ko_idx = next(i for i, line in enumerate(ko_turn) if line == "Double KO. No winner.")
    assert len(dot_idxs) == 2 and max(dot_idxs) < ko_idx, "Winner check should run only after end-of-turn damage systems finish"
    assert double_ko.phase == "ended" and double_ko.winner is None, "Double KO outcome should remain deterministic"
    return True


def scenario_phase0_normal_vs_immediate_parity_ordering_lock() -> bool:
    normal_unknown = make_match("warrior", "mage", seed=9051)
    submit_turn(normal_unknown, "totally_fake_ability", _DEF_PASS)
    normal_turn = _turn_lines(normal_unknown, 1)
    assert any("fumbles (unknown ability)." in line for line in normal_turn), "Normal-path unknown abilities should keep current fumble behavior"

    immediate_unknown = make_match("warlock", "warrior", seed=9052)
    effects.apply_effect_by_id(immediate_unknown.state[immediate_unknown.players[0]], "feared", overrides={"duration": 1})
    submit_turn(immediate_unknown, "totally_fake_ability", _DEF_PASS)
    immediate_turn = _turn_lines(immediate_unknown, 1)
    assert any("fumbles (unknown ability)." in line for line in immediate_turn), "Immediate-path unknown ability behavior should match current fumble behavior"
    assert not any("is feared and cannot act" in line for line in immediate_turn), "Unknown-ability fumble precedence should remain unchanged for immediate path"

    immediate_selection = make_match("warlock", "warrior", seed=9053)
    effects.apply_effect_by_id(immediate_selection.state[immediate_selection.players[0]], "feared", overrides={"duration": 1})
    submit_turn(immediate_selection, "teleport", _DEF_PASS)
    immediate_selection_turn = _turn_lines(immediate_selection, 1)
    assert any("tries to use Demonic Circle: Teleport but is feared and cannot act." in line for line in immediate_selection_turn), "Immediate-path denial should precede selection-failure logs"
    assert not any("Demonic Circle is required." in line for line in immediate_selection_turn), "Immediate-path selection-failure log should remain suppressed when denial applies"

    normal_selection = make_match("druid", "warrior", seed=9054)
    effects.apply_effect_by_id(normal_selection.state[normal_selection.players[0]], "feared", overrides={"duration": 1})
    submit_turn(normal_selection, "maul", _DEF_PASS)
    normal_selection_turn = _turn_lines(normal_selection, 1)
    assert any("tries to use Maul but is feared and cannot act." in line for line in normal_selection_turn), "Normal-path denial should precede selection-failure logs"
    assert not any("wasn't in Bear Form" in line for line in normal_selection_turn), "Normal-path selection-failure log should remain suppressed when denial applies"
    return True


def scenario_nature_resistance_reduces_nature_damage() -> bool:
    raw = 120
    with_target = make_match("warrior", "warrior", seed=4201).state["p2_sid"]
    without_target = make_match("warrior", "warrior", seed=4201).state["p2_sid"]
    for target in (with_target, without_target):
        target.stats["def"] = 10
        target.stats["magic_resist"] = 10
    with_target.stats["nature_resist"] = 20

    resisted = effects.mitigate_damage(raw, with_target, "magic", subschool="nature")
    unresisted = effects.mitigate_damage(raw, without_target, "magic", subschool="nature")

    assert unresisted == _expected_mitigated(raw, 20), "no Nature Resistance should mitigate with DEF + Magic Resist only"
    assert resisted == _expected_mitigated(raw, 40), "Nature Resistance should add to DEF + Magic Resist for Nature damage"
    assert resisted < unresisted, "a target with Nature Resistance should take less Nature damage than one without"
    return True


def scenario_nature_resistance_no_cross_school_protection() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4202).state["p2_sid"]
    target.stats["def"] = 10
    target.stats["magic_resist"] = 10
    target.stats["physical_reduction"] = 10
    target.stats["nature_resist"] = 30

    magic_baseline = _expected_mitigated(raw, 20)      # DEF + Magic Resist, no matching resist
    physical_baseline = _expected_mitigated(raw, 20)   # DEF + Physical Reduction

    for other in ("fire", "frost", "shadow", "holy", "arcane"):
        dmg = effects.mitigate_damage(raw, target, "magic", subschool=other)
        assert dmg == magic_baseline, f"Nature Resistance must not reduce {other} damage"

    generic = effects.mitigate_damage(raw, target, "magic", subschool=None)
    assert generic == magic_baseline, "Nature Resistance must not reduce generic magic damage with no subschool"

    physical = effects.mitigate_damage(raw, target, "physical")
    assert physical == physical_baseline, "Nature Resistance must not reduce physical damage"

    nature = effects.mitigate_damage(raw, target, "magic", subschool="nature")
    assert nature == _expected_mitigated(raw, 50), "matching Nature damage should benefit from Nature Resistance"
    assert nature < magic_baseline, "Nature Resistance should only reduce matching Nature damage"
    return True


def scenario_subschool_resistance_additive_before_curve() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4203).state["p2_sid"]
    target.stats["def"] = 15
    target.stats["magic_resist"] = 12
    target.stats["nature_resist"] = 13

    effective = effects.mitigation_effective_stat(target, "magic", subschool="nature")
    assert effective == 40, "effective stat must be DEF + Magic Resist + Nature Resistance, summed additively"

    reduced = effects.mitigate_damage(raw, target, "magic", subschool="nature")
    assert reduced == _expected_mitigated(raw, 40), "the summed stat must feed the unchanged mitigation curve exactly once"

    # The curve itself is unchanged: an equal effective stat reached purely via
    # Magic Resist mitigates identically, proving resistance is additive input
    # to the same curve rather than a new multiplier.
    curve_only = make_match("warrior", "warrior", seed=4203).state["p2_sid"]
    curve_only.stats["def"] = 15
    curve_only.stats["magic_resist"] = 25
    assert effects.mitigate_damage(raw, curve_only, "magic") == reduced, "the mitigation curve must be preserved; only the additive stat differs"
    return True


def scenario_subschool_resistance_is_generic_via_fire_resist() -> bool:
    # Temporary deterministic proof that the resistance foundation is generic:
    # a raw fire_resist stat (NOT a real Fire Resistance item) already resists
    # Fire damage and nothing else, with no resolver changes.
    raw = 120
    target = make_match("warrior", "warrior", seed=4204).state["p2_sid"]
    target.stats["def"] = 10
    target.stats["magic_resist"] = 10
    target.stats["fire_resist"] = 20

    fire = effects.mitigate_damage(raw, target, "magic", subschool="fire")
    assert fire == _expected_mitigated(raw, 40), "Fire Resistance should reduce Fire damage through the generic mapping"

    nature = effects.mitigate_damage(raw, target, "magic", subschool="nature")
    assert nature == _expected_mitigated(raw, 20), "Fire Resistance must not reduce Nature damage"
    assert fire < nature, "the generic subschool-resistance path must apply per-subschool without new gameplay code"
    return True


def scenario_nature_resistance_applies_across_damage_sources() -> bool:
    # Prove the shared pipeline applies Nature Resistance to multiple source
    # kinds: a direct Nature hit, a Nature DoT tick, and Nature pet damage.
    def direct_hit(nature_resist: bool) -> int:
        match = make_match("shaman", "warrior", seed=4205)
        shaman = match.state[match.players[0]]
        warrior = match.state[match.players[1]]
        shaman.stats["int"] = 200  # large raw so mitigation is clearly visible
        warrior.stats["def"] = 10
        warrior.stats["magic_resist"] = 10
        if nature_resist:
            warrior.stats["nature_resist"] = 60
        hp_before = warrior.res.hp
        submit_turn(match, "lightning_bolt", _DEF_PASS)
        return hp_before - warrior.res.hp

    direct_baseline = direct_hit(False)
    direct_resisted = direct_hit(True)
    assert direct_baseline > 0, "sanity: the direct Nature hit must deal damage"
    assert direct_resisted < direct_baseline, "Nature Resistance must reduce a direct Nature ability hit through the shared pipeline"

    def dot_tick(nature_resist: bool) -> int:
        match = make_match("warrior", "warrior", seed=4206)
        warrior = match.state[match.players[1]]
        warrior.stats["def"] = 10
        warrior.stats["magic_resist"] = 10
        if nature_resist:
            warrior.stats["nature_resist"] = 60
        effects.apply_effect_by_id(
            warrior,
            "wildfire_burn",
            overrides={"duration": 3, "tick_damage": 120, "source_sid": "p1_sid", "school": "magical", "subschool": "nature"},
        )
        sources = effects.tick_dots(warrior, [], "Warrior")
        return next(int(src.get("incoming", 0)) for src in sources if src.get("subschool") == "nature")

    dot_baseline = dot_tick(False)
    dot_resisted = dot_tick(True)
    assert dot_baseline > 0 and dot_resisted < dot_baseline, "Nature Resistance must reduce a Nature DoT tick through the shared mitigation helper"

    def pet_hit(nature_resist: bool) -> int:
        enemy = make_match("warrior", "warrior", seed=4207).state["p2_sid"]
        enemy.stats["def"] = 10
        enemy.stats["magic_resist"] = 10
        if nature_resist:
            enemy.stats["nature_resist"] = 60
        return PET_AI._damage_after_reduction(120, enemy, "magical", "nature")

    pet_baseline = pet_hit(False)
    pet_resisted = pet_hit(True)
    assert pet_baseline > 0 and pet_resisted < pet_baseline, "Nature Resistance must reduce Nature pet/summon damage through the shared pet mitigation helper"
    return True


def scenario_ignore_magic_resist_bypasses_subschool_resistance() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4208).state["p2_sid"]
    target.stats["def"] = 15
    target.stats["magic_resist"] = 12
    target.stats["nature_resist"] = 13

    normal = effects.mitigation_effective_stat(target, "magic", subschool="nature")
    ignored = effects.mitigation_effective_stat(target, "magic", subschool="nature", ignore_magic_resist=True)
    assert normal == 40, "normal Nature mitigation should use DEF + Magic Resist + Nature Resistance"
    assert ignored == 15, "ignore_magic_resist must bypass BOTH Magic Resist and matching Nature Resistance, leaving only DEF"

    normal_damage = effects.mitigate_damage(raw, target, "magic", subschool="nature")
    ignored_damage = effects.mitigate_damage(raw, target, "magic", subschool="nature", ignore_magic_resist=True)
    assert normal_damage == _expected_mitigated(raw, 40), "normal path uses the full additive stat"
    assert ignored_damage == _expected_mitigated(raw, 15), "ignore_magic_resist keeps Defense and the mitigation curve while dropping both resist components"
    assert ignored_damage > normal_damage, "ignoring Magic Resist and Nature Resistance should increase damage taken"
    return True


def scenario_subschool_resistance_compatibility_and_defaults() -> bool:
    raw = 120
    target = make_match("warrior", "warrior", seed=4209).state["p2_sid"]
    target.stats["def"] = 10
    target.stats["magic_resist"] = 10
    target.stats["nature_resist"] = 40

    generic_baseline = _expected_mitigated(raw, 20)

    # Unknown subschool does not crash and gets no specific resistance.
    unknown = effects.mitigate_damage(raw, target, "magic", subschool="chaos")
    assert unknown == generic_baseline, "unknown subschool should resolve to generic magic mitigation without crashing"

    # Missing subschool behaves as generic magic.
    missing = effects.mitigate_damage(raw, target, "magic", subschool=None)
    assert missing == generic_baseline, "missing subschool should behave as generic magic damage"

    # A character without any resistance stat is unchanged versus before.
    plain = make_match("warrior", "warrior", seed=4209).state["p2_sid"]
    plain.stats["def"] = 10
    plain.stats["magic_resist"] = 10
    assert "nature_resist" not in plain.stats, "characters without the item must not carry resistance stats"
    assert effects.mitigate_damage(raw, plain, "magic", subschool="nature") == generic_baseline, "absent resistance stats must default to zero and preserve existing behavior"

    # The canonical helper is a pure lookup: normalization + None for unknowns.
    from games.duel.engine.damage_types import subschool_resistance_stat
    assert subschool_resistance_stat("nature") == "nature_resist", "helper must map nature to nature_resist"
    assert subschool_resistance_stat(" FIRE ") == "fire_resist", "helper must normalize case/whitespace using existing conventions"
    assert subschool_resistance_stat("chaos") is None, "unknown subschool must return None"
    assert subschool_resistance_stat(None) is None, "missing subschool must return None"
    return True
