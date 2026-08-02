# games/duel/engine/models.py
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional

from ..availability import RESOURCE_LIMITS

# Canonical per-combatant combat-total keys. "healing"/"overhealing" account
# player-produced healing; "pet_healing"/"pet_overhealing" account healing
# produced by pets/totems/summons (stored under the owner SID for routing, but
# never rolled into the owner's regular "healing"). Overhealing is requested
# healing lost only to an upper max-HP cap and is excluded from effective
# healing totals.
COMBAT_TOTAL_KEYS = ("damage", "healing", "pet_healing", "overhealing", "pet_overhealing")


class BoundedCombatLog(list[str]):
    """Chronological list-compatible log with bounded retained history."""

    def __init__(
        self,
        entries: Iterable[str] = (),
        *,
        capacity: int,
        sequence: int | None = None,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("Combat-log capacity must be a positive integer")

        supplied_entries = list(entries)
        historical_count = len(supplied_entries) if sequence is None else sequence
        if (
            isinstance(historical_count, bool)
            or not isinstance(historical_count, int)
            or historical_count < len(supplied_entries)
        ):
            raise ValueError(
                "Combat-log sequence must be an integer at least as large as "
                "the supplied retained history"
            )

        self.capacity = capacity
        self.sequence = historical_count
        super().__init__(supplied_entries[-capacity:])

    def _evict_oldest(self) -> None:
        overflow = len(self) - self.capacity
        if overflow > 0:
            super().__delitem__(slice(0, overflow))

    def append(self, entry: str) -> None:
        super().append(entry)
        self.sequence += 1
        self._evict_oldest()

    def extend(self, entries: Iterable[str]) -> None:
        new_entries = list(entries)
        if not new_entries:
            return
        super().extend(new_entries)
        self.sequence += len(new_entries)
        self._evict_oldest()

    def __iadd__(self, entries: Iterable[str]):
        self.extend(entries)
        return self

    @staticmethod
    def _reject_non_append_mutation(*_args, **_kwargs):
        raise TypeError(
            "BoundedCombatLog is append-only; use append() or extend()"
        )

    __setitem__ = _reject_non_append_mutation
    __delitem__ = _reject_non_append_mutation
    __imul__ = _reject_non_append_mutation
    insert = _reject_non_append_mutation
    clear = _reject_non_append_mutation
    pop = _reject_non_append_mutation
    remove = _reject_non_append_mutation
    reverse = _reject_non_append_mutation
    sort = _reject_non_append_mutation


def new_combat_totals() -> Dict[str, int]:
    return {key: 0 for key in COMBAT_TOTAL_KEYS}


def combat_totals_entry(combat_totals: Dict[str, Dict[str, int]], sid: str) -> Dict[str, int]:
    """Return the mutable totals dict for ``sid``, creating/backfilling keys.

    Older matches (or tests that build MatchState directly) may carry totals
    dicts without the newer keys; backfilling here lets every crediting site
    increment any canonical key without defensive .get() chains.
    """
    totals = combat_totals.setdefault(sid, {})
    for key in COMBAT_TOTAL_KEYS:
        totals.setdefault(key, 0)
    return totals

@dataclass
class Resources:
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    energy: int
    energy_max: int
    rage: int
    rage_max: int
    absorbs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

@dataclass
class PlayerBuild:
    class_id: Optional[str] = None
    items: Dict[str, Optional[str]] = field(default_factory=lambda: {
        "weapon": None,
        "armor": None,
        "trinket": None,
    })

@dataclass
class PetState:
    id: str
    template_id: str
    name: str
    owner_sid: str
    hp: int
    hp_max: int
    mp: int = 0
    mp_max: int = 0
    energy: int = 0
    energy_max: int = 0
    rage: int = 0
    rage_max: int = 0
    stats: Dict[str, int] = field(default_factory=dict)
    effects: List[Dict[str, Any]] = field(default_factory=list)
    duration: Optional[int] = None
    action_consumed: bool = False
    action_state: str = "ready"
    entity_type: Optional[str] = None

@dataclass
class PlayerState:
    sid: str
    entity_type: str = "humanoid"
    build: PlayerBuild = field(default_factory=PlayerBuild)
    res: Optional[Resources] = None
    stats: Dict[str, int] = field(default_factory=dict)     # atk/def/spd/crit/acc/eva...
    effects: List[Dict[str, Any]] = field(default_factory=list)  # buffs/debuffs
    cooldowns: Dict[str, list[int]] = field(default_factory=dict)
    pets: Dict[str, PetState] = field(default_factory=dict)
    hunter_pet_memory: Dict[str, Dict[str, int]] = field(default_factory=dict)
    dead_hunter_pets: Dict[str, bool] = field(default_factory=dict)
    active_pet_id: Optional[str] = None
    pending_pet_command: Optional[str] = None

@dataclass
class MatchState:
    room_id: str
    players: List[str]                     # [p1_sid, p2_sid]
    phase: str = "prep"                    # "prep" | "combat" | "ended"
    turn: int = 0
    seed: int = 0                          # for deterministic dice
    picks: Dict[str, PlayerBuild] = field(default_factory=dict)
    locked_in: Dict[str, bool] = field(default_factory=dict)
    submitted: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # per-turn action
    state: Dict[str, PlayerState] = field(default_factory=dict)         # sid -> PlayerState
    max_retained_log_entries: int = field(
        default_factory=lambda: RESOURCE_LIMITS.max_retained_log_entries
    )
    log: BoundedCombatLog | List[str] = field(default_factory=list)
    winner: Optional[str] = None
    combat_totals: Dict[str, Dict[str, int]] = field(default_factory=dict)
    turn_in_progress: bool = False
    last_resolved_key: Optional[str] = None
    turn_lock: RLock = field(default_factory=RLock)

    def __post_init__(self) -> None:
        existing_sequence = (
            self.log.sequence if isinstance(self.log, BoundedCombatLog) else None
        )
        self.log = BoundedCombatLog(
            self.log,
            capacity=self.max_retained_log_entries,
            sequence=existing_sequence,
        )
