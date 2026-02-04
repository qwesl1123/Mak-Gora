# games/duel/content/abilities.py
ABILITIES = {
    "basic_attack": {
        "name": "Basic Attack",
        "cost": {"energy": 0},
        "dice": {"type": "d4", "power_on": "roll"},
        "scaling": {"atk": 0.2},
        "damage_type": "physical",  # Uses weapon's physical atk or base atk
        "tags": ["attack", "physical"],
        "cooldown": 0,
    },
    "fireball": {
        "name": "Fireball",
        "cost": {"mp": 10},
        "dice": {"type": "d6", "power_on": "roll"},
        "scaling": {"int": 0.4},  # Scale with int instead of atk
        "damage_type": "magic",
        "tags": ["spell", "attack", "magic"],
        "cooldown": 1,
        "classes": ["mage"],
        "on_hit_effects": [
            {"id": "hot_streak", "chance": 0.15, "log": "Hot Streak!"}
        ],
    },
    "defend": {
        "name": "Defend",
        "cost": {"energy": 0},
        "dice": None,
        "effect": {"type": "mitigation", "value": 0.5, "duration": 1},
        "tags": ["defense"],
        "cooldown": 2,
    },
    "overpower": {
        "name": "Overpower",
        "cost": {"rage": 0},
        "dice": {"type": "d4", "power_on": "roll"},
        "scaling": {"atk": 0.6},
        "damage_type": "physical",
        "tags": ["attack", "physical"],
        "cooldown": 1,
        "classes": ["warrior"],
        "resource_gain": {"rage": "damage"},
    },
    "mortal_strike": {
        "name": "Mortal Strike",
        "cost": {"rage": 30},
        "dice": {"type": "d6", "power_on": "roll"},
        "scaling": {"atk": 2.0},
        "damage_type": "physical",
        "tags": ["attack", "physical"],
        "cooldown": 2,
        "classes": ["warrior"],
    },
    "pyroblast": {
        "name": "Pyroblast",
        "cost": {"mp": 20},
        "dice": {"type": "d10", "power_on": "roll"},
        "scaling": {"int": 2.0},
        "damage_type": "magic",
        "tags": ["spell", "attack", "magic"],
        "cooldown": 2,
        "classes": ["mage"],
        "requires_effect": "hot_streak",
        "consume_effect": "hot_streak",
    },
}
