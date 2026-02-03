# games/duel/sockets.py
from flask import request
from flask_socketio import emit, join_room, leave_room
import time

from . import state
from .engine import resolver
from .content.classes import CLASSES
from .content.items import ITEMS

def snapshot_for(match, viewer_sid):
    """
    Returns a UI-friendly snapshot with friendly/enemy HP/MP/Energy/Rage.
    """
    p1, p2 = match.players
    you = viewer_sid
    enemy = p2 if you == p1 else p1

    def pack(sid):
        ps = match.state.get(sid)
        if not ps or not ps.res:
            return None
        r = ps.res
        return {
            "hp": r.hp, "hp_max": r.hp_max,
            "mp": r.mp, "mp_max": r.mp_max,
            "energy": r.energy, "energy_max": r.energy_max,
            "rage": r.rage, "rage_max": r.rage_max,
        }

    return {
        "phase": match.phase,
        "turn": match.turn,
        "you": pack(you),
        "enemy": pack(enemy),
        "log": match.log[-30:],
    }

def register_duel_socket_handlers(socketio):
    @socketio.on("duel_queue")
    def duel_queue():
        sid = request.sid
        if state.get_match_by_sid(sid):
            emit("duel_system", "Already in a duel.")
            return
        state.enqueue(sid)
        emit("duel_system", "Queued for DUEL...")

        if len(state.duel_queue) >= 2:
            p1 = state.duel_queue.pop(0)
            p2 = state.duel_queue.pop(0)
            seed = int(time.time() * 1000) & 0xFFFFFFFF
            match = state.create_room(p1, p2, seed)

            join_room(match.room_id, sid=p1)
            join_room(match.room_id, sid=p2)

            socketio.emit("duel_system", "Match found. Prep phase: pick class + items.", to=match.room_id)
            socketio.emit("duel_prep_options", {
                "classes": CLASSES,
                "items": ITEMS,
            }, to=match.room_id)

    @socketio.on("duel_prep_submit")
    def duel_prep_submit(payload):
        sid = request.sid
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if match.phase != "prep":
            emit("duel_system", "Prep phase is over.")
            return

        # store picks
        match.picks[sid] = payload  # later: validate schema
        emit("duel_system", "Prep saved. Waiting for opponent...")

        if len(match.picks) == 2:
            match.phase = "combat"
            resolver.apply_prep_build(match)
            socketio.emit("duel_snapshot", snapshot_for(match, match.players[0]), to=match.players[0])
            socketio.emit("duel_snapshot", snapshot_for(match, match.players[1]), to=match.players[1])
            socketio.emit("duel_system", "Combat begins.", to=match.room_id)

    @socketio.on("duel_action")
    def duel_action(payload):
        sid = request.sid
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if match.phase != "combat":
            emit("duel_system", "Not in combat phase.")
            return

        resolver.submit_action(match, sid, payload)
        emit("duel_system", "Action received.")

        if resolver.ready_to_resolve(match):
            resolver.resolve_turn(match)
            socketio.emit("duel_snapshot", snapshot_for(match, match.players[0]), to=match.players[0])
            socketio.emit("duel_snapshot", snapshot_for(match, match.players[1]), to=match.players[1])

    @socketio.on("disconnect")
    def duel_disconnect():
        sid = request.sid
        state.dequeue(sid)
        match = state.get_match_by_sid(sid)
        if not match:
            return
        room_id = match.room_id
        leave_room(room_id, sid=sid)
        socketio.emit("duel_system", "Opponent disconnected. Duel ended.", to=room_id)
        state.cleanup_room(room_id)
