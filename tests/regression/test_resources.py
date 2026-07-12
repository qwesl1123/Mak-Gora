"""Resource cost/gain/regen regression scenarios.

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import re

from harness import (
    ABILITIES,
    PET_AI,
    PetState,
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


def scenario_on_hit_resource_gain_log_uses_actual_gained() -> bool:
    # ------------------------------------------------------------------
    # The on_hit_resource_gains combat log must report the amount actually
    # gained through grant_resource (Challenger Wrath boost + cap), not the
    # static ability text (e.g. Aimed Shot's "restores 5 mana.").
    # ------------------------------------------------------------------

    # Source-of-truth math: Challenger Wrath turns a base-5 mana gain into 6.
    unit = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6220)
    unit_hunter = unit.state[unit.players[0]]
    unit_hunter.res.mp = unit_hunter.res.mp_max // 2  # 50% active resource -> Wrath
    assert effects.challenger_resource_stance_mode(unit_hunter) == "wrath", \
        "Low-resource hunter should be in Challenger Wrath"
    unit_hunter.res.mp = 0
    assert effects.grant_player_resource(unit_hunter, "mp", 5, challenger_mode="wrath") == 6, \
        "Challenger Wrath should turn a base-5 mana gain into an actual 6"

    # Full turn: a low-resource Challenger hunter procs Aimed Shot's on-hit mana
    # gain and the log must report the boosted 6, never the static 5.
    match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=3)
    hunter = match.state[match.players[0]]
    hunter.res.mp = hunter.res.mp_max // 2
    submit_turn(match, "aimed_shot", "die_by_the_sword")
    turn_lines = _turn_lines(match, 1)
    assert any("restores 6 mana." in line for line in turn_lines), \
        "Aimed Shot on-hit gain should log the Challenger-boosted amount (6)"
    assert not any("restores 5 mana." in line for line in turn_lines), \
        "Combat log must not report the static base amount (5) under Challenger Wrath"

    # Near-cap: when the adjusted amount would overflow the pool, the log must
    # follow the actual capped gain, not the pre-cap adjusted amount. Only Aimed
    # Shot uses on_hit_resource_gains and its cost draws from the same mana pool,
    # so temporarily widen the base gain to force the cap to bind.
    entry = ABILITIES["aimed_shot"]["on_hit_resource_gains"][0]
    original_amount = entry["amount"]
    try:
        entry["amount"] = 40  # int(40 * 1.30) == 52 adjusted, far above the mana headroom
        adjusted = int(40 * 1.30)
        cap_match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=3)
        cap_hunter = cap_match.state[cap_match.players[0]]
        cap_hunter.res.mp = cap_hunter.res.mp_max // 2
        submit_turn(cap_match, "aimed_shot", "die_by_the_sword")
        cap_lines = _turn_lines(cap_match, 1)
        assert cap_hunter.res.mp == cap_hunter.res.mp_max, \
            "Near-cap gain should fill the hunter's mana to its maximum"
        mana_logs = [line for line in cap_lines if re.search(r"restores \d+ mana\.", line)]
        assert mana_logs, "Near-cap Aimed Shot should still log a mana restore"
        logged = int(re.search(r"restores (\d+) mana\.", mana_logs[0]).group(1))
        assert 0 < logged < adjusted, \
            "Near-cap log should report the capped gain, strictly below the adjusted amount"
        assert not any("restores 40 mana." in line or f"restores {adjusted} mana." in line for line in cap_lines), \
            "Near-cap log must not report the base (40) or pre-cap adjusted (52) amounts"
        assert not any("restores 5 mana." in line for line in cap_lines), \
            "Near-cap log must not fall back to the static base text (5)"
    finally:
        entry["amount"] = original_amount

    return True


def scenario_queued_proc_resource_gain_uses_actual_dealt() -> bool:
    # ------------------------------------------------------------------
    # P2 fix: damage-based resource gains (e.g. Overpower's rage: damage) must be
    # credited from the ACTUAL HP dealt after each queued on-hit proc has landed
    # against its final target — not from the speculative pre-mitigation value the
    # proc was originally computed against. Raw queued proc events therefore no
    # longer contribute their speculative reduced value to bonus_damage; instead
    # the resolver defers damage-based resource gains to the post-damage stage and
    # sums the real dealt amount (post redirect / absorb / immunity / Mindgames).
    # ------------------------------------------------------------------
    lightning_passive = {
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

    def _overpower_override():
        # Deterministic single physical strike carrying rage: damage.
        original = dict(ABILITIES["overpower"])
        ABILITIES["overpower"] = dict(
            original, dice=None, scaling={}, flat_damage=20, cannot_miss=True, resource_gain={"rage": "damage"}
        )
        return original

    # -- Case 1: redirect to a surviving boar --------------------------------
    # The direct hit and the lightning proc both redirect to the Hunter's boar via
    # Blocking Defence. Rage must equal the ACTUAL damage dealt to the boar, and
    # must differ from the champion-mitigated value (proving it is not speculative).
    def redirect_run(redirect: bool) -> tuple[int, int, int, bool]:
        match = make_match("hunter", "warrior", seed=6212)
        hunter_sid, warrior_sid = match.players
        hunter = match.state[hunter_sid]
        warrior = match.state[warrior_sid]
        submit_turn(match, "call_boar", _DEF_PASS)
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Boar should be active for redirect coverage"
        # Divergent mitigation so champion vs boar damage differ meaningfully.
        hunter.stats["def"] = 40
        hunter.stats["magic_resist"] = 30
        if redirect:
            effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
        warrior.stats.update({"atk": 20, "crit": 0, "acc": 999})
        warrior.res.rage = 0
        warrior.effects.append(dict(lightning_passive, passive=dict(lightning_passive["passive"])))
        original = _overpower_override()
        try:
            hunter_hp0 = hunter.res.hp
            boar_hp0 = boar.hp
            submit_turn(match, _DEF_PASS, "overpower")
        finally:
            ABILITIES["overpower"] = original
        return warrior.res.rage, hunter_hp0 - hunter.res.hp, boar_hp0 - boar.hp, boar.hp > 0

    redirect_rage, redirect_hunter_dmg, redirect_boar_dmg, boar_alive = redirect_run(redirect=True)
    champion_rage, champion_hunter_dmg, _, _ = redirect_run(redirect=False)
    assert boar_alive, "Case 1 requires the boar to survive so real boar damage is observable"
    assert redirect_hunter_dmg == 0, "Blocking Defence should redirect the whole hit away from the Hunter"
    assert redirect_boar_dmg > 0, "Redirected hit and proc should deal real damage to the boar"
    assert redirect_rage == redirect_boar_dmg, "Rage must be credited from ACTUAL damage dealt to the boar"
    assert champion_hunter_dmg == champion_rage and champion_rage > 0, "Control run: rage tracks direct champion damage"
    assert redirect_rage != champion_rage, "Redirected rage must differ from the champion-mitigated value (not speculative)"

    # -- Case 2: direct hit kills the boar, proc falls through to the Hunter ---
    # The boar dies to the direct strike, so the queued lightning proc falls through
    # to the Hunter and must be mitigated with the Hunter's START-OF-TURN Challenger
    # stance. Wrath (start-of-turn) increases incoming, Might reduces it; the rage
    # difference must equal exactly the proc-damage difference.
    def fallthrough_run(hunter_start_mp: int) -> tuple[int, int, int]:
        match = make_match("hunter", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6212)
        hunter_sid, warrior_sid = match.players
        hunter = match.state[hunter_sid]
        warrior = match.state[warrior_sid]
        submit_turn(match, "call_boar", _DEF_PASS)
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Boar should be active for fall-through coverage"
        boar.hp = 1  # dies to the direct strike before the proc resolves
        hunter.stats["def"] = 0
        hunter.stats["magic_resist"] = 0
        hunter.res.mp = hunter_start_mp
        effects.apply_effect_by_id(hunter, "blocking_defence", overrides={"duration": 1, "redirect_to_pet_id": boar.id})
        warrior.stats.update({"atk": 20, "crit": 0, "acc": 999})
        warrior.res.rage = 0
        warrior.effects.append(dict(lightning_passive, passive=dict(lightning_passive["passive"])))
        original = _overpower_override()
        try:
            hunter_hp0 = hunter.res.hp
            submit_turn(match, _DEF_PASS, "overpower")
        finally:
            ABILITIES["overpower"] = original
        proc_line = next((line for line in match.log if "lightning from Test Thunderfury" in line), "")
        parsed = re.search(r"Deals (\d+) magic damage", proc_line)
        assert parsed is not None, "Fall-through proc should log its incoming damage against the Hunter"
        assert boar.hp <= 0, "Case 2 requires the direct hit to kill the boar"
        return warrior.res.rage, hunter_hp0 - hunter.res.hp, int(parsed.group(1))

    hunter_mp_max = make_match("hunter", "warrior", seed=6212).state["p1_sid"].res.mp_max
    wrath_rage, wrath_hunter_dmg, wrath_proc = fallthrough_run(hunter_start_mp=hunter_mp_max // 2)  # start-of-turn Wrath
    might_rage, might_hunter_dmg, might_proc = fallthrough_run(hunter_start_mp=hunter_mp_max)  # start-of-turn Might
    assert wrath_hunter_dmg == wrath_proc and might_hunter_dmg == might_proc, "Proc must fall through and hit the Hunter"
    assert wrath_proc > might_proc, "Start-of-turn Wrath must increase incoming proc damage relative to Might"
    assert wrath_rage - might_rage == wrath_proc - might_proc, (
        "Rage from the fall-through proc must track the Hunter's start-of-turn Challenger-mitigated damage"
    )

    # -- Cases 3 & 4: absorb / immunity zero out the proc; strike_again once ---
    def cast_vs_target(*, absorb: int = 0, immune_magic: bool = False, passive: dict | None = None) -> tuple[int, int, list[str]]:
        passive = passive if passive is not None else lightning_passive
        match = make_match("warrior", "warrior", seed=6212)
        warrior_sid, target_sid = match.players
        warrior = match.state[warrior_sid]
        target = match.state[target_sid]
        warrior.stats.update({"atk": 20, "crit": 0, "acc": 999})
        warrior.res.rage = 0
        target.stats.update({"def": 0, "magic_resist": 0})
        warrior.effects.append(dict(passive, passive=dict(passive["passive"])))
        if absorb:
            effects.add_absorb(target, absorb, source_name="Power Word: Shield", effect_id="power_word_shield")
        if immune_magic:
            target.effects.append({"id": "test_immune_magic", "name": "Test Magic Immunity", "flags": {"immune_magic": True}})
        original = _overpower_override()
        try:
            target_hp0 = target.res.hp
            submit_turn(match, "overpower", _DEF_PASS)
        finally:
            ABILITIES["overpower"] = original
        proc_lines = [line for line in match.log if "lightning from Test Thunderfury" in line]
        return warrior.res.rage, target_hp0 - target.res.hp, proc_lines

    baseline_rage, baseline_dmg, baseline_proc_lines = cast_vs_target()
    assert baseline_rage == baseline_dmg > 0, "Baseline: rage equals total actual damage (direct + proc)"
    assert baseline_proc_lines, "Baseline lightning proc should fire"

    absorbed_rage, absorbed_dmg, absorbed_proc_lines = cast_vs_target(absorb=999)
    assert absorbed_rage == 0 and absorbed_dmg == 0, "Fully absorbed hit + proc must contribute 0 resource gain"
    assert absorbed_proc_lines and "absorbed by Power Word: Shield" in absorbed_proc_lines[0], (
        "Proc event must still fire and be absorbed (not silently skipped)"
    )

    immune_rage, immune_dmg, immune_proc_lines = cast_vs_target(immune_magic=True)
    assert immune_rage == immune_dmg and immune_dmg > 0, "Immune target: rage equals direct physical damage only"
    assert immune_rage < baseline_rage, "Immuned magic proc must contribute 0 extra resource gain"

    strike_passive = {
        "type": "item_passive",
        "source_item": "Test Strike Blade",
        "passive": {"type": "strike_again", "trigger": "on_hit", "chance": 1.0, "multiplier": 0.5},
    }
    strike_rage, strike_dmg, _ = cast_vs_target(passive=strike_passive)
    assert strike_rage == strike_dmg, "Strike-again resource gain must equal total actual damage (counted exactly once)"
    assert strike_rage == 30, "Strike-again (20 direct + 10 strike) must be counted once, not doubled"

    # -- Case 5: Dragonwrath-style multi-hit duplicate ------------------------
    # The duplicate log must render real per-hit numbers, and the damage-based
    # resource gain must increase by exactly the duplicate's actual dealt damage.
    duplicate_passive = {
        "type": "item_passive",
        "source_item": "Dragonwrath",
        "passive": {"type": "duplicate_offensive_spell", "trigger": "on_hit", "chance": 1.0},
    }

    def cast_barrage(with_dragonwrath: bool) -> tuple[int, int, str | None]:
        match = make_match("mage", "priest", seed=6212)
        mage_sid, target_sid = match.players
        mage = match.state[mage_sid]
        target = match.state[target_sid]
        mage.stats.update({"int": 6, "crit": 0, "acc": 999})
        target.stats.update({"def": 0, "magic_resist": 0})
        target.res.hp = target.res.hp_max
        if with_dragonwrath:
            mage.effects.append(dict(duplicate_passive, passive=dict(duplicate_passive["passive"])))
        original = dict(ABILITIES["arcane_barrage"])
        # Exactly enough mana to cast so the gain is not clamped by the cap.
        mage.res.mp = int(original.get("cost", {}).get("mp", 0) or 0)
        try:
            ABILITIES["arcane_barrage"] = dict(
                original, dice=None, scaling={"int": 1.0}, hits=3, cannot_miss=True, resource_gain={"mp": "damage"}
            )
            target_hp0 = target.res.hp
            submit_turn(match, "arcane_barrage", _DEF_PASS)
        finally:
            ABILITIES["arcane_barrage"] = original
        duplicate_line = next((line for line in match.log if "duplicates Arcane Barrage!" in line), None)
        return mage.res.mp, target_hp0 - target.res.hp, duplicate_line

    plain_mp, plain_dmg, _ = cast_barrage(with_dragonwrath=False)
    dragon_mp, dragon_dmg, duplicate_line = cast_barrage(with_dragonwrath=True)
    assert duplicate_line is not None, "Deterministic Dragonwrath duplicate should fire"
    assert "__DMG_" not in duplicate_line, "Multi-hit duplicate log must render real damage numbers, not placeholders"
    assert "Hit 1: Deals 6 damage." in duplicate_line and "Hit 3: Deals 6 damage." in duplicate_line, (
        "Duplicate multi-hit log should show each real per-hit value"
    )
    duplicate_damage = dragon_dmg - plain_dmg
    assert duplicate_damage > 0, "Dragonwrath duplicate should add real HP damage"
    assert dragon_mp - plain_mp == duplicate_damage, (
        "Damage-based resource gain must increase by exactly the duplicate's actual dealt damage"
    )
    return True


def scenario_recover_log_shows_only_nonzero_resources_and_uses_mana_wording() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter_sid = match.players[0]
    hunter = match.state[hunter_sid]

    hunter.res.mp = 0
    hunter.res.energy = max(0, hunter.res.energy - 30)
    submit_turn(match, "turtle", _DEF_PASS)

    latest_turn = _turn_lines(match, 1)
    expected = f"{hunter_sid[:5]} recovers 10 Mana from Aspect of the Turtle."
    assert expected in latest_turn, "Recovery log should only include nonzero recovered resources"
    assert not any(" MP" in line for line in latest_turn), "Recovery log should use Mana instead of MP"
    assert not any("0 HP" in line or "0 Energy" in line for line in latest_turn), "Recovery log should omit zero-value recoveries"
    return True


def scenario_cleave_hits_all_targets_and_grants_rage_from_total_dealt() -> bool:
    match = make_match("warrior", "warlock", seed=6102)
    warrior_sid, warlock_sid = match.players
    warrior = match.state[warrior_sid]
    warlock = match.state[warlock_sid]
    warrior.res.rage = 0
    warrior.stats["atk"] = 20
    warrior.stats["acc"] = 999
    warlock.pets["p2_imp_1"] = PetState(id="p2_imp_1", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)
    warlock.pets["p2_imp_2"] = PetState(id="p2_imp_2", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)

    champion_hp_before = warlock.res.hp
    imp1_hp_before = warlock.pets["p2_imp_1"].hp
    imp2_hp_before = warlock.pets["p2_imp_2"].hp

    submit_turn(match, "cleave", _DEF_PASS)

    dealt_total = (champion_hp_before - warlock.res.hp) + (imp1_hp_before - warlock.pets["p2_imp_1"].hp) + (imp2_hp_before - warlock.pets["p2_imp_2"].hp)
    assert dealt_total > 0, "Cleave should deal damage to at least one valid AoE target"
    assert warlock.res.hp < champion_hp_before, "Cleave should hit the champion target"
    assert warlock.pets["p2_imp_1"].hp < imp1_hp_before and warlock.pets["p2_imp_2"].hp < imp2_hp_before, "Cleave should fan out to valid enemy pets"
    assert warrior.res.rage == dealt_total, "Cleave should grant rage equal to total damage dealt across all valid targets"
    return True


def scenario_spirit_mana_regen_formula_and_class_baselines() -> bool:
    mage_match = make_match("mage", "warrior", seed=9101)
    shaman_match = make_match("shaman", "warrior", seed=9102)
    paladin_match = make_match("paladin", "warrior", seed=9103)
    priest_match = make_match("priest", "warrior", seed=9104)
    custom_match = make_match("mage", "warrior", seed=9105)
    assert effects.mana_regen_from_spirit(mage_match.state[mage_match.players[0]]) == 0, "Mage should have 0 Spirit regen"
    assert effects.mana_regen_from_spirit(shaman_match.state[shaman_match.players[0]]) == 1, "Shaman should have 1 Spirit regen"
    assert effects.mana_regen_from_spirit(paladin_match.state[paladin_match.players[0]]) == 2, "Paladin should have 2 Spirit regen"
    assert effects.mana_regen_from_spirit(priest_match.state[priest_match.players[0]]) == 4, "Priest should have 4 Spirit regen"
    custom = custom_match.state[custom_match.players[0]]
    custom.stats["spirit"] = 0
    assert effects.mana_regen_from_spirit(custom) == 0, "0 Spirit should produce 0 mana regen"
    custom.stats["spirit"] = 5
    assert effects.mana_regen_from_spirit(custom) == 1, "5 Spirit should produce 1 mana regen"
    custom.stats["spirit"] = 10
    assert effects.mana_regen_from_spirit(custom) == 2, "10 Spirit should produce 2 mana regen"
    custom.stats["spirit"] = 20
    assert effects.mana_regen_from_spirit(custom) == 4, "20 Spirit should produce 4 mana regen"
    return True


def scenario_spirit_end_of_turn_regen_is_silent_and_clamped() -> bool:
    baseline = make_match("warrior", "rogue", seed=9106)
    warrior_sid, rogue_sid = baseline.players
    warrior = baseline.state[warrior_sid]
    rogue = baseline.state[rogue_sid]
    warrior_mp_before = warrior.res.mp
    rogue_mp_before = rogue.res.mp
    submit_turn(baseline, _DEF_PASS, _DEF_PASS)
    assert baseline.state[warrior_sid].res.mp == warrior_mp_before, "Non-mana classes with 0 Spirit should remain unchanged"
    assert baseline.state[rogue_sid].res.mp == rogue_mp_before, "Non-mana classes with 0 Spirit should remain unchanged"

    spirit_match = make_match("paladin", "warrior", seed=9107)
    paladin_sid, _ = spirit_match.players
    paladin = spirit_match.state[paladin_sid]
    paladin.res.mp = 0
    submit_turn(spirit_match, _DEF_PASS, _DEF_PASS)
    assert spirit_match.state[paladin_sid].res.mp == 4, "Paladin should gain base 2 + spirit 2 mana at end of turn"
    turn_lines = _turn_lines(spirit_match, 1)
    assert not any("recovers 2 Mana" in line or "recovers 4 Mana" in line for line in turn_lines), "Spirit mana regen should remain silent in combat log"

    clamp_match = make_match("priest", "warrior", seed=9108)
    priest_sid, _ = clamp_match.players
    priest = clamp_match.state[priest_sid]
    priest.res.mp = priest.res.mp_max - 1
    submit_turn(clamp_match, _DEF_PASS, _DEF_PASS)
    assert clamp_match.state[priest_sid].res.mp == priest.res.mp_max, "Spirit mana regen should clamp at mp_max"
    return True


def scenario_grant_player_resource_central_helper() -> bool:
    """Player resource grants (pet/totem restores included) route through
    grant_player_resource so Challenger Wrath applies and caps are respected."""

    def _restored(match, needle: str) -> list[int]:
        return [
            int(m.group(1))
            for line in match.log
            for m in [re.search(needle + r" restores (\d+) mana", line)]
            if m
        ]

    # --- Central helper unit behavior -------------------------------------
    helper_match = make_match("shaman", "rogue", p1_items={"armor": "challengers_chestplate"}, seed=6400)
    helper = helper_match.state["p1_sid"]
    helper.res.mp = 40  # <=50% -> Challenger Wrath boosts active-resource (mp) gains 30%
    assert effects.challenger_resource_stance_mode(helper) == "wrath", "Setup: helper owner should be in Wrath"
    assert effects.grant_player_resource(helper, "mp", 10) == 13, "Helper should apply Challenger Wrath (int(10*1.30)=13)"
    assert helper.res.mp == 53, "Helper should mutate player mana by the adjusted amount"
    # None Challenger mode means no modifier (not Wrath).
    helper.res.mp = 40
    assert effects.grant_player_resource(helper, "mp", 10, challenger_mode=None) == 10, "None mode should apply no Challenger modifier"
    # Non-positive amounts and HP are ignored; caps are respected.
    hp_before = helper.res.hp
    assert effects.grant_player_resource(helper, "mp", 0) == 0, "Non-positive grants are ignored"
    assert effects.grant_player_resource(helper, "mp", -5) == 0, "Negative grants are ignored"
    assert effects.grant_player_resource(helper, "hp", 10) == 0 and helper.res.hp == hp_before, "Helper must not touch HP"
    helper.res.mp = helper.res.mp_max - 4
    assert effects.grant_player_resource(helper, "mp", 10, challenger_mode=None) == 4, "Helper caps at resource max and returns actual restored"
    assert helper.res.mp == helper.res.mp_max, "Near-cap grant should clamp to max"
    # energy / rage support and a missing res guard.
    rogue_owner = make_match("rogue", "mage", seed=6401).state["p1_sid"]
    rogue_owner.res.energy = 10
    assert effects.grant_player_resource(rogue_owner, "energy", 5) == 5 and rogue_owner.res.energy == 15, "Helper should support energy"
    warrior_owner = make_match("warrior", "mage", seed=6402).state["p1_sid"]
    warrior_owner.res.rage = 10
    assert effects.grant_player_resource(warrior_owner, "rage", 5) == 5 and warrior_owner.res.rage == 15, "Helper should support rage"
    class _NoRes:
        res = None
    assert effects.grant_player_resource(_NoRes(), "mp", 10) == 0, "Helper should be safe when player.res is missing"

    # --- 1) Challenger Shaman in Wrath: Mana Tide restores 13 -------------
    wrath_tide = make_match("shaman", "rogue", p1_items={"armor": "challengers_chestplate"}, seed=6403)
    wrath_shaman = wrath_tide.state["p1_sid"]
    wrath_shaman.res.mp = 30
    assert effects.challenger_resource_stance_mode(wrath_shaman) == "wrath", "Setup: Wrath shaman at 30/100 mana"
    submit_turn(wrath_tide, "mana_tide_totem", _DEF_PASS)
    assert _restored(wrath_tide, "Mana Tide Totem") == [13], "Challenger Wrath Mana Tide should restore int(10*1.30)=13"

    # --- 2) Challenger Shaman in Might: Mana Tide restores 10 -------------
    might_tide = make_match("shaman", "rogue", p1_items={"armor": "challengers_chestplate"}, seed=6404)
    might_shaman = might_tide.state["p1_sid"]
    might_shaman.res.mp = might_shaman.res.mp_max
    assert effects.challenger_resource_stance_mode(might_shaman) == "might", "Setup: Might shaman above 50% mana"
    submit_turn(might_tide, "mana_tide_totem", _DEF_PASS)
    assert _restored(might_tide, "Mana Tide Totem") == [10], "Challenger Might Mana Tide should restore the base 10"

    # --- 3) No-Challenger Shaman: Mana Tide restores 10 ------------------
    plain_tide = make_match("shaman", "rogue", seed=6405)
    plain_shaman = plain_tide.state["p1_sid"]
    plain_shaman.res.mp = max(0, plain_shaman.res.mp - 40)
    submit_turn(plain_tide, "mana_tide_totem", _DEF_PASS)
    assert _restored(plain_tide, "Mana Tide Totem") == [10], "Non-Challenger Mana Tide should restore the base 10"

    original_hit_chance = PET_AI.hit_chance
    PET_AI.hit_chance = lambda acc, eva: 100
    try:
        # --- 4) Challenger Priest in Wrath: Shadowfiend hit restores 16 --
        wrath_fiend = make_match("priest", "warrior", p1_items={"armor": "challengers_chestplate"}, seed=6406)
        wrath_priest = wrath_fiend.state["p1_sid"]
        wrath_priest.res.mp = 30
        assert effects.challenger_resource_stance_mode(wrath_priest) == "wrath", "Setup: Wrath priest below 50% mana"
        submit_turn(wrath_fiend, "shadowfiend", _DEF_PASS)
        assert _restored(wrath_fiend, "Shadowfiend") == [16], "Challenger Wrath Shadowfiend should restore int(13*1.30)=16"

        # --- 5) Near-cap restore logs/state reflect only the actual amount
        cap_fiend = make_match("priest", "warrior", seed=6407)
        cap_priest = cap_fiend.state["p1_sid"]
        submit_turn(cap_fiend, "shadowfiend", _DEF_PASS)  # summon + first melee restore
        cap_priest.res.mp = cap_priest.res.mp_max - 3
        submit_turn(cap_fiend, _DEF_PASS, _DEF_PASS)  # next melee restore, now near cap
        cap_restores = _restored(cap_fiend, "Shadowfiend")
        assert cap_restores and cap_restores[-1] == 3, "Near-cap Shadowfiend log should reflect only the actual 3 mana restored"
        assert cap_priest.res.mp == cap_priest.res.mp_max, "Near-cap restore should clamp mana to max"
    finally:
        PET_AI.hit_chance = original_hit_chance

    # --- 6) Pet self-resource regeneration remains unchanged -------------
    saber_match = make_match("hunter", "warrior", seed=6408)
    hunter = saber_match.state["p1_sid"]
    submit_turn(saber_match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Frostsaber should summon"
    saber.energy = 7
    submit_turn(saber_match, _DEF_PASS, _DEF_PASS)
    assert saber.energy == 12, "Pet self-resource regen (+5 energy/turn) must be unaffected by the player helper"

    return True


def scenario_apply_player_healing_helper_contract() -> bool:
    """effects.apply_player_healing() is the player-HP final-application primitive.

    It owns only int normalization, the upper hp_max cap, the res.hp write,
    and the actual-gained return — and it must not lower-clamp transient
    negative HP. HP healing stays separate from the resource pipeline:
    grant_player_resource() keeps ignoring HP (asserted in
    scenario_grant_player_resource_central_helper).
    """
    player = make_match("warrior", "mage", seed=6511).state["p1_sid"]
    player.res.hp_max = 100

    # Normal healing: 50 + 20 = 70, return 20.
    player.res.hp = 50
    assert effects.apply_player_healing(player, 20) == 20, "Uncapped healing should return the full requested amount"
    assert player.res.hp == 70, "Uncapped healing should add the full requested amount"

    # Near-cap: 95 + 20 = 100, return 5.
    player.res.hp = 95
    assert effects.apply_player_healing(player, 20) == 5, "Near-cap healing should return only the HP that fit below hp_max"
    assert player.res.hp == 100, "Near-cap healing should clamp at hp_max"

    # Full HP: 100 + 20 = 100, return 0.
    assert effects.apply_player_healing(player, 20) == 0, "Healing at full HP should return 0"
    assert player.res.hp == 100, "Healing at full HP should change nothing"

    # Nonpositive requested amounts are no-ops.
    player.res.hp = 50
    assert effects.apply_player_healing(player, 0) == 0 and player.res.hp == 50, "Zero amount should be a no-op"
    assert effects.apply_player_healing(player, -5) == 0 and player.res.hp == 50, "Negative amount should be a no-op"

    # Negative current HP heals as-is (no lower clamp): -6 + 12 = 6.
    player.res.hp = -6
    assert effects.apply_player_healing(player, 12) == 12, "Healing from negative HP should return the full applied delta"
    assert player.res.hp == 6, "Healing must apply to the actual negative HP value, never zero-clamping it first"

    # Partial negative recovery may leave HP negative: -10 + 4 = -6.
    player.res.hp = -10
    assert effects.apply_player_healing(player, 4) == 4, "Partial negative recovery should return the applied delta"
    assert player.res.hp == -6, "Healing smaller than the deficit must leave HP negative until the winner check"

    # Missing res mirrors grant_player_resource's defensive no-op contract.
    class _NoRes:
        res = None

    assert effects.apply_player_healing(_NoRes(), 10) == 0, "Helper should be safe when target.res is missing"

    return True
