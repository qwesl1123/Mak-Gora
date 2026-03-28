"""Dedicated subschool validation suite for metadata/content/pipeline guardrails."""

from __future__ import annotations

import random
import sys
from typing import Any, Dict

from regression_suite import (  # type: ignore
    ABILITIES,
    EFFECT_TEMPLATES,
    PETS,
    submit_turn,
    make_match,
    effects,
)

ITEMS = sys.modules["games.duel.content.items"].ITEMS


_ALLOWED_SUBSCHOOLS = {"fire", "frost", "nature", "arcane", "shadow", "holy"}
_BLINK_LIKE_OR_MOVEMENT = {"blink", "teleport", "demonic_gateway", "disengage"}
_GENERIC_NON_MAGICAL_EFFECTS = {"stunned", "blink", "disengage", "evasion", "ignore_pain", "shielded"}


def _iter_item_passives() -> list[tuple[str, Dict[str, Any]]]:
    out: list[tuple[str, Dict[str, Any]]] = []
    for item_id, item in ITEMS.items():
        passive = item.get("passive")
        if not passive:
            continue
        if isinstance(passive, list):
            for entry in passive:
                out.append((item_id, entry))
        else:
            out.append((item_id, passive))
    return out


def test_content_subschool_invariants() -> None:
    for ability_id, ability in ABILITIES.items():
        school = ability.get("school")
        subschool = ability.get("subschool")
        if subschool is not None:
            assert school == "magical", f"{ability_id} has subschool but school={school}"
            assert subschool in _ALLOWED_SUBSCHOOLS, f"{ability_id} has unknown subschool={subschool}"
        if school == "physical":
            assert "subschool" not in ability, f"physical ability {ability_id} must not carry subschool"

    for effect_id, effect in EFFECT_TEMPLATES.items():
        school = effect.get("school")
        subschool = effect.get("subschool")
        if subschool is not None:
            assert school == "magical", f"effect {effect_id} has subschool but school={school}"
            assert subschool in _ALLOWED_SUBSCHOOLS, f"effect {effect_id} has unknown subschool={subschool}"

    for pet_id, pet in PETS.items():
        school = pet.get("school")
        subschool = pet.get("subschool")
        if subschool is not None:
            assert school == "magical", f"pet {pet_id} has subschool but school={school}"
        if school == "physical":
            assert "subschool" not in pet, f"physical pet {pet_id} must not carry subschool"
        for special_id, special in (pet.get("specials") or {}).items():
            spec_school = special.get("school")
            spec_subschool = special.get("subschool")
            if spec_subschool is not None:
                assert spec_school == "magical", f"pet special {pet_id}.{special_id} has subschool without magical school"

    for item_id, passive in _iter_item_passives():
        school = passive.get("school")
        subschool = passive.get("subschool")
        if subschool is not None:
            assert school == "magical", f"item passive {item_id} has subschool but school={school}"


def test_guardrail_blink_like_and_generic_effects_have_no_subschool() -> None:
    for ability_id in _BLINK_LIKE_OR_MOVEMENT:
        ability = ABILITIES[ability_id]
        assert "subschool" not in ability, f"{ability_id} should remain subschool-free"

    for effect_id in _GENERIC_NON_MAGICAL_EFFECTS:
        effect = EFFECT_TEMPLATES[effect_id]
        assert "subschool" not in effect, f"{effect_id} should remain subschool-free"


def test_expected_magical_identities_keep_subschools() -> None:
    expected_abilities = {
        "fireball": "fire",
        "ring_of_ice": "frost",
        "fear": "shadow",
        "judgment": "holy",
    }
    for ability_id, subschool in expected_abilities.items():
        ability = ABILITIES[ability_id]
        assert ability.get("school") == "magical"
        assert ability.get("subschool") == subschool

    expected_effects = {
        "vampiric_touch": "shadow",
        "power_word_shield": "holy",
        "ring_of_ice_freeze": "frost",
        "avenging_wrath": "holy",
    }
    for effect_id, subschool in expected_effects.items():
        effect = EFFECT_TEMPLATES[effect_id]
        assert effect.get("school") == "magical"
        assert effect.get("subschool") == subschool

    assert PETS["imp"].get("subschool") == "fire"
    assert PETS["emerald_serpent"].get("subschool") == "nature"


def test_dot_application_and_refresh_preserve_school_and_subschool() -> None:
    match = make_match("hunter", "warrior", seed=5010)
    _, warrior_sid = match.players

    submit_turn(match, "wildfire_bomb", "pass_turn")
    warrior = match.state[warrior_sid]
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None
    assert burn.get("school") == "magical"
    assert burn.get("subschool") == "fire"

    # Refresh path should not drop metadata.
    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=3,
        tick_damage=int(burn.get("tick_damage", 1) or 1) + 1,
        source_sid=match.players[0],
    )
    assert refreshed, "Wildfire Burn should refresh when present"
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None
    assert burn.get("school") == "magical"
    assert burn.get("subschool") == "fire"


def test_dot_ticks_emit_stored_school_subschool_metadata() -> None:
    match = make_match("hunter", "warrior", seed=5011)
    _, warrior_sid = match.players
    warrior = match.state[warrior_sid]

    effects.apply_effect_by_id(
        warrior,
        "wildfire_burn",
        overrides={"duration": 2, "tick_damage": 3, "source_sid": "p1_sid", "school": "magical", "subschool": "fire"},
    )
    summary = effects.end_of_turn(warrior, [], "Warrior")
    sources = summary.get("damage_sources", [])
    assert sources, "Expected wildfire burn to emit a damage source"
    assert any(src.get("school") == "magical" and src.get("subschool") == "fire" for src in sources)


def test_magical_pet_and_item_damage_metadata() -> None:
    assert PETS["imp"].get("school") == "magical" and PETS["imp"].get("subschool") == "fire"
    assert PETS["emerald_serpent"]["specials"]["lightning_breath"].get("subschool") == "nature"

    match = make_match("hunter", "warrior", p1_items={"weapon": "thunderfury"}, seed=5012)
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
    _, _, _, damage_events = effects.trigger_on_hit_passives(
        hunter,
        warrior,
        base_damage=10,
        damage_type="physical",
        rng=random.Random(42),
        ability=ABILITIES["overpower"],
        include_strike_again=False,
    )
    assert any(evt.get("school") == "magical" and evt.get("subschool") == "nature" for evt in damage_events)


def test_effect_override_injection_keeps_explicit_subschool() -> None:
    effect = effects.build_effect("power_word_shield", overrides={"school": "magical", "subschool": "holy"})
    assert effect.get("school") == "magical"
    assert effect.get("subschool") == "holy"


def run_all() -> list[tuple[str, bool, str]]:
    tests = [
        test_content_subschool_invariants,
        test_guardrail_blink_like_and_generic_effects_have_no_subschool,
        test_expected_magical_identities_keep_subschools,
        test_dot_application_and_refresh_preserve_school_and_subschool,
        test_dot_ticks_emit_stored_school_subschool_metadata,
        test_magical_pet_and_item_damage_metadata,
        test_effect_override_injection_keeps_explicit_subschool,
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
