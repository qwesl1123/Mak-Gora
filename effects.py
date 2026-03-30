# games/duel/engine/effects.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import PlayerState
from ..content.balance import DEFAULTS
from ..content.classes import class_display_name
from .dice import roll
from .rules import base_damage as calc_base_damage, mitigate

RESOLUTION_LAYERS: Dict[str, List[str]] = {
    "pre_action_state": [
        "form",
        "stealth",
        "pet_presence",
        "proc_available",
        "preexisting_buff",
        "preexisting_debuff",
    ],
    "action_selection_modifiers": [
        "cooldown_gate",
        "resource_gate",
        "proc_gate",
        "state_gate",
        "command_validation",
    ],
    "action_denial": [
        "stun",
        "freeze",
        "fear",
        "cannot_act",
        "silence",
    ],
    "pre_resolution_protection": [
        "immune_all",
        "magic_immunity",
        "single_target_protection",
        "stealth_protection",
    ],
    "target_resolution": [
        "redirect",
        "reveal",
        "retarget",
    ],
    "hit_resolution": [
        "accuracy_check",
        "evasion",
        "blink_like",
        "single_target_miss",
    ],
    "damage_modification": [
        "armor_mitigation",
        "magic_resist_mitigation",
        "damage_reduction",
        "damage_amplification",
        "ignore_armor",
        "ignore_magic_resist",
    ],
    "damage_application": [
        "direct_damage",
        "direct_heal",
        "absorb_application",
        "absorb_consumption",
        "resource_restore",
    ],
    "post_damage_reactions": [
        "break_on_damage",
        "lifesteal",
        "rage_on_damage",
        "on_hit_resource_gain",
        "on_damage_trigger",
    ],
    "effect_application": [
        "buff_application",
        "debuff_application",
        "dot_application",
        "hot_application",
        "proc_grant",
        "proc_consume",
        "summon_application",
        "pet_command_application",
        "dispel_application",
        "effect_refresh",
        "effect_removal",
    ],
    "end_of_turn": [
        "dot_tick",
        "hot_tick",
        "resource_tick",
        "pet_phase",
        "duration_decrement",
        "expiry_cleanup",
        "pet_cleanup",
        "winner_check",
    ],
}

ENGINE_RESOLUTION_ORDER: List[str] = [
    "pre_action_state",
    "action_selection_modifiers",
    "action_denial",
    "pre_resolution_protection",
    "target_resolution",
    "hit_resolution",
    "damage_modification",
    "damage_application",
    "post_damage_reactions",
    "effect_application",
    "end_of_turn",
]

EFFECT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "hot_streak": {
        "type": "status",
        "name": "Hot Streak",
        "duration": 3,
        "flags": {"hot_streak": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "die_by_sword": {
        "type": "status",
        "name": "Die by the Sword",
        "duration": 2,
        "flags": {"immune_physical": True},
        "tags": ["immune_part"],
    },
    "die_by_sword_mitigation": {
        "type": "mitigation",
        "name": "Die by the Sword",
        "duration": 2,
        "value": 0.3,
        "tags": ["damage_reduction"],
        "resolution_layer": "damage_modification",
    },
    "iceblock": {
        "type": "status",
        "name": "Ice Block",
        "duration": 3,
        "dispellable": True,
        "school": "magical",
        "subschool": "frost",
        "flags": {"immune_all": True, "stunned": True},
        "tags": ["immune_all"],
        "display": {
            "war_council": True,
            "label": "Immune",
            "color": "#3385cc",
            "priority": 90,
        },
        "regen": {"hp": 10, "mp": 25},
        "resolution_layer": "pre_resolution_protection",
    },
    "cloak_of_shadows": {
        "type": "status",
        "name": "Cloak of Shadows",
        "duration": 2,
        "flags": {"immune_magic": True},
        "tags": ["immune_part"],
        "resolution_layer": "pre_resolution_protection",
    },
    "item_passive_template": {
        "type": "item_passive",
        "name": "Item Passive",
        "duration": 999,
    },
    "burn": {
        "type": "burn",
        "name": "Burn",
        "duration": 999,
        "category": "dot",
        "school": "magical",
        "dispellable": True,
        "tick_damage": 1,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "agony": {
        "type": "dot",
        "name": "Agony",
        "duration": 10,
        "category": "dot",
        "school": "magical",
        "subschool": "shadow",
        "dispellable": False,
        "tick_damage": 1,
        "dot_mode": "ramp",
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "corruption": {
        "type": "dot",
        "name": "Corruption",
        "duration": 8,
        "category": "dot",
        "school": "magical",
        "subschool": "shadow",
        "dispellable": True,
        "tick_damage": 1,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "unstable_affliction": {
        "type": "dot",
        "name": "Unstable Affliction",
        "duration": 10,
        "category": "dot",
        "school": "magical",
        "subschool": "shadow",
        "dispellable": True,
        "tick_damage": 1,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "demonic_circle": {
        "type": "status",
        "name": "Demonic Circle",
        "duration": 999,
        "flags": {"demonic_circle": True},
        "resolution_layer": "pre_action_state",
    },
    "stunned": {
        "type": "status",
        "name": "Stunned",
        "duration": 1,
        "flags": {"stunned": True},
        "tags": ["incapacitating_cc"],
        "cant_act_reason": "stunned",
        "display": {
            "war_council": True,
            "label": "Stunned",
            "color": "#cc3333",
            "priority": 85,
        },
        "resolution_layer": "action_denial",
    },
    "feared": {
        "type": "status",
        "name": "Fear",
        "duration": 2,
        "category": "cc",
        "school": "magical",
        "subschool": "shadow",
        "harmful": True,
        "flags": {"break_on_damage": True},
        "tags": ["incapacitating_cc", "break_on_damage"],
        "cant_act_reason": "feared",
        "display": {
            "war_council": True,
            "label": "Feared",
            "color": "#9B59B6",
            "priority": 83,
        },
        "resolution_layer": "action_denial",
    },
    "ring_of_ice_freeze": {
        "type": "status",
        "name": "Ring of Ice",
        "duration": 2,
        "category": "cc",
        "school": "magical",
        "subschool": "frost",
        "harmful": True,
        "flags": {"stunned": True, "break_on_damage": True},
        "tags": ["incapacitating_cc", "break_on_damage"],
        "cant_act_reason": "frozen",
        "display": {
            "war_council": True,
            "label": "Frozen",
            "color": "#79C7FF",
            "priority": 84,
        },
        "resolution_layer": "action_denial",
    },
    "stealth": {
        "type": "stealth",
        "name": "Stealth",
        "duration": 3,
        "dispellable": True,
        "school": "magical",
        "flags": {"stealthed": True},
        "tags": ["stealth"],
        "break_on_damage_over": 5,
        "display": {
            "war_council": True,
            "label": "Stealthed",
            "color": "#FFF468",
            "priority": 80,
        },
        "resolution_layer": "pre_resolution_protection",
    },
    "blink": {
        "type": "status",
        "name": "Blink",
        "duration": 2,
        "flags": {"blinked": True, "untargetable": True},
        "tags": ["blink_like"],
        "resolution_layer": "hit_resolution",
    },
    "demonic_gateway": {
        "type": "status",
        "name": "Demonic Gateway",
        "duration": 2,
        "flags": {"blinked": True, "untargetable": True},
        "miss_log": "Target fled through the portal — Miss.",
        "tags": ["blink_like"],
        "resolution_layer": "hit_resolution",
    },
    "teleport": {
        "type": "status",
        "name": "Demonic Circle: Teleport",
        "duration": 2,
        "flags": {"blinked": True, "untargetable": True},
        "miss_log": "Target returned to their dark ward — Miss.",
        "tags": ["blink_like"],
        "resolution_layer": "hit_resolution",
    },
    "disengage": {
        "type": "status",
        "name": "Disengage",
        "duration": 1,
        "flags": {"blinked": True, "untargetable": True, "incoming_single_target_miss": True},
        "miss_log": "Target leaps away — Miss.",
        "tags": ["blink_like"],
        "display": {
            "war_council": True,
            "label": "Disengaged",
            "color": "#AAD372",
            "priority": 78,
        },
        "resolution_layer": "hit_resolution",
    },
    "evasion": {
        "type": "status",
        "name": "Evasion",
        "duration": 2,
        "flags": {"evade_all": True},
        "resolution_layer": "hit_resolution",
    },
    "ambush": {
        "type": "status",
        "name": "Ambush",
        "duration": 2,
        "flags": {"ambush_ready": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "thistle_tea": {
        "type": "status",
        "name": "Thistle Tea",
        "duration": 3,
        "regen": {"energy": 30},
    },
    "crusader_empower": {
        "type": "status",
        "name": "Crusader's Might",
        "duration": 999,
        "damage_mult": 1.2,
        "flags": {"empower_next_offense": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "paladin_final_verdict_empowered": {
        "type": "status",
        "name": "Final Verdict Empowered",
        "duration": 999,
        "flags": {"paladin_final_verdict_empowered": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "dark_pact": {
        "type": "status",
        "name": "Dark Pact",
        "duration": 3,
        "category": "absorb",
        "dispellable": True,
        "dispel_kind": "magical",
        "school": "magical",
        "tags": ["absorb"],
        "resolution_layer": "damage_application",
    },
    "unending_resolve": {
        "type": "status",
        "name": "Unending Resolve",
        "duration": 1,
        "dispellable": True,
        "school": "magical",
        "flags": {"immune_all": True},
        "tags": ["immune_all"],
        "display": {
            "war_council": True,
            "label": "Immune",
            "color": "#7A5AF8",
            "priority": 90,
        },
        "resolution_layer": "pre_resolution_protection",
    },
    "divine_shield": {
        "type": "status",
        "name": "Divine Shield",
        "duration": 2,
        "dispellable": True,
        "school": "magical",
        "subschool": "holy",
        "flags": {"immune_all": True},
        "tags": ["immune_all"],
        "display": {
            "war_council": True,
            "label": "Immune",
            "color": "#F48CBA",
            "priority": 90,
        },
        "resolution_layer": "pre_resolution_protection",
    },
    "shield_of_vengeance": {
        "type": "status",
        "name": "Shield of Vengeance",
        "duration": 3,
        "category": "absorb",
        "dispellable": True,
        "school": "magical",
        "subschool": "holy",
        "flags": {"shield_of_vengeance": True},
        "absorbed": 0,
        "tags": ["absorb"],
        "resolution_layer": "damage_application",
    },
    "ignore_pain": {
        "type": "status",
        "name": "Ignore Pain",
        "duration": 8,
        "category": "absorb",
        "tags": ["absorb"],
        "resolution_layer": "damage_application",
    },
    "shielded": {
        "type": "status",
        "name": "Shielded",
        "duration": 2,
        "category": "absorb",
        "tags": ["absorb"],
        "resolution_layer": "damage_application",
    },
    "power_word_shield": {
        "type": "status",
        "name": "Power Word: Shield",
        "duration": 8,
        "category": "absorb",
        "dispellable": True,
        "school": "magical",
        "subschool": "holy",
        "tags": ["absorb"],
        "resolution_layer": "damage_application",
    },
    "ice_barrier": {
        "type": "status",
        "name": "Ice Barrier",
        "duration": 8,
        "category": "absorb",
        "dispellable": True,
        "school": "magical",
        "subschool": "frost",
        "tags": ["absorb"],
        "resolution_layer": "damage_application",
    },
    "mind_blast_empowered": {
        "type": "status",
        "name": "Mind Blast Empowered",
        "duration": 999,
        "flags": {"mind_blast_empowered": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "shadowy_insight": {
        "type": "status",
        "name": "Shadowy Insight",
        "duration": 999,
        "flags": {"shadowy_insight": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "vampiric_touch": {
        "type": "dot",
        "name": "Vampiric Touch",
        "duration": 6,
        "category": "dot",
        "school": "magical",
        "subschool": "shadow",
        "dispellable": True,
        "tick_damage": 1,
        "lifesteal_pct": 0.4,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "devouring_plague": {
        "type": "dot",
        "name": "Devouring Plague",
        "duration": 4,
        "category": "dot",
        "school": "magical",
        "subschool": "shadow",
        "dispellable": True,
        "tick_damage": 1,
        "lifesteal_pct": 1.0,
        "refresh_only": True,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "pain_suppression": {
        "type": "mitigation",
        "name": "Pain Suppression",
        "duration": 3,
        "value": 0.4,
        "dispellable": True,
        "school": "magical",
        "subschool": "holy",
        "tags": ["damage_reduction"],
        "resolution_layer": "damage_modification",
    },
    "mindgames": {
        "type": "status",
        "name": "Mindgames",
        "duration": 1,
        "category": "debuff",
        "school": "magical",
        "subschool": "shadow",
        "harmful": True,
        "flags": {"mindgames": True},
    },
    "shadowfiend": {
        "type": "status",
        "name": "Shadowfiend",
        "duration": 5,
        "school": "magical",
        "subschool": "shadow",
        "flags": {"shadowfiend": True},
        "resolution_layer": "effect_application",
    },
    "dragon_roar_bleed": {
        "type": "dot",
        "name": "Dragon Roar Bleed",
        "duration": 4,
        "category": "dot",
        "school": "physical",
        "dispellable": False,
        "tick_damage": 1,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "avenging_wrath": {
        "type": "status",
        "name": "Avenging Wrath",
        "duration": 4,
        "dispellable": True,
        "school": "magical",
        "subschool": "holy",
        "outgoing_damage_mult": 1.2,
        "flags": {"avenging_wrath": True},
        "display": {
            "war_council": True,
            "label": "Empowered",
            "color": "#F48CBA",
            "priority": 70,
        },
    },
    "bear_form": {
        "type": "form",
        "name": "Bear Form",
        "duration": 999,
        "flags": {"form": "bear", "bear_form": True},
        "tags": ["form"],
        "display": {
            "war_council": True,
            "label": "Bear Form",
            "color": "#FF7C0A",
            "priority": 75,
        },
        "resolution_layer": "pre_action_state",
    },
    "bear_form_stats": {
        "type": "stat_mods",
        "name": "Bear Form",
        "duration": 999,
        "mods": {"atk": 1, "def": 15, "eva": -5, "physical_reduction": 3},
    },
    "cat_form": {
        "type": "form",
        "name": "Cat Form",
        "duration": 999,
        "flags": {"form": "cat", "cat_form": True},
        "tags": ["form"],
        "display": {
            "war_council": True,
            "label": "Cat Form",
            "color": "#FF7C0A",
            "priority": 75,
        },
        "resolution_layer": "pre_action_state",
    },
    "cat_form_stats": {
        "type": "stat_mods",
        "name": "Cat Form",
        "duration": 999,
        "mods": {"atk": 3, "def": 1, "crit": 2, "acc": 2, "eva": 2},
    },
    "moonkin_form": {
        "type": "form",
        "name": "Moonkin Form",
        "duration": 999,
        "flags": {"form": "moonkin", "moonkin_form": True},
        "tags": ["form"],
        "display": {
            "war_council": True,
            "label": "Moonkin Form",
            "color": "#FF7C0A",
            "priority": 75,
        },
        "resolution_layer": "pre_action_state",
    },
    "moonkin_form_stats": {
        "type": "stat_mods",
        "name": "Moonkin Form",
        "duration": 999,
        "mods": {"int": 2, "crit": 1, "acc": 5},
    },
    "tree_form": {
        "type": "form",
        "name": "Tree Form",
        "duration": 999,
        "flags": {"form": "tree", "tree_form": True},
        "tags": ["form"],
        "display": {
            "war_council": True,
            "label": "Tree Form",
            "color": "#FF7C0A",
            "priority": 75,
        },
        "resolution_layer": "pre_action_state",
    },
    "tree_form_stats": {
        "type": "stat_mods",
        "name": "Tree Form",
        "duration": 999,
        "mods": {"int": 2, "def": 3, "magic_resist": 2},
    },
    "rip_ready": {
        "type": "status",
        "name": "Rip Ready",
        "duration": 999,
        "flags": {"rip_ready": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "starfire_ready": {
        "type": "status",
        "name": "Starfire Ready",
        "duration": 999,
        "flags": {"starfire_ready": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "barkskin": {
        "type": "mitigation",
        "name": "Barkskin",
        "duration": 3,
        "value": 0.35,
        "tags": ["damage_reduction"],
        "resolution_layer": "damage_modification",
    },
    "ironfur": {
        "type": "stat_mods",
        "name": "Ironfur",
        "duration": 4,
        "mods": {"physical_reduction": 3},
    },
    "typhoon_disoriented": {
        "type": "status",
        "name": "Typhoon",
        "duration": 2,
        "school": "magical",
        "subschool": "nature",
        "flags": {"forced_miss": True},
    },
    "cyclone": {
        "type": "status",
        "name": "Cyclone",
        "duration": 2,
        "school": "magical",
        "subschool": "nature",
        "flags": {"cycloned": True, "stunned": True, "immune_all": True},
        "tags": ["incapacitating_cc"],
        "resolution_layer": "action_denial",
    },
    "frenzied_regeneration": {
        "type": "status",
        "name": "Frenzied Regeneration",
        "duration": 4,
        "dispellable": False,
        "regen": {"hp": 0},
        "tags": ["hot"],
        "resolution_layer": "end_of_turn",
    },
    "regrowth": {
        "type": "status",
        "name": "Regrowth",
        "duration": 5,
        "category": "hot",
        "dispellable": True,
        "school": "magical",
        "subschool": "nature",
        "regen": {"hp": 0},
        "tags": ["hot"],
        "resolution_layer": "end_of_turn",
    },
    "wildfire_burn": {
        "type": "dot",
        "name": "Wildfire Burn",
        "duration": 2,
        "category": "dot",
        "school": "magical",
        "subschool": "fire",
        "dispellable": True,
        "tick_damage": 1,
        "tags": ["dot"],
        "resolution_layer": "end_of_turn",
    },
    "arcane_shot_proc": {
        "type": "status",
        "name": "Arcane Shot",
        "duration": 2,
        "flags": {"arcane_shot_ready": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "raptor_strike_proc": {
        "type": "status",
        "name": "Raptor Strike",
        "duration": 2,
        "flags": {"raptor_strike_ready": True},
        "tags": ["proc"],
        "resolution_layer": "action_selection_modifiers",
    },
    "freezing_trap_freeze": {
        "type": "status",
        "name": "Freezing Trap",
        "duration": 2,
        "category": "cc",
        "school": "magical",
        "subschool": "frost",
        "harmful": True,
        "flags": {"stunned": True, "break_on_damage": True},
        "tags": ["incapacitating_cc", "break_on_damage"],
        "cant_act_reason": "frozen",
        "display": {
            "war_council": True,
            "label": "Frozen",
            "color": "#79C7FF",
            "priority": 84,
        },
        "resolution_layer": "action_denial",
    },
    "aspect_of_turtle": {
        "type": "mitigation",
        "name": "Aspect of the Turtle",
        "duration": 2,
        "value": 0.3,
        "school": "magical",
        "flags": {"incoming_single_target_miss": True, "disable_attacks": True},
        "regen": {"mp": 10},
        "tags": ["damage_reduction"],
        "resolution_layer": "pre_resolution_protection",
    },
    "blocking_defence": {
        "type": "status",
        "name": "Blocking Defence",
        "duration": 1,
        "flags": {"redirect_single_target_to_pet": True},
        "tags": ["redirect"],
        "display": {
            "war_council": True,
            "label": "Guarded",
            "color": "#B57A42",
            "priority": 72,
        },
        "resolution_layer": "target_resolution",
    },
}

FORM_EFFECT_IDS = ("bear_form", "cat_form", "moonkin_form", "tree_form")
FORM_STAT_EFFECT_IDS = (
    "bear_form_stats",
    "cat_form_stats",
    "moonkin_form_stats",
    "tree_form_stats",
)
FORM_STAT_MAP = {
    "bear_form": "bear_form_stats",
    "cat_form": "cat_form_stats",
    "moonkin_form": "moonkin_form_stats",
    "tree_form": "tree_form_stats",
}
FORM_CLEAR_EFFECT_IDS = ("stealth", "rip_ready", "starfire_ready")


def is_permanent(effect: Dict[str, Any]) -> bool:
    """Effects we do not tick down with durations (until you add cleanse/removal)."""
    return effect.get("type") in ("item_passive", "burn") or effect.get("id") == "demonic_circle"


def tick_durations(effects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Decrement duration for non-permanent effects; drop expired ones."""
    new_list: List[Dict[str, Any]] = []
    for e in effects:
        if is_permanent(e):
            new_list.append(e)
            continue
        d = int(e.get("duration", 0) or 0) - 1
        if d > 0:
            e2 = dict(e)
            e2["duration"] = d
            new_list.append(e2)
    return new_list


def tick_player_effects(ps: PlayerState) -> None:
    """Tick effect durations while ensuring expiry goes through remove_effect()."""
    expired_ids: List[str] = []
    for effect in list(ps.effects):
        if is_permanent(effect):
            continue
        next_duration = int(effect.get("duration", 0) or 0) - 1
        if next_duration > 0:
            effect["duration"] = next_duration
            continue
        effect_id = effect.get("id")
        if effect_id:
            expired_ids.append(effect_id)
    for effect_id in expired_ids:
        remove_effect(ps, effect_id)


def build_effect(effect_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if effect_id not in EFFECT_TEMPLATES:
        return {}
    base = EFFECT_TEMPLATES.get(effect_id, {})
    effect = dict(base)
    effect["id"] = effect_id
    if overrides:
        effect.update(overrides)
    return effect


def effect_template(effect_id: str) -> Dict[str, Any]:
    return EFFECT_TEMPLATES.get(effect_id, {})


def normalize_school(school: Any) -> str:
    normalized = str(school or "").lower()
    if normalized == "magic":
        return "magical"
    return normalized


def is_harmful_effect(effect: Dict[str, Any]) -> bool:
    """Classify whether an effect negatively impacts its target."""
    if "harmful" in effect:
        return bool(effect.get("harmful"))

    category = str(effect.get("category") or "").lower()
    if category in {"dot", "debuff", "cc"}:
        return True
    if category in {"hot", "buff", "absorb"}:
        return False

    flags = effect.get("flags", {}) or {}
    if flags.get("stunned"):
        return True
    return False


def is_magical_harmful_effect(effect: Dict[str, Any]) -> bool:
    school = normalize_school(effect.get("school"))
    return school == "magical" and is_harmful_effect(effect)


def is_dispellable_by(
    effect_or_id: str | Dict[str, Any],
    dispel_type: str = "mass_dispel",
    kind: str = "magical",
) -> bool:
    effect: Dict[str, Any] = (
        build_effect(effect_or_id) if isinstance(effect_or_id, str) else dict(effect_or_id)
    )
    if not effect:
        return False

    if not bool(effect.get("dispellable", False)):
        return False

    normalized_kind = str(kind).lower()
    if normalized_kind == "magic":
        normalized_kind = "magical"

    dispel_kind_raw = effect.get("dispel_kind") or effect.get("school")
    if dispel_kind_raw is None:
        return False
    dispel_kind = str(dispel_kind_raw).lower()
    if dispel_kind == "magic":
        dispel_kind = "magical"
    if dispel_kind != normalized_kind:
        return False

    allowed_dispellers = effect.get("dispel_by")
    if allowed_dispellers is None:
        return True
    if isinstance(allowed_dispellers, (list, tuple, set)):
        return dispel_type in allowed_dispellers
    return dispel_type == str(allowed_dispellers)


def apply_effect_by_id(
    target,
    effect_id: str,
    log: Optional[List[str]] = None,
    label: str = "",
    log_message: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    effect = build_effect(effect_id, overrides=overrides)
    if not effect:
        return
    if effect.get("category") == "absorb":
        remove_effect(target, effect_id)
    target.effects.append(effect)
    if log is not None and log_message:
        prefix = f"{label} " if label else ""
        log.append(f"{prefix}{log_message}")


def has_effect(target, effect_id: str) -> bool:
    return any(effect.get("id") == effect_id for effect in target.effects)


def effect_tags(effect: Any) -> set[str]:
    if not isinstance(effect, dict):
        return set()
    raw_tags = effect.get("tags")
    if raw_tags is None:
        effect_id = effect.get("id")
        template = EFFECT_TEMPLATES.get(effect_id) if effect_id else None
        raw_tags = (template or {}).get("tags")
    if not raw_tags:
        return set()
    if isinstance(raw_tags, str):
        return {raw_tags}
    if isinstance(raw_tags, (list, tuple, set, frozenset)):
        return {str(tag) for tag in raw_tags if tag}
    return set()


def effect_has_tag(effect: Any, tag: str) -> bool:
    return tag in effect_tags(effect)


def is_valid_resolution_layer(layer: str) -> bool:
    return layer in RESOLUTION_LAYERS


def effect_resolution_layer(effect: Any) -> Optional[str]:
    if not isinstance(effect, dict):
        return None

    layer = effect.get("resolution_layer")
    if isinstance(layer, str) and is_valid_resolution_layer(layer):
        return layer

    effect_id = effect.get("id")
    if not effect_id:
        return None
    template = EFFECT_TEMPLATES.get(effect_id) or {}
    template_layer = template.get("resolution_layer")
    if isinstance(template_layer, str) and is_valid_resolution_layer(template_layer):
        return template_layer
    return None


def target_effects_in_resolution_layer(target: Any, layer: str) -> list[dict]:
    if not is_valid_resolution_layer(layer):
        return []
    return [
        effect
        for effect in list(getattr(target, "effects", []) or [])
        if effect_resolution_layer(effect) == layer
    ]


def target_has_resolution_layer(target: Any, layer: str) -> bool:
    return any(effect_resolution_layer(effect) == layer for effect in list(getattr(target, "effects", []) or []))


def target_effects_with_tag(target: Any, tag: str) -> list[dict]:
    return [effect for effect in list(getattr(target, "effects", []) or []) if effect_has_tag(effect, tag)]


def target_has_effect_tag(target: Any, tag: str) -> bool:
    return any(effect_has_tag(effect, tag) for effect in list(getattr(target, "effects", []) or []))


def target_has_any_effect_tag(target: Any, tags: list[str] | tuple[str, ...] | set[str]) -> bool:
    return any(target_has_effect_tag(target, tag) for tag in tags)


def target_has_incapacitating_cc(target: Any) -> bool:
    return target_has_effect_tag(target, "incapacitating_cc")


def target_has_break_on_damage_cc(target: Any) -> bool:
    return any(
        effect_has_tag(effect, "incapacitating_cc") and effect_has_tag(effect, "break_on_damage")
        for effect in list(getattr(target, "effects", []) or [])
    )


def target_has_blink_like_state(target: Any) -> bool:
    return target_has_effect_tag(target, "blink_like")


def target_has_redirect_state(target: Any) -> bool:
    return target_has_effect_tag(target, "redirect")


def is_in_form(target, form_id: str) -> bool:
    return has_effect(target, form_id)


def current_form_id(target) -> Optional[str]:
    for form_id in FORM_EFFECT_IDS:
        if has_effect(target, form_id):
            return form_id
    return None


def clear_forms(target) -> None:
    remove_ids: List[str] = []
    for effect in target.effects:
        effect_id = effect.get("id")
        if effect_id in FORM_EFFECT_IDS or effect_id in FORM_STAT_EFFECT_IDS or effect_id in FORM_CLEAR_EFFECT_IDS:
            remove_ids.append(effect_id)
    for effect_id in remove_ids:
        remove_effect(target, effect_id)


def apply_form(
    target,
    form_id: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    clear_forms(target)
    apply_effect_by_id(target, form_id, overrides=overrides)
    stat_effect_id = FORM_STAT_MAP.get(form_id)
    if stat_effect_id:
        apply_effect_by_id(target, stat_effect_id)


def remove_effect(target, effect_id: str) -> None:
    target.effects = [effect for effect in target.effects if effect.get("id") != effect_id]
    target_res = getattr(target, "res", None)
    if target_res and effect_id in target_res.absorbs:
        del target.res.absorbs[effect_id]


def remove_stealth(target) -> None:
    remove_effect(target, "stealth")


def break_stealth_on_damage(target, damage: int) -> None:
    if damage <= 0:
        return
    for effect in target.effects:
        if effect.get("id") != "stealth":
            continue
        threshold = effect.get("break_on_damage_over")
        if threshold is None or damage > int(threshold):
            remove_stealth(target)
        return


def break_effects_on_damage(target) -> list[str]:
    removed: list[str] = []
    for effect in list(getattr(target, "effects", []) or []):
        has_legacy_flag = (effect.get("flags", {}) or {}).get("break_on_damage")
        if not (effect_has_tag(effect, "break_on_damage") or has_legacy_flag):
            continue
        effect_id = effect.get("id")
        if not effect_id:
            continue
        remove_effect(target, effect_id)
        removed.append(effect_id)
    return removed


def has_flag(target, flag: str) -> bool:
    return any(effect.get("flags", {}).get(flag) for effect in target.effects)


def is_stunned(target) -> bool:
    return has_flag(target, "stunned")


def cannot_act(target) -> bool:
    return get_cant_act_reason(target) is not None


def get_cant_act_reason(target) -> Optional[str]:
    priority = {"stunned": 0, "frozen": 1, "feared": 2, "terrified": 3}
    best_reason: Optional[str] = None
    best_rank = len(priority)

    for effect in target.effects:
        reason = effect.get("cant_act_reason")
        if not reason and effect.get("flags", {}).get("stunned"):
            reason = "stunned"
        if reason not in priority:
            continue
        rank = priority[reason]
        if rank < best_rank:
            best_rank = rank
            best_reason = reason

    return best_reason


def is_stealthed(target) -> bool:
    return has_flag(target, "stealthed")


def is_immune_all(target) -> bool:
    return has_flag(target, "immune_all")


def is_damage_immune(target: PlayerState, damage_type: str) -> bool:
    if is_immune_all(target):
        return True
    if damage_type == "physical" and has_flag(target, "immune_physical"):
        return True
    if damage_type == "magic" and has_flag(target, "immune_magic"):
        return True
    return False


def modify_stat(target: PlayerState, stat: str, base_value: int) -> int:
    """Apply stat modifiers from effects; supports flat + mult."""
    value = base_value
    multiplier = 1.0
    for effect in target.effects:
        effect_type = effect.get("type")
        if effect_type == "stat_mod":
            if effect.get("stat") != stat:
                continue
            value += int(effect.get("flat", 0) or 0)
            multiplier *= float(effect.get("mult", 1.0) or 1.0)
        elif effect_type == "stat_mods":
            mods = effect.get("mods", {}) or {}
            if stat in mods:
                value += int(mods.get(stat, 0) or 0)
    return int(value * multiplier)


def mitigation_effective_stat(
    target: PlayerState,
    school: str,
    *,
    ignore_armor: bool = False,
    ignore_magic_resist: bool = False,
) -> int:
    normalized = normalize_school(school)
    defense = modify_stat(target, "def", target.stats.get("def", 0))
    if normalized == "physical":
        armor = 0 if ignore_armor else modify_stat(target, "physical_reduction", target.stats.get("physical_reduction", 0))
        return max(0, defense + armor)
    magic_resist = 0 if ignore_magic_resist else modify_stat(target, "magic_resist", target.stats.get("magic_resist", 0))
    return max(0, defense + magic_resist)


def mitigate_damage(
    raw: int,
    target: PlayerState,
    school: str,
    *,
    ignore_armor: bool = False,
    ignore_magic_resist: bool = False,
) -> int:
    effective_stat = mitigation_effective_stat(
        target,
        school,
        ignore_armor=ignore_armor,
        ignore_magic_resist=ignore_magic_resist,
    )
    reduced = mitigate(raw, effective_stat)
    return int(reduced * mitigation_multiplier(target))


def mitigation_multiplier(target: PlayerState) -> float:
    """Sum mitigation effects and cap at 80%. Returns multiplier for damage."""
    total = 0.0
    for effect in target.effects:
        is_tagged_mitigation = effect_has_tag(effect, "damage_reduction")
        is_legacy_mitigation = effect.get("type") == "mitigation"
        if is_tagged_mitigation or is_legacy_mitigation:
            total += float(effect.get("value", 0) or 0.0)
    total = max(0.0, min(total, 0.8))
    return 1.0 - total


def apply_burn(
    target: PlayerState,
    value: int,
    source_item: str = "Unknown",
    duration: int = 999,
    source_sid: Optional[str] = None,
) -> None:
    """Attach a burn DoT to the target (matches your existing burn shape)."""
    for effect in target.effects:
        if effect.get("id") == "burn" or effect.get("type") == "burn":
            effect["category"] = "dot"
            effect["school"] = "magical"
            effect["dispellable"] = True
            effect["tick_damage"] = max(int(effect.get("tick_damage", effect.get("value", 0)) or 0), int(value))
            effect["duration"] = max(int(effect.get("duration", 0) or 0), int(duration))
            effect["source"] = str(source_item)
            if source_sid is not None:
                effect["source_sid"] = source_sid
            return
    apply_effect_by_id(
        target,
        "burn",
        overrides={
            "tick_damage": int(value),
            "duration": int(duration),
            "source": str(source_item),
            "source_sid": source_sid,
        },
    )


def dispel_effects(ps: PlayerState, *, category: Optional[str] = None, school: Optional[str] = None) -> int:
    to_remove: List[str] = []
    for effect in ps.effects:
        effect_category = effect.get("category")
        effect_school = effect.get("school")
        matches_category = category is None or effect_category == category
        matches_school = school is None or effect_school == school
        is_dispellable = bool(effect.get("dispellable", False))
        if matches_category and matches_school and is_dispellable:
            effect_id = effect.get("id")
            if effect_id:
                to_remove.append(effect_id)
    for effect_id in to_remove:
        remove_effect(ps, effect_id)
    return len(to_remove)


def absorb_total(ps: PlayerState) -> int:
    if not ps.res:
        return 0
    return sum(max(0, int(layer.get("remaining", 0) or 0)) for layer in ps.res.absorbs.values())


def refresh_dot_effect(
    target: PlayerState,
    effect_id: str,
    *,
    duration: int,
    tick_damage: int,
    source_sid: Optional[str] = None,
    school: Optional[str] = None,
    subschool: Optional[str] = None,
) -> bool:
    for effect in target.effects:
        if effect.get("id") != effect_id:
            continue
        effect["duration"] = int(duration)
        effect["tick_damage"] = int(tick_damage)
        if source_sid is not None:
            effect["source_sid"] = source_sid
        if school is not None:
            effect["school"] = normalize_school(school)
            if effect["school"] != "magical":
                effect["subschool"] = None
        if subschool is not None and effect.get("school") == "magical":
            effect["subschool"] = subschool
        return True
    return False


def add_absorb(
    ps: PlayerState,
    amount: int,
    source_item: Optional[str] = None,
    cap: Optional[int] = None,
    source_name: Optional[str] = None,
    effect_id: Optional[str] = None,
) -> int:
    if not ps.res:
        return 0
    value = int(amount)
    if value == 0:
        return absorb_total(ps)

    layer_id = effect_id or (source_name or source_item or "shield").lower().replace(" ", "_")
    next_value = value
    if cap is not None:
        next_value = max(0, min(next_value, int(cap)))
    else:
        next_value = max(0, next_value)

    ps.res.absorbs[layer_id] = {
        "name": source_name or source_item or "Shield",
        "remaining": int(next_value),
        "max": int(next_value),
    }
    return absorb_total(ps)


def get_effect(target: PlayerState, effect_id: str) -> Optional[Dict[str, Any]]:
    for effect in target.effects:
        if effect.get("id") == effect_id:
            return effect
    return None


def outgoing_damage_multiplier(target: PlayerState) -> float:
    mult = 1.0
    for effect in target.effects:
        mult *= float(effect.get("outgoing_damage_mult", 1.0) or 1.0)
    return mult


def consume_absorbs(ps: PlayerState, incoming: int) -> tuple[int, int, List[Dict[str, Any]]]:
    if not ps.res:
        return incoming, 0, []
    incoming_value = max(0, int(incoming))
    if incoming_value <= 0 or not ps.res.absorbs:
        return incoming_value, 0, []

    remaining = incoming_value
    absorbed_total = 0
    breakdown: List[Dict[str, Any]] = []

    for effect_id in list(ps.res.absorbs.keys()):
        if remaining <= 0:
            break
        layer = ps.res.absorbs.get(effect_id)
        if not layer:
            continue
        current = max(0, int(layer.get("remaining", 0) or 0))
        if current <= 0:
            del ps.res.absorbs[effect_id]
            continue
        absorbed = min(current, remaining)
        layer["remaining"] = current - absorbed
        remaining -= absorbed
        absorbed_total += absorbed
        if absorbed > 0:
            breakdown.append({"effect_id": effect_id, "name": layer.get("name", "Shield"), "amount": absorbed})
            if effect_id == "shield_of_vengeance":
                shield_fx = get_effect(ps, "shield_of_vengeance")
                if shield_fx:
                    shield_fx["absorbed"] = int(shield_fx.get("absorbed", 0) or 0) + int(absorbed)
        if int(layer.get("remaining", 0) or 0) <= 0:
            del ps.res.absorbs[effect_id]

    return remaining, absorbed_total, breakdown


def trigger_on_hit_passives(
    attacker: PlayerState,
    target: PlayerState,
    base_damage: int,
    damage_type: str,
    rng,
    ability: Optional[Dict[str, Any]] = None,
    include_strike_again: bool = True,
    only_strike_again: bool = False,
) -> tuple[int, List[str], int, List[Dict[str, Any]]]:
    """Run attacker item passives that trigger on_hit."""
    bonus_damage = 0
    bonus_healing = 0
    log_lines: List[str] = []
    damage_events: List[Dict[str, Any]] = []
    for effect in attacker.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "on_hit":
            continue

        passive_type = passive.get("type")
        if only_strike_again and passive_type != "strike_again":
            continue

        if passive_type == "burn":
            burn_value = int(passive.get("value", 0) or 0)
            if burn_value > 0:
                apply_burn(
                    target,
                    value=burn_value,
                    source_item=str(effect.get("source_item", "Unknown")),
                    duration=999,
                    source_sid=attacker.sid,
                )
                log_lines.append(
                    f"{attacker.sid[:5]} scorches the target with {effect.get('source_item', 'item')} ({burn_value} damage/turn)."
                )
        elif passive_type == "strike_again":
            if not include_strike_again:
                continue
            chance = float(passive.get("chance", 0) or 0)
            multiplier = float(passive.get("multiplier", 0) or 0)
            if base_damage > 0 and chance > 0 and multiplier > 0 and rng.random() <= chance:
                extra = int(base_damage * multiplier)
                if extra > 0:
                    bonus_damage += extra
                    damage_events.append(
                        {
                            "incoming": extra,
                            "school": "physical",
                            "subschool": None,
                            "log_template": (
                                f"{attacker.sid[:5]} strikes again with {effect.get('source_item', 'item')} "
                                "for __DMG_0__ bonus damage."
                            ),
                        }
                    )
        elif passive_type == "void_blade":
            if base_damage <= 0:
                continue
            int_multiplier = float(passive.get("int_multiplier", 0.4) or 0.4)
            dice = passive.get("dice", "d4")
            roll_power = roll(dice, rng) if dice else 0
            intellect = modify_stat(attacker, "int", attacker.stats.get("int", 0))
            raw = int(intellect * int_multiplier) + int(roll_power)
            if raw <= 0:
                continue
            reduced = mitigate_damage(raw, target, "magic")
            if is_damage_immune(target, "magic"):
                reduced = 0
            if reduced > 0:
                bonus_damage += reduced
                damage_events.append(
                    {
                        "incoming": reduced,
                        "school": normalize_school(passive.get("school") or "magical"),
                        "subschool": passive.get("subschool"),
                        "log_template": (
                            f"{attacker.sid[:5]} calls upon the void with {effect.get('source_item', 'item')}. "
                            f"Roll {dice} = {roll_power}. Deals __DMG_0__ magic damage."
                        ),
                    }
                )
        elif passive_type == "lightning_blast":
            chance = float(passive.get("chance", 0) or 0)
            scaling = passive.get("scaling", {}) or {}
            dice = passive.get("dice", "d3")
            if chance <= 0 or rng.random() > chance:
                continue
            roll_power = roll(dice, rng) if dice else 0
            raw = 0
            if "atk" in scaling:
                raw = calc_base_damage(
                    modify_stat(attacker, "atk", attacker.stats.get("atk", 0)),
                    scaling["atk"],
                    roll_power,
                )
            elif "int" in scaling:
                raw = calc_base_damage(
                    modify_stat(attacker, "int", attacker.stats.get("int", 0)),
                    scaling["int"],
                    roll_power,
                )
            if raw <= 0:
                continue
            reduced = mitigate_damage(raw, target, "magic")
            if is_damage_immune(target, "magic"):
                reduced = 0
            if reduced > 0:
                bonus_damage += reduced
                damage_events.append(
                    {
                        "incoming": reduced,
                        "school": normalize_school(passive.get("school") or "magical"),
                        "subschool": passive.get("subschool"),
                        "log_template": (
                            f"{attacker.sid[:5]} blasts the target with lightning from {effect.get('source_item', 'item')}. "
                            f"Roll {dice} = {roll_power}. Deals __DMG_0__ magic damage."
                        ),
                    }
                )
        elif passive_type == "heal_on_hit":
            chance = float(passive.get("chance", 0) or 0)
            scaling = passive.get("scaling", {}) or {}
            dice = passive.get("dice", "d3")
            if chance <= 0 or rng.random() > chance:
                continue
            roll_power = roll(dice, rng) if dice else 0
            heal_value = 0
            if "atk" in scaling:
                heal_value = calc_base_damage(
                    modify_stat(attacker, "atk", attacker.stats.get("atk", 0)),
                    scaling["atk"],
                    roll_power,
                )
            elif "int" in scaling:
                heal_value = calc_base_damage(
                    modify_stat(attacker, "int", attacker.stats.get("int", 0)),
                    scaling["int"],
                    roll_power,
                )
            if heal_value > 0 and attacker.res:
                before_hp = attacker.res.hp
                attacker.res.hp = min(attacker.res.hp + heal_value, attacker.res.hp_max)
                bonus_healing += attacker.res.hp - before_hp
                log_lines.append(
                    f"{attacker.sid[:5]} draws strength from {effect.get('source_item', 'item')}, healing {heal_value} HP."
                )
        elif passive_type == "empower_next_offense":
            chance = float(passive.get("chance", 0) or 0)
            effect_id = passive.get("effect_id", "crusader_empower")
            if chance > 0 and rng.random() <= chance and not has_effect(attacker, effect_id):
                overrides = {}
                if passive.get("multiplier") is not None:
                    overrides["damage_mult"] = float(passive.get("multiplier", 1.0) or 1.0)
                apply_effect_by_id(attacker, effect_id, overrides=overrides or None)
                log_lines.append(
                    f"{attacker.sid[:5]} feels empowered by {effect.get('source_item', 'item')}."
                )
        elif passive_type == "duplicate_offensive_spell":
            if base_damage <= 0 or not ability:
                continue
            tags = ability.get("tags") or []
            if "spell" not in tags or "attack" not in tags:
                continue
            chance = float(passive.get("chance", 0) or 0)
            if chance <= 0 or rng.random() > chance:
                continue

            dice_data = ability.get("dice")
            scaling = ability.get("scaling", {}) or {}
            flat_damage = ability.get("flat_damage")
            spell_school = normalize_school(
                ability.get("school") or ability.get("damage_type") or damage_type or "physical"
            )
            spell_subschool = ability.get("subschool") if spell_school == "magical" else None
            hits = max(1, int(ability.get("hits", 1) or 1))

            item_name = effect.get("source_item", "item")
            ability_name = ability.get("name", "spell")
            owner_class = class_display_name((attacker.build.class_id or "").strip().lower()) if attacker.build else "Player"
            duplicate_prefix = f"{owner_class}(you)'s {item_name}"
            duplicate_damage = 0
            per_hit_reduced: list[int] = []
            hit_segments: list[str] = []
            for hit_index in range(1, hits + 1):
                roll_power = 0
                dice_type = None
                if dice_data:
                    dice_type = dice_data.get("type")
                    if dice_type:
                        roll_power = roll(dice_type, rng)

                duplicate_raw = 0
                if flat_damage is not None:
                    duplicate_raw = int(flat_damage)
                elif "atk" in scaling:
                    duplicate_raw = calc_base_damage(
                        modify_stat(attacker, "atk", attacker.stats.get("atk", 0)),
                        scaling["atk"],
                        roll_power,
                    )
                elif "int" in scaling:
                    duplicate_raw = calc_base_damage(
                        modify_stat(attacker, "int", attacker.stats.get("int", 0)),
                        scaling["int"],
                        roll_power,
                    )
                if duplicate_raw <= 0:
                    continue

                duplicate_reduced = mitigate_damage(duplicate_raw, target, spell_school)
                if spell_school == "physical" and is_damage_immune(target, "physical"):
                    duplicate_reduced = 0
                if spell_school == "magical" and is_damage_immune(target, "magic"):
                    duplicate_reduced = 0
                if duplicate_reduced <= 0:
                    continue

                duplicate_damage += duplicate_reduced
                per_hit_reduced.append(duplicate_reduced)
                if hits > 1:
                    if dice_type:
                        hit_segments.append(f"Hit {hit_index}: Roll {dice_type} = {roll_power}. Deals __DMG_{len(per_hit_reduced) - 1}__ damage.")
                    else:
                        hit_segments.append(f"Hit {hit_index}: Deals __DMG_{len(per_hit_reduced) - 1}__ damage.")
                elif dice_type:
                    hit_segments.append(f"Roll {dice_type} = {roll_power}. Deals __DMG_{len(per_hit_reduced) - 1}__ damage.")
                else:
                    hit_segments.append(f"Deals __DMG_{len(per_hit_reduced) - 1}__ damage.")

            if duplicate_damage <= 0:
                continue

            bonus_damage += duplicate_damage
            damage_events.append(
                {
                    "incoming": duplicate_damage,
                    "damage_instances": per_hit_reduced,
                    "school": spell_school,
                    "subschool": spell_subschool,
                    "log_template": f"{duplicate_prefix} duplicates {ability_name}! {' '.join(hit_segments)}",
                }
            )


    return bonus_damage, log_lines, bonus_healing, damage_events

def damage_multiplier_from_passives(attacker: PlayerState) -> float:
    """Apply conditional damage multipliers from item passives."""
    if not attacker.res:
        return 1.0
    hp_pct = attacker.res.hp / max(1, attacker.res.hp_max)
    multiplier = 1.0
    for effect in attacker.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "on_damage":
            continue
        if passive.get("type") == "damage_bonus_above_hp":
            threshold = float(passive.get("threshold", 0) or 0)
            if hp_pct > threshold:
                multiplier *= float(passive.get("multiplier", 1.0) or 1.0)
        elif passive.get("type") == "damage_bonus_below_hp":
            threshold = float(passive.get("threshold", 0) or 0)
            if hp_pct < threshold:
                multiplier *= float(passive.get("multiplier", 1.0) or 1.0)
    return multiplier


def resource_gain_multiplier_from_passives(player: PlayerState, resource: str) -> float:
    """Apply item passives that modify resource gains from any source."""
    if not player.res:
        return 1.0
    multiplier = 1.0
    normalized_resource = str(resource or "").strip().lower()
    for effect in player.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("type") != "resource_gain_multiplier":
            continue
        passive_resource = str(passive.get("resource", "") or "").strip().lower()
        if passive_resource and passive_resource != normalized_resource:
            continue
        multiplier *= float(passive.get("multiplier", 1.0) or 1.0)
    return multiplier

def tick_dots(ps: PlayerState, log: List[str], label: str) -> list[dict[str, Any]]:
    """Compute DoT tick events after mitigation; resolution/logging happens in resolver."""
    if is_immune_all(ps):
        return []
    damage_sources: list[dict[str, Any]] = []
    for effect in ps.effects:
        effect_id = effect.get("id")
        effect_type = effect.get("type")
        category = effect.get("category")
        if effect_type != "burn" and category != "dot":
            continue

        if effect.get("skip_first_tick"):
            effect["skip_first_tick"] = False
            continue

        if effect_id == "agony":
            raw_damage = max(0, int(effect.get("tick_damage", 1) or 1))
            effect["tick_damage"] = min(15, raw_damage + 1)
        else:
            raw_damage = max(0, int(effect.get("tick_damage", effect.get("value", 0)) or 0))

        if raw_damage <= 0:
            continue

        school = normalize_school(effect.get("school") or "physical") or "physical"
        if effect_id == "agony":
            reduced = raw_damage
        else:
            reduced = mitigate_damage(raw_damage, ps, school)
        if school == "physical" and is_damage_immune(ps, "physical"):
            reduced = 0
        if school == "magical" and is_damage_immune(ps, "magic"):
            reduced = 0

        source_sid = effect.get("source_sid")
        damage_sources.append({
            "source_sid": source_sid,
            "incoming": reduced,
            "effect_id": effect.get("id"),
            "effect_name": effect.get("name", "DoT"),
            "school": school,
            "subschool": effect.get("subschool"),
            "lifesteal_pct": float(effect.get("lifesteal_pct", 0) or 0),
        })
    return damage_sources


def trigger_end_of_turn_passives(ps: PlayerState, log: List[str], label: str) -> int:
    """Run end-of-turn item passives (currently: heal_self)."""
    total_healing = 0
    for effect in ps.effects:
        if effect.get("type") != "item_passive":
            continue
        passive = effect.get("passive", {}) or {}
        if passive.get("trigger") != "end_of_turn":
            continue

        if passive.get("type") == "heal_self":
            heal_value = int(passive.get("value", 0) or 0)
            if heal_value > 0:
                before_hp = ps.res.hp
                ps.res.hp = min(ps.res.hp + heal_value, ps.res.hp_max)
                total_healing += ps.res.hp - before_hp
                log.append(
                    f"{label} heals {heal_value} HP from {effect.get('source_item', 'item')}."
                )
        elif passive.get("type") == "absorb_self":
            absorb_value = int(passive.get("value", 0) or 0)
            if absorb_value > 0:
                add_absorb(ps, absorb_value)
                log.append(
                    f"{label} gains {absorb_value} absorb from {effect.get('source_item', 'item')}."
                )
    return total_healing


def trigger_end_of_turn_effects(ps: PlayerState, log: List[str], label: str) -> tuple[int, List[Dict[str, Any]]]:
    """Run end-of-turn status effects such as regeneration from buffs."""
    total_healing = 0
    pending_mindgames_damage: List[Dict[str, Any]] = []
    for effect in ps.effects:
        regen = effect.get("regen", {}) or {}
        if not regen:
            continue
        if is_immune_all(ps) and not (effect.get("flags", {}) or {}).get("immune_all"):
            # While immune-all is active (e.g. Ice Block), suppress normal periodic
            # ticks (HoTs/regen) but still allow immunity effect self-regen.
            continue
        hp_gain = int(regen.get("hp", 0) or 0)
        mp_gain = int(regen.get("mp", 0) or 0)
        energy_gain = int(regen.get("energy", 0) or 0)
        effect_name = effect.get("name", "an effect")
        twisted_by_mindgames = hp_gain > 0 and has_effect(ps, "mindgames")
        if hp_gain > 0:
            if twisted_by_mindgames:
                pending_mindgames_damage.append(
                    {
                        "source_sid": ps.sid,
                        "incoming": hp_gain,
                        "effect_id": "mindgames",
                        "effect_name": "Mindgames",
                        "school": "magical",
                        "subschool": "shadow",
                        "suppress_log": True,
                    }
                )
                if log is not None:
                    log.append(
                        f"{label} is twisted by Mindgames and takes {hp_gain} self-damage instead of healing from {effect_name}."
                    )
            else:
                before_hp = ps.res.hp
                ps.res.hp = min(ps.res.hp + hp_gain, ps.res.hp_max)
                total_healing += ps.res.hp - before_hp
        if mp_gain > 0:
            ps.res.mp = min(ps.res.mp + mp_gain, ps.res.mp_max)
        if energy_gain > 0:
            ps.res.energy = min(ps.res.energy + energy_gain, ps.res.energy_max)
        should_log_recovery = ((hp_gain > 0 and not twisted_by_mindgames) or mp_gain > 0 or energy_gain > 0)
        if should_log_recovery and log is not None:
            log.append(
                f"{label} recovers {hp_gain} HP, {mp_gain} MP, and {energy_gain} Energy from {effect_name}."
            )
    return total_healing, pending_mindgames_damage


def end_of_turn(ps: PlayerState, log: List[str], label: str) -> dict[str, Any]:
    """End-of-turn pipeline: DoTs, passives, duration tick, regen."""
    if not ps.res:
        return {"damage_sources": [], "healing_done": 0, "self_damage_sources": []}

    if has_flag(ps, "cycloned"):
        return {"damage_sources": [], "healing_done": 0, "self_damage_sources": []}

    damage_sources = tick_dots(ps, log, label)
    total_healing = 0
    total_healing += trigger_end_of_turn_passives(ps, log, label)
    effect_healing, self_damage_sources = trigger_end_of_turn_effects(ps, log, label)
    total_healing += effect_healing

    if ps.res.hp > 0:
        ps.res.mp = min(ps.res.mp + DEFAULTS["mp_regen_per_turn"], ps.res.mp_max)
        ps.res.energy = min(ps.res.energy + DEFAULTS["energy_regen_per_turn"], ps.res.energy_max)
    return {"damage_sources": damage_sources, "healing_done": total_healing, "self_damage_sources": self_damage_sources}


def end_of_turn_pet(pet, log: List[str], label: str) -> dict[str, Any]:
    damage_sources: list[dict[str, Any]] = []
    healing_done = 0
    for effect in pet.effects:
        if effect.get("category") == "dot":
            raw_damage = max(0, int(effect.get("tick_damage", effect.get("value", 0)) or 0))
            if raw_damage > 0:
                damage_sources.append(
                    {
                        "source_sid": effect.get("source_sid"),
                        "incoming": raw_damage,
                        "effect_id": effect.get("id"),
                        "effect_name": effect.get("name", "DoT"),
                        "school": effect.get("school", "magical"),
                        "subschool": effect.get("subschool"),
                    }
                )
        regen = effect.get("regen", {}) or {}
        hp_gain = max(0, int(regen.get("hp", 0) or 0))
        if hp_gain > 0 and pet.hp > 0:
            before_hp = pet.hp
            pet.hp = min(pet.hp + hp_gain, pet.hp_max)
            gained = pet.hp - before_hp
            healing_done += gained
            if gained > 0 and log is not None:
                log.append(f"{label} recovers {gained} HP from {effect.get('name', 'an effect')}.")
    return {"damage_sources": damage_sources, "healing_done": healing_done}
