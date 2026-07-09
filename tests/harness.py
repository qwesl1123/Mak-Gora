"""Shared bootstrap and helper utilities for Mak'Gora regression tests.

This module intentionally contains only reusable test harness code; scenario
functions and the SCENARIOS registry stay in regression_suite.py.
"""
from __future__ import annotations

import importlib.util
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
    _load_module("games.duel.engine.damage_types", _ENGINE_DIR / "damage_types.py")
    _load_module("games.duel.engine.damage_events", _ENGINE_DIR / "damage_events.py")
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
CLASSES = sys.modules["games.duel.content.classes"].CLASSES
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
                "entity_type": pet.entity_type,
                "hp": pet.hp,
                "hp_max": pet.hp_max,
                "mp": getattr(pet, "mp", 0),
                "mp_max": getattr(pet, "mp_max", 0),
                "stats": dict(sorted((getattr(pet, "stats", {}) or {}).items())),
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
            "entity_type": ps.entity_type,
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


