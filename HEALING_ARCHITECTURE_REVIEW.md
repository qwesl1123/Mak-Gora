# Mak'Gora — Healing Architecture Review

Review/planning document only. No engine or test code was changed for this review.

Scope: decide whether a central healing helper is worthwhile, what it should own, and
what it must not own. This is the "further review pass" that `ROADMAP_NEXT_REVIEW.md`
gated its PR 4 on. Line numbers were captured on this branch at `main` @ `53ba30f`
(post PR #26); baseline verified green: 222 regression scenarios, 5/5 architecture
guardrails.

Mak'Gora keeps the duel format; future PvE stays duel-shaped (one main combatant per
side plus attached pets/adds). No ECS, no entity-hierarchy rewrite, no battlefield
generalization, no resolver rewrite is proposed here.

---

## 1. Current healing architecture

### 1.1 Where healing enters the engine (review question 1)

Healing enters through five phases of turn resolution. HP is only ever modified at the
application sites listed in §1.2 — there is no other write path in gameplay code.

```
submit actions
  │
  ├─ resolve_action (resolver.py:2438)
  │    ├─ special ability handlers (resolver.py:1186 dispatch table)
  │    │    Healthstone / Holy Light / Flash Heal / Lay on Hands / Wild Growth
  │    │    → inline Mindgames twist check → inline clamp write      [direct heals]
  │    │    Kill Command → pet_heal data → inline clamp on pet.hp    [pet heal]
  │    │    Frenzied Regen / Regrowth / Healing Stream → apply HoT effect (no HP write yet)
  │    ├─ penance_self branch (resolver.py:2784) → per-hit twist check → inline clamp
  │    ├─ generic heal_scaling/heal_dice/heal_on_hit after a landed hit (resolver.py:3089)
  │    │    → _apply_mindgames_aware_healing (the ONLY caller of the helper)
  │    └─ trigger_on_hit_passives (effects.py) → item heal_on_hit (Thunderfury)
  │         → inline clamp, returned as bonus_healing (effects.py:2253)
  │
  ├─ resolve_turn tail: totals credit result["healing"] once (resolver.py:3599)
  │
  ├─ post-damage reactions stage (resolver.py:408, called at resolver.py:4233)
  │    dealt = total ACTUAL HP dealt (primary + queued procs + AoE pets)
  │    ├─ heal_from_dealt_damage → inline clamp (resolver.py:425)     [full lifesteal]
  │    └─ heal_from_damage fraction → inline clamp (resolver.py:452)  [Drain Life]
  │
  ├─ end-of-turn stage (resolver.py:1280)
  │    ├─ pet phase: Lightning Breath lifesteal → inline clamp on pet.hp AND
  │    │    owner.res.hp (pet_ai.py:645–647)
  │    ├─ effects.end_of_turn (effects.py:2706), skipped entirely while cycloned
  │    │    ├─ item heal_self passives → inline clamp (effects.py:2622)
  │    │    └─ HoT regen ticks → Mindgames? queue self-damage : inline clamp
  │    │         (effects.py:2653–2675); healing_done credited at resolver.py:1350
  │    ├─ DoT lifesteal_pct (Vampiric Touch / Devouring Plague) → inline clamp
  │    │    (resolver.py:1331) — may revive from negative HP (pinned behavior)
  │    ├─ end_of_turn_pet HoT regen → inline clamp on pet.hp (effects.py:2752)
  │    ├─ Shield of Vengeance explosion (resolver.py:3909) — no healing of its own;
  │    │    only heals via the Mindgames flip below
  │    └─ Ancestral Knowledge shaman passive → inline clamp, requires hp > 0
  │         (resolver.py:1424)
  │
  └─ apply_damage (resolver.py:3661)
       mindgames_flip_damage=True (attacker is mindgamed) →
       _apply_heal_with_clamp(target, incoming) (resolver.py:3751)
       [enemy damage → target healing; reported as result "mindgames_healing"]
```

Two helpers exist but are underused:

* `_apply_heal_with_clamp(target, amount) -> actual` (resolver.py:203) — 2 consumers:
  `_apply_mindgames_aware_healing` and the Mindgames flip inside `apply_damage`.
* `_apply_mindgames_aware_healing(target, amount, apply_self_inflicted_magical_damage)
  -> (actual, twisted)` (resolver.py:211) — 1 consumer: the generic on-hit heal path
  (resolver.py:3109, e.g. Victory Rush).

### 1.2 Complete HP-restoring site inventory

Every gameplay write that increases HP (tests excluded). "Log amount" says whether the
combat log prints the actual clamped gain or the pre-clamp base value.

| # | Site | Mechanic | Mindgames-aware | Totals credit | Log amount |
|---|------|----------|-----------------|---------------|------------|
| 1 | resolver.py:425 | `heal_from_dealt_damage` full lifesteal (fury_of_azzinoth) | no | direct | actual |
| 2 | resolver.py:452 | `heal_from_damage` fractional lifesteal (drain_life 1.0) | no | direct | actual |
| 3 | resolver.py:984 | Kill Command `pet_heal` (clamps `pet.hp_max`) | no | direct (owner) | actual |
| 4 | resolver.py:1002 | Healthstone | inline twist | via result | actual |
| 5 | resolver.py:1025 | Holy Light | inline twist | via result | actual |
| 6 | resolver.py:1042 | Flash Heal (+Clarity of Mind ×1.4) | inline twist | via result | actual |
| 7 | resolver.py:1057 | Lay on Hands (`= hp_max`, not a min-clamp) | inline twist (missing_hp) | via result | n/a (full) |
| 8 | resolver.py:1150 | Wild Growth (+`cycloned` gate after twist) | inline twist | via result | **base** |
| 9 | resolver.py:1331 | DoT `lifesteal_pct` tick (Vampiric Touch 0.4 / Devouring Plague 1.0) | no | direct | actual |
| 10 | resolver.py:1424 | Ancestral Knowledge end-of-turn passive (requires hp > 0) | no | direct | actual |
| 11 | resolver.py:2795 | `penance_self` per-hit heal (+Clarity) | inline twist (per hit) | via result | actual |
| 12 | resolver.py:3109 | generic `heal_scaling`/`heal_dice` on-hit heal (victory_rush) | via helper | via result | **base** |
| 13 | resolver.py:3751 | Mindgames flip: enemy damage → target heal | n/a (is Mindgames) | **not credited** | pre-clamp (suffix) |
| 14 | effects.py:2253 | item `heal_on_hit` proc (Thunderfury) | **no** | via result (`bonus_healing`) | **base** |
| 15 | effects.py:2622 | item `heal_self` end-of-turn (Spirit Light Sword, Staff of Immortality) | **no** | via `healing_done` | **base** |
| 16 | effects.py:2674 | HoT regen tick (frenzied_regeneration, regrowth, healing_stream, vanish, iceblock) | queued self-damage | via `healing_done` | **base** |
| 17 | effects.py:2752 | pet HoT regen tick | n/a | **not credited** | actual |
| 18 | pet_ai.py:645 | Lightning Breath pet self-heal | n/a | direct (owner, combined) | actual |
| 19 | pet_ai.py:647 | Lightning Breath owner heal | **no** | direct (owner, combined) | actual |

Related but not healing: HP sacrifice (resolver.py:2039) spends HP; absorbs
(`add_absorb`/`consume_absorbs`) prevent damage rather than restore HP.

### 1.3 Mindgames — the one healing "modifier" with real machinery

Mindgames (`effects.py:558`, 1-turn debuff) flips both directions:

* **Healing received by a mindgamed player → self shadow damage.** Two backends:
  * `apply_self_inflicted_magical_damage` (resolver.py:1945, closure): manual checks
    (immune-all, cycloned, Cloak of Shadows), consumes absorbs, subtracts HP directly,
    breaks stealth, grants bear-form rage. **Not** counted in `combat_totals`.
  * Twisted HoT ticks (effects.py:2656): queued as `self_damage_sources`, resolved
    through `resolve_dot_tick` → the full `apply_damage` pipeline, and **is** counted
    in the player's own `combat_totals["damage"]` (resolver.py:1346).
* **Damage dealt by a mindgamed attacker → heals the target.** Set at three producers
  (`resolve_action` at resolver.py:3219, DoT ticks at resolver.py:3964, Shield of
  Vengeance explosion at resolver.py:3938), applied at one consumer inside
  `apply_damage` (resolver.py:3750–3763), logged via the shared
  `mindgames_flip_suffix` (resolver.py:3644) plus one inline rebuild in the AoE
  champion path (resolver.py:3863).

Shield of Vengeance itself is an absorb/explosion mechanic; its only healing
interaction is the flip above, and the interaction matrix is pinned by
`scenario_mindgames_shield_of_vengeance_explosion_interactions`.

### 1.4 Healing modifiers, reduction, overheal

* **Increase:** Clarity of Mind only (resolver.py:225–242), ability-scoped
  (flash_heal/penance/penance_self), applied at value computation, not application.
* **Prevention:** cyclone (skips `end_of_turn` wholesale, plus Wild Growth's inline
  gate), immune-all (suppresses HoT ticks except the immunity effect's own regen,
  effects.py:2645), Mindgames (flips).
* **Reduction:** none exists. There is no Mortal-Strike-style "healing taken −X%"
  mechanic anywhere in the codebase.
* **Overheal:** silently discarded by clamps everywhere; never tracked, never logged
  as a stat. The only trace is the base-vs-actual log divergence in §3.

---

## 2. Strengths

1. **Application is uniformly correct.** All 19 sites clamp properly (or set to full);
   none over-heals past `hp_max`. There is no known live healing bug.
2. **Lifesteal correctness already landed.** Damage-based healing reads the
   post-redirect/absorb/mitigation/Mindgames actual dealt total
   (`total_dealt_by_actor` → resolver.py:4236), pinned by the Dragonwrath,
   Fury of Azzinoth, and Thunderfury scenarios.
3. **The hard problem (Mindgames) is mostly funneled.** The damage→healing direction
   has exactly one applying consumer inside `apply_damage`; the flip suffix is a
   shared helper. The twist direction has a reusable helper
   (`_apply_mindgames_aware_healing`) even if only one site uses it.
4. **HoTs are data-driven.** A HoT is just an effect with `regen: {hp: N}` and the
   `hot` tag; ticking lives in one function (effects.py:2637). Adding a HoT requires
   zero resolver changes.
5. **Totals reach the UI through few seams.** `combat_totals["healing"]` feeds the
   post-combat summary and War Council panel (sockets.py:311, 377); the action-result
   path credits once, centrally (resolver.py:3599).
6. **Behavior is well pinned.** Negative-HP revival before winner check, Mindgames
   twist wording and zero-healing totals, Healing Stream ticks, SoV flip matrix — the
   regression suite would catch most conversion mistakes.

---

## 3. Weaknesses

### 3.1 Duplication (review question 2)

* **The clamp write is copy-pasted 16 times** (§1.2 minus the helper-routed sites and
  Lay on Hands' full-set) across three files, with two shape variants
  (`res.hp`/`res.hp_max` vs pet `hp`/`hp_max`). This is the same debt class
  `grant_player_resource()` retired for mp/energy/rage — AGENTS.md's own guardrail
  list still names `.res.hp = min(` as a suspicious pattern with no enforcement.
* **The Mindgames twist block is copy-pasted 8 times** with drifting wording:
  "Mindgames twists healing into X self-damage." (×6), "Hit i: Mindgames **turns**
  healing into X self-damage." (resolver.py:2792), and "is twisted by Mindgames and
  takes X self-damage instead of healing from Y." (effects.py:2670).
* **Totals crediting is spelled out at 8 sites** across three distinct flows
  (direct, action-result, `healing_done`).

### 3.2 Divergences (some deliberate, none documented)

* **Log base-vs-actual:** 5 sites log the pre-clamp base value (Wild Growth, generic
  on-hit heal, item heal_on_hit, item heal_self, HoT regen); the rest log the actual
  gain. At full HP a HoT logs "recovers 15 HP" while crediting 0. This is exactly the
  "old Aimed Shot bug" class that resource guardrail 4 eliminated for resources.
* **Mindgames coverage is implicit policy:** ability on-hit healing (Victory Rush) is
  twisted; item on-hit healing (Thunderfury, same trigger, same phase) is not. Item
  end-of-turn heal_self is not twisted; HoT regen in the same end-of-turn pass is.
  Lifesteal (all four forms) and pet-sourced owner healing are never twisted. Some of
  these are defensible ("drains" aren't "heals"); none are recorded anywhere as
  decisions, and no test pins the item-heal-under-Mindgames behavior.
* **Two Mindgames self-damage backends** (§1.3) differ in totals crediting and in how
  immunity is decided (manual check list vs the full pipeline). The manual list
  (`cloak_of_shadows` etc.) can silently go stale as protections evolve.
* **Liveness gates differ silently:** DoT lifesteal can revive from negative HP
  (pinned, load-bearing for winner-check timing); Ancestral Knowledge requires
  `hp > 0`; pet HoTs require the pet alive; item heal_self has no gate.
* **Crediting inconsistency:** Kill Command and Lightning Breath credit pet healing to
  the owner's totals; pet HoT regen credits nobody; Mindgames flip healing credits
  nobody. User-visible in the post-combat summary.

### 3.3 Where future bugs are likely

The engine currently stays correct by copy-paste discipline. The likely failure is a
new heal site (new class ability, new item, PvE boss kit) cloning the nearest block
and inheriting the wrong stance on one of the five divergence axes above — most
probably forgetting the Mindgames twist, crediting totals twice (once directly, once
via the result dict), or logging base instead of actual. Nothing structural prevents
any of these today.

### 3.4 Which duplication is worth removing, and which should stay

Worth removing: the 16 raw clamp writes (mechanical, zero-decision), and eventually
the totals-crediting boilerplate at the six "direct" sites (low priority).

Should STAY duplicated:

* The per-handler Mindgames twist branches. Each handler twists a different value
  (Lay on Hands twists `missing_hp`; penance twists per hit; Wild Growth interleaves
  a cyclone gate) and controls its own early return/cooldown flow. A shared
  twist-or-heal wrapper would have to own control flow and result-dict shape — that
  recreates the "partial early return" problem the packet contracts just documented.
* The three totals-crediting flows. Forcing one flow through a helper would
  double-count the action-result path (resolver.py:3599 already sums
  `result["healing"]`).
* Per-site log wording. Strings are pinned by tests and are player-facing flavor
  ("drains 4 life" vs "Holy Light restores 12 HP").
* The two Mindgames self-damage backends. The queued-HoT path exists because heals in
  `effects.py` cannot reach the resolver's `apply_damage` closure; the queue is the
  correct decoupling, not an accident. Unifying would mean threading resolver
  callables into `effects.py` — wrong direction.

---

## 4. Is a central helper recommended?

**Yes — but a deliberately small one, not a full equivalent of the resource pipeline.**

### 4.1 Should healing become `grant_player_resource()` for HP? (the important question)

No, not fully. The resource pipeline owns three things: a global gain-multiplier stack
(Challenger Wrath, Rage Crystal), the cap, and the actual-gained return. Healing
matches only the last two:

* **Healing has no global multiplier by design.** `effects.py:2676–2678` and
  2720–2723 explicitly keep HP out of `grant_player_resource` so Challenger's
  low-resource bonus can never scale healing. The only heal modifier (Clarity of
  Mind) is ability-scoped and applies at value computation. A helper that owns a
  modifier stack would be speculative machinery for a mechanic (generic +healing% /
  −healing%) that does not exist in this game.
* **Healing has an interception semantic resources lack.** Mindgames can replace the
  entire application with a damage event. Crucially, whether a source is twisted is
  per-source-kind policy (§3.2), not a global truth — so the decision must stay
  visible at call sites, never buried as a helper default.
* **Healing has two clamp targets** (player `res.hp`, pet `hp`). Resources are
  player-only by contract.

So: mirror the *shape* of the resource pipeline (single entry point, clamp, return
actual, guardrail, AGENTS.md contract) while owning strictly less.

### 4.2 Would it actually reduce bugs, or just move code? (review question 3)

Being critical: the clamp is two lines and no site currently gets it wrong. Converting
19 sites will not delete meaningful code — net LOC is roughly flat. The honest value
is not the function; it is the same thing that made `grant_player_resource` work:

1. **The guardrail becomes possible.** Today `.res.hp = min(` cannot be flagged
   because every legitimate site matches. After conversion, a static guardrail plus a
   narrow allowlist makes the *next* inline heal a test failure instead of a review
   hope. This is the actual bug-reduction mechanism — it acts on future code.
2. **Return-actual becomes the path of least resistance,** which is how the
   base-vs-actual log divergence stops growing (existing logs stay byte-identical
   until changed deliberately).
3. **The divergence axes become greppable.** "Which sites are Mindgames-aware"
   changes from archaeology to reading call sites of one function.

What the helper does NOT fix: the Mindgames policy inconsistencies, the crediting
gaps, and the wording drift are behavior decisions, not refactors. The helper only
makes them visible. If the team never adds another heal source, the helper is not
worth it; given the stated trajectory (new content, PvE boss/add kits), it is.

### 4.3 Would it make future PvE easier? (review question 6)

Marginally, and honestly not much. Healing is already side-symmetric, and PvE's
boss-as-`PlayerState` inherits every player heal path unchanged. The concrete gains
are small: boss/add kit heals get guardrail coverage for free, and `grant_pet_healing`
gives add/pet healing one blessed entry point instead of three crediting conventions.
No PvE feature is blocked without it. This review does **not** justify the helper on
PvE grounds — it justifies it on content-growth grounds.

---

## 5. Smallest useful abstraction (review question 4)

Two pure functions in `effects.py` (the import direction already works: resolver and
pet_ai import effects), mirroring `grant_player_resource`'s contract but smaller:

```python
def grant_player_healing(player: PlayerState, amount: int) -> int:
    """Apply a gameplay heal to a PLAYER: clamp to hp_max, return actual gained.

    Owns nothing else: no multipliers, no Mindgames decision, no logging,
    no combat_totals crediting, no liveness gate. Callers decide all policy.
    """

def grant_pet_healing(pet: PetState, amount: int) -> int:
    """Same contract for pet/totem entities (pet.hp / pet.hp_max)."""
```

That is the whole abstraction. Explicitly excluded from the helper:

* **Mindgames** — stays an explicit per-site decision. The existing
  `_apply_mindgames_aware_healing` remains the resolver-side opt-in wrapper (it needs
  the `apply_self_inflicted_magical_damage` closure and cannot live in effects.py).
* **Logging** — callers keep their exact strings; the helper returning actual merely
  makes correct logging easy.
* **Totals crediting** — three legitimate flows (§3.4); forcing one double-counts.
* **Liveness gates** — negative-HP revival is pinned, load-bearing behavior; a helper
  with an `hp > 0` gate would break it.
* **Value computation** — scaling/dice/Clarity are action-layer concerns.

Plus **guardrail 6** in `tests/architecture_guardrail_suite.py`: flag
`.res.hp = min(` and pet-HP clamp-increase writes in gameplay files
(`GAMEPLAY_FILE_BASENAMES`) outside the two helpers, with the narrow
file+function+snippet allowlist format guardrails 1–5 already use. Allowlist
seed: the helper bodies and Lay on Hands' `res.hp = res.hp_max` full-set (which the
`min(` pattern won't match anyway; cover it with a documented exception if the
guardrail regex is broadened).

## 6. Risks (review question 7)

If the helper conversion is implemented, mechanics most likely to regress, ranked:

1. **Mindgames interactions.** Highest risk: a converter "helpfully" routes lifesteal,
   item heals, or pet heals through the Mindgames-aware wrapper, silently changing
   twist coverage. Twist wording, zero-healing totals under twist, and the SoV flip
   matrix are all pinned — but item-heal-under-Mindgames is NOT pinned, so a mistake
   there would pass the suite today.
2. **End-of-turn healing totals.** The `healing_done` accumulation (effects.py:2675 →
   resolver.py:1350) must keep summing actuals exactly once; if the helper ever
   credits totals itself, action-path healing double-counts via resolver.py:3599.
3. **Negative-HP revival timing.** DoT lifesteal reviving a source from negative HP
   before winner finalization is pinned
   (`scenario_healing_resolves_from_negative_hp_before_winner_check`); any liveness
   gate added "for safety" breaks it.
4. **Log strings.** Five sites log base values; converting them to log actuals is a
   behavior change users see. Conversion must keep logging the base variable until a
   separate, deliberate log-correctness decision.
5. **The non-clamp shapes.** Lay on Hands (`= hp_max`) and Wild Growth's
   cyclone-gated write don't fit a naive mechanical rewrite.
6. **Pet crediting.** Lightning Breath's combined pet+owner crediting and Kill
   Command's owner crediting are easy to "normalize" by accident.

## 7. Suggested roadmap (review questions 8–9)

Exactly one roadmap: four tiny, separately-landable PRs. Every PR is
behavior-preserving — regression suite byte-identical, no log changes, no balance
changes.

* **PR A — Add the helpers, convert `effects.py`.**
  Add `grant_player_healing` / `grant_pet_healing` to `effects.py` next to
  `grant_player_resource` (effects.py:2505). Convert the four effects.py sites:
  item `heal_on_hit` (2253), item `heal_self` (2622), HoT regen (2674), pet HoT
  regen (2752). Sites that log base values keep logging the base variable.
* **PR B — Convert `resolver.py`.**
  Convert sites 1–6 and 8–12 of §1.2 (lifesteal ×2, Kill Command, the clamp-based
  direct-heal handlers, DoT lifesteal, Ancestral Knowledge, penance_self) and re-point
  `_apply_heal_with_clamp` (203) at the effects helper so the Mindgames flip (3751)
  and `_apply_mindgames_aware_healing` (211) ride along unchanged. Lay on Hands'
  full-set stays as-is with a short comment.
* **PR C — Convert `pet_ai.py`, add guardrail 6.**
  Convert Lightning Breath (pet_ai.py:645–647), then add the static guardrail per §5
  so no unconverted site can reappear. This PR is last-of-code deliberately: the
  guardrail lands only when the allowlist is minimal.
* **PR D — Document the contract (docs/tests only).**
  Add a "Healing pipeline rules" section to AGENTS.md mirroring the resource-pipeline
  section: route heals through the helpers, log from returned actuals for NEW logs,
  and — most valuable — an explicit Mindgames coverage table making §3.2's implicit
  policy deliberate (twisted: direct heals, ability on-hit heals, HoT ticks; not
  twisted: lifesteal, item heals, pet-sourced heals, Ancestral Knowledge). Add one
  regression scenario pinning item `heal_on_hit`/`heal_self` behavior under Mindgames
  *as it is today*, so the currently-untested divergence becomes a decision.

Files/functions touched across the roadmap:

| File | Functions/sites |
|------|-----------------|
| effects.py | new `grant_player_healing`, `grant_pet_healing`; `trigger_on_hit_passives` (heal_on_hit branch), `trigger_end_of_turn_passives`, `trigger_end_of_turn_effects`, `end_of_turn_pet` |
| resolver.py | `_apply_heal_with_clamp`; `_resolve_actor_post_damage_reactions_stage` (both lifesteal blocks); `_handle_kill_command_special`, `_handle_healthstone_special`, `_handle_holy_light_special`, `_handle_flash_heal_special`, `_handle_wild_growth_special`; `penance_self` branch in `resolve_action`; DoT-lifesteal and Ancestral Knowledge blocks in `resolve_end_of_turn_stage` |
| pet_ai.py | `lightning_breath` runner heal block |
| tests/architecture_guardrail_suite.py | new guardrail 6 + allowlist |
| tests/regression/ (PR D) | one new Mindgames/item-heal pinning scenario |
| AGENTS.md (PR D) | new "Healing pipeline rules" section |

## 8. Anti-goals — what must NOT change (review questions 5, 10)

Do not build:

* **A healing modifier stack** (generic +healing%/−healing%/healing-taken auras).
  No such mechanic exists; adding the machinery is speculative.
* **Overheal tracking/stats.** Nothing consumes it today.
* **Mindgames inside the helper.** Twist coverage is per-source policy and must stay
  visible at call sites; a default would silently rewrite the §3.2 matrix.
* **A merged Mindgames self-damage backend.** The queued-HoT path is correct
  decoupling (effects.py cannot reach resolver closures); unifying inverts the
  dependency direction.
* **Entity-generic `heal(anything)` polymorphism.** Two explicit helpers match the
  AGENTS.md tag-helper philosophy; champion/pet is the whole entity taxonomy this
  duel-shaped engine needs. No ECS, no entity hierarchy, no battlefield or resolver
  rewrite, no many-vs-many generalization.

Do not change while (or by) converting:

* Log strings — including the five base-value logs and the three twist wordings.
  Unifying wording is a separate log-correctness task needing test updates.
* Mindgames coverage — which sources are twisted/flipped stays exactly as today
  until PR D's table makes changes discussable.
* Totals crediting — including the gaps (pet HoT regen, Mindgames flip healing
  credit nobody). Fixing a gap is a user-visible stats change; decide it separately.
* Liveness gates — negative-HP revival, Ancestral Knowledge's `hp > 0`, pet-alive
  checks, the cyclone/immune-all suppression rules, and Ice Block's self-regen
  exception.
* HoT lifecycle — `regen` data shape, `skip_first_tick`, expiry timing, and the
  end-of-turn ordering (pet phase → ticks → SoV → Ancestral → duration decrement).
* Shield of Vengeance — nothing in it needs to know about the helper.
* `resolve_dot_tick` / `apply_damage` — the flip path is already single-consumer.

## 9. Final recommendation

The current healing architecture is **functionally healthy but structurally
pre-pipeline**: every site is individually correct and well pinned, yet correctness
depends on copy-pasting the right one of five subtly different blocks, and the
divergence axes (Mindgames coverage, log amounts, totals crediting, liveness gates)
exist only as unwritten convention. "Already good enough" is a defensible answer for
a frozen codebase — but this codebase is explicitly still growing content.

Recommendation: **implement the smallest helper** — `grant_player_healing` +
`grant_pet_healing` as pure clamp-and-return-actual functions in `effects.py`, plus
static guardrail 6 and an AGENTS.md healing contract with an explicit Mindgames
coverage table — via the four tiny PRs in §7. Do **not** build a full
resource-pipeline equivalent: healing legitimately has no global modifier stack, and
its one hard mechanic (Mindgames) must remain explicit per-site policy. The payoff is
preventive (guardrail + convention for future sites), the risk is low if every log
string, twist decision, crediting flow, and liveness gate is preserved verbatim, and
nothing about the duel-shaped engine or its planned PvE shape needs anything bigger.
