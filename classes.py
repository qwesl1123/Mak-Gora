# games/duel/content/classes.py
CLASSES = {
    "warrior": {
        "name": "Warrior",
        "base_stats": {"atk": 12, "int": 2, "def": 8, "spd": 8, "crit": 5, "acc": 90, "eva": 5},
        "resources": {"hp": 110, "mp": 0, "energy": 0, "rage": 0, "rage_max": 100},
        "resource_display": {"primary": {"id": "rage", "label": "Rage", "color": "var(--rage-red)"}},
    },
    "mage": {
        "name": "Mage",
        "base_stats": {"atk": 4, "int": 14, "def": 4, "spd": 9, "crit": 8, "acc": 95, "eva": 6},
        "resources": {"hp": 90, "mp": 80, "energy": 0, "rage": 0, "rage_max": 0},
        "resource_display": {"primary": {"id": "mp", "label": "Mana", "color": "var(--mana-blue)"}},
    },
    "rogue": {
        "name": "Rogue",
        "base_stats": {"atk": 10, "int": 1, "def": 4, "spd": 9, "crit": 8, "acc": 97, "eva": 20},
        "resources": {"hp": 95, "mp": 0, "energy": 100, "rage": 0, "rage_max": 0},
        "resource_display": {"primary": {"id": "energy", "label": "Energy", "color": "#FFF468"}},
    },
    "warlock": {
        "name": "Warlock",
        "base_stats": {"atk": 3, "int": 15, "def": 7, "spd": 9, "crit": 4, "acc": 96, "eva": 2},
        "resources": {"hp": 110, "mp": 100, "energy": 0, "rage": 0, "rage_max": 0},
        "resource_display": {"primary": {"id": "mp", "label": "Mana", "color": "var(--mana-blue)"}},
    },
    "druid": {
        "name": "Druid",
        "base_stats": {"atk": 6, "int": 6, "def": 5, "spd": 8, "crit": 8, "acc": 95, "eva": 5},
        "resources": {"hp": 100, "mp": 80, "energy": 100, "rage": 0, "rage_max": 100},
        "resource_display": {"primary": {"id": "mp", "label": "Mana", "color": "var(--mana-blue)"}},
        "resource_notes": {
            "base": "Uses Mana with no form applied.",
            "bear_form": "Uses Rage in Bear Form.",
            "cat_form": "Uses Energy in Cat Form.",
            "moonkin_form": "Uses Mana in Moonkin Form.",
            "tree_form": "Uses Mana in Tree Form.",
        },
    },
}
