# games/duel/engine/rules.py
from typing import Dict

def clamp(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))

def hit_chance(acc: int, eva: int) -> int:
    # simple, tunable
    return clamp(75 + (acc - eva), 15, 95)

def mitigate(raw: int, defense: int) -> int:
    # basic mitigation curve
    return int(raw * (100 / (100 + max(defense, 0))))

def base_damage(atk: int, scaling: float, power: int) -> int:
    # power could be dice roll or derived from it
    return int((atk * scaling) + power)

