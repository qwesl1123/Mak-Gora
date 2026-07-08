"""DoT/HoT regression scenarios (ticks, ramps, dot healing, HoTs).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import re

from harness import (
    ABILITIES,
    _has_effect,
    _player_states,
    _turn_lines,
    effects,
    make_match,
    submit_turn,
)

from .helpers import (
    _DEF_PASS,
)


def scenario_hunter_wildfire_arcane_proc() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]

    submit_turn(match, "wildfire_bomb", _DEF_PASS)
    arcane_proc = next((fx for fx in hunter.effects if fx.get("id") == "arcane_surge"), None)
    assert arcane_proc is not None, "Wildfire Bomb should grant Arcane Surge"
    assert int(arcane_proc.get("duration", 0) or 0) == 2, "Arcane Surge should be created from a 3-turn duration and tick after the proc turn resolves"
    proc_line = f"{match.players[0][:5]} has Arcane Surge!"
    assert proc_line in match.log, "Wildfire Bomb proc log should use the actor sid token so snapshots can render Hunter(you)"
    assert not any("Wildfire Bomb. has Arcane Surge!" in line or "Wildfire Bomb. Hunter has Arcane Surge!" in line for line in match.log), "Wildfire Bomb action line should not embed the proc sentence"

    submit_turn(match, "arcane_shot", _DEF_PASS)
    assert not _has_effect(hunter, "arcane_surge"), "Arcane Shot should consume its proc"

    match2 = make_match("hunter", "warrior", seed=123)
    hunter2 = match2.state[match2.players[0]]
    submit_turn(match2, "wildfire_bomb", _DEF_PASS)
    arcane_proc_2 = next((fx for fx in hunter2.effects if fx.get("id") == "arcane_surge"), None)
    assert arcane_proc_2 is not None and int(arcane_proc_2.get("duration", 0) or 0) == 2, "Unused Arcane Surge should still be present immediately after the proc turn"
    submit_turn(match2, _DEF_PASS, _DEF_PASS)
    assert _has_effect(hunter2, "arcane_surge"), "Arcane Surge should persist beyond one skipped turn"
    return True


def scenario_hunter_wildfire_dot_log_order() -> bool:
    match = make_match("hunter", "warrior", seed=123)

    submit_turn(match, "wildfire_bomb", _DEF_PASS)
    assert any("Wildfire Bomb applies Wildfire Burn" in line for line in match.log), "Wildfire Bomb should log a named burn application line"
    assert not any("Wildfire Bomb applies Wildfire Burn for" in line for line in match.log), "Wildfire Bomb burn application log should omit the per-turn amount"

    wildfire_idx = next(i for i, line in enumerate(match.log) if "uses their bare hands to cast Wildfire Bomb" in line)
    burn_idx = next(i for i, line in enumerate(match.log) if "Wildfire Bomb applies Wildfire Burn" in line)
    pass_idx = next(i for i, line in enumerate(match.log) if "uses their bare hands to cast Pass Turn" in line)

    assert wildfire_idx < burn_idx < pass_idx, "Wildfire Burn application should log after Wildfire Bomb and before the enemy action"
    return True


def scenario_mass_dispel_removes_same_turn_wildfire_burn() -> bool:
    match = make_match("priest", "hunter", seed=123)
    priest_sid, hunter_sid = match.players

    submit_turn(match, "mass_dispel", "wildfire_bomb")

    priest = match.state[priest_sid]
    assert not _has_effect(priest, "wildfire_burn"), "Mass Dispel should remove Wildfire Burn applied in the same turn"
    assert any("Wildfire Burn" in line and "removed by Mass Dispel" in line for line in match.log), "Same-turn Wildfire removal should be logged by Mass Dispel"
    return True


def scenario_mindgames_still_allows_direct_damage_dots() -> bool:
    dragon_match = make_match("warrior", "priest", seed=123)
    dragon_warrior, dragon_priest = dragon_match.players
    dragon_match.state[dragon_warrior].res.rage = dragon_match.state[dragon_warrior].res.rage_max
    submit_turn(dragon_match, "dragon_roar", "mindgames")
    assert _has_effect(dragon_match.state[dragon_priest], "dragon_roar_bleed"), "Dragon Roar bleed should apply even when Mindgames flips the same-turn direct damage"
    assert any("Mindgames flips damage into" in line for line in dragon_match.log), "Dragon Roar scenario should still record the Mindgames flip"

    wildfire_match = make_match("hunter", "priest", seed=123)
    submit_turn(wildfire_match, "wildfire_bomb", "mindgames")
    assert _has_effect(wildfire_match.state[wildfire_match.players[1]], "wildfire_burn"), "Wildfire Burn should apply even when Mindgames flips the same-turn direct damage"
    assert any("Wildfire Bomb applies Wildfire Burn" in line for line in wildfire_match.log), "Wildfire Bomb should keep its burn application log under Mindgames"
    assert not any("Wildfire Bomb applies Wildfire Burn for" in line for line in wildfire_match.log), "Wildfire Bomb burn application log should still omit the per-turn amount under Mindgames"
    return True


def scenario_devouring_plague_heals_for_full_tick_damage() -> bool:
    match = make_match("priest", "warrior", seed=123)
    priest_sid, warrior_sid = match.players
    priest = match.state[priest_sid]
    warrior = match.state[warrior_sid]

    priest.res.hp = max(1, priest.res.hp - 40)
    effects.apply_effect_by_id(
        warrior,
        "devouring_plague",
        overrides={"duration": 3, "tick_damage": 11, "source_sid": priest_sid},
    )
    hp_before_tick = priest.res.hp
    enemy_before_tick = warrior.res.hp

    submit_turn(match, _DEF_PASS, _DEF_PASS)

    priest_gain = priest.res.hp - hp_before_tick
    warrior_loss = max(0, enemy_before_tick - warrior.res.hp)
    assert warrior_loss > 0, "Devouring Plague should deal DoT damage on tick"
    assert priest_gain == warrior_loss, "Devouring Plague should heal for 100% of tick damage dealt"
    return True


def scenario_agony_ramp_progression_restored() -> bool:
    match = make_match("warlock", "warrior", seed=444)
    warlock_sid, warrior_sid = match.players
    submit_turn(match, "agony", _DEF_PASS)
    turn_1_lines = _turn_lines(match, 1)
    assert not any("suffers" in line and "Agony" in line for line in turn_1_lines), "Agony should not tick on the cast turn"

    observed_ticks: list[int] = []
    for _ in range(10):
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        turn_lines = _turn_lines(match, match.turn)
        agony_line = next((line for line in turn_lines if warrior_sid[:5] in line and "suffers" in line and "Agony" in line), None)
        assert agony_line is not None, "Agony should produce a per-turn visible tick log"
        parsed = re.search(r"suffers (\d+) damage from Agony", agony_line)
        assert parsed is not None, "Agony tick log should include numeric damage"
        observed_ticks.append(int(parsed.group(1)))

    assert observed_ticks[:10] == list(range(1, 11)), "Agony visible ticks should ramp exactly 1..10 across the first 10 ticks"
    assert max(observed_ticks) == 10, "Agony visible ticks should not exceed 10 damage"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    turn_after_last_tick = _turn_lines(match, match.turn)
    assert not any(warrior_sid[:5] in line and "suffers" in line and "Agony" in line for line in turn_after_last_tick), "Agony should expire immediately after its 10-damage tick"

    match.state[warlock_sid].cooldowns["agony"] = []
    match.state[warlock_sid].res.mp = match.state[warlock_sid].res.mp_max
    submit_turn(match, "agony", _DEF_PASS)
    recast_turn = _turn_lines(match, match.turn)
    assert any("uses their bare hands to cast Agony." in line for line in recast_turn), "Warlock should be able to recast Agony once the prior effect expires"
    assert not any("Agony is not stackable." in line for line in recast_turn), "Expired Agony should not block recast"
    return True


def scenario_dot_balance_per_turn_values_and_durations() -> bool:
    checks = [
        ("priest", "vampiric_touch", "vampiric_touch", 0.3, 6, 6),
        ("priest", "devouring_plague", "devouring_plague", 0.4, 4, 7),
        ("warlock", "corruption", "corruption", 0.2, 4, 8),
        ("warlock", "unstable_affliction", "unstable_affliction", 0.3, 6, 10),
    ]
    for caster_class, ability_id, effect_id, scale, die_max, expected_duration in checks:
        match = make_match(caster_class, "warrior", seed=6101)
        caster, target = _player_states(match)
        caster.stats["int"] = 20
        if ability_id == "devouring_plague":
            effects.apply_effect_by_id(caster, "shadowy_insight", overrides={"duration": 2})
        submit_turn(match, ability_id, _DEF_PASS)
        fx = next((effect for effect in target.effects if effect.get("id") == effect_id), None)
        assert fx is not None, f"{ability_id} should apply {effect_id}"
        assert int(ABILITIES[ability_id]["dot"]["duration"]) == expected_duration, f"{ability_id} duration should be {expected_duration}"
        tick_damage = int(fx.get("tick_damage", 0) or 0)
        expected_base = int(caster.stats["int"] * scale)
        roll_component = tick_damage - expected_base
        assert 1 <= roll_component <= die_max, f"{ability_id} tick damage should be Int({scale}x)+d{die_max} per turn"
    return True


def scenario_shaman_healing_stream_hot() -> bool:
    match = make_match("shaman", "warrior", seed=7002)
    shaman_sid, enemy_sid = match.players
    shaman = match.state[shaman_sid]
    enemy = match.state[enemy_sid]
    shaman.res.hp = 50
    shaman.stats["acc"] = 999
    enemy.stats["eva"] = 0
    submit_turn(match, "healing_stream", _DEF_PASS)
    hp_after_cast = shaman.res.hp
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert shaman.res.hp > hp_after_cast, "Healing Stream should heal over time"
    return True
