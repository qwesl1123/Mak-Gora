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
}
