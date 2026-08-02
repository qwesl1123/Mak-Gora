"""Focused regressions for bounded combat logs and socket input limits."""
from __future__ import annotations

import random
import runpy
import sys
import types
from pathlib import Path
from typing import Any

from harness import (
    BoundedCombatLog,
    MatchState,
    SOCKETS,
    _detect_duel_html_path,
    make_match,
    submit_turn,
)

from games.duel.engine import periodic_items


availability = sys.modules["games.duel.availability"]


def scenario_bounded_combat_log_retention_rollover() -> bool:
    log = BoundedCombatLog(capacity=3)
    for index in range(9):
        log.append(f"entry-{index}")

    assert len(log) == 3
    assert log == ["entry-6", "entry-7", "entry-8"]
    assert log.sequence == 9

    log.append("entry-9")
    assert log == ["entry-7", "entry-8", "entry-9"]
    assert log.sequence == 10
    return True


def scenario_bounded_combat_log_extend_and_supplied_history() -> bool:
    match = MatchState(
        room_id="bounded-init",
        players=["p1_sid", "p2_sid"],
        max_retained_log_entries=3,
        log=["entry-0", "entry-1", "entry-2", "entry-3", "entry-4"],
    )

    assert isinstance(match.log, BoundedCombatLog)
    assert match.log == ["entry-2", "entry-3", "entry-4"]
    assert match.log.sequence == 5

    match.log.extend(["entry-5", "entry-6", "entry-7"])
    assert match.log == ["entry-5", "entry-6", "entry-7"]
    assert match.log.sequence == 8
    return True


def scenario_bounded_combat_log_snapshot_cursor_rollover() -> bool:
    match = MatchState(
        room_id="bounded-snapshot",
        players=["p1_sid", "p2_sid"],
        max_retained_log_entries=35,
    )
    match.log.extend(f"entry-{index}" for index in range(50))

    first = SOCKETS.snapshot_for(match, match.players[0])
    second = SOCKETS.snapshot_for(match, match.players[0])
    assert len(match.log) == 35
    assert len(first["log"]) == availability.SNAPSHOT_LOG_ENTRY_LIMIT == 30
    assert first["log"] == [f"entry-{index}" for index in range(20, 50)]
    assert first["log_length"] == second["log_length"] == 50

    match.log.append("entry-50")
    third = SOCKETS.snapshot_for(match, match.players[0])
    assert len(match.log) == 35
    assert third["log_length"] == 51
    assert third["log"][-1] == "entry-50"
    return True


def scenario_bounded_combat_log_periodic_immunity_detector() -> bool:
    match = make_match(
        "warrior",
        "mage",
        p1_items={"trinket": "vial_of_shadows"},
        seed=9801,
    )
    match.log = BoundedCombatLog(
        ["retained-0", "retained-1", "retained-2"],
        capacity=3,
    )
    activations = periodic_items.collect_periodic_item_activations(
        match,
        current_turn=5,
    )
    assert len(activations) == 1

    application_count = 0

    def fake_apply_damage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal application_count
        application_count += 1
        if application_count == 1:
            match.log.append("Canonical shared-pipeline immunity line.")
        return {}

    original_is_damage_immune = periodic_items.is_damage_immune
    periodic_items.is_damage_immune = lambda target, school: True
    try:
        context = periodic_items.PeriodicItemHandlerContext(
            match=match,
            global_turn=5,
            rng=random.Random(9801),
            player_sids=tuple(match.players),
            turn_context=object(),
            apply_damage=fake_apply_damage,
        )
        periodic_items.periodic_global_damage(activations[0], context)
    finally:
        periodic_items.is_damage_immune = original_is_damage_immune

    fallback_lines = [line for line in match.log if "cannot harm" in line]
    assert application_count == 2
    assert match.log.sequence == 6
    assert "Canonical shared-pipeline immunity line." in match.log
    assert len(fallback_lines) == 1, \
        "A full log must not make retained length look unchanged after an append"
    assert match.players[1][:5] in fallback_lines[0], \
        "The no-log damage application must still receive the handler fallback"
    return True


def scenario_bounded_combat_log_short_duel_is_unchanged() -> bool:
    match = make_match("warrior", "mage", seed=123)
    submit_turn(match, "pass_turn", "pass_turn")

    expected_log = [
        "Turn 1",
        "p1_si uses their bare hands to cast Pass Turn. Passes the turn.",
        "p2_si uses their bare hands to cast Pass Turn. Passes the turn.",
    ]
    assert match.log == expected_log
    assert match.log.sequence == len(expected_log)
    assert len(match.log) < match.log.capacity

    snapshot = SOCKETS.snapshot_for(match, match.players[0])
    assert snapshot["log"] == [
        "Turn 1",
        "Warrior(you) uses their bare hands to cast Pass Turn. Passes the turn.",
        "Mage uses their bare hands to cast Pass Turn. Passes the turn.",
    ]
    assert snapshot["log_length"] == len(expected_log)
    return True


def scenario_bounded_combat_log_new_match_cursor_reset() -> bool:
    html = _detect_duel_html_path().read_text(encoding="utf-8")
    handler_start = html.index('socket.on("duel_role", (role) => {')
    handler_end = html.index("      });", handler_start)
    handler = html[handler_start:handler_end]
    assert "lastLogLength = 0;" in handler
    assert handler.index("lastLogLength = 0;") < handler.index("myRole = role;")

    socket_source = Path(SOCKETS.__file__).read_text(encoding="utf-8")
    role_emit = socket_source.index('socketio.emit("duel_role", "P1"')
    initial_snapshots = socket_source.index("# Send initial snapshots", role_emit)
    assert role_emit < initial_snapshots
    return True


def _application_path() -> Path:
    package_root = Path(SOCKETS.__file__).resolve().parent
    candidates = (package_root / "app.py", package_root.parent.parent / "app.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AssertionError("Unable to locate app.py in a supported checkout layout")


def scenario_socketio_resource_limit_configuration() -> bool:
    defaults = availability.DEFAULT_RESOURCE_LIMITS
    assert defaults.max_retained_log_entries == 500
    assert defaults.socket_max_buffer_bytes == 16 * 1024

    configured = availability.load_resource_limits({
        availability.MAX_RETAINED_LOG_ENTRIES_ENV: "60",
        availability.SOCKET_MAX_BUFFER_BYTES_ENV: "8192",
    })
    assert configured.max_retained_log_entries == 60
    assert configured.socket_max_buffer_bytes == 8192

    invalid_environments = (
        {availability.MAX_RETAINED_LOG_ENTRIES_ENV: "not-an-integer"},
        {availability.MAX_RETAINED_LOG_ENTRIES_ENV: "29"},
        {availability.SOCKET_MAX_BUFFER_BYTES_ENV: "4095"},
    )
    for environment in invalid_environments:
        try:
            availability.load_resource_limits(environment)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid limits must fail startup: {environment}")

    constructor_calls: list[dict[str, Any]] = []
    init_calls: list[tuple[Any, Any]] = []

    class FakeFlask:
        def __init__(self, name: str) -> None:
            self.name = name
            self.config: dict[str, Any] = {}

        def route(self, path: str):
            def register(handler):
                return handler

            return register

    class FakeSocketIO:
        def __init__(self, app: Any, **kwargs: Any) -> None:
            constructor_calls.append({"app": app, **kwargs})

        def run(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("Importing app.py must not start the server")

    fake_flask = types.ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.render_template = lambda template: template
    fake_flask_socketio = types.ModuleType("flask_socketio")
    fake_flask_socketio.SocketIO = FakeSocketIO

    duel_package = sys.modules["games.duel"]
    original_flask = sys.modules.get("flask")
    original_flask_socketio = sys.modules.get("flask_socketio")
    original_init_duel = getattr(duel_package, "init_duel", None)
    had_init_duel = hasattr(duel_package, "init_duel")
    original_limits = availability.RESOURCE_LIMITS
    sys.modules["flask"] = fake_flask
    sys.modules["flask_socketio"] = fake_flask_socketio
    duel_package.init_duel = lambda app, socketio: init_calls.append((app, socketio))
    availability.RESOURCE_LIMITS = defaults
    try:
        runpy.run_path(str(_application_path()), run_name="bounded_log_app_test")
    finally:
        availability.RESOURCE_LIMITS = original_limits
        if had_init_duel:
            duel_package.init_duel = original_init_duel
        else:
            delattr(duel_package, "init_duel")
        if original_flask is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = original_flask
        if original_flask_socketio is None:
            sys.modules.pop("flask_socketio", None)
        else:
            sys.modules["flask_socketio"] = original_flask_socketio

    assert len(constructor_calls) == 1
    assert len(init_calls) == 1
    socket_options = constructor_calls[0]
    assert socket_options["max_http_buffer_size"] == 16 * 1024
    assert socket_options["async_mode"] == "eventlet"
    assert socket_options["cors_allowed_origins"] == "*"
    return True
