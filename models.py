# games/duel/engine/models.py
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

from ..availability import BoundedCombatLog, DEFAULT_AVAILABILITY_POLICY

# Canonical per-combatant combat-total keys. "healing"/"overhealing" account
# player-produced healing; "pet_healing"/"pet_overhealing" account healing
# produced by pets/totems/summons (stored under the owner SID for routing, but
# never rolled into the owner's regular "healing"). Overhealing is requested
# healing lost only to an upper max-HP cap and is excluded from effective
# healing totals.
COMBAT_TOTAL_KEYS = ("damage", "healing", "pet_healing", "overhealing", "pet_overhealing")


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
    log: BoundedCombatLog | List[str] = field(default_factory=BoundedCombatLog)
    winner: Optional[str] = None
    combat_totals: Dict[str, Dict[str, int]] = field(default_factory=dict)
    turn_in_progress: bool = False
    last_resolved_key: Optional[str] = None
    turn_lock: RLock = field(default_factory=RLock)
    max_retained_log_entries: int = DEFAULT_AVAILABILITY_POLICY.max_retained_log_entries
    created_at: Optional[float] = None
    phase_started_at: Optional[float] = None
    last_gameplay_activity_at: Optional[float] = None
    ended_at: Optional[float] = None
    availability_closed: bool = False
    availability_transport_setup_in_progress: bool = False
    availability_resolution_in_progress: bool = False
    availability_pending_cleanup_reason: Optional[str] = None
    availability_pending_cleanup_message: Optional[str] = None
    monotonic_clock: Callable[[], float] = field(
        default_factory=lambda: __import__("time").monotonic,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        existing_sequence = int(getattr(self.log, "sequence", len(self.log)))
        self.log = BoundedCombatLog(
            self.log,
            max_entries=self.max_retained_log_entries,
            sequence=existing_sequence,
        )
        now = float(self.monotonic_clock())
        if self.created_at is None:
            self.created_at = now
        if self.phase_started_at is None:
            self.phase_started_at = now
        if self.last_gameplay_activity_at is None:
            self.last_gameplay_activity_at = now
        if self.phase == "ended" and self.ended_at is None:
            self.ended_at = now

    @property
    def log_sequence(self) -> int:
        return self.log.sequence

    def mark_gameplay_activity(self, now: Optional[float] = None) -> None:
        self.last_gameplay_activity_at = (
            float(self.monotonic_clock()) if now is None else float(now)
        )

    def mark_phase_started(self, phase: str, now: Optional[float] = None) -> None:
        timestamp = float(self.monotonic_clock()) if now is None else float(now)
        if self.phase != phase:
            self.phase = phase
            self.phase_started_at = timestamp
        self.last_gameplay_activity_at = timestamp
        if phase == "ended" and self.ended_at is None:
            self.ended_at = timestamp
