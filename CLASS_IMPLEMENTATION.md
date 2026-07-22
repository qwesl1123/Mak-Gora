# New Class Implementation Specification

This is a reusable specification and definition-of-done template for adding a class to Mak'Gora. Its job is to surface missing design information **before** any code is written.

How to use it:

- Copy this template into a class-specific issue, design document, or PR plan — do not fill it in here.
- Resolve every load-bearing question before implementation begins.
- Do not invent unspecified values; ask for the design decision instead.
- Mark non-applicable fields explicitly as `N/A`.
- Keep design decisions separate from implementation notes.

See [`AGENTS.md`](AGENTS.md) for the binding architecture contracts and the class delivery rules, and [`ROADMAP.md`](ROADMAP.md) for the current phase and status table.

---

## 1. Identity

- **Class ID:**
- **Display name:**
- **Class color:**
- **Intended playstyle:**
- **Strengths:**
- **Weaknesses:**
- **Comparable existing class/mechanics:**

## 2. Base stats

| Field | Value |
|---|---|
| HP | |
| Attack (`atk`) | |
| Intellect (`int`) | |
| Defense (`def`) | |
| Speed (`spd`) | |
| Crit (`crit`) | |
| Accuracy (`acc`) | |
| Evasion (`eva`) | |
| Spirit (`spirit`) | |

- **Justification / relationship to existing classes:**

## 3. Resources

- **Resource name:**
- **Currently supported by the engine (Mana / Energy / Rage)?**
- **Starting value:**
- **Maximum value:**
- **Passive generation:**
- **Action-generated gains:**
- **Costs:**
- **End-of-turn behavior:**
- **Cap behavior:**
- **UI label and color:**
- **Snapshot fields:**
- **Foundation PR required?**

Resource decision (pick exactly one):

- [ ] Existing resource system reused with user approval
- [ ] New resource foundation required
- [ ] No gameplay resource

## 4. Class-wide passive and signature mechanics

Repeat this block for each mechanic:

- **ID:**
- **Name:**
- **Trigger:**
- **Formula:**
- **Timing / resolution layer:**
- **Duration / stacks:**
- **Resource interaction:**
- **Damage / healing interaction:**
- **Dispel behavior:**
- **Logging:**
- **UI display:**
- **Edge cases:**

## 5. Ability specification

Repeat this block for **every** ability:

- **Ability ID:**
- **Display name:**
- **Role / type:**
- **Tags:**
- **Target mode:**
- **Cost:**
- **Cooldown:**
- **School:**
- **Subschool:**
- **Scaling:**
- **Dice:**
- **Hit behavior:**
- **Crit behavior:**
- **Damage instances:**
- **Healing:**
- **Absorb:**
- **Buff / debuff:**
- **Duration:**
- **Stacks:**
- **Dispellability:**
- **Effect tags:**
- **Resolution layer:**
- **Empowered variant:**
- **Resource gain:**
- **Pet / summon interaction:**
- **Log text:**
- **Tooltip / documentation text:**

## 6. Interaction matrix

Provide an explicit entry for each:

- **Mindgames — damage-to-healing:**
- **Mindgames — healing-to-damage:**
- **Cyclone:**
- **Immunity (all):**
- **Physical immunity:**
- **Magical immunity:**
- **Absorbs:**
- **Damage reduction:**
- **Redirects:**
- **Stealth / untargetable:**
- **Crowd-control denial:**
- **Dispels:**
- **Break-on-damage effects:**
- **Pets / totems / summons:**
- **AoE:**
- **Temporary negative HP:**
- **Double KO / winner timing (where relevant):**

## 7. Pets, summons, or forms

Mark this whole section `N/A` if the class has none.

- **Entity type:**
- **Owner:**
- **Stats / resources:**
- **Duration:**
- **Action timing:**
- **AI:**
- **Attacks:**
- **Healing:**
- **Owner modifiers:**
- **Death / cleanup:**
- **Display:**
- **Accounting attribution:**

## 8. Required engine foundations

Review each requirement explicitly. If a foundation PR is required, it must land before the class PR.

| Requirement | Already supported | Reusable change needed | Foundation PR required | Notes |
|---|---:|---:|---:|---|
| Resource model | | | | |
| Target modes | | | | |
| Damage source kind | | | | |
| Effect tags | | | | |
| Resolution layers | | | | |
| Costs | | | | |
| Cooldowns | | | | |
| Pets | | | | |
| Snapshots | | | | |
| UI / resource bars | | | | |

## 9. Required file changes

Check each file this class will touch; mark unused files `N/A`.

- [ ] classes.py
- [ ] abilities.py
- [ ] effects.py
- [ ] pets.py
- [ ] pet_ai.py
- [ ] models.py
- [ ] resolver.py
- [ ] sockets.py
- [ ] duel.html
- [ ] tests/regression/*
- [ ] tests/regression/registry.py
- [ ] AGENTS.md
- [ ] ROADMAP.md

## 10. Regression plan

Cover every ability and every signature mechanic.

| Scenario | Ability/Mechanic | Setup | Expected result | Existing interaction protected |
|---|---|---|---|---|
| | | | | |

## 11. Documentation and UI checklist

- [ ] Class selection
- [ ] Class color
- [ ] Resource bar
- [ ] Ability buttons
- [ ] Tooltips
- [ ] In-game static docs (`duel.html`)
- [ ] Mouseover / status display
- [ ] Pet / form display
- [ ] Snapshot serialization
- [ ] Responsive layout (where applicable)

## 12. Unresolved questions

List every open design question here, numbered:

1.

**Implementation must not begin while this section contains unresolved load-bearing design questions.**

## 13. Definition of done

- [ ] Complete and selectable class
- [ ] No placeholders / TODO values
- [ ] All abilities functional
- [ ] All effects registered
- [ ] All canonical pipelines respected (costs, `grant_player_resource()`, `apply_player_healing()`, shared damage pipeline)
- [ ] Docs match backend behavior
- [ ] Deterministic regressions registered exactly once
- [ ] All five validation suites pass
- [ ] `ROADMAP.md` updated
- [ ] No unrelated changes
