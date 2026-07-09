"""Damage source-kind taxonomy validation suite.

Validates the canonical source-kind constants in ``damage_types.py`` and the
metadata-only wiring of ``source_kind`` onto existing damage packets/events:
queued on-hit proc events, strike-again events, DoT tick sources, pet damage,
Mindgames-twisted self damage, and the Shield of Vengeance explosion.

These checks are deliberately metadata-focused: they assert what each packet
is *labelled* as, never how much damage it deals.
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from regression_suite import (  # type: ignore
    ABILITIES,
    PET_AI,
    PetState,
    effects,
    make_match,
)

damage_types = sys.modules["games.duel.engine.damage_types"]


_REPO_ROOT = Path(__file__).resolve().parents[1]

_EXPECTED_SOURCE_KINDS = {
    "direct_ability_damage",
    "direct_dot_application",
    "dot_tick",
    "on_hit_proc_damage",
    "strike_again_damage",
    "pet_damage",
    "reflect_damage",
    "absorb_explosion_damage",
    "self_damage",
    "environmental_damage",
}


def _engine_dir() -> Path:
    """Mirror the layout detection used by the other suites (flat vs nested)."""
    nested_engine = _REPO_ROOT / "engine"
    if (nested_engine / "resolver.py").exists():
        return nested_engine
    return _REPO_ROOT


def _engine_source(basename: str) -> str:
    return (_engine_dir() / basename).read_text(encoding="utf-8")


def _extract_function_block(source: str, function_name: str) -> str:
    """Return the source block of ``def function_name`` (indentation-based).

    Not line-number based: scans for the def and captures until the first
    non-blank, non-comment line at the same or lower indentation.
    """
    lines = source.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith(f"def {function_name}("):
            continue
        def_indent = len(line) - len(stripped)
        block = [line]
        for later in lines[index + 1:]:
            later_stripped = later.strip()
            if later_stripped and not later_stripped.startswith("#"):
                later_indent = len(later) - len(later.lstrip())
                if later_indent <= def_indent:
                    break
            block.append(later)
        return "\n".join(block)
    raise AssertionError(f"function {function_name} not found in engine source")


def test_taxonomy_constants_are_canonical() -> None:
    kinds = damage_types.ALL_DAMAGE_SOURCE_KINDS
    assert set(kinds) == _EXPECTED_SOURCE_KINDS, "ALL_DAMAGE_SOURCE_KINDS drifted from the AGENTS.md taxonomy"
    assert len(kinds) == len(set(kinds)), "ALL_DAMAGE_SOURCE_KINDS contains duplicates"
    assert damage_types.DAMAGE_SOURCE_DIRECT_ABILITY == "direct_ability_damage"
    assert damage_types.DAMAGE_SOURCE_DIRECT_DOT_APPLICATION == "direct_dot_application"
    assert damage_types.DAMAGE_SOURCE_DOT_TICK == "dot_tick"
    assert damage_types.DAMAGE_SOURCE_ON_HIT_PROC == "on_hit_proc_damage"
    assert damage_types.DAMAGE_SOURCE_STRIKE_AGAIN == "strike_again_damage"
    assert damage_types.DAMAGE_SOURCE_PET == "pet_damage"
    assert damage_types.DAMAGE_SOURCE_REFLECT == "reflect_damage"
    assert damage_types.DAMAGE_SOURCE_ABSORB_EXPLOSION == "absorb_explosion_damage"
    assert damage_types.DAMAGE_SOURCE_SELF == "self_damage"
    assert damage_types.DAMAGE_SOURCE_ENVIRONMENTAL == "environmental_damage"


def test_source_kind_helpers() -> None:
    for kind in damage_types.ALL_DAMAGE_SOURCE_KINDS:
        assert damage_types.is_damage_source_kind(kind), f"{kind} should be a valid source kind"
        assert damage_types.normalize_damage_source_kind(kind) == kind

    assert not damage_types.is_damage_source_kind(None)
    assert not damage_types.is_damage_source_kind("")
    assert not damage_types.is_damage_source_kind("direct_ability")
    assert not damage_types.is_damage_source_kind(" dot_tick ")

    assert damage_types.normalize_damage_source_kind(" DoT_Tick ") == "dot_tick"
    assert damage_types.normalize_damage_source_kind(None) is None
    assert damage_types.normalize_damage_source_kind("bogus_kind") is None
    assert (
        damage_types.normalize_damage_source_kind("bogus_kind", default="on_hit_proc_damage")
        == "on_hit_proc_damage"
    )
    assert damage_types.normalize_damage_source_kind(None, default="dot_tick") == "dot_tick"


def test_queued_on_hit_proc_events_carry_on_hit_proc_source_kind() -> None:
    match = make_match("hunter", "warrior", seed=6101)
    hunter_sid, warrior_sid = match.players
    hunter = match.state[hunter_sid]
    warrior = match.state[warrior_sid]

    hunter.effects.append(
        {
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
    )
    hunter.effects.append(
        {
            "type": "item_passive",
            "source_item": "Void Blade",
            "passive": {
                "type": "void_blade",
                "trigger": "on_hit",
                "int_multiplier": 0.4,
                "dice": "d4",
                "school": "magical",
                "subschool": "shadow",
            },
        }
    )
    _, _, _, damage_events = effects.trigger_on_hit_passives(
        hunter,
        warrior,
        base_damage=10,
        damage_type="physical",
        rng=random.Random(42),
        ability=ABILITIES["overpower"],
        include_strike_again=False,
    )
    assert damage_events, "on-hit proc passives should queue damage events"
    for event in damage_events:
        assert event.get("raw_incoming") is not None, "queued proc events should carry raw damage"
        assert event.get("source_kind") == damage_types.DAMAGE_SOURCE_ON_HIT_PROC, (
            f"queued proc event should carry on_hit_proc_damage, got {event.get('source_kind')!r}"
        )


def test_strike_again_events_carry_strike_again_source_kind() -> None:
    match = make_match("warrior", "hunter", seed=6102)
    warrior_sid, hunter_sid = match.players
    warrior = match.state[warrior_sid]
    hunter = match.state[hunter_sid]

    warrior.effects.append(
        {
            "type": "item_passive",
            "source_item": "Twin Blades of Azzinoth",
            "passive": {"type": "strike_again", "trigger": "on_hit", "chance": 1.0, "multiplier": 0.5},
        }
    )
    bonus_damage, _, _, damage_events = effects.trigger_on_hit_passives(
        warrior,
        hunter,
        base_damage=10,
        damage_type="physical",
        rng=random.Random(7),
        include_strike_again=True,
        only_strike_again=True,
    )
    assert bonus_damage > 0, "strike-again should produce resolved bonus damage"
    assert damage_events, "strike-again should queue its bonus damage event"
    for event in damage_events:
        assert event.get("source_kind") == damage_types.DAMAGE_SOURCE_STRIKE_AGAIN, (
            f"strike-again event should carry strike_again_damage, got {event.get('source_kind')!r}"
        )


def test_dot_tick_sources_carry_dot_tick_source_kind() -> None:
    match = make_match("warlock", "warrior", seed=6103)
    warlock_sid, warrior_sid = match.players
    warrior = match.state[warrior_sid]

    effects.apply_effect_by_id(
        warrior,
        "corruption",
        overrides={"duration": 3, "tick_damage": 2, "source_sid": warlock_sid},
    )
    sources = effects.tick_dots(warrior, [], "Warrior")
    assert sources, "expected the applied DoT to emit a tick source"
    for source in sources:
        assert source.get("source_kind") == damage_types.DAMAGE_SOURCE_DOT_TICK, (
            f"DoT tick source should carry dot_tick, got {source.get('source_kind')!r}"
        )


def test_pet_dot_tick_sources_carry_dot_tick_source_kind() -> None:
    pet = PetState(
        id="p1_imp_1",
        template_id="imp",
        name="Imp",
        owner_sid="p1_sid",
        hp=20,
        hp_max=20,
        effects=[
            {
                "id": "burn",
                "name": "Burn",
                "category": "dot",
                "tick_damage": 2,
                "school": "magical",
                "subschool": "fire",
                "source_sid": "p2_sid",
                "duration": 2,
            }
        ],
    )
    summary = effects.end_of_turn_pet(pet, [], "Imp")
    sources = summary.get("damage_sources", [])
    assert sources, "expected the pet DoT to emit a tick source"
    for source in sources:
        assert source.get("source_kind") == damage_types.DAMAGE_SOURCE_DOT_TICK


def test_mindgames_twisted_regen_is_tagged_self_damage() -> None:
    match = make_match("priest", "warrior", seed=6104)
    _, warrior_sid = match.players
    warrior = match.state[warrior_sid]

    effects.apply_effect_by_id(warrior, "mindgames")
    effects.apply_effect_by_id(warrior, "regrowth", overrides={"duration": 3, "regen": {"hp": 5}})
    _, pending_self_damage = effects.trigger_end_of_turn_effects(warrior, [], "Warrior")
    assert pending_self_damage, "Mindgames should twist regen into pending self-damage sources"
    for source in pending_self_damage:
        assert source.get("source_kind") == damage_types.DAMAGE_SOURCE_SELF, (
            f"Mindgames-twisted regen should carry self_damage, got {source.get('source_kind')!r}"
        )


def test_pet_damage_path_carries_pet_damage_source_kind() -> None:
    match = make_match("warlock", "warrior", seed=6105)
    owner_sid, enemy_sid = match.players
    owner = match.state[owner_sid]
    enemy = match.state[enemy_sid]
    imp = PetState(
        id="p1_imp_1",
        template_id="imp",
        name="Imp",
        owner_sid=owner_sid,
        hp=45,
        hp_max=45,
        mp=10,
        mp_max=10,
    )
    owner.pets[imp.id] = imp

    captured: List[Dict[str, Any]] = []

    def _capture_apply_damage(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        captured.append(dict(kwargs))
        return {"hp_damage": 1, "absorbed": 0, "absorbed_breakdown": []}

    result = PET_AI._resolve_pet_damage_and_log(
        owner=owner,
        enemy=enemy,
        pet=imp,
        owner_sid=owner_sid,
        enemy_sid=enemy_sid,
        label="Imp Firebolt",
        action_text="casts Firebolt",
        school="magical",
        subschool="fire",
        raw_damage=5,
        match=match,
        apply_damage=_capture_apply_damage,
        absorb_suffix=lambda absorbed, breakdown: "",
    )
    assert captured, "pet damage should route through apply_damage"
    assert captured[0].get("source_kind") == damage_types.DAMAGE_SOURCE_PET, (
        "pet damage should be applied with source_kind=pet_damage"
    )
    assert result.get("source_kind") == damage_types.DAMAGE_SOURCE_PET


def test_shield_of_vengeance_explosion_marked_absorb_explosion() -> None:
    resolver_source = _engine_source("resolver.py")
    block = _extract_function_block(resolver_source, "trigger_shield_of_vengeance_explosion")
    assert "DAMAGE_SOURCE_ABSORB_EXPLOSION" in block, (
        "Shield of Vengeance explosion must be tagged with DAMAGE_SOURCE_ABSORB_EXPLOSION"
    )


def test_resolver_wiring_uses_canonical_source_kind_markers() -> None:
    resolver_source = _engine_source("resolver.py")
    # Direct ability damage: resolve_action result dictionaries carry the kind.
    assert '"source_kind": DAMAGE_SOURCE_DIRECT_ABILITY' in resolver_source, (
        "resolve_action result dictionaries should be tagged as direct ability damage"
    )
    # Queued proc events keep the producer's kind and default to on-hit proc.
    # The default now lives inside the central factory, so validate both that
    # the resolver routes through the factory and that the factory itself
    # normalizes with the on-hit-proc default.
    queue_block = _extract_function_block(resolver_source, "queue_passive_damage_events")
    assert "make_queued_damage_event(" in queue_block, (
        "queued passive damage events should be built via "
        "damage_events.make_queued_damage_event()"
    )
    # NOTE: _extract_function_block is indentation-based and cannot span the
    # factories' multi-line signatures, so assert on the whole (small,
    # dedicated) module source instead.
    factory_source = _engine_source("damage_events.py")
    for factory_name in ("make_queued_damage_event", "make_passive_damage_event"):
        assert f"def {factory_name}(" in factory_source, (
            f"damage_events.py should define {factory_name}()"
        )
    normalize_calls = re.findall(
        r"normalize_damage_source_kind\(\s*source_kind\s*,\s*default=DAMAGE_SOURCE_ON_HIT_PROC\s*\)",
        factory_source,
    )
    assert len(normalize_calls) == 2, (
        "both damage-event factories should normalize source_kind with the "
        "on_hit_proc_damage default"
    )


def test_no_invalid_source_kind_literals_in_gameplay_files() -> None:
    literal_patterns = (
        re.compile(r"[\"']source_kind[\"']\s*:\s*[\"']([^\"']+)[\"']"),
        re.compile(r"source_kind\s*=\s*[\"']([^\"']+)[\"']"),
        re.compile(r"DAMAGE_SOURCE_[A-Z_]+\s*=\s*[\"']([^\"']+)[\"']"),
    )
    constant_pattern = re.compile(r"\b(DAMAGE_SOURCE_[A-Z_]+)\b")
    valid_kinds = set(damage_types.ALL_DAMAGE_SOURCE_KINDS)

    for basename in ("resolver.py", "effects.py", "pet_ai.py", "damage_types.py", "damage_events.py"):
        source = _engine_source(basename)
        for pattern in literal_patterns:
            for value in pattern.findall(source):
                assert value in valid_kinds, f"{basename} carries invalid source_kind literal {value!r}"
        for constant_name in constant_pattern.findall(source):
            assert hasattr(damage_types, constant_name), (
                f"{basename} references undefined source-kind constant {constant_name}"
            )


def run_all() -> list[tuple[str, bool, str]]:
    tests = [
        test_taxonomy_constants_are_canonical,
        test_source_kind_helpers,
        test_queued_on_hit_proc_events_carry_on_hit_proc_source_kind,
        test_strike_again_events_carry_strike_again_source_kind,
        test_dot_tick_sources_carry_dot_tick_source_kind,
        test_pet_dot_tick_sources_carry_dot_tick_source_kind,
        test_mindgames_twisted_regen_is_tagged_self_damage,
        test_pet_damage_path_carries_pet_damage_source_kind,
        test_shield_of_vengeance_explosion_marked_absorb_explosion,
        test_resolver_wiring_uses_canonical_source_kind_markers,
        test_no_invalid_source_kind_literals_in_gameplay_files,
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
    results = run_all()
    failures = [entry for entry in results if not entry[1]]
    for name, ok, reason in results:
        print(f"PASS: {name}" if ok else f"FAIL: {name} -> {reason}")
    if failures:
        sys.exit(1)
