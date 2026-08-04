# games/duel/sockets.py
from dataclasses import dataclass
import logging
import time

from eventlet.semaphore import Semaphore
from flask import request
from flask_socketio import emit

from . import state
from .availability import (
    LIFECYCLE_POLICY,
    SNAPSHOT_LOG_ENTRY_LIMIT,
)
from .engine import resolver
from .engine.effects import (
    build_champion_mouseover_payload,
    build_effect_panel_payload,
    current_form_id,
    effect_template,
    is_stealthed,
)
from .content.pets import PETS
from .content.classes import CLASSES, class_display_name, normalize_class_id
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

MAX_CLASS_ID_CHARACTERS = 100
MAX_ITEM_ID_CHARACTERS = 150
MAX_EQUIPMENT_SLOT_CHARACTERS = 32
MAX_CHAT_CHARACTERS = 500
MAX_PREP_TOP_LEVEL_FIELDS = 2
MAX_ACTION_TOP_LEVEL_FIELDS = 1
MAX_EQUIPMENT_FIELDS = 3

_PREP_FIELDS = frozenset({"class_id", "items"})
THROTTLE_WARNING = "Too many requests. Slow down."
QUEUE_EXPIRED_MESSAGE = (
    "Your matchmaking queue entry expired. Queue again to continue."
)
MATCH_SETUP_FAILED_MESSAGE = "Match setup failed. Queue again to continue."
MATCH_SETUP_INTERRUPTED_MESSAGE = (
    "Match setup was interrupted. Queue again to continue."
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepResult:
    expired_queue_sids: tuple[str, ...]
    detached_rooms: tuple[state.DetachedRoom, ...]
    skipped_busy: int
    replacement_room_ids: tuple[str, ...]


_lifecycle_sweeper_guard = Semaphore(1)
_lifecycle_sweeper_started = False


def _parse_prep_payload(payload):
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Invalid prep submission.")
    if len(payload) > MAX_PREP_TOP_LEVEL_FIELDS:
        raise ValueError("Unknown prep field.")
    if any(not isinstance(key, str) or key not in _PREP_FIELDS for key in payload):
        raise ValueError("Unknown prep field.")

    parsed = {}
    if "class_id" in payload:
        class_id = payload["class_id"]
        if not isinstance(class_id, str):
            raise ValueError("Invalid prep submission.")
        if len(class_id) > MAX_CLASS_ID_CHARACTERS:
            raise ValueError("Class ID is too long.")
        parsed["class_id"] = class_id

    if "items" in payload:
        items_payload = payload["items"]
        if not isinstance(items_payload, dict):
            raise ValueError("Equipment selections must be an object.")
        if len(items_payload) > MAX_EQUIPMENT_FIELDS:
            raise ValueError("Invalid prep submission.")

        parsed_items = {}
        for slot, item_id in items_payload.items():
            if not isinstance(slot, str):
                raise ValueError("Unknown equipment slot.")
            if len(slot) > MAX_EQUIPMENT_SLOT_CHARACTERS:
                raise ValueError("Equipment slot is too long.")
            if not isinstance(item_id, str):
                raise ValueError("Item IDs must be text.")
            if len(item_id) > MAX_ITEM_ID_CHARACTERS:
                raise ValueError("Item ID is too long.")
            parsed_items[slot] = item_id
        parsed["items"] = parsed_items

    return parsed


def _parse_action_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid action submission.")
    if len(payload) > MAX_ACTION_TOP_LEVEL_FIELDS:
        raise ValueError("Unknown action field.")
    if any(key != "ability_id" for key in payload):
        raise ValueError("Unknown action field.")
    if set(payload) != {"ability_id"}:
        raise ValueError("Invalid action submission.")

    raw_ability_id = payload["ability_id"]
    if not isinstance(raw_ability_id, str):
        raise ValueError("Ability ID must be text.")
    if len(raw_ability_id) > resolver.MAX_ABILITY_ID_CHARACTERS:
        raise ValueError("Ability ID is too long.")

    action = resolver.normalize_player_action({"ability_id": raw_ability_id})
    if action["ability_id"] not in ABILITIES:
        raise ValueError("Unknown ability.")
    return action


def _parse_chat_payload(payload):
    if not isinstance(payload, str):
        raise ValueError("Chat messages must be text.")
    if len(payload) > MAX_CHAT_CHARACTERS:
        raise ValueError("Chat message is too long.")

    message = payload.strip()
    if not message:
        raise ValueError("Chat message cannot be empty.")
    return message


def _accepts_empty_event_payload(payload_args):
    return not payload_args or (len(payload_args) == 1 and payload_args[0] is None)


def _picked_class_id(match, sid):
    picked = match.picks.get(sid, {})
    class_id = None
    if isinstance(picked, dict):
        class_id = picked.get("class_id")
    if not class_id:
        ps = match.state.get(sid)
        if ps and ps.build:
            class_id = ps.build.class_id
    return normalize_class_id(class_id)


def _picked_class_name(match, sid, default="Unknown Class"):
    return class_display_name(_picked_class_id(match, sid), default=default)


def _invalid_class_message(class_id):
    attempted = str(class_id).strip() if class_id is not None else ""
    if attempted:
        return f"Unknown class '{attempted}'. Choose a valid class before locking in."
    return "Choose a valid class before locking in."


def _normalized_item_updates(items_payload):
    canonical_items = resolver.canonicalize_equipment(
        items_payload,
        base_items={},
        allow_omitted_slot=True,
    )
    return {
        slot: item_id
        for slot, item_id in canonical_items.items()
        if item_id is not None
    }


def _canonical_prep_pick(current, payload):
    payload = _parse_prep_payload(payload)

    current_payload = current if isinstance(current, dict) else {}
    current_class_id = None
    if current_payload.get("class_id") is not None:
        current_class_id = normalize_class_id(current_payload.get("class_id"))
        if not current_class_id:
            raise ValueError(_invalid_class_message(current_payload.get("class_id")))

    proposed_class_id = current_class_id
    if "class_id" in payload:
        proposed_class_id = normalize_class_id(payload.get("class_id"))
        if not proposed_class_id:
            raise ValueError(_invalid_class_message(payload.get("class_id")))

    current_items = current_payload.get("items", {})
    if "items" in payload:
        proposed_items = resolver.canonicalize_equipment(
            payload.get("items"),
            class_id=proposed_class_id,
            base_items=current_items,
            allow_omitted_slot=True,
        )
    else:
        proposed_items = resolver.canonicalize_equipment(
            current_items,
            class_id=proposed_class_id,
        )

    proposed = {"items": proposed_items}
    if proposed_class_id:
        proposed["class_id"] = proposed_class_id
    return proposed


def _prep_selection_name(payload):
    if not isinstance(payload, dict):
        return None

    normalized_class_id = normalize_class_id(payload.get("class_id"))
    if normalized_class_id:
        return class_display_name(normalized_class_id, default=None)

    try:
        item_updates = _normalized_item_updates(payload.get("items", {}))
    except ValueError:
        return None
    for item_id in item_updates.values():
        return ITEMS[item_id]["name"]
    return None


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
    # DPT = actual credited damage / completed resolved turns. Snapshots are
    # produced after the resolved-turn counter advances, so match.turn already
    # counts the final turn; max(1, ...) keeps a first-turn kill (or a
    # pre-combat snapshot) from dividing by zero.
    completed_turns = max(1, int(match.turn))

    def damage_per_turn(source_totals):
        return round(int(source_totals.get("damage", 0) or 0) / completed_turns, 1)

    friendly_dpt = damage_per_turn(friendly_totals)
    enemy_dpt = damage_per_turn(enemy_totals)

    def class_name_for(sid):
        return _picked_class_name(match, sid, default="Unselected")

    def resource_config_for(sid):
        class_data = CLASSES.get(_picked_class_id(match, sid) or "", {})
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

    def entity_type_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return None
        return ps.entity_type

    def champion_mouseover_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return None
        return build_champion_mouseover_payload(ps)

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

    def effect_panel_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return {
                "buffs_physical": [],
                "buffs_magical": [],
                "debuffs_physical": [],
                "debuffs_magical": [],
            }
        return build_effect_panel_payload(ps)

    def pet_statuses_for(pet):
        statuses = []
        template = PETS.get(pet.template_id, {})
        display = template.get("display", {}) if isinstance(template, dict) else {}
        if display.get("war_council") and display.get("label"):
            statuses.append({
                "label": display.get("label"),
                "color": display.get("color"),
                "priority": int(display.get("priority", 0) or 0),
            })
        for effect in pet.effects or []:
            effect_id = effect.get("id")
            if not effect_id:
                continue
            effect_display = (effect_template(effect_id) or {}).get("display", {})
            if effect_display.get("war_council") and effect_display.get("label"):
                statuses.append({
                    "label": effect_display.get("label"),
                    "color": effect_display.get("color"),
                    "priority": int(effect_display.get("priority", 0) or 0),
                })
        if pet.template_id != "shadowfiend" and pet.entity_type != "totem" and pet.duration is not None and pet.duration > 0:
            statuses.append({"label": f"{int(pet.duration)}T", "color": "#FFFFFF", "priority": 100})
        return statuses

    def pet_primary_resource_for(pet):
        template = PETS.get(getattr(pet, "template_id", ""), {})
        template_resources = template.get("resources", {}) if isinstance(template, dict) else {}
        resource_order = ("mp", "energy", "rage")
        labels = {"mp": "Mana", "energy": "Energy", "rage": "Rage"}
        colors = {"mp": "var(--mana-blue)", "energy": "#FFF468", "rage": "var(--rage-red)"}

        for resource_id in resource_order:
            template_value = template_resources.get(resource_id, 0) if isinstance(template_resources, dict) else 0
            max_key = f"{resource_id}_max"
            max_value = int(getattr(pet, max_key, 0) or 0)
            if int(template_value or 0) > 0 or max_value > 0:
                if max_value <= 0:
                    continue
                return {
                    "id": resource_id,
                    "label": labels[resource_id],
                    "color": colors[resource_id],
                    "value": int(getattr(pet, resource_id, 0) or 0),
                    "max": max_value,
                }
        return None

    def pets_for(sid):
        ps = match.state.get(sid)
        if not ps:
            return []
        packed = []
        for pet_id in sorted((ps.pets or {}).keys()):
            pet = ps.pets[pet_id]
            primary_resource = pet_primary_resource_for(pet)
            packed.append({
                "id": pet.id,
                "name": pet.name,
                "hp": int(pet.hp),
                "hp_max": int(pet.hp_max),
                "mp": int(getattr(pet, "mp", 0) or 0),
                "mp_max": int(getattr(pet, "mp_max", 0) or 0),
                "energy": int(getattr(pet, "energy", 0) or 0),
                "energy_max": int(getattr(pet, "energy_max", 0) or 0),
                "rage": int(getattr(pet, "rage", 0) or 0),
                "rage_max": int(getattr(pet, "rage_max", 0) or 0),
                "stats": {k: int(v or 0) for k, v in sorted((getattr(pet, "stats", {}) or {}).items())},
                "entity_type": pet.entity_type,
                "statuses": pet_statuses_for(pet),
                "primary_resource": primary_resource,
            })
        return packed

    def display_name_for(sid):
        class_name = class_name_for(sid)
        if sid == viewer_sid:
            return f"{class_name}(you)"
        return class_name

    def sid_token(sid):
        return sid[:5]

    def format_log_line(line):
        formatted = line
        for sid in match.players:
            formatted = formatted.replace(sid_token(sid), display_name_for(sid))
        if "{friendly_damage}" in formatted:
            formatted = formatted.format(
                turns=completed_turns,
                friendly_damage=friendly_totals.get("damage", 0),
                friendly_healing=friendly_totals.get("healing", 0),
                friendly_pet_healing=friendly_totals.get("pet_healing", 0),
                friendly_dpt=f"{friendly_dpt:.1f}",
                enemy_damage=enemy_totals.get("damage", 0),
                enemy_healing=enemy_totals.get("healing", 0),
                enemy_pet_healing=enemy_totals.get("pet_healing", 0),
                enemy_dpt=f"{enemy_dpt:.1f}",
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
            "display_color": ability_data.get("display_color"),
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
        "you_entity_type": entity_type_for(you),
        "enemy_entity_type": entity_type_for(enemy),
        "you_champion_mouseover": champion_mouseover_for(you),
        "enemy_champion_mouseover": champion_mouseover_for(enemy),
        "you_effects": effects_for(you),
        "enemy_effects": effects_for(enemy),
        "you_effect_panel": effect_panel_for(you),
        "enemy_effect_panel": effect_panel_for(enemy),
        "you_pets": pets_for(you),
        "enemy_pets": pets_for(enemy),
        "log": [
            format_log_line(line)
            for line in match.log[-SNAPSHOT_LOG_ENTRY_LIMIT:]
        ],
        "winner": match.winner,
        "friendly_total_damage": friendly_totals.get("damage", 0),
        "friendly_total_healing": friendly_totals.get("healing", 0),
        "friendly_total_pet_healing": friendly_totals.get("pet_healing", 0),
        "enemy_total_damage": enemy_totals.get("damage", 0),
        "enemy_total_healing": enemy_totals.get("healing", 0),
        "enemy_total_pet_healing": enemy_totals.get("pet_healing", 0),
        "completed_turns": completed_turns,
        "friendly_damage_per_turn": friendly_dpt,
        "enemy_damage_per_turn": enemy_dpt,
        "log_length": match.log.sequence,
        "friendly_cooldowns": friendly_cooldowns,
        "ability_meta": ability_meta,
    }


def _consume_protected_event(sid, event):
    decision = state.consume_event_token(sid, event)
    if decision.allowed:
        return True
    if decision.emit_warning:
        emit("duel_system", THROTTLE_WARNING)
    return False


def _deliver_match_setup_locked(socketio, match):
    p1, p2 = match.players
    socketio.server.enter_room(p1, match.room_id, namespace="/")
    socketio.server.enter_room(p2, match.room_id, namespace="/")

    socketio.emit("duel_role", "P1", to=p1)
    socketio.emit("duel_role", "P2", to=p2)
    socketio.emit(
        "duel_system",
        "Match found. Prep phase: pick class + items.",
        to=match.room_id,
    )
    socketio.emit("duel_prep_options", {
        "classes": CLASSES,
        "items": ITEMS,
        "abilities": ABILITIES,
    }, to=match.room_id)
    # Send initial snapshots to both players after role assignment resets cursors.
    socketio.emit("duel_snapshot", snapshot_for(match, p1), to=p1)
    socketio.emit("duel_snapshot", snapshot_for(match, p2), to=p2)
    return True


def deliver_match_setup(socketio, match):
    with match.turn_lock:
        if not state.is_registered_match(match):
            return False
        return _deliver_match_setup_locked(socketio, match)


def apply_detached_room_cleanup(socketio, detached):
    try:
        socketio.emit(
            "duel_system",
            detached.message,
            to=detached.room_id,
        )
    except Exception:
        logger.exception(
            "Failed lifecycle notice for room %s",
            detached.room_id,
        )
    try:
        socketio.close_room(detached.room_id)
    except Exception:
        logger.exception(
            "Failed Socket.IO room cleanup for room %s",
            detached.room_id,
        )


def apply_failed_setup_cleanup(socketio, detached):
    for sid in detached.players:
        try:
            socketio.emit("duel_system", detached.message, to=sid)
        except Exception:
            logger.exception("Failed direct match-setup failure notice")
    try:
        socketio.close_room(detached.room_id)
    except Exception:
        logger.exception(
            "Failed partial Socket.IO room cleanup for room %s",
            detached.room_id,
        )


def _notify_queue_expirations(socketio, expired_queue_sids):
    for sid in expired_queue_sids:
        try:
            socketio.emit("duel_system", QUEUE_EXPIRED_MESSAGE, to=sid)
        except Exception:
            logger.exception("Failed queue-expiration notice for SID")


def _match_expiration(match, now, policy):
    if match.phase == "ended":
        if match.ended_at is None:
            return None
        if now >= match.ended_at + policy.ended_grace_seconds:
            return "ended_grace", "Finished duel room expired."
        return None

    if match.phase == "prep":
        deadlines = (
            (
                match.last_gameplay_activity_at + policy.prep_idle_ttl_seconds,
                "prep_idle",
                "Prep room expired due to inactivity.",
            ),
            (
                match.phase_started_at + policy.prep_absolute_ttl_seconds,
                "prep_absolute",
                "Prep room reached its maximum lifetime.",
            ),
        )
    elif match.phase == "combat":
        deadlines = (
            (
                match.last_gameplay_activity_at + policy.combat_idle_ttl_seconds,
                "combat_idle",
                "Duel expired due to inactivity.",
            ),
            (
                match.phase_started_at + policy.combat_absolute_ttl_seconds,
                "combat_absolute",
                "Duel reached its maximum lifetime.",
            ),
        )
    else:
        raise ValueError(f"Unknown match phase {match.phase!r}")

    deadline, reason, message = min(deadlines, key=lambda entry: entry[0])
    if now >= deadline:
        return reason, message
    return None


def _recover_room_capacity(
    socketio,
    room_count,
    *,
    now,
    admission_policy,
):
    replacements = []
    pairing_attempt = 0
    expired_queue_sids = state.expire_queued_sids(
        now=now,
        policy=admission_policy,
    )
    _notify_queue_expirations(socketio, expired_queue_sids)
    for _slot in range(room_count):
        while True:
            seed = (int(now * 1000) + pairing_attempt) & 0xFFFFFFFF
            pairing_attempt += 1
            try:
                replacement = state.try_pair_waiting(
                    seed,
                    now=now,
                    policy=admission_policy,
                )
            except Exception:
                logger.exception("Failed replacement matchmaking")
                break
            if replacement is None:
                return tuple(replacements)

            try:
                delivered = deliver_match_setup(socketio, replacement)
            except Exception:
                logger.exception(
                    "Failed replacement setup for room %s",
                    replacement.room_id,
                )
                with replacement.turn_lock:
                    detached = state.detach_match_if_current(
                        replacement,
                        reason="setup_failed",
                        message=MATCH_SETUP_FAILED_MESSAGE,
                    )
                if detached is not None:
                    apply_failed_setup_cleanup(socketio, detached)
                continue

            if delivered:
                replacements.append(replacement.room_id)
                break
    return tuple(replacements)


def run_lifecycle_sweep(
    socketio,
    *,
    now=None,
    lifecycle_policy=None,
    admission_policy=None,
):
    current_time = state.current_monotonic_time() if now is None else now
    active_lifecycle_policy = (
        LIFECYCLE_POLICY if lifecycle_policy is None else lifecycle_policy
    )
    active_admission_policy = (
        state.ADMISSION_POLICY if admission_policy is None else admission_policy
    )
    expired_queue_sids = state.expire_queued_sids(
        now=current_time,
        policy=active_admission_policy,
    )
    matches = state.registered_matches_snapshot()
    detached_rooms = []
    skipped_busy = 0

    for match in matches:
        acquired = match.turn_lock.acquire(blocking=False)
        if not acquired:
            skipped_busy += 1
            continue
        try:
            if not state.is_registered_match(match):
                continue
            expiration = _match_expiration(
                match,
                current_time,
                active_lifecycle_policy,
            )
            if expiration is None:
                continue
            reason, message = expiration
            detached = state.detach_match_if_current(
                match,
                reason=reason,
                message=message,
            )
            if detached is not None:
                detached_rooms.append(detached)
        except Exception:
            logger.exception(
                "Failed lifecycle check for room %s",
                match.room_id,
            )
        finally:
            match.turn_lock.release()

    _notify_queue_expirations(socketio, expired_queue_sids)

    for detached in detached_rooms:
        apply_detached_room_cleanup(socketio, detached)

    replacement_room_ids = _recover_room_capacity(
        socketio,
        len(detached_rooms),
        now=current_time,
        admission_policy=active_admission_policy,
    )
    return SweepResult(
        expired_queue_sids=expired_queue_sids,
        detached_rooms=tuple(detached_rooms),
        skipped_busy=skipped_busy,
        replacement_room_ids=replacement_room_ids,
    )


def lifecycle_sweeper(socketio):
    while True:
        try:
            run_lifecycle_sweep(socketio)
        except Exception:
            logger.exception("Lifecycle sweep failed")
        socketio.sleep(LIFECYCLE_POLICY.sweep_interval_seconds)


def start_lifecycle_sweeper_once(socketio):
    global _lifecycle_sweeper_started
    with _lifecycle_sweeper_guard:
        if _lifecycle_sweeper_started:
            return False
        _lifecycle_sweeper_started = True
    try:
        socketio.start_background_task(lifecycle_sweeper, socketio)
    except Exception:
        with _lifecycle_sweeper_guard:
            _lifecycle_sweeper_started = False
        raise
    return True


def register_duel_socket_handlers(socketio):
    @socketio.on("connect")
    def duel_connect():
        sid = request.sid
        emit("duel_system", "Connected to Arena Server")
    
    @socketio.on("duel_queue")
    def duel_queue(*payload_args):
        sid = request.sid
        if not _consume_protected_event(sid, state.QUEUE_EVENT):
            return
        if not _accepts_empty_event_payload(payload_args):
            emit("duel_system", "Invalid queue submission.")
            return
        seed = int(time.time() * 1000) & 0xFFFFFFFF
        result = state.request_matchmaking(sid, seed)
        _notify_queue_expirations(
            socketio,
            tuple(
                expired_sid
                for expired_sid in result.expired_queue_sids
                if expired_sid != sid
            ),
        )
        if result.status == "already_in_duel":
            emit("duel_system", "Already in a duel.")
            return
        # Complete the existing setup transport before any intermediate
        # acknowledgement can yield and let disconnect cleanup remove the room.
        if result.match is not None:
            delivered = deliver_match_setup(socketio, result.match)
            if not delivered:
                emit("duel_system", MATCH_SETUP_INTERRUPTED_MESSAGE)
                return
        if result.status == "already_queued":
            emit("duel_system", "Already in queue.")
        elif result.status == "queue_full":
            emit("duel_system", "Matchmaking is currently full. Try again shortly.")
            return
        elif result.status == "room_full":
            emit("duel_system", "All duel rooms are currently occupied. You remain queued.")
        elif result.newly_queued:
            emit("duel_system", "Queued for DUEL...")

    @socketio.on("duel_prep_submit")
    def duel_prep_submit(*payload_args):
        sid = request.sid
        if not _consume_protected_event(sid, state.PREP_EVENT):
            return
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if len(payload_args) != 1:
            emit("duel_system", "Invalid prep submission.")
            return

        payload = payload_args[0]
        with match.turn_lock:
            if not state.is_registered_match(match, sid=sid):
                emit("duel_system", "Not in a duel.")
                return
            if match.phase != "prep":
                emit("duel_system", "Prep phase is over.")
                return
            if match.locked_in.get(sid):
                emit("duel_system", "Your build is locked in and cannot be changed.")
                return
            current = match.picks.get(sid, {})
            try:
                merged = _canonical_prep_pick(current, payload)
            except ValueError as exc:
                emit("duel_system", str(exc))
                return

            # Commit only after the complete proposed class/equipment build passes.
            changed = merged != current
            match.picks[sid] = merged
            if changed:
                match.last_gameplay_activity_at = state.current_monotonic_time()
            selection_name = _prep_selection_name(payload)
            if selection_name:
                emit("duel_system", f"🛡️ Prep saved, {selection_name}.")
            else:
                emit("duel_system", "🛡️ Prep saved.")
            try_start_combat(match)

    def both_players_locked(match):
        return all(match.locked_in.get(sid) for sid in match.players)

    def player_has_class(match, sid):
        return _picked_class_id(match, sid) is not None

    def try_start_combat(match):
        if match.phase != "prep":
            return
        if not both_players_locked(match):
            return
        if not all(player_has_class(match, sid) for sid in match.players):
            for player_sid in match.players:
                picked = match.picks.get(player_sid, {})
                if isinstance(picked, dict) and picked.get("class_id") and not normalize_class_id(picked.get("class_id")):
                    socketio.emit("duel_system", _invalid_class_message(picked.get("class_id")), to=player_sid)
            return
        try:
            resolver.apply_prep_build(match)
        except ValueError as exc:
            for player_sid in match.players:
                socketio.emit("duel_system", f"Cannot start combat: {exc}", to=player_sid)
            return
        transition_at = state.current_monotonic_time()
        match.phase = "combat"
        match.phase_started_at = transition_at
        match.last_gameplay_activity_at = transition_at
        match.ended_at = None
        socketio.emit("duel_snapshot", snapshot_for(match, match.players[0]), to=match.players[0])
        socketio.emit("duel_snapshot", snapshot_for(match, match.players[1]), to=match.players[1])
        socketio.emit("duel_system", "Combat begins.", to=match.room_id)

    @socketio.on("duel_lock_in")
    def duel_lock_in(*payload_args):
        sid = request.sid
        if not _consume_protected_event(sid, state.LOCK_EVENT):
            return
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if not _accepts_empty_event_payload(payload_args):
            emit("duel_system", "Invalid lock-in submission.")
            return
        with match.turn_lock:
            if not state.is_registered_match(match, sid=sid):
                emit("duel_system", "Not in a duel.")
                return
            if match.phase != "prep":
                emit("duel_system", "Prep phase is over.")
                return
            if not player_has_class(match, sid):
                picked = match.picks.get(sid, {})
                attempted_class_id = picked.get("class_id") if isinstance(picked, dict) else None
                emit("duel_system", _invalid_class_message(attempted_class_id))
                return
            first_lock_in = not match.locked_in.get(sid)
            match.locked_in[sid] = True
            if first_lock_in:
                match.last_gameplay_activity_at = state.current_monotonic_time()
            emit("duel_system", "Locked in. Waiting for opponent...")
            try_start_combat(match)

    @socketio.on("duel_action")
    def duel_action(*payload_args):
        sid = request.sid
        if not _consume_protected_event(sid, state.ACTION_EVENT):
            return
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if len(payload_args) != 1:
            emit("duel_system", "Invalid action submission.")
            return

        payload = payload_args[0]
        try:
            action = _parse_action_payload(payload)
        except ValueError as exc:
            emit("duel_system", str(exc))
            return
        ability_id = action["ability_id"]
        with match.turn_lock:
            if not state.is_registered_match(match, sid=sid):
                emit("duel_system", "Not in a duel.")
                return
            if match.phase != "combat":
                emit("duel_system", "Prep phase: choose class/items before combat.")
                return
            first_submission = sid not in match.submitted
            resolver.submit_action(match, sid, action)
            if first_submission:
                match.last_gameplay_activity_at = state.current_monotonic_time()
            ability_name = ABILITIES.get(ability_id, {}).get("name", ability_id)
            cooldown_remaining = 0
            ps = match.state.get(sid)
            if ps:
                cooldown_remaining = resolver.cooldown_remaining(ps, ability_id, ABILITIES.get(ability_id, {}))
            if cooldown_remaining > 0:
                emit("duel_system", f"🛡️ Action received. Warning {ability_name} is on cooldown.")
            else:
                emit("duel_system", f"🛡️ Action received. {ability_name}")

            if resolver.ready_to_resolve(match) and not match.turn_in_progress:
                payload_key = resolver.resolution_key(match)
                if payload_key != match.last_resolved_key:
                    match.turn_in_progress = True
                    try:
                        resolver.resolve_turn(match)
                    except Exception:
                        match.turn_in_progress = False
                        emit("duel_system", "Turn resolution failed. Please submit your action again.")
                        raise
                    if match.phase == "ended" and match.ended_at is None:
                        match.ended_at = state.current_monotonic_time()
                    socketio.emit("duel_snapshot", snapshot_for(match, match.players[0]), to=match.players[0])
                    socketio.emit("duel_snapshot", snapshot_for(match, match.players[1]), to=match.players[1])
                    if match.phase == "ended":
                        socketio.emit("duel_system", "Duel ended.", to=match.room_id)

    @socketio.on("duel_chat")
    def duel_chat(*payload_args):
        sid = request.sid
        if not _consume_protected_event(sid, state.CHAT_EVENT):
            return
        match = state.get_match_by_sid(sid)
        if not match:
            emit("duel_system", "Not in a duel.")
            return
        if len(payload_args) != 1:
            emit("duel_system", "Chat messages must be text.")
            return
        payload = payload_args[0]
        try:
            message = _parse_chat_payload(payload)
        except ValueError as exc:
            emit("duel_system", str(exc))
            return
        
        with match.turn_lock:
            if not state.is_registered_match(match, sid=sid):
                emit("duel_system", "Not in a duel.")
                return
            p1, _p2 = match.players
            role = "P1" if sid == p1 else "P2"

            # Chat is intentionally not gameplay activity and never refreshes TTLs.
            player_class = _picked_class_name(match, sid)
            socketio.emit("duel_chat", {
                "playerClass": player_class,
                "message": message,
                "role": role
            }, to=match.room_id)

    @socketio.on("disconnect")
    def duel_disconnect(_reason=None):
        sid = request.sid
        match = state.disconnect_sid(sid)
        if not match:
            return
        with match.turn_lock:
            if not state.is_registered_match(match, sid=sid):
                return
            detached = state.detach_match_if_current(
                match,
                reason="disconnect",
                message="Opponent disconnected.",
            )
        if detached is None:
            return
        apply_detached_room_cleanup(socketio, detached)
        _recover_room_capacity(
            socketio,
            1,
            now=state.current_monotonic_time(),
            admission_policy=state.ADMISSION_POLICY,
        )
