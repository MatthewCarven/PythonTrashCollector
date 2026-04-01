"""
TRASH COLLECTOR - Standalone Game Engine
=========================================
Pure game logic extracted from the Discord cog.
No Discord dependencies — just game mechanics, math, and database ops.
"""

import random
import math
import time
import csv
import os
from mining_db import MiningDB
from database import CreditDB


# =============================================================================
# TYPE MULTIPLIERS
# =============================================================================
TYPE_MULTIPLIERS = {
    "CPU":         1.0,
    "GPU":         2.5,
    "FPU":         1.5,
    "DSP":         1.3,
    "DSC":         1.3,
    "MCU":         0.6,
    "APU":         2.0,
    "CUSTOM":      1.8,
    "COPROCESSOR": 1.2,
    "FPGA":        1.4,
    "NPU":         2.2,
    "TPU":         2.8,
    "ASIC":        2.0,
    "SOC":         1.7,
    "DATACENTER":  5.0,
    "ARRAY":       3.5,
}

# Extra multipliers for types whose scores are derived from hashrate/floor formulas
# that naturally produce tiny numbers compared to the compute path used by GPUs.
# A datacenter facility or ASIC array represents thousands of chips working in parallel
# and deserves to dominate the scoreboard accordingly.
TYPE_SCORE_BOOST = {
    "DATACENTER": 1,
    "ARRAY":      1,
}

# =============================================================================
# ERA BONUS
# =============================================================================
def era_bonus(year: int) -> float:
    if year < 1975:   return 5.0
    elif year < 1985: return 4.0
    elif year < 1995: return 3.0
    elif year < 2005: return 2.0
    elif year < 2015: return 1.5
    else:             return 1.0


def compute_score(hw: dict) -> float:
    hw_type = hw.get("type", "CPU")
    year    = hw.get("year", 2000)
    try:    year = int(year)
    except: year = 2000
    tmult = TYPE_MULTIPLIERS.get(hw_type, 1.0)
    eb    = era_bonus(year)

    # --- hashrate path (ASICs, dedicated miners) ---
    try:    hashrate = float(hw.get("hashrate_mhs") or 0)
    except: hashrate = 0.0

    # --- compute path (CPUs, GPUs, DSPs, etc.) ---
    # Defensive: some CSV rows have empty strings for numeric fields; treat as 0.
    try:    clock = float(hw.get("clock_mhz") or 1)
    except: clock = 1.0
    try:    bits  = float(hw.get("word_bits")  or 1)
    except: bits  = 1.0
    try:    cores = float(hw.get("cores")      or 1)
    except: cores = 1.0

    if hashrate:
        # sqrt formula: convert MH/s → TH/s first, then sqrt, scale by 2000.
        # This gives ~45x spread across the datacenter range vs log2's ~2x,
        # so a 20 PH/s Mega-Rack actually dominates a 20 TH/s backyard shed.
        raw_score = math.sqrt(hashrate / 1_000_000) * 2000 * tmult * eb
    else:
        raw_score = clock * ((bits or 8) / 8) * (cores or 1) * tmult * eb

    # --- transistor density bonus ---
    # More silicon = more IPC, more parallelism, more everything.
    # Soft multiplier capped at 3.5x (2.5 range above 1.0 base).
    # log scale so billions of transistors feel meaningful but don't
    # completely detach from clock/core reality.
    # 50B transistors (A100-class) hits the cap. 275K (386) barely moves.
    try:    transistors = float(hw.get("transistors") or 0)
    except: transistors = 0.0

    if transistors > 0:
        density_bonus = 1.0 + (math.log2(transistors) / math.log2(50_000_000_000)) * 2.5
        density_bonus = min(density_bonus, 3.5)  # hard cap
    else:
        density_bonus = 1.0

    raw_score *= density_bonus

    # --- floor: no piece of silicon ever scores absolute zero ---
    floor = math.log2(transistors + 2) * tmult * eb * 0.001

    score = max(raw_score, floor)
    boost = TYPE_SCORE_BOOST.get(hw_type, 1)
    return round(score * boost, 4)


# =============================================================================
# HARDWARE DATABASE (loaded from CSV)
# =============================================================================
_INT_FIELDS   = {"year", "word_bits", "cores", "process_nm", "transistors"}
_FLOAT_FIELDS = {"clock_mhz", "tdp_watts"}

# Hashrate unit multipliers → normalise everything to MH/s
_HASHRATE_UNITS = {
    "eh/s": 1_000_000_000_000.0,
    "ph/s": 1_000_000_000.0,
    "th/s": 1_000_000.0,
    "gh/s": 1_000.0,
    "mh/s": 1.0,
    "kh/s": 0.001,
    "h/s":  0.000001,
    # alternate spellings
    "th/h": 1_000_000.0,
    "gh/h": 1_000.0,
    "mh/h": 1.0,
}


def _parse_hashrate(val) -> float:
    """Parse a hashrate string of any denomination and return MH/s float."""
    if val is None:
        return 0.0
    s = str(val).strip().lower().replace(",", "")
    if not s:
        return 0.0
    # Try bare float first (already in MH/s from old CSVs)
    try:
        return float(s)
    except ValueError:
        pass
    for unit, mult in _HASHRATE_UNITS.items():
        if unit in s:
            number_part = s.replace(unit, "").strip()
            # Handle ranges like "5–10" — take the midpoint
            if "–" in number_part or "-" in number_part:
                parts = number_part.replace("–", "-").split("-")
                try:
                    nums = [float(p.strip().lstrip("~≈")) for p in parts if p.strip()]
                    return (sum(nums) / len(nums)) * mult
                except ValueError:
                    pass
            try:
                return float(number_part.lstrip("~≈")) * mult
            except ValueError:
                pass
    return 0.0


def _parse_numeric(val) -> float:
    """
    Robustly parse messy numeric strings from mixed-source CSVs.
    Handles: tildes, commas, ranges (take midpoint), word suffixes
    like 'million'/'billion', trailing unit labels like 'W' or 'nm'.
    """
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    # Strip leading noise characters
    s = s.lstrip("~≈<>").strip()
    # Strip trailing unit labels (W, nm, MHz, etc.) but keep digits and separators
    import re
    # Handle "million" / "billion" word multipliers
    mult = 1.0
    sl = s.lower()
    if "billion" in sl:
        mult = 1_000_000_000.0
        s = re.sub(r"billion", "", s, flags=re.IGNORECASE).strip()
    elif "million" in sl:
        mult = 1_000_000.0
        s = re.sub(r"million", "", s, flags=re.IGNORECASE).strip()
    # Strip anything that isn't a digit, dot, comma, space, dash, or en-dash
    s = re.sub(r"[^\d.,\-– ]", " ", s).strip()
    # Collapse spaces used as thousands separators (e.g. "10 000" -> "10000")
    # Only collapse when the right-hand group is exactly 3 digits (real thousands grouping)
    s = re.sub(r"(\d)\s+(\d{3})(?!\d)", r"\1\2", s)
    s = s.replace(",", "")
    # Handle ranges — take midpoint
    for sep in ("–", "-"):
        if sep in s:
            parts = s.split(sep)
            try:
                nums = [float(p.strip()) for p in parts if p.strip()]
                if nums:
                    return (sum(nums) / len(nums)) * mult
            except ValueError:
                pass
    try:
        return float(s.split()[0]) * mult
    except (ValueError, IndexError):
        return 0.0


def _load_hardware_csv(filename="trash.csv"):
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, filename),
        os.path.join(here, "cogs", filename),
        os.path.join(here, "..", filename),
    ]
    for path in candidates:
        if os.path.isfile(path):
            break
    else:
        raise FileNotFoundError(f"Cannot find {filename} in {candidates}")

    entries = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            for k in _INT_FIELDS:
                if k in row and row[k]:
                    try:
                        row[k] = int(_parse_numeric(row[k]))
                    except (ValueError, TypeError):
                        row[k] = 0
            for k in _FLOAT_FIELDS:
                if k in row and row[k]:
                    try:
                        row[k] = _parse_numeric(row[k])
                    except (ValueError, TypeError):
                        row[k] = 0.0
            # Hashrate gets its own unit-aware parser
            if "hashrate_mhs" in row:
                row["hashrate_mhs"] = _parse_hashrate(row.get("hashrate_mhs"))
            entries.append(row)
    return entries


HARDWARE_DB = _load_hardware_csv("trash.csv") + _load_hardware_csv("trash2.csv")
HARDWARE_LOOKUP = {hw["id"]: hw for hw in HARDWARE_DB}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def random_find() -> dict:
    return random.choice(HARDWARE_DB)

def random_finds(n: int = 5) -> list:
    return random.choices(HARDWARE_DB, k=n)


# =============================================================================
# ENVIRONMENTAL DESTRUCTION ENGINE
# =============================================================================
CO2_GRAMS_PER_KWH = 475.0
KG_CO2_PER_TREE_PER_YEAR = 22.0
TONNES_CO2_PER_HECTARE_YEAR = 7.6
HECTARES_PER_SOCCER_FIELD = 0.714
SQ_KM_PER_HECTARE = 0.01
PANDA_HABITAT_SQ_KM = 5900.0
ARCTIC_ICE_VOLUME_KM3 = 16500.0
KG_CO2_PER_KM3_ICE_MELT = 3.3e12
HOURS_PER_YEAR = 8760.0
GRAMS_PER_KG = 1000.0
KG_PER_TONNE = 1000.0


def rig_total_watts(parts: list) -> float:
    return sum(hw.get("tdp_watts", 0) for hw in parts)

def annual_kwh(total_watts: float) -> float:
    return (total_watts / 1000.0) * HOURS_PER_YEAR

def annual_co2_kg(total_watts: float) -> float:
    return (annual_kwh(total_watts) * CO2_GRAMS_PER_KWH) / GRAMS_PER_KG

def annual_co2_tonnes(total_watts: float) -> float:
    return annual_co2_kg(total_watts) / KG_PER_TONNE

def trees_destroyed_equivalent(total_watts: float) -> float:
    return annual_co2_kg(total_watts) / KG_CO2_PER_TREE_PER_YEAR

def rainforest_hectares_destroyed(total_watts: float) -> float:
    return annual_co2_tonnes(total_watts) / TONNES_CO2_PER_HECTARE_YEAR

def soccer_fields_destroyed(total_watts: float) -> float:
    return rainforest_hectares_destroyed(total_watts) / HECTARES_PER_SOCCER_FIELD

def panda_habitat_percentage(total_watts: float) -> float:
    hectares = rainforest_hectares_destroyed(total_watts)
    sq_km = hectares * SQ_KM_PER_HECTARE
    return (sq_km / PANDA_HABITAT_SQ_KM) * 100.0

def arctic_ice_equivalent_m3(total_watts: float) -> float:
    co2_kg = annual_co2_kg(total_watts)
    km3 = co2_kg / KG_CO2_PER_KM3_ICE_MELT
    return km3 * 1e9

def electricity_cost_annual(total_watts: float, price_per_kwh: float = 0.12) -> float:
    return annual_kwh(total_watts) * price_per_kwh


def guilt_rating_co2(co2_tonnes: float) -> str:
    if co2_tonnes < 0.001:     return "PRISTINE - A butterfly thanks you"
    elif co2_tonnes < 0.01:    return "NEGLIGIBLE - One fewer dandelion, maybe"
    elif co2_tonnes < 0.1:     return "MINOR - A small shrub frowns at you"
    elif co2_tonnes < 1.0:     return "MODERATE - Several trees are disappointed"
    elif co2_tonnes < 5.0:     return "NOTABLE - A forest ranger files a report"
    elif co2_tonnes < 20.0:    return "SIGNIFICANT - Visible from a weather satellite"
    elif co2_tonnes < 100.0:   return "SEVERE - Greta Thunberg has entered the chat"
    elif co2_tonnes < 500.0:   return "CATASTROPHIC - Penguins are filing a class-action lawsuit"
    elif co2_tonnes < 5000.0:  return "APOCALYPTIC - You are personally melting a glacier"
    elif co2_tonnes < 50000.0: return "EXTINCTION-LEVEL - Congrats, you're a geological event"
    else:                      return "COSMIC HORROR - The Sun asks you to tone it down"


def env_from_kwh(kwh: float) -> dict:
    co2_kg = (kwh * CO2_GRAMS_PER_KWH) / GRAMS_PER_KG
    co2_tonnes = co2_kg / KG_PER_TONNE
    rainforest = co2_tonnes / TONNES_CO2_PER_HECTARE_YEAR
    return {
        "kwh": round(kwh, 2),
        "co2_kg": round(co2_kg, 2),
        "co2_tonnes": round(co2_tonnes, 6),
        "trees_negated": round(co2_kg / KG_CO2_PER_TREE_PER_YEAR, 2),
        "rainforest_hectares": round(rainforest, 6),
        "soccer_fields": round(rainforest / HECTARES_PER_SOCCER_FIELD, 6),
        "panda_habitat_pct": round((rainforest * SQ_KM_PER_HECTARE / PANDA_HABITAT_SQ_KM) * 100, 10),
        "arctic_ice_m3": round((co2_kg / KG_CO2_PER_KM3_ICE_MELT) * 1e9, 6),
        "guilt_rating": guilt_rating_co2(co2_tonnes),
    }


def full_environmental_report(parts: list, price_per_kwh: float = 0.12) -> dict:
    watts = rig_total_watts(parts)
    return {
        "total_watts": round(watts, 2),
        "annual_kwh": round(annual_kwh(watts), 2),
        "annual_co2_kg": round(annual_co2_kg(watts), 2),
        "annual_co2_tonnes": round(annual_co2_tonnes(watts), 4),
        "trees_negated": round(trees_destroyed_equivalent(watts), 1),
        "rainforest_hectares": round(rainforest_hectares_destroyed(watts), 4),
        "soccer_fields": round(soccer_fields_destroyed(watts), 4),
        "panda_habitat_pct": round(panda_habitat_percentage(watts), 8),
        "arctic_ice_m3": round(arctic_ice_equivalent_m3(watts), 4),
        "annual_electricity_cost_usd": round(electricity_cost_annual(watts, price_per_kwh), 2),
        "guilt_rating": guilt_rating_co2(annual_co2_tonnes(watts)),
    }


# =============================================================================
# RARITY HELPERS
# =============================================================================
RARITY_ORDER = ["mythic", "legendary", "epic", "rare", "uncommon", "common"]

RARITY_EMOJI = {
    "mythic":    "\U0001f30c",
    "legendary": "\u2b50",
    "epic":      "\U0001f7e3",
    "rare":      "\U0001f535",
    "uncommon":  "\U0001f7e2",
    "common":    "\u26aa",
}

RARITY_COLOR_HEX = {
    "mythic":    "#AA00FF",
    "legendary": "#FFD700",
    "epic":      "#9B59B6",
    "rare":      "#3498DB",
    "uncommon":  "#2ECC71",
    "common":    "#95A5A6",
}


# =============================================================================
# GAME CONSTANTS
# =============================================================================
ELECTRICITY_RATE = 0.02               # credits per watt per hour (power draw matters but isn't brutal)
MINING_RATE = 1.0 / 5_000_000
ACTIVE_MINING_MULTIPLIER = 2.0
SCAVENGE_COOLDOWN = 7200          # 2 hours
MINE_COOLDOWN = 3600              # 1 hour
PARTS_PER_RIG = 5
BTC_BASE_PRICE = 50_000.0             # El Virtual is worth real virtual money
BTC_MIN_PRICE = 5.0
BTC_MAX_PRICE = 500.0
BTC_VOLATILITY = 0.03
BTC_REVERSION = 0.01
MARKET_REFRESH_SECONDS = 10800    # 3 hours
MARKET_SLOTS = 12
STARTING_CREDITS = 500.0          # New standalone players start with this

RARITY_PRICE_MULT = {
    "common":    0.002,
    "uncommon":  0.005,
    "rare":      0.012,
    "epic":      0.025,
    "legendary": 0.060,
    "mythic":    0.150,
}


def update_btc_price(current_price: float, last_updated: float) -> float:
    elapsed_hours = (time.time() - last_updated) / 3600.0
    if elapsed_hours < 0.01:
        return current_price
    steps = max(1, min(int(elapsed_hours), 168))
    price = current_price
    for _ in range(steps):
        drift = BTC_REVERSION * (BTC_BASE_PRICE - price)
        shock = random.gauss(0, BTC_VOLATILITY * price)
        price += drift + shock
    return round(max(BTC_MIN_PRICE, min(BTC_MAX_PRICE, price)), 2)


# =============================================================================
# RIG BONUS MULTIPLIERS
# =============================================================================

# Diversity bonus — reward heterogeneous rigs over five-of-the-same-GPU stacks.
# Keyed by number of unique hardware types present in the rig.
_DIVERSITY_BONUS = {
    1: 1.00,
    2: 1.25,
    3: 1.60,
    4: 2.00,
    5: 2.50,
}

def diversity_multiplier(parts: list) -> float:
    """Return the diversity bonus for a rig based on unique hardware types."""
    unique_types = len({p.get("type", "CPU") for p in parts})
    return _DIVERSITY_BONUS.get(unique_types, _DIVERSITY_BONUS[max(_DIVERSITY_BONUS)])


def legendary_multiplier(parts: list) -> float:
    """
    Return a multiplier based on legendary parts in the rig.
    Each additional legendary adds half of what the previous one contributed,
    converging toward ~9x but never quite reaching it.
      1 legendary  → 5.00x
      2 legendaries → 7.00x
      3 legendaries → 8.00x
      4 legendaries → 8.50x
      5 legendaries → 8.75x
    """
    n = sum(1 for p in parts if p.get("rarity", "") == "legendary")
    if n == 0:
        return 1.0
    # First legendary adds 4 (giving 5x), each subsequent adds half the previous:
    # 1→5x, 2→7x, 3→8x, 4→8.5x, 5→8.75x  (converges toward 9x)
    total = 1.0
    step = 4.0
    for _ in range(n):
        total += step
        step *= 0.5
    return total


# =============================================================================
# COMBO BONUS MULTIPLIERS
# Inspired by Rick Sanchez heterogeneous compute wiring philosophy:
#   "Wire the smallest device as the hypervisor — the FPGA reshapes itself
#    around everything else.  The ASIC just crunches its one trick beneath."
#
# FPGA is required in every named combo (it IS the hypervisor fabric).
# Combos are checked highest-tier first; only the best match applies.
# The combo multiplier stacks ON TOP of diversity and legendary multipliers.
# =============================================================================

# Each entry: (frozenset of required hw types, multiplier, combo name, description)
_COMBO_TIERS = [
    (
        frozenset(["FPGA", "CPU", "GPU", "ASIC", "TPU"]),
        4.0,
        "C-137 Stack",
        "Five-domain heterogeneous compute. Rick would approve.",
    ),
    (
        frozenset(["FPGA", "CPU", "GPU", "TPU"]),
        3.0,
        "Portal Gun Silicon",
        "Neural accelerator + reprogrammable fabric + shaders + general purpose. Wubba lubba.",
    ),
    (
        frozenset(["FPGA", "CPU", "GPU", "ASIC"]),
        2.5,
        "Rick's Übercomputer",
        "FPGA hypervisor orchestrates CPU, GPU, and a dedicated hash cruncher.",
    ),
    (
        frozenset(["FPGA", "CPU", "GPU"]),
        1.75,
        "Hybrid Hypervisor Stack",
        "FPGA reconfigures itself to bridge CPU control flow and GPU shader lanes.",
    ),
    (
        frozenset(["FPGA", "GPU"]),
        1.35,
        "FPGA-GPU Bridge",
        "Reprogrammable gate array wired directly into the shader pipeline.",
    ),
    (
        frozenset(["FPGA", "CPU"]),
        1.20,
        "FPGA-CPU Cluster",
        "FPGA acts as co-processor and memory controller. Vintage supercomputer vibes.",
    ),
    (
        frozenset(["FPGA", "ASIC"]),
        1.15,
        "Reconfigurable Miner",
        "FPGA handles algorithm switching; ASIC does the brute-force SHA-256.",
    ),
]


def combo_multiplier(parts: list) -> tuple:
    """
    Return (multiplier: float, combo_name: str, description: str).
    FPGA must be present for any named combo to activate.
    Returns (1.0, '', '') if no combo is matched.
    """
    types_present = {p.get("type", "").upper() for p in parts}
    if "FPGA" not in types_present:
        return 1.0, "", ""
    for required, mult, name, desc in _COMBO_TIERS:
        if required.issubset(types_present):
            return mult, name, desc
    return 1.0, "", ""


# =============================================================================
# BUSINESS ACCOUNTABILITY & PLANNING ACT — PERMIT TIERS
# "You use electricity to make money. We use electricity to eat.
#  We are not the same."
#
# Tiers are assessed against the user's TOTAL rig score across all rigs.
# Permit fee is weekly, paid in credits.
# CPRM (Computational Prosperity Redistribution Mechanism) is deducted
# from collections over CPRM_THRESHOLD_BTC at the rate for the player's tier.
# =============================================================================

CPRM_THRESHOLD_BTC = 100.0      # collections under this are beneath State notice
CPRM_OVERHEAD_RATE = 0.05       # 5% of pool retained as State administrative overhead
PERMIT_DURATION_DAYS = 7        # permits are weekly

# (score_threshold, tier_number, tier_name, weekly_credit_fee, cprm_rate, flavour)
PERMIT_TIERS = [
    (
        500_000_000_000,  # 500B+
        4,
        "State Competitor",
        50_000_000,
        0.25,
        "You have attracted the personal attention of the Ministry of Computational Prosperity. "
        "A dedicated observer has been assigned to your account. Comply.",
    ),
    (
        50_000_000_000,   # 50B–500B
        3,
        "Industrial Scale Operator",
        5_000_000,
        0.20,
        "Your energy consumption rivals a small municipality. "
        "The State notes your contribution to the electricity shortage with considerable interest.",
    ),
    (
        1_000_000_000,    # 1B–50B
        2,
        "Licensed Commercial Operator",
        500_000,
        0.15,
        "You are conducting business-scale compute operations. "
        "The Ministry of Computational Prosperity requires full accountability and planning compliance.",
    ),
    (
        100_000_000,      # 100M–1B
        1,
        "Registered Operator",
        50_000,
        0.10,
        "Your operation has been noted in the Ministry of Compute records. "
        "A Tier 1 permit is required to continue lawful mining activity.",
    ),
    (
        0,                # <100M — exempt
        0,
        "Hobbyist (Exempt)",
        0,
        0.0,
        "The State has not yet noticed your operation. "
        "Keep it that way.",
    ),
]


# =============================================================================
# RECYCLE YIELD
# Breaks a hardware part down into raw materials.
# Gold scales with rarity (connectors/pins).
# Copper and aluminium scale with TDP (cooling hardware).
# PCB fibreglass is a flat yield — every part has a board.
# =============================================================================

_GOLD_BY_RARITY = {
    "common":    0.001,   # trace gold in basic contacts
    "uncommon":  0.005,
    "rare":      0.020,
    "epic":      0.080,
    "legendary": 0.300,   # dense connector arrays, gold-plated everything
}

def recycle_yield(hw: dict) -> dict:
    """
    Returns a dict of material yields in grams:
    { gold, copper, aluminium, pcb }
    """
    rarity = hw.get("rarity", "common").lower()
    try:    tdp = float(hw.get("tdp_watts") or 0)
    except: tdp = 0.0

    gold      = _GOLD_BY_RARITY.get(rarity, 0.001)
    copper    = round(max(tdp * 0.15, 0.1), 4)   # heatpipes scale with heat
    aluminium = round(max(tdp * 1.50, 0.5), 4)   # heatsink scales with heat
    pcb       = 20.0                               # every part has a board

    return {
        "gold":      round(gold, 4),
        "copper":    copper,
        "aluminium": aluminium,
        "pcb":       pcb,
    }


def assess_permit_tier(total_score: float) -> dict:
    """
    Given a player's total rig score, return their permit tier info dict:
    {tier, name, weekly_fee, cprm_rate, flavour, score_threshold}
    """
    for threshold, tier, name, fee, rate, flavour in PERMIT_TIERS:
        if total_score >= threshold:
            return {
                "tier":            tier,
                "name":            name,
                "weekly_fee":      fee,
                "cprm_rate":       rate,
                "flavour":         flavour,
                "score_threshold": threshold,
            }
    # Fallback — should never reach here given 0-threshold tier
    return {
        "tier": 0, "name": "Hobbyist (Exempt)",
        "weekly_fee": 0, "cprm_rate": 0.0,
        "flavour": "The State has not yet noticed your operation.",
        "score_threshold": 0,
    }


# =============================================================================
# GAME ENGINE CLASS
# =============================================================================

class TrashCollectorEngine:
    """
    Pure game logic for Trash Collector.
    All methods return plain data (dicts, lists, strings) — no Discord objects.
    Uses a fixed user_id=1, guild_id=1 for standalone single-player mode.
    """

    USER_ID = 1
    GUILD_ID = 1

    def __init__(self, db_dir=None):
        if db_dir is None:
            # When frozen by PyInstaller (single .exe), __file__ points into a
            # temporary _MEIPASS extraction folder that is deleted on exit.
            # Use sys.executable's directory instead so data sits next to the .exe.
            import sys as _sys
            if getattr(_sys, "frozen", False):
                _exe_dir = os.path.dirname(os.path.abspath(_sys.executable))
            else:
                _exe_dir = os.path.dirname(os.path.abspath(__file__))

            # Try the game/exe directory first; fall back to a temp-friendly location
            # (some filesystems — e.g. mounted/network dirs — don't support SQLite WAL)
            for candidate in [
                os.path.join(_exe_dir, "standalone_data"),
                os.path.join(os.path.expanduser("~"), ".trash_collector"),
                os.path.join("/tmp", "trash_collector_data"),
            ]:
                os.makedirs(candidate, exist_ok=True)
                try:
                    import sqlite3 as _sq
                    _test = os.path.join(candidate, "_write_test.db")
                    _c = _sq.connect(_test)
                    _c.execute("CREATE TABLE IF NOT EXISTS _t (id INTEGER)")
                    _c.close()
                    os.remove(_test)
                    db_dir = candidate
                    break
                except Exception:
                    continue
            else:
                db_dir = os.path.join(_exe_dir, "standalone_data")
        os.makedirs(db_dir, exist_ok=True)
        mining_path = os.path.join(db_dir, "mining.db")
        credit_path = os.path.join(db_dir, "social_credit.db")
        self.mdb = MiningDB(mining_path)
        self.credit_db = CreditDB(credit_path)
        self._ensure_starting_credits()

    def _ensure_starting_credits(self):
        """Give new players starting credits if they have none and no rigs."""
        credits = self.credit_db.get_credit(self.USER_ID, self.GUILD_ID)
        rigs = self.mdb.get_rigs(self.USER_ID, self.GUILD_ID)
        inv = self.mdb.get_inventory(self.USER_ID, self.GUILD_ID)
        if credits == 0 and not rigs and not inv:
            self.credit_db.update_credit(self.USER_ID, self.GUILD_ID, STARTING_CREDITS)

    # ── Shorthand properties ─────────────────────────────────────────────

    @property
    def uid(self):
        return self.USER_ID

    @property
    def gid(self):
        return self.GUILD_ID

    # ── Internal helpers ─────────────────────────────────────────────────

    def _resolve_parts(self, hw_ids):
        return [HARDWARE_LOOKUP[hid] for hid in hw_ids if hid in HARDWARE_LOOKUP]

    def _rig_stats(self, rig_id):
        hw_ids = self.mdb.get_rig_components(rig_id)
        parts = self._resolve_parts(hw_ids)
        base_score  = sum(compute_score(p) for p in parts)
        div_mult    = diversity_multiplier(parts)
        leg_mult    = legendary_multiplier(parts)
        total_score = base_score * div_mult * leg_mult
        total_watts = rig_total_watts(parts)
        return parts, total_score, total_watts

    def _get_btc_price(self):
        price, last_updated = self.mdb.get_btc_price(self.gid)
        new_price = update_btc_price(price, last_updated)
        self.mdb.set_btc_price(self.gid, new_price)
        return new_price

    def _inventory_with_hw(self):
        raw = self.mdb.get_inventory(self.uid, self.gid)
        result = []
        for inv_id, hw_id in raw:
            hw = HARDWARE_LOOKUP.get(hw_id)
            if hw:
                result.append((inv_id, hw))
        return result

    def _collect_running_rig(self, rig_id, rig_data):
        """Collect pending earnings from a running rig. Returns (btc_mined, elec_cost, kwh_used)."""
        is_running, last_collected = rig_data[2], rig_data[4]
        if not is_running or not last_collected:
            return 0.0, 0.0, 0.0

        parts, score, watts = self._rig_stats(rig_id)
        hours = (time.time() - last_collected) / 3600.0
        btc_mined = score * MINING_RATE * hours
        elec_cost = watts * ELECTRICITY_RATE * hours
        kwh_used = (watts / 1000.0) * hours

        current_credits = self.credit_db.get_credit(self.uid, self.gid)
        if current_credits < elec_cost:
            ratio = max(0, current_credits / elec_cost) if elec_cost > 0 else 1.0
            btc_mined *= ratio
            elec_cost = current_credits
            kwh_used *= ratio

        if btc_mined > 0:
            self.mdb.add_btc(self.uid, self.gid, btc_mined)
            self.mdb.update_rig_collection(rig_id, btc_mined)
        if elec_cost > 0:
            self.credit_db.update_credit(self.uid, self.gid, -elec_cost)
        if kwh_used > 0:
            self.mdb.add_kwh(self.uid, self.gid, kwh_used)

        return btc_mined, elec_cost, kwh_used

    # ── /scavenge ────────────────────────────────────────────────────────

    def scavenge(self):
        """
        Dig through e-waste. Returns dict with result or error.
        {ok: True, finds: [...], cooldown_remaining: 0}
        {ok: False, cooldown_remaining: seconds}
        """
        last = self.mdb.get_cooldown(self.uid, self.gid, "scavenge")
        remaining = SCAVENGE_COOLDOWN - (time.time() - last)
        if remaining > 0:
            return {"ok": False, "cooldown_remaining": remaining}

        num_finds = random.choices([1, 2, 3], weights=[40, 45, 15], k=1)[0]
        finds = random_finds(num_finds)

        for hw in finds:
            self.mdb.add_hardware(self.uid, self.gid, hw["id"])
        self.mdb.set_cooldown(self.uid, self.gid, "scavenge")

        inv_count = len(self.mdb.get_inventory(self.uid, self.gid))

        return {
            "ok": True,
            "finds": [
                {
                    "hw": hw,
                    "score": compute_score(hw),
                    "rarity": hw.get("rarity", "common"),
                    "emoji": RARITY_EMOJI.get(hw.get("rarity", "common"), "\u26aa"),
                }
                for hw in finds
            ],
            "inventory_count": inv_count,
            "cooldown_remaining": 0,
        }

    # ── /parts (inventory) ───────────────────────────────────────────────

    def get_parts(self, sort_by="score"):
        """Return inventory sorted. sort_by: score, rarity, year, type, id"""
        parts_data = self._inventory_with_hw()

        sort_fns = {
            "id":     lambda x: x[0],
            "score":  lambda x: compute_score(x[1]),
            "rarity": lambda x: RARITY_ORDER.index(x[1].get("rarity", "common")),
            "year":   lambda x: x[1].get("year", 0),
            "type":   lambda x: x[1].get("type", ""),
        }
        key_fn = sort_fns.get(sort_by, sort_fns["score"])
        reverse = sort_by in ("score",)

        parts_data.sort(key=key_fn, reverse=reverse)

        return [
            {
                "inv_id": inv_id,
                "hw": hw,
                "score": compute_score(hw),
                "rarity": hw.get("rarity", "common"),
                "emoji": RARITY_EMOJI.get(hw.get("rarity", "common"), "\u26aa"),
            }
            for inv_id, hw in parts_data
        ]

    # ── /build_rig ───────────────────────────────────────────────────────

    def build_rig(self, name, part_inv_ids):
        """
        Build a rig from inventory IDs.
        Returns {ok, rig_id, parts, total_score, total_watts, ...} or {ok: False, error: str}
        """
        if len(name) > 32:
            return {"ok": False, "error": "Rig name must be 32 characters or fewer."}

        if self.mdb.get_rig_by_name(self.uid, self.gid, name):
            return {"ok": False, "error": f"You already have a rig called '{name}'."}

        parts_data = self._inventory_with_hw()
        if len(parts_data) < PARTS_PER_RIG:
            return {"ok": False, "error": f"Need at least {PARTS_PER_RIG} parts, you have {len(parts_data)}."}

        if len(part_inv_ids) != PARTS_PER_RIG:
            return {"ok": False, "error": f"Must select exactly {PARTS_PER_RIG} parts."}

        # Validate all IDs belong to user
        valid_ids = {inv_id for inv_id, _ in parts_data}
        for pid in part_inv_ids:
            if pid not in valid_ids:
                return {"ok": False, "error": f"Part ID {pid} not in your inventory."}

        rig_id = self.mdb.create_rig(self.uid, self.gid, name, part_inv_ids)
        hw_ids = self.mdb.get_rig_components(rig_id)
        parts = self._resolve_parts(hw_ids)
        total_score = sum(compute_score(p) for p in parts)
        total_watts = rig_total_watts(parts)
        rig_count = self.mdb.count_rigs(self.uid, self.gid)

        return {
            "ok": True,
            "rig_id": rig_id,
            "name": name,
            "parts": [
                {
                    "hw": p,
                    "score": compute_score(p),
                    "rarity": p.get("rarity", "common"),
                    "emoji": RARITY_EMOJI.get(p.get("rarity", "common"), "\u26aa"),
                }
                for p in parts
            ],
            "total_score": total_score,
            "total_watts": total_watts,
            "elec_per_hr": total_watts * ELECTRICITY_RATE,
            "rig_count": rig_count,
        }

    # ── /my_rigs ─────────────────────────────────────────────────────────

    def get_rig_detail(self, name):
        """Get detailed info for a single rig by name."""
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        rig = None
        for r in rigs:
            if r[1].lower() == name.lower():
                rig = r
                break
        if not rig:
            return {"ok": False, "error": f"No rig named '{name}' found."}

        rig_id, rig_name, is_running, started_at, last_collected, total_mined = rig
        parts, score, watts = self._rig_stats(rig_id)
        elec_hr = watts * ELECTRICITY_RATE
        now = time.time()

        if is_running and last_collected:
            hours = (now - last_collected) / 3600.0
            pending_btc = score * MINING_RATE * hours
            pending_elec = watts * ELECTRICITY_RATE * hours
            runtime = now - started_at if started_at else 0
            status = "RUNNING"
        else:
            pending_btc = 0
            pending_elec = 0
            runtime = 0
            status = "OFFLINE"

        env = full_environmental_report(parts)

        return {
            "ok": True,
            "rig_id": rig_id,
            "name": rig_name,
            "status": status,
            "runtime_seconds": runtime,
            "score": score,
            "watts": watts,
            "elec_per_hr": elec_hr,
            "pending_btc": pending_btc,
            "pending_elec": pending_elec,
            "total_mined": total_mined,
            "parts": [
                {
                    "hw": p,
                    "score": compute_score(p),
                    "rarity": p.get("rarity", "common"),
                    "emoji": RARITY_EMOJI.get(p.get("rarity", "common"), "\u26aa"),
                }
                for p in parts
            ],
            "env": env,
        }

    def get_all_rigs_overview(self):
        """Get overview of all rigs with totals."""
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        if not rigs:
            return {"ok": False, "error": "You don't own any rigs yet."}

        now = time.time()
        btc_balance = self.mdb.get_btc_balance(self.uid, self.gid)
        btc_price = self._get_btc_price()
        credits = self.credit_db.get_credit(self.uid, self.gid)

        total_pending_btc = 0.0
        total_pending_elec = 0.0
        total_score = 0.0
        total_watts = 0.0
        total_lifetime = 0.0
        online_count = 0
        offline_count = 0
        rig_list = []

        for rig_id, rig_name, is_running, started_at, last_collected, total_mined in rigs:
            parts, score, watts = self._rig_stats(rig_id)
            total_score += score
            total_watts += watts if is_running else 0
            total_lifetime += total_mined

            if is_running and last_collected:
                hours = (now - last_collected) / 3600.0
                pending_btc = score * MINING_RATE * hours
                pending_elec = watts * ELECTRICITY_RATE * hours
                total_pending_btc += pending_btc
                total_pending_elec += pending_elec
                online_count += 1
                status = "ON"
            else:
                offline_count += 1
                status = "OFF"

            rig_list.append({
                "name": rig_name,
                "status": status,
                "score": score,
                "watts": watts,
                "total_mined": total_mined,
            })

        total_kwh = self.mdb.get_total_kwh(self.uid, self.gid)
        lifetime_env = env_from_kwh(total_kwh)

        return {
            "ok": True,
            "rigs": rig_list,
            "online": online_count,
            "offline": offline_count,
            "total_score": total_score,
            "total_watts": total_watts,
            "elec_per_hr": total_watts * ELECTRICITY_RATE,
            "total_lifetime_mined": total_lifetime,
            "btc_balance": btc_balance,
            "pending_btc": total_pending_btc,
            "pending_elec": total_pending_elec,
            "btc_price": btc_price,
            "credits": credits,
            "lifetime_env": lifetime_env,
        }

    # ── /toggle_rig ──────────────────────────────────────────────────────

    def toggle_rig(self, name):
        """Toggle a rig on/off. Auto-collects if turning off."""
        rig = self.mdb.get_rig_by_name(self.uid, self.gid, name)
        if not rig:
            return {"ok": False, "error": f"No rig named '{name}' found."}

        rig_id = rig[0]
        was_running = bool(rig[2])
        btc_mined = 0.0
        elec_cost = 0.0

        if was_running:
            btc_mined, elec_cost, _ = self._collect_running_rig(rig_id, rig)

        new_state = self.mdb.toggle_rig(rig_id, self.uid, self.gid)
        parts, score, watts = self._rig_stats(rig_id)

        return {
            "ok": True,
            "name": name,
            "new_state": "RUNNING" if new_state else "OFFLINE",
            "was_running": was_running,
            "score": score,
            "watts": watts,
            "elec_per_hr": watts * ELECTRICITY_RATE,
            "btc_collected": btc_mined,
            "elec_paid": elec_cost,
        }

    # ── /toggle_all_rigs ─────────────────────────────────────────────────

    def toggle_all_rigs(self, on: bool):
        """Turn all rigs on or off."""
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        if not rigs:
            return {"ok": False, "error": "You don't own any rigs yet."}

        toggled = []
        total_collected_btc = 0.0
        total_elec_cost = 0.0

        for rig in rigs:
            rig_id, rig_name, is_running = rig[0], rig[1], bool(rig[2])
            if is_running == on:
                continue

            if is_running and not on:
                btc, elec, _ = self._collect_running_rig(rig_id, rig)
                total_collected_btc += btc
                total_elec_cost += elec

            self.mdb.set_rig_running(rig_id, self.uid, self.gid, on)
            toggled.append(rig_name)

        if not toggled:
            state = "online" if on else "offline"
            return {"ok": False, "error": f"All your rigs are already {state}."}

        return {
            "ok": True,
            "toggled": toggled,
            "new_state": "RUNNING" if on else "OFFLINE",
            "btc_collected": total_collected_btc,
            "elec_paid": total_elec_cost,
        }

    # ── /mine ────────────────────────────────────────────────────────────

    def mine(self):
        """Active mining cycle (2x bonus, 1hr cooldown)."""
        last = self.mdb.get_cooldown(self.uid, self.gid, "mine")
        remaining = MINE_COOLDOWN - (time.time() - last)
        if remaining > 0:
            return {"ok": False, "cooldown_remaining": remaining}

        rigs = self.mdb.get_rigs(self.uid, self.gid)
        running = [(r[0], r[1]) for r in rigs if r[2]]

        if not running:
            return {"ok": False, "error": "You need at least one running rig. Use 'toggle' first."}

        total_btc = 0.0
        total_elec = 0.0
        total_kwh_cycle = 0.0

        for rig_id, rig_name in running:
            parts, score, watts = self._rig_stats(rig_id)
            total_btc += score * MINING_RATE * ACTIVE_MINING_MULTIPLIER
            total_elec += watts * ELECTRICITY_RATE
            total_kwh_cycle += watts / 1000.0

        current_credits = self.credit_db.get_credit(self.uid, self.gid)
        if current_credits < total_elec:
            return {
                "ok": False,
                "error": f"Not enough credits for electricity! Need {total_elec:,.4f}, have {current_credits:,.1f}.",
            }

        self.credit_db.update_credit(self.uid, self.gid, -total_elec)
        self.mdb.add_btc(self.uid, self.gid, total_btc)
        self.mdb.set_cooldown(self.uid, self.gid, "mine")
        if total_kwh_cycle > 0:
            self.mdb.add_kwh(self.uid, self.gid, total_kwh_cycle)

        new_credits = self.credit_db.get_credit(self.uid, self.gid)
        btc_bal = self.mdb.get_btc_balance(self.uid, self.gid)
        price = self._get_btc_price()
        cycle_env = env_from_kwh(total_kwh_cycle)

        return {
            "ok": True,
            "rigs_used": len(running),
            "btc_mined": total_btc,
            "elec_cost": total_elec,
            "market_value": total_btc * price,
            "new_credits": new_credits,
            "btc_balance": btc_bal,
            "env": cycle_env,
        }

    # ── /collect_btc ─────────────────────────────────────────────────────

    def collect_btc(self):
        """Collect accumulated BTC from running rigs, pay electricity."""
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        running = [r for r in rigs if r[2]]
        if not running:
            return {"ok": False, "error": "No running rigs to collect from."}

        now = time.time()
        total_btc = 0.0
        total_elec = 0.0
        total_kwh = 0.0
        total_hours = 0.0

        for rig_id, name, _, started_at, last_collected, total_mined in running:
            parts, score, watts = self._rig_stats(rig_id)
            hours = (now - (last_collected or now)) / 3600.0
            total_btc += score * MINING_RATE * hours
            total_elec += watts * ELECTRICITY_RATE * hours
            total_kwh += (watts / 1000.0) * hours
            total_hours += hours

        avg_hours = total_hours / len(running)
        current_credits = self.credit_db.get_credit(self.uid, self.gid)
        shutdown = False

        if current_credits >= total_elec:
            self.credit_db.update_credit(self.uid, self.gid, -total_elec)
            self.mdb.add_btc(self.uid, self.gid, total_btc)
            for r in running:
                parts, score, watts = self._rig_stats(r[0])
                hours = (now - (r[4] or now)) / 3600.0
                self.mdb.update_rig_collection(r[0], score * MINING_RATE * hours)
            actual_btc = total_btc
            actual_elec = total_elec
            if total_kwh > 0:
                self.mdb.add_kwh(self.uid, self.gid, total_kwh)
        else:
            ratio = max(0, current_credits / total_elec) if total_elec > 0 else 1.0
            actual_btc = total_btc * ratio
            actual_elec = current_credits
            self.credit_db.update_credit(self.uid, self.gid, -actual_elec)
            self.mdb.add_btc(self.uid, self.gid, actual_btc)
            for r in running:
                self.mdb.shutdown_rig(r[0])
            shutdown = True
            if total_kwh > 0:
                self.mdb.add_kwh(self.uid, self.gid, total_kwh * ratio)

        new_credits = self.credit_db.get_credit(self.uid, self.gid)
        btc_bal = self.mdb.get_btc_balance(self.uid, self.gid)
        price = self._get_btc_price()
        cycle_env = env_from_kwh(total_kwh)

        return {
            "ok": True,
            "rigs_collected": len(running),
            "avg_hours": avg_hours,
            "btc_collected": actual_btc,
            "elec_paid": actual_elec,
            "shutdown": shutdown,
            "full_btc": total_btc,
            "full_elec": total_elec,
            "new_credits": new_credits,
            "btc_balance": btc_bal,
            "btc_price": price,
            "net_value": actual_btc * price,
            "env": cycle_env,
        }

    # ── /scrap_rig ───────────────────────────────────────────────────────

    def scrap_rig(self, name):
        """Scrap a rig, return parts to inventory."""
        rig = self.mdb.get_rig_by_name(self.uid, self.gid, name)
        if not rig:
            return {"ok": False, "error": f"No rig named '{name}' found."}

        rig_id = rig[0]
        btc_mined = 0.0
        elec_cost = 0.0

        if bool(rig[2]) and rig[4]:
            btc_mined, elec_cost, _ = self._collect_running_rig(rig_id, rig)

        hw_ids = self.mdb.scrap_rig(rig_id, self.uid, self.gid)
        if hw_ids is None:
            return {"ok": False, "error": "Failed to scrap rig."}

        parts = self._resolve_parts(hw_ids)
        return {
            "ok": True,
            "name": name,
            "parts_returned": [p["name"] for p in parts],
            "btc_collected": btc_mined,
            "elec_paid": elec_cost,
        }

    # ── /btc_price ───────────────────────────────────────────────────────

    def get_btc_price_info(self):
        price = self._get_btc_price()
        if price > BTC_BASE_PRICE * 1.2:
            trend = "BULL"
        elif price < BTC_BASE_PRICE * 0.8:
            trend = "BEAR"
        else:
            trend = "STABLE"

        return {
            "price": price,
            "trend": trend,
            "base_price": BTC_BASE_PRICE,
            "min_price": BTC_MIN_PRICE,
            "max_price": BTC_MAX_PRICE,
        }

    # ── /buy_btc ─────────────────────────────────────────────────────────

    def buy_btc(self, credit_amount):
        if credit_amount <= 0:
            return {"ok": False, "error": "Amount must be positive."}

        credits = self.credit_db.get_credit(self.uid, self.gid)
        if credits < credit_amount:
            return {"ok": False, "error": f"Insufficient credits. You have {credits:,.1f}."}

        price = self._get_btc_price()
        btc_bought = round(credit_amount / price, 6)

        self.credit_db.update_credit(self.uid, self.gid, -credit_amount)
        self.mdb.add_btc(self.uid, self.gid, btc_bought)

        return {
            "ok": True,
            "spent": credit_amount,
            "price": price,
            "btc_bought": btc_bought,
            "new_credits": self.credit_db.get_credit(self.uid, self.gid),
            "new_btc": self.mdb.get_btc_balance(self.uid, self.gid),
        }

    # ── /sell_btc ────────────────────────────────────────────────────────

    def sell_btc(self, btc_amount):
        if btc_amount <= 0:
            return {"ok": False, "error": "Amount must be positive."}

        balance = self.mdb.get_btc_balance(self.uid, self.gid)
        if balance < btc_amount:
            return {"ok": False, "error": f"Insufficient BTC. You have {balance:,.6f}."}

        price = self._get_btc_price()
        payout = round(btc_amount * price, 2)

        self.mdb.remove_btc(self.uid, self.gid, btc_amount)
        self.credit_db.update_credit(self.uid, self.gid, payout)

        return {
            "ok": True,
            "sold": btc_amount,
            "price": price,
            "payout": payout,
            "new_credits": self.credit_db.get_credit(self.uid, self.gid),
            "new_btc": self.mdb.get_btc_balance(self.uid, self.gid),
        }

    # ── /btc_wallet ──────────────────────────────────────────────────────

    def get_wallet(self):
        balance = self.mdb.get_btc_balance(self.uid, self.gid)
        price = self._get_btc_price()
        credits = self.credit_db.get_credit(self.uid, self.gid)
        return {
            "btc_balance": balance,
            "btc_price": price,
            "market_value": balance * price,
            "credits": credits,
        }

    # ── Parts Market ─────────────────────────────────────────────────────

    def _refresh_market_if_needed(self):
        last_refresh = self.mdb.get_market_refresh_time(self.gid)
        now = time.time()
        current_stock = self.mdb.get_market_stock(self.gid)

        if now - last_refresh >= MARKET_REFRESH_SECONDS or len(current_stock) != MARKET_SLOTS:
            rarity_weights = {
                "common": 35, "uncommon": 25, "rare": 18,
                "epic": 12, "legendary": 7, "mythic": 3,
            }
            pool = []
            weights = []
            for hw in HARDWARE_DB:
                r = hw.get("rarity", "common")
                pool.append(hw)
                weights.append(rarity_weights.get(r, 10))

            picks = random.choices(pool, weights=weights, k=MARKET_SLOTS)
            items = []
            for slot, hw in enumerate(picks, start=1):
                score = compute_score(hw)
                rarity = hw.get("rarity", "common")
                base_mult = RARITY_PRICE_MULT.get(rarity, 0.005)
                btc_price = round(base_mult * max(score, 1) * random.uniform(0.8, 1.3), 6)
                items.append((slot, hw["id"], btc_price))

            self.mdb.set_market_stock(self.gid, items)
            self.mdb.set_market_refresh_time(self.gid, now)

        return self.mdb.get_market_stock(self.gid)

    def get_market(self):
        stock = self._refresh_market_if_needed()
        btc_balance = self.mdb.get_btc_balance(self.uid, self.gid)

        last_refresh = self.mdb.get_market_refresh_time(self.gid)
        next_refresh = last_refresh + MARKET_REFRESH_SECONDS
        remaining = max(0, int(next_refresh - time.time()))

        items = []
        for slot, hw_id, btc_price in stock:
            hw = HARDWARE_LOOKUP.get(hw_id)
            if not hw:
                continue
            items.append({
                "slot": slot,
                "hw": hw,
                "score": compute_score(hw),
                "rarity": hw.get("rarity", "common"),
                "emoji": RARITY_EMOJI.get(hw.get("rarity", "common"), "\u26aa"),
                "btc_price": btc_price,
            })

        return {
            "items": items,
            "btc_balance": btc_balance,
            "refresh_remaining": remaining,
        }

    def buy_parts(self, slot_nums):
        """Buy parts from market by slot number(s)."""
        if not slot_nums:
            return {"ok": False, "error": "No slot numbers provided."}

        # Deduplicate
        seen = set()
        unique = []
        for n in slot_nums:
            if n not in seen:
                seen.add(n)
                unique.append(n)

        self._refresh_market_if_needed()
        stock = self.mdb.get_market_stock(self.gid)
        stock_map = {s: (hw_id, btc_price) for s, hw_id, btc_price in stock}

        to_buy = []
        total_cost = 0.0
        for s in unique:
            if s not in stock_map:
                return {"ok": False, "error": f"Slot {s} is empty or already bought."}
            hw_id, btc_price = stock_map[s]
            hw = HARDWARE_LOOKUP.get(hw_id)
            if not hw:
                return {"ok": False, "error": f"Slot {s}: part no longer in catalogue."}
            to_buy.append((s, hw_id, hw, btc_price))
            total_cost += btc_price

        btc_balance = self.mdb.get_btc_balance(self.uid, self.gid)
        if btc_balance < total_cost:
            return {"ok": False, "error": f"Not enough BTC. Need {total_cost:,.6f}, have {btc_balance:,.6f}."}

        for s, hw_id, hw, btc_price in to_buy:
            self.mdb.remove_btc(self.uid, self.gid, btc_price)
            self.mdb.add_hardware(self.uid, self.gid, hw_id)
            self.mdb.remove_market_slot(self.gid, s)

        new_btc = self.mdb.get_btc_balance(self.uid, self.gid)

        return {
            "ok": True,
            "bought": [
                {
                    "slot": s,
                    "hw": hw,
                    "score": compute_score(hw),
                    "rarity": hw.get("rarity", "common"),
                    "emoji": RARITY_EMOJI.get(hw.get("rarity", "common"), "\u26aa"),
                    "btc_price": btc_price,
                }
                for s, hw_id, hw, btc_price in to_buy
            ],
            "total_cost": total_cost,
            "new_btc": new_btc,
        }

    def sell_part(self, part_id):
        """Sell a part from inventory for BTC."""
        hw_id = self.mdb.get_hardware_by_id(part_id, self.uid, self.gid)
        if hw_id is None:
            return {"ok": False, "error": "No part with that ID in your inventory."}

        hw = HARDWARE_LOOKUP.get(hw_id)
        if hw is None:
            return {"ok": False, "error": "That part no longer exists in the catalogue."}

        rarity = hw.get("rarity", "common")
        score = compute_score(hw)
        sell_price = round(RARITY_PRICE_MULT.get(rarity, 0.002) * max(score, 1) * 0.5, 6)

        self.mdb.remove_hardware(part_id, self.uid, self.gid)
        self.mdb.add_btc(self.uid, self.gid, sell_price)
        new_btc = self.mdb.get_btc_balance(self.uid, self.gid)
        btc_price = self._get_btc_price()

        return {
            "ok": True,
            "hw": hw,
            "score": score,
            "rarity": rarity,
            "emoji": RARITY_EMOJI.get(rarity, "\u26aa"),
            "sell_price": sell_price,
            "credit_value": round(sell_price * btc_price, 2),
            "new_btc": new_btc,
        }

    # ── Rig name listing (for autocomplete equivalent) ───────────────────

    def list_rig_names(self):
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        return [r[1] for r in rigs]

    # ── Status / cooldowns ───────────────────────────────────────────────

    def get_cooldowns(self):
        """Return remaining cooldown seconds for scavenge and mine (0 = ready)."""
        now = time.time()
        scav_last  = self.mdb.get_cooldown(self.uid, self.gid, "scavenge")
        mine_last  = self.mdb.get_cooldown(self.uid, self.gid, "mine")
        return {
            "scavenge": max(0.0, SCAVENGE_COOLDOWN - (now - scav_last)),
            "mine":     max(0.0, MINE_COOLDOWN     - (now - mine_last)),
        }

    def reset_cooldowns(self):
        """Wipe scavenge and mine cooldowns instantly. Hidden dev shortcut."""
        self.mdb.reset_cooldowns(self.uid, self.gid)

    # ── Bulk convenience commands ─────────────────────────────────────────

    def scrap_all(self):
        """Scrap every rig the user owns. Returns list of individual scrap results."""
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        results = []
        for rig in rigs:
            results.append(self.scrap_rig(rig[1]))
        return results

    def scrap_num_rigs(self, count: int):
        """Scrap the <count> newest rigs (highest IDs first). Returns list of scrap results."""
        rigs = self.mdb.get_rigs(self.uid, self.gid)
        newest_first = sorted(rigs, key=lambda r: r[0], reverse=True)
        to_scrap = newest_first[:count]
        results = []
        for rig in to_scrap:
            results.append(self.scrap_rig(rig[1]))
        return results

    def build_all(self, name_prefix="auto"):
        """
        Build as many rigs as possible from current inventory.
        Names rigs <prefix>_1, <prefix>_2, … skipping names that already exist.
        Returns list of individual build results.
        """
        results = []
        counter = 1
        while True:
            inv = self.mdb.get_inventory(self.uid, self.gid)
            if len(inv) < PARTS_PER_RIG:
                break
            # Pick the first PARTS_PER_RIG inventory IDs (sorted by score desc)
            parts_data = self._inventory_with_hw()
            if len(parts_data) < PARTS_PER_RIG:
                break
            # Sort by score descending so best parts go in first
            parts_data.sort(key=lambda x: compute_score(x[1]), reverse=True)
            chosen_ids = [inv_id for inv_id, _ in parts_data[:PARTS_PER_RIG]]
            # Find an unused name
            name = f"{name_prefix}_{counter}"
            while self.mdb.get_rig_by_name(self.uid, self.gid, name):
                counter += 1
                name = f"{name_prefix}_{counter}"
            result = self.build_rig(name, chosen_ids)
            results.append(result)
            counter += 1
            if not result["ok"]:
                break
        return results

    def auto_build(self, name_prefix="Smart-Rig"):
        """
        Greedy diversity-optimised rig builder — O(n log n).
        Costs 10% of BTC balance as a consultant fee (waived if broke).

        Uses pre-indexed type buckets (deques, sorted best-first) so every
        draft is O(1) instead of scanning the whole remaining pool each rig.
        A global sorted overflow list handles Pass 2 fill slots.

        Returns dict:
          {
            "fee_charged": float,
            "rigs_built":  int,
            "rigs":        [{"name": str, "inv_ids": [...], "parts_hw": [...]}, ...],
            "parts_left":  int,
          }
        """
        from collections import deque

        DRAFT_ORDER = ["FPGA", "GPU", "CPU", "ASIC", "TPU",
                       "NEUROMORPHIC", "DSP", "MCU", "MEMORY",
                       "MOTHERBOARD", "DATACENTER", "ARRAY"]

        # ── Consultant fee ────────────────────────────────────────────────
        btc_bal = self.mdb.get_btc_balance(self.uid, self.gid)
        fee = btc_bal * 0.10 if btc_bal > 0 else 0.0
        if fee > 0:
            self.mdb.add_btc(self.uid, self.gid, -fee)

        # ── Build pool and type index — sort once, O(n log n) ─────────────
        raw_inv = self._inventory_with_hw()   # [(inv_id, hw_dict), ...]
        pool = []
        for inv_id, hw in raw_inv:
            pool.append({
                "inv_id": inv_id,
                "hw":     hw,
                "type":   hw.get("type", "CPU").upper(),
                "score":  compute_score(hw),
            })
        pool.sort(key=lambda x: x["score"], reverse=True)

        # One deque per type, best-score at front (already ordered by pool sort)
        type_buckets = {}
        for p in pool:
            type_buckets.setdefault(p["type"], deque()).append(p)

        # Global overflow list for Pass 2 — same order as pool (score desc)
        overflow = list(pool)   # shallow copy, we'll skip used entries cheaply

        used    = set()
        built   = []
        counter = 1
        total   = len(pool)

        while total - len(used) >= PARTS_PER_RIG:
            rig_parts     = []
            drafted_types = set()

            # Pass 1 — O(1) per type: pop best available from each bucket
            for want_type in DRAFT_ORDER:
                if len(rig_parts) >= PARTS_PER_RIG:
                    break
                if want_type in drafted_types:
                    continue
                bucket = type_buckets.get(want_type)
                if not bucket:
                    continue
                # Skip already-used entries at front of bucket
                while bucket and bucket[0]["inv_id"] in used:
                    bucket.popleft()
                if bucket:
                    pick = bucket.popleft()
                    rig_parts.append(pick)
                    drafted_types.add(want_type)
                    used.add(pick["inv_id"])

            # Pass 2 — fill remaining slots from overflow (score desc)
            # Advance a pointer rather than rebuilding a list each rig
            if len(rig_parts) < PARTS_PER_RIG:
                for p in overflow:
                    if len(rig_parts) >= PARTS_PER_RIG:
                        break
                    if p["inv_id"] not in used:
                        rig_parts.append(p)
                        used.add(p["inv_id"])

            if len(rig_parts) < PARTS_PER_RIG:
                break

            # Find an unused rig name — pre-check existing names once
            name = f"{name_prefix} #{counter}"
            while self.mdb.get_rig_by_name(self.uid, self.gid, name):
                counter += 1
                name = f"{name_prefix} #{counter}"

            inv_ids = [p["inv_id"] for p in rig_parts]
            self.mdb.create_rig(self.uid, self.gid, name, inv_ids)
            built.append({
                "name":     name,
                "inv_ids":  inv_ids,
                "parts_hw": [p["hw"] for p in rig_parts],
            })
            counter += 1

        return {
            "fee_charged": fee,
            "rigs_built":  len(built),
            "rigs":        built,
            "parts_left":  len(pool) - len(used),
        }

    def sell_part_all(self):
        """
        Sell every part in inventory — bulk optimised.

        Instead of N individual sell_part() calls (each doing a SELECT,
        DELETE and two UPDATEs), this method:
          1. Fetches the full inventory in one query
          2. Buckets by rarity (lookup table style) so price is O(1) per part
          3. Calculates total BTC in a single pass — no repeated DB reads
          4. Bulk-deletes all inventory rows in one DELETE … IN (…) query
          5. One single add_btc() call with the grand total

        Returns a summary dict (not a per-item list — the list would be huge).
        """
        inv = self._inventory_with_hw()   # [(inv_id, hw_dict), ...]
        if not inv:
            return {"ok": False, "sold": 0, "total_btc": 0.0}

        # ── Price bucket by rarity — one dict lookup per part ─────────────
        # Formula mirrors sell_part(): RARITY_PRICE_MULT[rarity] * score * 0.5
        total_btc   = 0.0
        rarity_counts = {}
        inv_ids_to_delete = []

        for inv_id, hw in inv:
            rarity     = hw.get("rarity", "common")
            score      = compute_score(hw)
            sell_price = round(RARITY_PRICE_MULT.get(rarity, 0.002) * max(score, 1) * 0.5, 6)
            total_btc += sell_price
            rarity_counts[rarity] = rarity_counts.get(rarity, 0) + 1
            inv_ids_to_delete.append(inv_id)

        # ── One bulk DELETE + one add_btc ──────────────────────────────────
        self.mdb.remove_hardware_bulk(inv_ids_to_delete, self.uid, self.gid)
        self.mdb.add_btc(self.uid, self.gid, total_btc)

        btc_price = self._get_btc_price()
        return {
            "ok":           True,
            "sold":         len(inv_ids_to_delete),
            "total_btc":    round(total_btc, 6),
            "credit_value": round(total_btc * btc_price, 2),
            "by_rarity":    rarity_counts,   # e.g. {"common": 5000, "rare": 200, …}
        }

    def get_status(self):
        """Compact snapshot for the status command."""
        wallet    = self.get_wallet()
        cooldowns = self.get_cooldowns()
        rigs      = self.mdb.get_rigs(self.uid, self.gid)
        inv_count = len(self.mdb.get_inventory(self.uid, self.gid))

        now = time.time()
        online, offline, pending_btc, pending_elec = 0, 0, 0.0, 0.0
        for rig_id, _, is_running, started_at, last_collected, _ in rigs:
            if is_running:
                online += 1
                parts, score, watts = self._rig_stats(rig_id)
                hours = (now - (last_collected or now)) / 3600.0
                pending_btc  += score * MINING_RATE * hours
                pending_elec += watts * ELECTRICITY_RATE * hours
            else:
                offline += 1

        return {
            "credits":      wallet["credits"],
            "btc_balance":  wallet["btc_balance"],
            "btc_price":    wallet["btc_price"],
            "market_value": wallet["market_value"],
            "rigs_online":  online,
            "rigs_offline": offline,
            "rigs_total":   len(rigs),
            "inv_count":    inv_count,
            "pending_btc":  pending_btc,
            "pending_elec": pending_elec,
            "cooldowns":    cooldowns,
        }

    def env_from_lifetime(self) -> dict:
        """Return environmental destruction dict derived from ALL kWh ever consumed."""
        total_kwh = self.mdb.get_total_kwh(self.uid, self.gid)
        return env_from_kwh(total_kwh)
