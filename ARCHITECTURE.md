# Mak'Gora Architecture

Mak'Gora is a turn-based PvP duel engine with a Flask + Socket.IO browser frontend. The main design goal is to keep combat mechanics data-driven, deterministic, and easy to extend without turning the resolver into a pile of one-off special cases.

This document defines the expected structure of the codebase and the rules future contributors should follow when adding abilities, items, effects, pets, or new game modes.

---

## High-Level Runtime Flow

```text
app.py
  -> games.duel.init_duel(app, socketio)
      -> routes.py registers HTTP pages
      -> sockets.py registers Socket.IO handlers
          -> player prep / lock-in / submit action
          -> resolver.py resolves turns
              -> effects.py handles reusable effect mechanics
              -> pet_ai.py handles pet/totem turns
              -> models.py stores runtime state
              -> content/*.py provides data definitions
```

The frontend should send player choices and actions. The backend owns the source of truth for combat state, legality, random rolls, mitigation, resources, cooldowns, effects, pets, logs, and victory.

---

## Main Files and Responsibilities

### `app.py`

Application entrypoint.

Responsibilities:

* create the Flask app
* create the Socket.IO server
* initialize the duel module
* serve the home page

Do not put combat rules here.

---

### `routes.py`

HTTP route registration for duel pages.

Responsibilities:

* register Flask blueprints
* serve templates such as the duel page

Do not put combat rules here.

---

### `sockets.py`

Socket.IO transport layer between browser and engine.

Responsibilities:

* handle matchmaking/prep/combat socket events
* normalize user-facing payloads before passing them to the engine
* create UI snapshots from backend state
* format logs for display
* expose pets, resources, effects, items, cooldowns, and class state to the frontend

Allowed logic:

* UI packing
* input normalization
* socket room handling
* user-facing display formatting

Avoid:

* combat math
* hidden gameplay rules
* special-case ability/item behavior
* direct mutation of combat state unless it is socket/session state

If a mechanic affects combat, it belongs in `resolver.py`, `effects.py`, `pet_ai.py`, or a content file.

---

### `models.py`

Runtime state dataclasses.

Important state objects:

* `Resources`: HP, mana, energy, rage, max values, absorbs
* `PlayerBuild`: selected class and equipped items
* `PlayerState`: champion state, stats, effects, cooldowns, pets, active pet, pending pet command
* `PetState`: pet/totem state, resources, stats, effects, duration, action state
* `MatchState`: room, players, phase, turn, RNG seed, submitted actions, logs, winner, combat totals

Rules:

* Keep models simple.
* Models should describe state, not execute gameplay.
* Do not hide combat behavior inside dataclass methods unless the behavior is purely structural.

---

### `resolver.py`

Main turn-resolution pipeline.

Responsibilities:

* validate and normalize submitted actions
* resolve action order and targeting
* apply costs and cooldowns
* call the damage pipeline
* apply direct ability effects
* run post-damage reactions
* coordinate pet phases
* call end-of-turn systems
* update combat totals and victory state

Resolver design rule:

> The resolver coordinates generic stages. It should not become a warehouse of item-specific or ability-specific hacks.

Good resolver logic:

* generic targeting
* generic AoE handling
* generic damage stage
* generic absorb stage
* generic resource spend/gain calls
* generic cooldown handling
* generic effect application hooks

Bad resolver logic:

* `if ability_id == "some_new_spell"` when data or effects can express it
* item-specific damage/resource mutations
* duplicate mitigation/resource/cost formulas
* frontend-only display decisions

Some exceptions are acceptable for mechanics that are truly unique, but add the smallest generic hook possible first.

---

### `effects.py`

Reusable effect and passive mechanics.

Responsibilities:

* effect templates
* applying/removing effects
* effect tags
* stat modification
* mitigation helpers
* damage/passive multipliers
* resource gain multipliers
* absorbs
* immunity checks
* stealth checks
* DoT/HoT/end-of-turn effect handling
* item passive helpers

Effects design rule:

> Persistent mechanics should live in effect templates and shared helpers, not scattered through resolver branches.

Use `effects.py` for:

* a buff/debuff with duration
* a status effect with flags/tags
* a passive item effect
* mitigation/resource/damage modifiers
* effect lifecycle behavior
* effect panel payload generation

Do not duplicate effect logic in `resolver.py`, `pet_ai.py`, or `sockets.py`.

---

### `pet_ai.py`

Pet and totem behavior.

Responsibilities:

* pet/totem action selection
* pet/totem attack resolution
* pet/totem specials
* pet self-resource regeneration
* pet cleanup and phase preparation

Rules:

* Pet-owned resources are handled by pet logic.
* Player-owned resources granted by pets/totems must use the central player resource grant helper.
* Pet damage should use the same damage/mitigation helpers as champion damage where applicable.
* Do not bypass player passive systems when a pet/totem grants resources or damage on behalf of the owner.

Important distinction:

```text
Pet resource gain:
  pet.mp / pet.energy / pet.rage
  -> handled by pet resource logic

Player resource gain caused by pet/totem:
  owner.res.mp / owner.res.energy / owner.res.rage
  -> must use grant_player_resource()
```

---

### `abilities.py`

Data definitions for champion abilities.

Responsibilities:

* ability name
* class restriction
* costs
* cooldowns
* damage dice/scaling
* target mode
* school/subschool
* tags
* self effects
* target effects
* resource gain definitions
* special data consumed by generic resolver/effect hooks

Rules:

* Prefer data over code.
* Add new abilities here first.
* If an ability needs behavior that does not exist, add the smallest generic resolver/effect hook possible.
* Avoid adding ability-specific branches to the resolver unless the mechanic truly cannot be expressed generically.

---

### `items.py`

Data definitions for weapons, armor, and trinkets.

Responsibilities:

* item name
* slot
* stat modifiers
* class restrictions
* damage type
* passive definitions
* item color/fx metadata

Rules:

* Item stats and passive configuration live here.
* Passive execution should live in `effects.py` or a generic resolver hook.
* Do not implement item mechanics directly in `sockets.py`.
* Avoid item-specific resolver branches when a passive type can be implemented generically.

---

### `classes.py`

Class metadata.

Responsibilities:

* class IDs
* class display names
* class resource display
* class base stats/resources
* class normalization helpers

Rules:

* Use class IDs consistently.
* Do not hardcode class display names in resolver logic.
* Druid form-specific active resources should be handled through central active-resource helpers.

---

### `pets.py`

Pet and totem templates.

Responsibilities:

* pet/totem names
* entity type
* base HP/resources/stats
* basic attack profile
* special actions
* display metadata

Rules:

* Pet/totem definitions should be declarative where possible.
* Pet action behavior belongs in `pet_ai.py`.
* Do not put champion combat rules into pet templates.

---

### `balance.py`

Global balance constants.

Responsibilities:

* defaults
* caps
* shared numeric constants

Rules:

* Keep broad tuning constants here.
* Do not hide one-off ability behavior here.

---

### `rules.py` and `dice.py`

Small generic math helpers.

Responsibilities:

* dice rolling
* base damage formulas
* hit chance
* clamps and simple rule math

Rules:

* Keep these pure where possible.
* Do not import gameplay-heavy modules here.

---

### `duel.html`

Main duel UI template.

Responsibilities:

* player interface
* command documentation
* class/item/ability help text
* status panels
* resource bars
* client-side presentation

Rules:

* UI may document mechanics, but it must not define mechanics.
* If a backend mechanic changes, update the UI docs in the same PR.
* Do not rely on frontend checks for combat correctness.

---

### `tests/regression_suite.py`

Regression coverage for combat behavior.

Responsibilities:

* deterministic combat scenarios
* bug regression tests
* high-level behavior checks
* critical interaction tests

Rules:

* Every new mechanic needs at least one regression test.
* Every bug fix needs a test that would fail before the fix.
* Prefer deterministic tests over broad random simulations.
* Check final state and logs when logs are part of user-visible behavior.

---

## Core Architecture Principles

### 1. Data-Driven First

When adding a mechanic, ask:

```text
Can this be expressed in abilities.py, items.py, effects.py, or pets.py data?
```

Prefer data definitions plus generic hooks over hardcoded branches.

Good:

```python
"target_effects": [{"id": "stunned", "duration": 2}]
```

Bad:

```python
if ability_id == "kidney_shot":
    target.effects.append(...)
```

---

### 2. Resolver Coordinates; Effects Execute Reusable Mechanics

The resolver should define the turn pipeline. Effects should define reusable status/passive behavior.

Use resolver for:

* turn order
* legality
* target selection
* stage ordering
* applying costs/cooldowns
* calling shared helpers

Use effects for:

* damage/passive multipliers
* mitigation modifiers
* resource gain multipliers
* active resource detection
* effect tags
* immunity/stealth/absorb checks
* DoT/HoT/end-of-turn effect behavior

---

### 3. Central Helpers Must Own Shared Mechanics

Do not directly mutate core gameplay resources when a shared helper exists.

Required central paths:

* player damage: use the damage application pipeline
* player resource gains: use `grant_player_resource()`
* player resource costs: use cost adjustment/payment helpers
* mitigation: use mitigation helpers
* absorbs: use absorb helpers
* effects: use effect application/removal helpers
* pet phase behavior: use pet AI helpers

Direct mutation is allowed only inside the central helper responsible for that mutation.

---

## Player Resource Rules

Player resources are:

* `mp`
* `energy`
* `rage`

HP is not a gameplay resource for this rule. HP healing has separate logic because it interacts with healing, absorbs, death, and effects such as Mindgames.

### Player Resource Gains

Any mechanic that grants a player mana, energy, or rage must use:

```python
grant_player_resource(player, resource, amount, *, challenger_mode=_LIVE_CHALLENGER_MODE)
```

Expected behavior:

* applies `resource_gain_multiplier_from_passives()`
* caps at the matching max resource
* returns the actual amount gained after cap
* supports `mp`, `energy`, and `rage`
* ignores non-positive amounts
* does not grant HP
* does not mutate pet resources

Examples that must use this helper:

* baseline mana/energy/rage generation
* ability `resource_gain`
* ability `resource_gain_on_dealt`
* item-generated resource gains
* status-effect mana/energy/rage regeneration
* pet/totem effects that restore the owner’s resource
* future mechanics like mana gems, energy potions, rage refunds, resource drains that restore the caster

Do not do this in gameplay code:

```python
player.res.mp = min(player.res.mp + amount, player.res.mp_max)
player.res.energy += amount
player.res.rage += amount
```

Instead:

```python
actual = grant_player_resource(player, "mp", amount)
```

### Player Resource Costs

Player resource spending must use the existing cost helpers.

Expected behavior:

* cost modifiers apply centrally
* affordability checks match actual payment
* rounding rules stay consistent
* active-resource item passives are respected

Do not subtract resource costs manually from `player.res`.

---

## Pet Resource Rules

Pet resources are separate from player resources.

Pet self-resource regeneration may mutate pet resource fields through pet-specific helpers, because player item passives should not automatically modify pet-owned resources.

Allowed:

```python
pet.mp = min(pet.mp + mp_regen, pet.mp_max)
```

Not allowed:

```python
owner.res.mp = min(owner.res.mp + 10, owner.res.mp_max)
```

Use:

```python
actual = grant_player_resource(owner, "mp", 10)
```

---

## Damage Pipeline Rules

Damage should pass through the shared damage pipeline so the following systems stay consistent:

* hit/miss
* crits
* raw damage calculation
* outgoing damage modifiers
* incoming damage modifiers
* mitigation
* absorbs
* immunity
* redirect
* break-on-damage effects
* stealth breaking
* logs
* combat totals

Avoid direct HP subtraction outside the damage application stage.

Bad:

```python
target.res.hp -= damage
```

Good:

```python
apply_damage(actor, target, incoming, target_sid, source_name, school=school, subschool=subschool)
```

If a new mechanic needs unusual damage behavior, add a generic stage or parameter rather than bypassing the pipeline.

---

## Healing Rules

Healing should use central healing helpers when available.

Healing is separate from resource gain because it can interact with:

* max HP
* combat totals
* logs
* Mindgames-style conversion
* death checks
* future anti-healing or healing amplification effects

Do not mix HP healing into `grant_player_resource()`.

---

## Effect System Rules

Effects are runtime dictionaries created from templates.

Effect templates should define:

* `id`
* `name`
* `type`
* `category`
* `duration`
* `flags`
* `tags`
* `display`
* `modifiers`
* `regen`
* DoT/HoT metadata
* passive metadata where appropriate

Rules:

* Use `apply_effect_by_id()` or equivalent helpers.
* Use `remove_effect()` for removal.
* Use `refresh_dot_effect()` for DoT refresh behavior.
* Do not manually append ad-hoc effect dictionaries unless a helper is unavailable and the mechanic truly needs it.
* If a new effect should appear in the UI, add display metadata.
* If a new effect changes engine behavior, add tags or flags deliberately.

---

## Ability Tags vs Effect Tags

Ability tags describe the action being taken.

Examples:

* `spell`
* `attack`
* `physical`
* `magic`
* `defense`
* `control`
* `aoe`

Effect tags describe persistent state on an entity.

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

Do not mix them.

Correct:

```python
ability_has_tag(ability, "spell")
effect_has_tag(effect, "break_on_damage")
```

Avoid generic ambiguous helpers like:

```python
has_tag(obj, "spell")
```

The object type matters.

---

## School and Subschool Rules

Use `school` for broad runtime damage type:

* `physical`
* `magical`

Use `subschool` for magical identity:

* `fire`
* `frost`
* `nature`
* `arcane`
* `shadow`
* `holy`

Rules:

* Physical damage should not have a subschool.
* If `subschool` exists, `school` should be `magical`.
* `school` is the authoritative runtime field.
* Legacy `damage_type` can exist for compatibility, but new engine logic should prefer `school`.

Examples:

```python
"school": "magical",
"subschool": "fire"
```

```python
"school": "physical"
```

---

## Active Resource Rules

The active player resource depends on class and form.

Examples:

* Mage/Priest/Paladin/Warlock/Shaman/Hunter: mana
* Warrior: rage
* Rogue: energy
* Druid caster forms: mana
* Druid Bear Form: rage
* Druid Cat Form: energy

Use central helpers such as `active_resource_id()` instead of duplicating this mapping.

This matters for mechanics like Challenger's Chestplate, which modifies only the active resource.

---

## Item Passive Rules

Item passives should be generic passive types where possible.

Good:

```python
"passive": {
    "type": "resource_gain_multiplier",
    "resource": "rage",
    "multiplier": 1.25
}
```

Good:

```python
"passive": {
    "type": "challenger_resource_stance",
    "threshold": 0.5,
    "high_damage_multiplier": 1.10,
    "high_incoming_damage_multiplier": 0.90,
    "high_resource_cost_multiplier": 1.20,
    "low_damage_multiplier": 0.90,
    "low_incoming_damage_multiplier": 1.10,
    "low_resource_gain_multiplier": 1.30
}
```

Bad:

```python
if equipped_item == "challengers_chestplate":
    ...
```

Item-specific names may appear in UI/log formatting, but combat behavior should use passive types.

---

## Pets and Totems

Pets and totems are runtime entities attached to a player.

Rules:

* Pet IDs should be deterministic.
* Pet/totem turns should be resolved in stable order.
* Dead pets should not act.
* Pet effects should use the same effect helpers where possible.
* Pet/totem damage should respect mitigation and immunity.
* Pet/totem owner resource restores must use the player resource grant helper.
* Pet-owned resources must not be modified by player item passives unless explicitly designed.

---

## Frontend Snapshot Rules

The backend snapshot should provide everything the frontend needs to render state.

Snapshot may include:

* HP/resource values
* absorb layers
* active class/resource display
* equipped item names
* visible effects
* pet/totem state
* cooldown metadata
* combat totals
* logs

Frontend should not infer hidden gameplay rules. If the UI needs to show a mechanic, expose it from backend state or content metadata.

---

## Adding a New Ability

Checklist:

1. Add ability data to `abilities.py`.
2. Use existing generic fields first:

   * `cost`
   * `cooldown`
   * `dice`
   * `scaling`
   * `school`
   * `subschool`
   * `tags`
   * `target_mode`
   * `self_effects`
   * `target_effects`
   * `resource_gain`
   * `resource_gain_on_dealt`
3. Add or reuse effect templates in `effects.py` if the ability creates persistent state.
4. Add the smallest generic resolver/effect hook only if existing data cannot express the mechanic.
5. Update `duel.html` docs/help text.
6. Add regression tests.
7. Run the regression suite.

Do not add a resolver branch until data and generic hooks are insufficient.

---

## Adding a New Item

Checklist:

1. Add item data to `items.py`.
2. Use existing passive types if possible.
3. If a new passive type is needed, implement it in `effects.py` or a generic resolver hook.
4. Ensure passive behavior goes through central damage/resource/effect helpers.
5. Update UI docs/help text.
6. Add regression tests.
7. Run the regression suite.

For item resource effects:

* gains use `grant_player_resource()`
* costs use cost helpers
* damage uses damage multiplier/mitigation helpers

---

## Adding a New Effect

Checklist:

1. Add an effect template in `effects.py`.
2. Give it clear `category`, `flags`, `tags`, and `display` metadata.
3. Use existing lifecycle helpers.
4. Add helper logic only if the effect introduces genuinely new behavior.
5. Add regression tests for:

   * application
   * duration
   * expiration
   * stacking/refreshing if relevant
   * interaction with immunity/dispels/break-on-damage if relevant

---

## Adding a New Pet or Totem

Checklist:

1. Add template data to `pets.py`.
2. Reuse existing pet AI profiles where possible.
3. Add a pet special in `pet_ai.py` only if needed.
4. If the pet grants the owner mana/energy/rage, use `grant_player_resource()`.
5. If the pet damages enemies, route through the shared damage/mitigation path.
6. Add frontend display metadata if it should appear in War Council.
7. Add regression tests.

---

## Adding a New Game Mode

The current engine is built around 1v1 PvP, but future PvE modes should preserve the core duel pipeline where possible.

Preferred PvE direction:

* reuse `PlayerState`, `PetState`, and effect helpers
* reuse damage/resource/effect pipelines
* introduce enemy/boss entities carefully
* avoid rewriting resolver from scratch
* avoid making every encounter a special-case script

For dungeons/raids, prefer:

```text
one primary enemy + attached entities/mechanics
```

over:

```text
many unrelated actors with separate combat systems
```

until the entity model is mature enough.

---

## Testing Standards

Every mechanic change needs regression coverage.

A good regression test:

* has deterministic setup
* controls RNG where needed
* checks exact state changes
* checks important logs
* proves the bug would fail before the fix
* avoids depending on unrelated random behavior

Required test categories:

* new ability behavior
* new item passive behavior
* resource cost/gain behavior
* damage/mitigation behavior
* pet/totem behavior
* effect duration/expiration
* UI docs presence for user-facing commands/items
* bug regressions

---

## Common Anti-Patterns

Avoid these:

```python
target.res.hp -= damage
```

```python
player.res.mp += amount
```

```python
if item_id == "specific_item":
    ...
```

```python
if ability_id == "specific_ability":
    ...
```

```python
target.effects.append({...})
```

```python
# same formula copied into resolver.py, effects.py, and pet_ai.py
```

Prefer these:

```python
apply_damage(...)
```

```python
grant_player_resource(...)
```

```python
passive["type"] == "generic_passive_type"
```

```python
apply_effect_by_id(...)
```

```python
resource_gain_multiplier_from_passives(...)
```

```python
damage_multiplier_from_passives(...)
```

---

## Codex / AI Contributor Rules

When asking Codex or another AI tool to modify this repo, include these constraints:

```text
This is an existing mature Python turn-based PvP engine.
This is NOT a rewrite.
Keep the diff minimal.
Preserve deterministic behavior.
Prefer data-driven content over hardcoded branches.
Do not bloat resolver.py with one-off mechanics.
Use existing helpers for damage, effects, resources, costs, mitigation, absorbs, and pets.
If a helper does not exist, add one reusable helper instead of duplicating logic.
Update UI docs for user-facing commands/items.
Add regression tests for every behavior change.
Return files changed and tests run.
```

For new abilities/items, also say:

```text
If the requested mechanic cannot be implemented from existing data fields, add the smallest generic hook needed and explain why it belongs there.
```

---

## Review Checklist Before Merging

Before merging a PR, check:

* Does the change keep combat behavior server-authoritative?
* Does it use data definitions where possible?
* Does it avoid resolver bloat?
* Does it use central resource/damage/effect helpers?
* Does it avoid direct player resource mutation?
* Does it avoid direct HP damage mutation outside the damage pipeline?
* Does it preserve deterministic RNG?
* Does it update frontend docs if user-facing?
* Does it include regression tests?
* Does the test suite pass?

If any answer is no, request changes.
