# Mak'Gora ⚔️

### *Duel to the Death*

Mak'Gora is a **World of Warcraft–inspired** dueling mini-game — a fun, fast browser game where you queue up and challenge a friend (or a stranger) to a live 1v1 fight to the death. Pick a class, gear up, and outplay your opponent turn by turn using abilities, cooldowns, and combos ripped straight from the spirit of Azeroth.

> This is a fan project built for fun, inspired by WoW's classes and combat — not affiliated with or endorsed by Blizzard.

## 🚧 Active Development

Mak'Gora is under active, ongoing development. The 1v1 PvP duel is live today, and the in-game mode select already teases what's coming next:

- **Mak'Gora (PvP)** — ✅ live now
- **Dungeons** — single-player PvE — 🔒 coming soon
- **Raids** — epic encounters — 🔒 coming soon
- **More Modes** — 🔒 uncharted realms

The current development priority is **completing the remaining playable classes on top of the stabilized combat architecture before expanding into larger PvE work.** See the [Roadmap](ROADMAP.md) for the current phase and class progress.

Expect frequent balance passes, new abilities, and new modes as this keeps growing.

## ✨ Notable Features

- **9 playable classes** — Warrior, Mage, Rogue, Warlock, Druid, Paladin, Priest, Hunter, and Shaman, each with authentic resource systems (Rage, Mana, Energy) and their own playstyle
- **Druid shapeshifting** — swap between Bear, Cat, Moonkin, and Tree forms, each with its own resource and kit
- **Real-time PvP duels** — matchmaking and live combat resolved over WebSockets, so you and a friend can jump straight into a fight
- **Deep spell system** — dozens of named abilities per class (Pyroblast, Mortal Strike, Kidney Shot, Vampiric Touch, Chain Lightning, and more), with dice-roll + stat-scaling damage and real hit/crit/mitigation math
- **DoTs, crowd control & cooldowns** — Corruption, Agony, Fear, Stuns, Ice Block, Divine Shield, Power Word: Shield, and other classic defensive/offensive tools
- **Pets & totems with their own AI** — summon companions like the Imp, Shadowfiend, Frostsaber, or Mana Tide Totem that act on their own each turn
- **Gear & loot** — equip weapons, armor, and trinkets, including legendary items like Thunderfury and Twin Blades of Azzinoth with unique passive effects
- **Live "War Council" panel** — track active pets, totems, and stealth status for both duelists mid-fight
- **Deterministic combat rolls** — seeded per-match RNG keeps fights fair and reproducible

## 🚀 Running It Yourself

Rough overview, no need to overthink it:

1. Have Python 3 installed
2. Install the dependencies: `pip install flask flask-socketio eventlet`
3. Run the app: `python app.py`
4. Open your browser to the address it prints and start dueling

### Resource limits

Each duel retains the latest 500 combat-log entries while snapshots continue
to expose the latest 30. Inbound Socket.IO messages are limited to 16 KiB.
Set `MAKGORA_MAX_RETAINED_LOG_ENTRIES` or
`MAKGORA_SOCKET_MAX_BUFFER_BYTES` before startup to override those defaults.
The retained-log value must be at least 30 and the socket buffer at least 4096
bytes; invalid explicit values stop startup with a configuration error.

Matchmaking retains at most 100 queued Socket.IO SIDs and 50 duel rooms by
default. Queue entries expire after 15 minutes, both lazily when matchmaking is
accessed and proactively by the lifecycle sweeper. Expired clients receive a
SID-local notice and must queue again. `MAKGORA_MAX_QUEUED_SIDS`,
`MAKGORA_MAX_ACTIVE_ROOMS`, and `MAKGORA_QUEUE_TTL_SECONDS` override these
values. Room IDs use a process-lifetime monotonic sequence and are not reused
after cleanup.

Retained rooms use these default monotonic lifecycle deadlines:

| Phase | Idle expiration | Absolute expiration / grace |
| --- | --- | --- |
| Prep | 10 minutes | 30 minutes absolute |
| Combat | 15 minutes | 2 hours absolute |
| Ended | n/a | 2 minutes after the duel ends |

The one process-local sweeper runs every 30 seconds. Deadlines are inclusive:
a room or queue entry is eligible when `now >= deadline`. Accepted
state-changing prep submissions, a SID's first lock-in, the prep-to-combat
transition, and accepted first action submissions for a turn refresh idle
activity. Malformed, rejected, duplicate, unauthorized, or throttled events do
not; neither do queue requests, snapshots, system messages, or chat. Absolute
deadlines never refresh.

Override lifecycle values with
`MAKGORA_PREP_IDLE_TTL_SECONDS`,
`MAKGORA_PREP_ABSOLUTE_TTL_SECONDS`,
`MAKGORA_COMBAT_IDLE_TTL_SECONDS`,
`MAKGORA_COMBAT_ABSOLUTE_TTL_SECONDS`,
`MAKGORA_ENDED_GRACE_SECONDS`, and
`MAKGORA_LIFECYCLE_SWEEP_INTERVAL_SECONDS`. Every value must be finite and
positive. Prep/combat absolute TTLs must be at least their corresponding idle
TTLs; invalid explicit configuration stops startup.

Admission uses one Eventlet registry semaphore, and each match uses one
Eventlet semaphore for setup, gameplay operations, disconnect, and lifecycle
cleanup. The lock order is match semaphore then registry semaphore. The
sweeper never waits for a busy match; disconnect waits cooperatively for only
the affected match. All room removal uses one atomic state-detachment path,
with Socket.IO notices/closure and FIFO capacity-recovery setup performed after
locks are released. A detached room can promote at most one waiting pair, and
individual transport failures do not stop later cleanup.

Expiration may occur up to one sweep interval late when a match is busy.
This is intentional and avoids cleanup leases or a deferred-operation state
machine.

High-frequency client events use per-SID token buckets:

| Event | Sustained rate | Burst |
| --- | --- | --- |
| `duel_queue` | 3 events / 10 seconds | 3 |
| `duel_prep_submit` | 12 events / 10 seconds | 12 |
| `duel_lock_in` | 4 events / 10 seconds | 4 |
| `duel_action` | 10 events / 10 seconds | 8 |
| `duel_chat` | 8 events / 10 seconds | 5 |

Use `MAKGORA_<QUEUE|PREP|LOCK|ACTION|CHAT>_RATE_EVENTS`,
`MAKGORA_<QUEUE|PREP|LOCK|ACTION|CHAT>_RATE_WINDOW_SECONDS`, and
`MAKGORA_<QUEUE|PREP|LOCK|ACTION|CHAT>_RATE_BURST` to override a category.
Throttle warnings are limited to one per SID every 2 seconds, configurable with
`MAKGORA_THROTTLE_WARNING_COOLDOWN_SECONDS`.

At most 1,000 limiter records are retained; `MAKGORA_MAX_LIMITER_SIDS`
overrides the cap. A new SID at capacity evicts the least-recently-seen record
with a deterministic SID tie-breaker, and disconnect removes its record
immediately. A client that rotates through many new SIDs can reset its
individual SID rate, but queue and room caps still bound retained matchmaking
state. The limits are per SID, not per IP, so two tabs on one computer remain
independent. Proxy-level and identity-based controls remain external.

Lifecycle state remains process-local. Multiple application workers are not
supported.

## Development Documentation

- [Roadmap](ROADMAP.md) — current development phase and class progress
- [Agent and Architecture Rules](AGENTS.md) — mandatory engine and contribution contracts
- [New Class Specification](CLASS_IMPLEMENTATION.md) — reusable, class-neutral design template and completion checklist (copy it per class; never fill in the template itself)

### Validating Changes

There is no hosted CI; contributors run the validation suites locally. Before merging engine or class changes, run the full validation command:

```bash
python tests/run_all_tests.py
```

It runs each of the five standard suites once, in its own subprocess, keeps going after a failure, prints a final per-suite summary, and exits `0` only when all five pass. The individual runners remain available for targeted development:

```bash
python tests/run_regression.py
python tests/run_architecture_guardrails.py
python tests/run_source_kind_validation.py
python tests/run_effect_tags_validation.py
python tests/run_subschool_validation.py
```

## Have Fun

Grab a friend, jump into the queue, and see who's the better duelist. May the RNG gods be kind.
