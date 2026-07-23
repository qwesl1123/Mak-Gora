"""Regression scenarios for the global periodic equipped-item stage."""
from __future__ import annotations

import copy
import random

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from harness import (
    PetState,
    effects,
    make_match,
    submit_turn,
)

from games.duel.content.items import ITEMS
from games.duel.engine import periodic_items

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


def scenario_existing_nonperiodic_items_do_not_activate_periodic_stage() -> bool:
    match = make_match(
        "warrior",
        "mage",
        p1_items={
            "weapon": "spirit_light_sword",
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
        "Existing end_of_turn, on_hit, on_damage, and triggerless item passives must not enter the periodic snapshot"
    return True
