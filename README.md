# 🗑️ Trash Collector

A terminal-based idle mining game where you scavenge vintage hardware, build Bitcoin mining rigs from electronic junk, and get relentlessly guilt-tripped about your environmental impact.

> *"You have negated the annual CO₂ absorption of one full tree. A squirrel has filed a missing persons report."*

---

## What Is This

You're a dumpster-diving hardware hoarder. Every two hours you can scavenge 5 random pieces of vintage computer hardware — anything from a 1971 Intel 4004 to a 2024 RTX 4090. Assemble 5 parts into a mining rig, toggle it on, and start mining **El Virtual** (₿v), a completely made-up cryptocurrency with a fluctuating exchange rate.

The twist: the game tracks every kilowatt-hour your rigs consume and converts it into a running tally of ecological destruction — trees negated, rainforest hectares lost, panda habitat percentages, Arctic ice melted. Cross certain thresholds and you'll receive dramatic one-time milestone notifications. Greta Thunberg will eventually enter the chat.

The other twist: **not all hardware is worth plugging in.** An Antminer S19 draws 3,250 watts and will bankrupt you. A 1974 Intel 8080 running hand-optimised `bitcoin_sieve` firmware earns a few fractions of a credit per hour and quietly judges you for using GPUs.

---

## Features

- 🖥️ **1,500+ real hardware entries** across two CSV databases — CPUs, GPUs, DSPs, FPGAs, ASICs, and some truly unhinged items (Hamster Wheel Powered Compute Node, Citrus-Powered Mining Array, a VAX 11/780)
- ⚡ **Electricity actually matters** — power draw is a real cost; high-wattage junk will drain your credits faster than it earns them
- 🏆 **Rarity system** — Common through Mythic, with era bonuses rewarding vintage finds
- 📊 **Rich terminal UI** with colour, tables, and panels via `rich`
- ⌨️ **Tab autocomplete** for all commands via `prompt_toolkit`
- 🕐 **Live cooldown display** in the prompt itself
- 📋 **Status dashboard**, persistent event log, cross-session command history
- ⚠️ **Background electricity monitor** — warns you before your bill shuts you down
- 🌍 **14 escalating guilt milestones** — from *"one fewer dandelion, maybe"* to *"you are now a stratigraphic layer"*
- 💾 **Standalone .exe** — no Python required to play

---

## Scoring

Every part gets a compute score based on real hardware specs:

**Compute path** (CPUs, GPUs, DSPs, etc.):
```
score = clock_mhz × (word_bits / 8) × cores × type_multiplier × era_bonus
```

**Hashrate path** (ASICs, dedicated miners):
```
score = log₂(hashrate_mhs + 1) × 100 × type_multiplier × era_bonus
```

Era bonuses reward vintage hardware — a 1974 chip gets a 5× multiplier. Type multipliers reward GPUs (2.5×), TPUs (2.8×), and Datacenter cards (5×) over plain CPUs (1.0×).

Every chip scores at least something — thanks to `bitcoin_sieve`, hand-optimised firmware that Rick Sanchez theoretically wrote for every piece of silicon ever fabbed. Floors are based on transistor count, so a chip with more transistors always scores higher than one with fewer, even with missing spec data.

---

## Installation

### Option A — Run the .exe (Windows)
Download `Trash Collector V1.exe` and run it. A `standalone_data/` folder will appear next to the exe containing your save data.

### Option B — Run from source

**Requirements:** Python 3.10+

```bash
pip install rich prompt_toolkit
python standalone.py
```

The game will create a `standalone_data/` folder next to the script, or fall back to `~/.trash_collector/` if the directory isn't writable.

### Option C — Build your own exe

```bash
pip install pyinstaller
pyinstaller --onefile --add-data "trash.csv;." --add-data "trash2.csv;." --add-data "trash3..csv;." standalone.py
```

The exe will be in `dist/`. Save data lives next to the exe in `standalone_data/`.

---

## Commands

| Command | What it does |
|---|---|
| `scavenge` | Get 5 random hardware parts (2hr cooldown) |
| `parts` | View your current inventory |
| `build <name>` | Build a rig from 5 parts in inventory |
| `rig <name>` | Detailed view of a rig |
| `rigs` | Overview of all your rigs |
| `toggle <name>` | Turn a rig on or off |
| `toggle_all on\|off` | Toggle all rigs at once |
| `mine` | Collect mined El Virtual from running rigs (1hr cooldown) |
| `collect` | Alias for mine |
| `wallet` | Check your credits and ₿v balance |
| `market` | View the parts market |
| `buy <slot>` | Buy a part from the market |
| `sell <inv_id>` | Sell a part from inventory |
| `buy_btc <amount>` | Buy El Virtual with credits |
| `sell_btc <amount>` | Sell El Virtual for credits |
| `scrap <name>` | Dismantle a rig and return parts to inventory |
| `status` | Compact dashboard |
| `log` | Last 20 game events |
| `help` | Command reference |
| `quit` | Exit |

Tab autocomplete works on all commands and arguments (rig names, slot numbers, inventory IDs).

---

## Economics

- **El Virtual price** starts at 50,000 credits and fluctuates with each transaction
- **Electricity rate**: 0.10 credits per watt per hour
- **Mining rate**: 1 ₿v per 5,000,000 compute-score-hours
- **Starting credits**: 100

A modern GPU like an RTX 3060 will earn thousands of credits per hour. A 1985 CPU earns a few credits per hour. An Antminer S19 will lose you 272 credits per hour because it draws 3,250 watts and the scoring model doesn't care about real-world hashrate — it cares about general compute. Welcome to El Virtual.

---

## Save Data

Game state is stored in SQLite databases in `standalone_data/` (next to the exe/script) or `~/.trash_collector/` as a fallback:

- `mining.db` — hardware inventory, rigs, wallet, market, environmental ledger
- `social_credit.db` — credits balance
- `event_log.jsonl` — persistent game event history
- `milestones.json` — tracks which guilt milestones have fired
- `command_history` — terminal command history

Delete the folder to start fresh.

---

## The Guilt System

Every kWh your rigs consume is tracked lifetime. Cross a threshold and you get a one-time dramatic notification:

| Threshold | Milestone |
|---|---|
| 1 kWh | ⚡ First Blood |
| 1 tree negated | 🌳 Deforestation Begins |
| 1 rainforest hectare | 🌴 One Hectare Down |
| 20t CO₂ | 😤 Greta Has Entered The Chat |
| 100t CO₂ | 🐧 Legal Action (penguins have retained counsel) |
| 500t CO₂ | 🧊 Personal Glacier Melter |
| 5,000t CO₂ | ☢️ Geological Event |
| 50,000t CO₂ | 🕳️ The Sun Has Concerns |

Each milestone fires exactly once per save file.

---

## Origin

Originally a Discord bot game (`cogs/trash_collector.py`). The standalone version was refactored to remove all Discord dependencies while keeping the Discord bot fully functional — they share the same SQLite databases and game logic via `game_engine.py`.

---

## License

Do whatever you want with it. If you build something cool, let me know.

---

*No actual rainforests were harmed. Probably.*
