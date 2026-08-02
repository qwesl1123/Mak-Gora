"""Security regressions for server-authoritative prep equipment."""
from __future__ import annotations

import copy
import random

from contextlib import contextmanager
from typing import Any, Iterator

from harness import (
    MatchState,
    PlayerBuild,
    SOCKETS,
    apply_prep_build,
    make_match,
    resolver,
)

from games.duel.engine import periodic_items


_EQUIPMENT_SLOTS = ("weapon", "armor", "trinket")


class _FakeSocketIO:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.emitted: list[tuple[str, Any, dict[str, Any]]] = []

    def on(self, event: str):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register

    def emit(self, event: str, payload: Any = None, **kwargs: Any) -> None:
        self.emitted.append((event, payload, kwargs))


@contextmanager
def _prep_handlers(match: MatchState) -> Iterator[tuple[_FakeSocketIO, list[tuple[str, str, Any, dict[str, Any]]]]]:
    socketio = _FakeSocketIO()
    direct_emits: list[tuple[str, str, Any, dict[str, Any]]] = []
    original_get_match = SOCKETS.state.get_match_by_sid
    original_emit = SOCKETS.emit
    original_sid = SOCKETS.request.sid

    SOCKETS.state.get_match_by_sid = (
        lambda sid: match if sid in match.players else None
    )

    def record_direct_emit(
        event: str,
        payload: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        direct_emits.append((SOCKETS.request.sid, event, payload, kwargs))

    SOCKETS.emit = record_direct_emit
    SOCKETS.register_duel_socket_handlers(socketio)
    try:
        yield socketio, direct_emits
    finally:
        SOCKETS.state.get_match_by_sid = original_get_match
        SOCKETS.emit = original_emit
        SOCKETS.request.sid = original_sid


def _call_socket_handler(handler: Any, *args: Any) -> None:
    try:
        handler(*args)
    except Exception as exc:  # pragma: no cover - converted into a clear regression failure
        raise AssertionError(
            f"Socket prep handler leaked {type(exc).__name__} instead of rejecting safely"
        ) from exc


def _assert_direct_build_rejected(
    items: object,
    *,
    class_id: str = "rogue",
    expected_message: str,
) -> MatchState:
    match = MatchState(
        room_id="equipment-bypass",
        players=["valid_sid", "invalid_sid"],
        phase="prep",
        seed=7110,
    )
    match.picks["valid_sid"] = PlayerBuild(class_id="mage")
    match.picks["invalid_sid"] = {
        "class_id": class_id,
        "items": items,
    }

    try:
        apply_prep_build(match)
    except ValueError as exc:
        assert expected_message.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError("apply_prep_build accepted an illegal equipment build")

    assert match.state == {}, "Equipment validation must finish before any player state is created"
    assert match.combat_totals == {}, "Equipment validation must finish before combat totals initialize"
    assert match.phase == "prep", "A rejected direct build must not enter combat"
    return match


def _item_passives(player: Any, item_id: str) -> list[dict[str, Any]]:
    return [
        effect
        for effect in player.effects
        if effect.get("type") == "item_passive"
        and effect.get("source_item_id") == item_id
    ]


def _unused_apply_damage(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise AssertionError("Staff of Immortality healing must not dispatch damage")


def _resolve_test_player_healing(
    producer: Any,
    recipient: Any,
    requested_amount: int,
    *,
    source_name: str,
    recipient_label: str | None = None,
    source_kind: str | None = None,
) -> dict[str, Any]:
    return resolver.resolve_player_produced_healing(
        producer,
        recipient,
        requested_amount,
        source_name=source_name,
        recipient_label=recipient_label,
        source_kind=source_kind,
        apply_damage=_unused_apply_damage,
    )


def scenario_duplicate_weapon_stats_cannot_stack() -> bool:
    _assert_direct_build_rejected(
        {
            "weapon": "glock_19",
            "armor": "glock_19",
            "trinket": "glock_19",
        },
        expected_message="same item cannot be equipped more than once",
    )

    legal = make_match(
        "rogue",
        "warrior",
        p1_items={"weapon": "glock_19"},
        seed=7111,
    )
    rogue = legal.state[legal.players[0]]
    assert rogue.stats["atk"] == 27, "One legal Glock must apply its +17 Attack exactly once"
    assert rogue.stats["atk"] != 61, "The former three-slot Glock stack produced 61 Attack"
    assert rogue.build.items == {
        "weapon": "glock_19",
        "armor": None,
        "trinket": None,
    }, "Successful builds must retain only the fixed canonical equipment shape"
    return True


def scenario_duplicate_item_passives_cannot_stack() -> bool:
    _assert_direct_build_rejected(
        {
            "weapon": "thunderfury",
            "armor": "thunderfury",
            "trinket": "thunderfury",
        },
        class_id="warrior",
        expected_message="same item cannot be equipped more than once",
    )

    legal = make_match(
        "warrior",
        "mage",
        p1_items={"weapon": "thunderfury"},
        seed=7112,
    )
    passives = _item_passives(legal.state[legal.players[0]], "thunderfury")
    assert len(passives) == 2, "One Thunderfury must create its normal two passive records, not six"
    passive_types = [effect["passive"]["type"] for effect in passives]
    assert passive_types.count("lightning_blast") == 1
    assert passive_types.count("heal_on_hit") == 1
    return True


def scenario_duplicate_periodic_items_cannot_stack() -> bool:
    _assert_direct_build_rejected(
        {
            "weapon": "staff_of_immortality",
            "armor": "staff_of_immortality",
            "trinket": "staff_of_immortality",
        },
        class_id="warrior",
        expected_message="same item cannot be equipped more than once",
    )

    legal = make_match(
        "warrior",
        "mage",
        p1_items={"weapon": "staff_of_immortality"},
        seed=7113,
    )
    owner_sid = legal.players[0]
    owner = legal.state[owner_sid]
    assert len(_item_passives(owner, "staff_of_immortality")) == 1
    owner.res.hp -= 20
    hp_before = owner.res.hp

    activations = periodic_items.resolve_periodic_item_stage(
        match=legal,
        rng=random.Random(7113),
        turn_context=None,
        apply_damage=_unused_apply_damage,
        resolve_player_produced_healing=_resolve_test_player_healing,
    )

    assert len(activations) == 1, "One Staff must schedule one activation, not three"
    assert activations[0].item_slot == "weapon"
    assert activations[0].item_id == "staff_of_immortality"
    assert owner.res.hp == hp_before + 4, "One Staff trigger must heal exactly 4 HP"
    assert legal.combat_totals[owner_sid]["healing"] == 4
    heal_lines = [
        line
        for line in legal.log
        if "heals 4 HP from Staff of Immortality." in line
    ]
    assert len(heal_lines) == 1, "One Staff trigger must emit one healing event"
    return True


def scenario_socket_equipment_updates_are_atomic() -> bool:
    p1_sid, p2_sid = "p1_sid", "p2_sid"
    match = MatchState(
        room_id="equipment-socket-atomicity",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7114,
    )
    match.picks[p1_sid] = {
        "class_id": "rogue",
        "items": {
            "weapon": "steel_daggers",
            "armor": "leather_armor",
            "trinket": "focus_charm",
        },
    }
    original_pick = copy.deepcopy(match.picks[p1_sid])
    invalid_updates = (
        ({"items": {"armor": "glock_19"}}, "cannot be equipped in the armor slot"),
        ({"items": {"weapon": "leather_armor"}}, "cannot be equipped in the weapon slot"),
        ({"items": {"weapon": "rage_crystal"}}, "cannot be equipped in the weapon slot"),
        ({"items": {"weapon": "not_a_real_item"}}, "unknown item"),
        ({"items": {"ring": "focus_charm"}}, "unknown equipment slot"),
        ({"items": ["thunderfury"]}, "equipment selections must be an object"),
        ({"items": {"weapon": 19}}, "item ids must be text"),
        (
            {"items": {"weapon": " Thunderfury ", "armor": "THUNDERFURY"}},
            "same item cannot be equipped more than once",
        ),
    )

    with _prep_handlers(match) as (socketio, direct_emits):
        submit = socketio.handlers["duel_prep_submit"]
        SOCKETS.request.sid = p1_sid
        for payload, expected_message in invalid_updates:
            direct_before = len(direct_emits)
            socket_before = len(socketio.emitted)
            _call_socket_handler(submit, payload)
            assert match.picks[p1_sid] == original_pick, (
                f"Invalid prep payload partially mutated equipment: {payload}"
            )
            assert match.phase == "prep"
            assert len(direct_emits) == direct_before + 1
            error_sid, event, message, kwargs = direct_emits[-1]
            assert error_sid == p1_sid and event == "duel_system"
            assert expected_message in str(message).lower()
            assert not kwargs.get("to"), "Prep validation errors must stay SID-local"
            assert len(socketio.emitted) == socket_before, (
                "Prep validation errors must not be broadcast through socketio.emit"
            )

        _call_socket_handler(submit, {"items": {"": " thunderfury "}})
        assert match.picks[p1_sid]["items"] == {
            "weapon": "thunderfury",
            "armor": "leather_armor",
            "trinket": "focus_charm",
        }, "The frontend's omitted-slot form must infer only the trusted canonical slot"
    return True


def scenario_equipment_class_and_direct_build_checks_remain_authoritative() -> bool:
    p1_sid, p2_sid = "p1_sid", "p2_sid"
    restricted = MatchState(
        room_id="equipment-class-socket",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7115,
    )
    restricted.picks[p1_sid] = {
        "class_id": "warrior",
        "items": {slot: None for slot in _EQUIPMENT_SLOTS},
    }

    with _prep_handlers(restricted) as (socketio, direct_emits):
        submit = socketio.handlers["duel_prep_submit"]
        SOCKETS.request.sid = p1_sid
        _call_socket_handler(submit, {"items": {"weapon": "glock_19"}})
        assert restricted.picks[p1_sid]["class_id"] == "warrior"
        assert all(
            item_id is None
            for item_id in restricted.picks[p1_sid]["items"].values()
        ), "A class-restricted item must not survive a correct-slot socket submission"
        assert "cannot be equipped by warrior" in str(direct_emits[-1][2]).lower()

    legal = MatchState(
        room_id="equipment-class-legal",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7116,
    )
    with _prep_handlers(legal) as (socketio, direct_emits):
        submit = socketio.handlers["duel_prep_submit"]
        SOCKETS.request.sid = p1_sid
        _call_socket_handler(
            submit,
            {"class_id": "rogue", "items": {"weapon": "glock_19"}},
        )
        expected_pick = copy.deepcopy(legal.picks[p1_sid])
        assert expected_pick == {
            "class_id": "rogue",
            "items": {
                "weapon": "glock_19",
                "armor": None,
                "trinket": None,
            },
        }

        _call_socket_handler(submit, {"class_id": "warrior"})
        assert legal.picks[p1_sid] == expected_pick, (
            "An incompatible class change must reject the whole proposed build"
        )
        assert "cannot be equipped by warrior" in str(direct_emits[-1][2]).lower()

    _assert_direct_build_rejected(
        {"weapon": "glock_19"},
        class_id="warrior",
        expected_message="cannot be equipped by warrior",
    )
    direct_bypasses = (
        ({"ring": "focus_charm"}, "unknown equipment slot"),
        ({"weapon": "leather_armor"}, "cannot be equipped in the weapon slot"),
        ({"weapon": "not_a_real_item"}, "unknown item"),
        (["glock_19"], "equipment selections must be an object"),
        ({"weapon": 19}, "item ids must be text"),
    )
    for items, expected_message in direct_bypasses:
        _assert_direct_build_rejected(
            items,
            expected_message=expected_message,
        )

    valid_direct = make_match(
        "rogue",
        "mage",
        p1_items={"weapon": "glock_19"},
        seed=7117,
    )
    assert valid_direct.state[valid_direct.players[0]].stats["atk"] == 27
    return True


def scenario_legal_incremental_three_slot_prep_is_unchanged() -> bool:
    p1_sid, p2_sid = "p1_sid", "p2_sid"
    match = MatchState(
        room_id="equipment-three-slot",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7118,
    )

    with _prep_handlers(match) as (socketio, _direct_emits):
        submit = socketio.handlers["duel_prep_submit"]
        lock_in = socketio.handlers["duel_lock_in"]

        SOCKETS.request.sid = p1_sid
        _call_socket_handler(submit, {"items": {"": "thunderfury"}})
        assert match.picks[p1_sid]["items"]["weapon"] == "thunderfury"
        _call_socket_handler(submit, {"class_id": "warrior"})
        assert match.picks[p1_sid]["items"]["weapon"] == "thunderfury"
        _call_socket_handler(submit, {"items": {"armor": "leather_armor"}})
        assert match.picks[p1_sid]["items"]["weapon"] == "thunderfury"
        _call_socket_handler(submit, {"items": {"trinket": "rage_crystal"}})
        assert match.picks[p1_sid] == {
            "class_id": "warrior",
            "items": {
                "weapon": "thunderfury",
                "armor": "leather_armor",
                "trinket": "rage_crystal",
            },
        }

        SOCKETS.request.sid = p2_sid
        _call_socket_handler(submit, {"class_id": "priest"})

        SOCKETS.request.sid = p1_sid
        _call_socket_handler(lock_in)
        assert match.phase == "prep"
        SOCKETS.request.sid = p2_sid
        _call_socket_handler(lock_in)

    assert match.phase == "combat", "A legal incremental three-slot build must start combat normally"
    warrior = match.state[p1_sid]
    assert warrior.build.items == {
        "weapon": "thunderfury",
        "armor": "leather_armor",
        "trinket": "rage_crystal",
    }
    assert warrior.stats["atk"] == 20
    assert warrior.stats["int"] == 2
    assert warrior.stats["def"] == 8
    assert warrior.stats["spd"] == 8
    assert warrior.stats["crit"] == 5
    assert warrior.stats["acc"] == 90
    assert warrior.stats["eva"] == 5
    assert warrior.stats["spirit"] == 0
    assert warrior.stats["physical_reduction"] == 9
    assert warrior.stats["magic_resist"] == 10
    assert warrior.res.rage_max == 120

    item_passives = [
        effect
        for effect in warrior.effects
        if effect.get("type") == "item_passive"
    ]
    assert len(item_passives) == 4
    assert len(_item_passives(warrior, "thunderfury")) == 2
    assert len(_item_passives(warrior, "rage_crystal")) == 2
    return True
