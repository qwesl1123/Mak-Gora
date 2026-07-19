"""DoT/HoT regression scenarios (ticks, ramps, dot healing, HoTs).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import re

from harness import (
    ABILITIES,
    PET_AI,
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
    _active_pet,
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
    priest = dragon_match.state[dragon_priest]
    assert priest.res.hp == priest.res.hp_max, "Setup: the flip target should start at full HP"
    submit_turn(dragon_match, "dragon_roar", "mindgames")
    assert _has_effect(dragon_match.state[dragon_priest], "dragon_roar_bleed"), "Dragon Roar bleed should apply even when Mindgames flips the same-turn direct damage"
    assert any("Mindgames flips damage into" in line for line in dragon_match.log), "Dragon Roar scenario should still record the Mindgames flip"
    # DamageApplicationResult["mindgames_healing"] carries the nominal converted
    # damage, not actual HP restored. Direct-DoT application uses that nominal
    # value as evidence the source hit resolved, which is why the bleed above
    # applies even though the full-HP target actually gains 0 HP from the flip.
    flip_amounts = [
        int(m.group(1))
        for line in dragon_match.log
        for m in [re.search(r"Mindgames flips damage into (\d+) healing for the target\.", line)]
        if m
    ]
    assert flip_amounts and all(amount > 0 for amount in flip_amounts), "Flip logs should keep reporting the positive nominal converted amount"
    assert priest.res.hp == priest.res.hp_max, "A full-HP flip target gains no actual HP even though the nominal converted amount is positive"

    wildfire_match = make_match("hunter", "priest", seed=123)
    wildfire_priest = wildfire_match.state[wildfire_match.players[1]]
    assert wildfire_priest.res.hp == wildfire_priest.res.hp_max, "Setup: the flip target should start at full HP"
    submit_turn(wildfire_match, "wildfire_bomb", "mindgames")
    assert _has_effect(wildfire_match.state[wildfire_match.players[1]], "wildfire_burn"), "Wildfire Burn should apply even when Mindgames flips the same-turn direct damage"
    assert any("Wildfire Bomb applies Wildfire Burn" in line for line in wildfire_match.log), "Wildfire Bomb should keep its burn application log under Mindgames"
    assert not any("Wildfire Bomb applies Wildfire Burn for" in line for line in wildfire_match.log), "Wildfire Bomb burn application log should still omit the per-turn amount under Mindgames"
    assert wildfire_priest.res.hp == wildfire_priest.res.hp_max, "The full-HP Wildfire flip target should also gain no actual HP"
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


def scenario_end_of_turn_healing_applies_before_queued_dot_damage() -> bool:
    """End-of-turn item/effect healing keeps its position in the stage order.

    Healing mutates HP before the same champion's queued DoT damage is applied,
    and both resolve before the winner check.
    """
    # Near-cap: the item heal caps at hp_max first, then the queued DoT damage
    # applies to the capped value. Applying the DoT first would end the turn at
    # hp_max - 7 instead of hp_max - 10.
    cap_match = make_match("warrior", "priest", p1_items={"weapon": "staff_of_immortality"}, seed=6504)
    warrior_sid, priest_sid = cap_match.players
    warrior = cap_match.state[warrior_sid]
    warrior.stats["def"] = 0
    warrior.stats["magic_resist"] = 0
    warrior.res.hp = warrior.res.hp_max - 1
    effects.apply_effect_by_id(warrior, "burn", overrides={"duration": 2, "tick_damage": 10, "source_sid": priest_sid})

    submit_turn(cap_match, _DEF_PASS, _DEF_PASS)

    assert warrior.res.hp == warrior.res.hp_max - 10, "End-of-turn item healing must apply (and cap at hp_max) before queued DoT damage lands"
    assert cap_match.combat_totals[warrior_sid]["healing"] == 1, "End-of-turn healing totals must credit the actual capped HP delta"
    turn_lines = _turn_lines(cap_match, 1)
    # The item heal log keeps its current requested-amount wording (4 HP) even
    # though only 1 HP fit below hp_max; do not standardize this divergence.
    heal_idx = next(i for i, line in enumerate(turn_lines) if "heals 4 HP from Staff of Immortality." in line)
    dot_idx = next(i for i, line in enumerate(turn_lines) if "suffers 10 damage from Burn." in line)
    assert heal_idx < dot_idx, "Item heal log should keep its position before the queued DoT application log"

    # Rescue: a champion whose queued DoT alone would be lethal survives
    # because same-stage healing applies first and the winner check runs last.
    rescue_match = make_match("warrior", "priest", p1_items={"weapon": "staff_of_immortality"}, seed=6505)
    warrior_sid, priest_sid = rescue_match.players
    warrior = rescue_match.state[warrior_sid]
    warrior.stats["def"] = 0
    warrior.stats["magic_resist"] = 0
    warrior.res.hp = 2
    effects.apply_effect_by_id(warrior, "burn", overrides={"duration": 2, "tick_damage": 3, "source_sid": priest_sid})

    submit_turn(rescue_match, _DEF_PASS, _DEF_PASS)

    assert warrior.res.hp == 2 + 4 - 3, "End-of-turn healing then queued DoT damage should leave the champion at exactly 3 HP"
    assert rescue_match.phase != "ended", "A champion healed above the queued DoT total must survive the end-of-turn winner check"
    return True


def scenario_passive_and_end_of_turn_player_healing_routes_through_shared_helper() -> bool:
    """Passive and end-of-turn player healing applies HP through effects.apply_player_healing().

    Migrated paths: item heal_on_hit (Thunderfury), end-of-turn item heal_self
    (Staff of Immortality), effect/HoT regeneration (Healing Stream), Ancestral
    Knowledge, and Emerald Serpent Lightning Breath owner healing. This
    completes migration of the known production player-healing application
    sites. Preserved caller-owned policies: Mindgames-twisted HoT ticks convert
    to queued self-damage without any normal healing call, Ancestral Knowledge
    heals through the helper with no liveness gate (its Cyclone/Mindgames and
    zero/negative-HP rules are pinned in the dedicated Ancestral Knowledge
    scenarios), item and effect logs keep reporting the requested amount
    rather than the actual capped gain, and pet HP stays locally clamped
    outside the player-only helper.
    """
    original = effects.apply_player_healing
    assert resolver.apply_player_healing is original, "resolver should share the effects.apply_player_healing primitive"
    assert PET_AI.apply_player_healing is original, "pet_ai should share the effects.apply_player_healing primitive"
    calls: list[tuple[object, int, int]] = []

    def spy(target, amount):
        gained = original(target, amount)
        calls.append((target, int(amount), int(gained)))
        return gained

    # Control run captured before patching: the spied Thunderfury match below
    # replays the same seed and must produce a byte-identical log, proving the
    # spy (and the migration) changed no RNG order or damage behavior.
    control = make_match("warrior", "priest", p1_items={"weapon": "thunderfury"}, seed=5)
    control_warrior = control.state[control.players[0]]
    control_warrior.res.hp = control_warrior.res.hp_max - 1
    submit_turn(control, "overpower", _DEF_PASS)

    effects.apply_player_healing = spy
    resolver.apply_player_healing = spy
    PET_AI.apply_player_healing = spy
    try:
        # Item heal_on_hit (Thunderfury) near cap: the helper receives the
        # passive's rolled request, only 1 HP fits below hp_max, bonus_healing/
        # totals use the actual gain, and the log keeps the requested wording.
        proc = make_match("warrior", "priest", p1_items={"weapon": "thunderfury"}, seed=5)
        proc_warrior_sid, _ = proc.players
        proc_warrior = proc.state[proc_warrior_sid]
        proc_warrior.res.hp = proc_warrior.res.hp_max - 1
        calls.clear()
        submit_turn(proc, "overpower", _DEF_PASS)
        heal_line = next(line for line in _turn_lines(proc, 1) if "draws strength from Thunderfury" in line)
        requested = int(re.search(r"healing (\d+) HP\.", heal_line).group(1))
        assert requested > 1, "Setup: the rolled heal request must exceed the 1 missing HP to distinguish requested from actual"
        assert calls == [(proc_warrior, requested, 1)], "heal_on_hit should route the passive's rolled request through exactly one helper call and gain only the capped 1 HP"
        assert proc_warrior.res.hp == proc_warrior.res.hp_max, "The near-cap proc should top the attacker off at hp_max"
        assert proc.combat_totals[proc_warrior_sid]["healing"] == 1, "bonus_healing/combat totals must credit the actual gain, not the request"
        assert proc.log == control.log, "The spied run must replay the control run byte-identically (RNG order and damage unchanged)"

        # End-of-turn item heal_self (Staff of Immortality) near cap: helper
        # receives the requested 4, returns the 1 HP that fit, the log keeps the
        # requested wording, and healing still lands before queued DoT damage.
        item = make_match("warrior", "priest", p1_items={"weapon": "staff_of_immortality"}, seed=6504)
        item_warrior_sid, item_priest_sid = item.players
        item_warrior = item.state[item_warrior_sid]
        item_warrior.stats["def"] = 0
        item_warrior.stats["magic_resist"] = 0
        item_warrior.res.hp = item_warrior.res.hp_max - 1
        effects.apply_effect_by_id(item_warrior, "burn", overrides={"duration": 2, "tick_damage": 10, "source_sid": item_priest_sid})
        calls.clear()
        submit_turn(item, _DEF_PASS, _DEF_PASS)
        assert calls == [(item_warrior, 4, 1)], "heal_self should request the item's 4 HP through the helper and gain only the capped 1 HP"
        assert item_warrior.res.hp == item_warrior.res.hp_max - 10, "The capped heal must still land before the queued 10-damage DoT"
        assert item.combat_totals[item_warrior_sid]["healing"] == 1, "end_summary healing totals must credit the actual gain"
        item_lines = _turn_lines(item, 1)
        heal_idx = next(i for i, line in enumerate(item_lines) if "heals 4 HP from Staff of Immortality." in line)
        dot_idx = next(i for i, line in enumerate(item_lines) if "suffers 10 damage from Burn." in line)
        assert heal_idx < dot_idx, "The requested-amount heal log should keep its position before the queued DoT log"

        # Normal effect/HoT regeneration (Healing Stream): the tick routes its
        # regen["hp"] request through the helper; recovery log and duration
        # semantics are unchanged.
        hot = make_match("shaman", "warrior", seed=6602)
        hot_shaman_sid, _ = hot.players
        hot_shaman = hot.state[hot_shaman_sid]
        hot_shaman.res.hp = max(1, hot_shaman.res.hp - 40)
        submit_turn(hot, "healing_stream", _DEF_PASS)
        stream = next(fx for fx in hot_shaman.effects if fx.get("id") == "healing_stream")
        hot_request = int((stream.get("regen") or {}).get("hp") or 0)
        assert hot_request > 0, "Setup: Healing Stream should carry a positive per-tick regen"
        duration_before = int(stream.get("duration") or 0)
        healing_before = int(hot.combat_totals[hot_shaman_sid]["healing"])
        calls.clear()
        submit_turn(hot, _DEF_PASS, _DEF_PASS)
        assert calls == [(hot_shaman, hot_request, hot_request)], "The HoT tick should route exactly regen['hp'] through one helper call"
        assert any(f"recovers {hot_request} HP from Healing Stream." in line for line in _turn_lines(hot, 2)), "Recovery log wording (requested amount) should be unchanged"
        assert int(hot.combat_totals[hot_shaman_sid]["healing"]) == healing_before + hot_request, "Healing totals must credit the actual gained HP"
        stream_after = next(fx for fx in hot_shaman.effects if fx.get("id") == "healing_stream")
        assert int(stream_after.get("duration") or 0) == duration_before - 1, "HoT duration/tick semantics must be unchanged"

        # Mindgames-twisted HoT tick: no normal healing-helper call; the
        # requested pre-clamp regen becomes queued self-damage (the shaman is at
        # full HP, so a clamped conversion would have been 0), with the exact
        # twist log and no healing-total credit.
        twisted = make_match("shaman", "priest", seed=6601)
        tw_shaman_sid, _tw_priest_sid = twisted.players
        tw_shaman = twisted.state[tw_shaman_sid]
        submit_turn(twisted, "healing_stream", _DEF_PASS)
        tw_stream = next(fx for fx in tw_shaman.effects if fx.get("id") == "healing_stream")
        tw_request = int((tw_stream.get("regen") or {}).get("hp") or 0)
        assert tw_request > 0, "Setup: the twisted tick needs a positive regen request"
        assert tw_shaman.res.hp == tw_shaman.res.hp_max, "Setup: a full-HP shaman proves the twist converts the pre-clamp request"
        tw_shaman.stats["def"] = 0
        tw_shaman.stats["magic_resist"] = 0
        tw_hp_before = tw_shaman.res.hp
        tw_healing_before = int(twisted.combat_totals[tw_shaman_sid]["healing"])
        calls.clear()
        submit_turn(twisted, _DEF_PASS, "mindgames")
        assert calls == [], "A Mindgames-twisted HoT tick must not call the normal healing helper"
        assert tw_shaman.res.hp == tw_hp_before - tw_request, "The queued self-damage must equal the requested pre-clamp regen amount"
        assert any(
            line == f"{tw_shaman_sid[:5]} is twisted by Mindgames and takes {tw_request} self-damage instead of healing from Healing Stream."
            for line in _turn_lines(twisted, 2)
        ), "Mindgames twist log wording must be unchanged"
        assert not any(f"recovers {tw_request} HP from Healing Stream." in line for line in _turn_lines(twisted, 2)), "No normal recovery log should appear for the twisted tick"
        assert int(twisted.combat_totals[tw_shaman_sid]["healing"]) == tw_healing_before, "Healing totals must not increase for a twisted tick"

        # Ancestral Knowledge: the shaman's end-of-turn heal requests exactly 3%
        # of hp_max through the helper; healing/Intellect logs and totals are
        # unchanged.
        ak = make_match("shaman", "warrior", seed=6603)
        ak_shaman_sid, _ = ak.players
        ak_shaman = ak.state[ak_shaman_sid]
        effects.add_absorb(ak_shaman, 10, source_name="Test Shield", effect_id="power_word_shield")
        ak_shaman.res.hp = max(1, ak_shaman.res.hp - 20)
        ak_request = int(ak_shaman.res.hp_max * 0.03)
        assert ak_request > 0, "Setup: 3% of hp_max must be a positive heal request"
        ak_int_before = int(ak_shaman.stats["int"])
        ak_int_gain = max(1, int(ak_int_before * 0.03))
        calls.clear()
        submit_turn(ak, _DEF_PASS, _DEF_PASS)
        assert calls == [(ak_shaman, ak_request, ak_request)], "Ancestral Knowledge should route exactly int(hp_max * 0.03) through one helper call targeting the shaman"
        assert ak.combat_totals[ak_shaman_sid]["healing"] == ak_request, "Ancestral Knowledge totals should credit the actual gain"
        ak_lines = _turn_lines(ak, 1)
        assert any(f"restores {ak_request} HP from Ancestral Knowledge." in line for line in ak_lines), "Ancestral Knowledge healing log wording must be unchanged"
        assert ak_shaman.stats["int"] == ak_int_before + ak_int_gain, "Ancestral Knowledge Intellect gain must be unchanged"
        assert any(f"gains +{ak_int_gain} Intellect from Ancestral Knowledge." in line for line in ak_lines), "Ancestral Knowledge Intellect log wording must be unchanged"

        # Ancestral Knowledge zero-HP recovery: the former hp > 0 liveness
        # gate is removed, so a shaman at exactly zero HP with an absorb heals
        # through the helper before the end-of-turn winner check.
        downed = make_match("shaman", "warrior", seed=6604)
        downed_shaman_sid, _ = downed.players
        downed_shaman = downed.state[downed_shaman_sid]
        effects.add_absorb(downed_shaman, 10, source_name="Test Shield", effect_id="power_word_shield")
        downed_shaman.res.hp = 0
        downed_request = int(downed_shaman.res.hp_max * 0.03)
        assert downed_request > 0, "Setup: 3% of hp_max must be a positive heal request"
        calls.clear()
        submit_turn(downed, _DEF_PASS, _DEF_PASS)
        assert calls == [(downed_shaman, downed_request, downed_request)], "Ancestral Knowledge must route the request through the helper even at zero HP (no hp > 0 gate)"
        assert downed_shaman.res.hp == downed_request, "A shaman at exactly zero HP must recover through Ancestral Knowledge"
        assert downed.phase != "ended", "A shaman healed above zero must survive the end-of-turn winner check"
        assert any(f"restores {downed_request} HP from Ancestral Knowledge." in line for line in _turn_lines(downed, 1)), "The actual-gained Ancestral Knowledge healing log should appear for the zero-HP recovery"

        # Emerald Serpent Lightning Breath: exactly one player-healing call for
        # the owner requesting actual_damage // 2; the pet heals through its own
        # local clamp (capping at hp_max), the log reports both actual gains,
        # and the owner's totals credit both exactly once.
        serpent_match = make_match("hunter", "warrior", seed=1)
        hunter_sid, serpent_warrior_sid = serpent_match.players
        hunter = serpent_match.state[hunter_sid]
        serpent_warrior = serpent_match.state[serpent_warrior_sid]
        submit_turn(serpent_match, "call_serpent", _DEF_PASS)
        serpent = _active_pet(hunter, "emerald_serpent")
        assert serpent is not None, "Setup: Emerald Serpent should be active"
        serpent.hp = serpent.hp_max - 2
        hunter.res.hp -= 20
        hunter.pending_pet_command = "special"
        hunter_hp_before = hunter.res.hp
        warrior_hp_before = serpent_warrior.res.hp
        totals_before = {key: int(value) for key, value in serpent_match.combat_totals[hunter_sid].items()}
        calls.clear()
        submit_turn(serpent_match, _DEF_PASS, _DEF_PASS)
        dealt = warrior_hp_before - serpent_warrior.res.hp
        assert dealt > 0 and any("breathes lightning" in line for line in _turn_lines(serpent_match, 2)), "Setup: seed 1 should land a forced Lightning Breath"
        breath_heal = dealt // 2
        assert breath_heal > 2, "Setup: the owner heal must exceed the pet's 2-HP deficit to distinguish the two clamps"
        assert calls == [(hunter, breath_heal, breath_heal)], "Lightning Breath should route only the owner heal (actual_damage // 2) through one helper call"
        assert hunter.res.hp == hunter_hp_before + breath_heal, "The owner should gain the full heal below cap"
        assert serpent.hp == serpent.hp_max, "The pet should heal through its local clamp up to hp_max, outside the helper"
        assert any(
            line == f"Emerald Serpent restores 2 HP to itself and {breath_heal} HP to {hunter_sid[:5]}."
            for line in _turn_lines(serpent_match, 2)
        ), "Lightning Breath log must keep reporting the actual pet and owner gains"
        assert int(serpent_match.combat_totals[hunter_sid]["damage"]) == totals_before["damage"] + dealt, "Damage totals must be unchanged by the migration"
        assert int(serpent_match.combat_totals[hunter_sid]["healing"]) == totals_before["healing"] + 2 + breath_heal, "Healing totals must credit actual pet gain + actual owner gain exactly once"
    finally:
        effects.apply_player_healing = original
        resolver.apply_player_healing = original
        PET_AI.apply_player_healing = original
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
