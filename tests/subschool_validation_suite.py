"""Dedicated subschool validation suite for metadata/content/pipeline guardrails."""

from __future__ import annotations

import random
import sys
from typing import Any, Dict

from regression_suite import (  # type: ignore
    ABILITIES,
    EFFECT_TEMPLATES,
    PETS,
    PET_AI,
    PetState,
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


def test_refresh_dot_effect_updates_and_preserves_metadata() -> None:
    match = make_match("hunter", "warrior", seed=5013)
    _, warrior_sid = match.players
    warrior = match.state[warrior_sid]
    effects.apply_effect_by_id(
        warrior,
        "wildfire_burn",
        overrides={"duration": 2, "tick_damage": 3, "source_sid": "p1_sid", "school": "magical", "subschool": "fire"},
    )

    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=3,
        tick_damage=4,
        source_sid="p2_sid",
        school="magical",
        subschool="shadow",
    )
    assert refreshed
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None
    assert burn.get("source_sid") == "p2_sid"
    assert burn.get("school") == "magical"
    assert burn.get("subschool") == "shadow"

    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=2,
        tick_damage=5,
        source_sid="p1_sid",
    )
    assert refreshed
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None
    assert burn.get("school") == "magical"
    assert burn.get("subschool") == "shadow"

    sources = effects.tick_dots(warrior, [], "Warrior")
    assert any(src.get("school") == "magical" and src.get("subschool") == "shadow" for src in sources)


def test_refresh_dot_effect_clears_subschool_when_school_becomes_physical() -> None:
    match = make_match("hunter", "warrior", seed=5017)
    _, warrior_sid = match.players
    warrior = match.state[warrior_sid]
    effects.apply_effect_by_id(
        warrior,
        "wildfire_burn",
        overrides={"duration": 2, "tick_damage": 3, "source_sid": "p1_sid", "school": "magical", "subschool": "fire"},
    )

    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=2,
        tick_damage=3,
        source_sid="p1_sid",
        school="physical",
    )
    assert refreshed
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None
    assert burn.get("school") == "physical"
    assert burn.get("subschool") is None

    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=2,
        tick_damage=3,
        source_sid="p1_sid",
        school="magical",
    )
    assert refreshed
    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=2,
        tick_damage=3,
        source_sid="p1_sid",
        subschool="fire",
    )
    assert refreshed
    refreshed = effects.refresh_dot_effect(
        warrior,
        "wildfire_burn",
        duration=2,
        tick_damage=3,
        source_sid="p1_sid",
        school="magical",
    )
    assert refreshed
    burn = next((fx for fx in warrior.effects if fx.get("id") == "wildfire_burn"), None)
    assert burn is not None
    assert burn.get("school") == "magical"
    assert burn.get("subschool") == "fire"


def test_legacy_damage_type_normalizes_school_for_triggered_damage_events() -> None:
    match = make_match("mage", "warrior", seed=5014)
    mage_sid, warrior_sid = match.players
    mage = match.state[mage_sid]
    warrior = match.state[warrior_sid]

    mage.effects.append(
        {
            "type": "item_passive",
            "source_item": "Mirror Charm",
            "passive": {"type": "duplicate_offensive_spell", "trigger": "on_hit", "chance": 1.0},
        }
    )
    _, _, _, damage_events = effects.trigger_on_hit_passives(
        mage,
        warrior,
        base_damage=10,
        damage_type="physical",
        rng=random.Random(9),
        ability={
            "name": "Legacy Spell",
            "tags": ["attack", "spell"],
            "damage_type": "magic",
            "subschool": "fire",
            "scaling": {"int": 1.0},
            "dice": {"type": "d4"},
        },
        include_strike_again=False,
    )
    assert damage_events, "duplicate_offensive_spell should emit runtime damage events"
    assert all(evt.get("school") == "magical" for evt in damage_events)
    assert all(evt.get("subschool") == "fire" for evt in damage_events)


def test_physical_runtime_payload_does_not_carry_bogus_subschool() -> None:
    match = make_match("warrior", "warrior", seed=5015)
    p1_sid, p2_sid = match.players
    p1 = match.state[p1_sid]
    p2 = match.state[p2_sid]

    p1.effects.append(
        {
            "type": "item_passive",
            "source_item": "Echo Blade",
            "passive": {"type": "duplicate_offensive_spell", "trigger": "on_hit", "chance": 1.0},
        }
    )
    _, _, _, damage_events = effects.trigger_on_hit_passives(
        p1,
        p2,
        base_damage=12,
        damage_type="physical",
        rng=random.Random(11),
        ability={
            "name": "Legacy Slam",
            "tags": ["attack", "spell"],
            "damage_type": "physical",
            "subschool": "fire",
            "scaling": {"atk": 1.0},
            "dice": {"type": "d4"},
        },
        include_strike_again=False,
    )
    assert damage_events, "duplicate_offensive_spell should emit runtime damage events"
    assert all(evt.get("school") == "physical" for evt in damage_events)
    assert all(evt.get("subschool") is None for evt in damage_events)


def test_pet_magical_damage_carries_school_and_subschool() -> None:
    match = make_match("warlock", "warrior", seed=5016)
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
        effects=[],
        duration=None,
    )
    owner.pets[imp.id] = imp

    captured: list[dict[str, Any]] = []

    def _capture_apply_damage(*args, **kwargs):
        captured.append({"school": kwargs.get("school"), "subschool": kwargs.get("subschool")})
        return {"hp_damage": 1, "absorbed": 0, "absorbed_breakdown": []}

    PET_AI._run_imp_firebolt(
        owner,
        enemy,
        imp,
        owner_sid,
        enemy_sid,
        match,
        random.Random(13),
        _capture_apply_damage,
        lambda absorbed, breakdown: "",
        {},
        lambda *_args, **_kwargs: False,
        lambda _target: "Target is untargetable",
        lambda *_args, **_kwargs: False,
    )
    assert captured, "Imp firebolt should call into apply_damage"
    assert captured[0]["school"] == "magical"
    assert captured[0]["subschool"] == "fire"


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
        test_refresh_dot_effect_updates_and_preserves_metadata,
        test_refresh_dot_effect_clears_subschool_when_school_becomes_physical,
        test_dot_ticks_emit_stored_school_subschool_metadata,
        test_legacy_damage_type_normalizes_school_for_triggered_damage_events,
        test_physical_runtime_payload_does_not_carry_bogus_subschool,
        test_pet_magical_damage_carries_school_and_subschool,
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
