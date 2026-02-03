# games/duel/content/abilities.py
ABILITIES = {
    "basic_attack": {
        "name": "Attack",
        "cost": {"energy": 0},
        "dice": {"type": "d20", "power_on": "roll"},   # later: "roll", "threshold", "sum"
        "scaling": {"atk": 1.0},
        "tags": ["attack"],
    },
    "fireball": {
        "name": "Fireball",
        "cost": {"mp": 10},
        "dice": {"type": "d20", "power_on": "roll"},
        "scaling": {"atk": 1.4},
        "tags": ["spell", "attack"],
    },
    "defend": {
        "name": "Defend",
        "cost": {"energy": 0},
        "dice": None,
        "effect": {"type": "mitigation", "value": 0.5, "duration": 1},
        "tags": ["defense"],
    },
}
