#!/usr/bin/env python3
"""
TRASH COLLECTOR - Standalone Terminal Edition
=============================================
Scavenge vintage hardware, build mining rigs, mine El Virtual.
Destroy the environment. Feel guilty about it.

Run:  python standalone.py
"""

import sys
import os
import shlex
import json
import threading
import time

# ── rich imports ─────────────────────────────────────────────────────────
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich import box

# ── prompt_toolkit imports ───────────────────────────────────────────────
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, FuzzyCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.patch_stdout import patch_stdout

from game_engine import (
    TrashCollectorEngine,
    RARITY_COLOR_HEX,
    RARITY_EMOJI,
    RARITY_ORDER,
    PARTS_PER_RIG,
    compute_score,
    diversity_multiplier,
    legendary_multiplier,
    combo_multiplier,
)

# ── Console with minimum width ──────────────────────────────────────────
MIN_WIDTH = 80

class MinWidthConsole(Console):
    """Console that never reports less than MIN_WIDTH columns.
    Rich auto-detects terminal width on every print — this just clamps
    the floor so tables don't collapse when the window is narrow."""
    @property
    def width(self):
        return max(super().width, MIN_WIDTH)

console = MinWidthConsole()


# =============================================================================
# EVENT LOG
# =============================================================================

class EventLog:
    """
    Lightweight append-only JSONL event log.
    One JSON object per line, stored in the same data dir as the databases.
    """

    # Human-readable icons and labels per event type
    _META = {
        "scavenge":   ("🗑 ", "Scavenged"),
        "build":      ("🔧", "Built rig"),
        "toggle":     ("⚡", "Toggled rig"),
        "toggle_all": ("⚡", "Toggled all"),
        "mine":       ("⛏ ", "Mined"),
        "collect":    ("💰", "Collected"),
        "scrap":      ("🔨", "Scrapped rig"),
        "buy_part":   ("🛒", "Bought part(s)"),
        "sell_part":  ("💵", "Sold part"),
        "buy_btc":    ("📈", "Bought BTC"),
        "sell_btc":   ("📉", "Sold BTC"),
    }

    def __init__(self, log_path):
        self.path = log_path

    def append(self, event_type: str, summary: str):
        """Write one event to the log file."""
        record = {
            "ts":      time.time(),
            "type":    event_type,
            "summary": summary,
        }
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass  # Never crash the game over logging

    def read_last(self, n: int = 20) -> list:
        """Return the last n events as a list of dicts, newest last."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return records[-n:]


# =============================================================================
# MILESTONE GUILT TRACKER
# =============================================================================
# Each milestone fires exactly once and is remembered across sessions.
# Thresholds are checked against lifetime environmental stats derived from
# the total kWh consumed.  All messages are appropriately on-brand.

_MILESTONES = [
    # key, threshold_fn(env), title, body, border_color
    (
        "first_kwh",
        lambda e: e["kwh"] >= 1,
        "⚡  First Blood",
        "You've consumed your very first kWh.\n"
        "Somewhere, a single LED bulb flickers in solidarity.\n"
        "[dim]This is how it starts.[/dim]",
        "dim white",
    ),
    (
        "first_tree",
        lambda e: e["trees_negated"] >= 1,
        "🌳  Deforestation Begins",
        "You have negated the annual CO₂ absorption of [bold]one full tree[/bold].\n"
        "A squirrel has filed a missing persons report.\n"
        "[dim]It only gets worse from here.[/dim]",
        "green",
    ),
    (
        "ten_trees",
        lambda e: e["trees_negated"] >= 10,
        "🌲🌲  A Small Grove, Gone",
        "[bold]10 trees[/bold] worth of CO₂ absorption, cancelled.\n"
        "A local hiking trail has been renamed in your dishonour.\n"
        "[dim]The squirrels are organising.[/dim]",
        "green",
    ),
    (
        "hundred_trees",
        lambda e: e["trees_negated"] >= 100,
        "🪓  The Lorax Has Left The Building",
        "[bold]100 trees[/bold] negated.\n"
        "The Lorax tried to speak for the trees.\n"
        "They have since gone into witness protection.",
        "yellow",
    ),
    (
        "first_soccer_field",
        lambda e: e["soccer_fields"] >= 1,
        "⚽  One Soccer Field of Rainforest",
        "You've cancelled out the carbon absorption of\n"
        "[bold]one entire FIFA-standard soccer pitch[/bold] of rainforest.\n"
        "[dim]FIFA has issued a formal complaint. Nobody read it.[/dim]",
        "yellow",
    ),
    (
        "first_hectare",
        lambda e: e["rainforest_hectares"] >= 1,
        "🌴  One Hectare Down",
        "A full [bold]hectare of tropical rainforest[/bold] absorption capacity,\n"
        "neutralised by your mining operation.\n"
        "Somewhere a jaguar is staring at you through the screen.",
        "yellow",
    ),
    (
        "greta_enters",
        lambda e: e["co2_tonnes"] >= 20,
        "😤  Greta Has Entered The Chat",
        "[bold]20+ tonnes of CO₂[/bold] on your conscience.\n"
        "Greta Thunberg has entered the chat.\n"
        "Her opening message is: [italic]'How dare you.'[/italic]\n"
        "[dim]She has a point.[/dim]",
        "red",
    ),
    (
        "penguins_sue",
        lambda e: e["co2_tonnes"] >= 100,
        "🐧  Legal Action",
        "[bold]100 tonnes of CO₂.[/bold]\n"
        "The penguins have retained legal counsel.\n"
        "The polar bears are listed as co-plaintiffs.\n"
        "[dim]The case is being heard in The Hague.[/dim]",
        "red",
    ),
    (
        "thousand_trees",
        lambda e: e["trees_negated"] >= 1000,
        "💀  A Thousand Trees",
        "[bold]1,000 trees.[/bold] One thousand.\n"
        "That's a small forest. An actual forest.\n"
        "[italic]You deleted a forest.[/italic]\n"
        "[dim]Virtually. But still.[/dim]",
        "red",
    ),
    (
        "ten_soccer_fields",
        lambda e: e["soccer_fields"] >= 10,
        "🏟️  Ten Pitches, Vaporised",
        "The carbon absorption of [bold]10 soccer fields[/bold] of rainforest,\n"
        "erased by your relentless hash-grinding.\n"
        "FIFA has upgraded their complaint to a strongly-worded letter.",
        "red",
    ),
    (
        "melting_glacier",
        lambda e: e["co2_tonnes"] >= 500,
        "🧊  Personal Glacier Melter",
        "[bold]500 tonnes of CO₂.[/bold]\n"
        "Scientists have named a retreating glacier after you.\n"
        "Not as a tribute.",
        "bold red",
    ),
    (
        "ten_hectares",
        lambda e: e["rainforest_hectares"] >= 10,
        "🗺️  Ten Hectares of Silence",
        "[bold]10 full hectares[/bold] of rainforest absorption capacity.\n"
        "That's roughly 14 football pitches of ecological debt.\n"
        "[dim]David Attenborough has recorded a special episode about you.[/dim]\n"
        "[dim]You are the villain.[/dim]",
        "bold red",
    ),
    (
        "extinction_level",
        lambda e: e["co2_tonnes"] >= 5000,
        "☢️  Geological Event",
        "[bold]5,000 tonnes of CO₂.[/bold]\n"
        "Geologists will find traces of your mining session\n"
        "in the sediment record.\n"
        "[italic]You are now a stratigraphic layer.[/italic]",
        "bold red",
    ),
    (
        "cosmic_horror",
        lambda e: e["co2_tonnes"] >= 50000,
        "🕳️  The Sun Has Concerns",
        "[bold]50,000 tonnes.[/bold]\n"
        "The Sun has asked you to tone it down.\n"
        "This is the first time the Sun has ever asked anything of anyone.\n"
        "[dim]You should feel special. And terrible.[/dim]",
        "bold magenta",
    ),
]

# Build a lookup dict for O(1) access
_MILESTONE_LOOKUP = {m[0]: m for m in _MILESTONES}


class MilestoneTracker:
    """
    Tracks which one-time guilt milestones have already fired.
    State persisted to a small JSON file so milestones survive restarts.
    """

    def __init__(self, path: str):
        self.path = path
        self._fired: set = self._load()

    def _load(self) -> set:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("fired", []))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"fired": sorted(self._fired)}, f)
        except OSError:
            pass

    def check(self, env: dict) -> list:
        """
        Check env stats against all milestones.
        Returns list of newly-triggered milestone tuples (key, title, body, color).
        Persists fired state immediately.
        """
        newly_fired = []
        for key, threshold_fn, title, body, color in _MILESTONES:
            if key in self._fired:
                continue
            try:
                if threshold_fn(env):
                    self._fired.add(key)
                    newly_fired.append((key, title, body, color))
            except Exception:
                pass
        if newly_fired:
            self._save()
        return newly_fired


def show_milestones(milestones: list):
    """Display each newly-fired milestone as a dramatic panel."""
    for key, title, body, color in milestones:
        console.print()
        console.print(Panel(
            f"{body}",
            title=f"[bold {color}]🏆  MILESTONE UNLOCKED:  {title}[/bold {color}]",
            border_style=color,
            padding=(1, 2),
        ))
        console.print()


# =============================================================================
# BACKGROUND ELECTRICITY DEBT MONITOR
# =============================================================================

# How often the monitor wakes up (seconds)
_MONITOR_INTERVAL = 600   # 10 minutes

# Warn when pending electricity bill exceeds this fraction of current credits
_WARN_THRESHOLD    = 0.50   # 50% → yellow warning
_CRITICAL_THRESHOLD = 1.00  # 100% → red alert (shutdown imminent)


class BackgroundMonitor:
    """
    Daemon thread that periodically checks whether running rigs are racking up
    an electricity bill the player can't afford, then prints a warning above
    the prompt using prompt_toolkit's patch_stdout mechanism.
    """

    def __init__(self, engine: "TrashCollectorEngine"):
        self.engine = engine
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ElecMonitor")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        # Wait one full interval before the first check so we don't nag
        # immediately on startup
        self._stop.wait(_MONITOR_INTERVAL)
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                pass  # Never crash the game from the monitor thread
            self._stop.wait(_MONITOR_INTERVAL)

    def _check(self):
        s = self.engine.get_status()
        if s["rigs_online"] == 0:
            return  # Nothing running, nothing to warn about

        pending = s["pending_elec"]
        credits = s["credits"]

        if credits <= 0:
            ratio = float("inf")
        else:
            ratio = pending / credits

        if ratio < _WARN_THRESHOLD:
            return  # All good

        if ratio >= _CRITICAL_THRESHOLD:
            msg = (
                f"\n[bold red]🚨 ELECTRICITY ALERT[/bold red]  "
                f"Pending bill [bold red]{pending:,.2f}[/bold red] cr "
                f"exceeds your balance of [bold red]{credits:,.1f}[/bold red] cr — "
                f"[bold red]rigs will shut down on next collect![/bold red]  "
                f"Run [bold]collect[/bold] now or [bold]toggle_all off[/bold] to avoid losing BTC.\n"
            )
        else:
            pct = int(ratio * 100)
            msg = (
                f"\n[yellow]⚠  Electricity heads-up:[/yellow]  "
                f"Pending bill is [yellow]{pending:,.2f}[/yellow] cr "
                f"([yellow]{pct}%[/yellow] of your {credits:,.1f} cr balance).  "
                f"Run [bold]collect[/bold] soon.\n"
            )

        # patch_stdout ensures the message appears cleanly above the prompt
        console.print(msg)


# =============================================================================
# TAB COMPLETION
# =============================================================================

# Commands that take a rig name as the next argument
_RIG_NAME_COMMANDS = {"toggle", "rig", "scrap"}

# All base commands
_ALL_COMMANDS = [
    "scavenge", "parts", "inventory", "inv", "build", "build_all", "auto_build", "rigs", "rig",
    "toggle", "toggle_all", "mine", "collect", "scrap", "scrap_all", "scrap_num_rigs", "market", "buy",
    "sell_part", "sell_part_all", "price", "buy_btc", "sell_btc", "wallet", "status",
    "log", "history", "help", "quit", "exit",
]

# Subcommands / fixed second args
_TOGGLE_ALL_ARGS = ["on", "off"]


class TrashCompleter(Completer):
    """Context-aware tab completer for Trash Collector commands."""

    def __init__(self, engine):
        self.engine = engine

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        # Nothing typed yet, or still on the first word
        if len(words) == 0 or (len(words) == 1 and not text.endswith(" ")):
            prefix = words[0].lower() if words else ""
            for cmd in _ALL_COMMANDS:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        cmd = words[0].lower()
        # Determine what we're completing (the current partial word)
        if text.endswith(" "):
            partial = ""
        else:
            partial = words[-1].lower()

        # Commands that take rig names
        if cmd in _RIG_NAME_COMMANDS:
            try:
                rig_names = self.engine.list_rig_names()
            except Exception:
                rig_names = []
            for name in rig_names:
                if name.lower().startswith(partial):
                    yield Completion(name, start_position=-len(partial))
            return

        # toggle_all takes on/off
        if cmd == "toggle_all":
            for arg in _TOGGLE_ALL_ARGS:
                if arg.startswith(partial):
                    yield Completion(arg, start_position=-len(partial))
            return

        # build — also suggest rig names for inspiration, but mainly freeform
        # buy — suggest slot numbers from current market
        if cmd == "buy" and not partial:
            try:
                market = self.engine.get_market()
                for item in market["items"]:
                    s = str(item["slot"])
                    yield Completion(s, start_position=0,
                                     display_meta=f"{item['hw']['name']} ({item['rarity']})")
            except Exception:
                pass
            return

        # sell_part — suggest inventory IDs
        if cmd == "sell_part":
            try:
                parts = self.engine.get_parts()
                for p in parts[:25]:
                    sid = str(p["inv_id"])
                    if sid.startswith(partial):
                        yield Completion(sid, start_position=-len(partial),
                                         display_meta=f"{p['hw']['name']} ({p['rarity']})")
            except Exception:
                pass
            return


# prompt_toolkit style for the prompt
_PT_STYLE = PTStyle.from_dict({
    "prompt": "ansicyan bold",
})

# ── Colour helpers ───────────────────────────────────────────────────────

RARITY_STYLE = {
    "mythic":    "bold magenta",
    "legendary": "bold yellow",
    "epic":      "bold purple",
    "rare":      "bold blue",
    "uncommon":  "bold green",
    "common":    "dim white",
}


def rarity_text(rarity: str, text: str) -> Text:
    return Text(text, style=RARITY_STYLE.get(rarity, "white"))


def fmt_cooldown(seconds):
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h {m}m {s}s"
    elif m:
        return f"{m}m {s}s"
    return f"{s}s"


# =============================================================================
# NUMBER FORMATTERS
# =============================================================================

def fmt_score(n: float) -> str:
    """Format a score with suffix for readability (K / M / B / T / Q)."""
    n = float(n)
    if n >= 1_000_000_000_000_000:
        return f"{n / 1_000_000_000_000_000:.2f}Q"
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.2f}T"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,.0f}"


def fmt_watts(w: float) -> str:
    """Format wattage with appropriate unit (W / kW / MW / GW)."""
    w = float(w)
    if w >= 1_000_000_000:
        return f"{w / 1_000_000_000:.2f} GW"
    if w >= 1_000_000:
        return f"{w / 1_000_000:.2f} MW"
    if w >= 1_000:
        return f"{w / 1_000:.1f} kW"
    return f"{w:.0f}W"


# =============================================================================
# DISPLAY FUNCTIONS
# =============================================================================

def show_scavenge(result):
    if not result["ok"]:
        console.print(
            f"[yellow]The dumpster is picked clean. Come back in "
            f"[bold]{fmt_cooldown(result['cooldown_remaining'])}[/bold].[/yellow]"
        )
        return

    console.print()
    console.print(Panel.fit(
        f"[bold]You rummage through the e-waste and find "
        f"[cyan]{len(result['finds'])}[/cyan] piece{'s' if len(result['finds']) > 1 else ''}![/bold]",
        title="[bold]\U0001f5d1  Dumpster Diving",
        border_style="green",
    ))

    for item in result["finds"]:
        hw = item["hw"]
        rarity = item["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        console.print(
            f"  {item['emoji']} [{style}]{hw['name']}[/{style}] "
            f"({hw['year']})  "
            f"[dim]{hw['type']}[/dim]  "
            f"Score: [bold]{fmt_score(item['score'])}[/bold]  "
            f"{fmt_watts(hw.get('tdp_watts', 0))}  "
            f"[italic]{rarity.title()}[/italic]"
        )
        desc = hw.get("description", "")
        if desc:
            console.print(f"    [dim italic]{desc}[/dim italic]")

    console.print(f"\n  [dim]Inventory: {result['inventory_count']} parts "
                  f"(need {PARTS_PER_RIG} to build a rig)[/dim]")


def show_parts(parts_list, page=0, per_page=15):
    if not parts_list:
        console.print("[yellow]Your inventory is empty. Use 'scavenge' to find parts![/yellow]")
        return

    total = len(parts_list)
    max_page = max(0, (total - 1) // per_page)
    page = max(0, min(page, max_page))
    start = page * per_page
    end = start + per_page
    page_items = parts_list[start:end]

    table = Table(
        title=f"\U0001f4e6 Hardware Inventory ({total} parts) - Page {page+1}/{max_page+1}",
        box=box.ROUNDED,
        title_style="bold cyan",
        show_lines=False,
    )
    table.add_column("ID", style="dim", width=6)
    table.add_column("", width=2)
    table.add_column("Name", min_width=25)
    table.add_column("Year", width=6)
    table.add_column("Type", width=12)
    table.add_column("Score", justify="right", width=10)
    table.add_column("TDP", justify="right", width=10)
    table.add_column("Rarity", width=12)

    for item in page_items:
        hw = item["hw"]
        rarity = item["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        table.add_row(
            str(item["inv_id"]),
            item["emoji"],
            f"[{style}]{hw['name']}[/{style}]",
            str(hw.get("year", "?")),
            hw.get("type", "?"),
            fmt_score(item['score']),
            fmt_watts(hw.get('tdp_watts', 0)),
            f"[{style}]{rarity.title()}[/{style}]",
        )

    console.print(table)
    if max_page > 0:
        console.print(f"  [dim]Use 'parts <page>' to see other pages (1-{max_page+1})[/dim]")


def show_rigs_overview(data):
    if not data["ok"]:
        console.print(f"[yellow]{data['error']}[/yellow]")
        return

    table = Table(
        title="\u26cf  Your Mining Operation",
        box=box.ROUNDED,
        title_style="bold #F7931A",
        show_lines=False,
    )
    table.add_column("Status", width=4)
    table.add_column("Rig Name", min_width=20)
    table.add_column("Score", justify="right", width=10)
    table.add_column("Power", justify="right", width=12)
    table.add_column("Mined", justify="right", width=12)

    for rig in data["rigs"]:
        status = "[green]\u25cf[/green]" if rig["status"] == "ON" else "[red]\u25cf[/red]"
        table.add_row(
            status,
            f"[bold]{rig['name']}[/bold]",
            fmt_score(rig['score']),
            fmt_watts(rig['watts']),
            f"{rig['total_mined']:,.4f}",
        )

    console.print(table)

    # Totals panel
    d = data
    le = d["lifetime_env"]
    totals = (
        f"[bold]Online:[/bold] {d['online']}  [bold]Offline:[/bold] {d['offline']}\n"
        f"[bold]Combined Score:[/bold] {fmt_score(d['total_score'])}  "
        f"[bold]Power Draw:[/bold] {fmt_watts(d['total_watts'])}  "
        f"[bold]Cost:[/bold] {d['elec_per_hr']:,.4f} cr/hr\n"
        f"[bold]Lifetime Mined:[/bold] {d['total_lifetime_mined']:,.6f} BTC\n"
        f"\n[bold cyan]Wallet[/bold cyan]\n"
        f"  El Virtual: {d['btc_balance']:,.6f} BTC\n"
        f"  Pending: {d['pending_btc']:,.6f} BTC / {d['pending_elec']:,.4f} cr electricity\n"
        f"  BTC Price: {d['btc_price']:,.2f} credits\n"
        f"  Social Credits: {d['credits']:,.1f}\n"
        f"\n[bold green]Lifetime Environmental Destruction[/bold green]\n"
        f"  Energy: {le['kwh']:,.2f} kWh  CO\u2082: {le['co2_kg']:,.2f} kg\n"
        f"  Trees: {le['trees_negated']:,.2f}  "
        f"Rainforest: {le['rainforest_hectares']:.6f} ha  "
        f"Ice: {le['arctic_ice_m3']:.6f} m\u00b3\n"
        f"  Guilt: {le['guilt_rating']}"
    )
    console.print(Panel(totals, border_style="#F7931A", title="Totals"))


def show_rig_detail(data):
    if not data["ok"]:
        console.print(f"[yellow]{data['error']}[/yellow]")
        return

    status_style = "green" if data["status"] == "RUNNING" else "red"
    runtime = ""
    if data["runtime_seconds"] > 0:
        rh = int(data["runtime_seconds"]) // 3600
        rm = (int(data["runtime_seconds"]) % 3600) // 60
        runtime = f" ({rh}h {rm}m)"

    header = (
        f"[{status_style}]{data['status']}{runtime}[/{status_style}]  "
        f"Score: [bold]{fmt_score(data['score'])}[/bold]  "
        f"Power: {fmt_watts(data['watts'])}  "
        f"Cost: {data['elec_per_hr']:,.4f} cr/hr"
    )
    console.print(Panel.fit(header, title=f"[bold]\u2699 {data['name']}[/bold]", border_style="#F7931A"))

    # Parts table
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("")
    table.add_column("Part")
    table.add_column("Type", width=10)
    table.add_column("Score", justify="right")
    table.add_column("TDP", justify="right")
    table.add_column("Rarity")

    for p in data["parts"]:
        hw = p["hw"]
        rarity = p["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        table.add_row(
            p["emoji"],
            f"[{style}]{hw['name']}[/{style}]",
            hw.get("type", "?"),
            fmt_score(p['score']),
            fmt_watts(hw.get('tdp_watts', 0)),
            f"[{style}]{rarity.title()}[/{style}]",
        )
    console.print(table)

    console.print(
        f"  [bold]Pending BTC:[/bold] {data['pending_btc']:,.6f}  "
        f"[bold]Pending Elec:[/bold] {data['pending_elec']:,.4f} cr  "
        f"[bold]Lifetime:[/bold] {data['total_mined']:,.6f} BTC"
    )
    env = data["env"]
    console.print(
        f"  [green]Trees/yr:[/green] {env['trees_negated']:,.1f}  "
        f"[green]Rainforest/yr:[/green] {env['rainforest_hectares']:.4f} ha  "
        f"[bold]Guilt:[/bold] {env['guilt_rating']}"
    )


def show_toggle(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    state_style = "green" if result["new_state"] == "RUNNING" else "red"
    console.print(f"[{state_style}]\u26a1 {result['name']} \u2014 {result['new_state']}[/{state_style}]")
    console.print(
        f"  Score: {fmt_score(result['score'])}  "
        f"Power: {fmt_watts(result['watts'])}  "
        f"Cost: {result['elec_per_hr']:,.4f} cr/hr"
    )
    if result["was_running"] and result["new_state"] == "OFFLINE":
        console.print(
            f"  Collected [bold]{result['btc_collected']:,.6f}[/bold] BTC, "
            f"paid [bold]{result['elec_paid']:,.4f}[/bold] electricity."
        )


def show_toggle_all(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    state_style = "green" if result["new_state"] == "RUNNING" else "red"
    names = ", ".join(result["toggled"])
    console.print(f"[{state_style}]\u26a1 {len(result['toggled'])} rigs \u2014 {result['new_state']}[/{state_style}]")
    console.print(f"  {names}")
    if result["btc_collected"] > 0:
        console.print(
            f"  Final collection: [bold]+{result['btc_collected']:,.6f}[/bold] BTC, "
            f"[bold]-{result['elec_paid']:,.4f}[/bold] electricity."
        )


def show_mine(result):
    if not result["ok"]:
        if "cooldown_remaining" in result:
            console.print(f"[yellow]Mining cooldown: {fmt_cooldown(result['cooldown_remaining'])} remaining.[/yellow]")
        else:
            console.print(f"[yellow]{result['error']}[/yellow]")
        return

    env = result["env"]
    console.print(Panel.fit(
        f"[bold]Rigs:[/bold] {result['rigs_used']} cranked\n"
        f"[bold]Mined:[/bold] {result['btc_mined']:,.6f} BTC\n"
        f"[bold]Electricity:[/bold] {result['elec_cost']:,.4f} credits\n"
        f"[bold]Market Value:[/bold] {result['market_value']:,.2f} credits\n"
        f"\n[green]CO\u2082: {env['co2_kg']:,.2f} kg  "
        f"Rainforest: {env['rainforest_hectares']:.6f} ha  "
        f"Trees: {env['trees_negated']:,.2f}[/green]\n"
        f"\n[dim]Wallet: {result['btc_balance']:,.6f} BTC  "
        f"Credits: {result['new_credits']:,.1f}[/dim]",
        title="[bold #F7931A]\u26cf  Active Mining Cycle Complete!",
        border_style="#F7931A",
    ))


def show_collect(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    color = "red" if result["shutdown"] else "green"
    ah = int(result["avg_hours"])
    am = int((result["avg_hours"] - ah) * 60)

    text = (
        f"[bold]Rigs Collected:[/bold] {result['rigs_collected']} (avg {ah}h {am}m)\n"
        f"[bold]Collected:[/bold] {result['btc_collected']:,.6f} El Virtual\n"
        f"[bold]Electricity Bill:[/bold] {result['elec_paid']:,.4f} credits\n"
        f"[bold]Net Value:[/bold] {result['net_value']:,.2f} credits (at {result['btc_price']:,.2f}/BTC)"
    )

    if result["shutdown"]:
        text += (
            f"\n\n[bold red]INSUFFICIENT FUNDS \u2014 all rigs shut down![/bold red]\n"
            f"Could only afford {result['elec_paid']:,.4f} of {result['full_elec']:,.4f} electricity.\n"
            f"Received {result['btc_collected']:,.6f} of {result['full_btc']:,.6f} BTC (partial)."
        )

    env = result["env"]
    text += (
        f"\n\n[green]Energy: {env['kwh']:,.2f} kWh  CO\u2082: {env['co2_kg']:,.2f} kg  "
        f"Trees: {env['trees_negated']:,.2f}  Rainforest: {env['rainforest_hectares']:.6f} ha[/green]"
        f"\n[italic]Was it worth it?[/italic]"
        f"\n\n[dim]Wallet: {result['btc_balance']:,.6f} BTC  Credits: {result['new_credits']:,.1f}[/dim]"
    )

    console.print(Panel(text, title=f"[bold {color}]\u26a1 Mining Collection", border_style=color))


def show_btc_price(info):
    trend_colors = {"BULL": "green", "BEAR": "red", "STABLE": "#F7931A"}
    c = trend_colors.get(info["trend"], "white")

    console.print(Panel.fit(
        f"[bold]Current Price:[/bold] {info['price']:,.2f} credits/BTC\n"
        f"[bold]Trend:[/bold] [{c}]{info['trend']}[/{c}]\n"
        f"[bold]Base:[/bold] {info['base_price']:,.2f}  "
        f"[bold]Range:[/bold] {info['min_price']:,.0f} - {info['max_price']:,.0f}",
        title="[bold #F7931A]\U0001f4b1 El Virtual Exchange",
        border_style="#F7931A",
    ))


def show_buy_btc(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return
    console.print(
        f"[bold green]\U0001f4b0 Purchased![/bold green] "
        f"Spent {result['spent']:,.2f} credits at {result['price']:,.2f} cr/BTC "
        f"\u2192 received [bold]{result['btc_bought']:,.6f}[/bold] BTC"
    )
    console.print(f"  [dim]Wallet: {result['new_btc']:,.6f} BTC  Credits: {result['new_credits']:,.1f}[/dim]")


def show_sell_btc(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return
    console.print(
        f"[bold green]\U0001f4b5 Sold![/bold green] "
        f"Sold {result['sold']:,.6f} BTC at {result['price']:,.2f} cr/BTC "
        f"\u2192 received [bold]{result['payout']:,.2f}[/bold] credits"
    )
    console.print(f"  [dim]Wallet: {result['new_btc']:,.6f} BTC  Credits: {result['new_credits']:,.1f}[/dim]")


def show_wallet(data):
    console.print(Panel.fit(
        f"[bold]El Virtual:[/bold] {data['btc_balance']:,.6f} BTC\n"
        f"[bold]Market Value:[/bold] {data['market_value']:,.2f} credits\n"
        f"[bold]Social Credits:[/bold] {data['credits']:,.1f}\n"
        f"[bold]BTC Price:[/bold] {data['btc_price']:,.2f} credits/BTC",
        title="[bold #F7931A]\U0001f4b0 Wallet",
        border_style="#F7931A",
    ))


def _fmt_cooldown_short(seconds):
    """Return 'READY' or a compact 'Xh Ym' / 'Ym Zs' string."""
    if seconds <= 0:
        return "[bold green]READY[/bold green]"
    s = int(seconds)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"[yellow]{h}h {m}m[/yellow]"
    elif m:
        return f"[yellow]{m}m {s}s[/yellow]"
    return f"[yellow]{s}s[/yellow]"


def show_status(data):
    cd = data["cooldowns"]
    scav_str = _fmt_cooldown_short(cd["scavenge"])
    mine_str = _fmt_cooldown_short(cd["mine"])

    # Rig status indicator
    if data["rigs_online"] > 0:
        rig_str = f"[green]{data['rigs_online']} online[/green]"
    else:
        rig_str = f"[dim]{data['rigs_online']} online[/dim]"
    if data["rigs_offline"] > 0:
        rig_str += f"  [red]{data['rigs_offline']} offline[/red]"

    # Pending earnings line — only show if rigs are actually running
    pending_line = ""
    if data["rigs_online"] > 0:
        pending_line = (
            f"\n  [dim]Pending:[/dim]  "
            f"{data['pending_btc']:,.6f} BTC "
            f"[dim]/ elec bill {data['pending_elec']:,.4f} cr[/dim]"
        )

    body = (
        f"  [bold]Credits:[/bold]    {data['credits']:,.1f}\n"
        f"  [bold]El Virtual:[/bold] {data['btc_balance']:,.6f} BTC"
        f"  [dim]({data['market_value']:,.2f} cr @ {data['btc_price']:,.2f})[/dim]\n"
        f"\n"
        f"  [bold]Rigs:[/bold]       {rig_str}  [dim]({data['rigs_total']} total)[/dim]"
        f"{pending_line}\n"
        f"  [bold]Inventory:[/bold]  {data['inv_count']} parts\n"
        f"\n"
        f"  \U0001f5d1  Scavenge:  {scav_str}\n"
        f"  \u26cf  Mine:      {mine_str}"
    )

    console.print(Panel(body, title="[bold cyan]\u26a1 Status", border_style="cyan", padding=(0, 1)))


def show_log(records):
    if not records:
        console.print("[dim]No events logged yet. Play a bit first![/dim]")
        return

    # Icon + label lookup, with a fallback
    _META = EventLog._META

    table = Table(
        title=f"\U0001f4dc Event Log  [dim](last {len(records)} events)[/dim]",
        box=box.SIMPLE,
        title_style="bold cyan",
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )
    table.add_column("Time",    width=9,  style="dim")
    table.add_column("",        width=2)
    table.add_column("Event",   width=14)
    table.add_column("Summary", min_width=40)

    import datetime
    now = time.time()
    for rec in records:
        age = now - rec["ts"]
        if age < 60:
            time_str = "just now"
        elif age < 3600:
            time_str = f"{int(age//60)}m ago"
        elif age < 86400:
            time_str = f"{int(age//3600)}h ago"
        else:
            dt = datetime.datetime.fromtimestamp(rec["ts"])
            time_str = dt.strftime("%d %b")

        icon, label = _META.get(rec["type"], ("·", rec["type"]))
        table.add_row(time_str, icon, label, rec["summary"])

    console.print(table)


def show_market(data):
    table = Table(
        title="\U0001f3ea Black Market Parts Dealer",
        box=box.ROUNDED,
        title_style="bold",
    )
    table.add_column("Slot", width=4, justify="center")
    table.add_column("", width=2)
    table.add_column("Part", min_width=25)
    table.add_column("Year", width=6)
    table.add_column("Type", width=12)
    table.add_column("Score", justify="right", width=10)
    table.add_column("TDP", justify="right", width=10)
    table.add_column("Rarity", width=12)
    table.add_column("Price (BTC)", justify="right", width=14)

    for item in data["items"]:
        hw = item["hw"]
        rarity = item["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        table.add_row(
            str(item["slot"]),
            item["emoji"],
            f"[{style}]{hw['name']}[/{style}]",
            str(hw.get("year", "?")),
            hw.get("type", "?"),
            fmt_score(item['score']),
            fmt_watts(hw.get('tdp_watts', 0)),
            f"[{style}]{rarity.title()}[/{style}]",
            f"{item['btc_price']:,.6f}",
        )

    console.print(table)
    remaining = data["refresh_remaining"]
    h, r = divmod(remaining, 3600)
    m, s = divmod(r, 60)
    console.print(
        f"  Your wallet: [bold]{data['btc_balance']:,.6f}[/bold] BTC  "
        f"[dim]Stock refreshes in {h}h {m}m {s}s[/dim]"
    )
    console.print("  [dim]Use 'buy <slot>' or 'buy <slot>,<slot>' to purchase[/dim]")


def show_buy_parts(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    for item in result["bought"]:
        hw = item["hw"]
        rarity = item["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        console.print(
            f"  [{style}]{item['emoji']} {hw['name']} ({hw['year']})[/{style}] "
            f"Score: {fmt_score(item['score'])}  {item['btc_price']:,.6f} BTC"
        )

    console.print(
        f"\n  Total: [bold]{result['total_cost']:,.6f}[/bold] BTC  "
        f"Wallet: [bold]{result['new_btc']:,.6f}[/bold] BTC"
    )


def show_sell_part(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    hw = result["hw"]
    rarity = result["rarity"]
    style = RARITY_STYLE.get(rarity, "white")
    console.print(
        f"[bold green]\U0001f4b0 Sold![/bold green] "
        f"{result['emoji']} [{style}]{hw['name']}[/{style}] ({hw['year']}) "
        f"\u2192 [bold]{result['sell_price']:,.6f}[/bold] BTC "
        f"(\u2248 {result['credit_value']:,.2f} credits)"
    )
    console.print(f"  [dim]Wallet: {result['new_btc']:,.6f} BTC[/dim]")


def show_build_rig(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    console.print(Panel.fit(
        f"[bold green]\u26a1 Rig Assembled: {result['name']}[/bold green]",
        border_style="green",
    ))

    for p in result["parts"]:
        hw = p["hw"]
        rarity = p["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        console.print(
            f"  {p['emoji']} [{style}]{hw['name']}[/{style}] ({hw['year']}) "
            f"Score: {fmt_score(p['score'])}"
        )

    console.print(
        f"\n  [bold]Compute Score:[/bold] {fmt_score(result['total_score'])}  "
        f"[bold]Power:[/bold] {fmt_watts(result['total_watts'])}  "
        f"[bold]Elec Cost:[/bold] {result['elec_per_hr']:,.4f} cr/hr  "
        f"[bold]Rigs:[/bold] {result['rig_count']}"
    )
    console.print("  [dim]Use 'toggle <name>' to start mining![/dim]")


def show_scrap_rig(result):
    if not result["ok"]:
        console.print(f"[yellow]{result['error']}[/yellow]")
        return

    parts_text = ", ".join(result["parts_returned"])
    console.print(f"[bold red]\U0001f527 Rig Scrapped:[/bold red] {result['name']}")
    console.print(f"  {len(result['parts_returned'])} parts returned: {parts_text}")
    if result["btc_collected"] > 0:
        console.print(
            f"  Final collection: +{result['btc_collected']:,.6f} BTC, "
            f"-{result['elec_paid']:,.4f} electricity"
        )


# =============================================================================
# HELP TEXT
# =============================================================================

def show_help():
    help_table = Table(
        title="\U0001f5d1  Trash Collector \u2014 Commands",
        box=box.ROUNDED,
        title_style="bold cyan",
        show_header=True,
        header_style="bold",
    )
    help_table.add_column("Command", style="bold cyan", min_width=30)
    help_table.add_column("Description")

    commands = [
        ("scavenge", "Dig through e-waste to find hardware (2hr cooldown)"),
        ("parts [page]", "View your hardware inventory"),
        ("build <name>", "Build a rig from 5 parts (interactive selection)"),
        ("build_all [prefix]",  "Auto-build as many rigs as possible (names: prefix_1, prefix_2…)"),
        ("auto_build [prefix]", "Smart build: greedy diversity+combo optimiser. Costs 10% BTC fee."),
        ("rigs", "Overview of all your mining rigs"),
        ("rig <name>", "Detailed view of a specific rig"),
        ("toggle <name>", "Turn a rig on/off"),
        ("toggle_all <on|off>", "Turn all rigs on or off"),
        ("mine", "Active mining cycle for 2x bonus (1hr cooldown)"),
        ("collect", "Collect accumulated BTC and pay electricity"),
        ("scrap <name>", "Disassemble a rig, return parts to inventory"),
        ("scrap_all", "Scrap every rig and return all parts to inventory"),
        ("scrap_num_rigs <n>", "Scrap the <n> newest rigs first"),
        ("market", "Browse the black market for parts"),
        ("buy <slot[,slot,...]>", "Buy parts from the market"),
        ("sell_part <id>", "Sell an inventory part for BTC"),
        ("sell_part_all", "Sell every part in inventory for BTC"),
        ("price", "Check El Virtual exchange rate"),
        ("buy_btc <credits>", "Buy El Virtual with Social Credits"),
        ("sell_btc <amount>", "Sell El Virtual for Social Credits"),
        ("wallet", "Check your balances"),
        ("status", "Quick dashboard: credits, rigs, cooldowns at a glance"),
        ("log / history", "Show last 20 game events across sessions"),
        ("help", "Show this help"),
        ("quit / exit", "Exit the game"),
    ]
    for cmd, desc in commands:
        help_table.add_row(cmd, desc)

    console.print(help_table)


# =============================================================================
# BUILD RIG INTERACTIVE FLOW
# =============================================================================

def interactive_build_rig(engine, name):
    """Interactive part selection for building a rig."""
    parts = engine.get_parts(sort_by="score")
    if len(parts) < PARTS_PER_RIG:
        console.print(f"[yellow]You need at least {PARTS_PER_RIG} parts. "
                      f"You have {len(parts)}. Use 'scavenge' to find more![/yellow]")
        return

    # Show available parts
    console.print(f"\n[bold]Select {PARTS_PER_RIG} parts for rig '[cyan]{name}[/cyan]'[/bold]")
    console.print("[dim]Enter part IDs separated by spaces (e.g. '42 17 89 3 56')[/dim]\n")

    # Show top 25 parts
    display = parts[:25]
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("ID", width=6)
    table.add_column("")
    table.add_column("Name", min_width=25)
    table.add_column("Year", width=6)
    table.add_column("Type", width=10)
    table.add_column("Score", justify="right", width=10)
    table.add_column("TDP", justify="right", width=10)
    table.add_column("Rarity", width=12)

    for item in display:
        hw = item["hw"]
        rarity = item["rarity"]
        style = RARITY_STYLE.get(rarity, "white")
        table.add_row(
            str(item["inv_id"]),
            item["emoji"],
            f"[{style}]{hw['name']}[/{style}]",
            str(hw.get("year", "?")),
            hw.get("type", "?"),
            fmt_score(item['score']),
            fmt_watts(hw.get('tdp_watts', 0)),
            f"[{style}]{rarity.title()}[/{style}]",
        )
    console.print(table)

    if len(parts) > 25:
        console.print(f"  [dim]Showing top 25 of {len(parts)} parts by score[/dim]")

    # Build a completer for part IDs
    class PartIDCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            words = text.split()
            partial = "" if text.endswith(" ") else (words[-1] if words else "")
            already = set(words[:-1]) if not text.endswith(" ") else set(words)
            for item in display:
                sid = str(item["inv_id"])
                if sid in already:
                    continue
                if sid.startswith(partial):
                    hw = item["hw"]
                    yield Completion(sid, start_position=-len(partial),
                                     display_meta=f"{hw['name']} ({item['rarity']})")

    try:
        build_session = PromptSession()
        raw = build_session.prompt(
            HTML("<ansicyan><b>Part IDs (5 required): </b></ansicyan>"),
            completer=PartIDCompleter(),
            style=_PT_STYLE,
        )
        ids = [int(x.strip()) for x in raw.split() if x.strip()]
    except (ValueError, KeyboardInterrupt, EOFError):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    if len(ids) != PARTS_PER_RIG:
        console.print(f"[yellow]You must select exactly {PARTS_PER_RIG} parts. Got {len(ids)}.[/yellow]")
        return

    result = engine.build_rig(name, ids)
    show_build_rig(result)


# =============================================================================
# MAIN LOOP
# =============================================================================

def main():
    # Change to the exe/script directory so CSVs are found.
    # When frozen by PyInstaller, __file__ points into a temporary _MEIPASS
    # extraction folder — use sys.executable's directory instead.
    import sys as _sys
    if getattr(_sys, "frozen", False):
        _base_dir = os.path.dirname(os.path.abspath(_sys.executable))
    else:
        _base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(_base_dir)

    console.print()
    console.print(Panel.fit(
        "[bold]\U0001f5d1  TRASH COLLECTOR[/bold]\n"
        "[dim]Standalone Terminal Edition[/dim]\n\n"
        "Scavenge vintage hardware. Build mining rigs.\n"
        "Mine El Virtual. Destroy the planet.\n"
        "Feel appropriately guilty.\n\n"
        "[dim]Type 'help' for commands. Tab to autocomplete.[/dim]",
        border_style="bold cyan",
    ))
    console.print()

    engine = TrashCollectorEngine()

    # ── Event log, milestones + background monitor ────────────────────
    data_dir = os.path.join(os.path.expanduser("~"), ".trash_collector")
    os.makedirs(data_dir, exist_ok=True)
    log       = EventLog(os.path.join(data_dir, "event_log.jsonl"))
    milestones = MilestoneTracker(os.path.join(data_dir, "milestones.json"))
    monitor   = BackgroundMonitor(engine)
    monitor.start()

    # ── prompt_toolkit session with history + tab completion ──────────
    history_dir = data_dir
    history_path = os.path.join(history_dir, "command_history")

    completer = FuzzyCompleter(TrashCompleter(engine))
    session = PromptSession(
        history=FileHistory(history_path),
        completer=completer,
        complete_while_typing=False,   # only complete on Tab press
        style=_PT_STYLE,
    )

    def _make_prompt():
        """Called fresh each time the prompt is displayed — reads live cooldowns."""
        cd = engine.get_cooldowns()

        def _cd_token(seconds, icon):
            if seconds <= 0:
                return f"<ansigreen>{icon} RDY</ansigreen>"
            s = int(seconds)
            h, s = divmod(s, 3600)
            m, s = divmod(s, 60)
            if h:
                label = f"{h}h{m:02d}m"
            elif m:
                label = f"{m}m{s:02d}s"
            else:
                label = f"{s}s"
            return f"<ansiyellow>{icon} {label}</ansiyellow>"

        scav = _cd_token(cd["scavenge"], "\U0001f5d1")
        mine = _cd_token(cd["mine"],     "\u26cf")
        return HTML(f"<ansicyan><b>trash</b></ansicyan> [{scav} <ansiwhite>|</ansiwhite> {mine}]<ansicyan><b>&gt;</b></ansicyan> ")

    # patch_stdout lets the background monitor thread print above the prompt
    # without garbling it
    with patch_stdout(raw=True):
        while True:
            try:
                raw = session.prompt(_make_prompt).strip()
            except KeyboardInterrupt:
                continue  # Ctrl-C at prompt just clears the line
            except EOFError:
                console.print("\n[dim]Goodbye, eco-terrorist.[/dim]")
                monitor.stop()
                break

            if not raw:
                continue

            # Parse command
            try:
                tokens = shlex.split(raw)
            except ValueError:
                tokens = raw.split()

            cmd = tokens[0].lower()
            args = tokens[1:]

            try:
                if cmd in ("quit", "exit", "q"):
                    console.print("[dim]Goodbye, eco-terrorist.[/dim]")
                    monitor.stop()
                    break

                elif cmd == "help":
                    show_help()

                elif cmd == "gofast":
                    engine.reset_cooldowns()
                    console.print("[dim]Cooldowns cleared.[/dim]")

                elif cmd == "scavenge":
                    result = engine.scavenge()
                    show_scavenge(result)
                    if result["ok"]:
                        names = ", ".join(f["hw"]["name"] for f in result["finds"])
                        log.append("scavenge", f"Found {len(result['finds'])}: {names}")

                elif cmd in ("parts", "inventory", "inv"):
                    page = 0
                    if args:
                        try:
                            page = int(args[0]) - 1
                        except ValueError:
                            pass
                    show_parts(engine.get_parts(sort_by="score"), page=page)

                elif cmd == "build":
                    if not args:
                        console.print("[yellow]Usage: build <rig name>[/yellow]")
                    else:
                        name = " ".join(args)
                        before = engine.mdb.count_rigs(engine.uid, engine.gid)
                        interactive_build_rig(engine, name)
                        after = engine.mdb.count_rigs(engine.uid, engine.gid)
                        if after > before:
                            log.append("build", f"Built rig '{name}'")

                elif cmd == "rigs":
                    show_rigs_overview(engine.get_all_rigs_overview())

                elif cmd == "rig":
                    if not args:
                        console.print("[yellow]Usage: rig <name>[/yellow]")
                    else:
                        show_rig_detail(engine.get_rig_detail(" ".join(args)))

                elif cmd == "toggle":
                    if not args:
                        console.print("[yellow]Usage: toggle <rig name>[/yellow]")
                    else:
                        result = engine.toggle_rig(" ".join(args))
                        show_toggle(result)
                        if result["ok"]:
                            log.append("toggle", f"'{result['name']}' → {result['new_state']}")

                elif cmd == "toggle_all":
                    if not args or args[0].lower() not in ("on", "off"):
                        console.print("[yellow]Usage: toggle_all <on|off>[/yellow]")
                    else:
                        result = engine.toggle_all_rigs(args[0].lower() == "on")
                        show_toggle_all(result)
                        if result["ok"]:
                            log.append("toggle_all", f"{len(result['toggled'])} rigs → {result['new_state']}")

                elif cmd == "mine":
                    result = engine.mine()
                    show_mine(result)
                    if result["ok"]:
                        log.append("mine", f"+{result['btc_mined']:,.6f} BTC  -{result['elec_cost']:,.4f} cr elec")
                        _env = engine.env_from_lifetime()
                        show_milestones(milestones.check(_env))

                elif cmd == "collect":
                    result = engine.collect_btc()
                    show_collect(result)
                    if result["ok"]:
                        suffix = "  ⚠ SHUTDOWN" if result["shutdown"] else ""
                        log.append("collect", f"+{result['btc_collected']:,.6f} BTC  -{result['elec_paid']:,.4f} cr elec{suffix}")
                        _env = engine.env_from_lifetime()
                        show_milestones(milestones.check(_env))

                elif cmd == "scrap":
                    if not args:
                        console.print("[yellow]Usage: scrap <rig name>[/yellow]")
                    else:
                        result = engine.scrap_rig(" ".join(args))
                        show_scrap_rig(result)
                        if result["ok"]:
                            log.append("scrap", f"Scrapped '{result['name']}', {len(result['parts_returned'])} parts returned")

                elif cmd == "scrap_all":
                    results = engine.scrap_all()
                    if not results:
                        console.print("[yellow]No rigs to scrap.[/yellow]")
                    else:
                        total_parts = 0
                        for r in results:
                            show_scrap_rig(r)
                            if r["ok"]:
                                total_parts += len(r["parts_returned"])
                        console.print(f"\n[bold red]🔧 Scrapped {len(results)} rig(s), {total_parts} parts returned to inventory.[/bold red]")
                        log.append("scrap_all", f"Scrapped {len(results)} rigs, {total_parts} parts returned")

                elif cmd == "scrap_num_rigs":
                    if not args:
                        console.print("[yellow]Usage: scrap_num_rigs <number>[/yellow]")
                    else:
                        try:
                            n = int(args[0])
                            if n <= 0:
                                raise ValueError
                            results = engine.scrap_num_rigs(n)
                            if not results:
                                console.print("[yellow]No rigs to scrap.[/yellow]")
                            else:
                                total_parts = 0
                                for r in results:
                                    show_scrap_rig(r)
                                    if r["ok"]:
                                        total_parts += len(r["parts_returned"])
                                console.print(f"\n[bold red]🔧 Scrapped {len(results)} rig(s), {total_parts} parts returned to inventory.[/bold red]")
                                log.append("scrap_num_rigs", f"Scrapped {len(results)} newest rigs, {total_parts} parts returned")
                        except ValueError:
                            console.print("[yellow]Please provide a valid positive number.[/yellow]")

                elif cmd == "build_all":
                    prefix = args[0] if args else "auto"
                    results = engine.build_all(name_prefix=prefix)
                    if not results:
                        console.print("[yellow]Not enough parts to build any rigs (need 5).[/yellow]")
                    else:
                        for r in results:
                            show_build_rig(r)
                        built = [r for r in results if r["ok"]]
                        console.print(f"\n[bold green]⚡ Built {len(built)} rig(s).[/bold green]")
                        if built:
                            log.append("build_all", f"Auto-built {len(built)} rigs with prefix '{prefix}'")

                elif cmd == "auto_build":
                    prefix = args[0] if args else "Smart-Rig"
                    result = engine.auto_build(name_prefix=prefix)
                    if result["rigs_built"] == 0:
                        console.print("[yellow]Not enough parts to build any rigs (need 5).[/yellow]")
                    else:
                        btc_price = engine._get_btc_price()
                        fee_creds = result["fee_charged"] * btc_price
                        console.print(
                            f"\n[bold yellow]🤖 AI Consultant Fee:[/bold yellow] "
                            f"{result['fee_charged']:,.6f} BTC "
                            f"(≈ {fee_creds:,.2f} credits)"
                        )
                        console.print(
                            f"[bold green]⚡ Built {result['rigs_built']} rig(s) "
                            f"({result['parts_left']} parts left in inventory)[/bold green]"
                        )
                        for r in result["rigs"]:
                            types  = sorted({hw.get("type", "?") for hw in r["parts_hw"]})
                            base   = sum(compute_score(hw) for hw in r["parts_hw"])
                            div    = diversity_multiplier(r["parts_hw"])
                            leg    = legendary_multiplier(r["parts_hw"])
                            cmult, cname, _ = combo_multiplier(r["parts_hw"])
                            total  = base * div * leg * cmult
                            combo_str = f"  🧬 {cname}" if cname else ""
                            console.print(
                                f"  [cyan]{r['name']}[/cyan] — "
                                f"Score: [bold]{total:,.0f}[/bold]  "
                                f"Types: {'+'.join(types)}{combo_str}"
                            )
                        log.append(
                            "auto_build",
                            f"Smart-built {result['rigs_built']} rigs, "
                            f"fee {result['fee_charged']:,.6f} BTC",
                        )

                elif cmd == "sell_part_all":
                    result = engine.sell_part_all()
                    if not result["ok"]:
                        console.print("[yellow]No parts in inventory to sell.[/yellow]")
                    else:
                        console.print(
                            f"[bold green]💰 Sold {result['sold']:,} part(s) for "
                            f"{result['total_btc']:,.6f} BTC "
                            f"(≈ {result['credit_value']:,.2f} credits)[/bold green]"
                        )
                        for rarity, count in sorted(
                            result["by_rarity"].items(),
                            key=lambda x: RARITY_ORDER.index(x[0]) if x[0] in RARITY_ORDER else 99
                        ):
                            emoji = RARITY_EMOJI.get(rarity, "⚪")
                            console.print(f"  {emoji} {rarity.title()}: {count:,}")
                        log.append("sell_part_all",
                                   f"Sold {result['sold']} parts +{result['total_btc']:,.6f} BTC")

                elif cmd == "market":
                    show_market(engine.get_market())

                elif cmd == "buy":
                    if not args:
                        console.print("[yellow]Usage: buy <slot> or buy <slot,slot,...>[/yellow]")
                    else:
                        try:
                            slots = [int(x.strip()) for x in args[0].split(",")]
                            result = engine.buy_parts(slots)
                            show_buy_parts(result)
                            if result["ok"]:
                                names = ", ".join(i["hw"]["name"] for i in result["bought"])
                                log.append("buy_part", f"Bought {len(result['bought'])} part(s): {names}  -{result['total_cost']:,.6f} BTC")
                        except ValueError:
                            console.print("[yellow]Invalid slot number(s).[/yellow]")

                elif cmd == "sell_part":
                    if not args:
                        console.print("[yellow]Usage: sell_part <part ID>[/yellow]")
                    else:
                        try:
                            result = engine.sell_part(int(args[0]))
                            show_sell_part(result)
                            if result["ok"]:
                                log.append("sell_part", f"Sold {result['hw']['name']} ({result['rarity']})  +{result['sell_price']:,.6f} BTC")
                        except ValueError:
                            console.print("[yellow]Invalid part ID.[/yellow]")

                elif cmd == "price":
                    show_btc_price(engine.get_btc_price_info())

                elif cmd == "buy_btc":
                    if not args:
                        console.print("[yellow]Usage: buy_btc <credit amount>[/yellow]")
                    else:
                        try:
                            result = engine.buy_btc(float(args[0]))
                            show_buy_btc(result)
                            if result["ok"]:
                                log.append("buy_btc", f"Bought {result['btc_bought']:,.6f} BTC for {result['spent']:,.2f} cr @ {result['price']:,.2f}")
                        except ValueError:
                            console.print("[yellow]Invalid amount.[/yellow]")

                elif cmd == "sell_btc":
                    if not args:
                        console.print("[yellow]Usage: sell_btc <BTC amount>[/yellow]")
                    else:
                        try:
                            result = engine.sell_btc(float(args[0]))
                            show_sell_btc(result)
                            if result["ok"]:
                                log.append("sell_btc", f"Sold {result['sold']:,.6f} BTC for {result['payout']:,.2f} cr @ {result['price']:,.2f}")
                        except ValueError:
                            console.print("[yellow]Invalid amount.[/yellow]")

                elif cmd == "wallet":
                    show_wallet(engine.get_wallet())

                elif cmd == "status":
                    show_status(engine.get_status())

                elif cmd in ("log", "history"):
                    show_log(log.read_last(20))

                else:
                    console.print(f"[yellow]Unknown command: '{cmd}'. Type 'help' for commands.[/yellow]")

            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")


if __name__ == "__main__":
    main()
