# games/duel/engine/models.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

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
class PlayerState:
    sid: str
    build: PlayerBuild = field(default_factory=PlayerBuild)
    res: Optional[Resources] = None
    stats: Dict[str, int] = field(default_factory=dict)     # atk/def/spd/crit/acc/eva...
    effects: List[Dict[str, Any]] = field(default_factory=list)  # buffs/debuffs
    cooldowns: Dict[str, list[int]] = field(default_factory=dict)
    minions: Dict[str, int] = field(default_factory=dict)

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
    log: List[str] = field(default_factory=list)
    winner: Optional[str] = None
    combat_totals: Dict[str, Dict[str, int]] = field(default_factory=dict)
