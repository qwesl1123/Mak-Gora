"""UI/docs/metadata regression scenarios (duel.html docs, payload contracts, command normalization, static strings).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

from harness import (
    ABILITIES,
    CLASSES,
    MatchState,
    SOCKETS,
    _detect_duel_html_path,
    _turn_lines,
    apply_prep_build,
    effects,
    make_match,
    resolver,
    state_extract,
    submit_action,
    submit_turn,
)

from .helpers import (
    _DEF_PASS,
    _active_pet,
)


def scenario_post_combat_summary_exposes_pet_healing_and_actual_damage_dpt() -> bool:
    """Ended-match snapshots expose pet healing and actual-damage DPT per viewer.

    Pet healing stays a separate statistic (never folded back into regular
    healing), DPT divides corrected actual damage by completed resolved turns,
    a first-turn kill uses completed_turns == 1, a zero-damage player shows
    0.0 DPT, and the tokenized summary line renders with every field filled.
    """
    # First-turn kill via direct resolve calls (overkill leaves negative HP,
    # which the submit_turn harness invariants reject by design).
    match = make_match("warrior", "priest", seed=123)
    warrior_sid, priest_sid = match.players
    match.state[warrior_sid].stats["acc"] = 999
    match.state[priest_sid].stats["eva"] = 0
    match.state[priest_sid].res.hp = 1
    resolver.submit_action(match, warrior_sid, {"ability_id": "basic_attack"})
    resolver.submit_action(match, priest_sid, {"ability_id": _DEF_PASS})
    resolver.resolve_turn(match)
    assert match.phase == "ended" and match.winner == warrior_sid, "Setup: the first turn should be lethal"
    assert match.turn == 1, "A first-turn kill leaves exactly one completed resolved turn"

    # Pin the totals so every summary field is deterministic, including pet
    # buckets this seed's fight did not naturally produce. 41 damage over one
    # turn gives a 41.0 DPT; the dead priest keeps zero credited damage.
    match.combat_totals[warrior_sid] = {"damage": 41, "healing": 9, "pet_healing": 6, "overhealing": 2, "pet_overhealing": 1}
    match.combat_totals[priest_sid] = {"damage": 0, "healing": 3, "pet_healing": 0, "overhealing": 0, "pet_overhealing": 0}

    warrior_view = SOCKETS.snapshot_for(match, warrior_sid)
    assert warrior_view["completed_turns"] == 1, "Fight length must equal completed resolved turns"
    assert warrior_view["friendly_total_damage"] == 41, "Friendly damage must use the corrected actual-damage total"
    assert warrior_view["friendly_total_healing"] == 9, "Friendly healing must contain only player-produced healing"
    assert warrior_view["friendly_total_pet_healing"] == 6, "Pet healing must surface as its own snapshot statistic"
    assert warrior_view["enemy_total_damage"] == 0 and warrior_view["enemy_total_healing"] == 3 and warrior_view["enemy_total_pet_healing"] == 0, "Enemy totals must read the opponent's buckets"
    assert warrior_view["friendly_damage_per_turn"] == 41.0, "First-turn kill DPT equals total damage (denominator 1, no divide-by-zero)"
    assert warrior_view["enemy_damage_per_turn"] == 0.0, "A zero-damage player must show 0.0 DPT"

    priest_view = SOCKETS.snapshot_for(match, priest_sid)
    assert priest_view["friendly_total_damage"] == 0 and priest_view["friendly_total_healing"] == 3 and priest_view["friendly_total_pet_healing"] == 0, "The second viewer's Friendly values must be their own totals"
    assert priest_view["enemy_total_damage"] == 41 and priest_view["enemy_total_healing"] == 9 and priest_view["enemy_total_pet_healing"] == 6, "The second viewer must see the opponent's totals as Enemy, reversing pet healing correctly"
    assert priest_view["friendly_damage_per_turn"] == 0.0 and priest_view["enemy_damage_per_turn"] == 41.0, "DPT must reverse with the viewer"

    warrior_summary = next(line for line in warrior_view["log"] if line.startswith("Post-Combat Summary|"))
    assert warrior_summary == "Post-Combat Summary|T:1|FD:41|FH:9|FPH:6|FDPT:41.0|ED:0|EH:3|EPH:0|EDPT:0.0", "The tokenized summary must carry turns, pet healing, and one-decimal DPT per viewer"
    priest_summary = next(line for line in priest_view["log"] if line.startswith("Post-Combat Summary|"))
    assert priest_summary == "Post-Combat Summary|T:1|FD:0|FH:3|FPH:0|FDPT:0.0|ED:41|EH:9|EPH:6|EDPT:41.0", "The second viewer's summary must reverse Friendly/Enemy"
    for bad_token in ("{", "}", "NaN", "undefined", "Infinity"):
        assert bad_token not in warrior_summary and bad_token not in priest_summary, "The rendered summary must never leak placeholders or non-numeric values"

    # Multi-turn control: 41 damage over 3 completed turns rounds to 13.7.
    long_match = make_match("warrior", "priest", seed=124)
    long_warrior_sid, long_priest_sid = long_match.players
    long_match.state[long_warrior_sid].stats["acc"] = 999
    long_match.state[long_priest_sid].stats["eva"] = 0
    submit_turn(long_match, _DEF_PASS, _DEF_PASS)
    submit_turn(long_match, _DEF_PASS, _DEF_PASS)
    long_match.state[long_priest_sid].res.hp = 1
    resolver.submit_action(long_match, long_warrior_sid, {"ability_id": "basic_attack"})
    resolver.submit_action(long_match, long_priest_sid, {"ability_id": _DEF_PASS})
    resolver.resolve_turn(long_match)
    assert long_match.phase == "ended" and long_match.turn == 3, "Setup: the third resolved turn should end the fight"
    long_match.combat_totals[long_warrior_sid]["damage"] = 41
    long_view = SOCKETS.snapshot_for(long_match, long_warrior_sid)
    assert long_view["completed_turns"] == 3, "The final resolved turn must be included in the denominator"
    assert long_view["friendly_damage_per_turn"] == 13.7, "DPT must round 41/3 to one decimal place"
    long_summary = next(line for line in long_view["log"] if line.startswith("Post-Combat Summary|"))
    assert "|T:3|" in long_summary and "|FDPT:13.7|" in long_summary, "The summary tokens must carry the multi-turn one-decimal DPT"

    # The client parser must consume the same token contract the backend emits.
    duel_html = _detect_duel_html_path().read_text(encoding="utf-8")
    for parser_key in ("values.T", "values.FD", "values.FH", "values.FPH", "values.FDPT", "values.ED", "values.EH", "values.EPH", "values.EDPT"):
        assert parser_key in duel_html, f"duel.html summary parser should read {parser_key}"
    assert "Friendly Pet Healing" in duel_html and "Enemy Pet Healing" in duel_html, "The post-combat summary should render pet healing rows"
    assert "Friendly DPT" in duel_html and "Enemy DPT" in duel_html, "The post-combat summary should render DPT rows"
    assert ".toFixed(1)" in duel_html, "The UI must format DPT with exactly one decimal place"
    return True


def scenario_double_ko_post_combat_summary_renders_dpt_and_fight_length() -> bool:
    """A double KO still renders fight length and both DPT values.

    With no winner, both viewers must still get a fully filled summary token
    (turns + one-decimal DPT on both sides) and the structured snapshot
    fields, on a first-turn double KO and on a multi-turn double KO whose
    DPT is a non-trivial one-decimal fraction.
    """
    # First-turn double KO: mutual 1-damage Agony ticks kill both at 1 HP.
    match = make_match("priest", "warlock", seed=9401)
    p1_sid, p2_sid = match.players
    match.state[p1_sid].res.hp = 1
    match.state[p2_sid].res.hp = 1
    effects.apply_effect_by_id(match.state[p1_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p2_sid, "dot_mode": "fixed"})
    effects.apply_effect_by_id(match.state[p2_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": p1_sid, "dot_mode": "fixed"})
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert match.phase == "ended" and match.winner is None, "Setup: the mutual DoT turn should be a true double KO"
    assert match.turn == 1, "A first-turn double KO leaves exactly one completed resolved turn"

    for viewer_sid in (p1_sid, p2_sid):
        view = SOCKETS.snapshot_for(match, viewer_sid)
        assert view["completed_turns"] == 1, "Fight length must render even without a winner"
        assert view["friendly_damage_per_turn"] == 1.0 and view["enemy_damage_per_turn"] == 1.0, "Both DPT values must render on a double KO (1 credited damage over 1 turn)"
        summary = next(line for line in view["log"] if line.startswith("Post-Combat Summary|"))
        for token in ("|T:1|", "|FD:1|", "|FDPT:1.0|", "|ED:1|", "|EDPT:1.0"):
            assert token in summary, f"Double-KO summary must fill {token} for every viewer"
        for bad_token in ("{", "}", "NaN", "undefined", "Infinity"):
            assert bad_token not in summary, "Double-KO summary must never leak placeholders or non-numeric values"
        assert "Double KO. No winner." in view["log"], "The double-KO outcome line should accompany the summary"

    # Multi-turn double KO: 1 credited damage over 3 completed turns must
    # render the one-decimal 0.3 DPT on both sides.
    long_match = make_match("priest", "warlock", seed=9402)
    long_p1_sid, long_p2_sid = long_match.players
    submit_turn(long_match, _DEF_PASS, _DEF_PASS)
    submit_turn(long_match, _DEF_PASS, _DEF_PASS)
    long_match.state[long_p1_sid].res.hp = 1
    long_match.state[long_p2_sid].res.hp = 1
    effects.apply_effect_by_id(long_match.state[long_p1_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": long_p2_sid, "dot_mode": "fixed"})
    effects.apply_effect_by_id(long_match.state[long_p2_sid], "agony", overrides={"duration": 1, "tick_damage": 1, "source_sid": long_p1_sid, "dot_mode": "fixed"})
    submit_turn(long_match, _DEF_PASS, _DEF_PASS)
    assert long_match.phase == "ended" and long_match.winner is None and long_match.turn == 3, "Setup: the third resolved turn should end in a double KO"

    long_view = SOCKETS.snapshot_for(long_match, long_p1_sid)
    assert long_view["completed_turns"] == 3, "The final double-KO turn must count toward fight length"
    assert long_view["friendly_damage_per_turn"] == 0.3 and long_view["enemy_damage_per_turn"] == 0.3, "Multi-turn double-KO DPT must round 1/3 to one decimal place"
    long_summary = next(line for line in long_view["log"] if line.startswith("Post-Combat Summary|"))
    assert "|T:3|" in long_summary and "|FDPT:0.3|" in long_summary and "|EDPT:0.3" in long_summary, "The multi-turn double-KO summary must carry turns and one-decimal DPT on both sides"
    return True


def scenario_warlock_imp_log_coloring_mapping_present() -> bool:
    duel_html = _detect_duel_html_path().read_text(encoding="utf-8")
    assert '{ names: ["Imp"], className: "log-class-warlock" }' in duel_html, "Combat log pet styling should map Imp to warlock class color"
    return True


def scenario_hunter_proc_log_stays_at_top_of_turn() -> bool:
    match = make_match("warrior", "hunter", seed=123)

    submit_turn(match, _DEF_PASS, "wildfire_bomb")

    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert latest_turn[0] == f"{match.players[1][:5]} has Arcane Surge!", "Hunter proc reminder should be the first line of the turn even when the Hunter acts second"
    warrior_action_idx = next(i for i, line in enumerate(latest_turn) if "uses their bare hands to cast Pass Turn" in line)
    hunter_action_idx = next(i for i, line in enumerate(latest_turn) if "uses their bare hands to cast Wildfire Bomb" in line)
    assert 0 < warrior_action_idx < hunter_action_idx, "Proc reminder should appear before both players' action lines"
    return True


def scenario_proc_and_has_reminders_stay_in_expected_order() -> bool:
    match = make_match("hunter", "priest", seed=123)
    hunter_sid, priest_sid = match.players
    hunter = match.state[hunter_sid]
    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should be active for redirect ordering coverage"
    hunter.pending_pet_command = "special"

    submit_turn(match, "wildfire_bomb", _DEF_PASS)

    latest_turn = _turn_lines(match, 2)
    proc_idx = next((i for i, line in enumerate(latest_turn) if "has Arcane Surge!" in line), -1)
    brace_idx = next((i for i, line in enumerate(latest_turn) if "Barrens Boar braces to intercept attacks." in line), -1)
    hunter_action_idx = next((i for i, line in enumerate(latest_turn) if "uses their bare hands to cast Wildfire Bomb." in line), -1)
    assert proc_idx == 0, "Proc reminder should stay at the top of the turn"
    assert brace_idx > proc_idx, "Pre-action pet brace log should remain below proc reminders"
    assert hunter_action_idx > brace_idx, "Action logs should remain below pre-action reminder/proc logs"

    execute_match = make_match("warrior", "priest", seed=123)
    warrior_sid, priest_sid = execute_match.players
    warrior = execute_match.state[warrior_sid]
    priest = execute_match.state[priest_sid]
    priest.res.hp = max(1, int(priest.res.hp_max * 0.15))
    warrior.res.rage = warrior.res.rage_max

    submit_turn(execute_match, _DEF_PASS, _DEF_PASS)
    execute_turn = _turn_lines(execute_match, 1)
    assert execute_turn[-1] == f"{warrior_sid[:5]} Can Use Execute!", "Can-use reminder should remain at the bottom of the turn"
    return True


def scenario_invalid_class_rejected() -> bool:
    match = MatchState(room_id="invalid-class", players=["p1_sid", "p2_sid"], phase="prep", seed=123)
    match.picks["p1_sid"] = {"class_id": "warlock"}
    match.picks["p2_sid"] = {"class_id": "adventurer", "items": {"weapon": "dagger"}}

    try:
        apply_prep_build(match)
    except ValueError as exc:
        assert "unknown class_id 'adventurer'" in str(exc), "invalid class error should mention the rejected class id"
    else:
        raise AssertionError("apply_prep_build should reject unknown class ids instead of creating a fake class")

    assert not match.state, "invalid prep build should not create partial player state"
    return True


def scenario_valid_class_id_is_normalized_before_build() -> bool:
    match = MatchState(room_id="normalized-class", players=["p1_sid", "p2_sid"], phase="prep", seed=123)
    match.picks["p1_sid"] = {"class_id": " WarLock "}
    match.picks["p2_sid"] = {"class_id": "warrior"}

    apply_prep_build(match)

    assert match.state["p1_sid"].build.class_id == "warlock", "valid class ids should be normalized before combat"
    assert match.state["p1_sid"].res.hp == match.state["p1_sid"].res.hp_max, "normalized class should still build a valid player state"
    return True


def scenario_prep_selection_name_uses_current_submission() -> bool:
    assert SOCKETS._prep_selection_name({"class_id": "warrior"}) == "Warrior", "class submissions should log the chosen class"
    assert SOCKETS._prep_selection_name({"items": {"weapon": "thunderfury"}}) == "Thunderfury, Blessed Blade of the Windseeker", "weapon submissions should log the weapon name"
    assert SOCKETS._prep_selection_name({"items": {"armor": "leather_armor"}}) == "Leather Armor", "armor submissions should log the armor name"
    assert SOCKETS._prep_selection_name({"items": {"trinket": "rage_crystal"}}) == "Rage Crystal", "trinket submissions should log the trinket name"
    assert SOCKETS._prep_selection_name({"class_id": "warrior", "items": {"weapon": "thunderfury"}}) == "Warrior", "class submissions should take precedence when sent together"
    return True


def scenario_command_input_normalizes_abilities_and_items() -> bool:
    assert resolver.normalize_command_input(" ring  of   ice ") == "ring_of_ice", "spaces should collapse into underscores"
    assert resolver.normalize_command_input("freeZing trAp") == "freezing_trap", "commands should be case-insensitive"
    assert resolver.normalize_command_input("rage cristal") == "rage_cristal", "item commands should normalize the same way"

    match = make_match("mage", "hunter", seed=321)
    submit_action(match, match.players[0], {"ability_id": " ring  of   ice "})
    submit_action(match, match.players[1], {"ability_id": "freeZing trAp"})
    assert match.submitted[match.players[0]]["ability_id"] == "ring_of_ice", "underscore ability input should stay canonical"
    assert match.submitted[match.players[1]]["ability_id"] == "freezing_trap", "mixed-case spaced ability input should normalize before lookup"

    normalized_items = SOCKETS._normalized_item_updates({"": " rage   crystal ", "armor": " leather  armor "})
    assert normalized_items == {"trinket": "rage_crystal", "armor": "leather_armor"}, "item payloads should infer slots and normalize names generically"
    assert SOCKETS._prep_selection_name({"items": {"": " rage   crystal "}}) == "Rage Crystal", "selection logging should use normalized item ids"
    return True


def scenario_shadowfiend_pet_box_hides_turn_counter_badge() -> bool:
    match = make_match("priest", "warrior", seed=150)
    submit_turn(match, "shadowfiend", _DEF_PASS)
    snapshot = SOCKETS.snapshot_for(match, match.players[0])
    fiend = next((pet for pet in snapshot.get("you_pets", []) if pet.get("name") == "Shadowfiend"), None)
    assert fiend is not None, "Shadowfiend should appear in pet snapshot payload"
    labels = [status.get("label") for status in fiend.get("statuses", []) if isinstance(status, dict)]
    assert not any(isinstance(label, str) and label.endswith("T") for label in labels), "Shadowfiend pet box should not include remaining-turn badge text"
    return True


def scenario_champion_mouseover_payload_contract() -> bool:
    match = make_match("warrior", "mage", seed=7101)
    p1_sid, p2_sid = match.players
    p1 = match.state[p1_sid]
    p2 = match.state[p2_sid]
    p1.entity_type = "beast"

    p1.effects.append({"id": "test_atk_boost", "type": "stat_mod", "stat": "atk", "flat": 4})
    p1.effects.append({"id": "test_armor_boost", "type": "stat_mods", "mods": {"physical_reduction": 3, "fire_resist": 2}})
    p2.effects.append({"id": "test_magic_boost", "type": "stat_mods", "mods": {"magic_resist": 2, "arcane_resist": 1}})

    viewer_snapshot = SOCKETS.snapshot_for(match, p1_sid)
    enemy_snapshot = SOCKETS.snapshot_for(match, p2_sid)
    you_payload = viewer_snapshot.get("you_champion_mouseover") or {}
    enemy_payload = viewer_snapshot.get("enemy_champion_mouseover") or {}

    assert you_payload.get("entity_type") == "beast", "Mouseover payload should expose live champion entity_type"
    expected_stats = ("atk", "int", "def", "spd", "crit", "acc", "eva", "spirit")
    for stat in expected_stats:
        stat_payload = (you_payload.get("stats") or {}).get(stat)
        assert isinstance(stat_payload, dict), f"Mouseover stat '{stat}' must use structured payload"
        assert {"value", "base", "is_increased"} <= set(stat_payload.keys()), f"Mouseover stat '{stat}' must include value/base/is_increased"
    atk_payload = you_payload.get("stats", {}).get("atk") or {}
    assert atk_payload.get("value") == p1.stats.get("atk", 0) + 4, "Mouseover stats should reflect runtime stat_mod changes"
    assert atk_payload.get("base") == CLASSES[p1.build.class_id]["base_stats"]["atk"], "Mouseover stat base should come from class baseline"
    assert atk_payload.get("is_increased") is True, "Mouseover stat should flag increases above class baseline"

    you_mitigations = you_payload.get("mitigations") or {}
    assert "physical_reduction" in you_mitigations and "magic_resist" in you_mitigations, "Mouseover payload must include mitigation fields"
    assert you_mitigations.get("physical_reduction") == {"value": p1.stats.get("physical_reduction", 0) + 3, "base": 0, "is_increased": True}, "Physical reduction should be structured and compared against normalized base"

    subschool = you_payload.get("subschool_resist") or {}
    for school in ("fire", "frost", "shadow", "arcane", "nature", "holy"):
        assert school in subschool, f"Mouseover payload must include subschool '{school}' resist value"
    assert subschool.get("fire") == {"value": 2, "base": 0, "is_increased": True}, "Subschool resist values should be structured and compared against normalized base"

    assert enemy_payload.get("mitigations", {}).get("magic_resist") == {"value": p2.stats.get("magic_resist", 0) + 2, "base": 0, "is_increased": True}, "Enemy mouseover should expose structured runtime magic resist"
    assert enemy_payload.get("subschool_resist", {}).get("arcane") == {"value": 1, "base": 0, "is_increased": True}, "Enemy mouseover should expose structured runtime subschool resist"

    enemy_view_you_payload = enemy_snapshot.get("enemy_champion_mouseover") or {}
    assert enemy_view_you_payload == you_payload, "Mouseover payload contract should stay stable across friendly/enemy viewer snapshots"
    return True


def scenario_balance_metadata_updates_and_shadowstrike_rename() -> bool:
    assert ABILITIES["shield_of_vengeance"]["cooldown"] == 15, "Shield of Vengeance cooldown should be 15"
    assert ABILITIES["psychic_scream"]["cost"]["mp"] == 6, "Psychic Scream mana cost should be 6"
    assert ABILITIES["mass_dispel"]["cost"]["mp"] == 8, "Mass Dispel mana cost should be 8"
    assert ABILITIES["agony"]["cooldown"] == 20, "Agony cooldown should be 20"
    assert ABILITIES["summon_imp"]["cooldown"] == 3, "Summon Imp cooldown should be 3"
    assert "shadowstrike" in ABILITIES, "Shadowstrike command should exist"
    assert ABILITIES["shadowstrike"]["name"] == "Shadowstrike", "Shadowstrike displayed name should be updated"
    assert "shadow_blade" not in ABILITIES, "Legacy shadow_blade command should be removed after rename"
    return True


def scenario_duel_html_agony_docs_updated() -> bool:
    duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
    assert "<h4>Agony</h4>" in duel_html_text, "Agony docs header should be present"
    assert '<p><span class="stat">Cost: 20 Mana</span> | <span class="stat">Cooldown: 20</span></p>' in duel_html_text, "Agony docs cost/cooldown should be updated"
    assert '<p><span class="stat">Command: <span class="kbd">agony</span></span></p>' in duel_html_text, "Agony docs command should be present"
    assert "Inflicts Agony for 10 turns. Deals increasing magical DoT damage from 1 up to 10. Not dispellable and not stackable. Agony ignores Magic Resist and Damage Reductions." in duel_html_text, "Agony docs description should match the requested wording exactly"
    return True


def scenario_effect_panel_payload_normalization() -> bool:
    match = make_match("warrior", "warlock", seed=6106)
    warrior_sid, warlock_sid = match.players
    warrior = match.state[warrior_sid]

    effects.apply_effect_by_id(warrior, "die_by_sword", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "die_by_sword_mitigation", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "hot_streak", overrides={"duration": 3})
    effects.apply_effect_by_id(warrior, "agony", overrides={"duration": 4, "source_sid": warlock_sid})
    effects.apply_effect_by_id(warrior, "dragon_roar_bleed", overrides={"duration": 3, "source_sid": warlock_sid})
    effects.apply_effect_by_id(warrior, "raptor_strike_proc", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "arcane_surge", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "rip_ready", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "starfire_ready", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "mind_blast_empowered", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "burn", overrides={"duration": 3, "tick_damage": 2, "source_sid": warlock_sid})
    effects.apply_effect_by_id(warrior, "crusader_empower", overrides={"duration": 1})
    effects.apply_effect_by_id(warrior, "ambush", overrides={"duration": 2})
    effects.apply_effect_by_id(warrior, "blocking_defence", overrides={"duration": 1})
    effects.apply_effect_by_id(warrior, "bear_form_stats", overrides={"duration": 2})
    effects.apply_effect_by_id(
        warrior,
        "stunned",
        overrides={"duration": 1, "source_ability_name": "Hammer of Justice", "school": "magical"},
    )
    effects.apply_effect_by_id(
        warrior,
        "stunned",
        overrides={"duration": 2, "source_ability_name": "Maim", "school": "physical"},
    )

    before = state_extract(match)
    panel = effects.build_effect_panel_payload(warrior)
    after = state_extract(match)
    assert before == after, "Effect-panel payload helper should be read-only and must not mutate gameplay state"

    assert list(panel.keys()) == ["buffs_physical", "buffs_magical", "debuffs_physical", "debuffs_magical"], "Effect panel payload should expose exactly four ordered buckets"
    physical_buffs = [entry.get("name") for entry in panel["buffs_physical"]]
    magical_buffs = [entry.get("name") for entry in panel["buffs_magical"]]
    physical_debuffs = [entry.get("name") for entry in panel["debuffs_physical"]]
    magical_debuffs = [entry.get("name") for entry in panel["debuffs_magical"]]
    all_names = set(physical_buffs + magical_buffs + physical_debuffs + magical_debuffs)
    all_entries = [entry for bucket in panel.values() for entry in bucket]

    assert all("description" in entry for entry in all_entries), "Each visible effect entry should include a description field"
    assert all("stacks" not in entry for entry in all_entries), "Non-stackable payload entries should not include stack counts"
    assert physical_buffs.count("Die by the Sword") == 1, "Die by the Sword should appear exactly once as a physical buff"
    assert "Killing Frenzy" in physical_buffs, "Raptor Strike proc should render as Killing Frenzy in physical buffs"
    assert "Ambush" in physical_buffs, "Ambush should appear as a physical buff"
    assert "Hot Streak" in magical_buffs, "Hot Streak should appear in magical buffs"
    assert "Arcane Surge" in magical_buffs, "Arcane Surge should render as Arcane Surge in magical buffs"
    assert "Astral Surge" in magical_buffs, "Starfire proc should render as Astral Surge in magical buffs"
    assert "Mind Assault" in magical_buffs, "Mind Blast empowerment should render as Mind Assault in magical buffs"
    assert "Crusader's Might" in magical_buffs, "Crusader's Greatsword proc should render as Crusader's Might in magical buffs"
    assert "Sharpened Claws" in physical_buffs, "Rip proc should render as Sharpened Claws in physical buffs"
    assert "Rending Roar" in physical_debuffs, "Dragon Roar bleed should render as Rending Roar in physical debuffs"
    assert "Maim" in physical_debuffs and "Maim" not in magical_debuffs, "Maim should classify as a physical debuff"
    assert "Agony" in magical_debuffs, "Agony should appear in magical debuffs"
    assert "Fire Burn" in magical_debuffs, "Wand of Fire burn should render as Fire Burn in magical debuffs"
    assert "Hammer of Justice" in magical_debuffs, "Shared stunned runtime effects should safely fallback into debuff buckets"
    entries_by_name = {entry.get("name"): entry for entry in all_entries}
    assert entries_by_name["Rending Roar"].get("description") == "Bleed inflicted by Dragon Roar.", "Rending Roar description should match source text"
    assert entries_by_name["Mind Assault"].get("description") == "Mind Blast empowered.", "Mind Assault description should match source text"
    assert entries_by_name["Fire Burn"].get("description") == "Damage over time every turn.", "Fire Burn description should match source text"
    assert entries_by_name["Ambush"].get("description") == "Able to cast Ambush (if not on cooldown).", "Ambush description should match source text"
    assert all("duration" not in str(entry.get("description") or "").lower() for entry in all_entries), "Descriptions should not duplicate duration wording"
    assert "Blocking Defence" not in all_names and "Guarded" not in all_names, "Implementation-detail redirect helpers should not leak into the panel"
    assert "bear_form_stats" not in all_names and "Bear Form Stats" not in all_names, "Companion stat effects should not leak into the panel"

    snapshot = SOCKETS.snapshot_for(match, warrior_sid)
    assert snapshot.get("you_effect_panel") == panel, "Snapshot should expose viewer effect panel payload as normalized backend data"
    enemy_panel = snapshot.get("enemy_effect_panel")
    assert isinstance(enemy_panel, dict) and list(enemy_panel.keys()) == ["buffs_physical", "buffs_magical", "debuffs_physical", "debuffs_magical"], "Enemy snapshot should also expose all four effect panel buckets"
    return True


def scenario_high_risk_snapshot_payload_stability_pack() -> bool:
    match = make_match("warrior", "hunter", seed=8601)
    p1_sid, p2_sid = match.players
    submit_turn(match, _DEF_PASS, "call_saber")
    submit_turn(match, _DEF_PASS, "turtle")
    p1_snapshot = SOCKETS.snapshot_for(match, p1_sid)
    p2_snapshot = SOCKETS.snapshot_for(match, p2_sid)
    assert isinstance(p1_snapshot.get("you_effect_panel"), dict) and isinstance(p1_snapshot.get("enemy_effect_panel"), dict), "Viewer snapshot should expose both friendly/enemy effect panels"
    assert isinstance(p2_snapshot.get("you_effect_panel"), dict) and isinstance(p2_snapshot.get("enemy_effect_panel"), dict), "Enemy-view snapshot should also expose both effect panels"
    assert p1_snapshot.get("you_entity_type") == "humanoid" and p1_snapshot.get("enemy_entity_type") == "humanoid", "Champion entity types should be present in snapshots"
    assert any(isinstance(pet.get("entity_type"), str) and pet.get("entity_type") for pet in p1_snapshot.get("you_pets", []) + p1_snapshot.get("enemy_pets", [])), "Pet entity types should be present in snapshot payload"

    hunter = match.state[p2_sid]
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None, "Frostsaber should be present before pet removal check"
    saber.hp = 1
    match.state[p1_sid].res.rage = 10
    submit_turn(match, "dragon_roar", _DEF_PASS)
    post_death_snapshot = SOCKETS.snapshot_for(match, p2_sid)
    assert not any(pet.get("name") == "Frostsaber" for pet in post_death_snapshot.get("you_pets", [])), "Pet panel state should clear after pet death/removal"

    target = match.state[p1_sid]
    effects.apply_effect_by_id(target, "ice_barrier", overrides={"duration": 8})
    effects.add_absorb(target, 3, source_name="Ice Barrier", effect_id="ice_barrier")
    assert any(entry.get("name") == "Ice Barrier" for entry in SOCKETS.snapshot_for(match, p1_sid)["you_effect_panel"]["buffs_magical"]), "Shield should appear in snapshot panel while active"
    effects.consume_absorbs(target, 3)
    after_consume_snapshot = SOCKETS.snapshot_for(match, p1_sid)
    assert not any(entry.get("name") == "Ice Barrier" for entry in after_consume_snapshot["you_effect_panel"]["buffs_magical"]), "Consumed shield should be removed from snapshot panel payload"
    return True
