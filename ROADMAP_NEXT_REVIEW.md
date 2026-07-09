# Mak'Gora — Next Maintainability Review

Review/planning document only. No engine or test code was changed for this review.

Scope: assess the codebase after the architecture-cleanup roadmap (AGENTS.md rules,
static guardrails, effect tags/helper API/resolution layers, central
`grant_player_resource`, actual-dealt resource correctness, damage source-kind taxonomy,
regression-suite split) and recommend the next round of small, low-risk maintainability
PRs. Mak'Gora keeps the duel format; future PvE stays duel-shaped (one main combatant per
side plus attached pets/adds). No ECS, no many-vs-many battlefield, no broad rewrites.

Line numbers below were captured at the time of the original review (`main` @
`91c4681`). This branch has since merged `main` @ `10c5791`, which includes the
damage-event factory PR (#23), so `damage_events.py` and the factory wiring are
present in this tree; resolver/effects line references may be off by a few
lines after that merge — re-verify before implementation.

**Revision note:** updated after PR #23 landed the damage-event factories
(`damage_events.py`). That item is now marked completed and the recommended PR
order has been re-sequenced accordingly.

---

## 1. Current architecture assessment

### What landed, and how well

| Roadmap item | Status | Evidence |
| --- | --- | --- |
| AGENTS.md rules | ✅ Strong | `AGENTS.md` (377 lines) covers resource/damage pipelines, snapshot-vs-live, tags, lifecycle, testing. Rules are specific and checkable. |
| Static architecture guardrails | ✅ Strong | `tests/architecture_guardrail_suite.py` (5 guardrails: direct resource writes, post-damage resource gains, speculative raw-proc double counting, actual-gained logging, factory-built queued damage events). Narrow allowlists with reasons. |
| Effect tags / helper API / resolution layers | ✅ Good | `effects.py:1539–1623` (`effect_tags`, `effect_has_tag`, `target_has_effect_tag`, `target_effects_in_resolution_layer`, …). Resolver stage functions carry `# Resolution layer:` annotations. |
| Central `grant_player_resource` pipeline | ✅ Good | `effects.py:2504`; guardrail 1 enforces it for mp/energy/rage. |
| Actual-dealt damage resource correctness | ✅ Good | Guardrails 2–4 plus regression scenarios (`scenario_queued_proc_resource_gain_uses_actual_dealt`, etc.). Queued raw events deliberately excluded from speculative `bonus_damage` (`effects.py:2087–2093`). |
| Damage source-kind taxonomy | ✅ Good | `damage_types.py` is the single authority; metadata-only contract documented; validated by `tests/source_kind_validation_suite.py`. |
| Split regression suite | ✅ Good | `tests/regression/` with 8 domain modules + `registry.py` identity-validated scenario registry preserving run order. |
| Damage event factory helpers | ✅ Landed (PR #23) | `damage_events.py` provides `make_passive_damage_event()` and `make_queued_damage_event()` as the single builders for the two event shapes. All 4 producer sites in `effects.trigger_on_hit_passives` and the resolver's `queue_passive_damage_events` now go through the factories; the module docstring documents the normalization contract (non-negative coercion, source-kind defaulting, optional keys omitted rather than `None`), and static guardrail 5 now flags ad-hoc `"type": "damage_event"` dict literals in gameplay producers. |

### Overall shape

The engine is in materially better shape than before the cleanup: the dangerous
correctness classes (speculative damage → resources, direct resource mutation, mislogged
gains) are now guarded by both regression scenarios and static guardrails, and the
metadata taxonomies (tags, layers, schools, source kinds) are centralized.

The remaining debt is almost entirely **structural**, concentrated in one place:
`resolver.py:resolve_turn` (`resolver.py:1653–4258`) is a ~2,600-line function containing
roughly 60 nested closures. Stage extraction has started (module-level
`resolve_*_stage` functions), but because core helpers (`apply_damage`, `consume_costs`,
`grant_resource`, `set_cooldown`, …) are closures over turn state, the extracted stages
take very large injected-callable lists — `resolve_end_of_turn_stage`
(`resolver.py:1179`) takes 11 callables. Nothing inside `resolve_turn` can be read or
unit-tested in isolation.

---

## 2. Top 5 pain points

### P1 — The `resolve_action` closure (~870 lines) is the hardest read in the engine

`resolver.py:2337–3206`. One closure interleaves: action-selection modifiers → runtime
CC denial → cost payment → special-ability handlers → the per-hit damage loop (dice,
accuracy stage, empowered-variant scaling overrides, Death execute doubling, Flame Dance,
crit/empower logging) → on-hit passives + strike-again + queued damage events → AoE
branching (`aoe_incoming_damage` vs `total_damage` bookkeeping) → a 17-key result dict
(`resolver.py:3183`). Sixteen early-return sites each hand-build partial result dicts.
Related hard spots for any reader:

- The **parallel immediate-resolution path** — `build_immediate_resolution`
  (`resolver.py:3206`), `resolve_immediate_effects` (`resolver.py:3373`),
  `immediate_action_can_stun` — re-implements denial/protection/cost logic that
  `resolve_action` also has, with subtle differences (e.g. `include_runtime_cant_act=False`).
- `apply_damage`'s **four-way mitigation branch matrix** (`resolver.py:3573–3707`):
  `resolve_player_mitigation` × `resolve_non_player_mitigation` × player/pet target,
  each branch repeating the instance-list normalization.
- The **deferred re-resolution dance** at the tail (`resolver.py:4104–4110`): a result
  flagged `deferred` causes `resolve_action` to be called a second time after damage
  application.

### P2 — Closure-capture architecture blocks testing and extraction

Because `apply_damage`, `grant_resource`, `consume_costs`, etc. capture `turn_ctx`,
`match`, and the deferred-log lists, every further extraction inherits a long parameter
list (see `resolve_end_of_turn_stage`, `run_pet_phase` in `pet_ai.py:670` which takes 10
arguments). This is the root cause that makes P1 expensive to fix wholesale — which is
why the recommendations below chip at it from the edges instead of proposing a rewrite.

### P3 — Core packet dicts are untyped, convention-only contracts

Three dict shapes carry most of the engine's data with no declared schema:

- **Action result** (17 keys, `resolver.py:3183`), plus 16 partial-shape early returns.
- **Queued `damage_event`** — construction is now centralized in
  `damage_events.py` (PR #23), which fixes shape drift at the producer end, but the
  contract is still a plain dict documented only in that module's docstring: presence
  of `raw_incoming` silently means "re-mitigate at apply time", and the consumer
  (`append_extra_logs`, `resolver.py:4027`) still reads keys by convention.
- **`apply_damage` result** (10 keys, built at three sites inside `apply_damage`, plus
  `_empty_damage_result`).

Agents editing one end of these contracts cannot see the other end's expectations.

### P4 — Healing has no central pipeline (the resource-gain problem, unfixed for HP)

The exact class of bug `grant_player_resource()` eliminated for mp/energy/rage still
exists for HP. There are 14+ inline `res.hp = min(res.hp + x, hp_max)` sites:
`resolver.py:324, 351, 901, 924, 941, 1049, 1230, 1323, 2694`, `effects.py:2252, 2621,
2673`, `pet_ai.py:647` — while `_apply_heal_with_clamp` (`resolver.py:137`) exists but is
used only twice. Each site independently re-implements (or forgets) actual-gained
computation, `combat_totals["healing"]` crediting, and Mindgames-flip awareness
(`_apply_mindgames_aware_healing` exists at `resolver.py:145` but most sites bypass it).

### P5 — Per-ability branches and copy-paste in the resolver

- **Empowered-variant hacks**: scaling overrides for `final_verdict`, `crusader_strike`,
  `judgment`, `mind_blast`, `lava_lash`, `arcane_shot` are hardcoded at
  `resolver.py:2805–2832`, with their consume logic repeated at `resolver.py:3154–3160` —
  the "per-ability resolver branch" pattern AGENTS.md prohibits.
- **Literal `result1`/`result2` duplication** in the tail (`resolver.py:~4070–4110`):
  format-log / mindgames-suffix / redirect-log / `append_extra_logs` /
  `apply_direct_damage_dot` block is copy-pasted per actor, and the Mindgames
  "flips damage into healing" suffix string is re-built at 4+ sites.

### Tests that remain hard for agents to review (focus area 4)

The domain split and registry were a clear win, but within domains:

- `tests/regression/test_classes_abilities.py` (1,789 lines, 44 scenarios) contains
  multi-phase mega-scenarios — `scenario_hunter_rework_phase1_phase2_regression` (120
  lines), `scenario_shaman_same_turn_on_hit_rider_commitment_fairness` (112),
  `scenario_warrior_onslaught_stackable_contract` (105) — that mix many independent
  assertions, so a reviewer can't tell which behavior a failure indicts.
- Several scenarios are named after **historical refactor phases**, not behavior:
  `scenario_phase_c_pass1_early_resolution_stages_are_preserved`,
  `scenario_phase_c_prompt1_middle_resolution_stages_are_preserved`,
  `scenario_phase_d_end_of_turn_stage_preserved` (in `test_damage_pipeline.py`). A future
  agent has no way to know what "phase C prompt 1" was.
- Expected mitigation numbers are frequently inlined as magic constants even though
  `_expected_mitigated` exists in `tests/regression/helpers.py:73`.

---

## 3. Recommended next 5 PRs (each small, low-risk, behavior-preserving)

The previously recommended PR 1 (damage-event factory) landed as PR #23
(`damage_events.py`) and is no longer in this list. The remaining items are
re-sequenced below.

### PR 1 — Declare the packet contracts: TypedDicts + AGENTS.md documentation

Add `TypedDict` definitions (annotations only, `total=False` where appropriate; zero
runtime change) for the three shapes in P3: `ActionResult`, `QueuedDamageEvent`,
`DamageApplicationResult`. Place them in a small `contracts.py` (or at the top of
`resolver.py`/`damage_types.py`) and annotate `resolve_action`, `apply_damage`,
`append_extra_logs`, and the factories in `damage_events.py` — whose docstring already
spells out the two event schemas and the `raw_incoming` semantics, making the
`QueuedDamageEvent`/producer-event TypedDicts nearly a transcription. Document the key
sets in AGENTS.md so future agents edit both ends of a contract consistently. Doing
this first locks in the contracts the later PRs touch.

### PR 2 — De-duplicate the `resolve_turn` tail

Extract a per-actor `finalize_actor_result(actor_sid, target_sid, result, dealt_data)`
inner function covering the copy-pasted `result1`/`result2` block
(`resolver.py:~4070–4110`: format damage log → Mindgames suffix → redirect log →
`append_extra_logs` → `apply_direct_damage_dot`), plus a shared
`mindgames_flip_suffix(dealt_data)` helper for the 4+ re-built suffix strings. Pure
mechanical extraction inside the existing closure scope; log output byte-identical.

### PR 3 — Data-driven empowered-variant overrides

Move the six hardcoded empowered-scaling branches into ability data: an `empowered_by`
key on the relevant `abilities.py` entries, e.g.
`{"effect_id": "avenging_wrath", "scaling": {"atk": 1.4}, "consume": false}` /
`{"effect_id": "mind_blast_empowered", ..., "consume": true}`. One generic lookup in
`resolve_action` replaces `resolver.py:2805–2832`, and one generic consume step replaces
`resolver.py:3154–3160`. Numbers and logs unchanged; existing per-ability regression
scenarios in `test_classes_abilities.py` pin the behavior. This directly enforces the
AGENTS.md "prefer data over resolver branches" rule.

### PR 4 — Central heal helper `grant_player_healing()` + guardrail 6 (only after more review)

Mirror `grant_player_resource` in `effects.py`: clamp to `hp_max`, return the actual
gained amount, with explicit opt-in flags for Mindgames-flip awareness. Convert the
inline sites listed in P4, and add static guardrail 6 (PR #23 took slot 5 for
factory-built damage events) to
`tests/architecture_guardrail_suite.py` flagging `.res.hp = min(` outside the helper +
allowlist, in the style of guardrail 1. **Deliberately sequenced last of the code PRs
and gated on a further review pass**: before implementation, audit each of the 14+
sites for which ones intentionally bypass `_apply_mindgames_aware_healing`, which ones
credit `combat_totals["healing"]` and which don't, and which are pet/owner heals with
different clamp targets. The conversion must be per-site mechanical with those
differences preserved explicitly — no balance change, identical math at every site.

### PR 5 — Test hygiene pass and/or PvE-readiness AGENTS.md addendum

Two docs/tests-only candidates; either (or both, as separate small PRs) closes out this
review:

- **Test hygiene** (tests-only): rename the phase-named scenarios
  (`scenario_phase_c_pass1_…`, etc.) to behavior-based names (registry entries update
  mechanically), and split the three 100+-line mega-scenarios at their existing
  internal phase comments into separately registered scenarios. Zero engine risk;
  large agent-reviewability payoff.
- **PvE-readiness AGENTS.md addendum** (docs-only): add the "PvE shape" section drafted
  in §4 below to AGENTS.md, pinning the duel-shaped PvE invariants (one main per side +
  attached pets/adds, no ECS, no battlefield generalization) before any PvE work starts.

### Worth doing, but after the above (not in the next five)

- **Incremental `resolve_action` stage extraction**: continue the existing
  `resolve_*_stage` pattern one stage per PR (per-hit damage roll stage first), only
  after PRs 1–5 shrink the surface. Explicitly **not** a wholesale breakup.

---

## 4. PvE-readiness notes (under the duel-shaped model)

The current architecture is already close to the target PvE shape, because "one main
combatant + attached entities per side" is exactly what the duel engine models today:

- `MatchState.players` is a two-sided pairing; `PlayerState.pets: Dict[str, PetState]`
  is the attached-entities mechanism (adds/pets), with summon caps, redirect pets, and
  per-pet mitigation already handled.
- AoE already resolves "champion + attached pets" per side via
  `build_aoe_enemy_target_list` (`resolver.py:3707`) — precisely the boss+adds shape.
- `PlayerState.entity_type` / `PetState.entity_type` and `entity_type_of()`
  (`resolver.py:41`) already provide the discriminator a boss needs; no class hierarchy
  required.
- The 46 `sids[0]/sids[1]` references in `resolver.py` correctly encode "exactly one
  main per side". **Keep them.** They are a feature of the duel shape, not debt.

Smallest enablers to note for the future (not proposed as immediate PRs):

1. **Scripted action provider for side B.** PvE's main enemy should submit actions
   through the existing `submit_action`/`ready_to_resolve`/`resolve_turn` pipeline —
   an AI counterpart to `pet_ai.py` that fills `match.submitted[boss_sid]` before
   resolution. No second resolution path, no resolver changes.
2. **Centralize entity display labels.** `effects.py` log templates slice
   `attacker.sid[:5]` directly at 6 sites, and `resolver.py` has `sid_token()` plus an
   `entity_log_label` closure. A single shared label helper (sid → display name) is the
   one small refactor that lets a boss log as "Ragnaros" instead of a sid prefix. Cheap
   now, annoying later.
3. **Boss-as-PlayerState.** A PvE main enemy can be a `PlayerState` with a non-humanoid
   `entity_type`, no `build`/items, and data-driven abilities — the entire
   damage/effect/pet pipeline then works unchanged. Trash fights are the same shape with
   different templates.

What PvE does **not** need: N-sided matches, a target-selection framework, an entity
component system, per-entity action economies beyond the existing pet phase, or changes
to the winner check (side's main entity dead → other side wins, same as today).

### Proposed AGENTS.md addendum (focus area 6)

Add a short **"PvE shape"** section to AGENTS.md, roughly:

> ## PvE shape
>
> Future PvE keeps the duel-shaped engine. A PvE encounter is side A (player champion +
> attached pets) versus side B (one main boss/trash enemy + attached adds/pets).
>
> * The match stays two-sided. Do not generalize `MatchState.players` beyond two mains.
> * Adds/pets attach to a side via the existing `PlayerState.pets` mechanism. Do not
>   introduce a separate add/minion system.
> * The PvE main enemy is a champion-like combatant (a `PlayerState` with an appropriate
>   `entity_type`) whose actions are chosen by scripted AI and submitted through the
>   normal action-submission pipeline. Do not add a second resolution path.
> * Do not introduce an entity-component system, battlefield/grid positioning, or
>   many-vs-many targeting. AoE remains "enemy champion + their attached entities".
> * `sids[0]`/`sids[1]` pairing, the winner check, and simultaneous-turn resolution are
>   duel-shape invariants — preserve them in PvE work.

---

## 5. Anti-goals — cleanup ideas explicitly rejected as over-engineering

1. **Generic ECS / entity framework.** The three entity kinds (champion, pet/add,
   future boss = champion-like) are fully served by the two dataclasses + `entity_type`.
2. **Many-vs-many battlefield rewrite** (free targeting, positioning, N-sided matches).
   Contradicts the project direction; nothing in PvE-as-specified needs it.
3. **Wholesale `resolve_turn`/`resolve_action` rewrite** or a pipeline framework with
   dynamic stage registration/priority ordering. Extraction should stay incremental,
   straight-line, and behavior-pinned by the phase-preservation regression scenarios.
4. **Effect-priority runtime rewrite.** No concrete resolution-order bug was found in
   this review; `display.priority` is already documented as UI-only. Leave it alone.
5. **Class hierarchies for abilities/effects/items** (Ability/Effect base classes,
   subclass-per-passive). The data-driven dict approach is the codebase's strength;
   PR 5's TypedDicts give the missing shape documentation without runtime churn.
6. **Generic `has_tag(obj, tag)` polymorphism.** Already prohibited by AGENTS.md; the
   explicit per-domain helpers are correct.
7. **Wholesale pytest migration.** The bespoke runner + identity-validated registry
   works, is deterministic, and preserves run order; migration is churn without a
   correctness payoff. (Individual future suites may still use whatever the task asks.)
8. **Event-bus / observer system for procs and reactions.** The current explicit
   call-order in the damage pipeline is a feature: AGENTS.md's canonical damage order is
   auditable precisely because control flow is visible.

---

## 6. Exact files/functions to inspect before implementing each PR

**PR 1 (TypedDict contracts):**
- `damage_events.py` — `make_passive_damage_event` / `make_queued_damage_event` and the module docstring (already documents both event schemas and the `raw_incoming` semantics; the TypedDicts should transcribe it, not re-derive it)
- `resolver.py:3183–3206` — full action-result shape; the 16 partial early returns inside `resolve_action` (grep `return {` between 2337–3206)
- `resolver.py:3573–3707` — `apply_damage` result shape; `resolver.py:107` — `_empty_damage_result`
- `resolver.py:4027–4068` — `append_extra_logs` (consumer-side key reads to cover)
- `AGENTS.md` — where to document the contracts (new subsection under "Damage pipeline rules")

**PR 2 (tail de-duplication):**
- `resolver.py:~4070–4130` — the twin `result1`/`result2` blocks and `deferred` re-resolution
- `resolver.py:3867–3880` — `absorb_suffix` / `format_damage_log` (called from the extracted helper)
- `tests/regression/test_damage_pipeline.py` — `scenario_winner_summary_logs_after_pet_phase_and_end_of_turn_resolution` and the phase-preservation scenarios (log-order pins)

**PR 3 (data-driven empowered overrides):**
- `resolver.py:2805–2832` — scaling-override branches; `resolver.py:3139–3160` — consume branches (note `lightning_bolt` at 3139 is a resource gain, not an empower — leave it)
- `abilities.py` — entries for `final_verdict`, `crusader_strike`, `judgment`, `mind_blast`, `lava_lash`, `arcane_shot`
- `effects.py` — templates `paladin_final_verdict_empowered`, `avenging_wrath`, `mind_blast_empowered`, `lava_surge`, `arcane_surge`
- `tests/regression/test_classes_abilities.py` — the empowered-ability scenarios that pin exact damage numbers/logs

**PR 4 (central heal helper + guardrail 6 — pre-implementation review pass required):**
- All inline clamp sites: `resolver.py:324, 351, 901, 924, 941, 1049, 1230, 1323, 2694`; `effects.py:2252, 2621, 2673`; `pet_ai.py:647` — audit each for Mindgames opt-out, `combat_totals["healing"]` crediting, and pet-vs-owner clamp target before converting anything
- `resolver.py:137–162` — `_apply_heal_with_clamp`, `_apply_mindgames_aware_healing` (decide which sites route through which; document per-site Mindgames opt-outs)
- `effects.py:2504` — `grant_player_resource` (the pattern to mirror: clamp, return actual, logging contract)
- `tests/architecture_guardrail_suite.py` — guardrail 1 structure and allowlist format to copy
- `tests/regression/test_dots_hots.py`, `test_resources.py` — healing-log scenarios that pin exact log strings

**PR 5 (test hygiene and/or AGENTS.md PvE addendum):**
- `tests/regression/test_damage_pipeline.py` — the `scenario_phase_*` names; `tests/regression/registry.py` — mechanical rename impact
- `tests/regression/test_classes_abilities.py` — the three 100+-line scenarios and their internal phase comments (natural split points)
- `tests/regression/helpers.py:73` — `_expected_mitigated` (adopt in place of inlined magic mitigation numbers)
- `AGENTS.md` + §4 of this document — the drafted "PvE shape" section to add verbatim (adjust only if the duel-shape invariants have changed by then)
