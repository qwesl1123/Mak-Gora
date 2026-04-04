"""Automated regression suite for Mak'Gora engine turn resolution.

Uses stdlib only and directly exercises MatchState + apply_prep_build + resolve_turn.
"""

from __future__ import annotations

import importlib.util
import random
import re
import sys
import types
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _detect_duel_html_path() -> Path:
    candidates = [
        _REPO_ROOT / "duel.html",
        _REPO_ROOT / "templates" / "duel.html",
        _REPO_ROOT.parent / "templates" / "duel.html",
        _REPO_ROOT.parent.parent / "templates" / "duel.html",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Unable to find duel.html; checked: "
        + ", ".join(str(path) for path in candidates)
    )


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


def _bootstrap_socket_module():
    if "games.duel.sockets" in sys.modules:
        return sys.modules["games.duel.sockets"]

    _bootstrap_engine_modules()
    _load_module("games.duel.state", _REPO_ROOT / "state.py")

    flask_module = types.ModuleType("flask")
    flask_module.request = types.SimpleNamespace(sid=None)
    sys.modules.setdefault("flask", flask_module)

    flask_socketio_module = types.ModuleType("flask_socketio")
    flask_socketio_module.emit = lambda *args, **kwargs: None
    flask_socketio_module.join_room = lambda *args, **kwargs: None
    flask_socketio_module.leave_room = lambda *args, **kwargs: None
    sys.modules.setdefault("flask_socketio", flask_socketio_module)

    return _load_module("games.duel.sockets", _REPO_ROOT / "sockets.py")


_MODS = _bootstrap_engine_modules()
MatchState = _MODS["models"].MatchState
PlayerBuild = _MODS["models"].PlayerBuild
PetState = _MODS["models"].PetState
resolver = _MODS["resolver"]
effects = _MODS["effects"]
PET_AI = sys.modules["games.duel.engine.pet_ai"]
PETS = sys.modules["games.duel.content.pets"].PETS
ABILITIES = sys.modules["games.duel.content.abilities"].ABILITIES
EFFECT_TEMPLATES = effects.EFFECT_TEMPLATES
SOCKETS = _bootstrap_socket_module()


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
                "subschool": fx.get("subschool"),
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


def scenario_healing_resolves_from_negative_hp_before_winner_check() -> bool:
    match = make_match("priest", "warrior", seed=123)
    priest_sid, warrior_sid = match.players
    priest = match.state[priest_sid]
    warrior = match.state[warrior_sid]

    priest.res.hp = -6
    effects.apply_effect_by_id(
        warrior,
        "devouring_plague",
        overrides={"duration": 2, "tick_damage": 12, "source_sid": priest_sid, "lifesteal_pct": 1.0},
    )

    submit_turn(match, _DEF_PASS, _DEF_PASS)

    assert priest.res.hp > 0, "DoT lifesteal should revive a source from negative HP in the same turn"
    assert match.phase != "ended", "Winner finalization should happen after same-turn healing/lifesteal resolves"
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


def scenario_rage_crystal_increases_all_rage_gain_sources() -> bool:
    baseline = make_match("warrior", "mage", seed=123)
    crystal = make_match("warrior", "mage", p1_items={"trinket": "rage_crystal"}, seed=123)

    baseline_warrior = baseline.state[baseline.players[0]]
    crystal_warrior = crystal.state[crystal.players[0]]

    baseline_warrior.res.rage = 0
    crystal_warrior.res.rage = 0
    submit_turn(baseline, "overpower", _DEF_PASS)
    submit_turn(crystal, "overpower", _DEF_PASS)
    baseline_overpower_rage = baseline_warrior.res.rage
    crystal_overpower_rage = crystal_warrior.res.rage
    assert crystal_overpower_rage == int(baseline_overpower_rage * 1.25), "Rage Crystal should grant 25% more rage from damage-based rage gain"

    baseline_druid = make_match("mage", "druid", seed=123)
    crystal_druid = make_match("mage", "druid", p2_items={"trinket": "rage_crystal"}, seed=123)
    submit_turn(baseline_druid, _DEF_PASS, "bear_form")
    submit_turn(crystal_druid, _DEF_PASS, "bear_form")
    baseline_bear = baseline_druid.state[baseline_druid.players[1]]
    crystal_bear = crystal_druid.state[crystal_druid.players[1]]
    baseline_bear.res.rage = 0
    crystal_bear.res.rage = 0
    submit_turn(baseline_druid, "fireball", _DEF_PASS)
    submit_turn(crystal_druid, "fireball", _DEF_PASS)
    assert crystal_bear.res.rage == int(baseline_bear.res.rage * 1.25), "Rage Crystal should grant 25% more rage from damage taken in Bear Form"
    return True


def scenario_warlock_imp_log_coloring_mapping_present() -> bool:
    duel_html = _detect_duel_html_path().read_text(encoding="utf-8")
    assert '{ names: ["Imp"], className: "log-class-warlock" }' in duel_html, "Combat log pet styling should map Imp to warlock class color"
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
    assert hunter.hunter_pet_memory.get("frostsaber", 0) > 0, "Dismissed saber HP should be stored"
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

    submit_turn(match, _DEF_PASS, "summon_imp")
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

    submit_turn(match, _DEF_PASS, "summon_imp")
    submit_turn(match, _DEF_PASS, "summon_imp")
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
    proc_line = f"{match.players[0][:5]} has Arcane Shot!"
    assert proc_line in match.log, "Wildfire Bomb proc log should use the actor sid token so snapshots can render Hunter(you)"
    assert not any("Wildfire Bomb. has Arcane Shot!" in line or "Wildfire Bomb. Hunter has Arcane Shot!" in line for line in match.log), "Wildfire Bomb action line should not embed the proc sentence"

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


def scenario_mass_dispel_removes_same_turn_wildfire_burn() -> bool:
    match = make_match("priest", "hunter", seed=123)
    priest_sid, hunter_sid = match.players

    submit_turn(match, "mass_dispel", "wildfire_bomb")

    priest = match.state[priest_sid]
    assert not _has_effect(priest, "wildfire_burn"), "Mass Dispel should remove Wildfire Burn applied in the same turn"
    assert any("Wildfire Burn" in line and "removed by Mass Dispel" in line for line in match.log), "Same-turn Wildfire removal should be logged by Mass Dispel"
    return True


def scenario_hunter_proc_log_stays_at_top_of_turn() -> bool:
    match = make_match("warrior", "hunter", seed=123)

    submit_turn(match, _DEF_PASS, "wildfire_bomb")

    latest_turn = match.log[match.log.index("Turn 1") + 1:]
    assert latest_turn[0] == f"{match.players[1][:5]} has Arcane Shot!", "Hunter proc reminder should be the first line of the turn even when the Hunter acts second"
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
    proc_idx = next((i for i, line in enumerate(latest_turn) if "has Arcane Shot!" in line), -1)
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
    brace_idx = next((i for i, line in enumerate(latest_turn) if "Barrens Boar braces to intercept attacks." in line), -1)
    action_idx = next((i for i, line in enumerate(latest_turn) if "uses their bare hands to cast Drain Life." in line), -1)
    intercept_idx = next((i for i, line in enumerate(latest_turn) if "Barrens Boar intercepts Drain Life" in line), -1)
    assert brace_idx != -1 and action_idx != -1 and intercept_idx != -1, "Brace, incoming action, and intercept logs should all be present"
    assert brace_idx < action_idx < intercept_idx, "Brace log should appear before actions; intercept log should appear at the redirect point"
    assert hunter.res.hp == hunter_hp_before, "Same-turn Blocking Defence should keep Drain Life off the Hunter"
    assert boar.hp < boar.hp_max, "Same-turn Blocking Defence should route Drain Life damage into the boar"
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
    assert hunter.hunter_pet_memory.get("emerald_serpent") == 0, "Permanent pet death should zero remembered HP"
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
    assert any("Must be in Bear Form." in line for line in form_turn), "Form-gated ability should preserve requires-form behavior"

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


def scenario_hunter_pet_recall_uses_calls_for_wording() -> bool:
    match = make_match("hunter", "warrior", seed=145)
    submit_turn(match, "call_boar", _DEF_PASS)
    submit_turn(match, "call_boar", _DEF_PASS)
    turn_two = _turn_lines(match, 2)
    assert any("calls for Barrens Boar." in line for line in turn_two), "Re-calling Barrens Boar should keep calls-for wording"
    assert not any("refreshes Barrens Boar." in line for line in turn_two), "Re-calling Barrens Boar should not say refreshes"
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


def scenario_immediate_path_denial_precedes_selection_failures() -> bool:
    match = make_match("warlock", "warrior", seed=615)
    warlock = match.state[match.players[0]]
    effects.apply_effect_by_id(warlock, "feared", overrides={"duration": 2})

    submit_turn(match, "teleport", _DEF_PASS)
    latest_turn = match.log[match.log.index("Turn 1") + 1:]

    assert any("tries to use Demonic Circle: Teleport but is feared and cannot act." in line for line in latest_turn), "Immediate-path denial should win over selection failures"
    assert not any("Demonic Circle is required." in line for line in latest_turn), "Immediate-path selection checks should not pre-empt denial logs"
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
        lightning_absorb_segment = thunder_line[thunder_line.rfind("("):]
        assert lightning_absorb_segment not in overpower_line, "Lightning absorb should not be appended to the primary ability line"
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
        submit_turn(match, "arcane_barrage", _DEF_PASS)
        duplicate_lines = [line for line in match.log if "duplicates Arcane Barrage!" in line]
        if not duplicate_lines:
            continue
        assert len(duplicate_lines) == 1, "Dragonwrath duplicate should render Arcane Barrage multi-hit text as one line"
        duplicate_line = duplicate_lines[0]
        assert "Hit 1:" in duplicate_line and "Hit 2:" in duplicate_line and "Hit 3:" in duplicate_line, "Combined duplicate line should include all Arcane Barrage hits"
        return True
    raise AssertionError("Could not find deterministic Dragonwrath Arcane Barrage duplicate proc seed in range")


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


def _expected_mitigated(raw: int, effective_stat: int) -> int:
    return int(raw * (40 / (max(0, effective_stat) + 40)))


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
    assert _has_effect(hunter, "arcane_shot_proc"), "Proc grant should remain unchanged"
    submit_turn(hunter_proc_match, "arcane_shot", _DEF_PASS)
    assert not _has_effect(hunter, "arcane_shot_proc"), "Proc consume timing should remain unchanged"

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
    _, _, _, damage_events = effects.trigger_on_hit_passives(
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


SCENARIOS = [
    scenario_mindgames_lay_on_hands,
    scenario_mass_dispel_selective_removal,
    scenario_healing_resolves_from_negative_hp_before_winner_check,
    scenario_mass_dispel_can_remove_pain_suppression_and_devouring_plague,
    scenario_cloak_of_shadows_interactions,
    scenario_shield_of_vengeance_duration_counts_current_turn,
    scenario_stealth_priority_over_stun,
    scenario_immunity_priority_over_stuns,
    scenario_stealth_priority_over_stuns_expanded,
    scenario_stun_priority_over_blink_like,
    scenario_blink_like_blocks_attacks_for_two_turns,
    scenario_iceblock_priority_vs_aoe_with_pets,
    scenario_blink_like_aoe_still_hits_pets,
    scenario_iceblock_blocks_same_turn_stun_and_next_turn_attack,
    scenario_aoe_hits_pets_with_immune_champion,
    scenario_rage_crystal_increases_all_rage_gain_sources,
    scenario_absorb_layering,
    scenario_pet_summon_data_driven,
    scenario_hunter_pet_summon_swap_memory,
    scenario_hunter_only_one_active_pet,
    scenario_hunter_companion_calls_have_no_cooldown,
    scenario_hunter_multi_shot_aoe,
    scenario_dragon_roar_cannot_miss_from_accuracy,
    scenario_dragon_roar_bleed_applies_to_pets_with_independent_rolls,
    scenario_dragon_roar_dead_pets_do_not_log_bleed_application,
    scenario_hunter_turtle_priority,
    scenario_hunter_wildfire_arcane_proc,
    scenario_hunter_wildfire_dot_log_order,
    scenario_mass_dispel_removes_same_turn_wildfire_burn,
    scenario_hunter_proc_log_stays_at_top_of_turn,
    scenario_proc_and_has_reminders_stay_in_expected_order,
    scenario_recover_log_shows_only_nonzero_resources_and_uses_mana_wording,
    scenario_hunter_aimed_shot_raptor_pet_special,
    scenario_hunter_boar_redirect,
    scenario_hunter_boar_redirect_same_turn_brace,
    scenario_winner_summary_logs_after_pet_phase_and_end_of_turn_resolution,
    scenario_shadow_word_death_double_damage_reminder_wording,
    scenario_hunter_boar_forced_pre_action_redirect_is_consistent,
    scenario_hunter_boar_no_late_brace_without_redirect,
    scenario_hunter_raptor_strike_forces_boar_redirect,
    scenario_hunter_freezing_trap_breaks_on_damage,
    scenario_hunter_freezing_trap_respects_cloak_same_turn,
    scenario_hunter_freezing_trap_respects_active_cloak,
    scenario_mage_hot_streak_lasts_three_turns,
    scenario_ring_of_ice_freezes_and_breaks_on_damage,
    scenario_fear_applies_feared_and_breaks_on_damage,
    scenario_mutual_stuns_count_current_turn_immediately,
    scenario_phase_c_pass1_early_resolution_stages_are_preserved,
    scenario_phase_c_prompt1_middle_resolution_stages_are_preserved,
    scenario_agony_ramp_progression_restored,
    scenario_immediate_path_denial_precedes_selection_failures,
    scenario_mutual_freeze_duration_model_remains_unchanged,
    scenario_break_on_damage_cc_no_damage_turn_preserves_lockout,
    scenario_break_on_damage_cc_dot_tick_breaks,
    scenario_break_on_damage_cc_aoe_breaks,
    scenario_break_on_damage_cc_pet_damage_breaks,
    scenario_break_on_damage_cc_blocks_form_shift_same_turn,
    scenario_break_on_damage_cc_blocks_other_normal_actions_same_turn,
    scenario_break_on_damage_cc_persists_after_same_turn_mutual_freeze,
    scenario_break_on_damage_cc_persists_after_same_turn_fear_vs_freeze,
    scenario_break_on_damage_logs_use_clean_wording_and_bottom_order,
    scenario_break_on_damage_uses_source_ability_name_for_shared_fear_state,
    scenario_stealth_break_log_order_after_actions,
    scenario_shield_of_vengeance_explosion_flushes_stealth_break_log,
    scenario_redirected_damage_does_not_break_frozen,
    scenario_redirected_damage_does_not_break_feared,
    scenario_aoe_bypasses_redirect_and_breaks_frozen,
    scenario_dot_bypasses_redirect_and_breaks_feared,
    scenario_proc_raptor_strike_expires_correctly,
    scenario_proc_pyroblast_window_correct,
    scenario_cc_status_display_metadata_is_exposed,
    scenario_hunter_disengage_uses_custom_miss_text,
    scenario_hunter_flare_logs_stealth_breaks,
    scenario_hunter_pet_permanent_death,
    scenario_hunter_pet_permanent_death_resummon_blocked,
    scenario_hunter_dead_pet_type_does_not_block_other_pet_types,
    scenario_hunter_dismissed_pet_clears_runtime_effects,
    scenario_hunter_multi_pet_memory_swap_cycle,
    scenario_hunter_redirect_removed_on_pet_dismiss,
    scenario_hunter_pet_recall_uses_calls_for_wording,
    scenario_negative_non_damage_effect_does_not_break_frozen,
    scenario_negative_non_damage_effect_does_not_break_feared,
    scenario_hunter_serpent_special_respects_stealth,
    scenario_pet_action_text_persists_on_miss,
    scenario_imp_firebolt_immunity_logs_under_cloak,
    scenario_redirect_and_blink_like_coexist_without_cross_regression,
    scenario_pet_specials_are_blocked_while_pet_is_ccd,
    scenario_shadowfiend_pet_box_hides_turn_counter_badge,
    scenario_mindgames_still_allows_direct_damage_dots,
    scenario_devouring_plague_heals_for_full_tick_damage,
    scenario_passive_secondary_damage_logs_own_absorb_suffix,
    scenario_dragonwrath_duplicate_spell_deals_real_damage,
    scenario_dragonwrath_duplicate_log_includes_class_owner_prefix,
    scenario_dragonwrath_multihit_duplicate_logs_as_single_line,
    scenario_thunderfury_lightning_uses_damage_pipeline,
    scenario_thunderfury_heal_proc_restores_expected_amount,
    scenario_azzinoth_strike_again_deals_secondary_damage,
    scenario_fury_of_azzinoth_cannot_miss_and_ignores_armor,
    scenario_mitigation_physical_uses_def_plus_armor,
    scenario_mitigation_magic_uses_def_plus_magic_resist,
    scenario_ignore_armor_bypasses_only_armor_component,
    scenario_pet_attacks_use_shared_mitigation_stats,
    scenario_break_on_damage_and_lifesteal_use_post_mitigation_damage,
    scenario_phase_c_prompt2_no_spillover_to_effect_application_or_end_of_turn,
    scenario_phase_c_prompt3_effect_application_stage_preserved,
    scenario_phase_d_end_of_turn_stage_preserved,
    scenario_subschool_metadata_and_templates,
    scenario_subschool_event_plumbing_for_dots_and_passives,
    scenario_direct_damage_dot_inherits_ability_subschool,
    scenario_true_aoe_school_subschool_propagation,
    scenario_invalid_class_rejected,
    scenario_valid_class_id_is_normalized_before_build,
    scenario_prep_selection_name_uses_current_submission,
    scenario_command_input_normalizes_abilities_and_items,
    scenario_warlock_imp_log_coloring_mapping_present,
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
