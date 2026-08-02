"""Security regressions for canonical Socket.IO client payloads."""
from __future__ import annotations

import copy

from contextlib import contextmanager
from typing import Any, Iterator

from harness import MatchState, SOCKETS, make_match, resolver


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
def _registered_handlers(
    match: MatchState | None,
) -> Iterator[
    tuple[
        _FakeSocketIO,
        list[tuple[str, str, Any, dict[str, Any]]],
        list[str],
    ]
]:
    socketio = _FakeSocketIO()
    direct_emits: list[tuple[str, str, Any, dict[str, Any]]] = []
    match_lookups: list[str] = []
    original_get_match = SOCKETS.state.get_match_by_sid
    original_emit = SOCKETS.emit
    original_sid = SOCKETS.request.sid

    def get_match_by_sid(sid: str) -> MatchState | None:
        match_lookups.append(sid)
        if match is not None and sid in match.players:
            return match
        return None

    def record_direct_emit(
        event: str,
        payload: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        direct_emits.append((SOCKETS.request.sid, event, payload, kwargs))

    SOCKETS.state.get_match_by_sid = get_match_by_sid
    SOCKETS.emit = record_direct_emit
    SOCKETS.register_duel_socket_handlers(socketio)
    try:
        yield socketio, direct_emits, match_lookups
    finally:
        SOCKETS.state.get_match_by_sid = original_get_match
        SOCKETS.emit = original_emit
        SOCKETS.request.sid = original_sid


def _call_socket_handler(handler: Any, *args: Any) -> None:
    try:
        handler(*args)
    except Exception as exc:  # pragma: no cover - converted into a clear regression failure
        raise AssertionError(
            f"Socket handler leaked {type(exc).__name__} instead of rejecting safely"
        ) from exc


def _assert_one_sid_local_error(
    direct_emits: list[tuple[str, str, Any, dict[str, Any]]],
    before: int,
    sid: str,
    *,
    expected_message: str | None = None,
) -> str:
    assert len(direct_emits) == before + 1, (
        "A rejected payload must emit exactly one submitting-SID error"
    )
    error_sid, event, message, kwargs = direct_emits[-1]
    assert error_sid == sid and event == "duel_system"
    assert kwargs.get("to") in (None, sid), "Validation errors must stay SID-local"
    assert isinstance(message, str) and len(message) < 200, (
        "Validation errors must be concise and must not echo attacker payloads"
    )
    if expected_message is not None:
        assert message == expected_message
    return message


def _canonical_pick(class_id: str) -> dict[str, Any]:
    return {
        "class_id": class_id,
        "items": {slot: None for slot in _EQUIPMENT_SLOTS},
    }


def scenario_socket_prep_unknown_fields_are_rejected() -> bool:
    p1_sid, p2_sid = "prep_unknown_p1", "prep_unknown_p2"
    match = MatchState(
        room_id="prep-unknown-fields",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7401,
    )
    match.picks[p1_sid] = _canonical_pick("warrior")
    original_pick = copy.deepcopy(match.picks[p1_sid])
    attacks = (
        {"class_id": "rogue", "unused": "attacker-value"},
        {
            "items": {"weapon": "thunderfury"},
            "target_sid": p2_sid,
        },
    )

    with _registered_handlers(match) as (socketio, direct_emits, lookups):
        submit = socketio.handlers["duel_prep_submit"]
        SOCKETS.request.sid = p1_sid
        for payload in attacks:
            direct_before = len(direct_emits)
            socket_before = len(socketio.emitted)
            _call_socket_handler(submit, payload)
            _assert_one_sid_local_error(
                direct_emits,
                direct_before,
                p1_sid,
                expected_message="Unknown prep field.",
            )
            assert len(socketio.emitted) == socket_before
            assert match.picks[p1_sid] == original_pick
            assert match.phase == "prep"
            assert "unused" not in match.picks[p1_sid]
            assert "target_sid" not in match.picks[p1_sid]

        assert lookups and set(lookups) == {p1_sid}
    return True


def scenario_socket_prep_structural_values_are_rejected() -> bool:
    p1_sid, p2_sid = "prep_structure_p1", "prep_structure_p2"
    match = MatchState(
        room_id="prep-structural-values",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7402,
    )
    match.picks[p1_sid] = _canonical_pick("warrior")
    original_pick = copy.deepcopy(match.picks[p1_sid])
    invalid_payloads: tuple[object, ...] = (
        "warrior",
        ["warrior"],
        {},
        {"class_id": ["warrior"]},
        {"class_id": {"nested": "warrior"}},
        {"class_id": 7},
        {"items": ["thunderfury"]},
        {"items": "thunderfury"},
        {"items": {"weapon": ["thunderfury"]}},
        {
            "class_id": "warrior".rjust(
                SOCKETS.MAX_CLASS_ID_CHARACTERS + 1
            ),
        },
        {
            "items": {
                "weapon": "thunderfury".rjust(
                    SOCKETS.MAX_ITEM_ID_CHARACTERS + 1
                ),
            },
        },
        {
            "items": {
                "weapon".rjust(
                    SOCKETS.MAX_EQUIPMENT_SLOT_CHARACTERS + 1
                ): "thunderfury",
            },
        },
    )

    with _registered_handlers(match) as (socketio, direct_emits, _lookups):
        submit = socketio.handlers["duel_prep_submit"]
        SOCKETS.request.sid = p1_sid
        for args in ((), ({"class_id": "warrior"}, {"items": {}})):
            direct_before = len(direct_emits)
            _call_socket_handler(submit, *args)
            _assert_one_sid_local_error(
                direct_emits,
                direct_before,
                p1_sid,
                expected_message="Invalid prep submission.",
            )
            assert match.picks[p1_sid] == original_pick
        for payload in invalid_payloads:
            direct_before = len(direct_emits)
            socket_before = len(socketio.emitted)
            _call_socket_handler(submit, payload)
            message = _assert_one_sid_local_error(direct_emits, direct_before, p1_sid)
            assert "nested" not in message and "thunderfury" not in message.lower()
            assert len(socketio.emitted) == socket_before
            assert match.picks[p1_sid] == original_pick
            assert match.phase == "prep"
    return True


def scenario_socket_prep_lock_is_final_and_opponent_can_continue() -> bool:
    p1_sid, p2_sid = "prep_lock_p1", "prep_lock_p2"
    match = MatchState(
        room_id="prep-lock-finality",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7403,
    )

    with _registered_handlers(match) as (socketio, direct_emits, _lookups):
        submit = socketio.handlers["duel_prep_submit"]
        lock_in = socketio.handlers["duel_lock_in"]

        SOCKETS.request.sid = p1_sid
        _call_socket_handler(submit, {"items": {"": "thunderfury"}})
        _call_socket_handler(submit, {"class_id": "warrior"})
        _call_socket_handler(submit, {"items": {"armor": "leather_armor"}})
        _call_socket_handler(submit, {"items": {"trinket": "rage_crystal"}})
        _call_socket_handler(lock_in)
        frozen_pick = copy.deepcopy(match.picks[p1_sid])
        assert match.locked_in[p1_sid] is True and match.phase == "prep"

        post_lock_payloads: tuple[object, ...] = (
            {"class_id": "rogue"},
            {"items": {"weapon": "steel_long_sword"}},
            {"items": {"armor": "plate_armor"}},
            ["malformed"],
        )
        for payload in post_lock_payloads:
            direct_before = len(direct_emits)
            _call_socket_handler(submit, payload)
            _assert_one_sid_local_error(
                direct_emits,
                direct_before,
                p1_sid,
                expected_message="Your build is locked in and cannot be changed.",
            )
            assert match.picks[p1_sid] == frozen_pick
            assert match.locked_in[p1_sid] is True

        SOCKETS.request.sid = p2_sid
        _call_socket_handler(submit, {"class_id": "priest"})
        _call_socket_handler(submit, {"class_id": "mage"})
        assert match.picks[p2_sid]["class_id"] == "mage"
        _call_socket_handler(lock_in)

    assert match.phase == "combat"
    assert match.picks[p1_sid] == frozen_pick
    warrior = match.state[p1_sid]
    assert warrior.build.class_id == "warrior"
    assert warrior.build.items == {
        "weapon": "thunderfury",
        "armor": "leather_armor",
        "trinket": "rage_crystal",
    }
    item_passives = [
        effect for effect in warrior.effects if effect.get("type") == "item_passive"
    ]
    assert len(item_passives) == 4
    assert sum(
        effect.get("source_item_id") == "thunderfury" for effect in item_passives
    ) == 2
    assert sum(
        effect.get("source_item_id") == "rage_crystal" for effect in item_passives
    ) == 2
    return True


def scenario_socket_hidden_action_fields_are_rejected() -> bool:
    match = make_match("warrior", "mage", seed=7404)
    p1_sid, p2_sid = match.players
    hidden_payloads = (
        {"ability_id": "pass_turn", "damage": 999},
        {"ability_id": "pass_turn", "target_sid": p2_sid},
        {"ability_id": "pass_turn", "seed": 1},
        {"ability_id": "pass_turn", "stats": {}},
        {"ability_id": "pass_turn", "room_id": "another-room"},
    )
    original_submit_action = resolver.submit_action
    original_resolve_turn = resolver.resolve_turn
    submit_calls: list[tuple[str, dict[str, Any]]] = []
    resolve_calls: list[MatchState] = []

    def tracking_submit_action(
        current_match: MatchState,
        sid: str,
        action: dict[str, Any],
    ) -> None:
        submit_calls.append((sid, copy.deepcopy(action)))
        original_submit_action(current_match, sid, action)

    def tracking_resolve_turn(current_match: MatchState) -> None:
        resolve_calls.append(current_match)

    resolver.submit_action = tracking_submit_action
    resolver.resolve_turn = tracking_resolve_turn
    try:
        with _registered_handlers(match) as (socketio, direct_emits, lookups):
            action_handler = socketio.handlers["duel_action"]
            SOCKETS.request.sid = p1_sid
            for payload in hidden_payloads:
                match.submitted = {p2_sid: {"ability_id": "pass_turn"}}
                match.turn_in_progress = False
                direct_before = len(direct_emits)
                _call_socket_handler(action_handler, payload)
                _assert_one_sid_local_error(
                    direct_emits,
                    direct_before,
                    p1_sid,
                    expected_message="Unknown action field.",
                )
                assert match.submitted == {p2_sid: {"ability_id": "pass_turn"}}
                assert match.turn == 0 and match.turn_in_progress is False

            assert submit_calls == []
            assert resolve_calls == []
            assert lookups and set(lookups) == {p1_sid}
    finally:
        resolver.submit_action = original_submit_action
        resolver.resolve_turn = original_resolve_turn
    return True


def scenario_socket_valid_action_is_minimally_canonical() -> bool:
    match = make_match("warrior", "mage", seed=7405)
    p1_sid = match.players[0]
    original_payload = {"ability_id": "  PASS TURN  "}

    with _registered_handlers(match) as (socketio, _direct_emits, _lookups):
        SOCKETS.request.sid = p1_sid
        _call_socket_handler(socketio.handlers["duel_action"], original_payload)

    assert match.submitted[p1_sid] == {"ability_id": "pass_turn"}
    assert set(match.submitted[p1_sid]) == {"ability_id"}
    assert original_payload == {"ability_id": "  PASS TURN  "}
    return True


def scenario_socket_action_structural_values_are_rejected() -> bool:
    match = make_match("warrior", "mage", seed=7406)
    p1_sid, p2_sid = match.players
    invalid_payloads: tuple[object, ...] = (
        "pass_turn",
        ["pass_turn"],
        None,
        b"pass_turn",
        {},
        {"ability_id": "pass_turn", "extra": True},
        {"ability_id": ["pass_turn"]},
        {"ability_id": {"nested": "pass_turn"}},
        {"ability_id": 7},
        {"ability_id": True},
        {"ability_id": b"pass_turn"},
        {"ability_id": ""},
        {"ability_id": "   "},
        {
            "ability_id": "pass_turn".rjust(
                resolver.MAX_ABILITY_ID_CHARACTERS + 1
            ),
        },
        {"ability_id": "not_a_real_ability"},
    )

    with _registered_handlers(match) as (socketio, direct_emits, _lookups):
        action_handler = socketio.handlers["duel_action"]
        SOCKETS.request.sid = p1_sid
        for args in ((), ({"ability_id": "pass_turn"}, {"ability_id": "pass_turn"})):
            match.submitted = {p2_sid: {"ability_id": "pass_turn"}}
            direct_before = len(direct_emits)
            _call_socket_handler(action_handler, *args)
            _assert_one_sid_local_error(
                direct_emits,
                direct_before,
                p1_sid,
                expected_message="Invalid action submission.",
            )
            assert match.submitted == {p2_sid: {"ability_id": "pass_turn"}}
        for payload in invalid_payloads:
            match.submitted = {p2_sid: {"ability_id": "pass_turn"}}
            direct_before = len(direct_emits)
            socket_before = len(socketio.emitted)
            _call_socket_handler(action_handler, payload)
            _assert_one_sid_local_error(direct_emits, direct_before, p1_sid)
            assert match.submitted == {p2_sid: {"ability_id": "pass_turn"}}
            assert match.turn == 0
            assert len(socketio.emitted) == socket_before
    return True


def scenario_resolver_action_boundary_rejects_hidden_fields() -> bool:
    assert resolver.normalize_player_action(
        {"ability_id": " Mortal Strike "}
    ) == {"ability_id": "mortal_strike"}

    invalid_actions: tuple[object, ...] = (
        {"ability_id": "pass_turn", "damage": 999, "target_sid": "p2"},
        "pass_turn",
        ["pass_turn"],
        None,
        {},
        {"ability_id": ["pass_turn"]},
        {"ability_id": ""},
        {"ability_id": "   "},
        {
            "ability_id": "pass_turn".rjust(
                resolver.MAX_ABILITY_ID_CHARACTERS + 1
            ),
        },
    )
    for action in invalid_actions:
        try:
            resolver.normalize_player_action(action)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Resolver accepted malformed action shape: {type(action)}")

    guarded = make_match("warrior", "mage", seed=7407)
    guarded_sid = guarded.players[0]
    before = copy.deepcopy(guarded.submitted)
    try:
        resolver.submit_action(
            guarded,
            guarded_sid,
            {"ability_id": "pass_turn", "damage": 999, "target_sid": "p2"},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Direct submit_action retained hidden client fields")
    assert guarded.submitted == before

    valid = make_match("warrior", "mage", seed=7408)
    resolver.submit_action(valid, valid.players[0], {"ability_id": " PASS TURN "})
    resolver.submit_action(valid, valid.players[1], {"ability_id": "pass_turn"})
    assert valid.submitted[valid.players[0]] == {"ability_id": "pass_turn"}
    resolver.resolve_turn(valid)
    assert valid.turn == 1 and valid.submitted == {}

    legacy_unknown = make_match("warrior", "mage", seed=7409)
    resolver.submit_action(
        legacy_unknown,
        legacy_unknown.players[0],
        {"ability_id": "totally_fake_ability"},
    )
    resolver.submit_action(
        legacy_unknown,
        legacy_unknown.players[1],
        {"ability_id": "pass_turn"},
    )
    resolver.resolve_turn(legacy_unknown)
    assert any("fumbles (unknown ability)." in line for line in legacy_unknown.log)
    return True


def scenario_socket_chat_is_bounded_plain_text() -> bool:
    p1_sid, p2_sid = "chat_p1", "chat_p2"
    match = MatchState(
        room_id="chat-schema",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7410,
    )
    match.picks[p1_sid] = _canonical_pick("warrior")
    match.picks[p2_sid] = _canonical_pick("mage")

    with _registered_handlers(match) as (socketio, direct_emits, lookups):
        chat = socketio.handlers["duel_chat"]
        SOCKETS.request.sid = p1_sid
        unicode_message = "\u4f60\u597d\uff0c\u52c7\u58eb \u2694\ufe0f"
        unicode_character = "\u754c"
        valid_messages = (
            ("  Hello  ", "Hello"),
            (f"  {unicode_message}  ", unicode_message),
            (
                unicode_character * SOCKETS.MAX_CHAT_CHARACTERS,
                unicode_character * SOCKETS.MAX_CHAT_CHARACTERS,
            ),
        )
        for submitted, expected in valid_messages:
            socket_before = len(socketio.emitted)
            _call_socket_handler(chat, submitted)
            assert len(socketio.emitted) == socket_before + 1
            event, payload, kwargs = socketio.emitted[-1]
            assert event == "duel_chat" and kwargs.get("to") == match.room_id
            assert payload == {
                "playerClass": "Warrior",
                "message": expected,
                "role": "P1",
            }

        rejected_payloads: tuple[object, ...] = (
            "",
            "   ",
            unicode_character * (SOCKETS.MAX_CHAT_CHARACTERS + 1),
            7,
            ["not", "a", "string"],
            {"message": "hidden", "role": "P2", "room_id": "other"},
            True,
            None,
            b"binary",
        )
        direct_before = len(direct_emits)
        socket_before = len(socketio.emitted)
        _call_socket_handler(chat)
        _assert_one_sid_local_error(
            direct_emits,
            direct_before,
            p1_sid,
            expected_message="Chat messages must be text.",
        )
        assert len(socketio.emitted) == socket_before

        direct_before = len(direct_emits)
        _call_socket_handler(chat, "one", "two")
        _assert_one_sid_local_error(
            direct_emits,
            direct_before,
            p1_sid,
            expected_message="Chat messages must be text.",
        )
        assert len(socketio.emitted) == socket_before

        for payload in rejected_payloads:
            socket_before = len(socketio.emitted)
            direct_before = len(direct_emits)
            _call_socket_handler(chat, payload)
            _assert_one_sid_local_error(direct_emits, direct_before, p1_sid)
            assert len(socketio.emitted) == socket_before

        assert lookups and set(lookups) == {p1_sid}
        assert all(
            not isinstance(payload.get("message"), (dict, list, bytes))
            for event, payload, _kwargs in socketio.emitted
            if event == "duel_chat"
        )
    return True


def scenario_socket_identity_and_no_payload_events_are_authoritative() -> bool:
    original_queue = list(SOCKETS.state.duel_queue)
    try:
        SOCKETS.state.duel_queue.clear()
        with _registered_handlers(None) as (socketio, direct_emits, lookups):
            queue = socketio.handlers["duel_queue"]
            SOCKETS.request.sid = "queue_sid"
            direct_before = len(direct_emits)
            _call_socket_handler(
                queue,
                {"room_id": "attacker-room", "opponent_sid": "victim", "seed": 1},
            )
            _assert_one_sid_local_error(
                direct_emits,
                direct_before,
                "queue_sid",
                expected_message="Invalid queue submission.",
            )
            assert SOCKETS.state.duel_queue == []

            _call_socket_handler(queue)
            assert SOCKETS.state.duel_queue == ["queue_sid"]
            SOCKETS.state.duel_queue.clear()
            _call_socket_handler(queue, None)
            assert SOCKETS.state.duel_queue == ["queue_sid"]
            assert lookups and set(lookups) == {"queue_sid"}
    finally:
        SOCKETS.state.duel_queue[:] = original_queue

    p1_sid, p2_sid = "identity_p1", "identity_p2"
    match = MatchState(
        room_id="server-owned-room",
        players=[p1_sid, p2_sid],
        phase="prep",
        seed=7411,
    )
    match.picks[p1_sid] = _canonical_pick("warrior")
    with _registered_handlers(match) as (socketio, direct_emits, lookups):
        lock_in = socketio.handlers["duel_lock_in"]
        SOCKETS.request.sid = p1_sid
        direct_before = len(direct_emits)
        _call_socket_handler(lock_in, {"sid": p2_sid, "confirmed": True})
        _assert_one_sid_local_error(
            direct_emits,
            direct_before,
            p1_sid,
            expected_message="Invalid lock-in submission.",
        )
        assert not match.locked_in.get(p1_sid)
        _call_socket_handler(lock_in)
        assert match.locked_in[p1_sid] is True

        calls: dict[str, list[Any]] = {
            "dequeue": [],
            "leave_room": [],
            "cleanup_room": [],
        }
        original_dequeue = SOCKETS.state.dequeue
        original_leave_room = SOCKETS.leave_room
        original_cleanup_room = SOCKETS.state.cleanup_room
        SOCKETS.state.dequeue = lambda sid: calls["dequeue"].append(sid)
        SOCKETS.leave_room = (
            lambda room_id, **kwargs: calls["leave_room"].append(
                (room_id, kwargs.get("sid"))
            )
        )
        SOCKETS.state.cleanup_room = (
            lambda room_id: calls["cleanup_room"].append(room_id)
        )
        try:
            _call_socket_handler(
                socketio.handlers["disconnect"],
                "client disconnect",
            )
        finally:
            SOCKETS.state.dequeue = original_dequeue
            SOCKETS.leave_room = original_leave_room
            SOCKETS.state.cleanup_room = original_cleanup_room

        assert calls == {
            "dequeue": [p1_sid],
            "leave_room": [(match.room_id, p1_sid)],
            "cleanup_room": [match.room_id],
        }
        assert lookups and set(lookups) == {p1_sid}
        disconnect_emits = [
            entry
            for entry in socketio.emitted
            if entry[0] == "duel_system" and entry[2].get("to") == match.room_id
        ]
        assert len(disconnect_emits) == 2
    return True
