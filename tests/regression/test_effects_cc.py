"""Status effect and crowd-control regression scenarios (stealth, stuns, freezes, fear, immunity, break-on-damage, durations).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import random

from typing import Any

from harness import (
    ABILITIES,
    SOCKETS,
    _BREAK_ON_DAMAGE_CC_CASES,
    _assert_no_stun_effect,
    _has_effect,
    _player_states,
    _turn_lines,
    effects,
    make_match,
    run_turns,
    submit_turn,
)

from .helpers import (
    _DEF_PASS,
    _add_pet,
    _pet_took_damage_or_died,
    _setup_imps,
    _active_pet,
)


def scenario_cloak_of_shadows_interactions() -> bool:
    # Ring of Ice blocked.
    ring_match = make_match("rogue", "mage", seed=123)
    submit_turn(ring_match, "cloak", "ring_of_ice")
    rogue = ring_match.state[ring_match.players[0]]
    assert _has_effect(rogue, "cloak_of_shadows"), "Cloak should be active"
    assert not _has_effect(rogue, "ring_of_ice_freeze"), "Ring of Ice should be blocked by Cloak"

    # Unstable Affliction blocked on apply and does not tick later.
    ua_match = make_match("rogue", "warlock", seed=123)
    submit_turn(ua_match, "cloak", "unstable_affliction")
    rogue_ua = ua_match.state[ua_match.players[0]]
    hp_after_apply = rogue_ua.res.hp
    assert not _has_effect(rogue_ua, "unstable_affliction"), "UA should not apply through Cloak"
    run_turns(ua_match, [(_DEF_PASS, _DEF_PASS), (_DEF_PASS, _DEF_PASS)])
    assert ua_match.state[ua_match.players[0]].res.hp == hp_after_apply, "UA ticks should not occur after blocked apply"

    # Shield of Vengeance explosion blocked.
    sov_match = make_match("rogue", "paladin", seed=123)
    rogue_sov = sov_match.state[sov_match.players[0]]
    pal = sov_match.state[sov_match.players[1]]
    effects.apply_effect_by_id(rogue_sov, "cloak_of_shadows", overrides={"duration": 2})
    effects.apply_effect_by_id(pal, "shield_of_vengeance", overrides={"duration": 1, "absorbed": 30})
    hp_before = rogue_sov.res.hp
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    assert sov_match.state[sov_match.players[0]].res.hp == hp_before, "SoV explosion should be blocked by Cloak"

    # Friendly magical buffs/heals are not blocked.
    friendly_match = make_match("paladin", "warrior", seed=123)
    pal_friendly = friendly_match.state[friendly_match.players[0]]
    effects.apply_effect_by_id(pal_friendly, "cloak_of_shadows", overrides={"duration": 2})
    pal_friendly.res.hp = max(1, pal_friendly.res.hp - 25)
    submit_turn(friendly_match, "holy_light", _DEF_PASS)
    assert pal_friendly.res.hp > 0 and pal_friendly.res.hp <= pal_friendly.res.hp_max
    assert pal_friendly.res.hp > (pal_friendly.res.hp_max - 25), "Friendly heal should still work under Cloak"
    effects.apply_effect_by_id(pal_friendly, "ice_barrier", overrides={"duration": 2})
    assert _has_effect(pal_friendly, "ice_barrier"), "Friendly magical absorb should apply while Cloak is active"

    return True


def scenario_stealth_priority_over_stun() -> bool:
    match = make_match("rogue", "paladin", seed=123)
    submit_turn(match, "vanish", "hammer_of_justice")
    rogue = match.state[match.players[0]]
    assert _has_effect(rogue, "stealth"), "Stealth should register"
    assert not _has_effect(rogue, "stunned"), "Stun should miss stealthed target by current rule"
    return True


def scenario_immunity_priority_over_stuns() -> bool:
    pal_match = make_match("paladin", "paladin", seed=123)
    submit_turn(pal_match, "hammer_of_justice", "divine_shield")
    _, pal_target = _player_states(pal_match)
    assert _has_effect(pal_target, "divine_shield"), "Divine Shield should apply first"
    _assert_no_stun_effect(pal_target)

    rogue_mage = make_match("rogue", "mage", seed=123)
    submit_turn(rogue_mage, "kidney_shot", "iceblock")
    _, mage = _player_states(rogue_mage)
    assert _has_effect(mage, "iceblock"), "Ice Block should apply first"
    _assert_no_stun_effect(mage)
    return True


def scenario_stealth_priority_over_stuns_expanded() -> bool:
    druid_rogue = make_match("druid", "rogue", seed=123)
    submit_turn(druid_rogue, "maim", "vanish")
    _, rogue = _player_states(druid_rogue)
    assert _has_effect(rogue, "stealth"), "Vanish stealth should apply"
    _assert_no_stun_effect(rogue)

    rogue_druid = make_match("rogue", "druid", seed=123)
    _, druid = _player_states(rogue_druid)
    effects.apply_effect_by_id(druid, "cat_form", overrides={"duration": 999})
    submit_turn(rogue_druid, "kidney_shot", "prowl")
    assert _has_effect(druid, "stealth"), "Prowl stealth should apply"
    _assert_no_stun_effect(druid)
    return True


def scenario_stun_priority_over_blink_like() -> bool:
    rogue_mage = make_match("rogue", "mage", seed=123)
    submit_turn(rogue_mage, "kidney_shot", "blink")
    _, mage = _player_states(rogue_mage)
    assert _has_effect(mage, "stunned"), "Kidney Shot should land before Blink"
    assert not _has_effect(mage, "blink"), "Blink should not become active when same-turn stunned"

    pal_warlock = make_match("paladin", "warlock", seed=123)
    submit_turn(pal_warlock, "hammer_of_justice", "demonic_gateway")
    _, warlock = _player_states(pal_warlock)
    assert _has_effect(warlock, "stunned"), "Hammer of Justice should land before Demonic Gateway"
    assert not _has_effect(warlock, "blink"), "Gateway blink effect should not be active when same-turn stunned"

    rogue_warlock = make_match("rogue", "warlock", seed=123)
    submit_turn(rogue_warlock, "kidney_shot", "demonic_circle_teleport")
    _, warlock_tp = _player_states(rogue_warlock)
    assert _has_effect(warlock_tp, "stunned"), "Kidney Shot should land before Demonic Circle: Teleport"
    assert not _has_effect(warlock_tp, "blink"), "Teleport blink effect should not be active when same-turn stunned"
    return True


def scenario_blink_like_blocks_attacks_for_two_turns() -> bool:
    match = make_match("mage", "rogue", seed=123)
    mage, rogue = _player_states(match)
    mage_hp_before = mage.res.hp
    submit_turn(match, "blink", "eviscerate")
    assert mage.res.hp == mage_hp_before, "Blink should force miss against same-turn attack"
    assert _has_effect(mage, "blink"), "Blink effect should be active after cast"

    submit_turn(match, _DEF_PASS, "eviscerate")
    assert mage.res.hp == mage_hp_before, "Blink should also force miss on the next turn"
    return True


def scenario_iceblock_priority_vs_aoe_with_pets() -> bool:
    match = make_match("warrior", "mage", seed=123)
    warrior, mage = _player_states(match)
    _add_pet(mage, "mage_imp_1")
    _add_pet(mage, "mage_imp_2")
    _add_pet(mage, "mage_imp_3")
    imp_ids = sorted(mage.pets.keys())

    mage_hp_before = mage.res.hp
    imp_hp_before = {pid: mage.pets[pid].hp for pid in imp_ids}
    warrior.res.rage = warrior.res.rage_max
    warrior.stats["atk"] = 1
    submit_turn(match, "dragon_roar", "iceblock")

    assert _has_effect(mage, "iceblock"), "Ice Block should apply this turn"
    assert mage.res.hp == mage_hp_before, "Ice Block should prevent champion AoE damage"
    for pid in imp_ids:
        assert _pet_took_damage_or_died(mage, pid, imp_hp_before[pid]), "Dragon Roar should still damage enemy pets through champion immunity"
    return True


def scenario_blink_like_aoe_still_hits_pets() -> bool:
    match = make_match("warrior", "mage", seed=123)
    warrior, mage = _player_states(match)
    _add_pet(mage, "mage_imp_1")
    _add_pet(mage, "mage_imp_2")
    _add_pet(mage, "mage_imp_3")
    imp_ids = sorted(mage.pets.keys())

    mage_hp_before = mage.res.hp
    imp_hp_before = {pid: mage.pets[pid].hp for pid in imp_ids}
    warrior.res.rage = warrior.res.rage_max

    submit_turn(match, "dragon_roar", "blink")

    assert _has_effect(mage, "blink"), "Blink should still activate on the defending champion"
    assert mage.res.hp == mage_hp_before, "Blink should preserve the champion's current AoE avoidance behavior"
    for pid in imp_ids:
        assert _pet_took_damage_or_died(mage, pid, imp_hp_before[pid]), "Blink-like defenses must not stop AoE pet damage"
    assert any("Target blinks away — Miss." in line for line in match.log), "Champion log should still reflect blink-like avoidance"
    assert any("Dragon Roar hits" in line and "Imp" in line for line in match.log), "AoE fanout should still log pet hits"
    return True


def scenario_iceblock_blocks_same_turn_stun_and_next_turn_attack() -> bool:
    match = make_match("rogue", "mage", seed=123)
    submit_turn(match, "kidney_shot", "iceblock")
    _, mage = _player_states(match)
    assert _has_effect(mage, "iceblock"), "Ice Block should apply"
    _assert_no_stun_effect(mage)

    hp_before = mage.res.hp
    submit_turn(match, "eviscerate", _DEF_PASS)
    assert mage.res.hp == hp_before, "Follow-up attack should be blocked while Ice Block remains active"
    return True


def scenario_aoe_hits_pets_with_immune_champion() -> bool:
    # Swipe case.
    swipe = make_match("warlock", "druid", seed=123)
    warlock_sid, druid_sid = _setup_imps(swipe, owner_idx=0)
    effects.apply_effect_by_id(swipe.state[druid_sid], "bear_form", overrides={"duration": 999})
    effects.apply_effect_by_id(swipe.state[warlock_sid], "iceblock", overrides={"duration": 1})
    imp_ids = sorted(swipe.state[warlock_sid].pets.keys())
    imp_hp_before = {pid: swipe.state[warlock_sid].pets[pid].hp for pid in imp_ids}
    submit_turn(swipe, _DEF_PASS, "swipe")
    for pid in imp_ids:
        assert _pet_took_damage_or_died(swipe.state[warlock_sid], pid, imp_hp_before[pid]), "Swipe should damage enemy pets"
    swipe_hits = [line for line in swipe.log if "Swipe hits" in line and "Imp" in line]
    observed_labels = []
    for line in swipe_hits:
        if "(imp1)" in line:
            observed_labels.append("imp1")
        elif "(imp2)" in line:
            observed_labels.append("imp2")
        elif "(imp3)" in line:
            observed_labels.append("imp3")
    assert observed_labels[:3] == ["imp1", "imp2", "imp3"], "Swipe pet hit order should be deterministic"

    # Dragon Roar case.
    roar = make_match("warlock", "warrior", seed=123)
    warlock_sid, warrior_sid = _setup_imps(roar, owner_idx=0)
    effects.apply_effect_by_id(roar.state[warlock_sid], "iceblock", overrides={"duration": 1})
    imp_ids = sorted(roar.state[warlock_sid].pets.keys())
    imp_hp_before = {pid: roar.state[warlock_sid].pets[pid].hp for pid in imp_ids}
    roar.state[warrior_sid].res.rage = roar.state[warrior_sid].res.rage_max
    submit_turn(roar, _DEF_PASS, "dragon_roar")
    for pid in imp_ids:
        assert _pet_took_damage_or_died(roar.state[warlock_sid], pid, imp_hp_before[pid]), "Dragon Roar should damage enemy pets"

    # Shield of Vengeance explosion case.
    sov = make_match("warlock", "paladin", seed=123)
    warlock_sid, pal_sid = _setup_imps(sov, owner_idx=0)
    effects.apply_effect_by_id(sov.state[warlock_sid], "iceblock", overrides={"duration": 2})
    imp_ids = sorted(sov.state[warlock_sid].pets.keys())
    imp_hp_before = {pid: sov.state[warlock_sid].pets[pid].hp for pid in imp_ids}
    effects.apply_effect_by_id(sov.state[pal_sid], "shield_of_vengeance", overrides={"duration": 1, "absorbed": 25})
    submit_turn(sov, _DEF_PASS, _DEF_PASS)
    assert any(_pet_took_damage_or_died(sov.state[warlock_sid], pid, imp_hp_before[pid]) for pid in imp_ids), "SoV explosion should damage enemy pets"
    return True


def scenario_hunter_turtle_priority() -> bool:
    match = make_match("hunter", "rogue", seed=123)
    hunter = match.state[match.players[0]]
    hp_before = hunter.res.hp

    submit_turn(match, "turtle", "kidney_shot")
    assert _has_effect(hunter, "aspect_of_turtle"), "Aspect of the Turtle should apply immediately"
    assert not any((fx.get("display") or {}).get("war_council") for fx in hunter.effects if fx.get("id") == "aspect_of_turtle"), "Aspect of the Turtle should not create a War Council status badge"
    assert any("uses their bare hands to cast Kidney Shot. Target evades the attack — Miss!" in line for line in match.log), "Single-target attacks into Turtle should use the evasion-style miss wording"
    assert any("uses their bare hands to cast Aspect of the Turtle. Causes incoming crowd control, single-target attacks and spells to miss, reduces all incoming damage by 30%." in line for line in match.log), "Aspect of the Turtle should log both miss and mitigation text"
    _assert_no_stun_effect(hunter)

    submit_turn(match, "aimed_shot", "eviscerate")
    assert hunter.res.hp == hp_before, "Single-target attack should miss into Aspect of the Turtle"
    latest_turn = match.log[match.log.index("Turn 2") + 1:]
    assert any("cannot attack while Aspect of the Turtle is active." in line for line in latest_turn), "Attack lockout should name Aspect of the Turtle, not the prior ability"

    warrior_match = make_match("hunter", "warrior", seed=123)
    hunter2 = warrior_match.state[warrior_match.players[0]]
    hp_before_aoe = hunter2.res.hp
    warrior_match.state[warrior_match.players[1]].res.rage = warrior_match.state[warrior_match.players[1]].res.rage_max
    submit_turn(warrior_match, "turtle", "dragon_roar")
    assert hunter2.res.hp < hp_before_aoe, "AoE should still damage the Hunter through Turtle"
    return True


def scenario_hunter_turtle_blocks_pet_spell_debuff_and_failed_cast_state() -> bool:
    # Pet melee (hunter companion) should miss into Turtle.
    hunter_pet_match = make_match("hunter", "hunter", seed=321)
    hunter_def = hunter_pet_match.state[hunter_pet_match.players[0]]
    submit_turn(hunter_pet_match, "turtle", "call_saber")
    assert any("Frostsaber" in line and "Target evades the attack — Miss!" in line for line in _turn_lines(hunter_pet_match, 1)), "Single-target hunter pet attacks should miss into Turtle"
    assert hunter_def.res.hp == hunter_def.res.hp_max, "Hunter pet attacks should not damage through Turtle"

    # Imp Firebolt and Agony application should miss into Turtle.
    imp_match = make_match("hunter", "warlock", seed=322)
    hunter_sid, warlock_sid = imp_match.players
    hunter = imp_match.state[hunter_sid]
    submit_turn(imp_match, "turtle", "summon_imp")
    turn1 = _turn_lines(imp_match, 1)
    assert any("Imp casts Firebolt" in line and "Target evades the attack — Miss!" in line for line in turn1), "Imp Firebolt should miss into Turtle"
    hp_after_turn1 = hunter.res.hp
    submit_turn(imp_match, _DEF_PASS, "agony")
    turn2 = _turn_lines(imp_match, 2)
    assert any("cast Agony. Target evades the attack — Miss!" in line for line in turn2), "Agony application should miss into Turtle"
    assert not _has_effect(hunter, "agony"), "Agony should not apply into Turtle"
    assert hunter.res.hp == hp_after_turn1, "Blocked Agony should not deal damage"

    # Shadowfiend melee should miss into Turtle.
    fiend_match = make_match("hunter", "priest", seed=323)
    fiend_hunter = fiend_match.state[fiend_match.players[0]]
    submit_turn(fiend_match, "turtle", "shadowfiend")
    turn_fiend = _turn_lines(fiend_match, 1)
    assert any("Shadowfiend melees the target" in line and "Target evades the attack — Miss!" in line for line in turn_fiend), "Shadowfiend melee should miss into Turtle"
    assert fiend_hunter.res.hp == fiend_hunter.res.hp_max, "Shadowfiend should not damage through Turtle"
    assert not any("Shadowfiend restores 13 mana" in line for line in turn_fiend), "Shadowfiend should not restore mana on a miss"

    # Failed Turtle cast while unable to act must not leave Turtle state/recovery active.
    denied_match = make_match("hunter", "priest", seed=324)
    denied_hunter_sid, denied_priest_sid = denied_match.players
    denied_hunter = denied_match.state[denied_hunter_sid]
    effects.apply_effect_by_id(denied_hunter, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "turtle", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Aspect of the Turtle but is feared and cannot act." in line for line in denied_turn), "Denied Turtle cast should log inability to act"
    assert not _has_effect(denied_hunter, "aspect_of_turtle"), "Denied Turtle cast must not apply Turtle"
    assert not any("recovers 10 Mana from Aspect of the Turtle." in line for line in denied_turn), "Denied Turtle cast must not produce Turtle mana recovery"

    submit_turn(denied_match, _DEF_PASS, _DEF_PASS)
    turn2 = _turn_lines(denied_match, 2)
    assert not any("recovers 10 Mana from Aspect of the Turtle." in line for line in turn2), "No stale Turtle recovery should persist after denied cast"
    return True


def scenario_hunter_turtle_same_turn_psychic_scream_consistency() -> bool:
    match = make_match("hunter", "priest", seed=325)
    hunter = match.state[match.players[0]]

    submit_turn(match, "turtle", "psychic_scream")
    turn1 = _turn_lines(match, 1)
    assert _has_effect(hunter, "aspect_of_turtle"), "Turtle should still resolve on the same turn against Psychic Scream"
    assert not _has_effect(hunter, "feared"), "Psychic Scream should miss into active Turtle protection on the same turn"
    assert any("uses their bare hands to cast Psychic Scream. Target evades the attack — Miss!" in line for line in turn1), "Psychic Scream should use the miss wording into Turtle"
    return True


def scenario_hunter_freezing_trap_breaks_on_damage() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    warrior = match.state[match.players[1]]
    submit_turn(match, "freezing_trap", _DEF_PASS)
    freeze = next((fx for fx in warrior.effects if fx.get("id") == "freezing_trap_freeze"), None)
    assert freeze is not None, "Freezing Trap should apply freeze"
    assert int(freeze.get("duration", 0) or 0) == 1, "Freezing Trap should leave exactly one locked turn after the application turn resolves"
    submit_turn(match, "aimed_shot", _DEF_PASS)
    assert not _has_effect(warrior, "freezing_trap_freeze"), "Any damage should break Freezing Trap freeze"
    return True


def scenario_hunter_freezing_trap_respects_cloak_same_turn() -> bool:
    match = make_match("rogue", "hunter", seed=123)
    rogue = match.state[match.players[0]]
    effects.remove_stealth(rogue)
    submit_turn(match, "cloak", "freezing_trap")
    assert _has_effect(rogue, "cloak_of_shadows"), "Cloak should be active on the same turn"
    assert not _has_effect(rogue, "freezing_trap_freeze"), "Freezing Trap should not apply through same-turn Cloak"
    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert any("uses their bare hands to cast Cloak of Shadows. Becomes shrouded from magic." in line for line in latest_turn), "Cloak action should use updated shrouded wording"
    assert any("uses their bare hands to cast Freezing Trap. Immune!" in line for line in latest_turn), "Freezing Trap should log immunity on same-turn Cloak"
    return True


def scenario_hunter_freezing_trap_respects_active_cloak() -> bool:
    match = make_match("rogue", "hunter", seed=123)
    rogue = match.state[match.players[0]]
    effects.remove_stealth(rogue)
    submit_turn(match, "cloak", _DEF_PASS)
    assert _has_effect(rogue, "cloak_of_shadows"), "Cloak should be active after the cast turn"
    submit_turn(match, _DEF_PASS, "freezing_trap")
    assert not _has_effect(rogue, "freezing_trap_freeze"), "Freezing Trap should not apply while Cloak is already active"
    latest_turn = match.log[match.log.index("Turn 2") + 1:]
    assert any("uses their bare hands to cast Freezing Trap. Immune!" in line for line in latest_turn), "Active Cloak should still produce the immunity log"
    return True


def scenario_divine_shield_lasts_three_turns() -> bool:
    match = make_match("paladin", "warrior", seed=123)
    paladin = match.state[match.players[0]]
    submit_turn(match, "divine_shield", _DEF_PASS)
    divine_shield = next((fx for fx in paladin.effects if fx.get("id") == "divine_shield"), None)
    assert divine_shield is not None, "Divine Shield should apply on cast"
    assert int(divine_shield.get("duration", 0) or 0) == 2, "Divine Shield should leave two turns after cast turn"
    return True


def scenario_cyclone_lasts_three_turns_and_has_status_metadata() -> bool:
    match = make_match("druid", "warrior", seed=123)
    druid, warrior = _player_states(match)
    effects.apply_effect_by_id(druid, "moonkin_form", overrides={"duration": 999})
    submit_turn(match, "cyclone", _DEF_PASS)
    cyclone = next((fx for fx in warrior.effects if fx.get("id") == "cyclone"), None)
    assert cyclone is not None, "Cyclone should apply on hit"
    assert int(cyclone.get("duration", 0) or 0) == 2, "Cyclone should leave two turns after the application turn"
    cyclone_display = effects.effect_template("cyclone").get("display", {})
    assert cyclone_display.get("war_council") is True, "Cyclone should expose status metadata for UI"
    assert cyclone_display.get("label") == "CYCLONED", "Cyclone UI status label should be CYCLONED"
    return True


def scenario_cyclone_denial_log_uses_cycloned_wording() -> bool:
    match = make_match("druid", "warrior", seed=123)
    druid, _ = _player_states(match)
    effects.apply_effect_by_id(druid, "moonkin_form", overrides={"duration": 999})
    submit_turn(match, "cyclone", _DEF_PASS)
    submit_turn(match, _DEF_PASS, "basic_attack")
    latest_turn = _turn_lines(match, 2)
    assert any("tries to use Basic Attack but is cycloned and cannot act." in line for line in latest_turn), "Cyclone denial log should use lower-case cycloned wording"
    return True


def scenario_mage_hot_streak_lasts_three_turns() -> bool:
    match = make_match("mage", "warrior", seed=123)
    mage = match.state[match.players[0]]

    submit_turn(match, "fire_blast", _DEF_PASS)
    hot_streak = next((fx for fx in mage.effects if fx.get("id") == "hot_streak"), None)
    assert hot_streak is not None, "Fire Blast should apply Hot Streak"
    assert int(hot_streak.get("duration", 0) or 0) == 2, "Hot Streak should leave the next 2 turns after the proc turn"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    hot_streak = next((fx for fx in mage.effects if fx.get("id") == "hot_streak"), None)
    assert hot_streak is not None and int(hot_streak.get("duration", 0) or 0) == 1, "Hot Streak should still be available on the following turn"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert not _has_effect(mage, "hot_streak"), "Hot Streak should expire after the 3-turn window"
    return True


def scenario_ring_of_ice_freezes_and_breaks_on_damage() -> bool:
    match = make_match("mage", "warrior", seed=123)
    warrior = match.state[match.players[1]]

    submit_turn(match, "ring_of_ice", _DEF_PASS)
    freeze = next((fx for fx in warrior.effects if fx.get("id") == "ring_of_ice_freeze"), None)
    assert freeze is not None, "Ring of Ice should apply its freeze effect"
    assert freeze.get("cant_act_reason") == "frozen", "Ring of Ice should use the frozen action-lock reason"
    assert int(freeze.get("duration", 0) or 0) == 1, "Ring of Ice should leave exactly one locked turn after the application turn resolves"

    submit_turn(match, "fireball", _DEF_PASS)
    latest_turn = _turn_lines(match, 2)
    assert any("deals" in line or "damage" in line for line in latest_turn), "Ring of Ice regression should verify an actual damaging hit lands on the frozen target"
    assert not _has_effect(warrior, "ring_of_ice_freeze"), "Any damage should break Ring of Ice freeze"
    return True


def scenario_fear_applies_feared_and_breaks_on_damage() -> bool:
    match = make_match("warlock", "warrior", seed=123)
    warrior = match.state[match.players[1]]

    submit_turn(match, "fear", _DEF_PASS)
    assert _has_effect(warrior, "feared"), "Fear should apply the feared effect"
    assert not _has_effect(warrior, "stunned"), "Fear should not apply the stunned effect"

    submit_turn(match, "drain_life", _DEF_PASS)
    latest_turn = _turn_lines(match, 2)
    assert any("deals" in line or "damage" in line for line in latest_turn), "Fear regression should verify an actual damaging hit lands on the feared target"
    assert not _has_effect(warrior, "feared"), "Any damage should break Fear"
    return True


def scenario_break_on_damage_cc_no_damage_turn_preserves_lockout() -> bool:
    for effect_id, effect_name, reason in _BREAK_ON_DAMAGE_CC_CASES:
        match = make_match("hunter", "warrior", seed=123)
        hunter = match.state[match.players[0]]
        effects.apply_effect_by_id(hunter, effect_id, overrides={"duration": 2})

        submit_turn(match, "basic_attack", _DEF_PASS)

        latest_turn = _turn_lines(match, 1)
        assert any(f"is {reason} and cannot act" in line for line in latest_turn), f"{effect_name} should keep the target locked on the no-damage turn after application"
    return True


def scenario_break_on_damage_cc_dot_tick_breaks() -> bool:
    for effect_id, effect_name, _ in _BREAK_ON_DAMAGE_CC_CASES:
        match = make_match("hunter", "warrior", seed=123)
        hunter = match.state[match.players[0]]
        warrior_sid = match.players[1]

        effects.apply_effect_by_id(hunter, effect_id, overrides={"duration": 2})
        effects.apply_effect_by_id(hunter, "wildfire_burn", overrides={"duration": 2, "tick_damage": 3, "source_sid": warrior_sid})
        hp_before = hunter.res.hp

        submit_turn(match, _DEF_PASS, _DEF_PASS)

        assert hunter.res.hp < hp_before, f"{effect_name} should break from incoming DoT damage"
        assert not _has_effect(hunter, effect_id), f"{effect_name} should be removed after a damaging DoT tick"
    return True


def scenario_break_on_damage_cc_aoe_breaks() -> bool:
    for effect_id, effect_name, _ in _BREAK_ON_DAMAGE_CC_CASES:
        match = make_match("hunter", "warrior", seed=123)
        hunter = match.state[match.players[0]]
        warrior = match.state[match.players[1]]

        effects.apply_effect_by_id(hunter, effect_id, overrides={"duration": 2})
        hp_before = hunter.res.hp
        warrior.res.rage = warrior.res.rage_max

        submit_turn(match, _DEF_PASS, "dragon_roar")

        assert hunter.res.hp < hp_before, f"{effect_name} should break from AoE damage"
        assert not _has_effect(hunter, effect_id), f"{effect_name} should be removed after AoE damage lands"
    return True


def scenario_break_on_damage_cc_pet_damage_breaks() -> bool:
    for effect_id, effect_name, _ in _BREAK_ON_DAMAGE_CC_CASES:
        match = make_match("hunter", "warrior", seed=123)
        hunter = match.state[match.players[0]]
        warrior = match.state[match.players[1]]

        submit_turn(match, "call_saber", _DEF_PASS)
        effects.apply_effect_by_id(warrior, effect_id, overrides={"duration": 2})
        hp_before = warrior.res.hp

        submit_turn(match, _DEF_PASS, _DEF_PASS)

        assert warrior.res.hp < hp_before, f"{effect_name} should break from Hunter pet damage"
        assert not _has_effect(warrior, effect_id), f"{effect_name} should be removed after Hunter pet damage lands"
    return True


def scenario_break_on_damage_cc_persists_after_same_turn_mutual_freeze() -> bool:
    match = make_match("mage", "hunter", seed=123)
    mage = match.state[match.players[0]]
    hunter = match.state[match.players[1]]

    submit_turn(match, "ring_of_ice", "freezing_trap")
    latest_turn = _turn_lines(match, 1)
    assert any("uses their bare hands to cast Ring of Ice." in line for line in latest_turn), "Ring of Ice should still resolve on the mutual-CC turn"
    assert any("uses their bare hands to cast Freezing Trap." in line for line in latest_turn), "Freezing Trap should still resolve on the mutual-CC turn"
    assert _has_effect(mage, "freezing_trap_freeze"), "Freezing Trap should remain active after same-turn mutual CC"
    assert _has_effect(hunter, "ring_of_ice_freeze"), "Ring of Ice should remain active after same-turn mutual CC"
    assert int(next(fx for fx in mage.effects if fx.get("id") == "freezing_trap_freeze").get("duration", 0) or 0) == 1, "Freezing Trap should carry its remaining duration into the next turn after same-turn mutual CC"
    assert int(next(fx for fx in hunter.effects if fx.get("id") == "ring_of_ice_freeze").get("duration", 0) or 0) == 1, "Ring of Ice should carry its remaining duration into the next turn after same-turn mutual CC"

    submit_turn(match, "fireball", "aimed_shot")
    latest_turn = _turn_lines(match, 2)
    assert any("tries to use Fireball but is frozen and cannot act." in line for line in latest_turn), "Ring of Ice / Freezing Trap mutual CC should keep the Mage frozen on the next turn"
    assert any("tries to use Aimed Shot but is frozen and cannot act." in line for line in latest_turn), "Ring of Ice / Freezing Trap mutual CC should keep the Hunter frozen on the next turn"
    return True


def scenario_break_on_damage_cc_persists_after_same_turn_fear_vs_freeze() -> bool:
    match = make_match("warlock", "hunter", seed=123)
    warlock = match.state[match.players[0]]
    hunter = match.state[match.players[1]]

    submit_turn(match, "fear", "freezing_trap")
    latest_turn = _turn_lines(match, 1)
    assert any("uses their bare hands to cast Fear." in line for line in latest_turn), "Fear should still resolve on the mutual-CC turn"
    assert any("uses their bare hands to cast Freezing Trap." in line for line in latest_turn), "Freezing Trap should still resolve on the mutual-CC turn"
    assert _has_effect(warlock, "freezing_trap_freeze"), "Freezing Trap should remain active after same-turn mutual CC"
    assert _has_effect(hunter, "feared"), "Fear should remain active after same-turn mutual CC"
    assert int(next(fx for fx in warlock.effects if fx.get("id") == "freezing_trap_freeze").get("duration", 0) or 0) == 1, "Freezing Trap should carry its remaining duration into the next turn after same-turn mutual CC"
    assert int(next(fx for fx in hunter.effects if fx.get("id") == "feared").get("duration", 0) or 0) == 1, "Fear should carry its remaining duration into the next turn after same-turn mutual CC"

    submit_turn(match, "drain_life", "aimed_shot")
    latest_turn = _turn_lines(match, 2)
    assert any("tries to use Drain Life but is frozen and cannot act." in line for line in latest_turn), "Freezing Trap should keep the Warlock frozen on the next turn"
    assert any("tries to use Aimed Shot but is feared and cannot act." in line for line in latest_turn), "Fear should keep the Hunter feared on the next turn"
    return True


def scenario_break_on_damage_logs_use_clean_wording_and_bottom_order() -> bool:
    match = make_match("mage", "hunter", seed=123)

    submit_turn(match, "ring_of_ice", _DEF_PASS)
    submit_turn(match, "fireball", _DEF_PASS)
    latest_turn = _turn_lines(match, 2)
    assert latest_turn[-1] == f"Ring of Ice on {match.players[1][:5]} breaks on damage.", "Ring of Ice break log should use clean wording and appear at the bottom of the turn"

    match = make_match("warlock", "hunter", seed=123)
    submit_turn(match, "fear", _DEF_PASS)
    submit_turn(match, "drain_life", _DEF_PASS)
    latest_turn = _turn_lines(match, 2)
    assert latest_turn[-1] == f"Fear on {match.players[1][:5]} breaks on damage.", "Fear break log should use clean wording and appear at the bottom of the turn"

    match = make_match("hunter", "mage", seed=123)
    submit_turn(match, "freezing_trap", _DEF_PASS)
    submit_turn(match, "basic_attack", _DEF_PASS)
    latest_turn = _turn_lines(match, 2)
    assert latest_turn[-1] == f"Freezing Trap on {match.players[1][:5]} breaks on damage.", "Freezing Trap break log should use clean wording and appear at the bottom of the turn"
    return True


def scenario_break_on_damage_uses_source_ability_name_for_shared_fear_state() -> bool:
    fear_match = make_match("warlock", "priest", seed=123)
    submit_turn(fear_match, "fear", _DEF_PASS)
    submit_turn(fear_match, "drain_life", _DEF_PASS)
    fear_turn = _turn_lines(fear_match, 2)
    assert fear_turn[-1] == f"Fear on {fear_match.players[1][:5]} breaks on damage.", "Fear-applied break should retain Fear naming"

    scream_match = make_match("priest", "warlock", seed=123)
    submit_turn(scream_match, "psychic_scream", _DEF_PASS)
    submit_turn(scream_match, "mind_blast", _DEF_PASS)
    scream_turn = _turn_lines(scream_match, 2)
    assert scream_turn[-1] == f"Psychic Scream on {scream_match.players[1][:5]} breaks on damage.", "Psychic Scream-applied break should keep source ability naming"
    return True


def scenario_redirected_damage_does_not_break_frozen() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]

    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active for redirect coverage"

    effects.apply_effect_by_id(hunter, "ring_of_ice_freeze", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    warrior.res.rage = warrior.res.rage_max

    submit_turn(match, _DEF_PASS, "mortal_strike")

    assert hunter.res.hp == hunter_hp_before, "Redirected single-target damage should not count as damage taken by the frozen Hunter"
    assert boar.hp < boar_hp_before, "Barrens Boar should absorb the redirected single-target hit"
    assert _has_effect(hunter, "ring_of_ice_freeze"), "Frozen should remain when the champion itself takes no damage"
    return True


def scenario_redirected_damage_does_not_break_feared() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]

    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active for redirect coverage"

    effects.apply_effect_by_id(hunter, "feared", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    warrior.res.rage = warrior.res.rage_max

    submit_turn(match, _DEF_PASS, "mortal_strike")

    assert hunter.res.hp == hunter_hp_before, "Redirected single-target damage should not count as damage taken by the feared Hunter"
    assert boar.hp < boar_hp_before, "Barrens Boar should absorb the redirected single-target hit"
    assert _has_effect(hunter, "feared"), "Fear should remain when the champion itself takes no damage"
    return True


def scenario_aoe_bypasses_redirect_and_breaks_frozen() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]

    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active for redirect coverage"

    effects.apply_effect_by_id(hunter, "ring_of_ice_freeze", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    hunter_hp_before = hunter.res.hp
    warrior.res.rage = warrior.res.rage_max

    submit_turn(match, _DEF_PASS, "dragon_roar")

    assert hunter.res.hp < hunter_hp_before, "AoE damage should still hit the frozen Hunter directly through redirect"
    assert not _has_effect(hunter, "ring_of_ice_freeze"), "Frozen should break when AoE damage reaches the champion directly"
    return True


def scenario_dot_bypasses_redirect_and_breaks_feared() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]

    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should be active for redirect coverage"

    effects.apply_effect_by_id(hunter, "feared", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    effects.apply_effect_by_id(hunter, "wildfire_burn", overrides={"duration": 2, "tick_damage": 3, "source_sid": warrior_sid})
    hunter_hp_before = hunter.res.hp

    submit_turn(match, _DEF_PASS, _DEF_PASS)

    assert hunter.res.hp < hunter_hp_before, "DoT damage should bypass redirect and still hurt the feared Hunter"
    assert not _has_effect(hunter, "feared"), "Fear should break when a DoT ticks on the champion directly"
    return True


def scenario_proc_raptor_strike_expires_correctly() -> bool:
    match = make_match("hunter", "warrior", seed=1)
    hunter = match.state[match.players[0]]
    enemy = match.state[match.players[1]]
    enemy.res.hp = enemy.res.hp_max = 999

    while not _has_effect(hunter, "raptor_strike_proc"):
        submit_turn(match, "aimed_shot", _DEF_PASS)
        assert match.turn < 10, "Aimed Shot should proc within a few deterministic turns"

    proc_effect = next((fx for fx in hunter.effects if fx.get("id") == "raptor_strike_proc"), None)
    assert proc_effect is not None and int(proc_effect.get("duration", 0) or 0) == 1, "Raptor Strike proc should be available for the next turn only after the proc turn resolves"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert not _has_effect(hunter, "raptor_strike_proc"), "Raptor Strike proc should expire after being skipped for its one available follow-up turn"
    return True


def scenario_proc_pyroblast_window_correct() -> bool:
    match = make_match("mage", "warrior", seed=123)
    mage = match.state[match.players[0]]

    submit_turn(match, "fire_blast", _DEF_PASS)
    hot_streak = next((fx for fx in mage.effects if fx.get("id") == "hot_streak"), None)
    assert hot_streak is not None and int(hot_streak.get("duration", 0) or 0) == 2, "Hot Streak should leave exactly the next 2 turns for Pyroblast after the proc turn"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    hot_streak = next((fx for fx in mage.effects if fx.get("id") == "hot_streak"), None)
    assert hot_streak is not None and int(hot_streak.get("duration", 0) or 0) == 1, "Hot Streak should still allow Pyroblast on the second turn of the window"

    submit_turn(match, "pyroblast", _DEF_PASS)
    assert not _has_effect(mage, "hot_streak"), "Pyroblast should consume Hot Streak on the last valid turn of the window"

    match2 = make_match("mage", "warrior", seed=123)
    mage2 = match2.state[match2.players[0]]
    submit_turn(match2, "fire_blast", _DEF_PASS)
    submit_turn(match2, _DEF_PASS, _DEF_PASS)
    submit_turn(match2, _DEF_PASS, _DEF_PASS)
    assert not _has_effect(mage2, "hot_streak"), "Hot Streak should expire after the full Pyroblast window if Pyroblast is not used"
    submit_turn(match2, "pyroblast", _DEF_PASS)
    latest_turn = _turn_lines(match2, 4)
    assert any("Pyroblast requires Hot Streak." in line for line in latest_turn), "Pyroblast should be rejected once the Hot Streak window has expired"
    return True


def scenario_negative_non_damage_effect_does_not_break_frozen() -> bool:
    match = make_match("mage", "hunter", seed=123)
    mage = match.state[match.players[0]]
    effects.apply_effect_by_id(mage, "ring_of_ice_freeze", overrides={"duration": 2})
    hp_before = mage.res.hp

    submit_turn(match, _DEF_PASS, "flare")

    assert mage.res.hp == hp_before, "Hostile non-damaging utility should not damage a frozen target"
    assert _has_effect(mage, "ring_of_ice_freeze"), "Frozen should remain after a hostile non-damaging effect"
    return True


def scenario_negative_non_damage_effect_does_not_break_feared() -> bool:
    match = make_match("warlock", "hunter", seed=123)
    warlock = match.state[match.players[0]]
    effects.apply_effect_by_id(warlock, "feared", overrides={"duration": 2})
    hp_before = warlock.res.hp

    submit_turn(match, _DEF_PASS, "flare")

    assert warlock.res.hp == hp_before, "Hostile non-damaging utility should not damage a feared target"
    assert _has_effect(warlock, "feared"), "Fear should remain after a hostile non-damaging effect"
    return True


def scenario_cc_status_display_metadata_is_exposed() -> bool:
    ring_display = effects.effect_template("ring_of_ice_freeze").get("display", {})
    trap_display = effects.effect_template("freezing_trap_freeze").get("display", {})
    fear_display = effects.effect_template("feared").get("display", {})

    assert ring_display.get("war_council") and ring_display.get("label") == "Frozen", "Ring of Ice should expose Frozen status metadata"
    assert trap_display.get("war_council") and trap_display.get("label") == "Frozen", "Freezing Trap should expose Frozen status metadata"
    assert fear_display.get("war_council") and fear_display.get("label") == "Feared", "Fear should expose Feared status metadata"
    return True


def scenario_key_buff_debuff_metadata_is_consistent() -> bool:
    mindgames = effects.effect_template("mindgames")
    avenging_wrath = effects.effect_template("avenging_wrath")
    die_by_sword = effects.effect_template("die_by_sword")

    assert mindgames.get("resolution_layer") == "damage_modification", "Mindgames should declare damage_modification resolution metadata"
    mindgames_display = mindgames.get("display", {})
    assert mindgames_display.get("war_council") is True and mindgames_display.get("label") == "Mindgames", "Mindgames should expose consistent status metadata"

    assert avenging_wrath.get("resolution_layer") == "damage_modification", "Avenging Wrath should declare damage_modification resolution metadata"

    assert die_by_sword.get("resolution_layer") == "pre_resolution_protection", "Die by the Sword should declare pre-resolution protection metadata"
    return True


def scenario_hunter_disengage_uses_custom_miss_text() -> bool:
    match = make_match("warrior", "hunter", seed=123)
    submit_turn(match, "basic_attack", "disengage")
    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert any("Target leaps away — Miss." in line for line in latest_turn), "Disengage should use the custom leap-away miss text"
    assert not any("Target blinks away — Miss." in line and "Disengage" in line for line in latest_turn), "Disengage should not reuse the blink-away miss text"
    return True


def scenario_blink_like_champion_status_and_disengage_duration() -> bool:
    blink_display = effects.effect_template("blink").get("display", {})
    gateway_display = effects.effect_template("demonic_gateway").get("display", {})
    teleport_display = effects.effect_template("teleport").get("display", {})
    disengage_display = effects.effect_template("disengage").get("display", {})
    assert blink_display.get("war_council") is True and blink_display.get("label") == "Blinked", "Blink should expose Blinked champion status metadata"
    assert gateway_display.get("war_council") is True and gateway_display.get("label") == "Teleported", "Demonic Gateway should expose Teleported champion status metadata"
    assert teleport_display.get("war_council") is True and teleport_display.get("label") == "Teleported", "Demonic Circle: Teleport should expose Teleported champion status metadata"
    assert disengage_display.get("war_council") is True and disengage_display.get("label") == "Disengaged", "Disengage should expose Disengaged champion status metadata"
    assert int(effects.effect_template("disengage").get("duration", 0) or 0) == 2, "Disengage duration should be 2 turns"

    match = make_match("mage", "warlock", seed=630)
    mage_sid, warlock_sid = match.players
    submit_turn(match, "blink", "demonic_gateway")
    snapshot = SOCKETS.snapshot_for(match, mage_sid)
    you_labels = [entry.get("display", {}).get("label") for entry in snapshot.get("you_effects", []) if isinstance(entry, dict)]
    enemy_labels = [entry.get("display", {}).get("label") for entry in snapshot.get("enemy_effects", []) if isinstance(entry, dict)]
    assert "Blinked" in you_labels, "Blink should render as Blinked in champion status snapshot data"
    assert "Teleported" in enemy_labels, "Demonic Gateway should render as Teleported in champion status snapshot data"
    assert all(name.get("name") != "Blink" for bucket in snapshot.get("you_effect_panel", {}).values() for name in (bucket or [])), "Blink-like effects should remain excluded from buff/debuff panel data"
    assert all(name.get("name") != "Demonic Gateway" for bucket in snapshot.get("enemy_effect_panel", {}).values() for name in (bucket or [])), "Demonic Gateway should remain excluded from buff/debuff panel data"

    teleport_match = make_match("warlock", "warrior", seed=631)
    submit_turn(teleport_match, "demonic_circle", _DEF_PASS)
    submit_turn(teleport_match, "teleport", _DEF_PASS)
    tp_snapshot = SOCKETS.snapshot_for(teleport_match, teleport_match.players[0])
    tp_labels = [entry.get("display", {}).get("label") for entry in tp_snapshot.get("you_effects", []) if isinstance(entry, dict)]
    assert "Teleported" in tp_labels, "Demonic Circle: Teleport should render as Teleported in champion status snapshot data"
    assert all(name.get("name") != "Demonic Circle: Teleport" for bucket in tp_snapshot.get("you_effect_panel", {}).values() for name in (bucket or [])), "Demonic Circle: Teleport should remain excluded from buff/debuff panel data"

    disengage_match = make_match("hunter", "warrior", seed=632)
    warrior = disengage_match.state[disengage_match.players[1]]
    submit_turn(disengage_match, "disengage", "basic_attack")
    hunter = disengage_match.state[disengage_match.players[0]]
    active_disengage = next((fx for fx in hunter.effects if fx.get("id") == "disengage"), None)
    assert active_disengage is not None and int(active_disengage.get("duration", 0) or 0) == 1, "Disengage should have one remaining turn after the cast turn"

    submit_turn(disengage_match, _DEF_PASS, "basic_attack")
    active_disengage = next((fx for fx in hunter.effects if fx.get("id") == "disengage"), None)
    assert active_disengage is None, "Disengage should expire after protecting the following turn"
    second_turn = _turn_lines(disengage_match, 2)
    assert any("Target leaps away — Miss." in line for line in second_turn), "Disengage should still force miss on the following turn after cast"

    hunter_hp_before = hunter.res.hp
    submit_turn(disengage_match, _DEF_PASS, "basic_attack")
    third_turn = _turn_lines(disengage_match, 3)
    assert not any("Target leaps away — Miss." in line for line in third_turn), "Disengage miss behavior should end after two protected turns"
    assert hunter.res.hp < hunter_hp_before, "Disengage should no longer protect beyond the intended two-turn window"
    return True


def scenario_hunter_flare_logs_stealth_breaks() -> bool:
    match = make_match("hunter", "hunter", seed=123)
    enemy = match.state[match.players[1]]
    submit_turn(match, _DEF_PASS, "call_saber")
    enemy_pet = _active_pet(enemy, "frostsaber")
    assert enemy_pet is not None, "Enemy pet should be present for Flare reveal coverage"
    effects.apply_effect_by_id(enemy, "stealth", overrides={"duration": 2})
    effects.apply_effect_by_id(enemy_pet, "stealth", overrides={"duration": 2})
    submit_turn(match, "flare", _DEF_PASS)
    latest_turn = match.log[match.log.index("Turn 2") + 1:]
    assert any("Flare reveals the target." in line for line in latest_turn), "Flare should keep its reveal summary log"
    assert any(line == f"{match.players[1][:5]}'s stealth broken by Flare." for line in latest_turn), "Flare should log the player stealth break on its own line"
    assert any(line == "Frostsaber's stealth broken by Flare." for line in latest_turn), "Flare should log pet stealth breaks on their own lines when present"
    assert not _has_effect(enemy, "stealth"), "Flare should remove player stealth"
    assert not _has_effect(enemy_pet, "stealth"), "Flare should remove pet stealth"
    return True


def scenario_redirect_and_blink_like_coexist_without_cross_regression() -> bool:
    redirect_match = make_match("hunter", "warrior", seed=123)
    hunter_sid, warrior_sid = redirect_match.players
    hunter = redirect_match.state[hunter_sid]
    warrior = redirect_match.state[warrior_sid]
    submit_turn(redirect_match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should summon for redirect coverage"
    effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
    hunter_hp_before = hunter.res.hp
    boar_hp_before = boar.hp
    warrior.res.rage = warrior.res.rage_max
    submit_turn(redirect_match, _DEF_PASS, "mortal_strike")
    turn_two = _turn_lines(redirect_match, 2)
    assert hunter.res.hp == hunter_hp_before, "Single-target hit should still redirect to boar"
    assert boar.hp < boar_hp_before, "Redirected single-target hit should damage boar"
    assert any("Barrens Boar intercepts Mortal Strike" in line for line in turn_two), "Redirect intercept log should remain present"

    blink_match = make_match("mage", "warrior", seed=123)
    submit_turn(blink_match, "blink", "basic_attack")
    blink_turn = _turn_lines(blink_match, 1)
    assert any("Target blinks away — Miss." in line for line in blink_turn), "Blink-like miss behavior should remain unchanged without redirect"

    combined_match = make_match("hunter", "mage", seed=123)
    submit_turn(combined_match, "call_boar", "blink")
    combined_hunter_sid, combined_mage_sid = combined_match.players
    combined_hunter = combined_match.state[combined_hunter_sid]
    combined_mage = combined_match.state[combined_mage_sid]
    combined_boar = _active_pet(combined_hunter, "barrens_boar")
    assert combined_boar is not None, "Boar should exist for mixed redirect/blink turn flow"
    effects.apply_effect_by_id(combined_hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": combined_boar.id})
    combined_hunter_hp_before = combined_hunter.res.hp
    combined_boar_hp_before = combined_boar.hp
    submit_turn(combined_match, _DEF_PASS, "fireball")
    mixed_turn = _turn_lines(combined_match, 2)
    assert combined_hunter.res.hp == combined_hunter_hp_before, "Redirect should still intercept normal single-target spells in mixed flows"
    assert combined_boar.hp < combined_boar_hp_before, "Boar should still absorb redirected mixed-flow damage"
    assert not any("uses their bare hands to cast Fireball." in line and "blinks away — Miss." in line for line in mixed_turn), "Redirected spells should not be turned into blink-miss outcomes"

    combined_mage_hp_before = combined_mage.res.hp
    submit_turn(combined_match, "multi_shot", _DEF_PASS)
    assert combined_mage.res.hp < combined_mage_hp_before, "AoE behavior should remain unchanged and bypass redirect"
    return True


def scenario_mutual_stuns_count_current_turn_immediately() -> bool:
    rogue_druid = make_match("rogue", "druid", seed=123)
    rogue_sid, druid_sid = rogue_druid.players
    effects.remove_effect(rogue_druid.state[rogue_sid], "stealth")
    submit_turn(rogue_druid, _DEF_PASS, "cat")
    submit_turn(rogue_druid, "kidney_shot", "maim")
    rogue_stun = next((fx for fx in rogue_druid.state[rogue_sid].effects if fx.get("id") == "stunned"), None)
    druid_stun = next((fx for fx in rogue_druid.state[druid_sid].effects if fx.get("id") == "stunned"), None)
    assert rogue_stun is not None, "Maim should stun the Rogue on the mutual-stun turn"
    assert druid_stun is not None, "Kidney Shot should stun the Druid on the mutual-stun turn"
    assert int(rogue_stun.get("duration", 0) or 0) == 2, "Maim(3) should leave 2 turns after the application turn resolves"
    assert int(druid_stun.get("duration", 0) or 0) == 1, "Kidney Shot(2) should leave 1 turn after the application turn resolves"

    mirrored_rogues = make_match("rogue", "rogue", seed=456)
    for sid in mirrored_rogues.players:
        effects.remove_effect(mirrored_rogues.state[sid], "stealth")
    submit_turn(mirrored_rogues, "cheap_shot", "cheap_shot")
    assert not _has_effect(mirrored_rogues.state[mirrored_rogues.players[0]], "stunned"), "Cheap Shot(1) should expire at end of the mutual application turn"
    assert not _has_effect(mirrored_rogues.state[mirrored_rogues.players[1]], "stunned"), "Cheap Shot(1) should expire at end of the mutual application turn"
    latest_turn = mirrored_rogues.log[mirrored_rogues.log.index("Turn 1") + 1:]
    assert any("uses their bare hands to cast Cheap Shot. Target stunned." in line for line in latest_turn), "Both Cheap Shots should resolve instead of being pre-blocked"

    paladin_rogue = make_match("paladin", "rogue", seed=789)
    effects.remove_effect(paladin_rogue.state[paladin_rogue.players[1]], "stealth")
    submit_turn(paladin_rogue, "hammer_of_justice", "kidney_shot")
    paladin_stun = next((fx for fx in paladin_rogue.state[paladin_rogue.players[0]].effects if fx.get("id") == "stunned"), None)
    rogue_stun_hybrid = next((fx for fx in paladin_rogue.state[paladin_rogue.players[1]].effects if fx.get("id") == "stunned"), None)
    assert paladin_stun is not None and int(paladin_stun.get("duration", 0) or 0) == 1, "Kidney Shot(2) should leave one future turn on the Paladin"
    assert rogue_stun_hybrid is not None and int(rogue_stun_hybrid.get("duration", 0) or 0) == 1, "HoJ(2) should leave one future turn on the Rogue"
    return True


def scenario_stealth_break_log_order_after_actions() -> bool:
    match = make_match("warrior", "rogue", p1_items={"weapon": "twin_blades_azzinoth"}, seed=123)
    warrior = match.state[match.players[0]]
    warrior.res.rage = warrior.res.rage_max
    effects.remove_effect(match.state[match.players[1]], "stealth")
    submit_turn(match, "dragon_roar", "vanish")
    latest_turn = _turn_lines(match, 1)

    roar_idx = next(i for i, line in enumerate(latest_turn) if "cast Dragon Roar." in line)
    vanish_idx = next(i for i, line in enumerate(latest_turn) if "cast Vanish." in line)
    stealth_break_idx = next(i for i, line in enumerate(latest_turn) if "stealth broken by Dragon Roar." in line)
    bleed_tick_idx = next(i for i, line in enumerate(latest_turn) if "Dragon Roar Bleed" in line and "suffers" in line)

    assert roar_idx < vanish_idx < stealth_break_idx < bleed_tick_idx, "Stealth-break log should appear after action logs and before bleed tick logs"
    return True


def scenario_mutual_freeze_duration_model_remains_unchanged() -> bool:
    ring_match = make_match("mage", "mage", seed=111)
    submit_turn(ring_match, "ring_of_ice", "ring_of_ice")
    p1_freeze = next((fx for fx in ring_match.state[ring_match.players[0]].effects if fx.get("id") == "ring_of_ice_freeze"), None)
    p2_freeze = next((fx for fx in ring_match.state[ring_match.players[1]].effects if fx.get("id") == "ring_of_ice_freeze"), None)
    assert p1_freeze is not None and int(p1_freeze.get("duration", 0) or 0) == 1, "Mutual Ring of Ice should still keep one frozen turn after application"
    assert p2_freeze is not None and int(p2_freeze.get("duration", 0) or 0) == 1, "Mutual Ring of Ice should still keep one frozen turn after application"

    trap_match = make_match("mage", "hunter", seed=222)
    submit_turn(trap_match, "ring_of_ice", "freezing_trap")
    mage_freeze = next((fx for fx in trap_match.state[trap_match.players[0]].effects if fx.get("id") == "freezing_trap_freeze"), None)
    hunter_freeze = next((fx for fx in trap_match.state[trap_match.players[1]].effects if fx.get("id") == "ring_of_ice_freeze"), None)
    assert mage_freeze is not None and int(mage_freeze.get("duration", 0) or 0) == 1, "Freezing Trap should still keep one frozen turn after application"
    assert hunter_freeze is not None and int(hunter_freeze.get("duration", 0) or 0) == 1, "Ring of Ice should still keep one frozen turn after application"
    return True


def scenario_break_on_damage_cc_blocks_form_shift_same_turn() -> bool:
    fear_match = make_match("warlock", "druid", seed=3101)
    submit_turn(fear_match, "fear", "bear")
    fear_turn = fear_match.log[fear_match.log.index("Turn 1") + 1:]
    assert any("tries to use Bear Form but is feared and cannot act." in line for line in fear_turn), "Fear should block Bear Form on the application turn"
    assert not _has_effect(fear_match.state[fear_match.players[1]], "bear_form"), "Bear Form should not apply while feared"

    trap_match = make_match("hunter", "druid", seed=3102)
    submit_turn(trap_match, "freezing_trap", "bear")
    trap_turn = trap_match.log[trap_match.log.index("Turn 1") + 1:]
    assert any("tries to use Bear Form but is frozen and cannot act." in line for line in trap_turn), "Freezing Trap should block Bear Form on the application turn"
    assert not _has_effect(trap_match.state[trap_match.players[1]], "bear_form"), "Bear Form should not apply while frozen"

    ring_match = make_match("mage", "druid", seed=3103)
    submit_turn(ring_match, "ring_of_ice", "bear")
    ring_turn = ring_match.log[ring_match.log.index("Turn 1") + 1:]
    assert any("tries to use Bear Form but is frozen and cannot act." in line for line in ring_turn), "Ring of Ice should block Bear Form on the application turn"
    assert not _has_effect(ring_match.state[ring_match.players[1]], "bear_form"), "Bear Form should not apply while frozen"
    return True


def scenario_break_on_damage_cc_blocks_other_normal_actions_same_turn() -> bool:
    match = make_match("hunter", "druid", seed=3104)
    submit_turn(match, "freezing_trap", "basic_attack")
    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert any("tries to use Basic Attack but is frozen and cannot act." in line for line in latest_turn), "Frozen lockout should block normal non-form abilities as well"
    return True


def scenario_only_selected_defensives_can_cast_while_crowd_controlled() -> bool:
    stunned_mage = make_match("mage", "warrior", seed=3201)
    mage = stunned_mage.state[stunned_mage.players[0]]
    effects.apply_effect_by_id(mage, "stunned", overrides={"duration": 1})
    submit_turn(stunned_mage, "iceblock", _DEF_PASS)
    assert _has_effect(mage, "iceblock"), "Stunned Mage should still cast Ice Block"

    stunned_paladin = make_match("paladin", "warrior", seed=3202)
    paladin = stunned_paladin.state[stunned_paladin.players[0]]
    effects.apply_effect_by_id(paladin, "stunned", overrides={"duration": 1})
    submit_turn(stunned_paladin, "divine_shield", _DEF_PASS)
    assert _has_effect(paladin, "divine_shield"), "Stunned Paladin should still cast Divine Shield"

    stunned_warlock = make_match("warlock", "warrior", seed=3203)
    warlock = stunned_warlock.state[stunned_warlock.players[0]]
    effects.apply_effect_by_id(warlock, "stunned", overrides={"duration": 1})
    submit_turn(stunned_warlock, "unending_resolve", _DEF_PASS)
    warlock_turn = _turn_lines(stunned_warlock, 1)
    assert any("uses their bare hands to cast Unending Resolve." in line for line in warlock_turn), "Stunned Warlock should still cast Unending Resolve"
    assert not any("tries to use Unending Resolve but is stunned and cannot act." in line for line in warlock_turn), "Unending Resolve should not be denied while stunned"

    feared_mage = make_match("mage", "warrior", seed=3204)
    feared_actor = feared_mage.state[feared_mage.players[0]]
    effects.apply_effect_by_id(feared_actor, "feared", overrides={"duration": 1})
    submit_turn(feared_mage, "iceblock", _DEF_PASS)
    assert _has_effect(feared_actor, "iceblock"), "Feared Mage should still cast Ice Block"

    frozen_paladin = make_match("paladin", "warrior", seed=3205)
    frozen_actor = frozen_paladin.state[frozen_paladin.players[0]]
    effects.apply_effect_by_id(frozen_actor, "ring_of_ice_freeze", overrides={"duration": 1})
    submit_turn(frozen_paladin, "divine_shield", _DEF_PASS)
    assert _has_effect(frozen_actor, "divine_shield"), "Frozen Paladin should still cast Divine Shield"

    denied_warrior = make_match("warrior", "mage", seed=3206)
    warrior = denied_warrior.state[denied_warrior.players[0]]
    effects.apply_effect_by_id(warrior, "stunned", overrides={"duration": 1})
    submit_turn(denied_warrior, "die_by_sword", _DEF_PASS)
    latest_turn = _turn_lines(denied_warrior, 1)
    assert any("tries to use Die by the Sword but is stunned and cannot act." in line for line in latest_turn), "Non-whitelisted defensives should remain denied while crowd controlled"
    assert not _has_effect(warrior, "die_by_sword"), "Die by the Sword should not apply while stunned"
    return True


def scenario_stealth_breaks_on_total_turn_damage_threshold() -> bool:
    match = make_match("rogue", "warlock", seed=6103)
    rogue_sid, warlock_sid = match.players
    rogue = match.state[rogue_sid]
    effects.apply_effect_by_id(rogue, "stealth", overrides={"duration": 3})
    effects.apply_effect_by_id(rogue, "corruption", overrides={"duration": 1, "tick_damage": 4, "source_sid": warlock_sid})
    effects.apply_effect_by_id(rogue, "agony", overrides={"duration": 1, "tick_damage": 4, "source_sid": warlock_sid, "dot_mode": "fixed"})

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert not _has_effect(rogue, "stealth"), "Stealth should break when cumulative damage in a turn exceeds 5"
    return True


def scenario_earth_shock_duration_update_does_not_change_global_duration_semantics() -> bool:
    assert ABILITIES["earth_shock"]["target_effects_on_hit"][0]["duration"] == 2, "Earth Shock rider duration should be 2 at ability definition level"
    assert effects.effect_template("earth_shock").get("duration") == 2, "Earth Shock effect template duration should be 2"

    ring_match = make_match("mage", "warrior", seed=9131)
    mage_sid, warrior_sid = ring_match.players
    mage = ring_match.state[mage_sid]
    warrior = ring_match.state[warrior_sid]
    mage.stats["acc"] = 999
    warrior.stats["eva"] = 0
    submit_turn(ring_match, "ring_of_ice", _DEF_PASS)
    ring_freeze = next((fx for fx in warrior.effects if fx.get("id") == "ring_of_ice_freeze"), None)
    assert ring_freeze is not None and int(ring_freeze.get("duration", 0) or 0) == 1, "Ring of Ice duration semantics should remain unchanged for unrelated effects"
    return True


def scenario_proc_and_burn_duration_cleanup_and_shield_panel_cleanup() -> bool:
    assert int(effects.effect_template("mind_blast_empowered").get("duration", 0) or 0) == 5, "Mind Blast empowerment duration should be 5"
    assert int(effects.effect_template("starfire_ready").get("duration", 0) or 0) == 5, "Starfire proc duration should be 5"
    assert int(effects.effect_template("rip_ready").get("duration", 0) or 0) == 5, "Rip proc duration should be 5"
    assert int(effects.effect_template("crusader_empower").get("duration", 0) or 0) == 5, "Crusader empowerment duration should be 5"
    assert int(effects.effect_template("paladin_final_verdict_empowered").get("duration", 0) or 0) == 5, "Final Verdict empowerment duration should be 5"
    assert int(effects.effect_template("shadowy_insight").get("duration", 0) or 0) == 5, "Shadowy Insight proc duration should be 5"
    assert int(effects.effect_template("burn").get("duration", 0) or 0) == 3, "Wand of Fire burn duration should be 3"

    for effect_id, template in effects.EFFECT_TEMPLATES.items():
        tags = template.get("tags") or []
        if int(template.get("duration", 0) or 0) != 999:
            continue
        if "proc" in tags or "empower" in effect_id or "empower" in str(template.get("name", "")).lower():
            raise AssertionError(f"{effect_id} should not remain at 999-turn duration")

    match = make_match("mage", "warrior", seed=777)
    mage = match.state[match.players[0]]
    warrior = match.state[match.players[1]]

    mage.effects.append(
        {
            "id": "wand_of_fire_test",
            "type": "item_passive",
            "source_item": "Wand of Fire",
            "passive": {"type": "burn", "value": 2, "trigger": "on_hit"},
        }
    )
    _, wand_logs, _, _ = effects.trigger_on_hit_passives(mage, warrior, base_damage=5, damage_type="magic", rng=random.Random(1))
    assert wand_logs == [f"{mage.sid[:5]} scorches the target with Wand of Fire."], "Wand of Fire log should omit damage-per-turn suffix"

    burn = next((fx for fx in warrior.effects if fx.get("id") == "burn"), None)
    assert burn is not None and int(burn.get("duration", 0) or 0) == 3, "Wand burn should apply as a 3-turn DoT"
    burn_panel = effects.build_effect_panel_payload(warrior)
    assert any(entry.get("name") == "Fire Burn" for entry in burn_panel["debuffs_magical"]), "Fire Burn should appear in magical debuffs"

    effects.apply_effect_by_id(mage, "rip_ready", overrides={"duration": 5})
    mage_panel = effects.build_effect_panel_payload(mage)
    assert any(entry.get("name") == "Sharpened Claws" for entry in mage_panel["buffs_physical"]), "Sharpened Claws should appear in physical buffs"

    absorb_match = make_match("mage", "warrior", seed=778)
    shield_owner = absorb_match.state[absorb_match.players[0]]
    effects.apply_effect_by_id(shield_owner, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(shield_owner, 10, source_name="Ice Barrier", effect_id="ice_barrier")

    panel_before = effects.build_effect_panel_payload(shield_owner)
    assert any(entry.get("name") == "Ice Barrier" for entry in panel_before["buffs_magical"]), "Ice Barrier should appear while absorb remains"

    remaining_partial, absorbed_partial, _ = effects.consume_absorbs(shield_owner, 4)
    assert remaining_partial == 0 and absorbed_partial == 4, "Partial absorb consumption should be tracked"
    assert _has_effect(shield_owner, "ice_barrier"), "Shield effect should remain while absorb is still available"
    panel_partial = effects.build_effect_panel_payload(shield_owner)
    assert any(entry.get("name") == "Ice Barrier" for entry in panel_partial["buffs_magical"]), "Ice Barrier should still appear while absorb remains"

    remaining_full, absorbed_full, _ = effects.consume_absorbs(shield_owner, 6)
    assert remaining_full == 0 and absorbed_full == 6, "Remaining absorb should fully consume matching incoming damage"
    assert "ice_barrier" not in shield_owner.res.absorbs, "Ice Barrier absorb layer should be removed when depleted"
    assert not _has_effect(shield_owner, "ice_barrier"), "Ice Barrier effect should be removed when absorb is depleted"
    panel_after = effects.build_effect_panel_payload(shield_owner)
    assert not any(entry.get("name") == "Ice Barrier" for entry in panel_after["buffs_magical"]), "Ice Barrier should disappear from panel when depleted"

    layering_match = make_match("priest", "warrior", seed=779)
    layered_owner = layering_match.state[layering_match.players[0]]
    effects.apply_effect_by_id(layered_owner, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(layered_owner, 30, source_name="Power Word: Shield", effect_id="power_word_shield")
    effects.apply_effect_by_id(layered_owner, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(layered_owner, 20, source_name="Ice Barrier", effect_id="ice_barrier")
    effects.consume_absorbs(layered_owner, 10)
    assert int(layered_owner.res.absorbs["ice_barrier"]["remaining"]) == 10, "Latest-cast absorb layer should be consumed first"
    assert int(layered_owner.res.absorbs["power_word_shield"]["remaining"]) == 30, "Earlier absorb layer should remain untouched until latest layer is exhausted"

    sov_match = make_match("paladin", "warrior", seed=780)
    pal_sid, war_sid = sov_match.players
    submit_turn(sov_match, "shield_of_vengeance", _DEF_PASS)
    paladin = sov_match.state[pal_sid]
    enemy = sov_match.state[war_sid]
    sov_layer = int(paladin.res.absorbs.get("shield_of_vengeance", {}).get("remaining", 0) or 0)
    assert sov_layer > 0, "Shield of Vengeance absorb layer should exist after cast"
    effects.consume_absorbs(paladin, sov_layer)
    assert "shield_of_vengeance" not in paladin.res.absorbs, "Shield of Vengeance absorb layer should be removed when fully consumed"
    sov_panel = effects.build_effect_panel_payload(paladin)
    assert not any(entry.get("name") == "Shield of Vengeance" for entry in sov_panel["buffs_magical"]), "Shield of Vengeance should disappear from panel once fully consumed"
    hp_before = enemy.res.hp
    submit_turn(sov_match, _DEF_PASS, _DEF_PASS)
    assert any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_match, 2)), "Shield of Vengeance should still explode after full absorb consumption"
    assert enemy.res.hp < hp_before, "Shield of Vengeance explosion should still deal damage after full absorb consumption"
    return True


def scenario_high_risk_same_turn_protection_and_denial_pack() -> bool:
    turtle_cc_match = make_match("hunter", "rogue", seed=8301)
    effects.remove_effect(turtle_cc_match.state[turtle_cc_match.players[1]], "stealth")
    submit_turn(turtle_cc_match, "turtle", "kidney_shot")
    turn_lines = _turn_lines(turtle_cc_match, 1)
    assert any("Target evades the attack — Miss!" in line for line in turn_lines), "Aspect of the Turtle should deny same-turn single-target CC applications"
    assert not _has_effect(turtle_cc_match.state[turtle_cc_match.players[0]], "stunned"), "Denied same-turn CC should not apply the stun state"

    blink_match = make_match("mage", "warrior", seed=8302)
    submit_turn(blink_match, "blink", "basic_attack")
    assert any("Target blinks away — Miss." in line for line in _turn_lines(blink_match, 1)), "Blink-like state should deny single-target attacks in the same turn"
    aoe_match = make_match("mage", "warrior", seed=8303)
    aoe_match.state[aoe_match.players[1]].res.rage = 10
    submit_turn(aoe_match, "blink", "dragon_roar")
    assert any("Target blinks away — Miss." in line for line in _turn_lines(aoe_match, 1)), "Current behavior should preserve blink-like champion miss text versus AoE"

    reveal_match = make_match("hunter", "rogue", seed=8304)
    submit_turn(reveal_match, "flare", _DEF_PASS)
    reveal_turn = _turn_lines(reveal_match, 1)
    action_idx = next(i for i, line in enumerate(reveal_turn) if "cast Flare. Flare reveals the target." in line)
    break_idx = next(i for i, line in enumerate(reveal_turn) if "stealth broken by Flare." in line)
    assert action_idx < break_idx, "Stealth reveal logs should stay in same-turn action-then-break order"
    assert not _has_effect(reveal_match.state[reveal_match.players[1]], "stealth"), "Flare should remove stealth on the same turn"

    denied_effect_match = make_match("hunter", "warrior", seed=8305)
    effects.apply_effect_by_id(denied_effect_match.state[denied_effect_match.players[0]], "feared", overrides={"duration": 1})
    submit_turn(denied_effect_match, "turtle", _DEF_PASS)
    assert not _has_effect(denied_effect_match.state[denied_effect_match.players[0]], "aspect_of_turtle"), "Denied actions should not apply their self-effects"

    immediate_match = make_match("warlock", "warrior", seed=8306)
    effects.apply_effect_by_id(immediate_match.state[immediate_match.players[0]], "feared", overrides={"duration": 1})
    submit_turn(immediate_match, "totally_fake_ability", _DEF_PASS)
    assert any("fumbles (unknown ability)." in line for line in _turn_lines(immediate_match, 1)), "Unknown ability immediate-path handling should remain first in precedence"
    return True


def scenario_high_risk_shared_effect_naming_and_panel_pack() -> bool:
    fear_match = make_match("warlock", "warrior", seed=8501)
    submit_turn(fear_match, "fear", _DEF_PASS)
    fear_panel = effects.build_effect_panel_payload(fear_match.state[fear_match.players[1]])
    assert any(entry.get("name") == "Fear" for entry in fear_panel["debuffs_magical"]), "Shared fear runtime should use Fear source name in panel"
    submit_turn(fear_match, "basic_attack", _DEF_PASS)
    assert any(line == f"Fear on {fear_match.players[1][:5]} breaks on damage." for line in _turn_lines(fear_match, 2)), "Break-on-damage naming should use Fear source ability name"

    scream_match = make_match("priest", "warrior", seed=8502)
    submit_turn(scream_match, "psychic_scream", _DEF_PASS)
    scream_panel = effects.build_effect_panel_payload(scream_match.state[scream_match.players[1]])
    assert any(entry.get("name") == "Psychic Scream" for entry in scream_panel["debuffs_magical"]), "Shared fear runtime should use Psychic Scream source name in panel"
    submit_turn(scream_match, "mind_blast", _DEF_PASS)
    assert any(line == f"Psychic Scream on {scream_match.players[1][:5]} breaks on damage." for line in _turn_lines(scream_match, 2)), "Break-on-damage naming should use Psychic Scream source ability name"

    naming_match = make_match("hunter", "warrior", seed=8503)
    hunter = naming_match.state[naming_match.players[0]]
    warrior = naming_match.state[naming_match.players[1]]
    effects.apply_effect_by_id(hunter, "arcane_surge", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "raptor_strike_proc", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "starfire_ready", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "rip_ready", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "mind_blast_empowered", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "dragon_roar_bleed", overrides={"duration": 2, "source_sid": naming_match.players[0]})
    effects.apply_effect_by_id(warrior, "burn", overrides={"duration": 2, "tick_damage": 2, "source_sid": naming_match.players[0]})
    effects.apply_effect_by_id(hunter, "die_by_sword_mitigation", overrides={"duration": 2})
    effects.apply_effect_by_id(hunter, "die_by_sword_mitigation", overrides={"duration": 2})
    panel_hunter = effects.build_effect_panel_payload(hunter)
    panel_warrior = effects.build_effect_panel_payload(warrior)
    buff_names = [entry.get("name") for bucket in panel_hunter.values() for entry in bucket]
    debuff_names = [entry.get("name") for bucket in panel_warrior.values() for entry in bucket]
    for expected in ("Arcane Surge", "Killing Frenzy", "Astral Surge", "Sharpened Claws", "Mind Assault"):
        assert expected in buff_names, f"{expected} renamed panel entry should remain stable"
    for expected in ("Rending Roar", "Fire Burn"):
        assert expected in debuff_names, f"{expected} renamed panel entry should remain stable"
    assert "Die by the Sword Mitigation" not in buff_names and "die_by_sword_mitigation" not in buff_names, "Internal helper effects must not leak to the panel"
    assert buff_names.count("Arcane Surge") == 1, "Merged visible buffs should only render once in panel payload"
    return True


def scenario_phase0_same_turn_protection_and_denial_timing_lock() -> bool:
    allowed = make_match("mage", "warrior", seed=9011)
    allowed_mage = allowed.state[allowed.players[0]]
    effects.apply_effect_by_id(allowed_mage, "stunned", overrides={"duration": 1})
    submit_turn(allowed, "iceblock", _DEF_PASS)
    assert _has_effect(allowed_mage, "iceblock"), "Ice Block should remain castable while stunned"
    assert not any("tries to use Ice Block but is stunned and cannot act." in line for line in _turn_lines(allowed, 1)), "Whitelisted defensive should not produce denial text"

    denied = make_match("warrior", "mage", seed=9012)
    denied_warrior = denied.state[denied.players[0]]
    effects.apply_effect_by_id(denied_warrior, "stunned", overrides={"duration": 1})
    submit_turn(denied, "die_by_sword", _DEF_PASS)
    denied_turn = _turn_lines(denied, 1)
    assert any("tries to use Die by the Sword but is stunned and cannot act." in line for line in denied_turn), "Non-whitelisted defensive should remain denied while stunned"
    assert not _has_effect(denied_warrior, "die_by_sword"), "Denied non-whitelisted defensive must not apply its effect"

    turtle = make_match("hunter", "rogue", seed=9013)
    effects.remove_effect(turtle.state[turtle.players[1]], "stealth")
    submit_turn(turtle, "turtle", "kidney_shot")
    hunter = turtle.state[turtle.players[0]]
    turtle_turn = _turn_lines(turtle, 1)
    assert any("Target evades the attack — Miss!" in line for line in turtle_turn), "Aspect of the Turtle should still force single-target misses"
    assert not _has_effect(hunter, "stunned"), "Kidney Shot should still fail into same-turn Turtle"

    blink_aoe = make_match("mage", "warrior", seed=9014)
    blink_aoe.state[blink_aoe.players[1]].res.rage = 10
    mage_hp_before = blink_aoe.state[blink_aoe.players[0]].res.hp
    submit_turn(blink_aoe, "blink", "dragon_roar")
    assert any("Target blinks away — Miss." in line for line in _turn_lines(blink_aoe, 1)), "Blink-like AoE champion miss text should remain unchanged"
    assert blink_aoe.state[blink_aoe.players[0]].res.hp == mage_hp_before, "Blink-like same-turn AoE champion protection behavior should remain unchanged"
    return True
