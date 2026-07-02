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

## Have Fun

Grab a friend, jump into the queue, and see who's the better duelist. May the RNG gods be kind.
