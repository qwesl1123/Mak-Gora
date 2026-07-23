"""Global scheduling and dispatch for periodic equipped-item passives."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Sequence

from .models import MatchState
from ..content.items import ITEMS


PERIODIC_ITEM_TRIGGER = "periodic_end_of_turn"

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


# Production periodic-item handlers register here. The foundation intentionally
# starts empty because no production periodic item is part of this change.
PERIODIC_ITEM_HANDLERS: dict[str, PeriodicItemHandler] = {}


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
