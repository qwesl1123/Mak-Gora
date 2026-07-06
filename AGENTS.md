# Mak'Gora agent instructions

## Project architecture
Mak'Gora is a mature Python turn-based PvP engine with Flask/Socket.IO frontend integration.

Core files:
- `abilities.py`: ability data. Prefer data-driven ability definitions.
- `effects.py`: effect templates, item passive helpers, mitigation/resource/passive helpers.
- `resolver.py`: turn-resolution pipeline. Keep generic. Avoid per-ability hacks.
- `items.py`: item definitions and passive data.
- `tests/regression_suite.py`: deterministic regression coverage.
- `duel.html`: frontend UI and static docs.

## Coding rules
- This is not a rewrite.
- Keep diffs tight and targeted.
- Preserve existing turn timing, duration rules, cooldown semantics, and deterministic behavior.
- Prefer data-driven behavior over hardcoded resolver branches.
- Do not change balance numbers unless the task explicitly asks.
- Do not perform unrelated formatting/refactors.

## Resolver/effects boundaries
- `resolver.py` coordinates turn order and action resolution.
- `effects.py` owns reusable effect/passive/mitigation/resource helpers.
- Avoid ability-specific hacks in `resolver.py`.
- If logic can be represented as ability/effect/item data, prefer data.

## Comments / architecture notes
When modifying complex engine code, add short high-signal comments explaining non-obvious invariants.

Add comments for:
- turn-resolution ordering assumptions
- snapshot-vs-live state decisions
- effect lifecycle rules
- damage/cost/resource modifier pipelines
- redirect, pet, AoE, or passive-proc edge cases
- places where a future contributor may accidentally reintroduce a bug

Do not add comments that merely restate syntax.

Good:
```python
# Use the action-start Challenger snapshot here. Paying the action cost may
# drop the actor into Wrath, but same-action gains must not change mode mid-cast.
