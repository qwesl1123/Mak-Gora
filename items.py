# games/duel/content/items.py
ITEMS = {
    # Weapons
    "steel_long_sword": {
        "name": "Steel Long Sword",
        "slot": "weapon",
        "mods": {"atk": 5},
        "damage_type": "physical",
    },
    "crusaders_greatsword": {
        "name": "Crusader's Greatsword",
        "slot": "weapon",
        "mods": {"atk": 4, "int": 2},
        "damage_type": "physical",
        "color": "#a335ee",
        "passive": {
            "type": "empower_next_offense",
            "chance": 0.25,
            "multiplier": 1.2,
            "trigger": "on_hit",
            "effect_id": "crusader_empower",
        },
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
    "spear_of_light": {
        "name": "Spear of Light",
        "slot": "weapon",
        "mods": {"atk": 3, "int": 3, "magic_resist": 2},
        "damage_type": "physical",
    },
    "void_blades": {
        "name": "Void Blades",
        "slot": "weapon",
        "mods": {"atk": 3, "int": 2},
        "damage_type": "physical",
        "passive": {"type": "void_blade", "trigger": "on_hit", "int_multiplier": 0.4, "dice": "d4"},
    },
    "thunderfury": {
        "name": "Thunderfury, Blessed Blade of the Windseeker",
        "slot": "weapon",
        "mods": {"atk": 8, "magic_resist": 1},
        "damage_type": "physical",
        "color": "#ff8000",
        "passive": [
            {
                "type": "lightning_blast",
                "trigger": "on_hit",
                "chance": 0.25,
                "scaling": {"atk": 0.5},
                "dice": "d3",
            },
            {
                "type": "heal_on_hit",
                "trigger": "on_hit",
                "chance": 0.25,
                "scaling": {"atk": 0.5},
                "dice": "d3",
            },
        ],
    },
    "dragonwrath": {
        "name": "Dragonwrath, Tarecgosa's Rest",
        "slot": "weapon",
        "mods": {"int": 7},
        "damage_type": "magic",
        "color": "#ff8000",
        "passive": {
            "type": "duplicate_offensive_spell",
            "trigger": "on_hit",
            "chance": 0.2,
        },
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
        "passive": {
            "type": "damage_bonus_above_hp",
            "threshold": 0.7,
            "multiplier": 1.1,
            "trigger": "on_damage",
        },
    },
    "rage_crystal": {
        "name": "Rage Crystal",
        "slot": "trinket",
        "mods": {"rage_max": 20},
        "passive": {
            "type": "damage_bonus_below_hp",
            "threshold": 0.3,
            "multiplier": 1.15,
            "trigger": "on_damage",
        },
    },
}
