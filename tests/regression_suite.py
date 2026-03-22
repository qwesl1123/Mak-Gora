"""Automated regression suite for Mak'Gora engine turn resolution.

Uses stdlib only and directly exercises MatchState + apply_prep_build + resolve_turn.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _detect_layout() -> tuple[Path, Path]:
    nested_engine = _REPO_ROOT / "engine"
    nested_content = _REPO_ROOT / "content"
    if (nested_engine / "models.py").exists() and (nested_content / "abilities.py").exists():
        return nested_engine, nested_content

    flat_models = _REPO_ROOT / "models.py"
    flat_abilities = _REPO_ROOT / "abilities.py"
    if flat_models.exists() and flat_abilities.exists():
        return _REPO_ROOT, _REPO_ROOT

    raise RuntimeError(
        f"Unable to detect duel module layout from {_REPO_ROOT}. "
        "Expected either ./engine+./content layout or flat module layout."
    )


_ENGINE_DIR, _CONTENT_DIR = _detect_layout()


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap_engine_modules() -> Dict[str, Any]:
    if "games.duel.engine.resolver" in sys.modules:
        return {
            "models": sys.modules["games.duel.engine.models"],
            "resolver": sys.modules["games.duel.engine.resolver"],
            "effects": sys.modules["games.duel.engine.effects"],
        }

    for pkg_name in ("games", "games.duel", "games.duel.engine", "games.duel.content"):
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = []
            sys.modules[pkg_name] = pkg

    _load_module("games.duel.engine.models", _ENGINE_DIR / "models.py")
    _load_module("games.duel.engine.dice", _ENGINE_DIR / "dice.py")
    _load_module("games.duel.engine.rules", _ENGINE_DIR / "rules.py")
    _load_module("games.duel.content.balance", _CONTENT_DIR / "balance.py")
    _load_module("games.duel.content.items", _CONTENT_DIR / "items.py")
    _load_module("games.duel.content.classes", _CONTENT_DIR / "classes.py")
    _load_module("games.duel.content.abilities", _CONTENT_DIR / "abilities.py")
    _load_module("games.duel.content.pets", _CONTENT_DIR / "pets.py")
    _load_module("games.duel.engine.effects", _ENGINE_DIR / "effects.py")
    _load_module("games.duel.engine.pet_ai", _ENGINE_DIR / "pet_ai.py")
    resolver = _load_module("games.duel.engine.resolver", _ENGINE_DIR / "resolver.py")

    return {
        "models": sys.modules["games.duel.engine.models"],
        "resolver": resolver,
        "effects": sys.modules["games.duel.engine.effects"],
    }


_MODS = _bootstrap_engine_modules()
MatchState = _MODS["models"].MatchState
PlayerBuild = _MODS["models"].PlayerBuild
PetState = _MODS["models"].PetState
resolver = _MODS["resolver"]
effects = _MODS["effects"]
PETS = sys.modules["games.duel.content.pets"].PETS


apply_prep_build = resolver.apply_prep_build
resolve_turn = resolver.resolve_turn
submit_action = resolver.submit_action


_DEF_PASS = "pass_turn"


def state_extract(match) -> Dict[str, Any]:
    state: Dict[str, Any] = {"turn": match.turn, "players": {}}
    for sid in sorted(match.players):
        ps = match.state[sid]
        absorbs = {
            key: {
                "remaining": int((meta or {}).get("remaining", 0)),
                "effect_id": (meta or {}).get("effect_id"),
                "source_name": (meta or {}).get("source_name"),
            }
            for key, meta in sorted((ps.res.absorbs or {}).items())
        }
        effects_list = [
            {
                "id": fx.get("id"),
                "duration": fx.get("duration"),
                "category": fx.get("category"),
                "school": fx.get("school"),
                "flags": dict(sorted((fx.get("flags") or {}).items())),
            }
            for fx in sorted(ps.effects, key=lambda entry: (str(entry.get("id")), str(entry.get("name"))))
        ]
        cooldowns = {ability: list(values) for ability, values in sorted(ps.cooldowns.items())}
        pets = {
            pet_id: {
                "template_id": pet.template_id,
                "name": pet.name,
                "hp": pet.hp,
                "hp_max": pet.hp_max,
                "duration": pet.duration,
                "effects": [
                    {"id": fx.get("id"), "duration": fx.get("duration")}
                    for fx in sorted((pet.effects or []), key=lambda entry: str(entry.get("id")))
                ],
            }
            for pet_id, pet in sorted((ps.pets or {}).items())
        }
        state["players"][sid] = {
            "class_id": ps.build.class_id,
            "hp": ps.res.hp,
            "hp_max": ps.res.hp_max,
            "mp": ps.res.mp,
            "mp_max": ps.res.mp_max,
            "energy": ps.res.energy,
            "energy_max": ps.res.energy_max,
            "rage": ps.res.rage,
            "rage_max": ps.res.rage_max,
            "absorbs": absorbs,
            "effects": effects_list,
            "cooldowns": cooldowns,
            "pets": pets,
            "hunter_pet_memory": dict(sorted((ps.hunter_pet_memory or {}).items())),
            "dead_hunter_pets": dict(sorted((ps.dead_hunter_pets or {}).items())),
            "active_pet_id": ps.active_pet_id,
        }
    return state


def _assert_invariants(match, prior_turn: int, prior_log_len: int) -> None:
    assert match.turn == prior_turn + 1, "resolve_turn should increment turn exactly once"
    new_lines = match.log[prior_log_len:]
    header = f"Turn {match.turn}"
    assert sum(1 for line in new_lines if line == header) == 1, "duplicate/missing turn header for a single turn"
    assert not match.submitted, "match.submitted should be cleared after successful resolution"
    assert match.turn_in_progress is False, "turn_in_progress should be false after success"

    for sid in match.players:
        ps = match.state[sid]
        assert ps.res.hp >= 0, f"negative hp for {sid}"
        for absorb_key, absorb_meta in (ps.res.absorbs or {}).items():
            remaining = int((absorb_meta or {}).get("remaining", 0))
            assert remaining >= 0, f"absorb layer {absorb_key} has negative remaining"
        for fx in ps.effects:
            duration = fx.get("duration")
            if duration is not None:
                assert int(duration) >= 0, f"effect duration below zero on {sid}: {fx.get('id')}"
        for pet_id, pet in sorted((ps.pets or {}).items()):
            assert 0 <= pet.hp <= pet.hp_max, f"pet hp out of range for {pet_id}"
            assert pet.hp > 0, f"dead pet should have been removed: {pet_id}"
            for fx in pet.effects or []:
                duration = fx.get("duration")
                if duration is not None:
                    assert int(duration) >= 0, f"pet effect duration below zero on {pet_id}"


def make_match(p1_class, p2_class, p1_items=None, p2_items=None, seed=123):
    p1_sid, p2_sid = "p1_sid", "p2_sid"
    match = MatchState(room_id="regression", players=[p1_sid, p2_sid], phase="combat", seed=seed)

    p1_build = PlayerBuild(class_id=p1_class)
    if p1_items:
        p1_build.items.update(dict(p1_items))
    p2_build = PlayerBuild(class_id=p2_class)
    if p2_items:
        p2_build.items.update(dict(p2_items))

    match.picks[p1_sid] = p1_build
    match.picks[p2_sid] = p2_build
    apply_prep_build(match)
    return match


def submit_turn(match, p1_ability_id, p2_ability_id):
    s1, s2 = match.players
    prior_turn, prior_log_len = match.turn, len(match.log)
    submit_action(match, s1, {"ability_id": p1_ability_id})
    submit_action(match, s2, {"ability_id": p2_ability_id})
    resolve_turn(match)
    if match.turn == prior_turn + 1:
        _assert_invariants(match, prior_turn, prior_log_len)
    else:
        raise AssertionError("turn did not resolve")
    return state_extract(match)


def run_turns(match, pairs: Iterable[Tuple[str, str]]):
    snapshots = []
    for p1_ability_id, p2_ability_id in pairs:
        snapshots.append(submit_turn(match, p1_ability_id, p2_ability_id))
    return snapshots


def _has_effect(ps, effect_id: str) -> bool:
    return any(fx.get("id") == effect_id for fx in ps.effects)


def _player_states(match):
    p1_sid, p2_sid = match.players
    return match.state[p1_sid], match.state[p2_sid]


def _assert_no_stun_effect(ps) -> None:
    stun_ids = {"stunned", "ring_of_ice_freeze"}
    active = {fx.get("id") for fx in ps.effects}
    assert not (active & stun_ids), f"Expected no stun/freeze effects, found: {sorted(active & stun_ids)}"


def _turn_lines(match, turn_number: int) -> List[str]:
    header = f"Turn {turn_number}"
    start = match.log.index(header) + 1
    end = len(match.log)
    for idx in range(start, len(match.log)):
        if match.log[idx].startswith("Turn "):
            end = idx
            break
    return match.log[start:end]


_BREAK_ON_DAMAGE_CC_CASES = (
    ("ring_of_ice_freeze", "Ring of Ice", "frozen"),
    ("freezing_trap_freeze", "Freezing Trap", "frozen"),
    ("feared", "Fear", "feared"),
)


def scenario_mindgames_lay_on_hands() -> bool:
    match = make_match("priest", "paladin", seed=123)
    pal = match.state[match.players[1]]
    pal.res.hp = max(1, pal.res.hp - 40)

    submit_turn(match, "mindgames", "lay_on_hands")

    assert pal.res.hp < pal.res.hp_max, "Lay on Hands should be twisted into self-damage under Mindgames"
    assert not _has_effect(pal, "mindgames"), "Mindgames should expire after use"
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
    submit_turn(match, "dragon_roar", "iceblock")

    assert _has_effect(mage, "iceblock"), "Ice Block should apply this turn"
    assert mage.res.hp == mage_hp_before, "Ice Block should prevent champion AoE damage"
    for pid in imp_ids:
        assert _pet_took_damage_or_died(mage, pid, imp_hp_before[pid]), "Dragon Roar should still damage enemy pets through champion immunity"
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




def _add_pet(owner, pet_id: str, template_id: str = "imp") -> None:
    template = PETS[template_id]
    hp = int(template.get("hp", 1) or 1)
    owner.pets[pet_id] = PetState(
        id=pet_id,
        template_id=template_id,
        name=str(template.get("name", template_id.title())),
        owner_sid=owner.sid,
        hp=hp,
        hp_max=hp,
        effects=[],
        duration=None,
    )


def _pet_took_damage_or_died(owner, pet_id: str, hp_before: int) -> bool:
    pet = owner.pets.get(pet_id)
    return pet is None or pet.hp < hp_before

def _setup_imps(match, owner_idx: int = 0):
    owner_sid = match.players[owner_idx]
    other_sid = match.players[1 - owner_idx]
    run_turns(match, [("summon_imp", _DEF_PASS), ("summon_imp", _DEF_PASS), ("summon_imp", _DEF_PASS)])
    return owner_sid, other_sid


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
    assert effects.absorb_total(priest) == total_after_damage - 20
    return True


def scenario_pet_summon_data_driven() -> bool:
    warlock_match = make_match("warlock", "priest", seed=123)
    run_turns(warlock_match, [("summon_imp", _DEF_PASS), ("summon_imp", _DEF_PASS), ("summon_imp", _DEF_PASS), ("summon_imp", _DEF_PASS)])
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


def _active_pet(owner, template_id: str | None = None):
    pets = sorted((owner.pets or {}).values(), key=lambda pet: pet.id)
    if template_id is None:
        return pets[0] if pets else None
    for pet in pets:
        if pet.template_id == template_id:
            return pet
    return None


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
    assert hunter.hunter_pet_memory.get("frostsaber") == 12, "Dismissed Frostsaber HP should be remembered"
    assert any("calls for Emerald Serpent." in line for line in _turn_lines(match, 2)), "Hunter summon log should say calls for Emerald Serpent"

    hunter.cooldowns.pop("call_saber", None)
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
    assert hunter.hunter_pet_memory.get("frostsaber", 0) > 0, "Dismissed saber HP should be stored"
    return True


def scenario_hunter_multi_shot_aoe() -> bool:
    match = make_match("hunter", "warlock", seed=123)
    hunter_sid, warlock_sid = match.players
    warlock = match.state[warlock_sid]
    run_turns(match, [(_DEF_PASS, "summon_imp"), (_DEF_PASS, "summon_imp"), (_DEF_PASS, "summon_imp")])
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


def scenario_hunter_turtle_priority() -> bool:
    match = make_match("hunter", "rogue", seed=123)
    hunter = match.state[match.players[0]]
    hp_before = hunter.res.hp

    submit_turn(match, "turtle", "kidney_shot")
    assert _has_effect(hunter, "aspect_of_turtle"), "Aspect of the Turtle should apply immediately"
    assert not any((fx.get("display") or {}).get("war_council") for fx in hunter.effects if fx.get("id") == "aspect_of_turtle"), "Aspect of the Turtle should not create a War Council status badge"
    assert any("uses their bare hands to cast Kidney Shot. Target evades the attack — Miss!" in line for line in match.log), "Single-target attacks into Turtle should use the evasion-style miss wording"
    assert any("uses their bare hands to cast Aspect of the Turtle. Causes all single-target spells and attacks to miss." in line for line in match.log), "Aspect of the Turtle should log its miss-causing effect text"
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


def scenario_hunter_wildfire_arcane_proc() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]

    submit_turn(match, "wildfire_bomb", _DEF_PASS)
    arcane_proc = next((fx for fx in hunter.effects if fx.get("id") == "arcane_shot_proc"), None)
    assert arcane_proc is not None, "Wildfire Bomb should grant Arcane Shot proc"
    assert int(arcane_proc.get("duration", 0) or 0) == 1, "Arcane Shot proc should be available for the next turn only after the proc turn resolves"
    proc_line = f"{match.players[0][:5]} can use Arcane Shot!"
    assert proc_line in match.log, "Wildfire Bomb proc log should use the actor sid token so snapshots can render Hunter(you)"
    assert not any("Wildfire Bomb. can use Arcane Shot!" in line or "Wildfire Bomb. Hunter can use Arcane Shot!" in line for line in match.log), "Wildfire Bomb action line should not embed the proc sentence"

    submit_turn(match, "arcane_shot", _DEF_PASS)
    assert not _has_effect(hunter, "arcane_shot_proc"), "Arcane Shot should consume its proc"

    match2 = make_match("hunter", "warrior", seed=123)
    hunter2 = match2.state[match2.players[0]]
    submit_turn(match2, "wildfire_bomb", _DEF_PASS)
    arcane_proc_2 = next((fx for fx in hunter2.effects if fx.get("id") == "arcane_shot_proc"), None)
    assert arcane_proc_2 is not None and int(arcane_proc_2.get("duration", 0) or 0) == 1, "Unused Arcane Shot proc should still be present immediately after the proc turn"
    submit_turn(match2, _DEF_PASS, _DEF_PASS)
    assert not _has_effect(hunter2, "arcane_shot_proc"), "Arcane Shot proc should expire if unused next turn"
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


def scenario_hunter_proc_log_stays_at_top_of_turn() -> bool:
    match = make_match("warrior", "hunter", seed=123)

    submit_turn(match, _DEF_PASS, "wildfire_bomb")

    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert latest_turn[0] == f"{match.players[1][:5]} can use Arcane Shot!", "Hunter proc reminder should be the first line of the turn even when the Hunter acts second"
    warrior_action_idx = next(i for i, line in enumerate(latest_turn) if "uses their bare hands to cast Pass Turn" in line)
    hunter_action_idx = next(i for i, line in enumerate(latest_turn) if "uses their bare hands to cast Wildfire Bomb" in line)
    assert 0 < warrior_action_idx < hunter_action_idx, "Proc reminder should appear before both players' action lines"
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
    assert f"{match.players[0][:5]} can use Raptor Strike!" in match.log, "Aimed Shot proc log should use the actor sid token so snapshots can render Hunter(you)"
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


def scenario_hunter_boar_redirect_same_turn_brace() -> bool:
    match = make_match("warlock", "hunter", seed=3)
    hunter = match.state[match.players[1]]
    hunter_hp_before = hunter.res.hp
    submit_turn(match, "drain_life", "call_boar")

    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Boar should be active"
    latest_turn = _turn_lines(match, 1)
    assert any("Barrens Boar braces to intercept attacks." in line for line in latest_turn), "Boar should brace on its summon turn when the pre-action special fires"
    assert any("Barrens Boar intercepts Drain Life" in line for line in latest_turn), "Intercept log should reference the redirected Drain Life"
    assert hunter.res.hp == hunter_hp_before, "Same-turn Blocking Defence should keep Drain Life off the Hunter"
    assert boar.hp < boar.hp_max, "Same-turn Blocking Defence should route Drain Life damage into the boar"
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
    assert any("uses their bare hands to cast Cloak of Shadows" in line for line in latest_turn), "Cloak action should resolve"
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


def scenario_hunter_disengage_uses_custom_miss_text() -> bool:
    match = make_match("warrior", "hunter", seed=123)
    submit_turn(match, "basic_attack", "disengage")
    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert any("Target leaps away — Miss." in line for line in latest_turn), "Disengage should use the custom leap-away miss text"
    assert not any("Target blinks away — Miss." in line and "Disengage" in line for line in latest_turn), "Disengage should not reuse the blink-away miss text"
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


def scenario_hunter_pet_permanent_death_resummon_blocked() -> bool:
    match = make_match("hunter", "warrior", seed=123)
    hunter = match.state[match.players[0]]
    submit_turn(match, "call_saber", _DEF_PASS)
    saber = _active_pet(hunter, "frostsaber")
    assert saber is not None
    saber.hp = 0
    resolver.cleanup_pets(match)
    assert hunter.dead_hunter_pets.get("frostsaber"), "Dead hunter pet should be marked permanently dead"
    assert hunter.hunter_pet_memory.get("frostsaber") == 0, "Permanent pet death should zero remembered HP"
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
    assert hunter.hunter_pet_memory.get("frostsaber") == remembered_hp, "Dismiss should store current HP before removing the pet"
    dismissed_turn = match.turn
    run_turns(match, [(_DEF_PASS, _DEF_PASS), (_DEF_PASS, _DEF_PASS)])
    assert hunter.hunter_pet_memory.get("frostsaber") == remembered_hp, "Dismissed pet should not keep taking DoT ticks"
    idle_turn_logs = _turn_lines(match, dismissed_turn + 1) + _turn_lines(match, dismissed_turn + 2)
    assert not any("Frostsaber" in line for line in idle_turn_logs), "Dismissed pet should not keep acting or logging runtime effects while inactive"

    hunter.cooldowns.pop("call_saber", None)
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
    assert hunter.hunter_pet_memory.get("frostsaber") == 12, "Frostsaber HP should be remembered on dismissal"
    serpent.hp = 9

    hunter.cooldowns.clear()
    submit_turn(match, "call_boar", _DEF_PASS)
    boar = _active_pet(hunter, "barrens_boar")
    assert boar is not None, "Barrens Boar should summon"
    assert hunter.hunter_pet_memory.get("emerald_serpent") == 9, "Serpent HP should be remembered on dismissal"
    boar.hp = 7

    hunter.cooldowns.clear()
    submit_turn(match, "call_saber", _DEF_PASS)
    saber_returned = _active_pet(hunter, "frostsaber")
    assert saber_returned is not None and saber_returned.hp == 12, "Frostsaber should return at its remembered HP after multiple swaps"
    assert hunter.hunter_pet_memory.get("barrens_boar") == 7, "Boar HP should be remembered when it is dismissed"
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


SCENARIOS = [
    scenario_mindgames_lay_on_hands,
    scenario_mass_dispel_selective_removal,
    scenario_cloak_of_shadows_interactions,
    scenario_stealth_priority_over_stun,
    scenario_immunity_priority_over_stuns,
    scenario_stealth_priority_over_stuns_expanded,
    scenario_stun_priority_over_blink_like,
    scenario_blink_like_blocks_attacks_for_two_turns,
    scenario_iceblock_priority_vs_aoe_with_pets,
    scenario_iceblock_blocks_same_turn_stun_and_next_turn_attack,
    scenario_aoe_hits_pets_with_immune_champion,
    scenario_absorb_layering,
    scenario_pet_summon_data_driven,
    scenario_hunter_pet_summon_swap_memory,
    scenario_hunter_only_one_active_pet,
    scenario_hunter_multi_shot_aoe,
    scenario_hunter_turtle_priority,
    scenario_hunter_wildfire_arcane_proc,
    scenario_hunter_wildfire_dot_log_order,
    scenario_hunter_proc_log_stays_at_top_of_turn,
    scenario_hunter_aimed_shot_raptor_pet_special,
    scenario_hunter_boar_redirect,
    scenario_hunter_boar_redirect_same_turn_brace,
    scenario_hunter_freezing_trap_breaks_on_damage,
    scenario_hunter_freezing_trap_respects_cloak_same_turn,
    scenario_hunter_freezing_trap_respects_active_cloak,
    scenario_mage_hot_streak_lasts_three_turns,
    scenario_ring_of_ice_freezes_and_breaks_on_damage,
    scenario_fear_applies_feared_and_breaks_on_damage,
    scenario_break_on_damage_cc_no_damage_turn_preserves_lockout,
    scenario_break_on_damage_cc_dot_tick_breaks,
    scenario_break_on_damage_cc_aoe_breaks,
    scenario_break_on_damage_cc_pet_damage_breaks,
    scenario_break_on_damage_cc_persists_after_same_turn_mutual_freeze,
    scenario_break_on_damage_cc_persists_after_same_turn_fear_vs_freeze,
    scenario_break_on_damage_logs_use_clean_wording_and_bottom_order,
    scenario_redirected_damage_does_not_break_frozen,
    scenario_redirected_damage_does_not_break_feared,
    scenario_aoe_bypasses_redirect_and_breaks_frozen,
    scenario_dot_bypasses_redirect_and_breaks_feared,
    scenario_proc_raptor_strike_expires_correctly,
    scenario_proc_pyroblast_window_correct,
    scenario_cc_status_display_metadata_is_exposed,
    scenario_hunter_disengage_uses_custom_miss_text,
    scenario_hunter_flare_logs_stealth_breaks,
    scenario_hunter_pet_permanent_death_resummon_blocked,
    scenario_hunter_dead_pet_type_does_not_block_other_pet_types,
    scenario_hunter_dismissed_pet_clears_runtime_effects,
    scenario_hunter_multi_pet_memory_swap_cycle,
    scenario_hunter_redirect_removed_on_pet_dismiss,
    scenario_negative_non_damage_effect_does_not_break_frozen,
    scenario_negative_non_damage_effect_does_not_break_feared,
    scenario_hunter_serpent_special_respects_stealth,
    scenario_pet_action_text_persists_on_miss,
    scenario_mindgames_still_allows_direct_damage_dots,
    scenario_invalid_class_rejected,
    scenario_valid_class_id_is_normalized_before_build,
]


def run_all() -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []
    for scenario in SCENARIOS:
        try:
            scenario()
            results.append((scenario.__name__, True, ""))
        except AssertionError as exc:
            results.append((scenario.__name__, False, str(exc)))
    return results
