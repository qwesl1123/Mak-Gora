# Mak'Gora Engine Architecture Notes

This document records cross-cutting rules that are easy to violate when adding
new mechanics. Keep it short and authoritative.

## Resource grants and costs

Player and pet resources are handled by **separate** systems. Do not mix them.

### Player mana / energy / rage GAINS

Any mechanic that grants a **player** mana, energy, or rage must go through the
central helper:

```python
grant_player_resource(
    player: PlayerState,
    resource: str,             # "mp" | "energy" | "rage"
    amount: int,
    *,
    challenger_mode=_LIVE_CHALLENGER_MODE,
) -> int                       # actual amount restored after the cap
```

Location: `effects.py`.

The helper is the single place that:

- applies `resource_gain_multiplier_from_passives` (so Challenger Wrath's
  low-resource bonus and item passives such as Rage Crystal are honoured),
- caps the gain at the player's resource max,
- returns the amount actually restored after the cap, and
- ignores non-positive amounts, HP, unknown resources, and a missing `player.res`.

`challenger_mode` mirrors `resource_gain_multiplier_from_passives`: the default
reads the live Challenger stance, `None` means "no Challenger modifier" (**not**
Wrath), and an explicit `"might"`/`"wrath"` pins a start-of-turn snapshot.

**Do NOT** directly mutate `player.res.mp`, `player.res.energy`, or
`player.res.rage` for gameplay resource gains. Log the value returned by the
helper so the combat log reflects the adjusted/capped amount. The resolver
exposes a thin `grant_resource` adapter that delegates to this helper — reuse it
inside the resolver rather than reimplementing the math.

### Player resource COSTS

Resource costs/spending must continue to go through the existing cost helpers
(`adjusted_resource_costs` / `can_pay_costs` / `consume_costs` in `resolver.py`),
which apply the Challenger active-resource surcharge. `grant_player_resource` is
for gains only; it must never be used to spend resources.

### Pet resources are separate

Pet-owned `mp`/`energy`/`rage` are governed by pet resource logic in `pet_ai.py`
(e.g. `apply_pet_resource_regen`, per-special pet costs). Pet self-regeneration
and pet costs must **not** be routed through `grant_player_resource` or the
player cost helpers. Only a pet/totem restoring **its owner's** resource is a
player gain and must use `grant_player_resource` (e.g. Shadowfiend and Mana Tide
Totem owner-mana restores).
