# games/duel/state.py
from typing import Dict, List, Optional
from .engine.models import MatchState

duel_queue: List[str] = []
duel_rooms: Dict[str, MatchState] = {}
sid_to_room: Dict[str, str] = {}

def enqueue(sid: str) -> None:
    if sid not in duel_queue:
        duel_queue.append(sid)

def dequeue(sid: str) -> None:
    if sid in duel_queue:
        duel_queue.remove(sid)

def create_room(p1: str, p2: str, seed: int) -> MatchState:
    room_id = f"duel-{p1[:5]}-{p2[:5]}"
    match = MatchState(room_id=room_id, players=[p1, p2], seed=seed)
    duel_rooms[room_id] = match
    sid_to_room[p1] = room_id
    sid_to_room[p2] = room_id
    return match

def get_match_by_sid(sid: str) -> Optional[MatchState]:
    room = sid_to_room.get(sid)
    if not room:
        return None
    return duel_rooms.get(room)

def cleanup_room(room_id: str) -> None:
    match = duel_rooms.pop(room_id, None)
    if not match:
        return
    for sid in match.players:
        sid_to_room.pop(sid, None)
