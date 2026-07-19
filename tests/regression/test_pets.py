"""Pet, totem, and summon regression scenarios (memory, death, resources, AI, mitigation).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import re
import sys

from typing import Any

from harness import (
    CLASSES,
    PETS,
    PET_AI,
    SOCKETS,
    _has_effect,
    _turn_lines,
    effects,
    make_match,
    resolver,
    run_turns,
    submit_turn,
)

from .helpers import (
    _DEF_PASS,
    _active_pet,
    _redirected_boar_damage_taken,
)


def scenario_pet_summon_data_driven() -> bool:
    warlock_match = make_match("warlock", "priest", seed=123)
    for _ in range(4):
        warlock_match.state[warlock_match.players[0]].cooldowns["summon_imp"] = []
        submit_turn(warlock_match, "summon_imp", _DEF_PASS)
    warlock = warlock_match.state[warlock_match.players[0]]
    imp_ids = sorted(pid for pid, pet in warlock.pets.items() if pet.template_id == "imp")
    assert len(imp_ids) == 3, "Imps should obey max_count"

    priest_match = make_match("priest", "warrior", seed=123)
    run_turns(priest_match, [("shadowfiend", _DEF_PASS)])
    priest = priest_match.state[priest_match.players[0]]
    fiend_ids = sorted(pid for pid, pet in priest.pets.items() if pet.template_id == "shadowfiend")
    assert len(fiend_ids) == 1, "Shadowfiend should exist in PlayerState.pets"
    fiend_id = fiend_ids[0]
    fiend_hp_before = priest.pets[fiend_id].hp

    run_turns(priest_match, [("shadowfiend", _DEF_PASS)])
    priest = priest_match.state[priest_match.players[0]]
    fiend_ids_after = sorted(pid for pid, pet in priest.pets.items() if pet.template_id == "shadowfiend")
    assert fiend_ids_after == [fiend_id], "Shadowfiend should refresh instead of duplicating"
    assert priest.pets[fiend_id].hp >= fiend_hp_before, "Refreshed Shadowfiend should reset/refresh hp"

    recent = "\n".join((warlock_match.log[-30:] + priest_match.log[-30:]))
    assert "casts Firebolt" in recent or "melees the target" in recent, "Summoned pets should act in pet phase"
    return True


def scenario_pet_totem_runtime_normalization_phase1() -> bool:
    rules = sys.modules["games.duel.engine.rules"]
    expected_imp_firebolt = rules.base_damage(5, 0.7, 3)

    def _run_imp_damage(owner_int: int) -> tuple[int, Any]:
        match = make_match("warlock", "warrior", seed=222)
        warlock_sid, warrior_sid = match.players
        warlock = match.state[warlock_sid]
        warrior = match.state[warrior_sid]
        warlock.stats["int"] = owner_int
        hp_before = warrior.res.hp

        original_roll = PET_AI.roll
        original_reduction = PET_AI._damage_after_reduction
        PET_AI.roll = lambda die, rng: 3 if die == "d4" else original_roll(die, rng)
        PET_AI._damage_after_reduction = lambda raw, enemy, school: raw
        try:
            submit_turn(match, "summon_imp", _DEF_PASS)
        finally:
            PET_AI.roll = original_roll
            PET_AI._damage_after_reduction = original_reduction

        imp = next(iter(warlock.pets.values()))
        dmg = hp_before - warrior.res.hp
        return dmg, imp

    low_owner_damage, low_owner_imp = _run_imp_damage(owner_int=1)
    high_owner_damage, high_owner_imp = _run_imp_damage(owner_int=500)
    assert low_owner_damage == expected_imp_firebolt, "Imp Firebolt should use [Intellect * 0.7 + d4] with Imp intellect"
    assert high_owner_damage == expected_imp_firebolt, "Imp Firebolt should not scale from owner intellect"

    imp_stats = high_owner_imp.stats
    assert imp_stats == {"acc": 98, "atk": 1, "crit": 7, "def": 5, "eva": 0, "int": 5, "magic_resist": 0, "spd": 8, "spirit": 0}, "Imp runtime stats should be fully explicit and normalized"
    assert high_owner_imp.mp == 9 and high_owner_imp.mp_max == 10, "Imp mana runtime values should be explicit and reflect Firebolt cost with passive regen"
    assert high_owner_imp.entity_type == "demon", "Imp should keep demon entity_type"

    hunter_match = make_match("hunter", "warrior", seed=333)
    submit_turn(hunter_match, "call_saber", _DEF_PASS)
    hunter = hunter_match.state[hunter_match.players[0]]
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None and isinstance(saber.stats, dict), "Hunter pets should still summon with normalized runtime stats"
    assert saber.hp > 0 and saber.hp_max > 0, "Hunter pet behavior should remain functional after normalization"

    totem_match = make_match("shaman", "warrior", seed=334)
    submit_turn(totem_match, "mana_tide_totem", _DEF_PASS)
    shaman = totem_match.state[totem_match.players[0]]
    totem = _active_pet(shaman, "mana_tide_totem")
    assert totem is not None, "Totem should still summon"
    assert totem.entity_type == "totem", "Totems should keep totem entity_type"
    assert isinstance(totem.stats, dict) and {"atk", "int", "def", "spirit", "spd", "crit", "acc", "eva"} <= set(totem.stats.keys()), "Totem runtime should include normalized stat shape"

    control_match = make_match("mage", "warrior", seed=335)
    warrior_hp_before = control_match.state[control_match.players[1]].res.hp
    submit_turn(control_match, "fireball", _DEF_PASS)
    assert control_match.state[control_match.players[1]].res.hp < warrior_hp_before, "Non-pet class behavior should remain unchanged in this phase"
    return True


def scenario_pet_totem_runtime_normalization_phase2b() -> bool:
    original_roll = PET_AI.roll
    original_reduction = PET_AI._damage_after_reduction
    original_hit_chance = PET_AI.hit_chance
    original_frostsaber_chance = PETS["frostsaber"]["special_chance"]
    original_serpent_chance = PETS["emerald_serpent"]["special_chance"]
    original_boar_chance = PETS["barrens_boar"]["special_chance"]
    PETS["frostsaber"]["special_chance"] = 0.0
    PETS["emerald_serpent"]["special_chance"] = 0.0
    PETS["barrens_boar"]["special_chance"] = 0.0
    PET_AI.hit_chance = lambda acc, eva: 100
    PET_AI._damage_after_reduction = lambda raw, enemy, school: raw
    PET_AI.roll = lambda die, rng: {"d4": 2, "d6": 4}.get(die, original_roll(die, rng))
    try:
        # Shadowfiend explicit stats + melee formula + mana restore identity.
        fiend_match = make_match("priest", "warrior", seed=700)
        priest_sid, warrior_sid = fiend_match.players
        priest = fiend_match.state[priest_sid]
        warrior = fiend_match.state[warrior_sid]
        priest.stats["int"] = 999
        hp_before = warrior.res.hp
        submit_turn(fiend_match, "shadowfiend", _DEF_PASS)
        assert hp_before - warrior.res.hp == 10, "Shadowfiend should use [Attack * 1.0 + d4] with explicit pet attack"
        fiend = _active_pet(priest, "shadowfiend")
        assert fiend is not None and fiend.stats == {"acc": 98, "atk": 8, "crit": 7, "def": 8, "eva": 0, "int": 0, "magic_resist": 0, "spd": 8, "spirit": 0}, "Shadowfiend runtime stats should be normalized and explicit"
        assert fiend.entity_type == "demon", "Shadowfiend should remain a demon entity"
        assert any("Shadowfiend restores 13 mana" in line for line in fiend_match.log), "Shadowfiend should still restore owner mana on hit"

        # Frostsaber energy model + basic + bite costs/formulas.
        saber_match = make_match("hunter", "warrior", seed=701)
        hunter_sid, warrior_sid = saber_match.players
        hunter = saber_match.state[hunter_sid]
        warrior = saber_match.state[warrior_sid]
        submit_turn(saber_match, "call_saber", _DEF_PASS)
        saber = _active_pet(hunter, "frostsaber")
        assert saber is not None, "Frostsaber should summon"
        assert (saber.energy_max, saber.energy) == (30, 30), "Frostsaber should passively regen 5 energy each turn and clamp at max"
        saber.energy = 7
        submit_turn(saber_match, _DEF_PASS, _DEF_PASS)
        assert saber.energy == 12, "Frostsaber basic attacks should not consume energy; passive regen should add 5 each turn"
        saber.energy = 20
        hp_before = warrior.res.hp
        hunter.pending_pet_command = "special"
        submit_turn(saber_match, _DEF_PASS, _DEF_PASS)
        assert hp_before - warrior.res.hp == 16, "Frostsaber Bite should use [Attack * 2.0 + d6]"
        assert saber.energy == 5, "Forced Bite should pay 20 and still receive +5 passive end-of-turn regen"

        # Emerald Serpent mana model + Lightning Breath scaling/heal-from-actual-damage.
        serpent_match = make_match("hunter", "warrior", seed=702)
        hunter_sid, warrior_sid = serpent_match.players
        hunter = serpent_match.state[hunter_sid]
        warrior = serpent_match.state[warrior_sid]
        hunter.res.hp -= 20
        submit_turn(serpent_match, "call_serpent", _DEF_PASS)
        serpent = _active_pet(hunter, "emerald_serpent")
        assert serpent is not None and serpent.mp_max == 40, "Emerald Serpent should use explicit 40 mana pool"
        assert serpent.mp == 40, "Emerald Serpent passive mana regen should clamp at max when already full"
        serpent.hp = 5
        warrior_hp_before = warrior.res.hp
        hunter_hp_before = hunter.res.hp
        hunter.pending_pet_command = "special"
        submit_turn(serpent_match, _DEF_PASS, _DEF_PASS)
        assert warrior_hp_before - warrior.res.hp == 19, "Lightning Breath should use [Intellect * 1.5 + d6]"
        assert serpent.hp == 14 and hunter.res.hp == hunter_hp_before + 9, "Lightning Breath healing should be 50% of actual HP damage dealt"
        assert serpent.mp == 27, "Lightning Breath should spend 15 mana and still receive +2 passive end-of-turn mana regen"
        serpent.mp = 39
        submit_turn(serpent_match, _DEF_PASS, _DEF_PASS)
        assert serpent.mp == 40, "Pet mana regen should clamp to max"

        # Barrens Boar rage model + no immediate special + fallback to basic with required log style.
        boar_match = make_match("hunter", "warrior", seed=703)
        hunter_sid, warrior_sid = boar_match.players
        hunter = boar_match.state[hunter_sid]
        warrior = boar_match.state[warrior_sid]
        submit_turn(boar_match, "call_boar", _DEF_PASS)
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None and boar.rage_max == 20, "Barrens Boar should use explicit 20 max rage"
        assert boar.rage == 5, "Barrens Boar should gain rage only from basic attack"
        boar.rage = 0
        hunter.pending_pet_command = "special"
        warrior_hp_before = warrior.res.hp
        submit_turn(boar_match, _DEF_PASS, _DEF_PASS)
        assert warrior_hp_before - warrior.res.hp == 8, "Insufficient-rage special should fall back to basic attack"
        assert boar.rage == 5, "Fallback basic attack should still grant boar rage"
        assert not _has_effect(hunter, "blocking_defence"), "Boar should not apply Blocking Defence without enough rage"
        assert not any("tried to use braces to intercept attacks but didn't have enough rage" in line for line in boar_match.log), "Insufficient-resource fallback log line should be removed"

        # Mana Tide Totem and Capacitor Totem utility should not use hit/accuracy rolls; totems remain entity_type=totem.
        totem_match = make_match("shaman", "rogue", seed=704)
        shaman_sid, rogue_sid = totem_match.players
        shaman = totem_match.state[shaman_sid]
        rogue = totem_match.state[rogue_sid]
        rogue.stats["eva"] = 999
        shaman.res.mp = max(0, shaman.res.mp - 30)
        mp_before = shaman.res.mp
        submit_turn(totem_match, "mana_tide_totem", _DEF_PASS)
        mana_tide = _active_pet(shaman, "mana_tide_totem")
        assert mana_tide is not None and mana_tide.entity_type == "totem", "Mana Tide should remain a totem entity"
        assert shaman.res.mp > mp_before, "Mana Tide restore should not depend on accuracy rolls"
        assert mana_tide.stats == {"acc": 0, "atk": 0, "crit": 0, "def": 0, "eva": 0, "int": 0, "magic_resist": 0, "spd": 0, "spirit": 0}, "Mana Tide should use explicit totem stats"

        cap_match = make_match("shaman", "rogue", seed=705)
        shaman_sid, rogue_sid = cap_match.players
        rogue = cap_match.state[rogue_sid]
        rogue.stats["eva"] = 999
        submit_turn(cap_match, "capacitor_totem", _DEF_PASS)
        assert any("is charging" in line for line in _turn_lines(cap_match, 1)), "Capacitor should charge on summon turn"
        submit_turn(cap_match, _DEF_PASS, _DEF_PASS)
        assert any("discharges!" in line for line in _turn_lines(cap_match, 2)), "Capacitor discharge should occur after one-turn delay without hit/accuracy checks"
        assert any(pet.template_id == "capacitor_totem" for pet in cap_match.state[shaman_sid].pets.values()) is False, "Capacitor Totem should disappear after discharging"

        # Broad no-unrelated-class sanity check.
        control_match = make_match("mage", "warrior", seed=706)
        hp_before = control_match.state[control_match.players[1]].res.hp
        submit_turn(control_match, "fireball", _DEF_PASS)
        assert control_match.state[control_match.players[1]].res.hp < hp_before, "Unrelated class combat behavior should remain unchanged"
        return True
    finally:
        PET_AI.roll = original_roll
        PET_AI._damage_after_reduction = original_reduction
        PET_AI.hit_chance = original_hit_chance
        PETS["frostsaber"]["special_chance"] = original_frostsaber_chance
        PETS["emerald_serpent"]["special_chance"] = original_serpent_chance
        PETS["barrens_boar"]["special_chance"] = original_boar_chance


def scenario_hunter_pet_summon_swap_memory() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]

    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Frostsaber should summon"
    assert any("calls for Frostsaber." in line for line in _turn_lines(match, 1)), "Hunter summon log should say calls for Frostsaber"
    saber.hp = 12

    submit_turn(match, "call_serpent", _DEF_PASS)
    assert _active_pet(hunter, "frostsaber") is None, "Frostsaber should be dismissed when serpent is summoned"
    assert (hunter.hunter_pet_memory.get("frostsaber") or {}).get("hp") == 12, "Dismissed Frostsaber HP should be remembered"
    assert any("calls for Emerald Serpent." in line for line in _turn_lines(match, 2)), "Hunter summon log should say calls for Emerald Serpent"

    assert not hunter.cooldowns.get("call_saber"), "Companion calls should not go on cooldown"
    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None and saber.hp == 12, "Re-summoned Frostsaber should return with remembered HP"
    return True


def scenario_hunter_only_one_active_pet() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]
    run_turns(match, [("call_saber", _DEF_PASS), ("call_boar", _DEF_PASS)])
    active_templates = sorted(pet.template_id for pet in hunter.pets.values())
    assert active_templates == ["barrens_boar"], "Hunter should have exactly one active pet at a time"
    assert ((hunter.hunter_pet_memory.get("frostsaber") or {}).get("hp") or 0) > 0, "Dismissed saber HP should be stored"
    return True


def scenario_hunter_companion_calls_have_no_cooldown() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]

    submit_turn(match, "call_saber", _DEF_PASS)
    assert not hunter.cooldowns.get("call_saber"), "Call Frostsaber should have no cooldown entry after use"

    submit_turn(match, "call_serpent", _DEF_PASS)
    assert not hunter.cooldowns.get("call_serpent"), "Call Emerald Serpent should have no cooldown entry after use"

    submit_turn(match, "call_boar", _DEF_PASS)
    assert not hunter.cooldowns.get("call_boar"), "Call Barrens Boar should have no cooldown entry after use"
    assert any("cast Call Emerald Serpent. calls for Emerald Serpent." in line for line in match.log), "Hunter combat log should use the Call Emerald Serpent name"
    return True


def scenario_hunter_aimed_shot_raptor_pet_special() -> bool:
    match = make_match("hunter", "warrior", seed=1)
    hunter = match.state[match.players[0]]
    enemy = match.state[match.players[1]]
    enemy.res.hp = enemy.res.hp_max = 999
    run_turns(match, [("call_saber", _DEF_PASS)])

    while not _has_effect(hunter, "raptor_strike_proc"):
        submit_turn(match, "aimed_shot", _DEF_PASS)
        assert match.turn < 10, "Aimed Shot should proc within a few deterministic turns"

    submit_turn(match, "raptor_strike", _DEF_PASS)
    assert not _has_effect(hunter, "raptor_strike_proc"), "Raptor Strike should consume its proc"
    assert f"{match.players[0][:5]} has Raptor Strike!" in match.log, "Aimed Shot proc log should use the actor sid token so snapshots can render Hunter(you)"
    assert hunter.pending_pet_command is None, "Pet command should be consumed after the pet phase"
    latest_turn = match.log[match.log.index("Turn 3") + 1:]
    assert any("Frostsaber bites the target" in line for line in latest_turn), "Raptor Strike should force the pet special attack, not the basic melee"
    assert not any("Frostsaber melees the target" in line for line in latest_turn), "Forced pet special should replace the normal melee attack that turn"
    return True


def scenario_hunter_boar_redirect() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]
    run_turns(match, [("call_boar", _DEF_PASS)])
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should be active"

    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    warrior.res.rage = warrior.res.rage_max
    submit_turn(match, _DEF_PASS, "mortal_strike")
    assert hunter.res.hp == hunter_hp_before, "Single-target attack should be redirected to the boar"
    assert boar.hp < boar_hp_before, "Boar should take redirected damage"

    warrior.res.rage = warrior.res.rage_max
    hunter_hp_before_aoe = hunter.res.hp
    submit_turn(match, _DEF_PASS, "dragon_roar")
    assert hunter.res.hp < hunter_hp_before_aoe, "AoE should not redirect to the boar"

    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    effects.apply_effect_by_id(hunter, "wildfire_burn", overrides={"duration": 2, "tick_damage": 3, "source_sid": warrior_sid})
    hunter_hp_before_dot = hunter.res.hp
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert hunter.res.hp < hunter_hp_before_dot, "DoT damage should not redirect to the boar"
    return True


def scenario_hunter_boar_redirects_single_target_cc() -> bool:
    match = make_match("hunter", "rogue", seed=123)
    hunter_sid, _ = match.players
    hunter = match.state[hunter_sid]
    run_turns(match, [("call_boar", _DEF_PASS)])
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should be active for CC redirect coverage"

    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    submit_turn(match, _DEF_PASS, "kidney_shot")
    assert not _has_effect(hunter, "stunned"), "Single-target CC should be redirected off the Hunter"
    assert _has_effect(boar, "stunned"), "Single-target CC should land on the boar"
    assert any("Barrens Boar intercepts Kidney Shot" in line for line in _turn_lines(match, 2)), "Redirected CC should emit the intercept log"

    effects.remove_effect(boar, "stunned")
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    submit_turn(match, _DEF_PASS, "fan_of_knives")
    assert not _has_effect(boar, "stunned"), "AoE CC should not redirect to the boar"
    return True


def scenario_hunter_boar_redirect_same_turn_brace() -> bool:
    match = make_match("warlock", "hunter", seed=3)
    hunter = match.state[match.players[1]]
    hunter_hp_before = hunter.res.hp
    submit_turn(match, "drain_life", "call_boar")

    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should be active"
    latest_turn = _turn_lines(match, 1)
    assert not any("Barrens Boar braces to intercept attacks." in line for line in latest_turn), "Boar should not brace on summon turn without enough rage"
    assert not any("Barrens Boar intercepts Drain Life" in line for line in latest_turn), "Intercept should not happen when summon-turn rage is insufficient"
    assert hunter.res.hp < hunter_hp_before, "Without enough rage, same-turn Drain Life should still hit the Hunter"
    assert boar.rage >= 0, "Boar should keep explicit rage tracking even when same-turn brace fails"
    return True


def scenario_hunter_boar_forced_pre_action_redirect_is_consistent() -> bool:
    match = make_match("warlock", "hunter", seed=123)
    hunter_sid = match.players[1]
    hunter = match.state[hunter_sid]

    submit_turn(match, _DEF_PASS, "call_boar")
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active"

    hunter.pending_pet_command = "special"
    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    submit_turn(match, "drain_life", _DEF_PASS)

    latest_turn = _turn_lines(match, 2)
    assert any("Barrens Boar braces to intercept attacks." in line for line in latest_turn), "Forced pre-action special should still log the brace"
    assert any("Barrens Boar intercepts Drain Life" in line for line in latest_turn), "Forced pre-action special should redirect same-turn single-target spells"
    assert hunter.res.hp == hunter_hp_before, "Forced pre-action brace should keep single-target damage off the Hunter"
    assert boar.hp < boar_hp_before, "Redirected damage should be applied to the boar"
    return True


def scenario_hunter_boar_no_late_brace_without_redirect() -> bool:
    match = make_match("warlock", "hunter", seed=1)
    hunter_sid = match.players[1]
    hunter = match.state[hunter_sid]

    submit_turn(match, _DEF_PASS, "call_boar")

    for turn in range(2, 10):
        hp_before = hunter.res.hp
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None and boar.hp > 0, "Barrens Boar should remain alive for the redirect consistency window"
        submit_turn(match, "drain_life", _DEF_PASS)
        latest_turn = _turn_lines(match, turn)
        brace_logged = any("Barrens Boar braces to intercept attacks." in line for line in latest_turn)
        intercept_logged = any("Barrens Boar intercepts Drain Life" in line for line in latest_turn)
        damage_attempt_logged = any("cast Drain Life." in line and "Deals" in line for line in latest_turn)
        if brace_logged:
            if damage_attempt_logged:
                assert intercept_logged, "Brace log should only appear on turns where redirect actually occurs for a same-turn single-target damage event"
                assert hunter.res.hp == hp_before, "Hunter should not take single-target damage on brace turns"
    return True


def scenario_hunter_raptor_strike_forces_boar_redirect() -> bool:
    match = make_match("hunter", "warlock", seed=1)
    hunter_sid, warlock_sid = match.players
    hunter = match.state[hunter_sid]
    warlock = match.state[warlock_sid]
    warlock.res.hp = warlock.res.hp_max = 999

    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active for forced-special coverage"

    while not _has_effect(hunter, "raptor_strike_proc"):
        submit_turn(match, "aimed_shot", _DEF_PASS)
        assert match.turn < 10, "Aimed Shot should proc Raptor Strike within a few deterministic turns"

    boar.rage = max(5, int(getattr(boar, "rage", 0) or 0))
    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    submit_turn(match, "raptor_strike", "drain_life")

    latest_turn = _turn_lines(match, match.turn)
    assert any("Barrens Boar braces to intercept attacks." in line for line in latest_turn), "Raptor Strike should force Barrens Boar's pre-action special"
    assert any("Barrens Boar intercepts Drain Life" in line for line in latest_turn), "Forced Barrens Boar special should redirect same-turn single-target spells"
    assert hunter.res.hp == hunter_hp_before, "Forced Barrens Boar special should keep same-turn single-target damage off the Hunter"
    assert boar.hp < boar_hp_before, "Forced redirect should damage the boar instead"
    assert not _has_effect(hunter, "raptor_strike_proc"), "Raptor Strike should consume its proc after use"
    return True


def scenario_hunter_pet_permanent_death() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]
    warrior = match.state[match.players[1]]
    submit_turn(match, "call_serpent", _DEF_PASS)
    serpent = _active_pet(hunter, "emerald_serpent")
    assert serpent is not None, "Emerald Serpent should summon before testing permanent death lockout"

    warrior.res.rage = 10
    submit_turn(match, _DEF_PASS, "dragon_roar")
    latest_turn = _turn_lines(match, match.turn)
    assert any("Dragon Roar hits" in line and "Emerald Serpent" in line for line in latest_turn), "AoE should hit the active hunter pet"
    assert any(line == "Emerald Serpent dies." for line in latest_turn), "Lethal AoE damage should kill the pet immediately"
    assert hunter.dead_hunter_pets.get("emerald_serpent"), "Dead hunter pet should be marked permanently dead"
    serpent_memory = hunter.hunter_pet_memory.get("emerald_serpent") or {}
    assert serpent_memory.get("hp") == 0, "Permanent pet death should zero remembered HP"
    assert serpent_memory.get("mp") == 0 and serpent_memory.get("energy") == 0 and serpent_memory.get("rage") == 0, "Permanent pet death should zero remembered resources"
    assert not any(pet.template_id == "emerald_serpent" for pet in hunter.pets.values()), "Dead Emerald Serpent should be removed from active pets"

    hunter.cooldowns.clear()
    pet_count_before = len(hunter.pets)
    submit_turn(match, "call_serpent", _DEF_PASS)
    assert _active_pet(hunter, "emerald_serpent") is None, "Permanently dead hunter pet should not be summoned again"
    assert len(hunter.pets) == pet_count_before, "Re-summon attempt should not create a replacement Emerald Serpent"
    latest_turn = _turn_lines(match, match.turn)
    assert any("Emerald Serpent has fallen and cannot be summoned again this match." in line for line in latest_turn), "Failure message should be logged"
    return True


def scenario_hunter_pet_permanent_death_resummon_blocked() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]
    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None
    saber.hp = 0
    resolver.cleanup_pets(match)
    assert hunter.dead_hunter_pets.get("frostsaber"), "Dead hunter pet should be marked permanently dead"
    saber_memory = hunter.hunter_pet_memory.get("frostsaber") or {}
    assert saber_memory.get("hp") == 0, "Permanent pet death should zero remembered HP"
    assert saber_memory.get("mp") == 0 and saber_memory.get("energy") == 0 and saber_memory.get("rage") == 0, "Permanent pet death should zero remembered resources"
    assert not any(pet.template_id == "frostsaber" for pet in hunter.pets.values()), "Dead Frostsaber should be removed from active pets"

    hunter.cooldowns.clear()
    pet_count_before = len(hunter.pets)
    submit_turn(match, "call_saber", _DEF_PASS)
    assert _active_pet(hunter, "frostsaber") is None, "Permanently dead hunter pet should not be summoned again"
    assert len(hunter.pets) == pet_count_before, "Re-summon attempt should not create a replacement Frostsaber"
    latest_turn = _turn_lines(match, match.turn)
    assert any("Frostsaber has fallen and cannot be summoned again this match." in line for line in latest_turn), "Failure message should be logged"
    return True


def scenario_hunter_dead_pet_type_does_not_block_other_pet_types() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]
    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Frostsaber should summon before testing permanent death lockout"

    saber.hp = 0
    resolver.cleanup_pets(match)
    assert hunter.dead_hunter_pets.get("frostsaber"), "Frostsaber should be marked permanently dead"

    hunter.cooldowns.clear()
    submit_turn(match, "call_saber", _DEF_PASS)
    assert _active_pet(hunter, "frostsaber") is None, "Dead pet type should stay blocked"

    hunter.cooldowns.clear()
    submit_turn(match, "call_serpent", _DEF_PASS)
    serpent = _active_pet(hunter, "emerald_serpent")
    assert serpent is not None, "Other living pet types should still summon normally"
    assert not hunter.dead_hunter_pets.get("emerald_serpent"), "Living pet types should not be marked dead when another pet dies"
    return True


def scenario_hunter_dismissed_pet_clears_runtime_effects() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]
    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None
    effects.apply_effect_by_id(saber, "wildfire_burn", overrides={"duration": 2, "tick_damage": 4, "source_sid": match.players[1]})
    effects.apply_effect_by_id(saber, "stealth", overrides={"duration": 2})
    remembered_hp = saber.hp

    submit_turn(match, "call_serpent", _DEF_PASS)
    assert (hunter.hunter_pet_memory.get("frostsaber") or {}).get("hp") == remembered_hp, "Dismiss should store current HP before removing the pet"
    dismissed_turn = match.turn
    run_turns(match, [(_DEF_PASS, _DEF_PASS), (_DEF_PASS, _DEF_PASS)])
    assert (hunter.hunter_pet_memory.get("frostsaber") or {}).get("hp") == remembered_hp, "Dismissed pet should not keep taking DoT ticks"
    idle_turn_logs = _turn_lines(match, dismissed_turn + 1) + _turn_lines(match, dismissed_turn + 2)
    assert not any("Frostsaber" in line for line in idle_turn_logs), "Dismissed pet should not keep acting or logging runtime effects while inactive"

    assert not hunter.cooldowns.get("call_saber"), "Companion calls should remain off cooldown after swaps"
    submit_turn(match, "call_saber", _DEF_PASS)
    saber_returned = _active_pet(hunter, "frostsaber")
    assert saber_returned is not None and saber_returned.hp == remembered_hp, "Re-summoned pet should return at remembered HP"
    assert not saber_returned.effects, "Dismissed pet should return without old runtime effects"
    return True


def scenario_hunter_multi_pet_memory_swap_cycle() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]

    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Frostsaber should summon"
    saber.hp = 12

    hunter.cooldowns.clear()
    submit_turn(match, "call_serpent", _DEF_PASS)
    serpent = _active_pet(hunter, "emerald_serpent")
    assert serpent is not None, "Emerald Serpent should summon"
    assert (hunter.hunter_pet_memory.get("frostsaber") or {}).get("hp") == 12, "Frostsaber HP should be remembered on dismissal"
    serpent.hp = 9

    hunter.cooldowns.clear()
    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should summon"
    assert (hunter.hunter_pet_memory.get("emerald_serpent") or {}).get("hp") == 9, "Serpent HP should be remembered on dismissal"
    boar.hp = 7

    hunter.cooldowns.clear()
    submit_turn(match, "call_saber", _DEF_PASS)
    saber_returned = _active_pet(hunter, "frostsaber")
    assert saber_returned is not None and saber_returned.hp == 12, "Frostsaber should return at its remembered HP after multiple swaps"
    assert (hunter.hunter_pet_memory.get("barrens_boar") or {}).get("hp") == 7, "Boar HP should be remembered when it is dismissed"
    assert sorted(pet.template_id for pet in hunter.pets.values()) == ["frostsaber"], "Only one Hunter pet should remain active after repeated swaps"

    hunter.cooldowns.clear()
    submit_turn(match, "call_serpent", _DEF_PASS)
    serpent_returned = _active_pet(hunter, "emerald_serpent")
    assert serpent_returned is not None and serpent_returned.hp >= 9, "Emerald Serpent should return with at least its remembered HP after multiple swaps before any same-turn self-healing"

    hunter.cooldowns.clear()
    submit_turn(match, "call_boar", _DEF_PASS)
    boar_returned = _active_pet(hunter, "barrens_boar")
    assert boar_returned is not None and boar_returned.hp == 7, "Barrens Boar should return at its remembered HP after multiple swaps"
    return True


def scenario_hunter_pet_resource_memory_and_clamp() -> bool:
    original_serpent_chance = PETS["emerald_serpent"].get("special_chance")
    original_saber_chance = PETS["frostsaber"].get("special_chance")
    original_boar_chance = PETS["barrens_boar"].get("special_chance")
    PETS["emerald_serpent"]["special_chance"] = 0
    PETS["frostsaber"]["special_chance"] = 0
    PETS["barrens_boar"]["special_chance"] = 0
    try:
        match = make_match("hunter", "warrior", seed=8129)
        hunter = match.state[match.players[0]]

        submit_turn(match, "call_serpent", _DEF_PASS)
        serpent = _active_pet(hunter, "emerald_serpent")
        assert serpent is not None, "Emerald Serpent should summon"
        serpent.hp = 9
        serpent.mp = 7

        submit_turn(match, "call_saber", _DEF_PASS)
        serpent_memory = hunter.hunter_pet_memory.get("emerald_serpent") or {}
        assert serpent_memory.get("hp") == 9, "Emerald Serpent HP should be remembered on swap"
        assert serpent_memory.get("mp") == 7, "Emerald Serpent mana should be remembered on swap"
        saber = _active_pet(hunter, "frostsaber")
        assert saber is not None, "Frostsaber should summon after swapping from serpent"
        saber.hp = 12
        saber.energy = 11

        submit_turn(match, "call_boar", _DEF_PASS)
        saber_memory = hunter.hunter_pet_memory.get("frostsaber") or {}
        assert saber_memory.get("hp") == 12, "Frostsaber HP should be remembered on swap"
        assert saber_memory.get("energy") == 11, "Frostsaber energy should be remembered on swap"
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Barrens Boar should summon after swapping from saber"
        boar.hp = 6
        boar.rage = 4

        submit_turn(match, "call_serpent", _DEF_PASS)
        boar_memory = hunter.hunter_pet_memory.get("barrens_boar") or {}
        assert boar_memory.get("hp") == 6, "Barrens Boar HP should be remembered on swap"
        assert boar_memory.get("rage") == 4, "Barrens Boar rage should be remembered on swap"
        serpent_returned = _active_pet(hunter, "emerald_serpent")
        assert serpent_returned is not None, "Emerald Serpent should be resummoned"
        assert serpent_returned.hp == 9, "Emerald Serpent should restore remembered HP"
        assert serpent_returned.mp >= 7, "Emerald Serpent should restore remembered mana before normal turn-end regen"

        submit_turn(match, "call_boar", _DEF_PASS)
        hunter.hunter_pet_memory["emerald_serpent"] = {"hp": 99, "mp": 999, "energy": 9, "rage": 9}
        submit_turn(match, "call_serpent", _DEF_PASS)
        clamped_serpent = _active_pet(hunter, "emerald_serpent")
        assert clamped_serpent is not None, "Emerald Serpent should summon for clamp coverage"
        assert clamped_serpent.hp == clamped_serpent.hp_max, "Remembered HP should clamp to current max HP"
        assert clamped_serpent.mp == clamped_serpent.mp_max, "Remembered mana should clamp to current max mana"
        assert clamped_serpent.energy == 0 and clamped_serpent.rage == 0, "Unsupported serpent resources should remain at max-valid values"
        return True
    finally:
        PETS["emerald_serpent"]["special_chance"] = original_serpent_chance
        PETS["frostsaber"]["special_chance"] = original_saber_chance
        PETS["barrens_boar"]["special_chance"] = original_boar_chance


def scenario_non_persistent_pet_memory_unchanged() -> bool:
    match = make_match("warlock", "warrior", seed=8130)
    warlock = match.state[match.players[0]]
    submit_turn(match, "summon_imp", _DEF_PASS)
    imp = _active_pet(warlock, "imp")
    assert imp is not None, "Imp should summon for non-persistent baseline coverage"
    imp.hp = 0
    resolver.cleanup_pets(match)
    assert not warlock.hunter_pet_memory, "Non-persistent pets should not populate hunter companion memory"
    assert not warlock.dead_hunter_pets, "Non-persistent pet defeat should not set permanent-death hunter companion flags"
    return True


def scenario_hunter_redirect_removed_on_pet_dismiss() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]

    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should summon before redirect coverage"
    assert any("calls for Barrens Boar." in line for line in _turn_lines(match, 1)), "Hunter summon log should say calls for Barrens Boar"

    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    hunter.cooldowns.clear()
    submit_turn(match, "call_serpent", _DEF_PASS)
    assert _active_pet(hunter, "barrens_boar") is None, "Boar should be dismissed when another companion is summoned"

    warrior.res.rage = warrior.res.rage_max
    hunter_hp_before = hunter.res.hp
    submit_turn(match, _DEF_PASS, "mortal_strike")
    latest_turn = _turn_lines(match, match.turn)
    assert hunter.res.hp < hunter_hp_before, "Dismissed boar should no longer intercept single-target attacks"
    assert not any("Barrens Boar intercepts Mortal Strike" in line for line in latest_turn), "Dismissed boar should not produce redirect logs"
    return True


def scenario_hunter_serpent_special_respects_stealth() -> bool:
    match = make_match("hunter", "rogue", seed=123)
    hunter = match.state[match.players[0]]
    rogue = match.state[match.players[1]]

    submit_turn(match, "call_serpent", _DEF_PASS)
    hunter.pending_pet_command = "special"
    submit_turn(match, _DEF_PASS, "vanish")

    assert _has_effect(rogue, "stealth"), "Rogue should still be stealthed after Vanish"
    latest_turn = match.log[match.log.index("Turn 2") + 1:]
    assert any("Emerald Serpent breathes lightning. Target is stealthed — Miss!" in line for line in latest_turn), "Lightning Breath should miss stealthed targets"
    assert not any("Emerald Serpent breathes lightning for" in line for line in latest_turn), "Lightning Breath should not deal damage into stealth"
    assert not any("stealth broken by Lightning Breath" in line for line in latest_turn), "Hunter pet specials must not break stealth when they miss"
    return True


def scenario_pet_action_text_persists_on_miss() -> bool:
    hunter_match = make_match("hunter", "rogue", seed=123)
    hunter = hunter_match.state[hunter_match.players[0]]
    submit_turn(hunter_match, "call_serpent", _DEF_PASS)
    hunter.pending_pet_command = "special"
    submit_turn(hunter_match, _DEF_PASS, "vanish")
    latest_hunter_turn = hunter_match.log[hunter_match.log.index("Turn 2") + 1:]
    assert any("Emerald Serpent breathes lightning. Target is stealthed — Miss!" in line for line in latest_hunter_turn), "Serpent special should keep its action text on miss"

    warlock_hit_match = make_match("warlock", "warrior", seed=123)
    submit_turn(warlock_hit_match, "summon_imp", _DEF_PASS)
    assert any("Imp casts Firebolt for" in line for line in warlock_hit_match.log), "Imp hit logs should use Firebolt action text"

    warlock_miss_match = make_match("warlock", "rogue", seed=123)
    submit_turn(warlock_miss_match, "summon_imp", _DEF_PASS)
    submit_turn(warlock_miss_match, _DEF_PASS, "vanish")
    latest_warlock_turn = warlock_miss_match.log[warlock_miss_match.log.index("Turn 2") + 1:]
    assert any("Imp casts Firebolt. Target is stealthed — Miss!" in line for line in latest_warlock_turn), "Imp miss logs should keep Firebolt action text"

    priest_hit_match = make_match("priest", "warrior", seed=123)
    submit_turn(priest_hit_match, "shadowfiend", _DEF_PASS)
    assert any("Shadowfiend melees the target for" in line for line in priest_hit_match.log), "Shadowfiend hit logs should use its melee action text"

    priest_miss_match = make_match("priest", "rogue", seed=123)
    priest_miss_rogue = priest_miss_match.state[priest_miss_match.players[1]]
    submit_turn(priest_miss_match, "shadowfiend", _DEF_PASS)
    effects.remove_effect(priest_miss_rogue, "stealth")
    effects.apply_effect_by_id(priest_miss_rogue, "evasion")
    submit_turn(priest_miss_match, _DEF_PASS, _DEF_PASS)
    latest_priest_turn = priest_miss_match.log[priest_miss_match.log.index("Turn 2") + 1:]
    assert any("Shadowfiend melees the target. Target evades the attack — Miss!" in line for line in latest_priest_turn), "Shadowfiend evade logs should keep its melee action text"
    return True


def scenario_imp_firebolt_immunity_logs_under_cloak() -> bool:
    match = make_match("warlock", "rogue", seed=123)
    rogue = match.state[match.players[1]]
    effects.remove_effect(rogue, "stealth")
    submit_turn(match, "summon_imp", "cloak")
    latest_turn = _turn_lines(match, 1)
    assert any("Imp casts Firebolt. Immune!" in line for line in latest_turn), "Imp Firebolt should log immunity when Cloak blocks magical damage"
    assert not any("Imp casts Firebolt for" in line for line in latest_turn), "Imp should not log damage when fully immune"
    assert _has_effect(rogue, "cloak_of_shadows"), "Cloak should still be active"
    return True


def scenario_imp_firebolt_target_check_ordering() -> bool:
    stealth_then_immunity = make_match("warlock", "rogue", seed=123)
    submit_turn(stealth_then_immunity, "summon_imp", "cloak")
    stealth_turn = _turn_lines(stealth_then_immunity, 1)
    assert any("Imp casts Firebolt. Target is stealthed — Miss!" in line for line in stealth_turn), "Stealth should be checked before immunity for Imp Firebolt"
    assert not any("Imp casts Firebolt. Immune!" in line for line in stealth_turn), "Stealth+immunity overlap should not log immunity first"

    blink_then_immunity = make_match("warlock", "mage", seed=123)
    mage = blink_then_immunity.state[blink_then_immunity.players[1]]
    effects.apply_effect_by_id(mage, "divine_shield", overrides={"duration": 2})
    submit_turn(blink_then_immunity, "summon_imp", "blink")
    blink_turn = _turn_lines(blink_then_immunity, 1)
    assert any("Imp casts Firebolt. Target blinks away — Miss." in line for line in blink_turn), "Blink-like untargetable should be checked before immunity for Imp Firebolt"
    assert not any("Imp casts Firebolt. Immune!" in line for line in blink_turn), "Blink+immunity overlap should not log immunity first"

    pure_immunity = make_match("warlock", "paladin", seed=123)
    submit_turn(pure_immunity, "summon_imp", "divine_shield")
    immune_turn = _turn_lines(pure_immunity, 1)
    assert any("Imp casts Firebolt. Immune!" in line for line in immune_turn), "Pure immunity should still log Immune for Imp Firebolt"
    return True


def scenario_pet_specials_are_blocked_while_pet_is_ccd() -> bool:
    boar_match = make_match("hunter", "warrior", seed=123)
    hunter = boar_match.state[boar_match.players[0]]
    submit_turn(boar_match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should be active for pet CC coverage"
    effects.apply_effect_by_id(boar, "feared", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "raptor_strike_proc", overrides={"duration": 2})
    submit_turn(boar_match, "raptor_strike", _DEF_PASS)
    boar_turn = _turn_lines(boar_match, 2)
    assert any("Barrens Boar is feared and cannot act." in line for line in boar_turn), "Feared boar should report cannot-act"
    assert not any("Barrens Boar braces to intercept attacks." in line for line in boar_turn), "Feared boar should not execute its pre-action special"

    saber_match = make_match("hunter", "warrior", seed=123)
    saber_hunter = saber_match.state[saber_match.players[0]]
    submit_turn(saber_match, "call_saber", _DEF_PASS)
    saber = _active_pet(saber_hunter, "frostsaber")
    assert saber is not None, "Frostsaber should be active for forced-special CC coverage"
    effects.apply_effect_by_id(saber, "feared", overrides={"duration": 2})
    effects.apply_effect_by_id(saber_hunter, "raptor_strike_proc", overrides={"duration": 2})
    submit_turn(saber_match, "raptor_strike", _DEF_PASS)
    saber_turn = _turn_lines(saber_match, 2)
    assert any("Frostsaber is feared and cannot act." in line for line in saber_turn), "Feared companion should report cannot-act on forced special turns"
    assert not any("Frostsaber bites the target" in line for line in saber_turn), "Forced special path should not bypass pet CC"

    imp_match = make_match("warlock", "warrior", seed=123)
    submit_turn(imp_match, "summon_imp", _DEF_PASS)
    warlock = imp_match.state[imp_match.players[0]]
    imp = _active_pet(warlock, "imp")
    assert imp is not None, "Imp should be active for pet CC parity coverage"
    effects.apply_effect_by_id(imp, "feared", overrides={"duration": 2})
    submit_turn(imp_match, _DEF_PASS, _DEF_PASS)
    imp_turn = _turn_lines(imp_match, 2)
    assert any("Imp is feared and cannot act." in line for line in imp_turn), "Feared imp should not act"
    assert not any("Imp casts Firebolt" in line for line in imp_turn), "Feared imp should not cast Firebolt"

    control_match = make_match("hunter", "warrior", seed=123)
    control_hunter = control_match.state[control_match.players[0]]
    submit_turn(control_match, "call_saber", _DEF_PASS)
    effects.apply_effect_by_id(control_hunter, "raptor_strike_proc", overrides={"duration": 2})
    submit_turn(control_match, "raptor_strike", _DEF_PASS)
    control_turn = _turn_lines(control_match, 2)
    assert any("Frostsaber bites the target" in line for line in control_turn), "Non-CC pets should still execute valid specials"
    return True


def scenario_hunter_pet_recall_uses_calls_for_wording() -> bool:
    match = make_match("hunter", "warrior", seed=145)
    submit_turn(match, "call_boar", _DEF_PASS)
    submit_turn(match, "call_boar", _DEF_PASS)
    turn_two = _turn_lines(match, 2)
    assert any("calls for Barrens Boar." in line for line in turn_two), "Re-calling Barrens Boar should keep calls-for wording"
    assert not any("refreshes Barrens Boar." in line for line in turn_two), "Re-calling Barrens Boar should not say refreshes"
    return True


def scenario_pet_primary_resource_snapshot_contract() -> bool:
    hunter_match = make_match("hunter", "warrior", seed=151)
    submit_turn(hunter_match, "call_saber", _DEF_PASS)
    saber_snapshot = SOCKETS.snapshot_for(hunter_match, hunter_match.players[0])
    saber = next((pet for pet in saber_snapshot.get("you_pets", []) if pet.get("name") == "Frostsaber"), None)
    assert saber is not None, "Frostsaber should appear in snapshot payload"
    assert saber.get("primary_resource", {}).get("id") == "energy", "Frostsaber should expose Energy as pet primary resource"
    assert int(saber.get("primary_resource", {}).get("max", 0) or 0) > 0, "Frostsaber Energy max should be positive"

    submit_turn(hunter_match, "call_serpent", _DEF_PASS)
    serpent_snapshot = SOCKETS.snapshot_for(hunter_match, hunter_match.players[0])
    serpent = next((pet for pet in serpent_snapshot.get("you_pets", []) if pet.get("name") == "Emerald Serpent"), None)
    assert serpent is not None, "Emerald Serpent should appear in snapshot payload"
    assert serpent.get("primary_resource", {}).get("id") == "mp", "Emerald Serpent should expose Mana as pet primary resource"
    assert int(serpent.get("primary_resource", {}).get("max", 0) or 0) > 0, "Emerald Serpent Mana max should be positive"

    submit_turn(hunter_match, "call_boar", _DEF_PASS)
    boar_snapshot = SOCKETS.snapshot_for(hunter_match, hunter_match.players[0])
    boar = next((pet for pet in boar_snapshot.get("you_pets", []) if pet.get("name") == "Barrens Boar"), None)
    assert boar is not None, "Barrens Boar should appear in snapshot payload"
    assert boar.get("primary_resource", {}).get("id") == "rage", "Barrens Boar should expose Rage as pet primary resource"
    assert int(boar.get("primary_resource", {}).get("max", 0) or 0) > 0, "Barrens Boar Rage max should be positive"

    priest_match = make_match("priest", "warrior", seed=152)
    submit_turn(priest_match, "shadowfiend", _DEF_PASS)
    priest_snapshot = SOCKETS.snapshot_for(priest_match, priest_match.players[0])
    fiend = next((pet for pet in priest_snapshot.get("you_pets", []) if pet.get("name") == "Shadowfiend"), None)
    assert fiend is not None, "Shadowfiend should appear in snapshot payload"
    assert fiend.get("primary_resource") is None, "Shadowfiend should not expose a pet resource row payload"

    shaman_match = make_match("shaman", "warrior", seed=153)
    submit_turn(shaman_match, "mana_tide_totem", _DEF_PASS)
    submit_turn(shaman_match, "capacitor_totem", _DEF_PASS)
    shaman_snapshot = SOCKETS.snapshot_for(shaman_match, shaman_match.players[0])
    mana_tide = next((pet for pet in shaman_snapshot.get("you_pets", []) if pet.get("name") == "Mana Tide Totem"), None)
    capacitor = next((pet for pet in shaman_snapshot.get("you_pets", []) if pet.get("name") == "Capacitor Totem"), None)
    assert mana_tide is not None, "Mana Tide Totem should appear in snapshot payload"
    assert capacitor is not None, "Capacitor Totem should appear in snapshot payload"
    assert mana_tide.get("primary_resource") is None, "Mana Tide Totem should not expose a pet resource row payload"
    assert capacitor.get("primary_resource") is None, "Capacitor Totem should not expose a pet resource row payload"
    mana_tide_status_labels = [status.get("label") for status in mana_tide.get("statuses", []) if isinstance(status, dict)]
    assert not any(isinstance(label, str) and label.endswith("T") for label in mana_tide_status_labels), "Mana Tide Totem should not expose XT duration status text"

    mixed_match = make_match("hunter", "shaman", seed=154)
    submit_turn(mixed_match, "call_saber", "mana_tide_totem")
    mixed_snapshot = SOCKETS.snapshot_for(mixed_match, mixed_match.players[0])
    assert len(mixed_snapshot.get("you_pets", [])) == 1 and len(mixed_snapshot.get("enemy_pets", [])) == 1, "Snapshot should remain stable with pets on both sides"
    assert mixed_snapshot["you_pets"][0].get("primary_resource", {}).get("id") == "energy", "Friendly pet resource should remain intact in mixed-pet snapshots"
    assert mixed_snapshot["enemy_pets"][0].get("primary_resource") is None, "Enemy totem should remain resource-less in mixed-pet snapshots"
    return True


def scenario_imp_firebolt_mana_cost_is_three() -> bool:
    match = make_match("warlock", "warrior", seed=992)
    submit_turn(match, "summon_imp", _DEF_PASS)
    warlock = match.state[match.players[0]]
    imp = _active_pet(warlock, "imp")
    assert imp is not None, "Imp should be active for Firebolt mana-cost validation"
    assert imp.mp == 9, "Imp Firebolt should cost 3 mana per cast and passively regen 2 mana per turn"
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert imp.mp == 8, "Imp should continue regening 2 mana per turn while spending 3 mana per Firebolt"
    return True


def scenario_pet_incoming_physical_uses_centralized_mitigation_phase3() -> bool:
    low_def_taken = _redirected_boar_damage_taken("rogue", "shadowstrike", boar_def=0, boar_magic_resist=0, seed=4101)
    high_def_taken = _redirected_boar_damage_taken("rogue", "shadowstrike", boar_def=80, boar_magic_resist=0, seed=4101)
    assert low_def_taken > 0, "Physical redirect setup should deal damage to boar"
    assert high_def_taken < low_def_taken, "Higher boar DEF should reduce redirected physical incoming damage"
    return True


def scenario_pet_incoming_magical_uses_centralized_resist_phase3() -> bool:
    low_mr_taken = _redirected_boar_damage_taken("mage", "fireball", boar_def=0, boar_magic_resist=0, seed=4102)
    high_mr_taken = _redirected_boar_damage_taken("mage", "fireball", boar_def=0, boar_magic_resist=80, seed=4102)
    assert low_mr_taken > 0, "Magical redirect setup should deal damage to boar"
    assert high_mr_taken < low_mr_taken, "Higher boar magic_resist should reduce redirected magical incoming damage"
    return True


def scenario_imp_and_shadowfiend_incoming_use_centralized_pet_mitigation_phase3() -> bool:
    imp_match = make_match("warlock", "warrior", seed=4103)
    submit_turn(imp_match, "summon_imp", _DEF_PASS)
    imp_owner_sid, _ = imp_match.players
    imp = next((pet for pet in imp_match.state[imp_owner_sid].pets.values() if pet.template_id == "imp"), None)
    assert imp is not None, "Imp should summon for centralized incoming mitigation test"
    imp.stats["def"] = 80
    hp_before_imp = imp.hp
    submit_turn(imp_match, _DEF_PASS, "dragon_roar")
    assert imp.hp > hp_before_imp - 20, "High DEF imp should mitigate incoming physical AoE damage"

    fiend_match = make_match("priest", "shaman", seed=4104)
    submit_turn(fiend_match, "shadowfiend", _DEF_PASS)
    fiend_owner_sid, _ = fiend_match.players
    fiend = next((pet for pet in fiend_match.state[fiend_owner_sid].pets.values() if pet.template_id == "shadowfiend"), None)
    assert fiend is not None, "Shadowfiend should summon for centralized incoming resist test"
    fiend.stats["magic_resist"] = 80
    hp_before_fiend = fiend.hp
    submit_turn(fiend_match, _DEF_PASS, "chain_lightning")
    assert fiend.hp > hp_before_fiend - 20, "High magic_resist shadowfiend should mitigate incoming magical AoE damage"
    return True


def scenario_pet_totem_default_magic_resist_zero_phase3() -> bool:
    assert int(PETS["frostsaber"]["stats"].get("magic_resist", -1)) == 0, "Hunter pets should default magic_resist to 0"
    assert int(PETS["imp"]["stats"].get("magic_resist", -1)) == 0, "Imp should default magic_resist to 0"
    assert int(PETS["mana_tide_totem"]["stats"].get("magic_resist", -1)) == 0, "Totems should default magic_resist to 0"
    return True


def scenario_entity_type_phase3_validation_suite() -> bool:
    for class_id in CLASSES.keys():
        mirror_match = make_match(class_id, class_id, seed=7000 + len(class_id))
        for sid in mirror_match.players:
            assert mirror_match.state[sid].entity_type == "humanoid", f"{class_id} should initialize as humanoid"

    match = make_match("warlock", "hunter", seed=7001)
    p1_sid, p2_sid = match.players
    p1 = match.state[p1_sid]
    p2 = match.state[p2_sid]
    expected_pet_types = {
        "imp": "demon",
        "shadowfiend": "demon",
        "frostsaber": "beast",
        "barrens_boar": "beast",
        "emerald_serpent": "beast",
    }
    for pet_id, entity_type in expected_pet_types.items():
        assert PETS[pet_id].get("entity_type") == entity_type, f"{pet_id} should declare entity_type={entity_type}"

    submit_turn(match, "summon_imp", _DEF_PASS)
    imp = next(iter(p1.pets.values()))
    assert imp.entity_type == "demon", "Summoned Imp runtime state should preserve demon entity_type"
    assert resolver.entity_type_of(imp) == "demon", "entity_type_of should normalize summoned pet runtime entity type"
    assert resolver.is_entity_type(imp, "Demon"), "is_entity_type should support normalized comparisons"

    priest_match = make_match("priest", "warrior", seed=7002)
    priest_sid = priest_match.players[0]
    submit_turn(priest_match, "shadowfiend", _DEF_PASS)
    shadowfiend = next(iter(priest_match.state[priest_sid].pets.values()))
    assert shadowfiend.entity_type == "demon", "Summoned Shadowfiend runtime state should preserve demon entity_type"

    hunter_match = make_match("hunter", "warrior", seed=7003)
    hunter_sid = hunter_match.players[0]
    submit_turn(hunter_match, "call_saber", _DEF_PASS)
    frostsaber = next(iter(hunter_match.state[hunter_sid].pets.values()))
    assert frostsaber.entity_type == "beast", "Summoned Frostsaber runtime state should preserve beast entity_type"
    assert resolver.entity_type_of(hunter_match.state[hunter_sid]) == "humanoid", "Champions should expose entity_type helper data"

    submit_turn(hunter_match, "call_boar", _DEF_PASS)
    boar = next((pet for pet in hunter_match.state[hunter_sid].pets.values() if pet.template_id == "barrens_boar"), None)
    assert boar is not None and boar.entity_type == "beast", "Summoned Barrens Boar runtime state should preserve beast entity_type"
    submit_turn(hunter_match, "call_serpent", _DEF_PASS)
    serpent = next((pet for pet in hunter_match.state[hunter_sid].pets.values() if pet.template_id == "emerald_serpent"), None)
    assert serpent is not None and serpent.entity_type == "beast", "Summoned Emerald Serpent runtime state should preserve beast entity_type"

    control_match = make_match("warrior", "warrior", seed=7004)
    mutated_match = make_match("warrior", "warrior", seed=7004)
    control_target = control_match.state[control_match.players[1]]
    mutated_target = mutated_match.state[mutated_match.players[1]]
    submit_turn(control_match, "overpower", _DEF_PASS)
    mutated_match.state[mutated_match.players[0]].entity_type = "demon"
    mutated_match.state[mutated_match.players[1]].entity_type = "beast"
    submit_turn(mutated_match, "overpower", _DEF_PASS)
    assert control_target.res.hp == mutated_target.res.hp, "Entity type metadata should not alter gameplay in Phase 1"

    snap_match = make_match("warlock", "hunter", seed=7005)
    submit_turn(snap_match, "summon_imp", "call_saber")
    p1_snapshot = SOCKETS.snapshot_for(snap_match, p1_sid)
    p2_snapshot = SOCKETS.snapshot_for(snap_match, p2_sid)
    for snapshot in (p1_snapshot, p2_snapshot):
        assert isinstance(snapshot.get("you_entity_type"), str) and snapshot["you_entity_type"], "Friendly champion snapshot entity_type must be present"
        assert isinstance(snapshot.get("enemy_entity_type"), str) and snapshot["enemy_entity_type"], "Enemy champion snapshot entity_type must be present"
        for pet in snapshot.get("you_pets", []) + snapshot.get("enemy_pets", []):
            assert isinstance(pet.get("entity_type"), str) and pet["entity_type"], f"Pet snapshot entity_type missing for {pet.get('name')}"
            assert pet["entity_type"] == pet["entity_type"].strip().lower(), f"Pet snapshot entity_type should be normalized for {pet.get('name')}"

    p1_imp = next((pet for pet in p1_snapshot.get("you_pets", []) if pet.get("name") == "Imp"), None)
    p1_enemy_saber = next((pet for pet in p1_snapshot.get("enemy_pets", []) if pet.get("name") == "Frostsaber"), None)
    assert p1_snapshot.get("you_entity_type") == "humanoid", "Friendly champion snapshot should expose humanoid entity_type"
    assert p1_snapshot.get("enemy_entity_type") == "humanoid", "Enemy champion snapshot should expose humanoid entity_type"
    assert p2_snapshot.get("you_entity_type") == "humanoid", "Viewer-relative champion entity_type should stay humanoid"
    assert p1_imp is not None and p1_imp.get("entity_type") == "demon", "Friendly pet snapshot should expose Imp as demon"
    assert p1_enemy_saber is not None and p1_enemy_saber.get("entity_type") == "beast", "Enemy pet snapshot should expose Frostsaber as beast"
    return True


def scenario_entity_type_phase3_completeness_audit() -> bool:
    audit_match = make_match("hunter", "priest", seed=7006)
    hunter_sid, priest_sid = audit_match.players
    hunter = audit_match.state[hunter_sid]
    priest = audit_match.state[priest_sid]
    assert hunter.entity_type and priest.entity_type, "Every champion runtime should have entity_type"
    assert all(ps.entity_type == "humanoid" for ps in audit_match.state.values()), "Current champion roster should be humanoid"

    submit_turn(audit_match, "call_saber", "shadowfiend")
    runtime_pets = list(hunter.pets.values()) + list(priest.pets.values())
    assert runtime_pets, "Audit should have runtime pets available"
    assert all(pet.entity_type for pet in runtime_pets), "Every summoned runtime pet should have entity_type"
    assert all(resolver.entity_type_of(pet) in {"demon", "beast"} for pet in runtime_pets), "Helper access should cover current runtime pet roster"

    viewer_snapshot = SOCKETS.snapshot_for(audit_match, hunter_sid)
    assert viewer_snapshot.get("you_entity_type") == "humanoid", "Snapshot/debug surface should expose champion entity_type"
    assert viewer_snapshot.get("enemy_entity_type") == "humanoid", "Snapshot/debug surface should expose enemy entity_type"
    for pet in viewer_snapshot.get("you_pets", []) + viewer_snapshot.get("enemy_pets", []):
        assert pet.get("entity_type") in {"demon", "beast"}, "Snapshot/debug surface should expose runtime pet entity_type"
    return True


def scenario_shadowfiend_summon_log_deduped() -> bool:
    match = make_match("priest", "warrior", seed=6105)
    submit_turn(match, "shadowfiend", _DEF_PASS)
    latest_turn = _turn_lines(match, 1)
    cast_line = next((line for line in latest_turn if "cast Shadowfiend." in line), "")
    assert cast_line.count("summons a Shadowfiend.") == 1, "Shadowfiend cast should log a single summon sentence"
    assert "summons Shadowfiend." not in cast_line, "Legacy duplicate summon wording should not appear"
    return True


def scenario_shaman_totems_and_astral_explosion() -> bool:
    match = make_match("shaman", "warlock", seed=7005)
    shaman_sid, warlock_sid = match.players
    shaman = match.state[shaman_sid]
    warlock = match.state[warlock_sid]

    shaman.res.mp = 0
    submit_turn(match, "mana_tide_totem", "summon_imp")
    mp_after_summon = shaman.res.mp
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert shaman.res.mp > mp_after_summon, "Mana Tide Totem should restore mana while alive"

    shaman.res.mp = max(shaman.res.mp, 30)
    submit_turn(match, "capacitor_totem", _DEF_PASS)
    assert any(p.template_id == "capacitor_totem" for p in match.state[shaman_sid].pets.values()), "Capacitor Totem should be summoned"
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert any("discharges!" in line for line in _turn_lines(match, 4)), "Capacitor Totem should discharge after a one-turn delay"
    assert not any(p.template_id == "capacitor_totem" for p in match.state[shaman_sid].pets.values()), "Capacitor Totem should disappear after discharging"

    shaman.res.mp = max(shaman.res.mp, 100)
    shaman.res.hp = max(1, int(shaman.res.hp_max * 0.4))
    submit_turn(match, "astral_shift", _DEF_PASS)
    warlock_hp_before = warlock.res.hp
    imp_ids = sorted(warlock.pets.keys())
    assert imp_ids, "Warlock should have at least one pet target for Astral Explosion"
    imp_hp_before = {pid: warlock.pets[pid].hp for pid in imp_ids}
    submit_turn(match, "astral_explosion", _DEF_PASS)
    imp_hp_after = {pid: warlock.pets[pid].hp for pid in imp_ids if pid in warlock.pets}
    assert warlock.res.hp == warlock_hp_before, "Astral Explosion should not damage the enemy champion"
    assert any(imp_hp_after.get(pid, 0) < hp for pid, hp in imp_hp_before.items()), "Astral Explosion should damage enemy pets"
    assert effects.absorb_total(shaman) == 0, "Astral Explosion should consume all absorb when valid targets exist"

    # Assert on the logged incoming damage rather than HP lost: the Imp only has a
    # few HP and is overkilled in both stances, so before/after HP would be
    # identical and hide the Might/Wrath difference. Shaman has 100 max mana, so
    # derive Might/Wrath mana from the pool instead of hard-coding 41/40 (both Wrath).
    def challenger_astral_pet_damage(*, shaman_mp: int) -> int:
        explosion_match = make_match("shaman", "warlock", p1_items={"armor": "challengers_chestplate"}, seed=7007)
        explosion_shaman_sid, explosion_warlock_sid = explosion_match.players
        explosion_shaman = explosion_match.state[explosion_shaman_sid]
        explosion_warlock = explosion_match.state[explosion_warlock_sid]
        submit_turn(explosion_match, _DEF_PASS, "summon_imp")
        explosion_shaman.res.mp = shaman_mp
        effects.add_absorb(explosion_shaman, 30, source_name="Test Absorb", effect_id="test_absorb")
        assert sorted(explosion_warlock.pets.keys()), "Astral Explosion Challenger coverage needs an enemy pet"
        line_start = len(explosion_match.log)
        submit_turn(explosion_match, "astral_explosion", _DEF_PASS)
        hit_line = next(
            (line for line in explosion_match.log[line_start:] if "Astral Explosion hits" in line and "Imp for" in line),
            None,
        )
        assert hit_line is not None, "Astral Explosion should log an incoming damage line against the enemy Imp"
        parsed = re.search(r"for (\d+) damage", hit_line)
        assert parsed is not None, "Astral Explosion pet hit line should report the incoming damage"
        return int(parsed.group(1))

    shaman_mp_max = make_match("shaman", "warlock", seed=7007).state["p1_sid"].res.mp_max
    astral_might_damage = challenger_astral_pet_damage(shaman_mp=shaman_mp_max // 2 + 1)
    astral_wrath_damage = challenger_astral_pet_damage(shaman_mp=shaman_mp_max // 2)
    assert astral_might_damage > astral_wrath_damage, "Astral Explosion pet damage should use the actor's action-time Challenger outgoing snapshot"
    return True


def scenario_capacitor_totem_aoe_timing_and_duration() -> bool:
    match = make_match("shaman", "warlock", seed=7012)
    shaman_sid, warlock_sid = match.players
    shaman = match.state[shaman_sid]
    warlock = match.state[warlock_sid]

    submit_turn(match, "capacitor_totem", "summon_imp")
    assert not effects.has_effect(warlock, "capacitor_totem_stun"), "Capacitor should not stun on summon turn"
    imp_ids = sorted(warlock.pets.keys())
    assert imp_ids, "Warlock should have pet targets for Capacitor AoE stun"
    assert all(not effects.has_effect(warlock.pets[pid], "capacitor_totem_stun") for pid in imp_ids), "Capacitor should not stun enemy pets on summon turn"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert any("discharges!" in line for line in _turn_lines(match, 2)), "Capacitor should discharge after one-turn delay"
    assert not any(p.template_id == "capacitor_totem" for p in match.state[shaman_sid].pets.values()), "Capacitor Totem should disappear after discharging"

    hp_before_t7 = warlock.res.hp
    imp_hp_before_t7 = {pid: warlock.pets[pid].hp for pid in imp_ids if pid in warlock.pets}
    submit_turn(match, _DEF_PASS, "shadow_bolt")
    assert warlock.res.hp == hp_before_t7, "Enemy champion should lose first action window while stunned"
    for pid, hp in imp_hp_before_t7.items():
        if pid in warlock.pets:
            assert warlock.pets[pid].hp == hp, "Enemy pets should lose first action window while stunned"

    hp_before_t8 = warlock.res.hp
    imp_hp_before_t8 = {pid: warlock.pets[pid].hp for pid in imp_ids if pid in warlock.pets}
    submit_turn(match, _DEF_PASS, "shadow_bolt")
    assert warlock.res.hp == hp_before_t8, "Enemy champion should lose second action window while stunned"
    for pid, hp in imp_hp_before_t8.items():
        if pid in warlock.pets:
            assert warlock.pets[pid].hp == hp, "Enemy pets should lose second action window while stunned"

    submit_turn(match, _DEF_PASS, "shadow_bolt")
    assert not effects.has_effect(warlock, "capacitor_totem_stun"), "Enemy champion stun should expire after two denied action windows"

    mana_match = make_match("shaman", "warrior", seed=7013)
    mana_shaman_sid, _ = mana_match.players
    mana_shaman = mana_match.state[mana_shaman_sid]
    mana_shaman.res.mp = 0
    submit_turn(mana_match, "mana_tide_totem", _DEF_PASS)
    mana_before = mana_shaman.res.mp
    submit_turn(mana_match, _DEF_PASS, _DEF_PASS)
    assert mana_shaman.res.mp > mana_before, "Mana Tide Totem behavior should remain unchanged"
    return True


def scenario_shaman_astral_explosion_no_pet_consumes_absorb() -> bool:
    match = make_match("shaman", "warrior", seed=7006)
    shaman_sid, _ = match.players
    shaman = match.state[shaman_sid]
    shaman.res.hp = max(1, int(shaman.res.hp_max * 0.4))
    submit_turn(match, "astral_shift", _DEF_PASS)
    submit_turn(match, "astral_explosion", _DEF_PASS)
    assert effects.absorb_total(shaman) == 0, "Astral Explosion should consume absorb even when no enemy pets are present"
    return True


def scenario_high_risk_pet_legality_and_protection_pack() -> bool:
    feared_pet_match = make_match("priest", "warrior", seed=8401)
    submit_turn(feared_pet_match, "shadowfiend", _DEF_PASS)
    shadowfiend = next(iter(feared_pet_match.state[feared_pet_match.players[0]].pets.values()))
    effects.apply_effect_by_id(shadowfiend, "feared", overrides={"duration": 1})
    warrior_hp_before = feared_pet_match.state[feared_pet_match.players[1]].res.hp
    submit_turn(feared_pet_match, _DEF_PASS, _DEF_PASS)
    feared_turn = _turn_lines(feared_pet_match, 2)
    assert any("Shadowfiend is feared and cannot act." in line for line in feared_turn), "Feared pets should be denied from basic attacks"
    assert feared_pet_match.state[feared_pet_match.players[1]].res.hp == warrior_hp_before, "Denied feared pet action should not deal damage"

    feared_special_match = make_match("hunter", "warrior", seed=8402)
    submit_turn(feared_special_match, "call_saber", _DEF_PASS)
    hunter = feared_special_match.state[feared_special_match.players[0]]
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Frostsaber should be active for forced special coverage"
    hunter.pending_pet_command = "special"
    effects.apply_effect_by_id(saber, "feared", overrides={"duration": 1})
    submit_turn(feared_special_match, _DEF_PASS, _DEF_PASS)
    assert any("Frostsaber is feared and cannot act." in line for line in _turn_lines(feared_special_match, 2)), "Feared pets should be denied from specials too"

    frozen_pet_match = make_match("priest", "warrior", seed=8403)
    submit_turn(frozen_pet_match, "shadowfiend", _DEF_PASS)
    frozen_pet = next(iter(frozen_pet_match.state[frozen_pet_match.players[0]].pets.values()))
    effects.apply_effect_by_id(frozen_pet, "ring_of_ice_freeze", overrides={"duration": 1, "source_ability_name": "Ring of Ice"})
    submit_turn(frozen_pet_match, _DEF_PASS, _DEF_PASS)
    assert any("Shadowfiend is frozen and cannot act." in line for line in _turn_lines(frozen_pet_match, 2)), "Frozen/stunned pets should not act"

    for protection_ability, expected_fragment in (("divine_shield", None), ("vanish", "Target is stealthed — Miss!"), ("blink", "Target blinks away"), ("turtle", "Target evades the attack — Miss!")):
        protection_match = make_match("priest", "paladin" if protection_ability == "divine_shield" else ("rogue" if protection_ability == "vanish" else ("mage" if protection_ability == "blink" else "hunter")), seed=8404)
        submit_turn(protection_match, "shadowfiend", protection_ability)
        enemy_hp_before = protection_match.state[protection_match.players[1]].res.hp
        submit_turn(protection_match, _DEF_PASS, _DEF_PASS)
        turn_two_lines = _turn_lines(protection_match, 2)
        if expected_fragment is not None:
            assert any(expected_fragment in line for line in turn_two_lines), f"Pet attacks should respect {protection_ability} target protection/miss rules"
        assert protection_match.state[protection_match.players[1]].res.hp == enemy_hp_before, f"Pet attacks should not damage targets protected by {protection_ability}"
    return True


def scenario_step2_pet_legality_and_protection_contracts() -> bool:
    feared = make_match("priest", "warrior", seed=8631)
    submit_turn(feared, "shadowfiend", _DEF_PASS)
    fiend = next(iter(feared.state[feared.players[0]].pets.values()))
    effects.apply_effect_by_id(fiend, "feared", overrides={"duration": 1})
    hp_before = feared.state[feared.players[1]].res.hp
    submit_turn(feared, _DEF_PASS, _DEF_PASS)
    assert any("Shadowfiend is feared and cannot act." in line for line in _turn_lines(feared, 2)), "Feared pets should be unable to act"
    assert feared.state[feared.players[1]].res.hp == hp_before, "Feared pets should not deal damage"

    frozen = make_match("priest", "warrior", seed=8632)
    submit_turn(frozen, "shadowfiend", _DEF_PASS)
    frozen_fiend = next(iter(frozen.state[frozen.players[0]].pets.values()))
    effects.apply_effect_by_id(frozen_fiend, "ring_of_ice_freeze", overrides={"duration": 1, "source_ability_name": "Ring of Ice"})
    submit_turn(frozen, _DEF_PASS, _DEF_PASS)
    assert any("Shadowfiend is frozen and cannot act." in line for line in _turn_lines(frozen, 2)), "Frozen/stunned pets should be unable to act"

    stealth_match = make_match("priest", "rogue", seed=8633)
    submit_turn(stealth_match, "shadowfiend", "vanish")
    hp_before = stealth_match.state[stealth_match.players[1]].res.hp
    submit_turn(stealth_match, _DEF_PASS, _DEF_PASS)
    stealth_turn = _turn_lines(stealth_match, 2)
    assert any("Shadowfiend melees the target." in line and "Target is stealthed — Miss!" in line for line in stealth_turn), "Pet melee should respect stealth miss behavior"
    assert stealth_match.state[stealth_match.players[1]].res.hp == hp_before, "Pet melee should not damage stealthed targets"

    blink_match = make_match("priest", "mage", seed=8634)
    submit_turn(blink_match, "shadowfiend", "blink")
    hp_before = blink_match.state[blink_match.players[1]].res.hp
    submit_turn(blink_match, _DEF_PASS, _DEF_PASS)
    blink_turn = _turn_lines(blink_match, 2)
    assert any("Shadowfiend melees the target." in line and "Target blinks away — Miss." in line for line in blink_turn), "Pet melee should respect blink-like miss behavior"
    assert blink_match.state[blink_match.players[1]].res.hp == hp_before, "Pet melee should not damage blink-protected targets"

    turtle_match = make_match("priest", "hunter", seed=8635)
    submit_turn(turtle_match, "shadowfiend", "turtle")
    hp_before = turtle_match.state[turtle_match.players[1]].res.hp
    submit_turn(turtle_match, _DEF_PASS, _DEF_PASS)
    turtle_turn = _turn_lines(turtle_match, 2)
    assert any("Shadowfiend melees the target." in line and "Target evades the attack — Miss!" in line for line in turtle_turn), "Pet melee should respect Aspect of the Turtle protection"
    assert turtle_match.state[turtle_match.players[1]].res.hp == hp_before, "Pet melee should not damage Turtle-protected targets"

    summon_spell_path = make_match("mage", "warlock", seed=8636)
    submit_turn(summon_spell_path, "blink", "summon_imp")
    turn_one = _turn_lines(summon_spell_path, 1)
    assert any("Imp casts Firebolt" in line and "Target blinks away — Miss." in line for line in turn_one), "Newly summoned pets should immediately respect current target protection state"
    return True


def scenario_phase0_pet_legality_and_protection_contract_lock() -> bool:
    feared_pet = make_match("priest", "warrior", seed=9041)
    submit_turn(feared_pet, "shadowfiend", _DEF_PASS)
    fiend = next(iter(feared_pet.state[feared_pet.players[0]].pets.values()))
    effects.apply_effect_by_id(fiend, "feared", overrides={"duration": 1})
    hp_before = feared_pet.state[feared_pet.players[1]].res.hp
    submit_turn(feared_pet, _DEF_PASS, _DEF_PASS)
    assert any("Shadowfiend is feared and cannot act." in line for line in _turn_lines(feared_pet, 2)), "Feared pet should be denied from acting"
    assert feared_pet.state[feared_pet.players[1]].res.hp == hp_before, "Denied feared pet action should not deal damage"

    melee_protect = make_match("priest", "hunter", seed=9042)
    submit_turn(melee_protect, "shadowfiend", "turtle")
    hp_before = melee_protect.state[melee_protect.players[1]].res.hp
    submit_turn(melee_protect, _DEF_PASS, _DEF_PASS)
    melee_turn = _turn_lines(melee_protect, 2)
    assert any("Shadowfiend melees the target." in line and "Target evades the attack — Miss!" in line for line in melee_turn), "Pet melee path should respect Turtle protection"
    assert melee_protect.state[melee_protect.players[1]].res.hp == hp_before, "Pet melee should not bypass Turtle protection"

    spell_protect = make_match("hunter", "warlock", seed=9043)
    submit_turn(spell_protect, "turtle", "summon_imp")
    spell_turn = _turn_lines(spell_protect, 1)
    assert any("Imp casts Firebolt" in line and "Target evades the attack — Miss!" in line for line in spell_turn), "Pet spell path should respect Turtle protection immediately"

    summon_immediate = make_match("mage", "warlock", seed=9044)
    submit_turn(summon_immediate, "blink", "summon_imp")
    assert any("Imp casts Firebolt" in line and "Target blinks away — Miss." in line for line in _turn_lines(summon_immediate, 1)), "Newly summoned pet should immediately respect current target protections"
    return True


def scenario_pet_hot_tick_credits_owner_pet_healing_bucket() -> bool:
    """Pet HoT/regen ticks are pet-produced healing: they land once in the
    owner's pet_healing/pet_overhealing buckets and never in player healing.
    """
    match = make_match("hunter", "warrior", seed=9301)
    hunter_sid, _ = match.players
    hunter = match.state[hunter_sid]
    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Setup: Frostsaber should be active"
    saber.hp = saber.hp_max - 2
    saber.effects.append({"id": "mend_pet_test", "name": "Mend Pet", "category": "hot", "regen": {"hp": 6}, "duration": 3})
    totals_before = dict(match.combat_totals[hunter_sid])

    submit_turn(match, _DEF_PASS, _DEF_PASS)

    totals = match.combat_totals[hunter_sid]
    assert saber.hp == saber.hp_max, "The 6-HP tick should cap the pet at hp_max"
    assert any("Frostsaber recovers 2 HP from Mend Pet." in line for line in _turn_lines(match, 2)), "The pet HoT log must report the actual gained 2 HP"
    assert totals["pet_healing"] == totals_before["pet_healing"] + 2, "Pet HoT healing must credit pet_healing exactly once"
    assert totals["pet_overhealing"] == totals_before["pet_overhealing"] + 4, "The requested regen lost to the pet's hp_max cap must land in pet_overhealing"
    assert totals["healing"] == totals_before["healing"], "Pet HoT healing must not roll into the owner's regular healing"
    assert totals["overhealing"] == totals_before["overhealing"], "Pet overhealing must stay separate from player overhealing"
    return True


def scenario_pet_attack_logs_on_miss_and_immune_consistently() -> bool:
    original_boar_chance = PETS["barrens_boar"]["special_chance"]
    original_saber_chance = PETS["frostsaber"]["special_chance"]
    original_serpent_chance = PETS["emerald_serpent"]["special_chance"]
    PETS["barrens_boar"]["special_chance"] = 0.0
    PETS["frostsaber"]["special_chance"] = 0.0
    PETS["emerald_serpent"]["special_chance"] = 0.0
    try:
        boar_immune = make_match("hunter", "mage", seed=9061)
        submit_turn(boar_immune, "call_boar", "iceblock")
        submit_turn(boar_immune, _DEF_PASS, _DEF_PASS)
        boar_immune_turn = _turn_lines(boar_immune, 2)
        assert any("Barrens Boar melees the target. Immune!" in line for line in boar_immune_turn), "Barrens Boar should keep a readable action log when champion target is immune"

        boar_miss = make_match("hunter", "rogue", seed=9062)
        submit_turn(boar_miss, "call_boar", "vanish")
        submit_turn(boar_miss, _DEF_PASS, _DEF_PASS)
        assert any("Barrens Boar melees the target." in line and "Target is stealthed — Miss!" in line for line in _turn_lines(boar_miss, 2)), "Barrens Boar should keep action text on miss outcomes"

        saber_immune = make_match("hunter", "mage", seed=9063)
        submit_turn(saber_immune, "call_saber", "iceblock")
        hunter = saber_immune.state[saber_immune.players[0]]
        saber = _active_pet(hunter, "frostsaber")
        assert saber is not None, "Frostsaber should be active for forced-special immunity logging"
        saber.energy = 20
        hunter.pending_pet_command = "special"
        submit_turn(saber_immune, _DEF_PASS, _DEF_PASS)
        assert any("Frostsaber bites the target. Immune!" in line for line in _turn_lines(saber_immune, 2)), "Frostsaber special should log immune outcomes with action text"

        serpent_immune = make_match("hunter", "rogue", seed=9064)
        effects.remove_effect(serpent_immune.state[serpent_immune.players[1]], "stealth")
        submit_turn(serpent_immune, "call_serpent", "cloak")
        serpent_owner = serpent_immune.state[serpent_immune.players[0]]
        serpent = _active_pet(serpent_owner, "emerald_serpent")
        assert serpent is not None, "Emerald Serpent should be active for forced-special immunity logging"
        serpent_owner.pending_pet_command = "special"
        submit_turn(serpent_immune, _DEF_PASS, _DEF_PASS)
        assert any("Emerald Serpent breathes lightning. Immune!" in line for line in _turn_lines(serpent_immune, 2)), "Emerald Serpent special should log cloak/magic immunity outcomes"

        fiend_immune = make_match("priest", "paladin", seed=9065)
        submit_turn(fiend_immune, "shadowfiend", "divine_shield")
        submit_turn(fiend_immune, _DEF_PASS, _DEF_PASS)
        assert any("Shadowfiend melees the target. Immune!" in line for line in _turn_lines(fiend_immune, 2)), "Shadowfiend should keep melee action logs on immune outcomes"

        imp_miss = make_match("mage", "warlock", seed=9066)
        submit_turn(imp_miss, "blink", "summon_imp")
        assert any("Imp casts Firebolt" in line and "Target blinks away — Miss." in line for line in _turn_lines(imp_miss, 1)), "Imp should keep casting action logs on miss outcomes"
    finally:
        PETS["barrens_boar"]["special_chance"] = original_boar_chance
        PETS["frostsaber"]["special_chance"] = original_saber_chance
        PETS["emerald_serpent"]["special_chance"] = original_serpent_chance
    return True
