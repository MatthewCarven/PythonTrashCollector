---
name: Trash Collector project context
description: Key context about the Trash Collector game project, planned features, and current technical state
type: project
---

A Python terminal idle mining game where you scavenge vintage e-waste, build mining rigs, and mine a fictional cryptocurrency called El Virtual (₿v). Originally a Discord bot, now a standalone terminal app built with rich and prompt_toolkit, compiled to .exe with PyInstaller.

Current versions: V1, V101, V102 in the project folder.
Key files: game_engine.py (core logic), standalone.py (terminal UI), trash.csv (CPU/GPU/etc hardware), trash2.csv (DATACENTER/ARRAY items).

---

**Planned features discussed:**
- `scrap` command: Scrap a rig or individual part for materials. Value based on real ewaste economics — aluminium heatsinks (scales with wattage/TDP), copper heatpipes, gold connector plating (scales with pin count/rarity). Higher wattage parts = bigger heatsinks = more aluminium value.
- `talkie_toaster` rig: A joke rig named after Red Dwarf's Talkie Toaster, built around the IoT Smart Toaster Controller part. Reserved — Matthew is saving the toaster part for it.
- **"Trash Collector Professional"** — a planned future version/edition of the game. Name reserved, no details yet.

**Why:** Keep these names/ideas reserved and don't suggest them as new ideas — they're already Matthew's plans.

---

**Current scoring system (game_engine.py):**
- TYPE_MULTIPLIERS: CPU=1.0, GPU=2.5, DATACENTER=5.0, ARRAY=3.5, TPU=2.8, NPU=2.2, ASIC=2.0, etc.
- Compute path: clock × (bits/8) × cores × type_multiplier × era_bonus
- Hashrate path: sqrt(hashrate_MH/s / 1,000,000) × 2000 × type_multiplier × era_bonus
- Transistor density bonus: logarithmic up to 3.5× cap (50B transistor ceiling)
- Era bonus: pre-1975=5.0, 1975-84=4.0, 1985-94=3.0, 1995-04=2.0, 2005-14=1.5, 2015+=1.0

**Scoring problems resolved (all traced to fields populated with 0s instead of real-world data):**
- ✅ Switched from log2 to sqrt formula — DATACENTER/ARRAY items now spread properly
- ✅ Fixed 80486DX IPC advantage — effective clock bumped ~1.5× vs 386
- ✅ Fixed 4 zero-hashrate cloud datacenters (Ashburn, Oregon, Sydney, Frankfurt)
- ✅ Added transistor density bonus (up to 3.5× multiplier)
- ✅ Looked up missing transistor counts for 13 console/mobile chips
- ✅ Fixed BM1397/BM1398 chip naming on S19 Pro row

**Remaining CSV work:**
- Research real SHA-256 hashrates for CPU/GPU items and quarterly mining data for datacenter/array items (using Perplexity — sources: whattomine.com, mining forums, operator reports from Foundry USA, Hut 8, Bitfarms)

**Display improvements already made (standalone.py):**
- fmt_score(n): formats scores with K/M/B/T/Q suffixes (e.g. 16.10B)
- fmt_watts(w): formats power as W/kW/MW/GW (e.g. 20.00 MW)
- Applied everywhere scores and TDP appear in the UI
- TDP table columns widened to width=10 to fit "20.00 MW" style strings