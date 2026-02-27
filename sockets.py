# games/duel/sockets.py
from flask import request
from flask_socketio import emit, join_room, leave_room
import time

from . import state
from .engine import resolver
from .engine.effects import current_form_id, effect_template, is_stealthed
from .content.classes import CLASSES
from .content.items import ITEMS
from .content.abilities import ABILITIES

THUNDERFURY_NAME = "Thunderfury, Blessed Blade of the Windseeker"
DRAGONWRATH_NAME = "Dragonwrath, Tarecgosa's Rest"
TWIN_BLADES_AZZINOTH_NAME = "Twin Blades of Azzinoth"
ITEM_FX_MARKUP = [
    (THUNDERFURY_NAME, "fx_thunderfury"),
    (DRAGONWRATH_NAME, "fx_dragonwrath"),
    (TWIN_BLADES_AZZINOTH_NAME, "fx_twin_blades_azzinoth"),
]


def apply_item_fx_markup(text):
    if not isinstance(text, str):
        return text
    formatted = text
    for item_name, fx_id in ITEM_FX_MARKUP:
        formatted = formatted.replace(item_name, f"[[fx:{fx_id}]]{item_name}[[/fx]]")
    return formatted

def snapshot_for(match, viewer_sid):
    """
    Returns a UI-friendly snapshot with friendly/enemy HP/Mana/Energy/Rage.
    """
    p1, p2 = match.players
    you = viewer_sid
    enemy = p2 if you == p1 else p1
    totals = match.combat_totals or {}
    friendly_totals = totals.get(you, {"damage": 0, "healing": 0})
    enemy_totals = totals.get(enemy, {"damage": 0, "healing": 0})

    def class_name_for(sid):
        picked = match.picks.get(sid, {})
        class_id = None
        if isinstance(picked, dict):
            class_id = picked.get("class_id")
        if not class_id:
            ps = match.state.get(sid)
            if ps and ps.build:
                class_id = ps.build.class_id
        class_data = CLASSES.get(class_id or "", {})
        return class_data.get("name", "Adventurer")

    def resource_config_for(sid):
        picked = match.picks.get(sid, {})
        class_id = None
        if isinstance(picked, dict):
            class_id = picked.get("class_id")
        if not class_id:
            ps = match.state.get(sid)
            if ps and ps.build:
                class_id = ps.build.class_id
        class_data = CLASSES.get(class_id or "", {})
        return class_data.get("resource_display", {
            "primary": {"id": "mp", "label": "Mana", "color": "var(--mana-blue)"},
        })
    
    def get_equipped_items(sid):
        """Get the equipped item names for display"""
        ps = match.state.get(sid)
        if not ps or not ps.build:
            return {"weapon": None, "armor": None, "trinket": None}
        
        equipped = {}
        for slot, item_id in ps.build.items.items():
            if item_id and item_id in ITEMS:
                item = ITEMS[item_id]
                allowed_classes = item.get("classes")
                if allowed_classes and ps.build.class_id not in allowed_classes:
                    equipped[slot] = None
                else:
                    equipped[slot] = apply_item_fx_markup(item["name"])
            else:
                equipped[slot] = None
        return equipped

    def pack(sid):
        ps = match.state.get(sid)
        if not ps or not ps.res:
            return None
        r = ps.res
        absorb_layers = []
        for effect_id, layer in getattr(r, "absorbs", {}).items():
            remaining = max(0, int(layer.get("remaining", 0) or 0))
            max_value = max(0, int(layer.get("max", remaining) or 0))
            absorb_layers.append({
                "id": effect_id,
                "name": layer.get("name", "Shield"),
                "remaining": remaining,
                "max": max_value,
            })

        absorb_remaining_total = sum(layer["remaining"] for layer in absorb_layers)
        absorb_max_total = sum(layer["max"] for layer in absorb_layers)

        return {
            "hp": r.hp, "hp_max": r.hp_max,
            "absorb": absorb_remaining_total,
            "absorb_max": absorb_max_total,
            "absorb_layers": absorb_layers,
            "mp": r.mp, "mp_max": r.mp_max,
            "energy": r.energy, "energy_max": r.energy_max,
            "rage": r.rage, "rage_max": r.rage_max,
        }

    def stealthed_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return False
        return is_stealthed(ps)

    def form_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return None
        return current_form_id(ps)
    def effects_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return []
        packed_effects = []
        for effect in ps.effects:
            effect_id = effect.get("id")
            if not effect_id:
                continue
            template = effect_template(effect_id)
            display = template.get("display")
            if not isinstance(display, dict) or not display.get("war_council"):
                continue
            packed_effects.append({
                "id": effect_id,
                "display": {
                    "label": display.get("label"),
                    "color": display.get("color"),
                    "priority": int(display.get("priority", 0) or 0),
                },
            })
        return packed_effects

    def pet_effects_for(effect_id, effect_instance=None):
        template = effect_template(effect_id)
        display = template.get("display") if isinstance(template, dict) else None
        pet_effects = []
        if isinstance(display, dict) and display.get("war_council") and display.get("label"):
            pet_effects.append({
                "label": display.get("label"),
                "color": display.get("color"),
                "priority": int(display.get("priority", 0) or 0),
            })

        if effect_id == "shadowfiend" and isinstance(effect_instance, dict):
            duration = int(effect_instance.get("duration", 0) or 0)
            if duration > 0:
                pet_effects.append({
                    "label": f"{duration}T",
                    "color": "#FFFFFF",
                    "priority": 100,
                })

        return pet_effects

    def pets_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return []

        minions = ps.minions if isinstance(ps.minions, dict) else {}
        packed_pets = []

        imp_count = int(minions.get("imp", 0) or 0)
        if imp_count > 0:
            packed_pets.append({
                "id": "imp",
                "name": "Imp",
                "count": imp_count,
                "effects": [],
            })

        shadowfiend_count = int(minions.get("shadowfiend", 0) or 0)
        if shadowfiend_count > 0:
            shadowfiend_effect = None
            for effect in ps.effects:
                if effect.get("id") == "shadowfiend":
                    shadowfiend_effect = effect
                    break

            packed_pets.append({
                "id": "shadowfiend",
                "name": "Shadowfiend",
                "count": shadowfiend_count,
                "effects": pet_effects_for("shadowfiend", shadowfiend_effect),
            })

        return packed_pets

    def display_name_for(sid):
        class_name = class_name_for(sid)
        if sid == viewer_sid:
            return f"{class_name}(you)"
        return class_name

    def format_log_line(line):
        formatted = line
        for sid in match.players:
            formatted = formatted.replace(sid[:5], display_name_for(sid))
        if "{friendly_damage}" in formatted:
            formatted = formatted.format(
                friendly_damage=friendly_totals.get("damage", 0),
                friendly_healing=friendly_totals.get("healing", 0),
                enemy_damage=enemy_totals.get("damage", 0),
                enemy_healing=enemy_totals.get("healing", 0),
            )
        return apply_item_fx_markup(formatted)

    def primary_resource_for(sid):
        form_id = form_for(sid)
        if form_id == "bear_form":
            primary = {"id": "rage", "label": "Rage", "color": "var(--rage-red)"}
        elif form_id == "cat_form":
            primary = {"id": "energy", "label": "Energy", "color": "#FFF468"}
        else:
            config = resource_config_for(sid)
            primary = config.get("primary", {"id": "mp", "label": "Mana", "color": "var(--mana-blue)"})
        return {
            "id": primary.get("id", "mp"),
            "label": primary.get("label", "Mana"),
            "color": primary.get("color", "var(--mana-blue)"),
        }

    friendly_cooldowns = {}
    viewer_state = match.state.get(you)
    if viewer_state:
        for ability_id in viewer_state.cooldowns.keys():
            remaining_turns = resolver.cooldown_remaining(viewer_state, ability_id, ABILITIES.get(ability_id, {}))
            if remaining_turns > 0:
                friendly_cooldowns[ability_id] = remaining_turns

    ability_meta = {}
    for ability_id in friendly_cooldowns.keys():
        ability_data = ABILITIES.get(ability_id, {})
        ability_meta[ability_id] = {
            "name": ability_data.get("name", ability_id),
            "icon": ability_data.get("icon"),
        }

    return {
        "phase": match.phase,
        "turn": match.turn,
        "you": pack(you),
        "enemy": pack(enemy),
        "you_class": class_name_for(you) + " (YOU)",
        "enemy_class": class_name_for(enemy),
        "you_items": get_equipped_items(you),
        "enemy_items": get_equipped_items(enemy),
        "you_resource": primary_resource_for(you),
        "enemy_resource": primary_resource_for(enemy),
        "you_stealthed": stealthed_for(you),
        "enemy_stealthed": stealthed_for(enemy),
        "you_form": form_for(you),
        "enemy_form": form_for(enemy),
        "you_effects": effects_for(you),
        "enemy_effects": effects_for(enemy),
        "you_pets": pets_for(you),
        "enemy_pets": pets_for(enemy),
        "log": [format_log_line(line) for line in match.log[-30:]],
        "winner": match.winner,
        "friendly_total_damage": friendly_totals.get("damage", 0),
        "friendly_total_healing": friendly_totals.get("healing", 0),
        "enemy_total_damage": enemy_totals.get("damage", 0),
        "enemy_total_healing": enemy_totals.get("healing", 0),
        "log_length": len(match.log),
        "friendly_cooldowns": friendly_cooldowns,
        "ability_meta": ability_meta,
    }

def register_duel_socket_handlers(socketio):
    @socketio.on("connect")
    def duel_connect():
        sid = request.sid
        emit("duel_system", "Connected to Arena Server")
    
    @socketio.on("duel_queue")
    def duel_queue():
        sid = request.sid
        if state.get_match_by_sid(sid):
            emit("duel_system", "Already in a duel.")
            return
        
        # Prevent duplicate queue entries
        if sid in state.duel_queue:
            emit("duel_system", "Already in queue.")
            return
            
        state.enqueue(sid)
        emit("duel_system", "Queued for DUEL...")

        if len(state.duel_queue) >= 2:
            p1 = state.duel_queue.pop(0)
            p2 = state.duel_queue.pop(0)
            seed = int(time.time() * 1000) & 0xFFFFFFFF
            match = state.create_room(p1, p2, seed)

            join_room(match.room_id, sid=p1)
            join_room(match.room_id, sid=p2)

            # Send role assignments
            socketio.emit("duel_role", "P1", to=p1)
            socketio.emit("duel_role", "P2", to=p2)

            socketio.emit("duel_system", "Match found. Prep phase: pick class + items.", to=match.room_id)
            socketio.emit("duel_prep_options", {
                "classes": CLASSES,
                "items": ITEMS,
                "abilities": ABILITIES,
            }, to=match.room_id)
            
            # Send initial snapshots to both players
            socketio.emit("duel_snapshot", snapshot_for(match, p1), to=p1)
            socketio.emit("duel_snapshot", snapshot_for(match, p2), to=p2)

    @socketio.on("duel_prep_submit")
    def duel_prep_submit(payload):
        sid = request.sid
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if match.phase != "prep":
            emit("duel_system", "Prep phase is over.")
            return

        # store picks
        current = match.picks.get(sid, {})
        if not isinstance(current, dict):
            current = {}
        if not isinstance(payload, dict):
            payload = {}
        merged = {**current, **payload}
        items = dict(current.get("items", {}))
        items.update(payload.get("items", {}))
        if items:
            merged["items"] = items
        match.picks[sid] = merged  # later: validate schema
        selection_name = None
        if isinstance(payload, dict):
            class_id = payload.get("class_id")
            if class_id:
                selection_name = CLASSES.get(class_id, {}).get("name", class_id)
            else:
                items_payload = payload.get("items", {})
                if isinstance(items_payload, dict):
                    for item_id in items_payload.values():
                        if item_id and item_id in ITEMS:
                            selection_name = ITEMS[item_id]["name"]
                            break
        if selection_name:
            emit("duel_system", f"🛡️ Prep saved, {selection_name}.")
        else:
            emit("duel_system", "🛡️ Prep saved.")
        try_start_combat(match)

    def both_players_locked(match):
        return all(match.locked_in.get(sid) for sid in match.players)

    def player_has_class(match, sid):
        picked = match.picks.get(sid, {})
        if isinstance(picked, dict):
            return bool(picked.get("class_id"))
        return False

    def try_start_combat(match):
        if match.phase != "prep":
            return
        if not both_players_locked(match):
            return
        if not all(player_has_class(match, sid) for sid in match.players):
            return
        match.phase = "combat"
        resolver.apply_prep_build(match)
        socketio.emit("duel_snapshot", snapshot_for(match, match.players[0]), to=match.players[0])
        socketio.emit("duel_snapshot", snapshot_for(match, match.players[1]), to=match.players[1])
        socketio.emit("duel_system", "Combat begins.", to=match.room_id)

    @socketio.on("duel_lock_in")
    def duel_lock_in():
        sid = request.sid
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if match.phase != "prep":
            emit("duel_system", "Prep phase is over.")
            return
        if not player_has_class(match, sid):
            emit("duel_system", "Choose a class before locking in.")
            return
        match.locked_in[sid] = True
        emit("duel_system", "Locked in. Waiting for opponent...")
        try_start_combat(match)

    @socketio.on("duel_action")
    def duel_action(payload):
        sid = request.sid
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if match.phase != "combat":
            emit("duel_system", "Prep phase: choose class/items before combat.")
            return

        action = payload if isinstance(payload, dict) else {"ability_id": str(payload).strip()}
        ability_id = action.get("ability_id", "")
        if ability_id not in ABILITIES:
            emit("duel_system", f"Unknown ability '{ability_id}'. Try again.")
            return
        resolver.submit_action(match, sid, action)
        ability_name = ABILITIES.get(ability_id, {}).get("name", ability_id)
        cooldown_remaining = 0
        ps = match.state.get(sid)
        if ps:
            cooldown_remaining = resolver.cooldown_remaining(ps, ability_id, ABILITIES.get(ability_id, {}))
        if cooldown_remaining > 0:
            emit("duel_system", f"🛡️ Action received. Warning {ability_name} is on cooldown.")
        else:
            emit("duel_system", f"🛡️ Action received. {ability_name}")

        if resolver.ready_to_resolve(match):
            resolver.resolve_turn(match)
            socketio.emit("duel_snapshot", snapshot_for(match, match.players[0]), to=match.players[0])
            socketio.emit("duel_snapshot", snapshot_for(match, match.players[1]), to=match.players[1])
            if match.phase == "ended":
                socketio.emit("duel_system", "Duel ended.", to=match.room_id)

    @socketio.on("duel_chat")
    def duel_chat(message):
        sid = request.sid
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        
        game = match
        p1, p2 = game.players
        role = "P1" if sid == p1 else "P2"
        
        # Get player class name
        picked = match.picks.get(sid, {})
        class_id = None
        if isinstance(picked, dict):
            class_id = picked.get("class_id")
        if not class_id:
            ps = match.state.get(sid)
            if ps and ps.build:
                class_id = ps.build.class_id
        
        from .content.classes import CLASSES
        class_data = CLASSES.get(class_id or "", {})
        player_class = class_data.get("name", "Adventurer")
        
        # Broadcast the chat message to both players with class name
        socketio.emit("duel_chat", {
            "playerClass": player_class,
            "message": message,
            "role": role
        }, to=match.room_id)

    @socketio.on("disconnect")
    def duel_disconnect():
        sid = request.sid
        state.dequeue(sid)
        match = state.get_match_by_sid(sid)
        if not match:
            return
        
        room_id = match.room_id
        
        # Determine which player disconnected and get their class name
        p1, p2 = match.players
        role = "P1" if sid == p1 else "P2"
        
        # Get disconnecting player's class name for better message
        picked = match.picks.get(sid, {})
        class_id = None
        if isinstance(picked, dict):
            class_id = picked.get("class_id")
        if not class_id:
            ps = match.state.get(sid)
            if ps and ps.build:
                class_id = ps.build.class_id
        
        class_data = CLASSES.get(class_id or "", {})
        player_class = class_data.get("name", "Adventurer")
        
        # Send disconnect message to the room BEFORE leaving
        disconnect_msg = f"⚠️ {player_class} ({role}) has left the instance"
        socketio.emit("duel_system", disconnect_msg, to=room_id)
        socketio.emit("duel_system", "Duel ended.", to=room_id)
        
        # Now remove the player from the room
        leave_room(room_id, sid=sid)
        
        # Clean up the match state
        state.cleanup_room(room_id)
