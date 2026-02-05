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
    "pass_turn": {
        "name": "Pass Turn",
        "cost": {"energy": 0},
        "dice": None,
        "tags": ["pass"],
        "cooldown": 0,
        "allow_while_stunned": True,
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
            {"id": "hot_streak", "chance": 0.15, "log": "Has HOT STREAK!"}
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
    "victory_rush": {
        "name": "Victory Rush",
        "cost": {"rage": 0},
        "dice": {"type": "d2", "power_on": "roll"},
        "scaling": {"atk": 0.5},
        "damage_type": "physical",
        "tags": ["attack", "physical"],
        "cooldown": 15,
        "classes": ["warrior"],
        "heal_on_hit": 40,
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
    "die_by_sword": {
        "name": "Die by the Sword",
        "cost": {"rage": 0},
        "dice": None,
        "tags": ["defense"],
        "cooldown": 4,
        "classes": ["warrior"],
        "self_effects": [
            {"id": "die_by_sword", "duration": 2, "log": "becomes immune to physical damage."}
        ],
    },
    "execute": {
        "name": "Execute",
        "cost": {"rage": 35},
        "dice": None,
        "flat_damage": 40,
        "damage_type": "physical",
        "tags": ["attack", "physical"],
        "cooldown": 1,
        "classes": ["warrior"],
        "requires_target_hp_below": 0.2,
    },
    "pyroblast": {
        "name": "Pyroblast",
        "cost": {"mp": 20},
        "dice": {"type": "d10", "power_on": "roll"},
        "scaling": {"int": 2.0},
        "damage_type": "magic",
        "tags": ["spell", "attack", "magic"],
        "cooldown": 0,
        "classes": ["mage"],
        "requires_effect": "hot_streak",
        "consume_effect": "hot_streak",
    },
    "iceblock": {
        "name": "Ice Block",
        "cost": {"mp": 0},
        "dice": None,
        "tags": ["defense", "spell"],
        "cooldown": 20,
        "classes": ["mage"],
        "self_effects": [
            {"id": "iceblock", "duration": 3, "log": "encases themselves in ice."}
        ],
    },
    "ring_of_ice": {
        "name": "Ring of Ice",
        "cost": {"mp": 0},
        "dice": None,
        "tags": ["spell", "control"],
        "cooldown": 3,
        "classes": ["mage"],
        "target_effects": [
            {"id": "stunned", "duration": 1, "log": "freezes the enemy solid."}
        ],
    },
}
