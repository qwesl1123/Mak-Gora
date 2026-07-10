"""Class-specific ability regression scenarios (paladin, priest, shaman, warrior, hunter, mindgames special handlers).

Moved verbatim from tests/regression_suite.py; registered in
tests/regression/registry.py, which preserves the original run order.
"""
from __future__ import annotations

import re

from typing import Any

from harness import (
    ABILITIES,
    CLASSES,
    EFFECT_TEMPLATES,
    PetState,
    _detect_duel_html_path,
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
    _pet_took_damage_or_died,
    _active_pet,
)


def scenario_mindgames_lay_on_hands() -> bool:
    match = make_match("priest", "paladin", seed=123)
    pal = match.state[match.players[1]]
    pal.res.hp = max(1, pal.res.hp - 40)

    submit_turn(match, "mindgames", "lay_on_hands")

    assert pal.res.hp < pal.res.hp_max, "Lay on Hands should be twisted into self-damage under Mindgames"
    assert not _has_effect(pal, "mindgames"), "Mindgames should expire after use"
    return True


def scenario_special_handler_healthstone_mindgames_parity() -> bool:
    match = make_match("warlock", "priest", seed=9110)
    warlock_sid, priest_sid = match.players
    warlock = match.state[warlock_sid]
    hp_before = warlock.res.hp

    submit_turn(match, "healthstone", "mindgames")

    expected_self_damage = max(1, int(warlock.res.hp_max * 0.25))
    assert warlock.res.hp == hp_before - expected_self_damage, "Healthstone should still be twisted into fixed self-damage under Mindgames"
    turn_lines = _turn_lines(match, 1)
    assert any("Mindgames twists healing into" in line for line in turn_lines), "Healthstone path should keep Mindgames twist wording"
    assert not any("Healthstone restores" in line for line in turn_lines), "Healthstone restore log should not appear when Mindgames twists healing"
    assert warlock.cooldowns.get("healthstone"), "Healthstone should still consume cooldown via special handler dispatch"
    return True


def scenario_generic_on_hit_healing_mindgames_backend() -> bool:
    normal_match = make_match("warrior", "priest", seed=9128)
    warrior_sid, priest_sid = normal_match.players
    warrior = normal_match.state[warrior_sid]
    priest = normal_match.state[priest_sid]
    warrior.stats["acc"] = 999
    priest.stats["agi"] = 0
    warrior.res.hp = max(1, warrior.res.hp - 50)
    normal_hp_before = warrior.res.hp

    submit_turn(normal_match, "victory_rush", _DEF_PASS)

    assert warrior.res.hp > normal_hp_before, "Victory Rush should preserve normal on-hit healing without Mindgames"
    assert normal_match.combat_totals[warrior_sid]["healing"] > 0, "Normal Victory Rush healing should count toward healing totals"
    normal_turn = _turn_lines(normal_match, 1)
    assert any("Heals" in line for line in normal_turn), "Normal Victory Rush healing log should be preserved"

    mindgames_match = make_match("warrior", "priest", seed=9128)
    warrior_sid, priest_sid = mindgames_match.players
    warrior = mindgames_match.state[warrior_sid]
    priest = mindgames_match.state[priest_sid]
    warrior.stats["acc"] = 999
    priest.stats["agi"] = 0
    warrior.res.hp = max(1, warrior.res.hp - 50)
    mindgames_hp_before = warrior.res.hp

    submit_turn(mindgames_match, "victory_rush", "mindgames")

    assert warrior.res.hp < mindgames_hp_before, "Mindgames should turn generic on-hit healing into self-damage"
    assert mindgames_match.combat_totals[warrior_sid]["healing"] == 0, "Mindgames-twisted on-hit healing should report zero healing"
    mindgames_turn = _turn_lines(mindgames_match, 1)
    assert any("Mindgames twists healing into" in line for line in mindgames_turn), "Generic on-hit healing should use backend Mindgames twist logging"
    assert not any("Heals" in line for line in mindgames_turn), "Twisted Victory Rush should not log normal healing"
    return True


def scenario_special_handler_innervate_mana_and_cooldown() -> bool:
    match = make_match("druid", "warrior", seed=9111)
    druid_sid, _ = match.players
    druid = match.state[druid_sid]
    effects.apply_effect_by_id(druid, "tree_form", overrides={"duration": 3})
    druid.res.mp = 1

    submit_turn(match, "innervate", _DEF_PASS)

    assert druid.res.mp == druid.res.mp_max, "Innervate should still restore mana to full through special handler dispatch"
    assert druid.cooldowns.get("innervate"), "Innervate should still trigger cooldown"
    assert any("restores their mana to full." in line for line in _turn_lines(match, 1)), "Innervate log wording should remain unchanged"
    return True


def scenario_special_handler_non_handler_baseline_path_unchanged() -> bool:
    assert "die_by_sword" not in resolver.SPECIAL_ABILITY_HANDLERS, "Die by the Sword should remain on the baseline resolver path"

    match = make_match("warrior", "priest", seed=9112)
    warrior_sid, _ = match.players
    warrior = match.state[warrior_sid]

    submit_turn(match, "die_by_sword", _DEF_PASS)

    assert _has_effect(warrior, "die_by_sword"), "Baseline non-handler Die by the Sword should still apply its defensive effect"
    assert warrior.cooldowns.get("die_by_sword"), "Baseline non-handler Die by the Sword should still consume cooldown"
    assert any("Die by the Sword" in line for line in _turn_lines(match, 1)), "Baseline non-handler Die by the Sword log wording should remain unchanged"
    return True


def scenario_special_handler_mass_dispel_parity_and_denial_order() -> bool:
    assert "mass_dispel" in resolver.SPECIAL_ABILITY_HANDLERS, "Mass Dispel should be routed through special handler dispatch in phase 3"

    parity_match = make_match("priest", "rogue", seed=9122)
    priest_sid, rogue_sid = parity_match.players
    priest = parity_match.state[priest_sid]
    rogue = parity_match.state[rogue_sid]
    effects.apply_effect_by_id(priest, "devouring_plague", overrides={"duration": 3, "source_sid": rogue_sid})
    effects.apply_effect_by_id(priest, "burn", overrides={"duration": 2, "tick_damage": 3, "source_sid": rogue_sid})
    effects.apply_effect_by_id(rogue, "divine_shield", overrides={"duration": 2})
    effects.apply_effect_by_id(rogue, "iceblock", overrides={"duration": 2})

    submit_turn(parity_match, "mass_dispel", _DEF_PASS)

    assert not _has_effect(priest, "devouring_plague"), "Mass Dispel handler should still remove self harmful magical debuffs"
    assert not _has_effect(priest, "burn"), "Mass Dispel handler should still remove self magical DoTs"
    assert not _has_effect(rogue, "divine_shield"), "Mass Dispel handler should still remove enemy magical immunity buffs"
    assert not _has_effect(rogue, "iceblock"), "Mass Dispel handler should still remove enemy magical shields"
    assert not _has_effect(rogue, "stealth"), "Mass Dispel handler should still remove enemy stealth"
    parity_turn = _turn_lines(parity_match, 1)
    assert any("removed by Mass Dispel!" in line for line in parity_turn), "Mass Dispel handler should preserve removal summary wording"
    assert any("Dispels 2 effects on self and 3 effects on enemy" in line for line in parity_turn), "Mass Dispel handler should preserve dispel count wording and order"

    denied_match = make_match("priest", "warrior", seed=9123)
    denied_priest_sid, denied_enemy_sid = denied_match.players
    denied_priest = denied_match.state[denied_priest_sid]
    denied_enemy = denied_match.state[denied_enemy_sid]
    effects.apply_effect_by_id(denied_priest, "feared", overrides={"duration": 1})
    effects.apply_effect_by_id(denied_priest, "devouring_plague", overrides={"duration": 2, "source_sid": denied_enemy_sid})
    effects.apply_effect_by_id(denied_enemy, "pain_suppression", overrides={"duration": 2})
    submit_turn(denied_match, "mass_dispel", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Mass Dispel but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Mass Dispel handler execution"
    assert _has_effect(denied_priest, "devouring_plague"), "Denied Mass Dispel should not remove self effects"
    assert _has_effect(denied_enemy, "pain_suppression"), "Denied Mass Dispel should not remove enemy effects"
    assert "mass_dispel" not in denied_priest.cooldowns, "Denied Mass Dispel should not consume cooldown"
    assert not any("removed by Mass Dispel!" in line for line in denied_turn), "Denied Mass Dispel should not emit handler removal logs"
    return True


def scenario_special_handler_holy_light_parity_and_denial_order() -> bool:
    assert "holy_light" in resolver.SPECIAL_ABILITY_HANDLERS, "Holy Light should be routed through special handler dispatch in phase 2"

    heal_match = make_match("paladin", "warrior", seed=9113)
    paladin_sid, _ = heal_match.players
    paladin = heal_match.state[paladin_sid]
    paladin.res.hp = max(1, paladin.res.hp - 40)
    hp_before = paladin.res.hp
    submit_turn(heal_match, "holy_light", _DEF_PASS)
    assert paladin.res.hp > hp_before, "Holy Light handler should still heal"
    assert any("Holy Light restores" in line for line in _turn_lines(heal_match, 1)), "Holy Light handler should preserve log wording"

    mindgames_match = make_match("priest", "paladin", seed=9114)
    priest_sid, paladin_sid = mindgames_match.players
    paladin = mindgames_match.state[paladin_sid]
    hp_before = paladin.res.hp
    submit_turn(mindgames_match, "mindgames", "holy_light")
    assert paladin.res.hp < hp_before, "Holy Light should still be twisted into self-damage under Mindgames"
    assert any("Mindgames twists healing into" in line for line in _turn_lines(mindgames_match, 1)), "Holy Light handler should preserve Mindgames twist log wording"

    denied_match = make_match("paladin", "warrior", seed=9115)
    denied_paladin_sid, _ = denied_match.players
    denied_paladin = denied_match.state[denied_paladin_sid]
    denied_paladin.res.hp = max(1, denied_paladin.res.hp - 20)
    hp_before = denied_paladin.res.hp
    effects.apply_effect_by_id(denied_paladin, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "holy_light", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Holy Light but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Holy Light handler execution"
    assert denied_paladin.res.hp == hp_before, "Denied Holy Light should not apply healing side effects"
    assert "holy_light" not in denied_paladin.cooldowns, "Denied Holy Light should not consume cooldown"
    assert not any("Holy Light restores" in line for line in denied_turn), "Denied Holy Light should not produce handler heal log"
    return True


def scenario_special_handler_flash_heal_parity_and_denial_order() -> bool:
    assert "flash_heal" in resolver.SPECIAL_ABILITY_HANDLERS, "Flash Heal should be routed through special handler dispatch in phase 2"

    heal_match = make_match("priest", "warrior", seed=9116)
    priest_sid, _ = heal_match.players
    priest = heal_match.state[priest_sid]
    priest.res.hp = max(1, priest.res.hp - 30)
    hp_before = priest.res.hp
    submit_turn(heal_match, "flash_heal", _DEF_PASS)
    assert priest.res.hp > hp_before, "Flash Heal handler should still heal"
    assert any("Flash Heal restores" in line for line in _turn_lines(heal_match, 1)), "Flash Heal handler should preserve log wording"

    mindgames_match = make_match("priest", "priest", seed=9117)
    _, priest_sid = mindgames_match.players
    priest = mindgames_match.state[priest_sid]
    hp_before = priest.res.hp
    submit_turn(mindgames_match, "mindgames", "flash_heal")
    assert priest.res.hp < hp_before, "Flash Heal should still be twisted into self-damage under Mindgames"
    assert any("Mindgames twists healing into" in line for line in _turn_lines(mindgames_match, 1)), "Flash Heal handler should preserve Mindgames twist log wording"

    denied_match = make_match("priest", "warrior", seed=9118)
    denied_priest_sid, _ = denied_match.players
    denied_priest = denied_match.state[denied_priest_sid]
    denied_priest.res.hp = max(1, denied_priest.res.hp - 20)
    hp_before = denied_priest.res.hp
    effects.apply_effect_by_id(denied_priest, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "flash_heal", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Flash Heal but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Flash Heal handler execution"
    assert denied_priest.res.hp == hp_before, "Denied Flash Heal should not apply healing side effects"
    assert "flash_heal" not in denied_priest.cooldowns, "Denied Flash Heal should not consume cooldown"
    assert not any("Flash Heal restores" in line for line in denied_turn), "Denied Flash Heal should not produce handler heal log"
    return True


def scenario_special_handler_lay_on_hands_parity_and_denial_order() -> bool:
    assert "lay_on_hands" in resolver.SPECIAL_ABILITY_HANDLERS, "Lay on Hands should be routed through special handler dispatch in phase 2"

    heal_match = make_match("paladin", "warrior", seed=9119)
    paladin_sid, _ = heal_match.players
    paladin = heal_match.state[paladin_sid]
    paladin.res.hp = max(1, paladin.res.hp - 60)
    missing_hp = paladin.res.hp_max - paladin.res.hp
    submit_turn(heal_match, "lay_on_hands", _DEF_PASS)
    assert paladin.res.hp == paladin.res.hp_max, "Lay on Hands handler should still full-heal"
    assert any("Lay on Hands restores health to full." in line for line in _turn_lines(heal_match, 1)), "Lay on Hands handler should preserve log wording"
    assert paladin.cooldowns.get("lay_on_hands"), "Lay on Hands handler should preserve cooldown behavior"
    assert missing_hp > 0, "Lay on Hands parity test should start with missing health"

    mindgames_match = make_match("priest", "paladin", seed=9120)
    paladin = mindgames_match.state[mindgames_match.players[1]]
    paladin.res.hp = max(1, paladin.res.hp - 40)
    hp_before = paladin.res.hp
    submit_turn(mindgames_match, "mindgames", "lay_on_hands")
    assert paladin.res.hp < hp_before, "Lay on Hands should still be twisted into self-damage under Mindgames"
    assert any("Mindgames twists healing into" in line for line in _turn_lines(mindgames_match, 1)), "Lay on Hands handler should preserve Mindgames twist log wording"

    denied_match = make_match("paladin", "warrior", seed=9121)
    denied_paladin_sid, _ = denied_match.players
    denied_paladin = denied_match.state[denied_paladin_sid]
    denied_paladin.res.hp = max(1, denied_paladin.res.hp - 35)
    hp_before = denied_paladin.res.hp
    effects.apply_effect_by_id(denied_paladin, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "lay_on_hands", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Lay on Hands but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Lay on Hands handler execution"
    assert denied_paladin.res.hp == hp_before, "Denied Lay on Hands should not apply healing side effects"
    assert "lay_on_hands" not in denied_paladin.cooldowns, "Denied Lay on Hands should not consume cooldown"
    assert not any("Lay on Hands restores health to full." in line for line in denied_turn), "Denied Lay on Hands should not produce handler heal log"
    return True


def scenario_special_handler_frenzied_regeneration_parity_and_denial_order() -> bool:
    assert "frenzied_regeneration" in resolver.SPECIAL_ABILITY_HANDLERS, "Frenzied Regeneration should be routed through special handler dispatch in this extraction pass"

    parity_match = make_match("druid", "warrior", seed=9124)
    druid_sid, _ = parity_match.players
    druid = parity_match.state[druid_sid]
    effects.apply_effect_by_id(druid, "bear_form", overrides={"duration": 2})
    druid.res.rage = 28

    submit_turn(parity_match, "frenzied_regeneration", _DEF_PASS)

    regen = next((fx for fx in druid.effects if fx.get("id") == "frenzied_regeneration"), None)
    assert regen is not None, "Frenzied Regeneration handler should still apply the same HoT effect"
    assert int(((regen.get("regen") or {}).get("hp") or 0)) == 7, "Frenzied Regeneration handler should preserve rage-to-regen conversion"
    assert druid.res.rage == 0, "Frenzied Regeneration handler should still consume all rage"
    assert druid.cooldowns.get("frenzied_regeneration"), "Frenzied Regeneration handler should still consume cooldown on successful cast"
    assert any("channels Frenzied Regeneration." in line for line in _turn_lines(parity_match, 1)), "Frenzied Regeneration handler should preserve log wording"

    denied_match = make_match("druid", "warrior", seed=9125)
    denied_druid_sid, _ = denied_match.players
    denied_druid = denied_match.state[denied_druid_sid]
    effects.apply_effect_by_id(denied_druid, "bear_form", overrides={"duration": 2})
    denied_druid.res.rage = 24
    effects.apply_effect_by_id(denied_druid, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "frenzied_regeneration", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Frenzied Regeneration but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Frenzied Regeneration handler execution"
    assert denied_druid.res.rage == 24, "Denied Frenzied Regeneration should not consume rage"
    assert not _has_effect(denied_druid, "frenzied_regeneration"), "Denied Frenzied Regeneration should not apply regen effect"
    assert "frenzied_regeneration" not in denied_druid.cooldowns, "Denied Frenzied Regeneration should not consume cooldown"
    assert not any("channels Frenzied Regeneration." in line for line in denied_turn), "Denied Frenzied Regeneration should not emit handler success log"
    return True


def scenario_special_handler_wild_growth_parity_and_denial_order() -> bool:
    assert "wild_growth" in resolver.SPECIAL_ABILITY_HANDLERS, "Wild Growth should be routed through special handler dispatch in this extraction pass"

    heal_match = make_match("druid", "warrior", seed=9126)
    druid_sid, _ = heal_match.players
    druid = heal_match.state[druid_sid]
    effects.apply_effect_by_id(druid, "tree_form", overrides={"duration": 3})
    druid.res.hp = max(1, druid.res.hp - 30)
    hp_before = druid.res.hp
    submit_turn(heal_match, "wild_growth", _DEF_PASS)
    assert druid.res.hp > hp_before, "Wild Growth handler should still heal"
    assert any("Wild Growth heals" in line for line in _turn_lines(heal_match, 1)), "Wild Growth handler should preserve log wording"

    mindgames_match = make_match("priest", "druid", seed=9127)
    _, druid_sid = mindgames_match.players
    druid = mindgames_match.state[druid_sid]
    effects.apply_effect_by_id(druid, "tree_form", overrides={"duration": 3})
    hp_before = druid.res.hp
    submit_turn(mindgames_match, "mindgames", "wild_growth")
    assert druid.res.hp < hp_before, "Wild Growth should still be twisted into self-damage under Mindgames"
    assert any("Mindgames twists healing into" in line for line in _turn_lines(mindgames_match, 1)), "Wild Growth handler should preserve Mindgames twist log wording"

    cycloned_match = make_match("druid", "druid", seed=9128)
    actor_sid, _ = cycloned_match.players
    cycloned_druid = cycloned_match.state[actor_sid]
    effects.apply_effect_by_id(cycloned_druid, "tree_form", overrides={"duration": 3})
    cycloned_druid.res.hp = max(1, cycloned_druid.res.hp - 40)
    effects.apply_effect_by_id(cycloned_druid, "cyclone", overrides={"duration": 1})
    hp_before = cycloned_druid.res.hp
    submit_turn(cycloned_match, "wild_growth", _DEF_PASS)
    turn_one = _turn_lines(cycloned_match, 1)
    assert any("tries to use Wild Growth but is cycloned and cannot act." in line for line in turn_one), "Cycloned Wild Growth should be denied before special handler execution"
    assert cycloned_druid.res.hp == hp_before, "Cycloned Wild Growth cast should not apply healing"
    assert "wild_growth" not in cycloned_druid.cooldowns, "Cycloned-denied Wild Growth should not consume cooldown"

    denied_match = make_match("druid", "warrior", seed=9129)
    denied_druid_sid, _ = denied_match.players
    denied_druid = denied_match.state[denied_druid_sid]
    effects.apply_effect_by_id(denied_druid, "tree_form", overrides={"duration": 3})
    denied_druid.res.hp = max(1, denied_druid.res.hp - 25)
    hp_before = denied_druid.res.hp
    effects.apply_effect_by_id(denied_druid, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "wild_growth", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Wild Growth but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Wild Growth handler execution"
    assert denied_druid.res.hp == hp_before, "Denied Wild Growth should not apply healing side effects"
    assert "wild_growth" not in denied_druid.cooldowns, "Denied Wild Growth should not consume cooldown"
    assert not any("Wild Growth heals" in line for line in denied_turn), "Denied Wild Growth should not emit handler heal log"
    return True


def scenario_special_handler_regrowth_parity_and_denial_order() -> bool:
    assert "regrowth" in resolver.SPECIAL_ABILITY_HANDLERS, "Regrowth should be routed through special handler dispatch in this extraction pass"

    parity_match = make_match("druid", "warrior", seed=9130)
    druid_sid, _ = parity_match.players
    druid = parity_match.state[druid_sid]
    effects.apply_effect_by_id(druid, "tree_form", overrides={"duration": 3})

    submit_turn(parity_match, "regrowth", _DEF_PASS)

    regrowth_fx = next((fx for fx in druid.effects if fx.get("id") == "regrowth"), None)
    assert regrowth_fx is not None, "Regrowth handler should still apply regrowth HoT effect"
    assert int(regrowth_fx.get("duration") or 0) == 4, "Regrowth handler should preserve same-turn tick/decrement semantics for regrowth duration"
    assert int(((regrowth_fx.get("regen") or {}).get("hp") or 0)) > 0, "Regrowth handler should preserve positive per-tick healing"
    assert druid.cooldowns.get("regrowth"), "Regrowth handler should still consume cooldown"
    assert any("Healing over time for 5 turns." in line for line in _turn_lines(parity_match, 1)), "Regrowth handler should preserve log wording"

    denied_match = make_match("druid", "warrior", seed=9131)
    denied_druid_sid, _ = denied_match.players
    denied_druid = denied_match.state[denied_druid_sid]
    effects.apply_effect_by_id(denied_druid, "tree_form", overrides={"duration": 3})
    effects.apply_effect_by_id(denied_druid, "feared", overrides={"duration": 1})
    submit_turn(denied_match, "regrowth", _DEF_PASS)
    denied_turn = _turn_lines(denied_match, 1)
    assert any("tries to use Regrowth but is feared and cannot act." in line for line in denied_turn), "Denial should still occur before Regrowth handler execution"
    assert not _has_effect(denied_druid, "regrowth"), "Denied Regrowth should not apply HoT effect"
    assert "regrowth" not in denied_druid.cooldowns, "Denied Regrowth should not consume cooldown"
    assert not any("Healing over time for 5 turns." in line for line in denied_turn), "Denied Regrowth should not emit handler success log"
    return True


def scenario_mindgames_shield_of_vengeance_explosion_interactions() -> bool:
    normal_damage = make_match("priest", "paladin", seed=9101)
    priest_sid, pal_sid = normal_damage.players
    priest = normal_damage.state[priest_sid]
    priest.res.hp = max(1, priest.res.hp - 20)
    hp_before = priest.res.hp
    submit_turn(normal_damage, "mindgames", "judgment")
    assert priest.res.hp > hp_before, "Mindgames should still flip normal Paladin damage into healing"
    assert any("Mindgames flips damage into" in line for line in _turn_lines(normal_damage, 1)), "Normal Paladin damage path should still log Mindgames flips"

    sov_mindgames = make_match("priest", "paladin", seed=9102)
    priest_sid, pal_sid = sov_mindgames.players
    priest = sov_mindgames.state[priest_sid]
    paladin = sov_mindgames.state[pal_sid]
    effects.apply_effect_by_id(paladin, "shield_of_vengeance", overrides={"duration": 1, "absorbed": 18})
    priest.res.hp = max(1, priest.res.hp - 20)
    hp_before = priest.res.hp
    submit_turn(sov_mindgames, "mindgames", _DEF_PASS)
    assert any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_mindgames, 1)), "Shield of Vengeance should still explode on expiry"
    assert priest.res.hp > hp_before, "Shield of Vengeance explosion should be flipped into healing under Mindgames"
    assert any("Shield of Vengeance hits" in line and "Mindgames flips damage into" in line for line in _turn_lines(sov_mindgames, 1)), "Shield of Vengeance explosion should use Mindgames flip logging"

    sov_no_mindgames = make_match("priest", "paladin", seed=9103)
    priest_sid, pal_sid = sov_no_mindgames.players
    priest = sov_no_mindgames.state[priest_sid]
    paladin = sov_no_mindgames.state[pal_sid]
    effects.apply_effect_by_id(paladin, "shield_of_vengeance", overrides={"duration": 1, "absorbed": 18})
    hp_before = priest.res.hp
    submit_turn(sov_no_mindgames, _DEF_PASS, _DEF_PASS)
    assert priest.res.hp < hp_before, "Shield of Vengeance explosion should still deal normal damage without Mindgames"

    sov_absorb = make_match("priest", "paladin", seed=9104)
    priest_sid, pal_sid = sov_absorb.players
    priest = sov_absorb.state[priest_sid]
    paladin = sov_absorb.state[pal_sid]
    effects.apply_effect_by_id(paladin, "shield_of_vengeance", overrides={"duration": 1, "absorbed": 18})
    effects.apply_effect_by_id(priest, "power_word_shield", overrides={"duration": 8})
    effects.add_absorb(priest, 18, source_name="Power Word: Shield", effect_id="power_word_shield")
    hp_before = priest.res.hp
    submit_turn(sov_absorb, _DEF_PASS, _DEF_PASS)
    assert priest.res.hp == hp_before, "Shield of Vengeance explosion should still respect absorbs when Mindgames is absent"
    assert any("Shield of Vengeance explodes!" in line for line in _turn_lines(sov_absorb, 1)), "SoV explosion should still trigger into active absorbs"
    return True


def scenario_mass_dispel_selective_removal() -> bool:
    match = make_match("priest", "paladin", seed=123)
    pal = match.state[match.players[1]]
    effects.apply_effect_by_id(pal, "divine_shield", overrides={"duration": 2})
    effects.apply_effect_by_id(pal, "iceblock", overrides={"duration": 2})
    effects.apply_effect_by_id(pal, "dragon_roar_bleed", overrides={"duration": 2})

    submit_turn(match, "mass_dispel", _DEF_PASS)

    assert not _has_effect(pal, "divine_shield"), "Mass Dispel should remove Divine Shield"
    assert not _has_effect(pal, "iceblock"), "Mass Dispel should remove Ice Block"
    assert _has_effect(pal, "dragon_roar_bleed"), "Mass Dispel should not remove physical Dragon Roar Bleed"
    return True


def scenario_mass_dispel_can_remove_pain_suppression_and_devouring_plague() -> bool:
    pain_match = make_match("priest", "warrior", seed=123)
    warrior = pain_match.state[pain_match.players[1]]
    effects.apply_effect_by_id(warrior, "pain_suppression", overrides={"duration": 3})
    submit_turn(pain_match, "mass_dispel", _DEF_PASS)
    assert not _has_effect(warrior, "pain_suppression"), "Mass Dispel should remove Pain Suppression via dispel metadata"

    plague_match = make_match("priest", "warrior", seed=123)
    priest = plague_match.state[plague_match.players[0]]
    effects.apply_effect_by_id(priest, "devouring_plague", overrides={"duration": 3, "source_sid": plague_match.players[1]})
    submit_turn(plague_match, "mass_dispel", _DEF_PASS)
    assert not _has_effect(priest, "devouring_plague"), "Mass Dispel should remove Devouring Plague via dispel metadata"
    return True


def scenario_shield_of_vengeance_duration_counts_current_turn() -> bool:
    match = make_match("paladin", "warrior", seed=123)
    paladin_sid = match.players[0]
    enemy_sid = match.players[1]
    paladin = match.state[paladin_sid]
    enemy = match.state[enemy_sid]

    submit_turn(match, "shield_of_vengeance", _DEF_PASS)
    shield = next((effect for effect in paladin.effects if effect.get("id") == "shield_of_vengeance"), None)
    assert shield is not None, "Shield of Vengeance should be applied when cast"
    assert int(shield.get("duration", 0) or 0) == 2, "Current turn should count; SoV should have 2 turns remaining after cast turn"

    shield["absorbed"] = 8
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert any(effect.get("id") == "shield_of_vengeance" for effect in paladin.effects), "SoV should still exist one turn before expiry"

    hp_before = enemy.res.hp
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    assert enemy.res.hp < hp_before, "SoV should explode on the following turn after the cast turn"
    assert any("Shield of Vengeance explodes!" in line for line in match.log), "Explosion log should be present on expiry"
    return True


def scenario_hunter_multi_shot_aoe() -> bool:
    match = make_match("hunter", "warlock", seed=123)
    hunter_sid, warlock_sid = match.players
    warlock = match.state[warlock_sid]
    for _ in range(3):
        warlock.cooldowns["summon_imp"] = []
        submit_turn(match, _DEF_PASS, "summon_imp")
    imp_ids = sorted(warlock.pets.keys())
    champion_hp_before = warlock.res.hp
    imp_hp_before = {pid: warlock.pets[pid].hp for pid in imp_ids}

    submit_turn(match, "multi_shot", _DEF_PASS)

    assert warlock.res.hp < champion_hp_before, "Multi-Shot should damage the enemy champion"
    for pid in imp_ids:
        assert _pet_took_damage_or_died(warlock, pid, imp_hp_before[pid]), "Multi-Shot should damage every enemy pet"
    shot_logs = [line for line in match.log if "Multi-Shot hits" in line and "Imp" in line]
    observed = []
    for line in shot_logs:
        if "(imp1)" in line:
            observed.append("imp1")
        elif "(imp2)" in line:
            observed.append("imp2")
        elif "(imp3)" in line:
            observed.append("imp3")
    assert observed[:3] == ["imp1", "imp2", "imp3"], "Multi-Shot pet hit order should be deterministic"
    return True


def scenario_dragon_roar_cannot_miss_from_accuracy() -> bool:
    match = make_match("warrior", "rogue", seed=123)
    warrior_sid, rogue_sid = match.players
    warrior = match.state[warrior_sid]
    rogue = match.state[rogue_sid]

    warrior.res.rage = warrior.res.rage_max
    warrior.stats["acc"] = 1
    rogue.stats["eva"] = 999
    hp_before = rogue.res.hp

    submit_turn(match, "dragon_roar", _DEF_PASS)

    assert rogue.res.hp < hp_before, "Dragon Roar should still land through extreme accuracy/evasion mismatch"
    dragon_lines = [line for line in match.log if "Dragon Roar" in line]
    assert not any("Miss!" in line for line in dragon_lines), "Dragon Roar should not log a normal miss roll"
    return True


def scenario_dragon_roar_bleed_applies_to_pets_with_independent_rolls() -> bool:
    match = make_match("warrior", "warlock", seed=1010)
    warrior_sid, warlock_sid = match.players
    warrior = match.state[warrior_sid]
    warlock = match.state[warlock_sid]

    warlock.cooldowns["summon_imp"] = []
    submit_turn(match, _DEF_PASS, "summon_imp")
    warlock.cooldowns["summon_imp"] = []
    submit_turn(match, _DEF_PASS, "summon_imp")
    assert len(warlock.pets) == 2, "Warlock should have two imps before Dragon Roar"

    warrior.res.rage = warrior.res.rage_max
    hp_before = warlock.res.hp
    pet_hp_before = {pid: pet.hp for pid, pet in warlock.pets.items()}

    original_roll = resolver.roll
    dot_roll_counter = {"count": 0}

    def _spy_roll(die: str, rng: Any) -> int:
        if die == "d2":
            dot_roll_counter["count"] += 1
            return dot_roll_counter["count"]
        return original_roll(die, rng)

    resolver.roll = _spy_roll
    try:
        submit_turn(match, "dragon_roar", _DEF_PASS)
    finally:
        resolver.roll = original_roll

    assert warlock.res.hp < hp_before, "Dragon Roar should still damage the enemy champion"
    for pid, before_hp in pet_hp_before.items():
        assert _pet_took_damage_or_died(warlock, pid, before_hp), "Dragon Roar should still damage every enemy pet"

    champ_bleed = next((fx for fx in warlock.effects if fx.get("id") == "dragon_roar_bleed"), None)
    assert champ_bleed is not None, "Dragon Roar should apply bleed to the enemy champion"

    pet_bleeds = []
    for pet in warlock.pets.values():
        bleed = next((fx for fx in pet.effects if fx.get("id") == "dragon_roar_bleed"), None)
        assert bleed is not None, f"Dragon Roar should apply bleed to pet {pet.name}"
        pet_bleeds.append((pet.name, int(bleed.get('tick_damage', 0) or 0)))

    expected_dot_rolls = 1 + len(pet_bleeds)
    assert dot_roll_counter["count"] == expected_dot_rolls, "Dragon Roar bleed should roll once per affected target"

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    latest_turn = _turn_lines(match, match.turn)
    assert any("suffers" in line and "Dragon Roar Bleed" in line and warlock_sid[:5] in line for line in latest_turn), "Champion bleed should tick independently"
    for pet_name, _ in pet_bleeds:
        assert any("suffers" in line and "Dragon Roar Bleed" in line and pet_name in line for line in latest_turn), f"Pet bleed should tick independently for {pet_name}"

    assert any("Dragon Roar applies bleed on Warlock." in line for line in match.log), "Champion bleed log should use champion class label"
    if pet_bleeds:
        assert any("Dragon Roar applies bleed on Warlock's Imp." in line for line in match.log), "Pet bleed log should use owner and pet name"
    else:
        assert not any("Dragon Roar applies bleed on Warlock's Imp." in line for line in match.log), "Dead pets should not get bleed logs"
    return True


def scenario_dragon_roar_dead_pets_do_not_log_bleed_application() -> bool:
    match = make_match("warrior", "warlock", seed=1313)
    warrior_sid, warlock_sid = match.players
    warrior = match.state[warrior_sid]
    warlock = match.state[warlock_sid]

    for _ in range(3):
        warlock.cooldowns["summon_imp"] = []
        submit_turn(match, _DEF_PASS, "summon_imp")
    assert len(warlock.pets) == 3, "Expected three imps before Dragon Roar"

    # Force all imps to die from the AoE hit so only champion should receive bleed logs.
    for pet in warlock.pets.values():
        pet.hp = 1
        pet.hp_max = max(1, pet.hp_max)

    warrior.res.rage = warrior.res.rage_max
    submit_turn(match, "dragon_roar", _DEF_PASS)

    dragon_roar_lines = [line for line in match.log if "Dragon Roar" in line]
    pet_bleed_lines = [line for line in dragon_roar_lines if "Dragon Roar applies bleed on Warlock's Imp." in line]
    assert not pet_bleed_lines, "Dead pets should not emit Dragon Roar bleed application logs"
    assert any("Dragon Roar applies bleed on Warlock." in line for line in dragon_roar_lines), "Champion bleed should still apply"
    assert sum(1 for line in match.log if line == "Imp dies.") == 3, "All imps should die from Dragon Roar in this setup"
    return True


def scenario_hunter_rework_phase1_phase2_regression() -> bool:
    wildfire = ABILITIES["wildfire_bomb"]
    assert wildfire.get("scaling") == {"atk": 0.7}, "Wildfire Bomb direct damage should use Attack 0.7x"
    assert (wildfire.get("dice") or {}).get("type") == "d8", "Wildfire Bomb direct damage should use d8"
    assert wildfire.get("school") == "magical" and wildfire.get("subschool") == "fire", "Wildfire Bomb should remain magical Fire"
    assert wildfire.get("cooldown") == 8, "Wildfire Bomb cooldown should be 8"
    assert wildfire.get("dot") == {"id": "wildfire_burn", "duration": 2, "school": "magical", "subschool": "fire", "scaling": {"atk": 0.5}, "dice": {"type": "d4", "power_on": "roll"}}, "Wildfire Burn DoT formula should remain unchanged"

    aimed = ABILITIES["aimed_shot"]
    assert aimed.get("scaling") == {"atk": 0.4} and (aimed.get("dice") or {}).get("type") == "d6", "Aimed Shot should use Attack 0.4x + d6"
    raptor = ABILITIES["raptor_strike"]
    assert raptor.get("scaling") == {"atk": 1.1} and (raptor.get("dice") or {}).get("type") == "d6", "Raptor Strike should use Attack 1.1x + d6"
    assert raptor.get("pet_command") == "special", "Raptor Strike should keep current forced-special behavior"

    arcane = ABILITIES["arcane_shot"]
    assert "requires_effect" not in arcane, "Arcane Shot should no longer require the old proc"
    assert "consume_effect" not in arcane, "Arcane Shot consumption should live inside empowered_by, not a top-level consume_effect"
    assert arcane.get("empowered_by") == {
        "effect_id": "arcane_surge",
        "scaling_override": {"atk": 1.0},
        "dice_override": {"type": "d8", "power_on": "roll"},
        "log": "Empowered by Arcane Surge!",
        "consume": {"mode": "remove"},
    }, "Arcane Shot should declare its Arcane Surge empowerment via empowered_by"
    assert arcane.get("cooldown") == 3, "Arcane Shot cooldown should be 3"
    assert arcane.get("scaling") == {"atk": 0.5} and (arcane.get("dice") or {}).get("type") == "d6", "Normal Arcane Shot should use Attack 0.5x + d6"
    assert arcane.get("school") == "magical" and arcane.get("subschool") == "arcane", "Arcane Shot should remain magical Arcane"

    kill = ABILITIES["kill_command"]
    assert kill.get("cost") == {"mp": 15} and kill.get("cooldown") == 6, "Kill Command should cost 15 mana with 6 cooldown"
    assert kill.get("requires_active_pet") is True and kill.get("pet_command") == "special", "Kill Command should require and force the current pet special"
    assert kill.get("pet_heal") == {"scaling": {"atk": 0.4}, "dice": {"type": "d4", "power_on": "roll"}}, "Kill Command should heal current pet from Hunter Attack 0.4x + d4"

    surge = EFFECT_TEMPLATES["arcane_surge"]
    assert surge.get("name") == "Arcane Surge" and surge.get("duration") == 3, "Arcane Surge template should last 3 turns"
    assert (wildfire.get("self_effects") or [{}])[0].get("duration") == 3, "Wildfire Bomb should grant Arcane Surge with duration 3"
    assert effects._EFFECT_PANEL_DESCRIPTION_BY_NAME.get("Arcane Surge") == "Arcane Shot empowered.", "Arcane Surge panel description should match the required mouseover text"
    assert "arcane_shot_proc" not in EFFECT_TEMPLATES, "Old Arcane Shot proc template should be removed"
    assert "Charged Quiver" not in effects._EFFECT_PANEL_DESCRIPTION_BY_NAME, "Old Charged Quiver panel description should be removed"

    original_roll = resolver.roll
    original_hit = resolver.hit_chance
    try:
        resolver.hit_chance = lambda acc, eva: 100
        resolver.roll = lambda die, rng: {"d4": 3, "d6": 4, "d8": 5}.get(die, original_roll(die, rng))

        wildfire_match = make_match("hunter", "warrior", seed=9101)
        hunter, warrior = _player_states(wildfire_match)
        hunter.stats["crit"] = 0
        warrior.stats["def"] = 0
        warrior.stats["magic_resist"] = 0
        submit_turn(wildfire_match, "wildfire_bomb", _DEF_PASS)
        wildfire_turn = _turn_lines(wildfire_match, 1)
        assert any("cast Wildfire Bomb" in line and "Roll d8 = 5." in line and "Deals 13 damage." in line for line in wildfire_turn), "Wildfire Bomb direct damage should resolve as Attack 0.7x + d8"
        burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
        assert burn is not None and int(burn.get("tick_damage", 0) or 0) == 9, "Wildfire Burn tick should still use Attack 0.5x + d4"
        assert wildfire_match.state[wildfire_match.players[0]].cooldowns.get("wildfire_bomb") == [7], "Wildfire Bomb should enter an 8-turn cooldown, ticked to 7 after turn end"
        assert _has_effect(hunter, "arcane_surge"), "Wildfire Bomb should grant Arcane Surge"
        assert not _has_effect(hunter, "arcane_shot_proc"), "Wildfire Bomb should not grant the old Arcane Shot proc"

        normal_match = make_match("hunter", "warrior", seed=9102)
        normal_hunter, normal_warrior = _player_states(normal_match)
        normal_hunter.stats["crit"] = 0
        normal_warrior.stats["def"] = 0
        normal_warrior.stats["magic_resist"] = 0
        submit_turn(normal_match, "arcane_shot", _DEF_PASS)
        normal_turn = _turn_lines(normal_match, 1)
        assert any("cast Arcane Shot" in line and "Roll d6 = 4." in line and "Deals 10 damage." in line for line in normal_turn), "Arcane Shot should be castable without Charged Quiver/old proc and use Attack 0.5x + d6"
        assert normal_hunter.cooldowns.get("arcane_shot") == [2], "Arcane Shot should enter a 3-turn cooldown, ticked to 2 after turn end"

        empowered_match = make_match("hunter", "warrior", seed=9103)
        empowered_hunter, empowered_warrior = _player_states(empowered_match)
        empowered_hunter.stats["crit"] = 0
        empowered_warrior.stats["def"] = 0
        empowered_warrior.stats["magic_resist"] = 0
        effects.apply_effect_by_id(empowered_hunter, "arcane_surge", overrides={"duration": 3})
        submit_turn(empowered_match, "arcane_shot", _DEF_PASS)
        empowered_turn = _turn_lines(empowered_match, 1)
        assert any("cast Arcane Shot" in line and "Roll d8 = 5." in line and "Empowered by Arcane Surge!" in line and "Deals 17 damage." in line for line in empowered_turn), "Arcane Surge should empower Arcane Shot to Attack 1.0x + d8"
        assert not _has_effect(empowered_hunter, "arcane_surge"), "Arcane Surge should be consumed on Arcane Shot use"

        kill_match = make_match("hunter", "warrior", seed=9104)
        kill_hunter = kill_match.state[kill_match.players[0]]
        mp_before = kill_hunter.res.mp
        submit_turn(kill_match, "kill_command", _DEF_PASS)
        kill_fail_turn = _turn_lines(kill_match, 1)
        expected_kill_fail_log = f"{kill_match.players[0][:5]} tried to cast Kill Command but had no active pet."
        assert expected_kill_fail_log in kill_fail_turn, "Kill Command should log the hunter token when cast without an active pet"
        assert kill_hunter.res.mp == mp_before, "Kill Command should fail cleanly without an active pet before spending mana"
        assert not kill_hunter.cooldowns.get("kill_command"), "Kill Command should not go on cooldown when there is no active pet"

        pet_match = make_match("hunter", "warrior", seed=9105)
        pet_hunter, pet_warrior = _player_states(pet_match)
        pet_warrior.stats["def"] = 0
        submit_turn(pet_match, "call_saber", _DEF_PASS)
        saber = _active_pet(pet_hunter, "frostsaber")
        assert saber is not None, "Frostsaber should be active for Kill Command coverage"
        saber.hp = 10
        saber.energy = 30
        submit_turn(pet_match, "kill_command", _DEF_PASS)
        kill_turn = _turn_lines(pet_match, 2)
        assert saber.hp == 17, "Kill Command should heal current pet by Hunter Attack 0.4x + d4 before the pet special can act"
        assert any("cast Kill Command" in line and "Roll d4 = 3." in line and "Heals Frostsaber for 7 HP." in line for line in kill_turn), "Kill Command log should show the d4 pet heal"
        assert any("Frostsaber bites the target" in line for line in kill_turn), "Kill Command should force the same pet special behavior style as Raptor Strike"
        assert not any("Frostsaber melees the target" in line for line in kill_turn), "Kill Command forced special should replace the normal pet action instead of granting an extra one"

        resource_match = make_match("hunter", "warrior", seed=9106)
        resource_hunter = resource_match.state[resource_match.players[0]]
        submit_turn(resource_match, "call_boar", _DEF_PASS)
        boar = _active_pet(resource_hunter, "barrens_boar")
        assert boar is not None, "Barrens Boar should be active for resource gating coverage"
        boar.rage = 0
        submit_turn(resource_match, "kill_command", "mortal_strike")
        resource_turn = _turn_lines(resource_match, 2)
        assert not any("Barrens Boar braces to intercept attacks." in line for line in resource_turn), "Kill Command forced special should still require the pet resource"
    finally:
        resolver.roll = original_roll
        resolver.hit_chance = original_hit

    duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
    assert "<h4>Kill Command</h4>" in duel_html_text and "[Attack (0.4x) + d4] HP" in duel_html_text, "Hunter docs should include Kill Command with the live heal formula"
    assert "Cooldown: 8" in duel_html_text and "[Attack (0.7x) + d8] Fire damage" in duel_html_text, "Hunter docs should list Wildfire Bomb's new cooldown and formula"
    assert "Castable whenever off cooldown" in duel_html_text and "[Attack (1.0x) + d8] Arcane damage" in duel_html_text, "Hunter docs should document Arcane Shot normal/empowered behavior"
    assert "Requires the proc from casting Wildfire Bomb" not in duel_html_text and "Charged Quiver" not in duel_html_text, "Hunter docs should remove old Charged Quiver/Arcane Shot proc wording"
    assert "[Attack (0.4x) + d6] physical damage" in duel_html_text and "[Attack (1.1x) + d6] physical damage" in duel_html_text, "Hunter docs should list Aimed Shot and Raptor Strike nerfed formulas"
    return True


def scenario_shadow_word_death_double_damage_reminder_wording() -> bool:
    match = make_match("priest", "warrior", seed=123)
    priest_sid, warrior_sid = match.players
    warrior = match.state[warrior_sid]

    warrior.res.hp = max(1, int(warrior.res.hp_max * 0.15))
    submit_turn(match, _DEF_PASS, _DEF_PASS)
    latest_turn = _turn_lines(match, 1)
    assert f"{priest_sid[:5]} Shadow Word: Death Damage will be Doubled!" in latest_turn, "Shadow Word: Death reminder should use updated wording"
    assert not any("Shadow Word: Death Damage Doubled!" in line for line in latest_turn), "Legacy Shadow Word: Death reminder wording should not appear"
    return True


def scenario_die_by_the_sword_log_wording() -> bool:
    match = make_match("warrior", "warrior", p1_items={"weapon": "twin_blades_azzinoth"}, seed=123)
    submit_turn(match, "die_by_sword", _DEF_PASS)
    latest_turn = _turn_lines(match, 1)
    assert any("uses Twin Blades of Azzinoth to cast Die by the Sword. Becomes immune to physical damage, reduces all incoming damage by 30%." in line for line in latest_turn), "Die by the Sword should log as one sentence with updated wording"
    return True


def scenario_druid_form_requirement_log_wording() -> bool:
    match = make_match("druid", "warrior", seed=123)
    submit_turn(match, "maul", _DEF_PASS)
    assert "Druid tried to use Maul but wasn't in Bear Form." in _turn_lines(match, 1), "Bear-form requirement log should include class, ability, and required form"
    submit_turn(match, "rip", _DEF_PASS)
    assert "Druid tried to use Rip but wasn't in Cat Form." in _turn_lines(match, 2), "Cat-form requirement log should include class, ability, and required form"
    submit_turn(match, "wrath", _DEF_PASS)
    assert "Druid tried to use Wrath but wasn't in Moonkin Form." in _turn_lines(match, 3), "Moonkin-form requirement log should include class, ability, and required form"
    return True


def scenario_shield_of_vengeance_explosion_flushes_stealth_break_log() -> bool:
    match = make_match("paladin", "rogue", seed=321)
    paladin_sid, rogue_sid = match.players
    paladin = match.state[paladin_sid]

    submit_turn(match, "shield_of_vengeance", "vanish")
    shield = next((effect for effect in paladin.effects if effect.get("id") == "shield_of_vengeance"), None)
    assert shield is not None, "Shield of Vengeance should be active after cast"
    shield["absorbed"] = 9

    submit_turn(match, _DEF_PASS, _DEF_PASS)
    submit_turn(match, _DEF_PASS, _DEF_PASS)

    latest_turn = _turn_lines(match, 3)
    assert any("Shield of Vengeance explodes!" in line for line in latest_turn), "Explosion should occur on expiry turn"
    assert any(line == f"{rogue_sid[:5]} stealth broken by Shield of Vengeance." for line in latest_turn), "SoV explosion should flush deferred stealth-break logs"
    assert not _has_effect(match.state[rogue_sid], "stealth"), "SoV explosion damage should still break stealth"
    return True


def scenario_mind_blast_empowered_log_wording() -> bool:
    match = make_match("priest", "warrior", seed=6104)
    priest = match.state[match.players[0]]
    effects.apply_effect_by_id(priest, "mind_blast_empowered", overrides={"duration": 2})
    submit_turn(match, "mind_blast", _DEF_PASS)
    latest_turn = _turn_lines(match, 1)
    assert any("Roll d8 =" in line and "Empowered by Mind Flay!" in line for line in latest_turn), "Empowered Mind Blast log should use the new wording"
    assert not any("Empowered Roll d8" in line for line in latest_turn), "Legacy empowered roll wording should not appear"
    return True


def scenario_priest_clarity_of_mind_buff_and_empowerment() -> bool:
    original_roll = resolver.roll
    original_hit = resolver.hit_chance
    try:
        resolver.hit_chance = lambda acc, eva: 100
        resolver.roll = lambda die, rng: {"d4": 2, "d6": 3, "d8": 4}.get(die, original_roll(die, rng))

        template = effects.effect_template("clarity_of_mind")
        assert template.get("name") == "Clarity of Mind", "Clarity of Mind should use the visible effect name"
        assert template.get("school") == "magical", "Clarity of Mind should be magical"
        assert template.get("dispellable") is True, "Clarity of Mind should be dispellable"
        assert int(template.get("duration", 0) or 0) == 4, "Clarity of Mind should last 4 turns"
        assert template.get("stackable") is True, "Clarity of Mind should be stackable"
        assert int(template.get("max_stacks", 0) or 0) == 2, "Clarity of Mind should cap at 2 stacks"
        assert effects._EFFECT_PANEL_DESCRIPTION_BY_NAME.get("Clarity of Mind") == "Next Flash Heal or Penance increased by 40%.", "Clarity of Mind panel mouseover should match"

        flash_match = make_match("priest", "warrior", seed=8801)
        priest, warrior = _player_states(flash_match)
        priest.stats["int"] = 10
        priest.res.hp = priest.res.hp_max - 50
        submit_turn(flash_match, "flash_heal", _DEF_PASS)
        flash_turn = _turn_lines(flash_match, 1)
        assert any("Flash Heal restores 19 HP." in line for line in flash_turn), "Flash Heal should use [Intellect (1.5x) + d8]"

        shield_match = make_match("priest", "warrior", seed=8802)
        priest, _ = _player_states(shield_match)
        priest.stats["int"] = 10
        submit_turn(shield_match, "shield", _DEF_PASS)
        clarity = effects.get_effect(priest, "clarity_of_mind")
        assert clarity is not None, "Power Word: Shield should grant Clarity of Mind"
        assert effects.effect_stack_count(clarity) == 2, "Power Word: Shield should grant 2 Clarity of Mind stacks"
        assert int(clarity.get("duration", 0) or 0) == 3, "Clarity of Mind should have 4-turn duration before end-of-turn ticking"
        panel = effects.build_effect_panel_payload(priest)
        clarity_entry = next((entry for entry in panel.get("buffs_magical", []) if entry.get("name") == "Clarity of Mind"), None)
        assert clarity_entry is not None, "Active effects should show Clarity of Mind"
        assert clarity_entry.get("stackable") is True and clarity_entry.get("stacks") == 2, "Active effects should show Clarity of Mind stack count"
        assert clarity_entry.get("description") == "Next Flash Heal or Penance increased by 40%.", "Clarity of Mind mouseover description should match"

        clarity["duration"] = 1
        priest.cooldowns.pop("shield", None)
        submit_turn(shield_match, "shield", _DEF_PASS)
        refreshed = effects.get_effect(priest, "clarity_of_mind")
        assert refreshed is not None and effects.effect_stack_count(refreshed) == 2, "Recasting Power Word: Shield should not exceed 2 Clarity of Mind stacks"
        assert int(refreshed.get("duration", 0) or 0) == 3, "Recasting Power Word: Shield should refresh Clarity of Mind duration"

        empowered_flash = make_match("priest", "warrior", seed=8803)
        priest, _ = _player_states(empowered_flash)
        priest.stats["int"] = 10
        priest.res.hp = priest.res.hp_max - 100
        effects.apply_effect_by_id(priest, "clarity_of_mind", overrides={"duration": 4, "stacks": 2})
        submit_turn(empowered_flash, "flash_heal", _DEF_PASS)
        clarity_after_flash = effects.get_effect(priest, "clarity_of_mind")
        assert clarity_after_flash is not None and effects.effect_stack_count(clarity_after_flash) == 1, "Flash Heal should consume 1 Clarity of Mind stack on cast"
        assert any("Flash Heal restores 26 HP." in line for line in _turn_lines(empowered_flash, 1)), "Flash Heal should gain +40% final healing with Clarity of Mind"

        penance_match = make_match("priest", "warrior", seed=8804)
        priest, warrior = _player_states(penance_match)
        priest.stats.update({"int": 10, "acc": 999, "crit": 0})
        warrior.stats.update({"eva": 0, "def": 0, "magic_resist": 0})
        effects.apply_effect_by_id(priest, "clarity_of_mind", overrides={"duration": 4, "stacks": 2})
        hp_before = warrior.res.hp
        submit_turn(penance_match, "penance", _DEF_PASS)
        clarity_after_penance = effects.get_effect(priest, "clarity_of_mind")
        assert clarity_after_penance is not None and effects.effect_stack_count(clarity_after_penance) == 1, "Penance should consume 1 Clarity of Mind stack total per cast"
        assert hp_before - warrior.res.hp == 24, "Penance should gain +40% final damage across the cast"

        penance_self_match = make_match("priest", "warrior", seed=8805)
        priest, _ = _player_states(penance_self_match)
        priest.stats["int"] = 10
        priest.res.hp = priest.res.hp_max - 100
        effects.apply_effect_by_id(priest, "clarity_of_mind", overrides={"duration": 4, "stacks": 2})
        hp_before = priest.res.hp
        submit_turn(penance_self_match, "penance_self", _DEF_PASS)
        clarity_after_self = effects.get_effect(priest, "clarity_of_mind")
        assert clarity_after_self is not None and effects.effect_stack_count(clarity_after_self) == 1, "Penance (Self) should consume 1 Clarity of Mind stack total per cast"
        assert priest.res.hp - hp_before == 24, "Penance (Self) should gain +40% final healing across the cast"

        miss_match = make_match("priest", "mage", seed=8806)
        priest, mage = _player_states(miss_match)
        effects.apply_effect_by_id(priest, "clarity_of_mind", overrides={"duration": 4, "stacks": 1})
        effects.apply_effect_by_id(mage, "blink", overrides={"duration": 2})
        submit_turn(miss_match, "penance", _DEF_PASS)
        assert not effects.has_effect(priest, "clarity_of_mind"), "Clarity of Mind should be consumed when Penance is cast even if it misses"

        immune_match = make_match("priest", "mage", seed=8807)
        priest, mage = _player_states(immune_match)
        effects.apply_effect_by_id(priest, "clarity_of_mind", overrides={"duration": 4, "stacks": 1})
        effects.apply_effect_by_id(mage, "iceblock", overrides={"duration": 2})
        submit_turn(immune_match, "penance", _DEF_PASS)
        assert not effects.has_effect(priest, "clarity_of_mind"), "Clarity of Mind should be consumed when Penance is cast even if the target is immune"

        duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
        assert "Gain an absorb shield for [Intellect (1.0x) + d6] and grant 2 stacks of Clarity of Mind for 4 turns" in duel_html_text, "Power Word: Shield docs should mention Clarity of Mind"
        assert "Heals self for [Intellect (1.5x) + d8]. If Clarity of Mind is active" in duel_html_text, "Flash Heal docs should match live behavior"
        assert "If Clarity of Mind is active, consumes 1 stack total per cast to increase final damage/healing by 40%." in duel_html_text, "Penance docs should match live behavior"
    finally:
        resolver.roll = original_roll
        resolver.hit_chance = original_hit
    return True


def scenario_shaman_shocks_apply_phase1_riders_and_lava_surge() -> bool:
    earth_match = make_match("shaman", "warrior", seed=7004)
    shaman_sid, enemy_sid = earth_match.players
    shaman = earth_match.state[shaman_sid]
    enemy = earth_match.state[enemy_sid]
    shaman.stats["acc"] = 999
    shaman.stats["int"] = 25
    enemy.stats["eva"] = 0
    enemy.stats["mres"] = 0
    enemy.stats["damage_reduction"] = 0
    submit_turn(earth_match, "earth_shock", _DEF_PASS)
    earth_debuff = next((fx for fx in enemy.effects if fx.get("id") == "earth_shock"), None)
    assert earth_debuff is not None, "Earth Shock should apply on hit"
    assert int(earth_debuff.get("duration", 0) or 0) == 1, "Earth Shock should leave exactly one miss window after the application turn resolves"
    earth_turn = _turn_lines(earth_match, 1)
    assert any("has Lava Surge!" in line for line in earth_turn), "Lava Surge proc reminder should reference Lava Lash"
    assert not any("has Lava Surge empowered!" in line for line in earth_turn), "Legacy Lava Surge empowered reminder text should not appear"
    assert effects.has_effect(shaman, "lava_surge"), "Earth Shock hit should be able to grant Lava Surge at the 30% proc path"
    enemy_panel_after_earth = effects.build_effect_panel_payload(enemy)
    earth_shock_entry = next((entry for entry in enemy_panel_after_earth.get("debuffs_magical", []) if entry.get("name") == "Earth Shock"), None)
    assert earth_shock_entry and earth_shock_entry.get("description") == "Outgoing attacks will miss.", "Earth Shock effect panel entry/description should match"
    shaman_hp_before = shaman.res.hp
    submit_turn(earth_match, _DEF_PASS, "basic_attack")
    assert shaman.res.hp == shaman_hp_before, "Earth Shock should cause the target's next eligible attack to miss"
    assert not effects.has_effect(enemy, "earth_shock"), "Earth Shock should expire after consuming the next attack miss window"

    flame_match = make_match("shaman", "warrior", seed=7004)
    shaman_sid, enemy_sid = flame_match.players
    flame_shaman = flame_match.state[shaman_sid]
    flame_enemy = flame_match.state[enemy_sid]
    flame_shaman.stats["acc"] = 999
    flame_shaman.stats["int"] = 25
    flame_enemy.stats["eva"] = 0
    flame_enemy.stats["mres"] = 0
    flame_enemy.stats["damage_reduction"] = 0
    submit_turn(flame_match, "flame_shock", _DEF_PASS)
    flame_dance = next((fx for fx in flame_shaman.effects if fx.get("id") == "flame_dance"), None)
    assert flame_dance is not None, "Flame Shock should grant Flame Dance on hit"
    assert int(flame_dance.get("duration", 0) or 0) == 4, "Flame Dance should leave 4 turns remaining after its application turn resolves"
    assert effects.has_effect(flame_shaman, "lava_surge"), "Flame Shock hit should be able to grant Lava Surge at the 30% proc path"

    frost_match = make_match("shaman", "warrior", seed=7004)
    shaman_sid, enemy_sid = frost_match.players
    frost_shaman = frost_match.state[shaman_sid]
    frost_enemy = frost_match.state[enemy_sid]
    frost_shaman.stats["acc"] = 999
    frost_shaman.stats["int"] = 25
    frost_enemy.stats["eva"] = 0
    frost_enemy.stats["mres"] = 0
    frost_enemy.stats["damage_reduction"] = 0
    submit_turn(frost_match, "frost_shock", _DEF_PASS)
    freeze = next((fx for fx in frost_enemy.effects if fx.get("id") == "frost_shock_freeze"), None)
    assert freeze is not None, "Frost Shock should apply a 2-turn freeze on hit"
    assert int(freeze.get("duration", 0) or 0) == 1, "Frost Shock should leave exactly one locked turn after the application turn resolves"
    assert effects.has_effect(frost_shaman, "lava_surge"), "Frost Shock hit should be able to grant Lava Surge at the 30% proc path"
    submit_turn(frost_match, _DEF_PASS, "basic_attack")
    assert not effects.has_effect(frost_enemy, "frost_shock_freeze"), "Frost Shock freeze should break on damage"
    return True


def scenario_shaman_same_turn_on_hit_rider_commitment_fairness() -> bool:
    # 1) Earth Shock mirror: both committed equal-tier actions should still execute.
    earth_mirror = make_match("shaman", "shaman", seed=8121)
    p1_sid, p2_sid = earth_mirror.players
    p1 = earth_mirror.state[p1_sid]
    p2 = earth_mirror.state[p2_sid]
    for ps in (p1, p2):
        ps.stats["acc"] = 999
        ps.stats["eva"] = 0
        ps.stats["crit"] = 0
        ps.stats["int"] = 20
    p1_hp_before = p1.res.hp
    p2_hp_before = p2.res.hp
    submit_turn(earth_mirror, "earth_shock", "earth_shock")
    assert p1.res.hp < p1_hp_before and p2.res.hp < p2_hp_before, "Earth Shock mirror should land both hits on the cast turn"
    earth_turn_lines = _turn_lines(earth_mirror, 1)
    assert sum(1 for line in earth_turn_lines if "shakes the target's aim." in line) == 2, "Earth Shock mirror should apply both outgoing-miss rider logs on the cast turn"

    # 2) Frost Shock mirror: both committed equal-tier actions should still execute and both freezes should apply.
    frost_mirror = make_match("shaman", "shaman", seed=8122)
    f1_sid, f2_sid = frost_mirror.players
    f1 = frost_mirror.state[f1_sid]
    f2 = frost_mirror.state[f2_sid]
    for ps in (f1, f2):
        ps.stats["acc"] = 999
        ps.stats["eva"] = 0
        ps.stats["crit"] = 0
        ps.stats["int"] = 20
    f1_hp_before = f1.res.hp
    f2_hp_before = f2.res.hp
    submit_turn(frost_mirror, "frost_shock", "frost_shock")
    assert f1.res.hp < f1_hp_before and f2.res.hp < f2_hp_before, "Frost Shock mirror should land both hits on the cast turn"
    assert effects.has_effect(f1, "frost_shock_freeze"), "Frost Shock mirror should apply freeze to p1"
    assert effects.has_effect(f2, "frost_shock_freeze"), "Frost Shock mirror should apply freeze to p2"

    # 3) Blink-like protection still wins pre-hit.
    blink_match = make_match("mage", "shaman", seed=8123)
    mage_sid, shaman_sid = blink_match.players
    shaman = blink_match.state[shaman_sid]
    mage = blink_match.state[mage_sid]
    shaman.stats["acc"] = 999
    shaman.stats["eva"] = 0
    mage_hp_before = mage.res.hp
    submit_turn(blink_match, "blink", "earth_shock")
    assert mage.res.hp == mage_hp_before, "Blink should still cause Earth Shock to miss"

    # 4) Turtle forced miss still wins pre-hit.
    turtle_match = make_match("hunter", "shaman", seed=8124)
    hunter_sid, tshaman_sid = turtle_match.players
    tshaman = turtle_match.state[tshaman_sid]
    hunter = turtle_match.state[hunter_sid]
    tshaman.stats["acc"] = 999
    tshaman.stats["eva"] = 0
    hunter_hp_before = hunter.res.hp
    submit_turn(turtle_match, "turtle", "frost_shock")
    assert hunter.res.hp == hunter_hp_before, "Aspect of the Turtle should still cause Frost Shock to miss"
    assert not effects.has_effect(hunter, "frost_shock_freeze"), "Frost Shock freeze should not apply through Turtle miss protection"

    # 5) Die by the Sword remains mitigation-only versus Frost Shock.
    dbts_match = make_match("shaman", "warrior", seed=8125)
    d_shaman_sid, warrior_sid = dbts_match.players
    d_shaman = dbts_match.state[d_shaman_sid]
    warrior = dbts_match.state[warrior_sid]
    d_shaman.stats["acc"] = 999
    d_shaman.stats["eva"] = 0
    submit_turn(dbts_match, "frost_shock", _DEF_PASS)
    baseline_damage = warrior.res.hp_max - warrior.res.hp
    dbts_match = make_match("shaman", "warrior", seed=8125)
    d_shaman_sid, warrior_sid = dbts_match.players
    d_shaman = dbts_match.state[d_shaman_sid]
    warrior = dbts_match.state[warrior_sid]
    d_shaman.stats["acc"] = 999
    d_shaman.stats["eva"] = 0
    submit_turn(dbts_match, "frost_shock", "die_by_sword")
    reduced_damage = warrior.res.hp_max - warrior.res.hp
    assert reduced_damage < baseline_damage, "Die by the Sword should still reduce Frost Shock damage"
    assert effects.has_effect(warrior, "frost_shock_freeze"), "Die by the Sword should not block Frost Shock freeze rider"

    # 6) Pain Suppression remains mitigation-only versus Frost Shock.
    ps_match = make_match("shaman", "priest", seed=8126)
    p_shaman_sid, priest_sid = ps_match.players
    p_shaman = ps_match.state[p_shaman_sid]
    priest = ps_match.state[priest_sid]
    p_shaman.stats["acc"] = 999
    p_shaman.stats["eva"] = 0
    submit_turn(ps_match, "frost_shock", _DEF_PASS)
    ps_baseline_damage = priest.res.hp_max - priest.res.hp
    ps_match = make_match("shaman", "priest", seed=8126)
    p_shaman_sid, priest_sid = ps_match.players
    p_shaman = ps_match.state[p_shaman_sid]
    priest = ps_match.state[priest_sid]
    p_shaman.stats["acc"] = 999
    p_shaman.stats["eva"] = 0
    submit_turn(ps_match, "frost_shock", "pain_supp")
    ps_reduced_damage = priest.res.hp_max - priest.res.hp
    assert ps_reduced_damage < ps_baseline_damage, "Pain Suppression should still reduce Frost Shock damage"
    assert effects.has_effect(priest, "frost_shock_freeze"), "Pain Suppression should not block Frost Shock freeze rider"

    # 7) Non-mirror baseline: Shock rider should still affect future turns normally.
    followup_match = make_match("shaman", "warrior", seed=8127)
    fsid, wsid = followup_match.players
    fshaman = followup_match.state[fsid]
    warrior = followup_match.state[wsid]
    fshaman.stats["acc"] = 999
    fshaman.stats["eva"] = 0
    submit_turn(followup_match, "frost_shock", _DEF_PASS)
    assert effects.has_effect(warrior, "frost_shock_freeze"), "Frost Shock freeze should still apply in non-mirror baseline flow"
    submit_turn(followup_match, _DEF_PASS, "basic_attack")
    turn_two_lines = _turn_lines(followup_match, 2)
    assert any("is frozen and cannot act." in line for line in turn_two_lines), "Frost Shock freeze should still deny action on the following turn in non-mirror flow"
    return True


def scenario_shaman_shock_and_lava_lash_balance_metadata() -> bool:
    assert ABILITIES["earth_shock"]["cooldown"] == 5, "Earth Shock cooldown should be 5"
    assert ABILITIES["earth_shock"]["scaling"]["int"] == 0.4, "Earth Shock should scale with Intellect at 0.4x"
    assert ABILITIES["earth_shock"]["dice"]["type"] == "d4", "Earth Shock should use d4"
    assert ABILITIES["flame_shock"]["cooldown"] == 6, "Flame Shock cooldown should be 6"
    assert ABILITIES["flame_shock"]["scaling"]["int"] == 0.8, "Flame Shock should scale with Intellect at 0.8x"
    assert ABILITIES["flame_shock"]["dice"]["type"] == "d4", "Flame Shock should use d4"
    assert ABILITIES["frost_shock"]["cooldown"] == 7, "Frost Shock cooldown should be 7"
    assert ABILITIES["frost_shock"]["scaling"]["int"] == 0.4, "Frost Shock should scale with Intellect at 0.4x"
    assert ABILITIES["frost_shock"]["dice"]["type"] == "d4", "Frost Shock should use d4"
    assert not ABILITIES["earth_shock"].get("requires_effect"), "Earth Shock should be independently castable"
    assert not ABILITIES["flame_shock"].get("requires_effect"), "Flame Shock should be independently castable"
    assert not ABILITIES["frost_shock"].get("requires_effect"), "Frost Shock should be independently castable"
    assert ABILITIES["lava_lash"]["scaling"]["int"] == 0.2, "Lava Lash base should scale with Intellect at 0.2x"
    assert ABILITIES["lava_lash"]["dice"]["type"] == "d4", "Lava Lash base should use d4"
    assert not ABILITIES["lava_lash"].get("requires_effect"), "Lava Lash should remain castable without Lava Surge"
    assert ABILITIES["chain_lightning"]["target_mode"] == "aoe_enemy", "Chain Lightning should use enemy AoE targeting"
    assert ABILITIES["chain_lightning"]["scaling"]["int"] == 0.6, "Chain Lightning should scale with Intellect at 0.6x"
    assert ABILITIES["chain_lightning"]["dice"]["type"] == "d6", "Chain Lightning should use d6"
    assert ABILITIES["lightning_bolt"]["scaling"]["int"] == 0.3, "Lightning Bolt should scale with Intellect at 0.3x"
    assert ABILITIES["lightning_bolt"]["dice"]["type"] == "d4", "Lightning Bolt should use d4"
    assert ABILITIES["lightning_bolt"]["cost"]["mp"] == 5, "Lightning Bolt should cost 5 mana"
    assert ABILITIES["lightning_bolt"]["cooldown"] == 0, "Lightning Bolt should have no cooldown"
    assert ABILITIES["corruption"]["scaling"]["int"] == 0.2, "Corruption should scale with Intellect at 0.2x"
    assert CLASSES["warlock"]["resources"]["hp"] == 100, "Warlock HP should be 100"
    return True


def scenario_shaman_shock_lava_surge_proc_chances() -> bool:
    for ability_id, chance in (("earth_shock", 0.3), ("flame_shock", 0.3), ("frost_shock", 0.3)):
        effects_list = ABILITIES[ability_id].get("on_hit_effects", [])
        lava_surge = next((entry for entry in effects_list if entry.get("id") == "lava_surge"), None)
        assert lava_surge is not None, f"{ability_id} should be able to grant Lava Surge"
        assert float(lava_surge.get("chance", 0)) == chance, f"{ability_id} should use {int(chance * 100)}% Lava Surge proc chance"
        assert lava_surge.get("log") == "{actor} has Lava Surge!", f"{ability_id} Lava Surge proc log should reference Lava Lash"
    return True


def scenario_shaman_shock_lava_surge_does_not_proc_on_no_hit() -> bool:
    miss_match = make_match("shaman", "warrior", seed=7020)
    shaman_sid, enemy_sid = miss_match.players
    miss_shaman = miss_match.state[shaman_sid]
    miss_enemy = miss_match.state[enemy_sid]
    miss_shaman.stats["acc"] = 0
    miss_enemy.stats["eva"] = 100
    submit_turn(miss_match, "earth_shock", _DEF_PASS)
    assert not effects.has_effect(miss_shaman, "lava_surge"), "Missed Shock should not grant Lava Surge"

    immune_match = make_match("shaman", "mage", seed=7021)
    shaman_sid, mage_sid = immune_match.players
    immune_shaman = immune_match.state[shaman_sid]
    immune_mage = immune_match.state[mage_sid]
    immune_shaman.stats["acc"] = 999
    immune_mage.stats["eva"] = 0
    submit_turn(immune_match, _DEF_PASS, "iceblock")
    submit_turn(immune_match, "flame_shock", _DEF_PASS)
    assert not effects.has_effect(immune_shaman, "lava_surge"), "Immune/no-hit Shock should not grant Lava Surge"
    return True


def scenario_shaman_repeated_shock_lava_surge_stacks_and_logs() -> bool:
    shock_ids = ("earth_shock", "flame_shock", "frost_shock")
    original_on_hit = {shock_id: list(ABILITIES[shock_id].get("on_hit_effects", [])) for shock_id in shock_ids}
    try:
        for shock_id in shock_ids:
            ABILITIES[shock_id]["on_hit_effects"] = [
                {
                    "id": "lava_surge",
                    "chance": 1.0,
                    "log": "{actor} has Lava Surge!",
                    "separate_log": True,
                }
            ]

        match = make_match("shaman", "warrior", seed=7102)
        shaman_sid, enemy_sid = match.players
        shaman = match.state[shaman_sid]
        enemy = match.state[enemy_sid]
        shaman.stats["acc"] = 999
        shaman.stats["crit"] = 0
        enemy.stats["eva"] = 0
        enemy.stats["mres"] = 0
        enemy.stats["damage_reduction"] = 0

        ability_sequence = ("earth_shock", "flame_shock", "frost_shock", "earth_shock")
        for turn_no, ability_id in enumerate(ability_sequence, start=1):
            submit_turn(match, ability_id, _DEF_PASS)
            turn_lines = _turn_lines(match, turn_no)
            surge = effects.get_effect(shaman, "lava_surge")
            assert surge is not None, "Repeated successful Shock hits should keep Lava Surge active"
            expected_stacks = min(3, turn_no)
            assert effects.effect_stack_count(surge) == expected_stacks, "Repeated successful Shock hits should add Lava Surge stacks up to max"
            row_count = sum(1 for fx in shaman.effects if fx.get("id") == "lava_surge")
            assert row_count == 1, "Stackable Lava Surge should stay as one effect row instead of duplicates"
            if turn_no <= 3:
                assert any("has Lava Surge!" in line for line in turn_lines), "A successful Lava Surge stack gain should log the proc message again"

        capped_turn_lines = _turn_lines(match, 4)
        assert not any("has Lava Surge!" in line for line in capped_turn_lines), "Lava Surge proc log should not repeat when already at stack cap with no new stack gained"

        panel = effects.build_effect_panel_payload(shaman)
        lava_entry = next((entry for entry in panel.get("buffs_magical", []) if entry.get("name") == "Lava Surge"), None)
        assert lava_entry is not None, "Visible payload should still include Lava Surge after repeated procs"
        assert lava_entry.get("stacks") == 3, "Visible payload should report the capped Lava Surge stack count"
        return True
    finally:
        for shock_id in shock_ids:
            ABILITIES[shock_id]["on_hit_effects"] = original_on_hit[shock_id]


def scenario_shaman_lava_lash_empowered_damage_and_consume() -> bool:
    match = make_match("shaman", "warrior", seed=7007)
    shaman_sid, enemy_sid = match.players
    shaman = match.state[shaman_sid]
    warrior = match.state[enemy_sid]
    shaman.stats["acc"] = 999
    shaman.stats["crit"] = 0
    shaman.stats["int"] = 10
    warrior.stats["eva"] = 0
    warrior.stats["def"] = 0
    warrior.res.hp = warrior.res.hp_max

    submit_turn(match, "lava_lash", _DEF_PASS)
    base_turn = _turn_lines(match, 1)
    assert any("Roll d4 =" in line for line in base_turn), "Base Lava Lash should roll d4"
    assert not any("Empowered by Lava Surge!" in line for line in base_turn), "Base Lava Lash should not log empowerment"

    hp_after_base = warrior.res.hp
    effects.apply_effect_by_id(shaman, "lava_surge", overrides={"duration": 2})
    submit_turn(match, "lava_lash", _DEF_PASS)
    empowered_turn = _turn_lines(match, 2)
    assert any("Roll d6 =" in line for line in empowered_turn), "Empowered Lava Lash should roll d6"
    assert any("Empowered by Lava Surge!" in line for line in empowered_turn), "Empowered Lava Lash should log Lava Surge empowerment"
    roll_line = next((line for line in empowered_turn if "Roll d6 =" in line), "")
    dealt_line = next((line for line in empowered_turn if "Deals " in line and "damage" in line), "")
    roll_match = re.search(r"Roll d6 = (\d+)", roll_line)
    dealt_match = re.search(r"Deals (\d+) damage", dealt_line)
    assert roll_match and dealt_match, "Empowered Lava Lash logs should include both d6 roll and dealt damage"
    roll_value = int(roll_match.group(1))
    dealt_value = int(dealt_match.group(1))
    assert dealt_value == int(shaman.stats["int"] * 1.5 + roll_value), "Empowered Lava Lash damage should be [Intellect * 1.5 + d6] under zero-mitigation setup"
    assert not effects.has_effect(shaman, "lava_surge"), "Empowered Lava Lash should consume the only Lava Surge stack"
    assert warrior.res.hp < hp_after_base, "Empowered Lava Lash should deal damage"

    match_flame_dance = make_match("shaman", "warrior", seed=7009)
    shaman_sid, enemy_sid = match_flame_dance.players
    dance_shaman = match_flame_dance.state[shaman_sid]
    dance_enemy = match_flame_dance.state[enemy_sid]
    dance_shaman.stats["acc"] = 999
    dance_shaman.stats["crit"] = 0
    dance_shaman.stats["int"] = 10
    dance_enemy.stats["eva"] = 0
    dance_enemy.stats["def"] = 0

    submit_turn(match_flame_dance, "flame_shock", _DEF_PASS)
    assert effects.has_effect(dance_shaman, "flame_dance"), "Flame Shock should grant Flame Dance"
    hp_after_flame_shock = dance_enemy.res.hp
    submit_turn(match_flame_dance, "lava_lash", _DEF_PASS)
    flame_dance_turn = _turn_lines(match_flame_dance, 2)
    assert any("Empowered by Flame Dance!" in line for line in flame_dance_turn), "Next qualifying fire spell should be empowered by Flame Dance"
    roll_line = next((line for line in flame_dance_turn if "Roll d4 =" in line), "")
    dealt_line = next((line for line in flame_dance_turn if "Deals " in line and "damage" in line), "")
    roll_match = re.search(r"Roll d4 = (\d+)", roll_line)
    dealt_match = re.search(r"Deals (\d+) damage", dealt_line)
    assert roll_match and dealt_match, "Flame Dance empowered logs should include both d4 roll and dealt damage"
    roll_value = int(roll_match.group(1))
    dealt_value = int(dealt_match.group(1))
    assert dealt_value == int((dance_shaman.stats["int"] * 0.2 + roll_value) * 1.5), "Flame Dance should increase the next Fire spell by 50%"
    assert not effects.has_effect(dance_shaman, "flame_dance"), "Flame Dance should be consumed by the next qualifying fire spell"
    assert dance_enemy.res.hp < hp_after_flame_shock, "Flame Dance empowered fire spell should deal damage"
    return True


def scenario_shaman_lava_surge_stackable_backend_contract() -> bool:
    match = make_match("shaman", "warrior", seed=7101)
    shaman_sid, enemy_sid = match.players
    shaman = match.state[shaman_sid]
    enemy = match.state[enemy_sid]
    shaman.stats["acc"] = 999
    shaman.stats["crit"] = 0
    shaman.stats["int"] = 10
    enemy.stats["eva"] = 0
    enemy.stats["def"] = 0
    enemy.stats["mres"] = 0
    enemy.stats["damage_reduction"] = 0
    enemy.res.hp = enemy.res.hp_max

    for duration in (1, 2, 3):
        effects.apply_effect_by_id(shaman, "lava_surge", overrides={"duration": duration})
    surge_effect = effects.get_effect(shaman, "lava_surge")
    assert surge_effect is not None, "Lava Surge should exist after being applied"
    assert effects.is_effect_stackable(surge_effect), "Lava Surge should be marked stackable in backend metadata"
    assert effects.effect_max_stacks(surge_effect) == 3, "Lava Surge stack cap should be 3"
    assert effects.effect_stack_count(surge_effect) == 3, "Lava Surge should gain multiple stacks"
    assert int(surge_effect.get("duration", 0) or 0) == 3, "Lava Surge re-proc should refresh to the latest duration deterministically"
    assert sum(1 for fx in shaman.effects if fx.get("id") == "lava_surge") == 1, "Re-proc should increment stacks without creating duplicate Lava Surge effects"

    effects.apply_effect_by_id(shaman, "lava_surge", overrides={"duration": 5})
    surge_after_cap = effects.get_effect(shaman, "lava_surge")
    assert surge_after_cap is not None and effects.effect_stack_count(surge_after_cap) == 3, "Lava Surge stacks should cap at 3"
    assert int(surge_after_cap.get("duration", 0) or 0) == 5, "Re-proc at cap should still refresh duration"

    panel = effects.build_effect_panel_payload(shaman)
    lava_entries = [entry for entry in panel.get("buffs_magical", []) if entry.get("name") == "Lava Surge"]
    assert len(lava_entries) == 1, "Visible effect payload should include exactly one Lava Surge row"
    assert lava_entries[0].get("stackable") is True, "Visible effect payload should expose stackable metadata for Lava Surge"
    assert lava_entries[0].get("stacks") == 3, "Visible effect payload should include current Lava Surge stack count"

    effects.apply_effect_by_id(shaman, "hot_streak", overrides={"duration": 3})
    panel_with_non_stackable = effects.build_effect_panel_payload(shaman)
    hot_streak_entry = next((entry for entry in panel_with_non_stackable.get("buffs_magical", []) if entry.get("name") == "Hot Streak"), None)
    assert hot_streak_entry is not None, "Non-stackable visible effects should remain present as before"
    assert "stacks" not in hot_streak_entry and "stackable" not in hot_streak_entry, "Non-stackable visible effects should remain unchanged in payload shape"

    for expected_stacks in (2, 1):
        hp_before = enemy.res.hp
        submit_turn(match, "lava_lash", _DEF_PASS)
        hp_after = enemy.res.hp
        current = effects.get_effect(shaman, "lava_surge")
        assert current is not None and effects.effect_stack_count(current) == expected_stacks, "Empowered Lava Lash should consume exactly one Lava Surge stack"
        assert hp_after < hp_before, "Empowered Lava Lash should deal damage while consuming stacks"

    submit_turn(match, "lava_lash", _DEF_PASS)
    assert not effects.has_effect(shaman, "lava_surge"), "Lava Surge should disappear when stacks reach zero"
    panel_after_deplete = effects.build_effect_panel_payload(shaman)
    assert not any(entry.get("name") == "Lava Surge" for entry in panel_after_deplete.get("buffs_magical", [])), "Lava Surge should be removed from payload when stacks reach zero"
    return True


def scenario_warrior_onslaught_stackable_contract() -> bool:
    match = make_match("warrior", "mage", seed=7301)
    warrior_sid, enemy_sid = match.players
    warrior = match.state[warrior_sid]
    enemy = match.state[enemy_sid]
    warrior.stats["acc"] = 999
    warrior.stats["crit"] = 0
    enemy.stats["eva"] = 0
    enemy.stats["def"] = 0
    enemy.stats["damage_reduction"] = 0

    submit_turn(match, "overpower", _DEF_PASS)
    onslaught = effects.get_effect(warrior, "onslaught")
    assert onslaught is not None and effects.effect_stack_count(onslaught) == 1, "Overpower should grant 1 stack of Onslaught"

    submit_turn(match, "overpower", _DEF_PASS)
    submit_turn(match, "overpower", _DEF_PASS)
    onslaught = effects.get_effect(warrior, "onslaught")
    assert onslaught is not None and effects.effect_stack_count(onslaught) == 3, "Repeated Overpower should stack Onslaught up to 3"
    assert sum(1 for fx in warrior.effects if fx.get("id") == "onslaught") == 1, "Onslaught should remain a single stackable effect row"

    refresh_probe = make_match("warrior", "mage", seed=7310)
    refresh_warrior = refresh_probe.state[refresh_probe.players[0]]
    effects.apply_effect_by_id(refresh_warrior, "onslaught", overrides={"duration": 1})
    effects.apply_effect_by_id(refresh_warrior, "onslaught", overrides={"duration": 3})
    refreshed = effects.get_effect(refresh_warrior, "onslaught")
    assert refreshed is not None and effects.effect_stack_count(refreshed) == 2 and int(refreshed.get("duration", 0) or 0) == 3, "Gaining another Onslaught stack should refresh duration to 3"

    submit_turn(match, "overpower", _DEF_PASS)
    onslaught = effects.get_effect(warrior, "onslaught")
    assert onslaught is not None and effects.effect_stack_count(onslaught) == 3, "Onslaught should not exceed 3 stacks"

    panel = effects.build_effect_panel_payload(warrior)
    onslaught_entries = [entry for entry in panel.get("buffs_physical", []) if entry.get("name") == "Onslaught"]
    assert len(onslaught_entries) == 1, "Visible payload should contain one Onslaught row"
    assert onslaught_entries[0].get("stackable") is True and onslaught_entries[0].get("stacks") == 3, "Onslaught payload row should expose stackable metadata and stack count"
    assert onslaught_entries[0].get("description") == "Next rage-spending damaging ability deals 4% more damage per stack.", "Onslaught payload should expose the expected description"

    ignore_match = make_match("warrior", "mage", seed=7302)
    ignore_warrior_sid, ignore_enemy_sid = ignore_match.players
    ignore_warrior = ignore_match.state[ignore_warrior_sid]
    ignore_enemy = ignore_match.state[ignore_enemy_sid]
    ignore_warrior.stats["acc"] = 999
    ignore_warrior.stats["crit"] = 0
    ignore_enemy.stats["eva"] = 0
    ignore_warrior.res.rage = ignore_warrior.res.rage_max
    effects.apply_effect_by_id(ignore_warrior, "onslaught", overrides={"duration": 3})
    effects.apply_effect_by_id(ignore_warrior, "onslaught", overrides={"duration": 3})
    submit_turn(ignore_match, "ignore_pain", _DEF_PASS)
    onslaught_after_ignore = effects.get_effect(ignore_warrior, "onslaught")
    assert onslaught_after_ignore is not None and effects.effect_stack_count(onslaught_after_ignore) == 2, "Ignore Pain should not consume Onslaught"

    baseline_match = make_match("warrior", "mage", seed=7303)
    baseline_warrior_sid, baseline_enemy_sid = baseline_match.players
    baseline_warrior = baseline_match.state[baseline_warrior_sid]
    baseline_enemy = baseline_match.state[baseline_enemy_sid]
    baseline_warrior.stats["acc"] = 999
    baseline_warrior.stats["crit"] = 0
    baseline_warrior.stats["atk"] = 40
    baseline_enemy.stats["eva"] = 0
    baseline_enemy.stats["def"] = 0
    baseline_enemy.stats["damage_reduction"] = 0
    baseline_warrior.res.rage = baseline_warrior.res.rage_max
    hp_before = baseline_enemy.res.hp
    submit_turn(baseline_match, "mortal_strike", _DEF_PASS)
    base_damage = hp_before - baseline_enemy.res.hp

    buffed_match = make_match("warrior", "mage", seed=7303)
    buffed_warrior_sid, buffed_enemy_sid = buffed_match.players
    buffed_warrior = buffed_match.state[buffed_warrior_sid]
    buffed_enemy = buffed_match.state[buffed_enemy_sid]
    buffed_warrior.stats["acc"] = 999
    buffed_warrior.stats["crit"] = 0
    buffed_warrior.stats["atk"] = 40
    buffed_enemy.stats["eva"] = 0
    buffed_enemy.stats["def"] = 0
    buffed_enemy.stats["damage_reduction"] = 0
    buffed_warrior.res.rage = buffed_warrior.res.rage_max
    for _ in range(3):
        effects.apply_effect_by_id(buffed_warrior, "onslaught", overrides={"duration": 3})
    hp_before = buffed_enemy.res.hp
    submit_turn(buffed_match, "mortal_strike", _DEF_PASS)
    boosted_damage = hp_before - buffed_enemy.res.hp
    assert boosted_damage == int(base_damage * 1.12), "3-stack Onslaught should grant +12% damage to the next qualifying rage spender"
    assert not effects.has_effect(buffed_warrior, "onslaught"), "All Onslaught stacks should be consumed at once by the next qualifying rage spender"

    execute_match = make_match("warrior", "mage", seed=7304)
    execute_warrior_sid, execute_enemy_sid = execute_match.players
    execute_warrior = execute_match.state[execute_warrior_sid]
    execute_enemy = execute_match.state[execute_enemy_sid]
    execute_warrior.stats["acc"] = 999
    execute_warrior.stats["crit"] = 0
    execute_enemy.stats["eva"] = 0
    execute_enemy.stats["def"] = 100
    execute_enemy.stats["damage_reduction"] = 0
    execute_warrior.res.rage = execute_warrior.res.rage_max
    execute_enemy.res.hp = max(1, int(execute_enemy.res.hp_max * 0.15))
    effects.apply_effect_by_id(execute_warrior, "onslaught", overrides={"duration": 3})
    submit_turn(execute_match, "execute", _DEF_PASS)
    assert not effects.has_effect(execute_warrior, "onslaught"), "Execute should qualify and consume all Onslaught stacks"

    duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
    assert "grants <span class=\"stat\">Onslaught</span>" in duel_html_text, "Overpower docs should mention Onslaught grant"
    return True


def scenario_shaman_chain_lightning_aoe_and_docs_and_effect_panel() -> bool:
    match = make_match("shaman", "hunter", seed=7008)
    shaman_sid, hunter_sid = match.players
    shaman = match.state[shaman_sid]
    hunter = match.state[hunter_sid]
    shaman.stats["acc"] = 999
    shaman.stats["crit"] = 0
    hunter.stats["eva"] = 0
    hunter.stats["def"] = 0

    submit_turn(match, _DEF_PASS, "call_saber")
    pet_ids = sorted(hunter.pets.keys())
    assert pet_ids, "Hunter should have a pet target for Chain Lightning AoE validation"
    pet_hp_before = {pid: hunter.pets[pid].hp for pid in pet_ids}
    submit_turn(match, "chain_lightning", _DEF_PASS)
    assert hunter.res.hp < hunter.res.hp_max, "Chain Lightning should hit enemy champion"
    assert any(hunter.pets.get(pid) and hunter.pets[pid].hp < pet_hp_before[pid] for pid in pet_ids), "Chain Lightning should hit enemy pets/totems under AoE model"

    effects.apply_effect_by_id(shaman, "lava_surge", overrides={"duration": 2})
    effects.apply_effect_by_id(shaman, "flame_dance", overrides={"duration": 5})
    effects.apply_effect_by_id(hunter, "frost_shock_freeze", overrides={"duration": 2})
    panel = effects.build_effect_panel_payload(shaman)
    magical_names = {entry.get("name") for entry in panel.get("buffs_magical", [])}
    lava_surge_entry = next((entry for entry in panel.get("buffs_magical", []) if entry.get("name") == "Lava Surge"), None)
    flame_dance_entry = next((entry for entry in panel.get("buffs_magical", []) if entry.get("name") == "Flame Dance"), None)
    assert "Lava Surge" in magical_names, "Lava Surge should appear in magical buffs effect panel"
    assert lava_surge_entry and lava_surge_entry.get("description") == "Lava Lash empowered.", "Lava Surge effect panel description should match"
    assert flame_dance_entry and flame_dance_entry.get("description") == "Next Fire spell’s damage increased by 50%.", "Flame Dance effect panel entry/description should match"

    enemy_panel = effects.build_effect_panel_payload(hunter)
    frost_shock_entry = next((entry for entry in enemy_panel.get("debuffs_magical", []) if entry.get("name") == "Frost Shock"), None)
    assert frost_shock_entry and frost_shock_entry.get("description") == "Frozen and cannot act. Breaks on damage.", "Frost Shock effect panel entry/description should match"

    duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
    shaman_docs_start = duel_html_text.index('<h4 class="doc-subtitle" style="color: var(--shaman-color);">Shaman</h4>')
    first_shaman_lightning_idx = duel_html_text.find('<h4>Lightning Bolt</h4>', shaman_docs_start)
    first_shaman_earth_idx = duel_html_text.find('<h4>Earth Shock</h4>', shaman_docs_start)
    assert first_shaman_lightning_idx != -1 and first_shaman_earth_idx != -1 and first_shaman_lightning_idx < first_shaman_earth_idx, "Lightning Bolt docs box should be the first Shaman ability entry"
    assert '<h4>Lava Lash</h4>' in duel_html_text, "Lava Lash docs entry should exist"
    assert '<h4>Chain Lightning</h4>' in duel_html_text, "Chain Lightning docs entry should exist"
    assert '<h4>Lightning Bolt</h4>' in duel_html_text, "Lightning Bolt docs entry should exist"
    assert '"Lava Lash"' in duel_html_text and '"Chain Lightning"' in duel_html_text and '"Lightning Bolt"' in duel_html_text, "Tooltip/doc-driven ability lists should include Lava Lash, Chain Lightning, and Lightning Bolt"
    assert '"Magical Attack": [' in duel_html_text and '"Lava Lash"' in duel_html_text and '"Chain Lightning"' in duel_html_text and '"Lightning Bolt"' in duel_html_text, "Icon source should classify Lava Lash, Chain Lightning, and Lightning Bolt as magical attacks"
    assert '"Single Target": [' in duel_html_text and '"Lava Lash"' in duel_html_text and '"Lightning Bolt"' in duel_html_text, "Icon source should classify Lava Lash and Lightning Bolt as single target"
    assert '"AoE": [' in duel_html_text and '"Chain Lightning"' in duel_html_text, "Icon source should classify Chain Lightning as AoE"
    assert '<p><span class="stat">Cost: 5 Mana</span> | <span class="stat">Type: Magic (Nature)</span> | <span class="stat">Cooldown: 5</span></p>' in duel_html_text, "Earth Shock docs should list 5 cooldown"
    assert "Deals [Intellect (0.4x) + d4] Nature damage." in duel_html_text, "Earth Shock docs should list 0.4x Intellect scaling with d4"
    assert '<p><span class="stat">Cost: 10 Mana</span> | <span class="stat">Type: Magic (Fire)</span> | <span class="stat">Cooldown: 6</span></p>' in duel_html_text, "Flame Shock docs should list 6 cooldown"
    assert "Deals [Intellect (0.8x) + d4] Fire damage." in duel_html_text, "Flame Shock docs should list 0.8x Intellect scaling with d4"
    assert '<p><span class="stat">Cost: 10 Mana</span> | <span class="stat">Type: Magic (Frost)</span> | <span class="stat">Cooldown: 7</span></p>' in duel_html_text, "Frost Shock docs should list 7 cooldown"
    assert "Deals [Intellect (0.4x) + d4] Frost damage." in duel_html_text, "Frost Shock docs should list 0.4x Intellect scaling with d4"
    assert "Lava Lash is empowered to [Intellect (1.5x) + d6]" in duel_html_text, "Lava Lash docs should list 1.5x empowered scaling with d6"
    assert "On hit, enemy's attacks next turn will miss, and has a 30% chance to grant <span class=\"stat\">Lava Surge</span>" in duel_html_text, "Earth Shock docs should describe next-turn miss behavior"
    assert "On hit, grants <span class=\"stat\">Flame Dance</span> for 5 turns (next Fire spell’s damage increased by 50%)" in duel_html_text, "Flame Shock docs should describe Flame Dance at 50%"
    assert "Deals [Intellect (0.3x) + d4] Nature damage." in duel_html_text, "Lightning Bolt docs should list 0.3x Intellect scaling with d4"
    assert "On hit, freezes the target next turn (breaks on damage)" in duel_html_text, "Frost Shock docs should describe next-turn freeze rider"
    assert "Only usable below 50% HP." in duel_html_text, "Astral Shift docs should include the below-50% HP requirement"
    assert "restore 3% of max HP and permanently gain +3% Intellect" in duel_html_text, "Ancestral Knowledge docs should include 3% max HP healing"
    return True


def scenario_shaman_ancestral_guidance_and_knowledge() -> bool:
    match = make_match("shaman", "warrior", seed=7003)
    shaman_sid, _ = match.players
    shaman = match.state[shaman_sid]
    base_int = int(shaman.stats.get("int", 0))
    shaman.res.hp = 50
    expected_heal = int(shaman.res.hp_max * 0.03)
    hp_before = shaman.res.hp
    submit_turn(match, "ancestral_guidance", _DEF_PASS)
    assert effects.absorb_total(shaman) > 0, "Ancestral Guidance should grant an end-of-turn shield"
    assert effects.has_effect(shaman, "ancestral_guidance_shield"), "Ancestral Guidance Shield should be present"
    assert shaman.res.hp == hp_before + expected_heal, "Ancestral Knowledge should heal for 3% max HP"
    totals = match.combat_totals.get(shaman_sid, {})
    assert int(totals.get("healing", 0) or 0) >= expected_heal, "Ancestral Knowledge healing should contribute to combat healing totals"
    assert shaman.stats["int"] >= base_int + 1, "Ancestral Knowledge should grant at least +1 permanent Int"
    return True


def scenario_shaman_astral_shift_conversion() -> bool:
    match = make_match("shaman", "warrior", seed=7004)
    shaman_sid, _ = match.players
    shaman = match.state[shaman_sid]
    shaman.res.hp = 40
    submit_turn(match, "astral_shift", _DEF_PASS)
    cast_turn = _turn_lines(match, 1)
    assert any("can use Astral Shift!" in line for line in cast_turn), "Shaman should get Astral Shift can-use reminder when below 50% HP"
    assert shaman.res.hp >= 1, "Astral Shift should never reduce the caster below 1 HP"
    assert effects.absorb_total(shaman) >= 39, "Astral Shift should convert HP into absorb"
    assert effects.has_effect(shaman, "astral_shield"), "Astral Shift should apply Astral Shield"

    blocked_match = make_match("shaman", "warrior", seed=7014)
    blocked_sid, _ = blocked_match.players
    blocked_shaman = blocked_match.state[blocked_sid]
    blocked_shaman.res.hp = blocked_shaman.res.hp_max
    submit_turn(blocked_match, "astral_shift", _DEF_PASS)
    blocked_turn = _turn_lines(blocked_match, 1)
    assert any("Astral Shift can only be used below 50% HP." in line for line in blocked_turn), "Astral Shift should be blocked at or above 50% HP with the correct fail message"
    assert not effects.has_effect(blocked_shaman, "astral_shield"), "Astral Shift should not apply shield when blocked by HP requirement"
    return True


def scenario_shaman_lightning_bolt_damage_and_shock_resets() -> bool:
    match = make_match("shaman", "warrior", seed=7016)
    shaman_sid, enemy_sid = match.players
    shaman = match.state[shaman_sid]
    enemy = match.state[enemy_sid]
    shaman.stats["acc"] = 999
    shaman.stats["crit"] = 0
    shaman.stats["int"] = 20
    enemy.stats["eva"] = 0
    enemy.stats["def"] = 0
    enemy.stats["mres"] = 0
    submit_turn(match, "lightning_bolt", _DEF_PASS)
    turn_lines = _turn_lines(match, 1)
    roll_line = next((line for line in turn_lines if "Roll d4 =" in line), "")
    dealt_line = next((line for line in turn_lines if "Deals " in line and "damage" in line), "")
    roll_match = re.search(r"Roll d4 = (\d+)", roll_line)
    dealt_match = re.search(r"Deals (\d+) damage", dealt_line)
    assert roll_match and dealt_match, "Lightning Bolt logs should include both d4 roll and dealt damage"
    assert int(dealt_match.group(1)) == int(shaman.stats["int"] * 0.3 + int(roll_match.group(1))), "Lightning Bolt damage should be [Intellect * 0.3 + d4]"

    def _find_reset_seed(shock_ability: str, log_text: str) -> int:
        for seed in range(7200, 7800):
            reset_match = make_match("shaman", "warrior", seed=seed)
            sid, _ = reset_match.players
            sham = reset_match.state[sid]
            foe = reset_match.state[reset_match.players[1]]
            sham.stats["acc"] = 999
            sham.stats["crit"] = 0
            foe.stats["eva"] = 0
            foe.stats["def"] = 0
            foe.stats["mres"] = 0
            submit_turn(reset_match, shock_ability, _DEF_PASS)
            submit_turn(reset_match, "lightning_bolt", _DEF_PASS)
            if not sham.cooldowns.get(shock_ability) and any(log_text in line for line in _turn_lines(reset_match, 2)):
                return seed
        raise AssertionError(f"Unable to find deterministic seed for {shock_ability} reset path")

    earth_seed = _find_reset_seed("earth_shock", "Earth Shock's cooldown has been reset!")
    flame_seed = _find_reset_seed("flame_shock", "Flame Shock's cooldown has been reset!")
    frost_seed = _find_reset_seed("frost_shock", "Frost Shock's cooldown has been reset!")
    assert earth_seed != flame_seed or flame_seed != frost_seed, "Cooldown reset path seeds should be independently discovered"

    miss_match = make_match("shaman", "warrior", seed=7022)
    miss_sid, _ = miss_match.players
    miss_shaman = miss_match.state[miss_sid]
    miss_shaman.stats["acc"] = 0
    miss_match.state[miss_match.players[1]].stats["eva"] = 100
    submit_turn(miss_match, "earth_shock", _DEF_PASS)
    assert miss_shaman.cooldowns.get("earth_shock"), "Earth Shock should be on cooldown before miss-path validation"
    submit_turn(miss_match, "lightning_bolt", _DEF_PASS)
    assert miss_shaman.cooldowns.get("earth_shock"), "Missed Lightning Bolt should not reset Shock cooldowns"
    assert not any("cooldown has been reset!" in line for line in _turn_lines(miss_match, 2)), "Missed Lightning Bolt should not emit cooldown reset logs"
    return True


def scenario_shaman_and_rogue_docs_and_stats() -> bool:
    assert CLASSES["rogue"]["base_stats"]["eva"] == 9, "Rogue evasion should be 9"
    assert CLASSES["warlock"]["resources"]["hp"] == 100, "Warlock HP should be 100"
    duel_html_text = _detect_duel_html_path().read_text(encoding="utf-8")
    for class_name in ("Warrior", "Mage", "Rogue", "Druid", "Warlock", "Paladin", "Priest", "Hunter", "Shaman"):
        assert f"<h4 style=" in duel_html_text and class_name in duel_html_text, f"{class_name} docs should be present"
    assert "Evasion: 9%" in duel_html_text, "Rogue docs should show Evasion: 9%"
    assert "HP: 100" in duel_html_text, "Warlock docs should show HP: 100"
    assert duel_html_text.count("Evasion:") >= 9, "Class docs should show Evasion for each class"
    assert "Spirit: 0" in duel_html_text and "Spirit: 5" in duel_html_text and "Spirit: 10" in duel_html_text and "Spirit: 20" in duel_html_text, "Class docs should include Spirit base stat values"
    assert duel_html_text.count("Spirit:") >= 9, "Class docs should show Spirit for each class"
    assert CLASSES["mage"]["base_stats"]["spirit"] == 0, "Mage Spirit should be 0"
    assert CLASSES["druid"]["base_stats"]["spirit"] == 0, "Druid Spirit should be 0"
    assert CLASSES["warlock"]["base_stats"]["spirit"] == 0, "Warlock Spirit should be 0"
    assert CLASSES["paladin"]["base_stats"]["spirit"] == 10, "Paladin Spirit should be 10"
    assert CLASSES["priest"]["base_stats"]["spirit"] == 20, "Priest Spirit should be 20"
    assert CLASSES["hunter"]["base_stats"]["spirit"] == 0, "Hunter Spirit should be 0"
    assert CLASSES["shaman"]["base_stats"]["spirit"] == 5, "Shaman Spirit should be 5"
    assert CLASSES["warrior"]["base_stats"]["spirit"] == 0, "Warrior Spirit should be 0"
    assert CLASSES["rogue"]["base_stats"]["spirit"] == 0, "Rogue Spirit should be 0"
    return True


def scenario_paladin_divine_storm_behavior_and_docs() -> bool:
    ability = ABILITIES.get("divine_storm") or {}
    assert ability.get("cost", {}).get("mp") == 18, "Divine Storm should cost 18 mana"
    assert ability.get("cooldown") == 10, "Divine Storm cooldown should be 10"
    assert ability.get("scaling") == {"atk": 0.7}, "Divine Storm should scale from Attack x 0.7"
    assert ability.get("dice", {}).get("type") == "d6", "Divine Storm should add d6 damage"
    assert ability.get("school") == "magical" and ability.get("subschool") == "holy", "Divine Storm should be magical holy damage"
    assert ability.get("target_mode") == "aoe_enemy" and ability.get("max_targets") == 3, "Divine Storm should use capped enemy AoE targeting"

    match = make_match("paladin", "warlock", seed=7301)
    paladin_sid, warlock_sid = match.players
    paladin = match.state[paladin_sid]
    warlock = match.state[warlock_sid]
    paladin.stats.update({"atk": 20, "acc": 999, "crit": 0, "spirit": 0})
    warlock.stats.update({"def": 0, "magic_resist": 0, "eva": 0})
    warlock.pets["p2_imp_1"] = PetState(id="p2_imp_1", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)
    warlock.pets["p2_imp_2"] = PetState(id="p2_imp_2", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)
    warlock.pets["p2_imp_3"] = PetState(id="p2_imp_3", template_id="imp", name="Imp", owner_sid=warlock_sid, hp=40, hp_max=40)

    mp_before = paladin.res.mp
    champion_hp_before = warlock.res.hp
    pet_hp_before = {pet_id: pet.hp for pet_id, pet in warlock.pets.items()}
    submit_turn(match, "divine_storm", _DEF_PASS)

    turn_lines = _turn_lines(match, 1)
    roll_line = next((line for line in turn_lines if "Roll d6 =" in line), "")
    roll_match = re.search(r"Roll d6 = (\d+)", roll_line)
    assert roll_match, "Divine Storm should roll d6"
    expected_raw = int(paladin.stats["atk"] * 0.7) + int(roll_match.group(1))
    champion_damage = champion_hp_before - warlock.res.hp
    assert champion_damage == expected_raw, "Divine Storm should deal [Attack x 0.7 + d6] to the champion before mitigation"
    assert paladin.res.mp == mp_before - 18 + 2, "Divine Storm should spend exactly 18 mana before the baseline end-of-turn mana regen"
    assert paladin.cooldowns.get("divine_storm") == [9], "Divine Storm should enter a 10-turn cooldown, ticked to 9 after turn end"

    damaged_pets = [pet_id for pet_id, before in pet_hp_before.items() if warlock.pets[pet_id].hp < before]
    assert warlock.res.hp < champion_hp_before, "Divine Storm should hit the enemy champion"
    assert damaged_pets == ["p2_imp_1", "p2_imp_2"], "Divine Storm should deterministically hit the first two enemy pets after the champion"
    assert len(damaged_pets) + int(warlock.res.hp < champion_hp_before) == 3, "Divine Storm should not exceed 3 total targets"
    assert warlock.pets["p2_imp_3"].hp == pet_hp_before["p2_imp_3"], "Divine Storm should not hit a fourth enemy target"
    assert sum(1 for line in turn_lines if "Divine Storm hits" in line) == 2, "Divine Storm should only fan out to two pets when the champion is also hit"

    duel_html = _detect_duel_html_path().read_text()
    assert "Divine Storm" in duel_html and "divine_storm" in duel_html, "duel.html should document Divine Storm and its command"
    assert "Cost: 18 Mana" in duel_html and "Cooldown: 10" in duel_html, "duel.html should document Divine Storm cost and cooldown"
    assert "up to 3 enemy targets total" in duel_html and "[(Attack x 0.7) + d6] Holy magic damage" in duel_html, "duel.html should document Divine Storm targeting and damage"
    paladin_list_match = re.search(r"const paladinAbilities = \[([^\]]+)\];", duel_html)
    assert paladin_list_match and "Divine Storm" in paladin_list_match.group(1), "Combat log Paladin ability styling should include Divine Storm"
    assert "paladinAbilities.forEach((ability)" in duel_html and "log-ability-paladin" in duel_html, "Combat log should use existing Paladin ability color wrapping"
    assert re.search(r'"Magical Attack": \[[^\]]*"Divine Storm"', duel_html), "Divine Storm docs should be categorized as a magical attack for tooltip/icon metadata"
    assert re.search(r'"AoE": \[[^\]]*"Divine Storm"', duel_html), "Divine Storm docs should be categorized as AoE for tooltip/icon metadata"
    assert "data-tip-ability" in duel_html, "Combat log ability mouseover stamping should remain enabled"
    return True


def scenario_shield_of_vengeance_explosion_uses_absorbed_amount_for_pets() -> bool:
    match = make_match("paladin", "warlock", seed=7310)
    paladin_sid, warlock_sid = match.players
    paladin = match.state[paladin_sid]
    warlock = match.state[warlock_sid]
    warlock.stats.update({"def": 0, "magic_resist": 0})
    warlock.pets["p2_imp_1"] = PetState(
        id="p2_imp_1",
        template_id="imp",
        name="Imp",
        owner_sid=warlock_sid,
        hp=40,
        hp_max=40,
        stats={"def": 80, "magic_resist": 80},
    )

    effects.apply_effect_by_id(paladin, "shield_of_vengeance", overrides={"duration": 1})
    effects.add_absorb(paladin, 14, source_name="Shield of Vengeance", effect_id="shield_of_vengeance")
    absorbed_chunks = []
    for incoming in (5, 3, 4):
        remaining, absorbed, _ = effects.consume_absorbs(paladin, incoming)
        assert remaining == 0, "Shield of Vengeance should fully absorb the regression setup hits"
        absorbed_chunks.append(absorbed)

    expected_explosion_damage = sum(absorbed_chunks)
    shield = effects.get_effect(paladin, "shield_of_vengeance")
    assert expected_explosion_damage == 12, "Regression setup should absorb exactly 12 before exploding"
    assert shield is not None
    assert int(shield.get("absorbed", 0) or 0) == expected_explosion_damage, (
        "Shield of Vengeance should track the absorbed total used for explosion damage"
    )

    champion_hp_before = warlock.res.hp
    imp_hp_before = warlock.pets["p2_imp_1"].hp

    submit_turn(match, _DEF_PASS, _DEF_PASS)

    champion_damage = champion_hp_before - warlock.res.hp
    imp_damage = imp_hp_before - warlock.pets["p2_imp_1"].hp
    turn_lines = _turn_lines(match, 1)
    champion_line = next(
        (line for line in turn_lines if line == f"Shield of Vengeance hits p2_si for {expected_explosion_damage} damage."),
        "",
    )
    imp_line = next(
        (
            line
            for line in turn_lines
            if line == f"Shield of Vengeance hits p2_si's Imp (imp1) for {expected_explosion_damage} damage."
        ),
        "",
    )

    assert champion_damage == expected_explosion_damage, "Shield of Vengeance champion damage should use the tracked absorbed explosion amount"
    assert imp_damage == expected_explosion_damage, "Shield of Vengeance pet damage should use the same tracked absorbed explosion amount"
    assert champion_damage == imp_damage, "Champion and pet targets should resolve from the same Shield of Vengeance base amount"
    assert champion_line, "Shield of Vengeance champion log should match actual champion damage"
    assert imp_line, "Shield of Vengeance pet log should match actual pet damage"

    def challenger_sov_champion_damage(*, paladin_mp: int) -> int:
        challenger_match = make_match(
            "paladin",
            "warrior",
            p1_items={"armor": "challengers_chestplate"},
            seed=7311,
        )
        challenger_paladin_sid, challenger_enemy_sid = challenger_match.players
        challenger_paladin = challenger_match.state[challenger_paladin_sid]
        challenger_enemy = challenger_match.state[challenger_enemy_sid]
        challenger_enemy.stats.update({"def": 0, "magic_resist": 0})
        challenger_paladin.res.mp = paladin_mp
        effects.apply_effect_by_id(challenger_paladin, "shield_of_vengeance", overrides={"duration": 1})
        effects.add_absorb(
            challenger_paladin,
            expected_explosion_damage,
            source_name="Shield of Vengeance",
            effect_id="shield_of_vengeance",
        )
        remaining, absorbed, _ = effects.consume_absorbs(challenger_paladin, expected_explosion_damage)
        assert remaining == 0 and absorbed == expected_explosion_damage, "Challenger SoV setup should absorb the same amount"
        enemy_hp_before = challenger_enemy.res.hp
        submit_turn(challenger_match, _DEF_PASS, _DEF_PASS)
        return enemy_hp_before - challenger_enemy.res.hp

    sov_might_damage = challenger_sov_champion_damage(paladin_mp=41)
    sov_wrath_damage = challenger_sov_champion_damage(paladin_mp=40)
    assert sov_might_damage == expected_explosion_damage, "Shield of Vengeance should ignore Challenger Might outgoing modifiers"
    assert sov_wrath_damage == expected_explosion_damage, "Shield of Vengeance should ignore Challenger Wrath outgoing modifiers"
    assert not any(effect.get("id") == "shield_of_vengeance" for effect in paladin.effects), "Shield of Vengeance should still be removed after exploding"
    return True


def scenario_paladin_shield_of_vengeance_reset_and_no_unrelated_changes() -> bool:
    expected_existing_paladin = {
        "crusader_strike": ({"mp": 0}, 0),
        "judgment": ({"mp": 5}, 3),
        "final_verdict": ({"mp": 10}, 6),
        "holy_light": ({"mp": 30}, 0),
        "hammer_of_justice": ({"mp": 0}, 15),
        "divine_shield": ({"mp": 0}, 25),
        "shield_of_vengeance": ({"mp": 10}, 15),
        "lay_on_hands": ({"mp": 20}, 50),
        "avenging_wrath": ({"mp": 20}, 20),
    }
    for ability_id, (cost, cooldown) in expected_existing_paladin.items():
        ability = ABILITIES[ability_id]
        assert ability.get("cost") == cost, f"{ability_id} cost should be unchanged"
        assert ability.get("cooldown") == cooldown, f"{ability_id} cooldown should be unchanged"
    assert ABILITIES["lay_on_hands"].get("cooldown_resets_on_cast") == ["shield_of_vengeance"], "Lay on Hands should only reset Shield of Vengeance"
    assert ABILITIES["avenging_wrath"].get("cooldown_resets_on_cast") == ["shield_of_vengeance"], "Avenging Wrath should only reset Shield of Vengeance"

    lay_match = make_match("paladin", "warrior", seed=7302)
    paladin = lay_match.state[lay_match.players[0]]
    paladin.stats["spirit"] = 0
    paladin.res.hp -= 25
    paladin.cooldowns["shield_of_vengeance"] = [12]
    paladin.cooldowns["judgment"] = [2]
    submit_turn(lay_match, "lay_on_hands", _DEF_PASS)
    assert "shield_of_vengeance" not in paladin.cooldowns, "Lay on Hands should reset Shield of Vengeance cooldown"
    assert paladin.cooldowns.get("judgment") == [1], "Lay on Hands should not reset unrelated Paladin cooldowns"
    assert any("Shield of Vengeance's cooldown has been reset!" in line for line in _turn_lines(lay_match, 1)), "Lay on Hands reset should be logged"

    wrath_match = make_match("paladin", "warrior", seed=7303)
    paladin = wrath_match.state[wrath_match.players[0]]
    paladin.stats["spirit"] = 0
    paladin.cooldowns["shield_of_vengeance"] = [12]
    paladin.cooldowns["judgment"] = [2]
    submit_turn(wrath_match, "avenging_wrath", _DEF_PASS)
    assert "shield_of_vengeance" not in paladin.cooldowns, "Avenging Wrath should reset Shield of Vengeance cooldown"
    assert paladin.cooldowns.get("judgment") == [1], "Avenging Wrath should not reset unrelated Paladin cooldowns"
    assert any("Shield of Vengeance's cooldown has been reset!" in line for line in _turn_lines(wrath_match, 1)), "Avenging Wrath reset should be logged"

    duel_html = _detect_duel_html_path().read_text()
    assert "Restore your HP to full and reset Shield of Vengeance's cooldown." in duel_html, "Lay on Hands docs should mention Shield of Vengeance reset"
    assert "resets Shield of Vengeance's cooldown" in duel_html, "Avenging Wrath docs should mention Shield of Vengeance reset"
    assert "Its cooldown can be reset by Lay on Hands or Avenging Wrath." in duel_html, "Shield of Vengeance docs should mention the reset sources"
    return True


_EMPOWERED_BY_EXPECTED_SPECS = {
    "final_verdict": {
        "effect_id": "paladin_final_verdict_empowered",
        "scaling_override": {"atk": 2.0},
        "consume": {"mode": "remove"},
    },
    "crusader_strike": {
        "effect_id": "avenging_wrath",
        "scaling_override": {"atk": 1.0},
    },
    "judgment": {
        "effect_id": "avenging_wrath",
        "scaling_override": {"atk": 1.4},
    },
    "mind_blast": {
        "effect_id": "mind_blast_empowered",
        "scaling_override": {"int": 1.3},
        "dice_override": {"type": "d8", "power_on": "roll"},
        "log": "Empowered by Mind Flay!",
        "consume": {"mode": "remove"},
    },
    "lava_lash": {
        "effect_id": "lava_surge",
        "scaling_override": {"int": 1.5},
        "dice_override": {"type": "d6", "power_on": "roll"},
        "log": "Empowered by Lava Surge!",
        "consume": {"mode": "stack", "amount": 1},
    },
    "arcane_shot": {
        "effect_id": "arcane_surge",
        "scaling_override": {"atk": 1.0},
        "dice_override": {"type": "d8", "power_on": "roll"},
        "log": "Empowered by Arcane Surge!",
        "consume": {"mode": "remove"},
    },
}


def scenario_empowered_by_metadata_validation() -> bool:
    allowed_keys = {"effect_id", "scaling_override", "dice_override", "log", "consume"}
    allowed_consume_keys = {"mode", "amount"}
    allowed_stat_keys = {"atk", "int"}
    allowed_dice_keys = {"type", "power_on"}
    allowed_consume_modes = {"remove", "stack"}

    declared = {
        ability_id: ability["empowered_by"]
        for ability_id, ability in ABILITIES.items()
        if "empowered_by" in ability
    }
    assert set(declared) == set(_EMPOWERED_BY_EXPECTED_SPECS), (
        f"empowered_by should be declared on exactly the six migrated abilities, found {sorted(declared)}"
    )
    for ability_id, expected_spec in _EMPOWERED_BY_EXPECTED_SPECS.items():
        assert declared[ability_id] == expected_spec, f"{ability_id} empowered_by spec should match the migrated formula variant"

    for ability_id, spec in declared.items():
        assert isinstance(spec, dict), f"{ability_id} empowered_by must be a dict"
        unsupported = set(spec) - allowed_keys
        assert not unsupported, f"{ability_id} empowered_by carries unsupported keys {sorted(unsupported)}"

        effect_id = spec.get("effect_id")
        assert isinstance(effect_id, str) and effect_id, f"{ability_id} empowered_by.effect_id must be a non-empty string"
        assert effect_id in EFFECT_TEMPLATES, f"{ability_id} empowered_by references unknown effect {effect_id!r}"

        if "scaling_override" in spec:
            scaling_override = spec["scaling_override"]
            assert isinstance(scaling_override, dict) and scaling_override, f"{ability_id} scaling_override must be a non-empty dict"
            for stat, value in scaling_override.items():
                assert stat in allowed_stat_keys, f"{ability_id} scaling_override uses unsupported stat key {stat!r}"
                assert isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0, (
                    f"{ability_id} scaling_override[{stat!r}] must be a positive number"
                )

        if "dice_override" in spec:
            dice_override = spec["dice_override"]
            assert isinstance(dice_override, dict), f"{ability_id} dice_override must be a dict"
            unsupported_dice = set(dice_override) - allowed_dice_keys
            assert not unsupported_dice, f"{ability_id} dice_override carries unsupported keys {sorted(unsupported_dice)}"
            die_type = dice_override.get("type")
            assert isinstance(die_type, str) and re.fullmatch(r"d[1-9]\d*", die_type or ""), (
                f"{ability_id} dice_override.type must be a valid die such as 'd8', got {die_type!r}"
            )

        if "log" in spec:
            assert isinstance(spec["log"], str) and spec["log"], f"{ability_id} empowered_by.log must be a non-empty string"

        if "consume" in spec:
            consume = spec["consume"]
            assert isinstance(consume, dict), f"{ability_id} empowered_by.consume must be a dict"
            unsupported_consume = set(consume) - allowed_consume_keys
            assert not unsupported_consume, f"{ability_id} consume carries unsupported keys {sorted(unsupported_consume)}"
            mode = consume.get("mode")
            assert mode in allowed_consume_modes, f"{ability_id} consume.mode must be one of {sorted(allowed_consume_modes)}, got {mode!r}"
            if mode == "stack":
                amount = consume.get("amount", 1)
                assert isinstance(amount, int) and not isinstance(amount, bool) and amount > 0, (
                    f"{ability_id} consume.amount must be a positive integer for stack mode"
                )
                assert EFFECT_TEMPLATES[effect_id].get("stackable") is True, (
                    f"{ability_id} uses stack consumption but effect {effect_id!r} is not stackable"
                )
            else:
                assert "amount" not in consume, f"{ability_id} consume.amount is only supported for stack mode"

    # Unknown consume modes must fail loudly in the resolver instead of being
    # silently ignored.
    mode_match = make_match("priest", "warrior", seed=1)
    mode_priest = mode_match.state[mode_match.players[0]]
    try:
        resolver.consume_ability_empowerment(
            mode_priest,
            {"effect_id": "mind_blast_empowered", "consume": {"mode": "banish"}},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("consume_ability_empowerment should raise on an unsupported consume mode")
    return True


def scenario_paladin_empowered_by_scaling_profiles() -> bool:
    original_roll = resolver.roll
    original_hit = resolver.hit_chance
    try:
        resolver.hit_chance = lambda acc, eva: 100
        resolver.roll = lambda die, rng: {"d6": 4, "d8": 5}.get(die, original_roll(die, rng))

        verdict_match = make_match("paladin", "warrior", seed=8601)
        paladin, warrior = _player_states(verdict_match)
        paladin.stats["crit"] = 0
        warrior.stats["def"] = 0
        effects.apply_effect_by_id(paladin, "paladin_final_verdict_empowered", overrides={"duration": 5})
        expected_verdict = int(paladin.stats["atk"] * 2.0 + 5)
        submit_turn(verdict_match, "final_verdict", _DEF_PASS)
        verdict_turn = _turn_lines(verdict_match, 1)
        assert any(f"Deals {expected_verdict} damage." in line for line in verdict_turn), "Empowered Final Verdict should deal exactly Attack 2.0x + d8"
        assert not _has_effect(paladin, "paladin_final_verdict_empowered"), "Final Verdict empowerment should be removed after use"

        strike_match = make_match("paladin", "warrior", seed=8602)
        paladin, warrior = _player_states(strike_match)
        paladin.stats["crit"] = 0
        warrior.stats["def"] = 0
        effects.apply_effect_by_id(paladin, "avenging_wrath", overrides={"duration": 4})
        # Avenging Wrath's separate global 1.2 outgoing multiplier must still
        # combine with the empowered Attack 1.0x scaling profile.
        expected_strike = int(int(paladin.stats["atk"] * 1.0 + 4) * 1.2)
        submit_turn(strike_match, "crusader_strike", _DEF_PASS)
        strike_turn = _turn_lines(strike_match, 1)
        assert any(f"Deals {expected_strike} damage." in line for line in strike_turn), "Crusader Strike under Avenging Wrath should deal exactly [Attack 1.0x + d6] * 1.2"
        assert _has_effect(paladin, "avenging_wrath"), "Avenging Wrath should not be consumed by Crusader Strike"

        judgment_match = make_match("paladin", "warrior", seed=8603)
        paladin, warrior = _player_states(judgment_match)
        paladin.stats["crit"] = 0
        warrior.stats["def"] = 0
        warrior.stats["magic_resist"] = 0
        effects.apply_effect_by_id(paladin, "avenging_wrath", overrides={"duration": 4})
        expected_judgment = int(int(paladin.stats["atk"] * 1.4 + 4) * 1.2)
        submit_turn(judgment_match, "judgment", _DEF_PASS)
        judgment_turn = _turn_lines(judgment_match, 1)
        assert any(f"Deals {expected_judgment} damage." in line for line in judgment_turn), "Judgment under Avenging Wrath should deal exactly [Attack 1.4x + d6] * 1.2"
        assert _has_effect(paladin, "avenging_wrath"), "Avenging Wrath should not be consumed by Judgment"
        return True
    finally:
        resolver.roll = original_roll
        resolver.hit_chance = original_hit


def scenario_mind_blast_empowered_formula_consume_and_rng_order() -> bool:
    rolled_dice: list[str] = []
    original_roll = resolver.roll
    original_hit = resolver.hit_chance
    try:
        resolver.hit_chance = lambda acc, eva: 100

        def spy_roll(die, rng):
            rolled_dice.append(die)
            return {"d6": 3, "d8": 5}.get(die, original_roll(die, rng))

        resolver.roll = spy_roll

        match = make_match("priest", "warrior", seed=8611)
        priest, warrior = _player_states(match)
        priest.stats["crit"] = 0
        warrior.stats["def"] = 0
        warrior.stats["magic_resist"] = 0
        effects.apply_effect_by_id(priest, "mind_blast_empowered", overrides={"duration": 5})
        expected = int(priest.stats["int"] * 1.3 + 5)
        submit_turn(match, "mind_blast", _DEF_PASS)
        turn = _turn_lines(match, 1)
        assert any(
            "Roll d8 = 5." in line and "Empowered by Mind Flay!" in line and f"Deals {expected} damage." in line
            for line in turn
        ), "Empowered Mind Blast should deal exactly Intellect 1.3x + d8 with the Mind Flay log"
        assert not any("Roll d6 = 3." in line for line in turn), "The displayed base-roll log line should be replaced by the override roll"
        assert not _has_effect(priest, "mind_blast_empowered"), "Mind Blast empowerment should be removed after use"

        # RNG-order guardrail: the base d6 is still consumed from the seeded
        # stream before the empowered d8 override is rolled.
        empowered_sequence = [die for die in rolled_dice if die in ("d6", "d8")]
        assert empowered_sequence[:2] == ["d6", "d8"], (
            f"Empowered Mind Blast must roll the base d6 before the override d8, got {empowered_sequence}"
        )
        return True
    finally:
        resolver.roll = original_roll
        resolver.hit_chance = original_hit


def scenario_empowerment_consumed_on_miss_but_not_on_rejection() -> bool:
    rolled_dice: list[str] = []
    original_roll = resolver.roll
    original_hit = resolver.hit_chance
    try:
        resolver.hit_chance = lambda acc, eva: 0

        def spy_roll(die, rng):
            rolled_dice.append(die)
            return original_roll(die, rng)

        resolver.roll = spy_roll

        miss_match = make_match("priest", "warrior", seed=8621)
        priest, _ = _player_states(miss_match)
        effects.apply_effect_by_id(priest, "mind_blast_empowered", overrides={"duration": 5})
        submit_turn(miss_match, "mind_blast", _DEF_PASS)
        miss_turn = _turn_lines(miss_match, 1)
        assert any("cast Mind Blast" in line and "Miss!" in line for line in miss_turn), "Forced-miss setup should produce an attack-roll miss"
        assert not _has_effect(priest, "mind_blast_empowered"), "Empowerment should still be consumed on a valid cast whose attack roll misses"
        assert "d8" not in rolled_dice, "The override die must not be rolled when the hit does not land"
        assert not any("Empowered by Mind Flay!" in line for line in miss_turn), "Missed empowered cast should not log the empowerment"
    finally:
        resolver.roll = original_roll
        resolver.hit_chance = original_hit

    deny_match = make_match("priest", "warrior", seed=8622)
    priest, _ = _player_states(deny_match)
    effects.apply_effect_by_id(priest, "mind_blast_empowered", overrides={"duration": 5})
    priest.res.mp = 0
    submit_turn(deny_match, "mind_blast", _DEF_PASS)
    deny_turn = _turn_lines(deny_match, 1)
    assert any("didn't have enough mana" in line for line in deny_turn), "Zero-mana Mind Blast should be rejected before resolution"
    assert _has_effect(priest, "mind_blast_empowered"), "Empowerment should not be consumed when the action is rejected before resolution"
    return True


def scenario_lava_surge_and_flame_dance_stack_on_lava_lash() -> bool:
    original_roll = resolver.roll
    original_hit = resolver.hit_chance
    try:
        resolver.hit_chance = lambda acc, eva: 100
        resolver.roll = lambda die, rng: {"d4": 2, "d6": 4}.get(die, original_roll(die, rng))

        match = make_match("shaman", "warrior", seed=8631)
        shaman, warrior = _player_states(match)
        shaman.stats["crit"] = 0
        shaman.stats["int"] = 10
        warrior.stats["def"] = 0
        warrior.stats["magic_resist"] = 0
        for _ in range(2):
            effects.apply_effect_by_id(shaman, "lava_surge", overrides={"duration": 3})
        effects.apply_effect_by_id(shaman, "flame_dance", overrides={"duration": 5})

        # Lava Surge (empowered_by) and Flame Dance (separate raw multiplier)
        # must keep stacking on the same Lava Lash cast.
        expected = int(int(10 * 1.5 + 4) * 1.5)
        submit_turn(match, "lava_lash", _DEF_PASS)
        turn = _turn_lines(match, 1)
        assert any(
            "Roll d6 = 4." in line
            and "Empowered by Lava Surge!" in line
            and "Empowered by Flame Dance!" in line
            and f"Deals {expected} damage." in line
            for line in turn
        ), "Lava Surge and Flame Dance should both empower the same Lava Lash cast"
        surge = effects.get_effect(shaman, "lava_surge")
        assert surge is not None and effects.effect_stack_count(surge) == 1, "Empowered Lava Lash should consume exactly one Lava Surge stack and keep the rest"
        assert not effects.has_effect(shaman, "flame_dance"), "Flame Dance should still be consumed by its own separate landed-fire-hit rule"
        return True
    finally:
        resolver.roll = original_roll
        resolver.hit_chance = original_hit
