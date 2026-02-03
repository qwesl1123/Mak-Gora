# games/duel/engine/resolver.py
from typing import Dict, Any, List, Tuple
from .models import MatchState
from .dice import rng_for, roll
from .rules import base_damage, mitigate
from ..content.abilities import ABILITIES

def apply_prep_build(match: MatchState) -> None:
    """
    Called once when both players have selected class + items.
    Creates PlayerState.res/stats from content + item modifiers.
    """
    # You will implement: merge class base stats + item mods into match.state
    pass

def submit_action(match: MatchState, sid: str, action: Dict[str, Any]) -> None:
    match.submitted[sid] = action

def ready_to_resolve(match: MatchState) -> bool:
    return len(match.submitted) == 2

def resolve_turn(match: MatchState) -> None:
    """
    Resolves both submitted actions simultaneously.
    Appends to match.log and updates match.state.
    Clears submissions and increments match.turn.
    """
    r = rng_for(match.seed, match.turn)
    sids = match.players
    a1 = match.submitted[sids[0]]
    a2 = match.submitted[sids[1]]

    # You will implement:
    # - parse ability id
    # - check costs (hp/mp/energy/rage)
    # - roll dice if needed
    # - compute damage/effects
    # - apply mitigation/buffs
    # - win condition
    match.submitted.clear()
    match.turn += 1
