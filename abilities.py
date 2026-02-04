# games/duel/content/abilities.py
ABILITIES = {
    "basic_attack": {
        "name": "Attack",
        "cost": {"energy": 0},
        "dice": {"type": "d20", "power_on": "roll"},
        "scaling": {"atk": 1.0},
        "damage_type": "physical",  # Uses weapon's physical atk or base atk
        "tags": ["attack", "physical"],
    },
    "fireball": {
        "name": "Fireball",
        "cost": {"mp": 10},
        "dice": {"type": "d20", "power_on": "roll"},
        "scaling": {"int": 1.4},  # Scale with int instead of atk
        "damage_type": "magic",
        "tags": ["spell", "attack", "magic"],
    },
    "defend": {
        "name": "Defend",
        "cost": {"energy": 0},
        "dice": None,
        "effect": {"type": "mitigation", "value": 0.5, "duration": 1},
        "tags": ["defense"],
    },
}
