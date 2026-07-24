"""Regression scenarios for the global periodic equipped-item stage."""
from __future__ import annotations

import copy
import random
import re

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

from harness import (
    PetState,
    effects,
    make_match,
    resolver,
    submit_turn,
)

from games.duel.content.items import ITEMS
from games.duel.engine import damage_types, periodic_items

from .helpers import _DEF_PASS


_MISSING = object()


def _periodic_passive(
    passive_type: str,
    *,
    interval: int = 1,
    first_trigger_turn: int = 1,
) -> dict[str, Any]:
    return {
        "type": passive_type,
        "trigger": periodic_items.PERIODIC_ITEM_TRIGGER,
        "interval": interval,
        "first_trigger_turn": first_trigger_turn,
    }


def _synthetic_item(
    name: str,
    slot: str,
    passive: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "name": name,
        "slot": slot,
        "passive": passive,
    }


@contextmanager
def _temporary_periodic_content(
    item_definitions: dict[str, dict[str, Any]],
    handlers: dict[str, Callable[..., None]],
) -> Iterator[None]:
    original_items = {
        item_id: ITEMS.get(item_id, _MISSING)
        for item_id in item_definitions
    }
    original_handlers = {
        handler_type: periodic_items.PERIODIC_ITEM_HANDLERS.get(
            handler_type,
            _MISSING,
        )
        for handler_type in handlers
    }
    ITEMS.update(item_definitions)
    periodic_items.PERIODIC_ITEM_HANDLERS.update(handlers)
    try:
        yield
    finally:
        for item_id, original in original_items.items():
            if original is _MISSING:
                ITEMS.pop(item_id, None)
            else:
                ITEMS[item_id] = original
        for handler_type, original in original_handlers.items():
            if original is _MISSING:
                periodic_items.PERIODIC_ITEM_HANDLERS.pop(handler_type, None)
            else:
                periodic_items.PERIODIC_ITEM_HANDLERS[handler_type] = original


def _unused_apply_damage(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise AssertionError("The synthetic stage test did not expect damage application")


@contextmanager
def _fixed_vial_roll(value: int, calls: list[str]) -> Iterator[None]:
    original_roll = periodic_items.roll

    def fixed_roll(dice: str, rng: Any) -> int:
        calls.append(dice)
        assert dice == "d6", "Vial of Shadows must roll exactly a d6"
        return value

    periodic_items.roll = fixed_roll
    try:
        yield
    finally:
        periodic_items.roll = original_roll


def _add_periodic_test_entity(
    owner: Any,
    entity_id: str,
    *,
    name: str = "Imp",
    hp: int = 50,
    entity_type: str = "pet",
    stats: dict[str, int] | None = None,
) -> PetState:
    entity = PetState(
        id=entity_id,
        template_id="test_periodic_entity",
        name=name,
        owner_sid=owner.sid,
        hp=hp,
        hp_max=max(1, hp),
        stats=dict(stats or {}),
        entity_type=entity_type,
    )
    owner.pets[entity_id] = entity
    return entity


def _capture_damage_calls(
    captured: list[dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    def capture(
        source: Any,
        target: Any,
        incoming: int,
        target_sid: str,
        source_name: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.append(
            {
                "source": source,
                "target": target,
                "incoming": incoming,
                "target_sid": target_sid,
                "source_name": source_name,
                "kwargs": dict(kwargs),
            }
        )
        return {
            "hp_damage": incoming,
            "absorbed": 0,
            "absorbed_breakdown": [],
            "instances": [
                {
                    "hp_damage": incoming,
                    "absorbed": 0,
                    "absorbed_breakdown": [],
                }
            ],
            "mindgames_healing": 0,
            "mindgames_healing_gained": 0,
            "source_kind": kwargs.get("source_kind"),
        }

    return capture


def scenario_periodic_item_empty_stage_is_true_noop() -> bool:
    match = make_match("warrior", "mage", seed=9401)
    state_before = copy.deepcopy(match.state)
    totals_before = copy.deepcopy(match.combat_totals)
    log_before = list(match.log)
    phase_before = match.phase
    winner_before = match.winner
    turn_before = match.turn
    submitted_before = copy.deepcopy(match.submitted)

    stage_rng = random.Random(9401)
    control_rng = random.Random(9401)
    activations = periodic_items.resolve_periodic_item_stage(
        match=match,
        rng=stage_rng,
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )

    assert activations == (), "An empty periodic-item stage should collect no activations"
    assert match.state == state_before, "An empty stage must not mutate player or pet state"
    assert match.combat_totals == totals_before, "An empty stage must not mutate combat totals"
    assert match.log == log_before, "An empty stage must not append logs"
    assert match.phase == phase_before and match.winner == winner_before, \
        "An empty stage must not affect winner state"
    assert match.turn == turn_before and match.submitted == submitted_before, \
        "An empty stage must not change turn-resolution state"
    assert stage_rng.getstate() == control_rng.getstate(), \
        "An empty stage must consume no RNG values"
    assert stage_rng.random() == control_rng.random(), \
        "The next deterministic RNG result must remain identical after an empty stage"
    return True


def scenario_periodic_item_stage_runs_once_per_global_turn() -> bool:
    handler_type = "test_periodic_once"
    item_id = "test_periodic_once_weapon"
    calls: list[tuple[str, int]] = []

    def record_once(activation, context) -> None:
        calls.append((activation.owner_sid, context.global_turn))

    item_definitions = {
        item_id: _synthetic_item(
            "Synthetic Once Weapon",
            "weapon",
            _periodic_passive(handler_type),
        )
    }
    with _temporary_periodic_content(item_definitions, {handler_type: record_once}):
        match = make_match(
            "warrior",
            "mage",
            p1_items={"weapon": item_id},
            seed=9402,
        )
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        assert calls == [(match.players[0], 1)], \
            "One eligible equipped passive must dispatch exactly once on the completed global turn"
    return True


def scenario_periodic_item_schedule_boundaries_use_global_turn() -> bool:
    handler_type = "test_periodic_schedule"
    item_id = "test_periodic_schedule_trinket"
    triggered_turns: list[int] = []

    def record_turn(activation, context) -> None:
        triggered_turns.append(context.global_turn)

    item_definitions = {
        item_id: _synthetic_item(
            "Synthetic Schedule Trinket",
            "trinket",
            _periodic_passive(
                handler_type,
                interval=5,
                first_trigger_turn=5,
            ),
        )
    }
    with _temporary_periodic_content(item_definitions, {handler_type: record_turn}):
        match = make_match(
            "warrior",
            "mage",
            p1_items={"trinket": item_id},
            seed=9403,
        )
        for global_turn in range(1, 11):
            submit_turn(match, _DEF_PASS, _DEF_PASS)
            expected = [5] if 5 <= global_turn < 10 else [5, 10] if global_turn == 10 else []
            assert triggered_turns == expected, \
                f"interval=5 and first_trigger_turn=5 scheduled incorrectly on global turn {global_turn}"
        assert match.turn == 10, "Schedule coverage must advance the canonical global match turn"
    return True


def scenario_periodic_item_activations_snapshot_before_dispatch() -> bool:
    handler_type = "test_periodic_snapshot"
    item_id = "test_periodic_snapshot_armor"
    dispatch_order: list[str] = []
    committed_sids: tuple[str, str] | None = None

    def mutate_then_record(activation, context) -> None:
        dispatch_order.append(activation.owner_sid)
        assert committed_sids is not None
        if activation.owner_sid == committed_sids[0]:
            context.match.state[committed_sids[1]].res.hp = 0
            context.match.state[committed_sids[1]].build.items["armor"] = None

    item_definitions = {
        item_id: _synthetic_item(
            "Synthetic Snapshot Armor",
            "armor",
            _periodic_passive(handler_type),
        )
    }
    with _temporary_periodic_content(item_definitions, {handler_type: mutate_then_record}):
        match = make_match(
            "warrior",
            "mage",
            p1_items={"armor": item_id},
            p2_items={"armor": item_id},
            seed=9404,
        )
        committed_sids = (match.players[0], match.players[1])
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        assert dispatch_order == list(committed_sids), \
            "The second committed activation must execute after the first removes its equipment and makes it non-positive"
        assert match.phase == "ended" and match.winner == committed_sids[0], \
            "Winner evaluation should observe the post-snapshot handler mutations"
    return True


def scenario_periodic_item_activation_order_is_deterministic() -> bool:
    recorded: list[tuple[str, str, str, int, str]] = []
    handler_types = {
        "test_order_weapon_0",
        "test_order_weapon_2",
        "test_order_armor",
        "test_order_trinket",
        "test_order_p2_weapon",
        "test_order_p2_armor_0",
        "test_order_p2_armor_1",
        "test_order_p2_trinket",
    }

    def record_activation(activation, context) -> None:
        assert context.player_sids == tuple(context.match.players), \
            "Handler context must expose canonical match player order"
        recorded.append(
            (
                activation.owner_sid,
                activation.item_slot,
                activation.item_id,
                activation.passive_index,
                activation.passive_type,
            )
        )

    item_definitions = {
        "test_order_p1_weapon": _synthetic_item(
            "Synthetic Ordered P1 Weapon",
            "weapon",
            [
                _periodic_passive("test_order_weapon_0"),
                {"type": "ordinary_on_hit", "trigger": "on_hit"},
                _periodic_passive("test_order_weapon_2"),
            ],
        ),
        "test_order_p1_armor": _synthetic_item(
            "Synthetic Ordered P1 Armor",
            "armor",
            _periodic_passive("test_order_armor"),
        ),
        "test_order_p1_trinket": _synthetic_item(
            "Synthetic Ordered P1 Trinket",
            "trinket",
            _periodic_passive("test_order_trinket"),
        ),
        "test_order_p2_weapon": _synthetic_item(
            "Synthetic Ordered P2 Weapon",
            "weapon",
            _periodic_passive("test_order_p2_weapon"),
        ),
        "test_order_p2_armor": _synthetic_item(
            "Synthetic Ordered P2 Armor",
            "armor",
            [
                _periodic_passive("test_order_p2_armor_0"),
                _periodic_passive("test_order_p2_armor_1"),
            ],
        ),
        "test_order_p2_trinket": _synthetic_item(
            "Synthetic Ordered P2 Trinket",
            "trinket",
            _periodic_passive("test_order_p2_trinket"),
        ),
    }
    handlers = {handler_type: record_activation for handler_type in handler_types}
    with _temporary_periodic_content(item_definitions, handlers):
        match = make_match(
            "warrior",
            "mage",
            p1_items={
                "weapon": "test_order_p1_weapon",
                "armor": "test_order_p1_armor",
                "trinket": "test_order_p1_trinket",
            },
            p2_items={
                "weapon": "test_order_p2_weapon",
                "armor": "test_order_p2_armor",
                "trinket": "test_order_p2_trinket",
            },
            seed=9405,
        )
        p1_sid, p2_sid = match.players
        match.state = {
            p2_sid: match.state[p2_sid],
            p1_sid: match.state[p1_sid],
        }
        match.state[p1_sid].build.items = {
            "trinket": "test_order_p1_trinket",
            "armor": "test_order_p1_armor",
            "weapon": "test_order_p1_weapon",
        }
        match.state[p2_sid].build.items = {
            "armor": "test_order_p2_armor",
            "trinket": "test_order_p2_trinket",
            "weapon": "test_order_p2_weapon",
        }
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        expected = [
            (p1_sid, "weapon", "test_order_p1_weapon", 0, "test_order_weapon_0"),
            (p1_sid, "weapon", "test_order_p1_weapon", 2, "test_order_weapon_2"),
            (p1_sid, "armor", "test_order_p1_armor", 0, "test_order_armor"),
            (p1_sid, "trinket", "test_order_p1_trinket", 0, "test_order_trinket"),
            (p2_sid, "weapon", "test_order_p2_weapon", 0, "test_order_p2_weapon"),
            (p2_sid, "armor", "test_order_p2_armor", 0, "test_order_p2_armor_0"),
            (p2_sid, "armor", "test_order_p2_armor", 1, "test_order_p2_armor_1"),
            (p2_sid, "trinket", "test_order_p2_trinket", 0, "test_order_p2_trinket"),
        ]
        assert recorded == expected, \
            "Dispatch must follow match player order, centralized equipment-slot order, then passive index"
    return True


def scenario_periodic_item_passive_dict_and_list_support() -> bool:
    handler_type = "test_periodic_shape"
    recorded: list[tuple[str, int]] = []

    def record_shape(activation, context) -> None:
        recorded.append((activation.item_id, activation.passive_index))

    item_definitions = {
        "test_periodic_dict_weapon": _synthetic_item(
            "Synthetic Dictionary Weapon",
            "weapon",
            _periodic_passive(handler_type),
        ),
        "test_periodic_list_armor": _synthetic_item(
            "Synthetic List Armor",
            "armor",
            [
                {"type": "ordinary_passive", "trigger": "on_hit"},
                _periodic_passive(handler_type),
                _periodic_passive(
                    handler_type,
                    interval=5,
                    first_trigger_turn=5,
                ),
            ],
        ),
    }
    with _temporary_periodic_content(item_definitions, {handler_type: record_shape}):
        match = make_match(
            "warrior",
            "mage",
            p1_items={
                "weapon": "test_periodic_dict_weapon",
                "armor": "test_periodic_list_armor",
            },
            seed=9406,
        )
        collected = periodic_items.collect_periodic_item_activations(
            match,
            current_turn=1,
        )
        source_passive = ITEMS["test_periodic_dict_weapon"]["passive"]
        source_passive["interval"] = 99
        try:
            assert collected[0].passive_metadata["interval"] == 1, \
                "Activation metadata must be copied instead of retaining mutable item-content references"
        finally:
            source_passive["interval"] = 1
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        assert recorded == [
            ("test_periodic_dict_weapon", 0),
            ("test_periodic_list_armor", 1),
        ], "Dictionary and list passives should work while non-periodic/ineligible entries stay inactive"
    return True


def scenario_periodic_item_stage_precedes_winner_evaluation() -> bool:
    handler_type = "test_periodic_rescue"
    item_id = "test_periodic_rescue_trinket"
    observed_pre_heal_hp: list[int] = []

    def rescue_owner(activation, context) -> None:
        owner = context.match.state[activation.owner_sid]
        observed_pre_heal_hp.append(owner.res.hp)
        effects.apply_player_healing(owner, 2)

    item_definitions = {
        item_id: _synthetic_item(
            "Synthetic Rescue Trinket",
            "trinket",
            _periodic_passive(handler_type),
        )
    }
    with _temporary_periodic_content(item_definitions, {handler_type: rescue_owner}):
        match = make_match(
            "priest",
            "warlock",
            p1_items={"trinket": item_id},
            seed=9407,
        )
        owner_sid, enemy_sid = match.players
        owner = match.state[owner_sid]
        owner.res.hp = 1
        effects.apply_effect_by_id(
            owner,
            "agony",
            overrides={
                "duration": 2,
                "tick_damage": 1,
                "source_sid": enemy_sid,
                "dot_mode": "fixed",
            },
        )
        submit_turn(match, _DEF_PASS, _DEF_PASS)
        assert observed_pre_heal_hp == [0], \
            "The periodic handler must observe normal end-of-turn lethal damage first"
        assert owner.res.hp == 2, "The synthetic handler should restore the non-positive owner"
        assert match.phase == "combat" and match.winner is None, \
            "Final alive/winner evaluation must observe the post-periodic-stage HP"
    return True


def scenario_periodic_item_stage_order_between_normal_processing_and_cleanup() -> bool:
    handler_type = "test_periodic_phase_probe"
    item_id = "test_periodic_phase_probe_weapon"
    handler_marker = "synthetic periodic phase marker"
    observations: list[tuple[int, bool, int]] = []

    def probe_phase(activation, context) -> None:
        owner = context.match.state[activation.owner_sid]
        hot = next(effect for effect in owner.effects if effect.get("id") == "test_normal_hot")
        observations.append(
            (
                owner.res.hp,
                "test_dead_pet" in owner.pets,
                int(hot.get("duration", 0) or 0),
            )
        )
        context.match.log.append(handler_marker)

    item_definitions = {
        item_id: _synthetic_item(
            "Synthetic Phase Probe Weapon",
            "weapon",
            _periodic_passive(handler_type),
        )
    }
    with _temporary_periodic_content(item_definitions, {handler_type: probe_phase}):
        match = make_match(
            "warrior",
            "mage",
            p1_items={"weapon": item_id},
            seed=9408,
        )
        owner_sid = match.players[0]
        enemy_sid = match.players[1]
        owner = match.state[owner_sid]
        owner.res.hp -= 5
        expected_stage_hp = owner.res.hp + 1
        owner.effects.append(
            {
                "id": "test_normal_hot",
                "name": "Synthetic Normal HoT",
                "duration": 2,
                "regen": {"hp": 2},
            }
        )
        effects.apply_effect_by_id(
            owner,
            "feared",
            overrides={"duration": 2},
        )
        effects.apply_effect_by_id(
            owner,
            "agony",
            overrides={
                "duration": 2,
                "tick_damage": 1,
                "source_sid": enemy_sid,
                "dot_mode": "fixed",
            },
        )
        owner.pets["test_dead_pet"] = PetState(
            id="test_dead_pet",
            template_id="imp",
            name="Synthetic Expiring Pet",
            owner_sid=owner_sid,
            hp=0,
            hp_max=10,
        )

        submit_turn(match, _DEF_PASS, _DEF_PASS)

        assert observations == [(expected_stage_hp, True, 2)], \
            "The stage must run after normal HoT processing but before duration and pet cleanup"
        assert "test_dead_pet" not in owner.pets, "Final pet cleanup must run after the periodic stage"
        remaining_hot = next(effect for effect in owner.effects if effect.get("id") == "test_normal_hot")
        assert remaining_hot.get("duration") == 1, \
            "Duration decrement/expiry cleanup must run after the periodic stage"
        normal_idx = next(
            index
            for index, line in enumerate(match.log)
            if "recovers 2 HP from Synthetic Normal HoT" in line
        )
        deferred_break_idx = next(
            index
            for index, line in enumerate(match.log)
            if "breaks on damage." in line and owner_sid[:5] in line
        )
        stage_idx = match.log.index(handler_marker)
        cleanup_idx = match.log.index("Synthetic Expiring Pet dies.")
        assert normal_idx < deferred_break_idx < stage_idx < cleanup_idx, \
            "Normal and deferred end-of-turn logs must precede periodic dispatch and final cleanup"
    return True


def scenario_periodic_item_invalid_metadata_and_unknown_handler_fail_clearly() -> bool:
    trigger = periodic_items.PERIODIC_ITEM_TRIGGER
    invalid_passives = {
        "test_periodic_interval_zero": (
            {"type": "test_bad", "trigger": trigger, "interval": 0, "first_trigger_turn": 1},
            "interval",
        ),
        "test_periodic_interval_negative": (
            {"type": "test_bad", "trigger": trigger, "interval": -2, "first_trigger_turn": 1},
            "interval",
        ),
        "test_periodic_interval_missing": (
            {"type": "test_bad", "trigger": trigger, "first_trigger_turn": 1},
            "interval",
        ),
        "test_periodic_interval_string": (
            {"type": "test_bad", "trigger": trigger, "interval": "5", "first_trigger_turn": 1},
            "interval",
        ),
        "test_periodic_first_missing": (
            {"type": "test_bad", "trigger": trigger, "interval": 1},
            "first_trigger_turn",
        ),
        "test_periodic_first_zero": (
            {"type": "test_bad", "trigger": trigger, "interval": 1, "first_trigger_turn": 0},
            "first_trigger_turn",
        ),
        "test_periodic_first_string": (
            {"type": "test_bad", "trigger": trigger, "interval": 1, "first_trigger_turn": "1"},
            "first_trigger_turn",
        ),
        "test_periodic_type_missing": (
            {"trigger": trigger, "interval": 1, "first_trigger_turn": 1},
            "type",
        ),
        "test_periodic_type_empty": (
            {"type": "  ", "trigger": trigger, "interval": 1, "first_trigger_turn": 1},
            "type",
        ),
    }
    item_definitions = {
        item_id: _synthetic_item(
            f"Synthetic Invalid {item_id}",
            "weapon",
            passive,
        )
        for item_id, (passive, _) in invalid_passives.items()
    }
    unknown_item_id = "test_periodic_unknown_handler_item"
    unknown_handler_type = "test_periodic_unknown_handler"
    item_definitions[unknown_item_id] = _synthetic_item(
        "Synthetic Unknown Handler Item",
        "weapon",
        _periodic_passive(unknown_handler_type),
    )

    with _temporary_periodic_content(item_definitions, {}):
        for item_id, (_, expected_field) in invalid_passives.items():
            match = make_match(
                "warrior",
                "mage",
                p1_items={"weapon": item_id},
                seed=9409,
            )
            try:
                periodic_items.collect_periodic_item_activations(match, current_turn=1)
            except ValueError as exc:
                message = str(exc)
                assert item_id in message and expected_field in message, \
                    f"Invalid periodic metadata should identify item {item_id} and field {expected_field}"
            else:
                raise AssertionError(f"Invalid periodic metadata for {item_id} should fail clearly")

        unknown = make_match(
            "warrior",
            "mage",
            p1_items={"weapon": unknown_item_id},
            seed=9410,
        )
        try:
            periodic_items.resolve_periodic_item_stage(
                match=unknown,
                rng=random.Random(9410),
                turn_context=None,
                apply_damage=_unused_apply_damage,
            )
        except ValueError as exc:
            message = str(exc)
            assert unknown_item_id in message and unknown_handler_type in message, \
                "Unknown handler failures must identify both the item ID and passive type"
        else:
            raise AssertionError("An unregistered periodic handler type must fail loudly")
    return True


def scenario_periodic_self_heal_item_metadata_and_handler_validation() -> bool:
    expected_values = {
        "spirit_light_sword": 3,
        "staff_of_immortality": 4,
    }
    for item_id, heal_value in expected_values.items():
        assert ITEMS[item_id]["passive"] == {
            "type": periodic_items.PERIODIC_SELF_HEAL_HANDLER,
            "trigger": periodic_items.PERIODIC_ITEM_TRIGGER,
            "interval": 1,
            "first_trigger_turn": 1,
            "value": heal_value,
            "target_mode": "self",
        }, f"{item_id} must use the complete periodic self-heal schedule"

    assert periodic_items.PERIODIC_SELF_HEAL_HANDLER == "periodic_self_heal"
    assert (
        periodic_items.PERIODIC_ITEM_HANDLERS[
            periodic_items.PERIODIC_SELF_HEAL_HANDLER
        ]
        is periodic_items.periodic_self_heal
    ), "periodic_self_heal must be registered through the shared handler registry"

    match = make_match("warrior", "mage", seed=9411)
    owner_sid = match.players[0]
    context = periodic_items.PeriodicItemHandlerContext(
        match=match,
        global_turn=1,
        rng=random.Random(9411),
        player_sids=tuple(match.players),
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )

    def activation(owner: str, passive: dict[str, Any]):
        return periodic_items.PeriodicItemActivation(
            owner_sid=owner,
            item_slot="weapon",
            item_id="spirit_light_sword",
            passive_type=periodic_items.PERIODIC_SELF_HEAL_HANDLER,
            passive_metadata=passive,
            passive_index=0,
        )

    valid_passive = dict(ITEMS["spirit_light_sword"]["passive"])
    try:
        periodic_items.periodic_self_heal(
            activation("missing-owner", valid_passive),
            context,
        )
    except ValueError as exc:
        assert "missing-owner" in str(exc), \
            "A missing periodic item owner must fail loudly with its SID"
    else:
        raise AssertionError("periodic_self_heal must reject a missing owner")

    invalid_passives = (
        ({**valid_passive, "target_mode": "enemy"}, "target_mode"),
        ({**valid_passive, "value": True}, "positive integer"),
        ({**valid_passive, "value": 0}, "positive integer"),
        ({**valid_passive, "value": -1}, "positive integer"),
        ({**valid_passive, "value": 3.0}, "positive integer"),
        ({**valid_passive, "value": "3"}, "positive integer"),
    )
    hp_before = match.state[owner_sid].res.hp
    totals_before = copy.deepcopy(match.combat_totals)
    log_before = list(match.log)
    for passive, expected_message in invalid_passives:
        try:
            periodic_items.periodic_self_heal(
                activation(owner_sid, passive),
                context,
            )
        except ValueError as exc:
            assert expected_message in str(exc), \
                f"Invalid periodic_self_heal metadata should identify {expected_message}"
        else:
            raise AssertionError(
                f"periodic_self_heal must reject invalid metadata: {passive}"
            )
    assert match.state[owner_sid].res.hp == hp_before
    assert match.combat_totals == totals_before
    assert match.log == log_before
    return True


def scenario_periodic_self_heal_schedule_and_rng() -> bool:
    for index, (item_id, heal_value) in enumerate(
        (("spirit_light_sword", 3), ("staff_of_immortality", 4))
    ):
        match = make_match(
            "warrior",
            "mage",
            p1_items={"weapon": item_id},
            seed=9420 + index,
        )
        owner_sid = match.players[0]
        owner = match.state[owner_sid]
        owner.res.hp -= 30
        hp_before = owner.res.hp
        stage_rng = random.Random(9420 + index)
        control_rng = random.Random(9420 + index)
        observed_turns: list[int] = []

        for global_turn in range(1, 5):
            match.turn = global_turn - 1
            activations = periodic_items.resolve_periodic_item_stage(
                match=match,
                rng=stage_rng,
                turn_context=None,
                apply_damage=_unused_apply_damage,
            )
            assert len(activations) == 1
            activation = activations[0]
            assert activation.item_id == item_id
            assert activation.passive_type == periodic_items.PERIODIC_SELF_HEAL_HANDLER
            observed_turns.append(global_turn)

        assert observed_turns == [1, 2, 3, 4], \
            f"{item_id} must activate once starting on turn 1 and every turn thereafter"
        assert owner.res.hp == hp_before + (heal_value * 4)
        assert match.combat_totals[owner_sid]["healing"] == heal_value * 4
        assert match.combat_totals[owner_sid]["overhealing"] == 0
        assert stage_rng.getstate() == control_rng.getstate(), \
            f"{item_id} periodic healing must consume no RNG"
    return True


def scenario_periodic_self_heal_accounting_and_exceptions() -> bool:
    near_cap = make_match(
        "warrior",
        "mage",
        p1_items={"weapon": "staff_of_immortality"},
        seed=9430,
    )
    near_cap_sid = near_cap.players[0]
    near_cap_owner = near_cap.state[near_cap_sid]
    near_cap_owner.res.hp = near_cap_owner.res.hp_max - 1
    periodic_items.resolve_periodic_item_stage(
        match=near_cap,
        rng=random.Random(9430),
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )
    assert near_cap_owner.res.hp == near_cap_owner.res.hp_max
    assert near_cap.combat_totals[near_cap_sid]["healing"] == 1
    assert near_cap.combat_totals[near_cap_sid]["overhealing"] == 3
    assert near_cap.log[-1] == (
        f"{near_cap_sid[:5]} heals 1 HP from Staff of Immortality."
    )

    full = make_match(
        "warrior",
        "mage",
        p1_items={"weapon": "spirit_light_sword"},
        seed=9431,
    )
    full_sid = full.players[0]
    periodic_items.resolve_periodic_item_stage(
        match=full,
        rng=random.Random(9431),
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )
    assert full.combat_totals[full_sid]["healing"] == 0
    assert full.combat_totals[full_sid]["overhealing"] == 3
    assert full.log[-1] == f"{full_sid[:5]} heals 0 HP from Spirit Light Sword."

    for index, starting_hp in enumerate((0, -2)):
        rescue = make_match(
            "warrior",
            "mage",
            p1_items={"weapon": "spirit_light_sword"},
            seed=9432 + index,
        )
        rescue_sid = rescue.players[0]
        rescue_owner = rescue.state[rescue_sid]
        rescue_owner.res.hp = starting_hp
        submit_turn(rescue, _DEF_PASS, _DEF_PASS)
        assert rescue_owner.res.hp == starting_hp + 3
        assert rescue.combat_totals[rescue_sid]["healing"] == 3
        assert rescue.combat_totals[rescue_sid]["overhealing"] == 0
        assert rescue.phase == "combat" and rescue.winner is None, \
            "Periodic item healing must restore zero or transient negative HP before winner evaluation"

    cycloned = make_match(
        "warrior",
        "mage",
        p1_items={"weapon": "staff_of_immortality"},
        seed=9434,
    )
    cyclone_sid = cycloned.players[0]
    cyclone_owner = cycloned.state[cyclone_sid]
    cyclone_owner.res.hp -= 20
    effects.apply_effect_by_id(cyclone_owner, "cyclone", overrides={"duration": 2})
    cyclone_hp = cyclone_owner.res.hp
    totals_before = copy.deepcopy(cycloned.combat_totals)
    logs_before = list(cycloned.log)
    periodic_items.resolve_periodic_item_stage(
        match=cycloned,
        rng=random.Random(9434),
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )
    assert cyclone_owner.res.hp == cyclone_hp
    assert cycloned.combat_totals == totals_before
    assert cycloned.log == logs_before, \
        "Cyclone must suppress periodic item healing, accounting, and success logs"

    mindgames = make_match(
        "warrior",
        "priest",
        p1_items={"weapon": "staff_of_immortality"},
        seed=9435,
    )
    mindgames_sid = mindgames.players[0]
    mindgames_owner = mindgames.state[mindgames_sid]
    mindgames_owner.res.hp -= 20
    effects.apply_effect_by_id(
        mindgames_owner,
        "mindgames",
        overrides={"duration": 2, "source_sid": mindgames.players[1]},
    )
    mindgames_hp = mindgames_owner.res.hp
    periodic_items.resolve_periodic_item_stage(
        match=mindgames,
        rng=random.Random(9435),
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )
    assert mindgames_owner.res.hp == mindgames_hp + 4
    assert mindgames.combat_totals[mindgames_sid]["healing"] == 4
    assert not any("Mindgames" in line for line in mindgames.log), \
        "Legacy item healing must not be converted by Mindgames"

    ice_block = make_match(
        "mage",
        "warrior",
        p1_items={"weapon": "spirit_light_sword"},
        seed=9436,
    )
    ice_block_sid = ice_block.players[0]
    ice_block_owner = ice_block.state[ice_block_sid]
    ice_block_owner.res.hp -= 20
    effects.apply_effect_by_id(ice_block_owner, "ice_block", overrides={"duration": 2})
    ice_block_hp = ice_block_owner.res.hp
    periodic_items.resolve_periodic_item_stage(
        match=ice_block,
        rng=random.Random(9436),
        turn_context=None,
        apply_damage=_unused_apply_damage,
    )
    assert ice_block_owner.res.hp == ice_block_hp + 3
    assert ice_block.combat_totals[ice_block_sid]["healing"] == 3, \
        "Non-Cyclone immunity-all states must not suppress periodic item healing"
    return True


def scenario_staff_dispatches_before_vial_for_same_owner() -> bool:
    match = make_match(
        "warrior",
        "mage",
        p1_items={
            "weapon": "staff_of_immortality",
            "trinket": "vial_of_shadows",
        },
        seed=9440,
    )
    owner_sid = match.players[0]
    match.state[owner_sid].res.hp -= 20
    match.turn = 4
    damage_calls: list[dict[str, Any]] = []
    roll_calls: list[str] = []
    with _fixed_vial_roll(1, roll_calls):
        activations = periodic_items.resolve_periodic_item_stage(
            match=match,
            rng=random.Random(9440),
            turn_context=None,
            apply_damage=_capture_damage_calls(damage_calls),
        )

    assert [
        (activation.item_slot, activation.item_id)
        for activation in activations
    ] == [
        ("weapon", "staff_of_immortality"),
        ("trinket", "vial_of_shadows"),
    ], "Canonical weapon-before-trinket ordering must dispatch Staff before Vial"
    staff_idx = next(
        index
        for index, line in enumerate(match.log)
        if "heals 4 HP from Staff of Immortality." in line
    )
    vial_idx = match.log.index(f"{owner_sid[:5]} triggers Vial of Shadows.")
    assert staff_idx < vial_idx
    assert roll_calls == ["d6"], "The added heal must not change Vial's one-roll contract"
    return True


def scenario_existing_nonperiodic_items_do_not_activate_periodic_stage() -> bool:
    match = make_match(
        "warrior",
        "mage",
        p1_items={
            "weapon": "steel_long_sword",
            "armor": "challengers_chestplate",
            "trinket": "focus_charm",
        },
        p2_items={
            "weapon": "thunderfury",
            "armor": "cloth_armor",
            "trinket": "rage_crystal",
        },
        seed=9411,
    )
    activations = periodic_items.collect_periodic_item_activations(
        match,
        current_turn=5,
    )
    assert activations == (), \
        "Existing on_hit, on_damage, and triggerless item passives must not enter the periodic snapshot"
    return True


def scenario_vial_of_shadows_item_data_docs_and_ui() -> bool:
    item = ITEMS["vial_of_shadows"]
    passive = item["passive"]
    assert item.get("item_id") == "vial_of_shadows"
    assert item.get("name") == "Vial of Shadows"
    assert item.get("slot") == "trinket"
    assert item.get("color") == "#a335ee"
    assert item.get("mods") == {}
    assert "active" not in item and "classes" not in item
    assert passive == {
        "type": "periodic_global_damage",
        "trigger": "periodic_end_of_turn",
        "interval": 5,
        "first_trigger_turn": 5,
        "school": "magical",
        "subschool": "shadow",
        "scaling": {
            "stats": ["atk", "int"],
            "multiplier": 0.3,
            "rounding": "floor",
        },
        "dice": "d6",
        "target_mode": "all_players_and_pets",
    }, "Vial production metadata must remain the complete data-driven mechanic"

    match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9501,
    )
    owner = match.state[match.players[0]]
    panel = effects.build_effect_panel_payload(owner)
    assert all(not entries for entries in panel.values()), \
        "The internal periodic item passive must not create a visible effect-panel row"
    vial_effects = [
        effect
        for effect in owner.effects
        if effect.get("source_item_id") == "vial_of_shadows"
    ]
    assert len(vial_effects) == 1 and vial_effects[0].get("type") == "item_passive"
    assert not any(effect.get("duration") == 5 for effect in vial_effects), \
        "Vial scheduling must not be represented by a five-turn countdown effect"

    duel_html = (
        Path(__file__).resolve().parents[2] / "duel.html"
    ).read_text(encoding="utf-8")
    required_docs = (
        "Vial of Shadows",
        "/item trinket vial_of_shadows",
        'nameColor: "#a335ee"',
        "Every 5 turns, starting on turn 5",
        "30% of the sum of your Attack and Intellect, rounded down, plus d6",
        "Shadow damage",
        "both players and all living pets, summons, and totems",
        "including you and your own entities",
    )
    for text in required_docs:
        assert text in duel_html, f"Vial documentation is missing: {text}"
    assert "Attack + 30% Intellect" not in duel_html
    assert "(Attack + Intellect) + 0.3" not in duel_html
    assert '"Vial of Shadows": {' in duel_html and 'meta: "Trinket — Epic"' in duel_html
    purple_items_match = re.search(
        r"const purpleItems = \[(.*?)\];",
        duel_html,
        flags=re.DOTALL,
    )
    assert purple_items_match is not None, "duel.html must retain the purpleItems array"
    purple_items_block = purple_items_match.group(1)
    assert purple_items_block.count('"Vial of Shadows"') == 1, \
        "Vial of Shadows must appear exactly once inside purpleItems"
    return True


def scenario_vial_of_shadows_schedule_boundaries_and_rng() -> bool:
    match = make_match(
        "priest",
        "warrior",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9502,
    )
    owner_sid = match.players[0]
    roll_calls: list[str] = []
    with _fixed_vial_roll(1, roll_calls):
        for global_turn in range(1, 11):
            submit_turn(match, _DEF_PASS, _DEF_PASS)
            trigger_count = match.log.count(
                f"{owner_sid[:5]} triggers Vial of Shadows."
            )
            expected_count = 0 if global_turn < 5 else 1 if global_turn < 10 else 2
            assert trigger_count == expected_count, \
                f"Vial scheduled incorrectly on global turn {global_turn}"
            assert len(roll_calls) == expected_count, \
                f"Vial consumed RNG on an ineligible global turn {global_turn}"

    assert roll_calls == ["d6", "d6"], \
        "Turns 5 and 10 must each consume exactly one Vial d6"
    turn_5_index = match.log.index("Turn 5")
    turn_6_index = match.log.index("Turn 6")
    turn_10_index = match.log.index("Turn 10")
    trigger_indices = [
        index
        for index, line in enumerate(match.log)
        if line == f"{owner_sid[:5]} triggers Vial of Shadows."
    ]
    assert turn_5_index < trigger_indices[0] < turn_6_index
    assert turn_10_index < trigger_indices[1]
    return True


def scenario_vial_of_shadows_formula_live_stats_and_target_snapshot() -> bool:
    match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9503,
    )
    owner_sid, enemy_sid = match.players
    owner = match.state[owner_sid]
    enemy = match.state[enemy_sid]
    owner.stats["atk"] = 13
    owner.stats["int"] = 57
    enemy.res.hp = -3

    _add_periodic_test_entity(owner, "b_entity", name="Imp")
    _add_periodic_test_entity(owner, "a_entity", name="Imp")
    _add_periodic_test_entity(owner, "dead_entity", name="Dead Imp", hp=0)
    _add_periodic_test_entity(enemy, "c_entity", name="Totem", entity_type="totem")

    captured: list[dict[str, Any]] = []
    roll_calls: list[str] = []
    match.turn = 4
    with _fixed_vial_roll(1, roll_calls):
        activations = periodic_items.resolve_periodic_item_stage(
            match=match,
            rng=random.Random(9503),
            turn_context=None,
            apply_damage=_capture_damage_calls(captured),
        )

    assert len(activations) == 1
    assert roll_calls == ["d6"], \
        "One activation with multiple targets must roll only one d6"
    assert all(call["incoming"] == 22 for call in captured), \
        "Vial base damage must be int((13 + 57) * 0.3) + 1 == 22 for every target"
    target_order = [
        call["target"].sid
        if hasattr(call["target"], "res")
        else call["target"].id
        for call in captured
    ]
    assert target_order == [
        owner_sid,
        "a_entity",
        "b_entity",
        enemy_sid,
        "c_entity",
    ], "Targets must use player order, then living entity ID order per player"
    assert "dead_entity" not in target_order, \
        "An entity already at zero HP must be excluded from the activation snapshot"
    assert enemy_sid in target_order, \
        "A player at negative HP must still be included before final winner evaluation"

    for call in captured:
        kwargs = call["kwargs"]
        assert kwargs.get("mindgames_flip_damage") is False
        assert kwargs.get("allow_redirect") is False
        assert kwargs.get("school") == "magical"
        assert kwargs.get("subschool") == "shadow"
        assert kwargs.get("source_kind") == damage_types.DAMAGE_SOURCE_PERIODIC_ITEM
        assert kwargs.get("damage_instances") == [22]
        is_player = hasattr(call["target"], "res")
        assert kwargs.get("resolve_player_mitigation") is is_player
        assert kwargs.get("resolve_non_player_mitigation") is (not is_player)

    target_logs = [
        line
        for line in match.log
        if line.startswith("Vial of Shadows hits ")
    ]
    assert "(a_entity)" in target_logs[1] and "(b_entity)" in target_logs[2], \
        "Same-name entity logs must include stable IDs"
    assert match.combat_totals[owner_sid]["damage"] == 110, \
        "Captured actual HP damage must be credited once to the Vial owner"

    live_match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9504,
    )
    live_owner = live_match.state[live_match.players[0]]
    live_owner.stats["atk"] = 11
    live_owner.stats["int"] = 10
    live_owner.effects.append(
        {
            "id": "test_vial_live_attack",
            "type": "stat_mods",
            "mods": {"atk": 9},
            "duration": 2,
        }
    )
    live_captured: list[dict[str, Any]] = []
    live_roll_calls: list[str] = []
    live_match.turn = 4
    with _fixed_vial_roll(4, live_roll_calls):
        periodic_items.resolve_periodic_item_stage(
            match=live_match,
            rng=random.Random(9504),
            turn_context=None,
            apply_damage=_capture_damage_calls(live_captured),
        )
    assert live_roll_calls == ["d6"]
    assert all(call["incoming"] == 13 for call in live_captured), \
        "Vial must use live modified Attack: int((20 + 10) * 0.3) + 4 == 13"
    return True


def scenario_vial_of_shadows_outgoing_modifier_snapshot() -> bool:
    base_raw_damage = 22

    def run_case(
        *,
        seed: int,
        snapshot_mode: str | None,
        include_challenger: bool,
        include_avenging_wrath: bool,
        live_mode: str | None = None,
        mutate_after_first_packet: bool = False,
    ) -> tuple[int, float, float, list[dict[str, Any]]]:
        p1_items = {"trinket": "vial_of_shadows"}
        if include_challenger:
            p1_items["armor"] = "challengers_chestplate"
        match = make_match(
            "warrior",
            "mage",
            p1_items=p1_items,
            seed=seed,
        )
        owner_sid = match.players[0]
        owner = match.state[owner_sid]
        owner.stats["atk"] = 13
        owner.stats["int"] = 57
        if include_challenger:
            current_mode = live_mode if live_mode is not None else snapshot_mode
            owner.res.rage = owner.res.rage_max if current_mode == "might" else 0
        if include_avenging_wrath:
            effects.apply_effect_by_id(
                owner,
                "avenging_wrath",
                overrides={"duration": 4},
            )

        passive_multiplier = effects.damage_multiplier_from_passives(
            owner,
            challenger_mode=snapshot_mode,
        )
        effect_multiplier = effects.outgoing_damage_multiplier(owner)
        expected_outgoing = base_raw_damage
        if passive_multiplier != 1.0:
            expected_outgoing = int(expected_outgoing * passive_multiplier)
        if effect_multiplier != 1.0:
            expected_outgoing = int(expected_outgoing * effect_multiplier)

        captured: list[dict[str, Any]] = []
        capture_damage = _capture_damage_calls(captured)

        def capture_and_mutate(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = capture_damage(*args, **kwargs)
            if mutate_after_first_packet and len(captured) == 1:
                owner.res.rage = 0
                effects.remove_effect(owner, "avenging_wrath")
            return result

        match.turn = 4
        roll_calls: list[str] = []
        turn_context = SimpleNamespace(
            challenger_mode_by_sid={owner_sid: snapshot_mode},
        )
        with _fixed_vial_roll(1, roll_calls):
            periodic_items.resolve_periodic_item_stage(
                match=match,
                rng=random.Random(seed),
                turn_context=turn_context,
                apply_damage=capture_and_mutate,
            )

        assert roll_calls == ["d6"]
        assert len(captured) == 2, "The baseline outgoing test must hit both players"
        assert all(call["incoming"] == expected_outgoing for call in captured)
        assert all(
            call["kwargs"].get("damage_instances") == [expected_outgoing]
            for call in captured
        ), "damage_instances must carry the outgoing-adjusted raw packet"
        return expected_outgoing, passive_multiplier, effect_multiplier, captured

    might_damage, might_passive, might_effect, _ = run_case(
        seed=9512,
        snapshot_mode="might",
        include_challenger=True,
        include_avenging_wrath=False,
    )
    assert might_effect == 1.0
    assert might_damage == int(base_raw_damage * might_passive) == 24, \
        "Challenger Might must raise the Vial pre-mitigation packet to 24"

    wrath_damage, wrath_passive, wrath_effect, _ = run_case(
        seed=9513,
        snapshot_mode="wrath",
        include_challenger=True,
        include_avenging_wrath=False,
    )
    assert wrath_effect == 1.0
    assert wrath_damage == int(base_raw_damage * wrath_passive) == 19
    assert wrath_damage < base_raw_damage, \
        "Challenger Wrath must lower the Vial pre-mitigation packet"

    avenging_damage, avenging_passive, avenging_effect, _ = run_case(
        seed=9514,
        snapshot_mode=None,
        include_challenger=False,
        include_avenging_wrath=True,
    )
    assert avenging_passive == 1.0
    assert avenging_damage == int(base_raw_damage * avenging_effect) == 26, \
        "Avenging Wrath must raise the Vial pre-mitigation packet to 26"

    combined_damage, combined_passive, combined_effect, combined_calls = run_case(
        seed=9515,
        snapshot_mode="might",
        include_challenger=True,
        include_avenging_wrath=True,
        mutate_after_first_packet=True,
    )
    expected_combined = int(
        int(base_raw_damage * combined_passive) * combined_effect
    )
    assert combined_damage == expected_combined == 28, \
        "Passive then effect multiplier ordering must produce 28 exactly once"
    assert [call["incoming"] for call in combined_calls] == [28, 28], \
        "Owner-state changes after the first packet must not change later packets"

    snapshot_damage, snapshot_passive, _, _ = run_case(
        seed=9516,
        snapshot_mode="might",
        include_challenger=True,
        include_avenging_wrath=False,
        live_mode="wrath",
    )
    assert snapshot_damage == int(base_raw_damage * snapshot_passive) == 24, \
        "The turn-context Might snapshot must take precedence over live Wrath state"
    return True


def scenario_vial_of_shadows_shared_mitigation_absorbs_immunity() -> bool:
    mitigation_match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        p2_items={"armor": "challengers_chestplate"},
        seed=9505,
    )
    owner_sid, target_sid = mitigation_match.players
    owner = mitigation_match.state[owner_sid]
    target = mitigation_match.state[target_sid]
    owner.stats["atk"] = 11
    owner.stats["int"] = 10
    target.stats["def"] = 5
    target.stats["magic_resist"] = 7
    target.stats["shadow_resist"] = 3
    target.effects.append(
        {
            "id": "test_vial_incoming_reduction",
            "type": "mitigation",
            "value": 0.25,
            "duration": 2,
        }
    )
    raw_damage = 10
    expected_target_damage = effects.resolve_incoming_damage(
        raw_damage,
        target,
        "magical",
        subschool="shadow",
    )
    target_hp_before = target.res.hp
    mitigation_match.turn = 4
    mitigation_rolls: list[str] = []
    with _fixed_vial_roll(4, mitigation_rolls):
        submit_turn(mitigation_match, _DEF_PASS, _DEF_PASS)
    assert target_hp_before - target.res.hp == expected_target_damage, \
        "Vial must use Defense, Magic Resistance, Shadow Resistance, incoming reduction, and Challenger mitigation"

    absorb_match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9506,
    )
    absorb_owner_sid, absorb_target_sid = absorb_match.players
    absorb_owner = absorb_match.state[absorb_owner_sid]
    absorb_target = absorb_match.state[absorb_target_sid]
    absorb_owner.stats["atk"] = 11
    absorb_owner.stats["int"] = 10
    owner_damage = effects.resolve_incoming_damage(
        raw_damage,
        absorb_owner,
        "magical",
        subschool="shadow",
    )
    target_damage = effects.resolve_incoming_damage(
        raw_damage,
        absorb_target,
        "magical",
        subschool="shadow",
    )
    absorb_amount = max(1, target_damage - 1)
    effects.add_absorb(
        absorb_target,
        absorb_amount,
        source_name="Power Word: Shield",
        effect_id="power_word_shield",
    )
    absorb_target_hp_before = absorb_target.res.hp
    absorb_match.turn = 4
    absorb_rolls: list[str] = []
    with _fixed_vial_roll(4, absorb_rolls):
        submit_turn(absorb_match, _DEF_PASS, _DEF_PASS)
    assert absorb_target_hp_before - absorb_target.res.hp == target_damage - absorb_amount
    assert absorb_match.combat_totals[absorb_owner_sid]["damage"] == (
        owner_damage + target_damage - absorb_amount
    ), "Absorbed Vial damage must not count as actual damage done"
    absorb_log = next(
        line
        for line in absorb_match.log
        if line.startswith(f"Vial of Shadows hits {absorb_target_sid[:5]}")
    )
    assert f"{absorb_amount} absorbed by Power Word: Shield" in absorb_log

    for effect_id, seed in (("cloak_of_shadows", 9507), ("divine_shield", 9508)):
        immune_match = make_match(
            "warrior",
            "mage",
            p1_items={"trinket": "vial_of_shadows"},
            seed=seed,
        )
        immune_owner_sid, immune_target_sid = immune_match.players
        immune_owner = immune_match.state[immune_owner_sid]
        immune_target = immune_match.state[immune_target_sid]
        immune_owner.stats["atk"] = 11
        immune_owner.stats["int"] = 10
        self_damage = effects.resolve_incoming_damage(
            raw_damage,
            immune_owner,
            "magical",
            subschool="shadow",
        )
        effects.apply_effect_by_id(
            immune_target,
            effect_id,
            overrides={"duration": 2},
        )
        immune_hp_before = immune_target.res.hp
        immune_match.turn = 4
        immune_rolls: list[str] = []
        with _fixed_vial_roll(4, immune_rolls):
            submit_turn(immune_match, _DEF_PASS, _DEF_PASS)
        assert immune_target.res.hp == immune_hp_before, \
            f"{effect_id} must prevent Vial damage"
        assert immune_match.combat_totals[immune_owner_sid]["damage"] == self_damage, \
            f"{effect_id} must contribute zero Vial damage credit"
        immune_lines = [
            line
            for line in immune_match.log
            if "immune" in line.lower() and immune_target_sid[:5] in line
        ]
        assert len(immune_lines) == 1, \
            f"{effect_id} must produce exactly one concise immunity result log"
    return True


def scenario_vial_of_shadows_ignores_miss_redirect_and_mindgames() -> bool:
    match = make_match(
        "warrior",
        "rogue",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9509,
    )
    owner_sid, target_sid = match.players
    owner = match.state[owner_sid]
    target = match.state[target_sid]
    owner.stats["atk"] = 11
    owner.stats["int"] = 10
    owner.stats["acc"] = -999
    target.stats["eva"] = 999
    redirect_pet = _add_periodic_test_entity(
        target,
        "redirect_pet",
        name="Barrens Boar",
        entity_type="pet",
    )
    effects.apply_effect_by_id(owner, "mindgames", overrides={"source_sid": target_sid})
    effects.apply_effect_by_id(target, "blink", overrides={"duration": 2})
    effects.apply_effect_by_id(target, "evasion", overrides={"duration": 2})
    effects.apply_effect_by_id(
        target,
        "blocking_defence",
        overrides={
            "duration": 2,
            "redirect_to_pet_id": redirect_pet.id,
        },
    )

    raw_damage = 10
    expected_owner_damage = effects.resolve_incoming_damage(
        raw_damage,
        owner,
        "magical",
        subschool="shadow",
    )
    expected_target_damage = effects.resolve_incoming_damage(
        raw_damage,
        target,
        "magical",
        subschool="shadow",
    )
    expected_pet_damage = effects.resolve_incoming_damage(
        raw_damage,
        redirect_pet,
        "magical",
        subschool="shadow",
    )
    owner_hp_before = owner.res.hp
    target_hp_before = target.res.hp
    pet_hp_before = redirect_pet.hp
    match.turn = 4
    roll_calls: list[str] = []
    with _fixed_vial_roll(4, roll_calls):
        submit_turn(match, _DEF_PASS, _DEF_PASS)

    assert owner_hp_before - owner.res.hp == expected_owner_damage, \
        "Mindgames must not flip Vial self-damage into healing"
    assert target_hp_before - target.res.hp == expected_target_damage, \
        "Accuracy, Evasion, stealth, and blink-like avoidance must not stop Vial"
    assert pet_hp_before - redirect_pet.hp == expected_pet_damage, \
        "The redirect pet must receive only its own global packet"
    assert match.combat_totals[owner_sid]["damage"] == (
        expected_owner_damage + expected_target_damage + expected_pet_damage
    )
    assert match.combat_totals[owner_sid]["healing"] == 0
    assert match.combat_totals[target_sid]["healing"] == 0
    assert not any("Mindgames flips" in line for line in match.log)
    return True


def scenario_vial_of_shadows_combat_totals_all_entities() -> bool:
    match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9510,
    )
    owner_sid, target_sid = match.players
    owner = match.state[owner_sid]
    target = match.state[target_sid]
    owner.stats["atk"] = 11
    owner.stats["int"] = 10
    owner_pet = _add_periodic_test_entity(owner, "owner_pet", name="Imp")
    owner_summon = _add_periodic_test_entity(
        owner,
        "owner_summon",
        name="Imp",
        entity_type="summon",
    )
    target_totem = _add_periodic_test_entity(
        target,
        "target_totem",
        name="Mana Tide Totem",
        entity_type="totem",
    )
    targets = (owner, owner_pet, owner_summon, target, target_totem)
    hp_before = {
        id(entity): entity.res.hp if hasattr(entity, "res") else entity.hp
        for entity in targets
    }
    raw_damage = 10
    expected_damage = {
        id(entity): effects.resolve_incoming_damage(
            raw_damage,
            entity,
            "magical",
            subschool="shadow",
        )
        for entity in targets
    }

    match.turn = 4
    roll_calls: list[str] = []
    with _fixed_vial_roll(4, roll_calls):
        submit_turn(match, _DEF_PASS, _DEF_PASS)

    actual_damage = {
        id(entity): hp_before[id(entity)] - (
            entity.res.hp if hasattr(entity, "res") else entity.hp
        )
        for entity in targets
    }
    assert actual_damage == expected_damage, \
        "Every player, pet, summon, and totem must receive one independently mitigated packet"
    assert match.combat_totals[owner_sid]["damage"] == sum(expected_damage.values()), \
        "The Vial owner must receive exactly the sum of actual all-entity HP damage"
    assert match.combat_totals[target_sid]["damage"] == 0, \
        "No target may receive credit for the Vial owner's damage"
    entity_hit_logs = [
        line
        for line in match.log
        if line.startswith("Vial of Shadows hits ")
    ]
    assert len(entity_hit_logs) == len(targets), \
        "Every eligible entity must have exactly one resolved target log"
    assert sum("(owner_pet)" in line for line in entity_hit_logs) == 1
    assert sum("(owner_summon)" in line for line in entity_hit_logs) == 1
    assert sum("(target_totem)" in line for line in entity_hit_logs) == 1
    return True


def scenario_dual_vials_commit_and_preserve_double_ko() -> bool:
    match = make_match(
        "priest",
        "priest",
        p1_items={"trinket": "vial_of_shadows"},
        p2_items={"trinket": "vial_of_shadows"},
        seed=9511,
    )
    p1_sid, p2_sid = match.players
    p1 = match.state[p1_sid]
    p2 = match.state[p2_sid]
    raw_damage = int((p1.stats["atk"] + p1.stats["int"]) * 0.3) + 6
    lethal_damage = effects.resolve_incoming_damage(
        raw_damage,
        p1,
        "magical",
        subschool="shadow",
    )
    assert lethal_damage > 0
    p1.res.hp = lethal_damage
    p2.res.hp = lethal_damage
    match.turn = 4

    roll_calls: list[str] = []
    with _fixed_vial_roll(6, roll_calls):
        resolver.submit_action(match, p1_sid, {"ability_id": _DEF_PASS})
        resolver.submit_action(match, p2_sid, {"ability_id": _DEF_PASS})
        resolver.resolve_turn(match)

    activation_lines = [
        line
        for line in match.log
        if line.endswith("triggers Vial of Shadows.")
    ]
    assert activation_lines == [
        f"{p1_sid[:5]} triggers Vial of Shadows.",
        f"{p2_sid[:5]} triggers Vial of Shadows.",
    ], "Both committed Vials must execute in canonical player order"
    assert roll_calls == ["d6", "d6"], \
        "Two committed Vials must consume exactly two d6 rolls"
    assert p1.res.hp == -lethal_damage and p2.res.hp == -lethal_damage, \
        "The second Vial must still hit both already non-positive players"
    assert match.combat_totals[p1_sid]["damage"] == lethal_damage * 2
    assert match.combat_totals[p2_sid]["damage"] == lethal_damage * 2
    assert match.turn == 5
    assert match.phase == "ended" and match.winner is None
    assert "Double KO. No winner." in match.log
    assert match.log.index(activation_lines[1]) < match.log.index("Double KO. No winner."), \
        "Winner evaluation must occur only after the second committed activation"
    return True
