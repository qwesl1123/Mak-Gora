# Mak'Gora agent instructions

## Project architecture

Mak'Gora is a mature Python turn-based PvP engine with Flask/Socket.IO frontend integration.

This repository is not a rewrite target. Treat the current engine as production gameplay code. Prefer small, targeted, data-driven changes that preserve existing timing and deterministic behavior.

Core files:

* `abilities.py`: ability data. Prefer data-driven ability definitions.
* `effects.py`: effect templates, item passive helpers, mitigation/resource/passive helpers.
* `resolver.py`: turn-resolution pipeline. Keep generic. Avoid per-ability hacks.
* `items.py`: item definitions and item passive data.
* `pet_ai.py`: pet/totem action behavior and pet resource handling.
* `pets.py`: pet/totem definitions.
* `tests/regression_suite.py`: deterministic gameplay regression coverage.
* `tests/subschool_validation_suite.py`: school/subschool metadata validation.
* `duel.html`: frontend UI and static docs.

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

If the user request does not provide enough information for a new item, ability, or mechanic, ask for the missing design details before inventing balance or behavior.

## Resolver/effects boundaries

`resolver.py` coordinates turn order, action resolution, targeting, redirects, damage application, resource spending, and final combat logs.

`effects.py` owns reusable effect/passive/mitigation/resource helpers.

Avoid ability-specific hacks in `resolver.py`.

If behavior can be represented as ability/effect/item/pet data, prefer data.

If a helper is reusable across multiple abilities/items/effects, put it in `effects.py` or another focused helper module instead of embedding it inside one resolver branch.

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

Run relevant tests after changes:

```bash
python tests/run_regression.py
python tests/run_subschool_validation.py
```

If only one area changed, run the targeted test first, then the full relevant suite.

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

## Change summaries

When returning a completed change, summarize:

1. Files changed.
2. Gameplay behavior changed, if any.
3. Tests added or updated.
4. Commands run and results.
5. Any intentional exceptions or follow-up risks.
