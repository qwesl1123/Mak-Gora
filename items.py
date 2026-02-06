# games/duel/content/items.py
ITEMS = {
    # Weapons
    "steel_long_sword": {
        "name": "Steel Long Sword",
        "slot": "weapon",
        "mods": {"atk": 5},
        "damage_type": "physical",
    },
    "spirit_light_sword": {
        "name": "Spirit Light Sword",
        "slot": "weapon",
        "mods": {"atk": 3},
        "damage_type": "physical",
        "passive": {"type": "heal_self", "value": 3, "trigger": "end_of_turn"},
    },
    "wand_of_fire": {
        "name": "Wand of Fire",
        "slot": "weapon",
        "mods": {"int": 6},
        "damage_type": "magic",
        "passive": {"type": "burn", "value": 2, "trigger": "on_hit"},
    },
    "staff_of_immortality": {
        "name": "Staff of Immortality",
        "slot": "weapon",
        "mods": {"int": 3},
        "damage_type": "magic",
        "passive": {"type": "heal_self", "value": 4, "trigger": "end_of_turn"},
    },
    "steel_daggers": {
        "name": "Steel Daggers",
        "slot": "weapon",
        "mods": {"atk": 4},
        "damage_type": "physical",
        "classes": ["rogue"],
        "passive": {"type": "strike_again", "chance": 0.25, "multiplier": 0.4, "trigger": "on_hit"},
    },
    "glock_19": {
        "name": "Glock 19",
        "slot": "weapon",
        "mods": {"atk": 17},
        "damage_type": "physical",
        "classes": ["rogue"],
        "miss_chance": 0.55,
    },
    
    # Armor
    "leather_armor": {
        "name": "Leather Armor",
        "slot": "armor",
        "mods": {"physical_reduction": 3, "magic_resist": 3},
    },
    "cloth_armor": {
        "name": "Cloth Armor",
        "slot": "armor",
        "mods": {"physical_reduction": 1, "magic_resist": 5},
    },
    "plate_armor": {
        "name": "Plate Armor",
        "slot": "armor",
        "mods": {"physical_reduction": 6, "magic_resist": 0},
    },
    
    # Trinkets
    "focus_charm": {
        "name": "Focus Charm",
        "slot": "trinket",
        "mods": {"mp_max": 10},
    },
    "rage_crystal": {
        "name": "Rage Crystal",
        "slot": "trinket",
        "mods": {"rage_max": 20},
    },
}
