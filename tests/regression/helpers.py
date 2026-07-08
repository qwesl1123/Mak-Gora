"""Shared scenario-level helpers for the regression domain modules.

Moved verbatim from tests/regression_suite.py.
"""
from __future__ import annotations

from harness import (
    PETS,
    PetState,
    effects,
    make_match,
    submit_turn,
)


_DEF_PASS = "pass_turn"


def _add_pet(owner, pet_id: str, template_id: str = "imp") -> None:
    template = PETS[template_id]
    resources = template.get("resources", {}) or {}
    hp = int(resources.get("hp", template.get("hp", 1)) or 1)
    mp_max = int(resources.get("mp", template.get("mana", 0)) or 0)
    energy_max = int(resources.get("energy", 0) or 0)
    rage_max = int(resources.get("rage", 0) or 0)
    owner.pets[pet_id] = PetState(
        id=pet_id,
        template_id=template_id,
        name=str(template.get("name", template_id.title())),
        owner_sid=owner.sid,
        hp=hp,
        hp_max=hp,
        mp=mp_max,
        mp_max=mp_max,
        energy=energy_max,
        energy_max=energy_max,
        rage=0,
        rage_max=rage_max,
        stats={k: int(v or 0) for k, v in ((template.get("stats", {}) or {}).items())},
        effects=[],
        duration=None,
        entity_type=template.get("entity_type"),
    )


def _pet_took_damage_or_died(owner, pet_id: str, hp_before: int) -> bool:
    pet = owner.pets.get(pet_id)
    return pet is None or pet.hp < hp_before


def _setup_imps(match, owner_idx: int = 0):
    owner_sid = match.players[owner_idx]
    other_sid = match.players[1 - owner_idx]
    for _ in range(3):
        match.state[owner_sid].cooldowns["summon_imp"] = []
        if owner_idx == 0:
            submit_turn(match, "summon_imp", _DEF_PASS)
        else:
            submit_turn(match, _DEF_PASS, "summon_imp")
    return owner_sid, other_sid


def _active_pet(owner, template_id: str | None = None):
    pets = sorted((owner.pets or {}).values(), key=lambda pet: pet.id)
    if template_id is None:
        return pets[0] if pets else None
    for pet in pets:
        if pet.template_id == template_id:
            return pet
    return None


def _expected_mitigated(raw: int, effective_stat: int) -> int:
    return int(raw * (40 / (max(0, effective_stat) + 40)))


def _redirected_boar_damage_taken(enemy_class: str, enemy_action: str, *, boar_def: int, boar_magic_resist: int, seed: int) -> int:
    original_boar_special_chance = PETS["barrens_boar"]["special_chance"]
    PETS["barrens_boar"]["special_chance"] = 1.0
    try:
        match = make_match("hunter", enemy_class, seed=seed)
        submit_turn(match, "call_boar", _DEF_PASS)
        hunter_sid, enemy_sid = match.players
        hunter = match.state[hunter_sid]
        boar = _active_pet(hunter, "barrens_boar")
        assert boar is not None, "Barrens Boar should be active for redirect mitigation test"
        boar.stats["def"] = boar_def
        boar.stats["magic_resist"] = boar_magic_resist
        hp_before = boar.hp
        submit_turn(match, _DEF_PASS, enemy_action)
        return hp_before - boar.hp
    finally:
        PETS["barrens_boar"]["special_chance"] = original_boar_special_chance
