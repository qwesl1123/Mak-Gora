# Mak'Gora Roadmap

## Purpose

This document tracks Mak'Gora's active development priorities and completion status. It is the authoritative planning surface for what is being worked on now and what remains.

It is deliberately narrow in scope:

- Architecture and implementation rules live in [`AGENTS.md`](AGENTS.md).
- Detailed per-class design uses a class-specific copy based on [`CLASS_IMPLEMENTATION.md`](CLASS_IMPLEMENTATION.md) (the canonical template stays blank and reusable).

This file records priorities and progress, not design details or historical audits.

## Current playable roster

Nine classes are playable today:

- Warrior
- Mage
- Rogue
- Warlock
- Druid
- Paladin
- Priest
- Hunter
- Shaman

## Completed engine foundations

The combat architecture these classes run on is stabilized:

- [x] Deterministic, seeded turn resolution
- [x] Data-driven abilities and effects
- [x] Canonical resource-gain pipeline (`grant_player_resource()`)
- [x] Canonical player-healing application (`apply_player_healing()`) with an HP-write guardrail
- [x] Shared damage pipeline
- [x] Schools, subschools, and source-kind metadata
- [x] Combat totals that represent actual HP damage
- [x] Player healing, pet healing, and overhealing accounting
- [x] Viewer-relative post-combat summary
- [x] DPT and completed-turn calculation
- [x] Pet/totem, redirect, and AoE support
- [x] Split deterministic regression suite plus static validators

## Active phase: remaining classes

The current priority is completing the remaining playable classes on top of the stabilized combat architecture, before expanding into larger PvE work.

Four classes remain. No implementation order has been chosen; priority is assigned only with explicit user approval.

| Class | Priority | Design | Resource Foundation | Implementation | UI/Docs | Regression Coverage | Status |
|---|---|---|---|---|---|---|---|
| Death Knight | TBD | Not started | Not assessed | Not started | Not started | Not started | Planned |
| Monk | TBD | Not started | Not assessed | Not started | Not started | Not started | Planned |
| Demon Hunter | TBD | Not started | Not assessed | Not started | Not started | Not started | Planned |
| Evoker | TBD | Not started | Not assessed | Not started | Not started | Not started | Planned |

Allowed `Status` values:

- **Planned** — not yet started.
- **Designing** — a class-specific design based on `CLASS_IMPLEMENTATION.md` is being completed.
- **Foundation** — a required reusable resource/engine foundation is in progress.
- **In Progress** — class implementation underway.
- **Blocked** — waiting on a decision or an unmerged dependency.
- **Complete** — fully implemented, selectable, documented, and tested.

## Class delivery rules

- One class at a time.
- No partially selectable class: a class becomes selectable only when every required surface is complete.
- When a class needs an unsupported shared resource or engine mechanic, the reusable foundation PR lands first.
- The complete class PR lands second, on top of the merged foundation.
- Balance follow-up is separated into its own PR when practical.
- Every class's specification must satisfy the requirements defined by the [`CLASS_IMPLEMENTATION.md`](CLASS_IMPLEMENTATION.md) template.
- Every merged class must update the table above.

## Deferred work

Larger PvE mode development (Dungeons, Raids, and beyond) remains deferred while class expansion is the active priority.

## Roadmap maintenance

Update this roadmap:

- when a class enters design;
- when a foundation PR starts or lands;
- when implementation begins;
- when a class becomes complete;
- when priorities are explicitly changed by the user.
