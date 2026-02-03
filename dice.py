# games/duel/engine/dice.py
import random
from typing import Tuple

def rng_for(seed: int, turn: int) -> random.Random:
    # deterministic per match seed + turn
    return random.Random(f"{seed}:{turn}")

def roll(dice: str, r: random.Random) -> int:
    # supports "d6", "d20" etc.
    if not dice.startswith("d"):
        raise ValueError("dice must be like 'd20'")
    sides = int(dice[1:])
    return r.randint(1, sides)
