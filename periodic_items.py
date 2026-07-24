"""Global scheduling and dispatch for periodic equipped-item passives."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Sequence

from .damage_types import DAMAGE_SOURCE_PERIODIC_ITEM
from .dice import roll
from .effects import (
    damage_multiplier_from_passives,
    is_damage_immune,
    modify_stat,
    outgoing_damage_multiplier,
)
from .models import MatchState, PetState, PlayerState, combat_totals_entry
from ..content.items import ITEMS


PERIODIC_ITEM_TRIGGER = "periodic_end_of_turn"
PERIODIC_GLOBAL_DAMAGE_HANDLER = "periodic_global_damage"

# PlayerBuild currently supports these equipment slots. Periodic item ordering
# must use this tuple rather than build/items dictionary insertion order.
EQUIPMENT_SLOT_ORDER = ("weapon", "armor", "trinket")


@dataclass(frozen=True)
class PeriodicItemActivation:
    """Immutable identity plus copied metadata for one scheduled item effect."""

    owner_sid: str
    item_slot: str
    item_id: str
    passive_type: str
    passive_metadata: Mapping[str, Any]
    passive_index: int


@dataclass(frozen=True)
class PeriodicItemHandlerContext:
    """Reusable engine services exposed to periodic-item handlers."""

    match: MatchState
    global_turn: int
    rng: Any
    player_sids: tuple[str, ...]
    turn_context: Any
    apply_damage: Callable[..., Dict[str, Any]]


PeriodicItemHandler = Callable[
    [PeriodicItemActivation, PeriodicItemHandlerContext],
    None,
]


def _periodic_global_targets(
    context: PeriodicItemHandlerContext,
) -> tuple[tuple[str, PlayerState | PetState, bool], ...]:
    """Snapshot players and living owned entities in deterministic order."""

    targets: list[tuple[str, PlayerState | PetState, bool]] = []
    for player_sid in context.player_sids:
        player = context.match.state.get(player_sid)
        if player is None:
            continue
        targets.append((player_sid, player, True))
        for pet_id in sorted((player.pets or {}).keys()):
            pet = player.pets.get(pet_id)
            if pet is not None and pet.hp > 0:
                targets.append((player_sid, pet, False))
    return tuple(targets)


def _periodic_global_damage_formula(
    owner: PlayerState,
    passive: Mapping[str, Any],
    context: PeriodicItemHandlerContext,
) -> int:
    scaling = passive.get("scaling")
    if not isinstance(scaling, Mapping) or not scaling:
        raise ValueError("periodic_global_damage requires non-empty scaling metadata")

    scaling_stats = scaling.get("stats")
    if not isinstance(scaling_stats, (list, tuple)) or not scaling_stats:
        raise ValueError("periodic_global_damage requires grouped scaling stats")
    normalized_stats: list[str] = []
    for stat in scaling_stats:
        if stat not in {"atk", "int"}:
            raise ValueError(
                f"periodic_global_damage does not support scaling stat '{stat}'"
            )
        normalized_stats.append(stat)
    if len(normalized_stats) != len(set(normalized_stats)):
        raise ValueError("periodic_global_damage scaling stats must be unique")

    multiplier = scaling.get("multiplier")
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        raise ValueError("periodic_global_damage scaling multiplier must be numeric")
    if scaling.get("rounding") != "floor":
        raise ValueError("periodic_global_damage requires floor rounding")

    current_stat_total = sum(
        modify_stat(owner, stat, owner.stats.get(stat, 0))
        for stat in normalized_stats
    )
    scaled_damage = int(current_stat_total * float(multiplier))

    dice = passive.get("dice")
    if not isinstance(dice, str) or not dice.strip():
        raise ValueError("periodic_global_damage requires dice metadata")
    return scaled_damage + roll(dice.strip(), context.rng)


def _periodic_outgoing_damage(
    owner: PlayerState,
    base_raw_damage: int,
    context: PeriodicItemHandlerContext,
) -> int:
    challenger_mode_by_sid = getattr(
        context.turn_context,
        "challenger_mode_by_sid",
        None,
    )
    if isinstance(challenger_mode_by_sid, Mapping):
        passive_multiplier = damage_multiplier_from_passives(
            owner,
            challenger_mode=challenger_mode_by_sid.get(owner.sid),
        )
    else:
        passive_multiplier = damage_multiplier_from_passives(owner)
    effect_multiplier = outgoing_damage_multiplier(owner)

    # Match the canonical damage-modification ordering: passive first, then
    # effect-owned outgoing multipliers, with integer rounding at each stage.
    outgoing_damage = base_raw_damage
    if passive_multiplier != 1.0:
        outgoing_damage = int(outgoing_damage * passive_multiplier)
    if effect_multiplier != 1.0:
        outgoing_damage = int(outgoing_damage * effect_multiplier)
    return outgoing_damage


def _periodic_target_log_label(owner_sid: str, target: PlayerState | PetState) -> str:
    if isinstance(target, PlayerState):
        return target.sid[:5]
    return f"{owner_sid[:5]}'s {target.name} ({target.id})"


def _periodic_absorb_suffix(damage_result: Mapping[str, Any]) -> str:
    absorbed = int(damage_result.get("absorbed", 0) or 0)
    if absorbed <= 0:
        return ""
    parts: list[str] = []
    for entry in damage_result.get("absorbed_breakdown", []) or []:
        amount = int(entry.get("amount", 0) or 0)
        if amount > 0:
            parts.append(f"{amount} absorbed by {entry.get('name') or 'Shield'}")
    if not parts:
        parts.append(f"{absorbed} absorbed by Shield")
    return f" ({', '.join(parts)})"


def periodic_global_damage(
    activation: PeriodicItemActivation,
    context: PeriodicItemHandlerContext,
) -> None:
    """Resolve one metadata-driven global damage pulse from an equipped item."""

    passive = activation.passive_metadata
    if passive.get("target_mode") != "all_players_and_pets":
        raise ValueError(
            "periodic_global_damage requires target_mode='all_players_and_pets'"
        )

    owner = context.match.state.get(activation.owner_sid)
    if owner is None:
        raise ValueError(
            f"Periodic item owner '{activation.owner_sid}' is missing from match state"
        )

    school = str(passive.get("school") or "").strip().lower()
    if school not in {"physical", "magical"}:
        raise ValueError("periodic_global_damage requires a canonical damage school")
    subschool_value = passive.get("subschool")
    subschool = (
        str(subschool_value).strip().lower()
        if isinstance(subschool_value, str) and subschool_value.strip()
        else None
    )
    item = ITEMS.get(activation.item_id, {})
    item_name = str(item.get("name") or activation.item_id)

    # Target eligibility is fixed before the first packet of this activation.
    # A later committed activation creates its own fresh snapshot.
    target_snapshot = _periodic_global_targets(context)
    base_raw_damage = _periodic_global_damage_formula(owner, passive, context)
    # Snapshot owner-side multipliers once. Self-damage and target reactions
    # from this pulse must not change later packets in the same activation.
    outgoing_raw_damage = _periodic_outgoing_damage(
        owner,
        base_raw_damage,
        context,
    )
    context.match.log.append(f"{owner.sid[:5]} triggers {item_name}.")

    total_hp_damage = 0
    damage_label = subschool.title() if subschool else school.title()
    immunity_school = "physical" if school == "physical" else "magic"
    for target_owner_sid, target, is_player_target in target_snapshot:
        immune_before_application = is_damage_immune(target, immunity_school)
        log_length_before_application = len(context.match.log)
        target_damage = context.apply_damage(
            owner,
            target,
            outgoing_raw_damage,
            target.sid if is_player_target else target.name,
            item_name,
            mindgames_flip_damage=False,
            damage_instances=[outgoing_raw_damage],
            school=school,
            subschool=subschool,
            allow_redirect=False,
            resolve_non_player_mitigation=not is_player_target,
            resolve_player_mitigation=is_player_target,
            source_kind=DAMAGE_SOURCE_PERIODIC_ITEM,
        )
        target_label = _periodic_target_log_label(target_owner_sid, target)
        if immune_before_application:
            # Cloak/Cyclone already emit their canonical shared-pipeline line;
            # full immunity and pet immunity need a handler-owned result line.
            if len(context.match.log) == log_length_before_application:
                context.match.log.append(
                    f"{item_name} cannot harm {target_label}; the target is immune."
                )
            continue

        hp_damage = int(target_damage.get("hp_damage", 0) or 0)
        absorbed = int(target_damage.get("absorbed", 0) or 0)
        total_hp_damage += hp_damage
        context.match.log.append(
            f"{item_name} hits {target_label} for {hp_damage + absorbed} "
            f"{damage_label} damage.{_periodic_absorb_suffix(target_damage)}"
        )

    combat_totals_entry(
        context.match.combat_totals,
        activation.owner_sid,
    )["damage"] += total_hp_damage


PERIODIC_ITEM_HANDLERS: dict[str, PeriodicItemHandler] = {
    PERIODIC_GLOBAL_DAMAGE_HANDLER: periodic_global_damage,
}


def _periodic_metadata_error(
    item_id: str,
    passive_index: int,
    detail: str,
) -> ValueError:
    return ValueError(
        f"Invalid periodic item metadata for item '{item_id}' "
        f"passive index {passive_index}: {detail}"
    )


def _validated_schedule(
    item_id: str,
    passive_index: int,
    passive: Mapping[str, Any],
) -> tuple[str, int, int]:
    passive_type = passive.get("type")
    if not isinstance(passive_type, str) or not passive_type.strip():
        raise _periodic_metadata_error(
            item_id,
            passive_index,
            "type must be a non-empty string",
        )

    if "interval" not in passive:
        raise _periodic_metadata_error(
            item_id,
            passive_index,
            "interval is required",
        )
    interval = passive.get("interval")
    if type(interval) is not int or interval < 1:
        raise _periodic_metadata_error(
            item_id,
            passive_index,
            "interval must be a positive integer",
        )

    if "first_trigger_turn" not in passive:
        raise _periodic_metadata_error(
            item_id,
            passive_index,
            "first_trigger_turn is required",
        )
    first_trigger_turn = passive.get("first_trigger_turn")
    if type(first_trigger_turn) is not int or first_trigger_turn < 1:
        raise _periodic_metadata_error(
            item_id,
            passive_index,
            "first_trigger_turn must be a positive integer",
        )

    return passive_type.strip(), interval, first_trigger_turn


def _passive_entries(item: Mapping[str, Any]) -> tuple[tuple[int, Mapping[str, Any]], ...]:
    passive_data = item.get("passive")
    if isinstance(passive_data, Mapping):
        return ((0, passive_data),)
    if isinstance(passive_data, list):
        return tuple(
            (index, passive)
            for index, passive in enumerate(passive_data)
            if isinstance(passive, Mapping)
        )
    return ()


def collect_periodic_item_activations(
    match: MatchState,
    current_turn: int,
) -> tuple[PeriodicItemActivation, ...]:
    """Collect one ordered activation snapshot from canonical equipment state."""

    activations: list[PeriodicItemActivation] = []
    for owner_sid in tuple(match.players):
        owner = match.state.get(owner_sid)
        if not owner or not owner.build:
            continue
        equipped_items = owner.build.items or {}
        for item_slot in EQUIPMENT_SLOT_ORDER:
            item_id = equipped_items.get(item_slot)
            if not item_id:
                continue
            item = ITEMS.get(item_id)
            if not item:
                continue
            for passive_index, passive in _passive_entries(item):
                if passive.get("trigger") != PERIODIC_ITEM_TRIGGER:
                    continue
                passive_type, interval, first_trigger_turn = _validated_schedule(
                    item_id,
                    passive_index,
                    passive,
                )
                if current_turn < first_trigger_turn:
                    continue
                if (current_turn - first_trigger_turn) % interval != 0:
                    continue
                passive_snapshot = MappingProxyType(deepcopy(dict(passive)))
                activations.append(
                    PeriodicItemActivation(
                        owner_sid=owner_sid,
                        item_slot=item_slot,
                        item_id=item_id,
                        passive_type=passive_type,
                        passive_metadata=passive_snapshot,
                        passive_index=passive_index,
                    )
                )
    return tuple(activations)


def dispatch_periodic_item_activations(
    activations: Sequence[PeriodicItemActivation],
    context: PeriodicItemHandlerContext,
    *,
    before_dispatch: Callable[[], None] | None = None,
) -> None:
    """Resolve all handler lookups before executing the activation snapshot."""

    dispatch_plan: list[tuple[PeriodicItemHandler, PeriodicItemActivation]] = []
    for activation in activations:
        handler = PERIODIC_ITEM_HANDLERS.get(activation.passive_type)
        if handler is None:
            raise ValueError(
                f"Unknown periodic item handler type '{activation.passive_type}' "
                f"for item '{activation.item_id}'"
            )
        dispatch_plan.append((handler, activation))

    if dispatch_plan and before_dispatch is not None:
        before_dispatch()

    for handler, activation in dispatch_plan:
        handler(activation, context)


def resolve_periodic_item_stage(
    *,
    match: MatchState,
    rng: Any,
    turn_context: Any,
    apply_damage: Callable[..., Dict[str, Any]],
    before_dispatch: Callable[[], None] | None = None,
) -> tuple[PeriodicItemActivation, ...]:
    """Snapshot and dispatch scheduled item effects once for the active turn."""

    # match.turn counts completed turns until resolve_turn finishes. The stage
    # therefore schedules against the one-based global turn being resolved.
    global_turn = int(match.turn) + 1
    player_sids = tuple(match.players)
    activations = collect_periodic_item_activations(match, global_turn)
    context = PeriodicItemHandlerContext(
        match=match,
        global_turn=global_turn,
        rng=rng,
        player_sids=player_sids,
        turn_context=turn_context,
        apply_damage=apply_damage,
    )
    dispatch_periodic_item_activations(
        activations,
        context,
        before_dispatch=before_dispatch,
    )
    return activations
