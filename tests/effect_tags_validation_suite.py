"""Phase A validation suite for effect tags and blink-like effect ownership."""

from __future__ import annotations

from typing import Dict, Set

from regression_suite import ABILITIES, EFFECT_TEMPLATES, make_match, submit_turn  # type: ignore


PHASE_A_EXPECTED_TAGS: Dict[str, Set[str]] = {
    "iceblock": {"immune_all"},
    "unending_resolve": {"immune_all"},
    "divine_shield": {"immune_all"},
    "die_by_sword": {"immune_part"},
    "cloak_of_shadows": {"immune_part"},
    "stealth": {"stealth"},
    "stunned": {"incapacitating_cc"},
    "feared": {"incapacitating_cc", "break_on_damage"},
    "ring_of_ice_freeze": {"incapacitating_cc", "break_on_damage"},
    "freezing_trap_freeze": {"incapacitating_cc", "break_on_damage"},
    "cyclone": {"incapacitating_cc"},
    "blink": {"blink_like"},
    "demonic_gateway": {"blink_like"},
    "teleport": {"blink_like"},
    "disengage": {"blink_like"},
    "blocking_defence": {"redirect"},
    "dark_pact": {"absorb"},
    "shield_of_vengeance": {"absorb"},
    "ignore_pain": {"absorb"},
    "shielded": {"absorb"},
    "power_word_shield": {"absorb"},
    "ice_barrier": {"absorb"},
    "die_by_sword_mitigation": {"damage_reduction"},
    "pain_suppression": {"damage_reduction"},
    "barkskin": {"damage_reduction"},
    "aspect_of_turtle": {"damage_reduction"},
    "burn": {"dot"},
    "agony": {"dot"},
    "corruption": {"dot"},
    "unstable_affliction": {"dot"},
    "vampiric_touch": {"dot"},
    "devouring_plague": {"dot"},
    "dragon_roar_bleed": {"dot"},
    "wildfire_burn": {"dot"},
    "regrowth": {"hot"},
    "frenzied_regeneration": {"hot"},
    "hot_streak": {"proc"},
    "ambush": {"proc"},
    "crusader_empower": {"proc"},
    "paladin_final_verdict_empowered": {"proc"},
    "mind_blast_empowered": {"proc"},
    "shadowy_insight": {"proc"},
    "arcane_shot_proc": {"proc"},
    "raptor_strike_proc": {"proc"},
    "rip_ready": {"proc"},
    "starfire_ready": {"proc"},
    "bear_form": {"form"},
    "cat_form": {"form"},
    "moonkin_form": {"form"},
    "tree_form": {"form"},
}

PHASE_A_UNTAGGED = {
    "demonic_circle",
    "evasion",
    "thistle_tea",
    "mindgames",
    "shadowfiend",
    "avenging_wrath",
    "ironfur",
    "typhoon_disoriented",
    "item_passive_template",
    "bear_form_stats",
    "cat_form_stats",
    "moonkin_form_stats",
    "tree_form_stats",
}


def _has_effect(player, effect_id: str) -> bool:
    return any(fx.get("id") == effect_id for fx in player.effects)


def test_phase_a_expected_effect_tags_present() -> None:
    for effect_id, expected in PHASE_A_EXPECTED_TAGS.items():
        effect = EFFECT_TEMPLATES[effect_id]
        actual = set(effect.get("tags") or [])
        missing = expected - actual
        assert not missing, f"{effect_id} missing tags: {sorted(missing)}"


def test_phase_a_untagged_effects_remain_untagged() -> None:
    for effect_id in PHASE_A_UNTAGGED:
        effect = EFFECT_TEMPLATES[effect_id]
        assert not effect.get("tags"), f"{effect_id} should remain untagged in Phase A"


def test_psychic_scream_reuses_live_feared_effect_family() -> None:
    assert "psychic_scream" not in EFFECT_TEMPLATES, "Unused psychic_scream effect template should be removed"
    ability = ABILITIES["psychic_scream"]
    target_effect = (ability.get("target_effects") or [])[0]
    assert target_effect.get("id") == "feared", "Psychic Scream should keep using feared as the live fear-state"


def test_warlock_blink_like_tag_membership() -> None:
    assert "blink_like" in (EFFECT_TEMPLATES["blink"].get("tags") or [])
    assert "blink_like" in (EFFECT_TEMPLATES["disengage"].get("tags") or [])
    assert "blink_like" in (EFFECT_TEMPLATES["demonic_gateway"].get("tags") or [])
    assert "blink_like" in (EFFECT_TEMPLATES["teleport"].get("tags") or [])
    assert "blink_like" not in (EFFECT_TEMPLATES["demonic_circle"].get("tags") or [])


def test_warlock_gateway_and_teleport_preserve_miss_behavior() -> None:
    gateway_match = make_match("warrior", "warlock", seed=7601)
    submit_turn(gateway_match, "basic_attack", "demonic_gateway")
    warlock = gateway_match.state[gateway_match.players[1]]
    assert _has_effect(warlock, "demonic_gateway"), "Demonic Gateway effect should be active after cast"
    assert any("Target fled through the portal — Miss." in line for line in gateway_match.log), "Gateway miss log should be preserved"

    teleport_match = make_match("warrior", "warlock", seed=7602)
    submit_turn(teleport_match, "pass_turn", "demonic_circle")
    submit_turn(teleport_match, "basic_attack", "teleport")
    warlock_tp = teleport_match.state[teleport_match.players[1]]
    assert _has_effect(warlock_tp, "teleport"), "Teleport effect should be active after cast"
    assert any("Target returned to their dark ward — Miss." in line for line in teleport_match.log), "Teleport miss log should be preserved"


def test_pet_attack_into_gateway_and_teleport_uses_effect_owned_miss_logs() -> None:
    gateway_match = make_match("warlock", "warlock", seed=7610)
    submit_turn(gateway_match, "summon_imp", "pass_turn")
    submit_turn(gateway_match, "pass_turn", "demonic_gateway")
    defender = gateway_match.state[gateway_match.players[1]]
    hp_before = defender.res.hp
    submit_turn(gateway_match, "pass_turn", "pass_turn")
    assert defender.res.hp == hp_before, "Gateway should still avoid pet single-target attacks"
    assert any("Target fled through the portal — Miss." in line for line in gateway_match.log), "Gateway miss log should appear for pet attacks"

    teleport_match = make_match("warlock", "warlock", seed=7611)
    submit_turn(teleport_match, "summon_imp", "demonic_circle")
    submit_turn(teleport_match, "pass_turn", "teleport")
    defender_tp = teleport_match.state[teleport_match.players[1]]
    hp_before_tp = defender_tp.res.hp
    submit_turn(teleport_match, "pass_turn", "pass_turn")
    assert defender_tp.res.hp == hp_before_tp, "Teleport should still avoid pet single-target attacks"
    assert any("Target returned to their dark ward — Miss." in line for line in teleport_match.log), "Teleport miss log should appear for pet attacks"


def test_harmful_target_effect_path_keeps_gateway_teleport_miss_logs() -> None:
    gateway_match = make_match("warlock", "warlock", seed=7612)
    submit_turn(gateway_match, "pass_turn", "demonic_gateway")
    submit_turn(gateway_match, "fear", "pass_turn")
    defender = gateway_match.state[gateway_match.players[1]]
    assert not _has_effect(defender, "feared"), "Gateway should cause harmful target_effect misses"
    assert any("Target fled through the portal — Miss." in line for line in gateway_match.log), "Gateway miss log should be preserved for harmful target_effects"

    teleport_match = make_match("warlock", "warlock", seed=7613)
    submit_turn(teleport_match, "pass_turn", "demonic_circle")
    submit_turn(teleport_match, "pass_turn", "teleport")
    submit_turn(teleport_match, "fear", "pass_turn")
    defender_tp = teleport_match.state[teleport_match.players[1]]
    assert not _has_effect(defender_tp, "feared"), "Teleport should cause harmful target_effect misses"
    assert any("Target returned to their dark ward — Miss." in line for line in teleport_match.log), "Teleport miss log should be preserved for harmful target_effects"


def test_psychic_scream_aoe_fear_applies_feared_to_champion_and_pets() -> None:
    match = make_match("priest", "warlock", seed=7701)
    submit_turn(match, "pass_turn", "summon_imp")
    submit_turn(match, "pass_turn", "summon_imp")
    submit_turn(match, "pass_turn", "summon_imp")

    submit_turn(match, "psychic_scream", "pass_turn")
    warlock = match.state[match.players[1]]
    assert _has_effect(warlock, "feared"), "Psychic Scream should fear the enemy champion"
    for pet_id in sorted(warlock.pets.keys()):
        assert _has_effect(warlock.pets[pet_id], "feared"), f"Psychic Scream should fear pet {pet_id}"


def test_psychic_scream_status_text_and_family_alignment() -> None:
    ability = ABILITIES["psychic_scream"]
    target_effect = (ability.get("target_effects") or [])[0]
    assert target_effect.get("id") == "feared", "Psychic Scream should reuse the fear family effect id"

    match = make_match("priest", "warlock", seed=7702)
    submit_turn(match, "psychic_scream", "pass_turn")
    warlock = match.state[match.players[1]]
    feared = next((fx for fx in warlock.effects if fx.get("id") == "feared"), None)
    assert feared is not None, "Psychic Scream should apply feared"
    assert feared.get("name") == "Feared", "Psychic Scream should expose Feared status text"
    assert feared.get("cant_act_reason") == "feared", "Psychic Scream should align with fear-style action lock"
    flags = feared.get("flags") or {}
    assert flags.get("break_on_damage") is True, "Psychic Scream should align with fear-style break-on-damage behavior"
    assert any("uses their bare hands to cast Psychic Scream. Lets out a terrifying scream fearing all enemies away" in line for line in match.log), "Psychic Scream cast log text should be preserved"


def test_psychic_scream_feared_warlock_imps_do_not_act() -> None:
    match = make_match("priest", "warlock", seed=7703)
    submit_turn(match, "pass_turn", "summon_imp")
    submit_turn(match, "pass_turn", "summon_imp")
    submit_turn(match, "pass_turn", "summon_imp")

    submit_turn(match, "psychic_scream", "pass_turn")
    latest_turn = match.log[match.log.index("Turn 4") + 1:]
    assert any("is feared and cannot act." in line and "Imp" in line for line in latest_turn), "Feared imps should log cannot-act lines"
    assert not any("Imp casts Firebolt" in line for line in latest_turn), "Feared imps should not cast Firebolt"


def test_psychic_scream_feared_hunter_companion_does_not_act() -> None:
    match = make_match("priest", "hunter", seed=7704)
    submit_turn(match, "pass_turn", "call_saber")
    submit_turn(match, "psychic_scream", "pass_turn")
    latest_turn = match.log[match.log.index("Turn 2") + 1:]
    assert any("Frostsaber is feared and cannot act." in line for line in latest_turn), "Feared hunter pet should log cannot-act line"
    assert not any("Frostsaber melees the target" in line or "Frostsaber bites the target" in line for line in latest_turn), "Feared hunter pet should not attack while feared"


def run_all() -> list[tuple[str, bool, str]]:
    tests = [
        test_phase_a_expected_effect_tags_present,
        test_phase_a_untagged_effects_remain_untagged,
        test_psychic_scream_reuses_live_feared_effect_family,
        test_warlock_blink_like_tag_membership,
        test_warlock_gateway_and_teleport_preserve_miss_behavior,
        test_pet_attack_into_gateway_and_teleport_uses_effect_owned_miss_logs,
        test_harmful_target_effect_path_keeps_gateway_teleport_miss_logs,
        test_psychic_scream_aoe_fear_applies_feared_to_champion_and_pets,
        test_psychic_scream_status_text_and_family_alignment,
        test_psychic_scream_feared_warlock_imps_do_not_act,
        test_psychic_scream_feared_hunter_companion_does_not_act,
    ]
    results: list[tuple[str, bool, str]] = []
    for test in tests:
        try:
            test()
            results.append((test.__name__, True, ""))
        except AssertionError as exc:
            results.append((test.__name__, False, str(exc)))
    return results


if __name__ == "__main__":
    failures = [entry for entry in run_all() if not entry[1]]
    for name, ok, reason in run_all():
        print(f"PASS: {name}" if ok else f"FAIL: {name} -> {reason}")
    if failures:
        raise SystemExit(1)
