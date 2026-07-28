# Mak'Gora agent instructions

## Project architecture

Mak'Gora is a mature Python turn-based PvP engine with Flask/Socket.IO frontend integration.

This repository is not a rewrite target. Treat the current engine as production gameplay code. Prefer small, targeted, data-driven changes that preserve existing timing and deterministic behavior.

Core files:

* `classes.py`: class metadata, base stats, and resource-display configuration.
* `abilities.py`: data-driven ability definitions.
* `effects.py`: reusable effect templates plus passive, healing, mitigation, and resource helpers.
* `resolver.py`: generic turn-resolution coordination (turn order, targeting, redirects, damage application, resource spending, final logs). Keep generic; avoid class- or ability-specific hacks.
* `models.py`: match/player state and shared data structures (including combat-total shapes).
* `items.py`: item definitions and item passive data.
* `pets.py`: pet/totem definitions.
* `pet_ai.py`: pet/totem action behavior and pet resource handling.
* `sockets.py`: viewer-relative snapshot serialization and the post-combat summary line.
* `duel.html`: frontend UI and player-facing static documentation.
* `tests/regression/`: domain-organized deterministic regression scenarios.
* `tests/regression/registry.py`: authoritative scenario registration and execution order (`SCENARIOS`, `run_all`, `validate_scenario_registry`).
* `tests/architecture_guardrail_suite.py`: static guardrails (e.g. direct player-HP-write rejection).
* `tests/source_kind_validation_suite.py`: damage source-kind metadata validation.
* `tests/effect_tags_validation_suite.py`: effect-tag metadata validation.
* `tests/subschool_validation_suite.py`: school/subschool metadata validation.
* `tests/path_discovery.py`: canonical, engine-free test-time path discovery for `duel.html` and the repository root.

New regression scenarios belong in the appropriate `tests/regression/` domain module and must be registered in `tests/regression/registry.py`. `tests/regression_suite.py` is now only a compatibility import shim and is not the place to add scenarios.

## Non-goals

Do not rewrite the engine.

Do not perform broad refactors unless the task explicitly asks for an architecture cleanup.

Do not change balance numbers unless the task explicitly asks.

Do not change turn timing, duration semantics, cooldown behavior, proc timing, or deterministic test behavior unless the task explicitly asks.

Do not perform unrelated formatting-only changes.

Do not add new mechanics while fixing bugs unless the requested fix requires it.

## General coding rules

Keep diffs tight and targeted.

Prefer data-driven behavior over hardcoded resolver branches.

Preserve existing logs unless the task is specifically about log correctness or the old log is provably wrong.

When adding an item, ability, pet, effect, or passive, update all required surfaces:

* backend data
* resolver/effects integration if needed
* static docs in `duel.html`
* frontend display/mouseover surfaces if applicable
* deterministic regression tests

### Design-completeness rule

If the user has not supplied enough information to define a class, ability, item, pet, summon, totem, passive, resource, buff, debuff, effect, formula, cost, cooldown, duration, target mode, school, subschool, timing rule, log, or interaction unambiguously, stop and ask for the missing design decisions. Do not invent balance numbers or gameplay behavior silently.

Distinguish, and treat differently:

* **Design decisions** — balance numbers, formulas, resource behavior, playstyle, and any gameplay-visible choice — require user approval before implementation.
* **Implementation details** — how to wire an already-decided design into the engine — can be derived from existing engine conventions and the contracts in this document without asking.

When in doubt about whether something is a design decision or an implementation detail, ask.

## Adding a New Class

The remaining classes (Death Knight, Monk, Demon Hunter, Evoker) are added one at a time under the rules below. These requirements are mandatory; a class that skips any of them is not ready to merge.

### Design gate

Before writing any implementation:

1. Read `ROADMAP.md` for the current phase, class status, and delivery rules.
2. Create a class-specific specification by copying `CLASS_IMPLEMENTATION.md` into a class-specific issue, design document, or PR plan, then complete that copy (or provide an equivalent complete specification). Never place class-specific design values into the canonical template.
3. Inspect at least one existing class with similar mechanics to reuse established conventions.
4. List the unresolved design questions.
5. Do not begin implementation while any load-bearing design question remains unresolved.

`CLASS_IMPLEMENTATION.md` is the canonical reusable, class-neutral template:

* It must remain reusable and class-neutral — never fill it with a specific class design.
* Editing the template itself is allowed only when improving the template's structure or requirements.
* Completed class designs belong in a class-specific issue, design document, or PR plan.

Load-bearing questions include:

* base stats;
* HP and resources;
* initial and maximum resource values;
* resource generation and spending;
* every ability's formula and dice;
* accuracy/crit behavior;
* costs and cooldowns;
* targets;
* schools and subschools;
* durations;
* effect behavior;
* dispel behavior;
* tags and resolution layers;
* pets or summons;
* logs;
* Mindgames behavior;
* Cyclone behavior;
* immunity, absorb, and redirect interactions;
* class color and UI representation.

### Resource foundation rule

The current engine directly models **Mana**, **Energy**, and **Rage**.

When a class requires another resource — such as Fury, Runic Power, Runes, Chi, or Essence — do **not** silently map it onto an existing resource merely because the mechanics appear similar. Instead:

1. Determine whether the resource can legitimately reuse an existing canonical system.
2. Obtain explicit user approval for any intentional reuse.
3. Otherwise, propose a small reusable foundation PR that adds the resource properly.
4. Add the complete class in a later PR, on top of the merged foundation.

Do not replace the entire resource model with a generic dictionary unless explicitly requested.

### Complete-class rule

A class must not become selectable until all required surfaces are complete.

A complete class requires:

* class metadata and base stats;
* resource initialization and display;
* class color;
* complete ability data;
* effect templates;
* passive/signature mechanics;
* pet/summon data when applicable;
* generic engine integration;
* frontend selection/display integration;
* player-facing documentation in `duel.html`;
* deterministic regression coverage;
* validator compliance.

Do not merge:

* empty class shells;
* selectable classes with placeholder abilities;
* undocumented abilities;
* TODO balance values;
* dead buttons;
* incomplete resource UI;
* unregistered effects;
* untested signature mechanics.

### Mandatory implementation surfaces

For each new class, audit and update whichever of these are applicable — do not require every file to change when it is unnecessary:

```
classes.py
abilities.py
effects.py
pets.py
pet_ai.py
models.py
resolver.py
sockets.py
duel.html
tests/regression/*
tests/regression/registry.py
AGENTS.md
ROADMAP.md
```

### Architecture rules for classes

* Abilities remain data-driven wherever practical.
* Reusable mechanics become generic helpers rather than class-specific resolver branches.
* Ability costs use the canonical cost pipeline (`adjusted_resource_costs()` / `can_pay_costs()` / `consume_costs()`).
* Player resource gains use `grant_player_resource()`.
* Player HP restoration uses `apply_player_healing()`.
* Damage uses the shared damage pipeline.
* Damage packets carry a valid source kind.
* Magical effects and damage carry valid subschools.
* Effects carry appropriate tags and resolution layers.
* Logs use actual gained/dealt values where required.
* Pet-produced healing remains separate from player-produced healing.
* Damage totals continue to represent actual HP damage.
* Deterministic RNG order is preserved.

If a new generic hook is added for a class, add regressions proving both:

1. the new class behavior;
2. unchanged behavior for any existing mechanic sharing that hook.

### Class regression matrix

Require deterministic coverage for:

* every ability;
* success and failure/denial paths;
* resource affordability and spending;
* cooldown application and expiry;
* buffs/debuffs and duration timing;
* damage, healing, and absorbs;
* immunity;
* Mindgames where relevant;
* Cyclone where relevant;
* crowd-control denial;
* dispels where relevant;
* pets, redirects, or AoE where relevant;
* class signature mechanics;
* logs and player-facing documentation;
* selection and snapshot/UI integration.

Register every scenario exactly once (see `tests/regression/registry.py`).

### Scope control

One class per implementation series. Use separate PRs when appropriate:

* a foundation PR for a reusable resource or engine mechanic;
* a complete class PR after the foundation merges;
* an optional balance-only PR after functional correctness is established.

Do not combine unrelated cleanup, refactors, items, or other classes with a class implementation.

## Resolver/effects boundaries

`resolver.py` coordinates turn order, action resolution, targeting, redirects, damage application, resource spending, and final combat logs.

`effects.py` owns reusable effect/passive/mitigation/resource helpers.

Avoid ability-specific hacks in `resolver.py`.

If behavior can be represented as ability/effect/item/pet data, prefer data.

If a helper is reusable across multiple abilities/items/effects, put it in `effects.py` or another focused helper module instead of embedding it inside one resolver branch.

## Global periodic-item stage

Scheduled equipment effects use the single global `periodic_item_stage`. It runs exactly once for the active one-based global match turn, after normal end-of-turn pet/player ticks, deferred explosions, class mechanics, and their phase logs, but before duration/expiry cleanup, pet cleanup, and final alive/winner evaluation. Never run periodic equipment inside an individual player loop or create a separate stage per item.

Periodic item passives use `trigger: "periodic_end_of_turn"` plus a non-empty `type`, positive integer `interval`, and positive integer `first_trigger_turn`. An activation is eligible when `current_turn >= first_trigger_turn` and `(current_turn - first_trigger_turn) % interval == 0`. The collector reads canonical equipped-item slots, supports dictionary or list passive data, snapshots all eligible activations before dispatch, and orders them by match player order, `EQUIPMENT_SLOT_ORDER`, then passive-list index.

`PERIODIC_ITEM_HANDLERS` owns type-to-handler dispatch. The stage owns scheduling, collection, snapshotting, ordering, and dispatch only; handlers own formulas, RNG, targets, damage/healing, logs, and combat-total attribution. Future periodic items must use this metadata and registry: do not hardcode item IDs in the resolver or add item-specific stage branches. Missing handlers and invalid periodic schedules fail loudly. An empty stage must append nothing and consume no RNG.

Temporary effect/HoT regeneration remains in normal player `end_of_turn`
processing. Equipped periodic-item healing runs later in the global periodic
item stage, after normal player/pet ticks, deferred explosions, and class
mechanics, but before cleanup and winner evaluation. Both paths must route
final player HP restoration through `apply_player_healing()`.

Damage from the committed periodic-item dispatch may consume reactive absorbs.
The post-periodic Shield of Vengeance causal chain (see "Shield of Vengeance
end-of-turn cascade" below) therefore runs only when the periodic batch actually
produced activations, after the complete dispatch and before cleanup or winner
evaluation; never insert that reaction checkpoint between individual periodic
activations. This gate is not a substitute for ordinary cascading: the earlier
pre-periodic checkpoint already resolves ordinary cascades completely.

Every deferred damage-reaction log (stealth break, break-on-damage, and any
future reaction queue) produced during or after the periodic dispatch must be
flushed through the single periodic-boundary helper. On a turn with periodic
activations that helper runs three times -- before dispatch, immediately after
dispatch, and again after the post-periodic reaction checkpoint -- because the
post-periodic Shield of Vengeance explosion can itself queue break-on-damage
and stealth-break reactions that must surface before duration/expiry cleanup,
pet cleanup, and winner evaluation. Turns with no periodic activation keep the
legacy timing: break-on-damage stays deferred to the final end-of-turn flush.
Do not add a periodic-boundary flush that drains only one reaction queue.

### Periodic self-sacrifice empowerment

`periodic_self_sacrifice_empower` is the reusable metadata-driven handler for
scheduled item effects that pay a nonlethal current-HP cost and refresh one
ordinary `empower_next_offense` effect. HP paid through
`apply_player_hp_sacrifice()` is a cost, not damage: it bypasses
`apply_damage()`, mitigation, absorbs, immunity, Mindgames, damage reactions,
rage-from-damage, stealth/break-on-damage handling, Shield of Vengeance
accumulation, and combat damage totals.

Every active `empower_next_offense` effect snapshots onto the same next valid,
committed, player-selected cast with direct damage. The snapshot is immutable
and ordered canonically by effect ID, so damage, consumption, and logs never
depend on runtime effect insertion order. Simultaneous damage multipliers
combine multiplicatively into one scalar before the canonical damage stage
performs integer truncation. All snapshotted effects consume together at the
committed direct-offense boundary, and each effect's empowerment log emits
exactly once per cast in canonical order.

Every direct hit and direct AoE packet from that cast receives the combined
snapshot. Attached future DoT ticks, strike-again packets, item/on-hit procs,
duplicate spells, autonomous entities, periodic items, and shield-derived
explosions do not. Secondary damage receives a separately resolved parent-hit
basis that excludes only the generic next-offense multiplier; critical strikes,
ability formula overrides, Flame Dance, Death doubling, Challenger snapshots,
ordinary outgoing modifiers, target mitigation, and ignore-mitigation flags
remain part of that basis unless the secondary mechanic explicitly excludes
them.

The effects consume even when the cast misses, is evaded, is immune, is fully
absorbed, or deals zero HP damage. Rejected or unavailable selections,
insufficient resources, inability to act, Pass, healing, defense, utility, pure
control, and pure DoT casts do not consume them.

`direct_damage` is a strict boolean ability-definition property. When omitted,
the shared classifier infers direct damage only from immediate `dice`,
`scaling`, or `flat_damage` formula metadata. Offensive-action classification
remains broader and separate: hostile effects, pure DoTs, and crowd control may
be offensive without dispatching a direct packet or consuming next-offense
effects. Fixed-value shield-derived damage is explicitly non-direct for this
contract.

Scheduled empowerment refreshes pay their HP cost again, replace the existing
same-ID effect, and never stack charges. A failed nonlethal payment leaves an
existing charge unchanged.

Fixed-value shield-derived damage, including Astral Explosion and Shield of
Vengeance, uses the exact committed absorb value. It ignores Attack, Intellect,
critical strikes, ordinary outgoing attack multipliers, and next-offense
effects, and it never consumes a next-offense charge.

## Shield of Vengeance end-of-turn cascade

Shield of Vengeance is resolved through an explicit causal chain, not a blind
one-pass scan of both players. Every checkpoint begins in deterministic player
order, P1 then P2. Whenever one player's Shield of Vengeance actually explodes,
the opponent's Shield of Vengeance is immediately rechecked, because the
exploding shield's absorbed damage may have consumed the opponent's absorb and
made it eligible. If that opponent now explodes it immediately rechecks the
first player, and this alternation continues until the causal chain ends (a
checked side has no eligible explosion). The entire chain resolves in the same
turn.

`trigger_shield_of_vengeance_explosion(owner, enemy)` returns `True` only when an
actual explosion event fires and `False` otherwise (no shield, the shield is not
ready, or a ready shield had nothing absorbed to explode). "Fired" means the
explosion event happened, not that it dealt positive HP damage: a fully absorbed
packet still returns `True`, because that absorbed damage can consume the
opponent's shield and form the next link. The owner's shield instance is removed
(resolved) before its explosion damage is applied, so a shield can explode at
most once and the chain is naturally finite -- no iteration cap. The chain helper
simply loops `while trigger(owner, enemy): swap owner/enemy`, and the
deterministic checkpoint is expressed as `resolve_chain(P1, P2)` then
`resolve_chain(P2, P1)`.

There are two checkpoints. The pre-periodic checkpoint runs the chain in P1->P2
order and resolves ordinary cascades completely, in the same turn, regardless of
whether any periodic item activates. Same-turn ordinary cascades -- P2 explodes,
consumes P1's remaining absorb, and P1 immediately explodes in that same turn --
are intentional gameplay behavior, not an accidental fixed-point regression, and
must not be deferred to a later turn merely because P1 was checked before P2. The
post-periodic checkpoint exists only to resolve shields changed by the committed
periodic-item stage; it runs the same chain but only when `periodic_activations`
is non-empty, and it is never a substitute for the pre-periodic cascade.

The whole periodic batch finishes before any post-periodic explosion starts:
complete periodic activation dispatch, flush reactions produced by periodic
handlers, resolve the complete Shield of Vengeance causal chain, flush reactions
produced by the chain, then duration cleanup, pet cleanup, and winner evaluation.
Cleanup and winner evaluation occur only after the chain and its deferred
reaction-log flushes. Explosion and damage logs are emitted in actual execution
order -- each explosion and its hit line as the chain runs -- never collected and
logged afterward.

## Ability empowerment contract

Fixed ability-specific empowered formula variants (an effect that changes one specific ability's scaling/dice/log, e.g. empowered Mind Blast or Final Verdict) belong in `abilities.py` under the ability's `empowered_by` metadata:

```python
"empowered_by": {
    "effect_id": "mind_blast_empowered",
    "scaling_override": {"int": 1.3},          # replaces base scaling
    "dice_override": {"type": "d8", "power_on": "roll"},  # replaces base die
    "log": "Empowered by Mind Flay!",
    "consume": {"mode": "remove"},             # or {"mode": "stack", "amount": 1}
}
```

Contract rules:

* `resolver.py` must not add new `ability_id == ...` empowered formula branches; the generic `active_ability_empowerment()` / `consume_ability_empowerment()` path handles all `empowered_by` specs.
* `scaling_override` and `dice_override` REPLACE the base values; they never merge or multiply.
* Broad modifiers are separate systems and must not be folded into `empowered_by`: Flame Dance (next qualifying Fire ability, 1.5x raw), Avenging Wrath's global `outgoing_damage_mult` on its effect template, Onslaught stacks, and generic `empower_next_offense` effects.
* Consumption timing must be explicitly declared via `consume`. No `consume` entry means the empowering effect is never consumed by the ability (Avenging Wrath). Consumption happens once per ability execution after a valid resolved cast — even when the attack roll misses — and never when the action is rejected/denied before resolution. Unknown consume modes raise instead of guessing.
* Deterministic RNG order is part of current behavior and must be preserved unless intentionally changed: the base ability die is rolled first, then accuracy resolves, and the empowered override die is rolled only after the hit lands (replacing the displayed base-roll log line). Rolling the override die earlier would shift every later seeded roll.

## Resource pipeline rules

Player resource gains must route through:

```python
effects.grant_player_resource()
```

Do not directly mutate these for gameplay resource generation/restoration:

```python
player.res.mp
player.res.energy
player.res.rage
```

Allowed direct mutations:

* player initialization/setup
* test setup
* explicit resource spending
* resource caps/clamps inside `grant_player_resource()`
* tightly documented exceptional mechanics

Gameplay resource spending must use the resolver cost pipeline:

```python
adjusted_resource_costs()
consume_costs()
can_pay_costs()
```

Do not subtract ability costs manually in unrelated branches.

When a resource gain is logged, the log must use the actual amount returned by `grant_player_resource()`, not the base amount from ability data.

Same-action resource gains that depend on an action-start state must use the correct action snapshot. Do not let paying an action cost change that same action’s resource-gain stance mid-cast.

## Player healing application contract

Player healing is separate from `grant_player_resource()`. The resource helper ignores HP and must not gain an `"hp"` branch.

`effects.apply_player_healing()` is the canonical final application primitive for player HP restoration. It owns only the upper `hp_max` cap, the `res.hp` mutation, and the actual-gained return value, and it must not lower-clamp transient negative HP. Formulas/dice, eligibility, timing, ordinary log wording, and combat-total attribution remain caller-owned. Mindgames-aware player-produced healing resolves through `resolve_player_produced_healing()`, which still delegates normal player HP restoration to `apply_player_healing()`. Pet HP restoration remains explicit and local.

All known production player HP restoration now routes through `effects.apply_player_healing()`: the action-time paths (Healthstone, Holy Light, Flash Heal, Lay on Hands, Wild Growth, Penance Self, and generic on-hit healing such as Victory Rush), the damage-derived paths (full `heal_from_dealt_damage` — Fury of Azzinoth; fractional `heal_from_damage` — Drain Life; periodic DoT `lifesteal_pct` — Vampiric Touch and Devouring Plague; and the final HP application inside `apply_damage()`'s Mindgames damage-to-healing branch), passive on-hit item healing (`heal_on_hit`, e.g. Thunderfury), equipped periodic-item healing (`periodic_self_heal`, e.g. Staff of Immortality), effect/HoT regeneration (`regen["hp"]` — Regrowth, Frenzied Regeneration, Healing Stream, Ice Block, Vanish, etc.), Ancestral Knowledge, and Emerald Serpent Lightning Breath owner healing. Normal pet HP application remains locally clamped (Kill Command, pet regen, and the serpent's own half of Lightning Breath); Kill Command nevertheless uses the shared player-produced-healing resolver so its producer classification and Mindgames behavior are correct. Whenever a healing log displays a numeric normal-healing amount, that number is the actual HP gained, never the pre-cap requested amount; the capped remainder is tracked as overhealing (see the healing accounting policy below).

Ancestral Knowledge follows the same temporary-negative-HP semantics as other player healing: its former `hp > 0` liveness gate is removed, so it can restore a Shaman from zero or negative HP before the final winner check and insufficient healing may leave the Shaman negative. Cyclone suppresses only its healing portion and takes precedence over Mindgames, preventing both healing and converted self-damage. Otherwise, Mindgames converts the requested pre-cap healing into magical Shadow self-damage through the shared damage pipeline. The passive's Intellect increase remains independent and still occurs in the normal, full-HP, Cyclone, Mindgames, zero-HP, and negative-HP cases. Final survival is determined only by the end-of-turn winner check.

The architecture guardrail suite (`guardrail_player_hp_writes` in `tests/architecture_guardrail_suite.py`) statically rejects new direct production player-resource HP mutations outside `effects.apply_player_healing()` and narrow documented non-healing exceptions. Pet HP mutations remain outside this player-only guardrail. Concretely: player HP restoration must use the helper; damage subtraction, HP sacrifice/spending, and explicit debug/setup writes are not healing and may remain local `*.res.hp` writes only when they carry a narrow documented `ALLOWED_PLAYER_HP_MUTATIONS` entry; allowlisting must never be used to bypass the helper for ordinary restoration; and pet HP (bare `pet.hp`) stays explicit and local.

Current load-bearing application behavior, pinned by regression tests; do not change it incidentally:

* Player HP restoration caps only at `hp_max`.
* Healing applies to the current HP value as-is. Transient negative HP must not be lower-clamped to zero before healing: healing a player at `-6` by 12 leaves them at `6`; healing by less than the deficit leaves them negative until the end-of-turn winner check.
* Action-time healing resolves before direct damage application. Damage-derived healing (lifesteal, heal-from-dealt) uses actual dealt HP damage and resolves only after it is known. Temporary effect/HoT regeneration applies during normal player `end_of_turn` processing before that champion's queued DoT damage. Equipped periodic-item healing applies later, after queued DoT damage and class mechanics but before cleanup and the winner check.
* Callers currently own formulas, dice, eligibility, timing, Mindgames decisions, log wording, and combat-total attribution. Wherever a healing source is credited to combat totals or logged with a number, the amount used is the actual HP delta, not the request.
* Mindgames healing-to-damage converts the requested pre-clamp healing amount, not the portion that would have fit below `hp_max`.
* `DamageApplicationResult.mindgames_healing` represents nominal converted damage, not actual HP restored. Direct-DoT application uses it as evidence that the source hit resolved; do not rename or redefine the field. Its sibling `mindgames_healing_gained` carries the actual HP the flip target gained and drives healing/overhealing totals and flip-log wording.
* Pet HP application (`pet.hp`) remains outside this player-only helper contract.
* Do not add a universal healing pipeline, healing modifier stack, or entity-generic healing abstraction without a concrete mechanic that needs it. Overhealing is tracked only through the combat-total counters described below, not through a per-event ledger.

## Mindgames damage-to-healing contract

Mindgames damage conversion is producer-aware and final-recipient entity-aware. Player-produced damage carries an explicit `mindgames_flip_damage` packet/action flag; `apply_damage()` must never infer the flip from its `source` object because autonomous pet, totem, and summon attacks may pass their owner as that object and do not inherit the owner's Mindgames state.

After redirects, immunity, and mitigation resolve, every eligible champion or pet recipient converts independently before absorbs or HP damage. A converted packet restores the final entity's HP up to its own maximum, consumes no absorbs, triggers no post-damage reactions, and contributes zero actual damage. Its positive nominal `mindgames_healing` still counts as a resolved hit for attached effects even when the recipient gains zero HP at full health. AoE producers must forward the same explicit action snapshot to every champion and pet packet; that snapshot is captured from the producer independently of any one recipient's immunity, untargetability, mitigation, or skipped packet.

The explicit flag remains authoritative for self-target packets: an eligible player-owned periodic item packet may convert when it targets its owner. Callers that create non-flippable self-damage, especially Mindgames-twisted healing-to-damage packets, must explicitly pass `mindgames_flip_damage=False` and retain `DAMAGE_SOURCE_SELF` where applicable so the damage cannot recursively flip back into healing.

Player-produced DoT ticks against champions and pets use the source player's live Mindgames state at tick time, while one periodic-item activation snapshots its owner's Mindgames state once before dispatching its deterministic all-entity target batch. Autonomous pet-, summon-, and totem-produced damage remains excluded even when the owner's `PlayerState` is passed through the damage API.

Player-produced special-case damage branches that return before the generic action-result path must capture the producer's Mindgames action snapshot before the branch and dispatch eligible packets through `apply_damage()`; direct HP mutation must not bypass the explicit packet contract.

## Mindgames healing-to-damage contract

Mindgames healing conversion is producer-aware and final-recipient entity-aware. `resolve_player_produced_healing()` handles only HP healing explicitly classified as player-produced. It accepts the producing player and the final player or pet recipient; normal player HP restoration delegates to `apply_player_healing()`, while normal pet HP restoration keeps its local `pet.hp` cap. Resource restoration such as mana, rage, and energy is never part of this resolver.

When the healing producer has Mindgames at resolution time, the full requested pre-cap healing amount becomes terminal magical Shadow damage to the actual recipient. The packet goes through `apply_damage()` with the producer as source, the final player or pet as target, redirects disabled, ordinary armor/magic-resistance mitigation disabled, an explicit self-damage source kind, and `mindgames_flip_damage=False`. Immunity and absorbs still apply; actual HP damage triggers the normal damage reactions, and a defeated pet follows canonical defeat/permanent-death cleanup exactly once. The canonical result and log distinguish requested healing, actual normal gain, overhealing, actual twisted HP damage, absorbed damage and breakdown, immunity, and the final entity label.

Player abilities, equipped items, player-produced on-hit healing, player-produced lifesteal, explicitly marked player HoTs/regeneration, Ancestral Knowledge, periodic equipped-item healing, and Kill Command use this resolver. Scheduled player HoTs carry explicit producer metadata and evaluate the producer's live Mindgames state when each tick resolves. `effects.end_of_turn()` collects marked player HoT/regeneration ticks as typed player-produced-healing events instead of applying them there; `resolver.resolve_end_of_turn_stage()` applies those events after all queued champion DoT packets and their damage-derived lifesteal for that player, but before pet upkeep and the Shield of Vengeance checkpoint. The established order is therefore incoming player DoTs, player-produced healing events, pet upkeep, then the shield checkpoint. Cyclone or another eligibility rule that suppresses the healing event also suppresses conversion and is not overhealing. Unclassified/autonomous regeneration may remain local, and resource regeneration stays separate from these HP-healing events.

Player-produced-healing integration fails closed. Explicitly marked player HoTs/regeneration, player `heal_on_hit` passives, and player-owned periodic-item healing require the resolver callback and raise `RuntimeError` when it is missing; they must never silently fall back to ordinary healing that bypasses Mindgames. Autonomous pet-, summon-, and totem-produced healing and resource restoration do not require this callback.

Autonomous pet-, summon-, and totem-produced healing never inherits the owner's Mindgames merely because an engine call receives the owner's `PlayerState`. Emerald Serpent Lightning Breath, pet HoTs, and pet regeneration remain normal pet-produced healing credited only to `pet_healing`/`pet_overhealing`. Mindgames damage-to-healing conversion inside `apply_damage()` also bypasses the player-produced-healing resolver, so neither direction can recursively convert back.

Twisted player-produced healing credits zero normal healing, zero normal overhealing, and zero pet healing. It is not ordinary outgoing damage merely because the producer harmed themselves or an owned pet; retain only narrowly established self-damage attribution such as the existing Ancestral Knowledge regression. Absorbed or immune damage is never credited as HP damage.

## Healing accounting policy

Combat totals (`match.combat_totals`, shaped by `models.COMBAT_TOTAL_KEYS` and accessed through `models.combat_totals_entry()`) carry five counters per combatant SID:

```python
{
    "damage": 0,
    "healing": 0,
    "pet_healing": 0,
    "overhealing": 0,
    "pet_overhealing": 0,
}
```

Finalized policies, pinned by regression tests:

* Visible numeric healing amounts use actual HP gained. When a healing log displays a number, that number is the actual HP restored, never the pre-cap request. (Whether a path emits a log is unchanged; Lay on Hands includes the actual amount restored.)
* Player-produced and pet-produced healing are separate accounting categories. `healing` covers champion abilities, lifesteal, item healing, HoTs/regeneration, Ancestral Knowledge, Kill Command (the champion ability produces the pet's healing), and Mindgames damage-to-healing credited to the Mindgames caster. `pet_healing` covers healing produced by pets/totems/summons — Emerald Serpent Lightning Breath (both the serpent's self-heal and the owner heal) and pet HoT/regen ticks. Classification follows the producer, not the recipient.
* `pet_healing` is stored under the owner SID for routing/summary purposes but is semantically pet-produced healing and must never be added to the owner's regular `healing` value. No healing event is counted in both buckets.
* Effective healing totals use actual gain. Overhealing — `max(0, requested - actual)` — is tracked in `overhealing`/`pet_overhealing` for future mechanics but is never included in effective healing totals.
* Overhealing counts only requested healing lost to an upper maximum-HP cap. Temporary negative-HP recovery is not overhealing: healing a player at `-8` by 12 is 12 actual healing and 0 overhealing. Cyclone/immunity suppression and Mindgames healing-to-damage twists are also not overhealing.
* Mindgames damage-to-healing tracks nominal conversion and actual gain separately. The nominal `mindgames_healing` amount keeps qualifying direct-DoT application; `mindgames_healing_gained` (actual) drives the caster's `healing`/`overhealing` credit and the flip log ("Mindgames flips N damage into healing; X restores M HP."). The conversion is credited to the player who applied Mindgames, read from the active Mindgames effect's `source_sid` (target-applied effects record the caster's SID at application time); the healed target is only a fallback when that metadata is absent.
* Lifesteal derived from actual dealt damage still produces zero healing when Mindgames flips the damage (actual dealt HP damage is zero).

## Damage totals, snapshots, and the post-combat summary

`combat_totals[sid]["damage"]` represents actual HP damage dealt. It excludes damage absorbed by shields, damage prevented by immunity or pre-application mitigation, damage converted into healing by Mindgames, and any nominal or pre-application value. Attribution (which SID owns pet damage or intentionally credited self-damage) is unchanged; only actual amounts are credited.

* `ActionResult["damage"]` is a pre-application mechanics value used for damage application; it is NOT authoritative combat-total credit. Direct-action damage is credited after `apply_damage()` returns, from `dealt_data["hp_damage"]`. A fully Mindgames-converted hit therefore credits the attacker zero damage while the caster's healing/overhealing credit comes from the actual/nominal conversion amounts.
* Queued proc events, DoT ticks, pet attacks, AoE pet damage, Shield of Vengeance explosions, and Mindgames-twisted Ancestral Knowledge self-damage keep their own separate hp_damage-based crediting sites; never double-count a packet that is credited elsewhere.
* Viewer snapshots (`sockets.snapshot_for()`) expose `friendly_total_pet_healing` / `enemy_total_pet_healing` alongside the existing damage/healing fields; the post-combat summary displays pet healing as its own statistic and never folds it back into regular healing. Overhealing buckets are tracked internally and are not yet displayed.
* DPT (Damage Per Turn) is derived, not stored: actual credited damage divided by completed resolved turns, computed in `snapshot_for()` as `completed_turns = max(1, int(match.turn))` (snapshots are produced after the resolved-turn counter advances, so the final resolved turn is included and a first-turn kill divides by 1). DPT is viewer-relative and displayed with exactly one decimal place.
* The tokenized post-combat log line is `Post-Combat Summary|T:{turns}|FD:..|FH:..|FPH:..|FDPT:..|ED:..|EH:..|EPH:..|EDPT:..`; the backend formatter in `sockets.py` and the parser in `duel.html` must agree exactly, and the parser guards every field so it never renders NaN/undefined/Infinity or raw placeholders.

## Damage pipeline rules

Damage that can be modified, mitigated, redirected, absorbed, immuned, logged, or used for resource/healing calculations must go through the shared damage pipeline.

Canonical damage order:

1. Compute base raw damage.
2. Apply outgoing modifiers.
3. Resolve target and redirects.
4. Apply incoming modifiers and mitigation.
5. Apply immunity/absorb/HP damage.
6. Produce actual dealt HP damage.
7. Trigger resource gains, lifesteal, healing, and post-damage reactions from actual dealt damage.
8. Format logs from final resolved damage data.

Do not use speculative pre-mitigation damage for damage-based resource gains, healing, lifesteal, or combat totals.

If queued damage is stored for later application, store raw damage and enough metadata to resolve it correctly later.

If queued damage will be re-mitigated later, do not also count its pre-mitigated value as final damage.

Preserve multihit damage instances when queuing damage so logs can render real per-hit values.

## Damage source-kind rules

When adding or modifying damage behavior, be explicit about the damage source kind.

Common source kinds:

* direct ability damage
* direct DoT application
* DoT tick
* on-hit proc damage
* strike-again damage
* pet damage
* reflect damage
* absorb explosion damage
* self damage
* environmental damage
* periodic item damage

Items/passives should define which source kinds they affect whenever the behavior is not obvious.

Example: fixed reflected or absorb-explosion damage should not accidentally inherit generic outgoing-damage modifiers unless explicitly intended.

## Packet / result dict contracts

The engine intentionally passes packets/results between stages as plain dicts. `TypedDict` declarations document the key schemas but do not change runtime behavior; do not migrate these shapes to dataclasses or classes.

Documented contracts:

* `ActionResult` (`resolver.py`): the dict returned by `resolve_action()`. It remains a plain dict. Early-return paths return partial dicts, so consumers must read keys with `.get(...)`.
* `DamageApplicationResult` (`resolver.py`): the dict returned by `apply_damage()` / `_empty_damage_result()`. It remains a plain dict.
* `PassiveDamageEvent` (`damage_events.py`): the producer-side on-hit event built by `make_passive_damage_event()`. It remains a plain dict.
* `QueuedDamageEvent` (`damage_events.py`): the resolver-side queued `"damage_event"` extra-log entry built by `make_queued_damage_event()`. It remains a plain dict.

Contract rules:

* `raw_incoming` on a passive damage event means the queued event carries pre-mitigation damage and must be re-mitigated at apply time. Fully resolved events must not carry it.
* `source_kind` is metadata-only: it classifies the packet for rules/tests and never alters damage math unless a consumer explicitly opts into filtering on it.
* Optional keys should be OMITTED when absent, not stored as `None`, whenever consumers distinguish absence from `None` (e.g. `event.get("raw_incoming") is not None`).
* When adding a key to one of these shapes, update the matching TypedDict and keep constructing plain dicts via the existing factories.

## Snapshot-vs-live state rules

Be explicit when a mechanic reads state.

Use action-start or turn-start snapshots for:

* same-action damage modifiers
* same-action resource cost modifiers
* same-action resource-gain modifiers
* simultaneous-turn fairness
* effects where spending a cost should not change the current action’s mode

Use live state only when the mechanic is intentionally reactive to the current board state, such as end-of-turn regen or passive effects that are meant to observe the current resource/HP state.

`None` snapshot values should mean “no modifier,” not “low-resource/wrath/default negative state.”

Add a short comment whenever snapshot-vs-live behavior is non-obvious.

## Effect lifecycle rules

Preserve existing duration semantics.

Current-turn application, expiry timing, cooldown ticking, and crowd-control duration behavior are gameplay-sensitive. Do not change them incidentally.

When modifying effect duration behavior, add regression tests for:

* same-turn application
* next-turn behavior
* expiry timing
* crowd-control denial
* break-on-damage behavior if relevant

Effect templates should carry metadata such as:

* category
* school
* subschool
* flags
* display metadata
* tags, when available

Avoid encoding effect behavior only in display text.

## Ability tags vs effect tags

Ability tags describe action-layer behavior.

Examples:

* `attack`
* `spell`
* `defense`
* `control`
* `aoe`
* `physical`
* `magic`

Effect tags describe active state on a player, pet, or entity.

Examples:

* `immune_all`
* `immune_part`
* `stealth`
* `incapacitating_cc`
* `break_on_damage`
* `blink_like`
* `redirect`
* `absorb`
* `damage_reduction`
* `dot`
* `hot`
* `proc`
* `form`

Do not mix ability tags and effect tags in the same helper.

Prefer explicit helpers:

```python
ability_has_tag(ability, "spell")
effect_has_tag(effect, "blink_like")
target_has_effect_tag(target, "incapacitating_cc")
```

Avoid vague generic helpers such as:

```python
has_tag(obj, tag)
```

unless the object type is explicit and constrained.

`display.priority` is for UI ordering only. Do not use display priority for gameplay resolution priority.

## Pets, totems, redirects, and AoE

Pets and totems are gameplay entities, but they do not automatically inherit all player item passives.

Be explicit when an owner’s modifier should or should not apply to pet/totem behavior.

Redirect behavior must be resolved before final target-specific mitigation whenever possible.

AoE should not accidentally use single-target redirect logic.

Queued proc damage must resolve against the actual final target at application time, not the originally assumed target.

If a direct hit kills a redirect pet before queued damage lands, queued damage must fall through or resolve according to the current redirect state.

Add regression coverage for redirect/pet/AoE/proc interactions whenever touching these systems.

## School and subschool rules

Use normalized schools consistently:

* `physical`
* `magical`

Magical damage may have a subschool:

* `fire`
* `frost`
* `nature`
* `arcane`
* `shadow`
* `holy`

Physical damage should not carry a magical subschool.

If `school == "physical"`, clear or ignore `subschool`.

If `subschool` exists, the damage/effect should normally be magical.

### Magical subschool resistance

Magical subschool resistances use the centralized mapping in `damage_types.py`:
`arcane_resist`, `fire_resist`, `frost_resist`, `holy_resist`,
`nature_resist`, and `shadow_resist`. Only the resistance matching the
damage subschool contributes to mitigation; generic magical damage without a
subschool gets no specific resistance. `ignore_magic_resist` ignores both
general Magic Resist and the matching subschool resistance, but no other
mitigation stage. Future resistance items must reuse this mapping, and
gameplay code must never hardcode specific item IDs.

When adding damage, DoTs, pet attacks, or proc damage, preserve school/subschool metadata through:

* initial ability data
* effect templates
* queued damage packets
* `apply_damage()`
* logs/tooltips where relevant

Run subschool validation when changing school/subschool behavior.

## Frontend and static docs

When adding or changing an item, ability, pet, effect, or command, update `duel.html` if the player-facing docs or UI should expose it.

For visual/frontend changes:

* follow existing CSS and tooltip patterns
* do not invent a new style system
* keep rarity colors consistent
* ensure champion-box equipment, docs, and mouseover surfaces stay aligned
* do not change gameplay logic for visual-only tasks

Static docs should match backend behavior. If a mechanic has an intentional exception, document it clearly.

## Testing rules

Add deterministic regression coverage for gameplay changes.

Use fixed seeds where randomness is involved.

Regression tests should verify behavior, not implementation details, unless the test is specifically a guardrail test.

For bug fixes, add a test that would have failed before the fix.

For visual/static-doc changes, update existing string/static UI checks where practical.

Full source-checkout validation runs every standard suite with one command:

```bash
python tests/run_all_tests.py
```

The five standard validation suites remain individually runnable for targeted development:

```bash
python tests/run_regression.py
python tests/run_architecture_guardrails.py
python tests/run_source_kind_validation.py
python tests/run_effect_tags_validation.py
python tests/run_subschool_validation.py
```

* `tests/run_all_tests.py` is the preferred command before merging; the individual runners stay available while iterating on a single area.
* The aggregate runner contains no test logic. It launches each of the five runners exactly once, each in its own subprocess, so module bootstrapping and process state stay isolated exactly as they are for a standalone run.
* Child output streams straight to the terminal and is never captured, so the exact per-suite pass counts are still reported by the child runners themselves. Report those counts, not the aggregate summary alone.
* Every suite runs even when an earlier one fails; the command exists to produce one complete validation report. The final summary lists each suite with `PASS`/`FAIL`.
* A successful aggregate exit (`0`) means all five suites passed. Exit code `1` means at least one suite failed or a declared runner file is missing.
* No standard suite requires development Markdown. The aggregate command validates engine and content **source**, so it runs unchanged in a deployed nested tree that ships neither `AGENTS.md` nor `ROADMAP.md`.
* Run targeted scenarios during development to iterate quickly.
* Run all five suites before merging a class or any shared engine foundation.
* Report exact pass counts.
* Do not claim a suite passed if it was not executed.

The aggregate runner is test infrastructure, not gameplay behavior, so its own coverage lives in the architecture guardrail suite (`guardrail_aggregate_runner_declaration`, `guardrail_aggregate_runner_subprocess_contract`, `guardrail_aggregate_runner_failure_semantics`) and drives it with an injected fake subprocess runner rather than launching the five real suites again.

### Test dependencies and path discovery

Gameplay regressions (`tests/run_regression.py`) may depend only on runtime artifacts: engine/content source modules, their data, and the `duel.html` template. They must never read development Markdown (`AGENTS.md`, `ROADMAP.md`, `README.md`, ...), because the deployed runtime tree legitimately ships without a documentation checkout, and a missing document must never fail a gameplay scenario.

Architecture/static validation (`tests/architecture_guardrail_suite.py`) does not read development Markdown either. It validates *source architecture*, so it must pass in a deployed nested tree that ships no documentation at all. Feature-specific documentation contracts — asserting exact `ROADMAP.md` phase wording or exact `AGENTS.md` prose — are not enduring engine architecture invariants and must not be added back as guardrails: they would force architecture validation to require development Markdown, and Markdown must never be copied into a production tree merely to make a test pass. `guardrail_markdown_reference_detector_self_test` enforces this statically over the whole suite module — helpers included, since a helper a guardrail calls is just as much a dependency — by closing both ways the dependency can be written: no executable `.md` / `.markdown` path literal anywhere in the file (AST-based, using the same detector applied to gameplay regressions), and no *reference of any kind* to `path_discovery.detect_repository_root()`, which reaches the documentation without spelling a literal. The second check rejects imports (aliased or not), attribute and bare-name references, `getattr` by literal *or* by a name bound to that literal, and any dynamic `getattr` on `path_discovery` — under its own name or any import alias or rebinding of it — whose attribute name cannot be checked statically — not merely calls spelled with the helper's own name, since `from path_discovery import detect_repository_root as repo_root` or `getattr(path_discovery, helper_name)` would otherwise make the call invisible. Aliasing requires a reference, an import, or a module lookup first, so banning those bans the alias. Naming the helper in a *string* (asserting `path_discovery` still exports it) is documentation of the API, not use of it, and stays allowed.

Static detection has a floor: a determined author can still reach the filesystem through `importlib`, `sys.modules`, `eval`, or a path assembled at runtime, and no AST scan closes that. The static checks exist to catch the accidental and the casual, which is where this bug class actually comes from. The authoritative check is behavioral — **run the standard suites against a nested tree containing no Markdown at all** before merging anything that touches test path discovery. That is what proves the boundary; the guardrails only keep it from eroding quietly between runs. The only exemptions are the declared detector fixture tables (`_MARKDOWN_SUFFIXES`, `_MARKDOWN_DETECTOR_MUST_DETECT`, `_MARKDOWN_DETECTOR_MUST_IGNORE`, `_MARKDOWN_PREDICATE_CASES`), which are data under test rather than paths the suite opens. Two things keep the exemption from becoming a hole of its own: it is keyed on module-level assignments to those exact names, so a local of the same name cannot launder a documentation path through it; and it covers only constants reachable through *literal containers* (tuples, lists, sets, dicts), so an executable expression inside a fixture assignment — `(Path("ROADMAP.md").read_text() and "duel.html", False)` — is not exempt merely for sitting under a fixture's name. **Keep these tables pure literal data**, and keep every Markdown literal in this suite inside one of them.

`path_discovery.detect_repository_root()` remains the canonical documentation-root lookup for any *focused* tooling that genuinely needs a full source checkout; it is simply not something the standard architecture suite depends on.

Test path discovery is centralized in `tests/path_discovery.py` and must support every documented layout:

* the flat repository checkout (`<repo>/duel.html`, `<repo>/AGENTS.md`); and
* the deployed nested application layout (`<app>/games/duel/` with `<app>/templates/duel.html`).

Rules:

* Resolve the template with the canonical detector (`_detect_duel_html_path()` / `path_discovery.detect_duel_html_path()`); never hardcode `Path(__file__).resolve().parents[N] / "duel.html"`.
* Resolve repository documentation with `path_discovery.detect_repository_root()`; never derive it from the template directory (`_detect_duel_html_path().parent / "AGENTS.md"` is prohibited), and never copy documentation into `templates/`. Root discovery offers at most two roots — the package root first (a flat checkout is its own repository root, wherever it happens to be cloned), then the application root two levels above a `games/duel` package as a fallback — and raises rather than searching further up. Artifacts decide the layout: whichever candidate actually carries the documentation wins, directory names never override that, the intermediate `games/` directory is never a candidate, and architecture validation can never silently validate an unrelated checkout further up the tree.
* All discovery is anchored on `Path(__file__).resolve()`, never on the invocation directory. Running `python games/duel/tests/run_regression.py` from the application root and `python run_regression.py` from inside `games/duel/tests/` must resolve identically.
* Static suites that read source **text** must resolve engine modules and content modules through different hints, because the nested layout splits them: `resolver.py` / `effects.py` / `pet_ai.py` live in `<package>/engine/`, while `abilities.py` / `items.py` / `pets.py` live in `<package>/content/`. In `tests/architecture_guardrail_suite.py` this is `_gameplay_file()` for engine modules and `_content_file()` for content data; both fall back to the flat `<package>/<module>.py` layout. Resolving a content module through the engine hint works in a flat checkout and silently fails in the nested one. Candidates are selected with `.is_file()`, never `.exists()`, so a directory named `abilities.py` cannot shadow the real module, and an unresolved module is reported by its exact logical name and searched paths rather than as a combined "one of these is missing" message. `_run_source_resolution_self_tests()` pins all of this against synthetic flat and nested trees in temporary directories.

`guardrail_test_path_discovery_contract` enforces these rules statically over the test tree. Its Markdown check is AST-based and rejects *every* executable `.md` / `.markdown` path literal (case-insensitive, any filename or directory) inside `tests/regression/`, however the literal is consumed — `Path(...)`, `/` composition, `open()`, `.read_text()`, `.read_bytes()`, or any helper/loader call. Comments and module/class/function docstrings that merely discuss Markdown are ignored, and `guardrail_markdown_reference_detector_self_test` pins that detector's behavior with synthetic sources.

## Guardrail expectations

Future cleanup should add guardrail tests for risky patterns, especially direct resource mutation.

Examples of suspicious patterns in gameplay code:

* `.res.mp = min(`
* `.res.energy = min(`
* `.res.rage = min(`
* direct resource restoration outside `grant_player_resource()`
* damage-based resource gain before final damage application
* queued raw damage counted as final damage before `apply_damage()`

These patterns may be valid in setup, tests, or tightly documented exceptions, but should be reviewed carefully.

## Comments and architecture notes

When modifying complex engine code, add short high-signal comments explaining non-obvious invariants.

Add comments for:

* turn-resolution ordering assumptions
* snapshot-vs-live state decisions
* effect lifecycle rules
* damage/cost/resource modifier pipelines
* redirect, pet, AoE, or passive-proc edge cases
* places where a future contributor may accidentally reintroduce a bug

Do not add comments that merely restate syntax.

Good:

```python
# Use the action-start Challenger snapshot here. Paying the action cost may
# drop the actor into Wrath, but same-action gains must not change mode mid-cast.
```

Bad:

```python
# Add 1 to x.
x += 1
```

## Documentation maintenance

The active documentation set has distinct, non-overlapping roles:

* `ROADMAP.md` is the authoritative current planning document (active phase, class status, delivery rules).
* `CLASS_IMPLEMENTATION.md` is the reusable specification template and definition-of-done checklist for a new class.
* `AGENTS.md` is the authoritative architecture and implementation contract.
* `README.md` is project-facing and must not contain detailed engine internals.
* `duel.html` remains the player-facing source for ability and gameplay documentation.

Keep them in sync:

* When a class begins or completes, update `ROADMAP.md` in the same PR.
* When a new reusable architecture contract is introduced, update `AGENTS.md` in the same PR.

## Change summaries

When returning a completed change, summarize:

1. Files changed.
2. Gameplay behavior changed, if any.
3. Tests added or updated.
4. Commands run and results.
5. Any intentional exceptions or follow-up risks.
