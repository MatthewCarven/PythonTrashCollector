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
- TYPE_SCORE_BOOST: DATACENTER=1_000_000, ARRAY=1_000_000 (applied after log2 formula)
- Compute path: clock * (bits/8) * cores * tmult * era_bonus
- Hashrate path: log2(hashrate_mhs + 1) * 100 * tmult * era_bonus * boost
- Era bonus: pre-1975=5.0, 1975-84=4.0, 1985-94=3.0, 1995-04=2.0, 2005-14=1.5, 2015+=1.0

**Known scoring problems (not yet fixed):**
- log2 compression means all DATACENTER/ARRAY items cluster in a ~2x score band regardless of hashrate spread (e.g. Mega-Rack S19 at 20 PH/s scores barely more than Backyard Pod at 0.45 PH/s)
- 80386DX and 80486DX both at 33MHz score identically (396) — 486 should score higher due to IPC advantage (~1.5x clock-for-clock)
- 4 cloud datacenter items (Ashburn, Oregon, Sydney, Frankfurt) have hashrate=0 and score zero
- DATACENTER type multiplier (5.0) means a small datacenter can outscore a large ARRAY (3.5) unfairly

**Agreed next steps for CSV work:**
- Matthew will use Perplexity to research real-world hashrate data (EH/s or PH/s) for the actual facilities in trash2.csv — operators like Foundry USA, Hut 8, Bitfarms publish quarterly reports
- Also source real SHA-256 / GPU mining benchmark hashrates (MH/s, GH/s) for CPU/GPU items in trash.csv — sites like whattomine.com, old mining forums
- Fix the four zero-hashrate cloud DCs with plausible values
- Fix 486 IPC advantage in trash.csv (bump effective clock ~1.5x to reflect real-world perf vs 386)
- Consider changing hashrate score formula from log2 to sqrt(hashrate_in_PH) for better spread

**Display improvements already made (standalone.py):**
- fmt_score(n): formats scores with K/M/B/T/Q suffixes (e.g. 16.10B)
- fmt_watts(w): formats power as W/kW/MW/GW (e.g. 20.00 MW)
- Applied everywhere scores and TDP appear in the UI
- TDP table columns widened to width=10 to fit "20.00 MW" style strings