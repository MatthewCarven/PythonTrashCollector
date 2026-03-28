import sqlite3
import time


class MiningDB:
    def __init__(self, db_path="mining.db"):
        self.db_path = db_path
        self._create_tables()

    def _create_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hardware_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    hardware_id TEXT NOT NULL,
                    acquired_at REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_rigs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    is_running INTEGER DEFAULT 0,
                    started_at REAL,
                    last_collected REAL,
                    total_btc_mined REAL DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS rig_components (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rig_id INTEGER NOT NULL,
                    hardware_id TEXT NOT NULL,
                    FOREIGN KEY (rig_id) REFERENCES mining_rigs(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS btc_wallet (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    balance REAL DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS btc_market (
                    guild_id INTEGER PRIMARY KEY,
                    current_price REAL DEFAULT 50.0,
                    last_updated REAL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_cooldowns (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    last_used REAL NOT NULL,
                    PRIMARY KEY (user_id, guild_id, command)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS environmental_ledger (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    total_kwh REAL DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS parts_market (
                    guild_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    hardware_id TEXT NOT NULL,
                    btc_price REAL NOT NULL,
                    PRIMARY KEY (guild_id, slot)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS parts_market_refresh (
                    guild_id INTEGER PRIMARY KEY,
                    last_refreshed REAL NOT NULL
                )
            """)

            conn.commit()

    # ── Inventory ────────────────────────────────────────────────────────

    def get_hardware_by_id(self, inv_id, user_id, guild_id):
        """Return the hardware_id for a single inventory row, or None."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT hardware_id FROM hardware_inventory WHERE id = ? AND user_id = ? AND guild_id = ?",
                (inv_id, user_id, guild_id),
            ).fetchone()
            return row[0] if row else None

    def add_hardware(self, user_id, guild_id, hardware_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO hardware_inventory (user_id, guild_id, hardware_id, acquired_at) VALUES (?, ?, ?, ?)",
                (user_id, guild_id, hardware_id, time.time()),
            )
            conn.commit()

    def get_inventory(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, hardware_id FROM hardware_inventory WHERE user_id = ? AND guild_id = ? ORDER BY acquired_at DESC",
                (user_id, guild_id),
            )
            return cursor.fetchall()

    def transfer_hardware(self, inv_id, from_user_id, guild_id, to_user_id):
        """Transfer a hardware item to another user. Returns True on success."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT hardware_id FROM hardware_inventory WHERE id = ? AND user_id = ? AND guild_id = ?",
                (inv_id, from_user_id, guild_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE hardware_inventory SET user_id = ? WHERE id = ? AND user_id = ? AND guild_id = ?",
                (to_user_id, inv_id, from_user_id, guild_id),
            )
            conn.commit()
            return True

    def remove_hardware(self, inv_id, user_id, guild_id):
        """Delete a single hardware item by inventory id. Returns the hardware_id or None."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT hardware_id FROM hardware_inventory WHERE id = ? AND user_id = ? AND guild_id = ?",
                (inv_id, user_id, guild_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "DELETE FROM hardware_inventory WHERE id = ?", (inv_id,)
            )
            conn.commit()
            return row[0]

    def remove_hardware_bulk(self, inv_ids, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            placeholders = ",".join("?" for _ in inv_ids)
            conn.execute(
                f"DELETE FROM hardware_inventory WHERE id IN ({placeholders}) AND user_id = ? AND guild_id = ?",
                (*inv_ids, user_id, guild_id),
            )
            conn.commit()

    # ── Rigs ─────────────────────────────────────────────────────────────

    def create_rig(self, user_id, guild_id, name, inventory_ids):
        """Create a rig from inventory items. inventory_ids are hardware_inventory.id values."""
        with sqlite3.connect(self.db_path) as conn:
            now = time.time()
            cursor = conn.execute(
                "INSERT INTO mining_rigs (user_id, guild_id, name, last_collected) VALUES (?, ?, ?, ?)",
                (user_id, guild_id, name, now),
            )
            rig_id = cursor.lastrowid

            for inv_id in inventory_ids:
                row = conn.execute(
                    "SELECT hardware_id FROM hardware_inventory WHERE id = ? AND user_id = ? AND guild_id = ?",
                    (inv_id, user_id, guild_id),
                ).fetchone()
                if row:
                    conn.execute(
                        "INSERT INTO rig_components (rig_id, hardware_id) VALUES (?, ?)",
                        (rig_id, row[0]),
                    )

            placeholders = ",".join("?" for _ in inventory_ids)
            conn.execute(
                f"DELETE FROM hardware_inventory WHERE id IN ({placeholders}) AND user_id = ? AND guild_id = ?",
                (*inventory_ids, user_id, guild_id),
            )

            conn.commit()
            return rig_id

    def get_rigs(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, is_running, started_at, last_collected, total_btc_mined "
                "FROM mining_rigs WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            return cursor.fetchall()

    def get_rig_by_name(self, user_id, guild_id, name):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, is_running, started_at, last_collected, total_btc_mined "
                "FROM mining_rigs WHERE user_id = ? AND guild_id = ? AND LOWER(name) = LOWER(?)",
                (user_id, guild_id, name),
            )
            return cursor.fetchone()

    def get_rig_components(self, rig_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT hardware_id FROM rig_components WHERE rig_id = ?",
                (rig_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def count_rigs(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM mining_rigs WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            return cursor.fetchone()[0]

    def toggle_rig(self, rig_id, user_id, guild_id):
        """Toggle rig on/off. Returns new state (True=running) or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT is_running FROM mining_rigs WHERE id = ? AND user_id = ? AND guild_id = ?",
                (rig_id, user_id, guild_id),
            ).fetchone()
            if not row:
                return None

            new_state = 0 if row[0] else 1
            now = time.time()

            if new_state:
                conn.execute(
                    "UPDATE mining_rigs SET is_running = 1, started_at = ?, last_collected = ? WHERE id = ?",
                    (now, now, rig_id),
                )
            else:
                conn.execute(
                    "UPDATE mining_rigs SET is_running = 0, started_at = NULL WHERE id = ?",
                    (rig_id,),
                )

            conn.commit()
            return bool(new_state)

    def set_rig_running(self, rig_id, user_id, guild_id, running: bool):
        """Set rig to a specific state. Returns previous state or None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT is_running FROM mining_rigs WHERE id = ? AND user_id = ? AND guild_id = ?",
                (rig_id, user_id, guild_id),
            ).fetchone()
            if not row:
                return None

            was_running = bool(row[0])
            now = time.time()

            if running and not was_running:
                conn.execute(
                    "UPDATE mining_rigs SET is_running = 1, started_at = ?, last_collected = ? WHERE id = ?",
                    (now, now, rig_id),
                )
            elif not running and was_running:
                conn.execute(
                    "UPDATE mining_rigs SET is_running = 0, started_at = NULL WHERE id = ?",
                    (rig_id,),
                )

            conn.commit()
            return was_running

    def scrap_rig(self, rig_id, user_id, guild_id):
        """Scrap a rig and return components to inventory. Returns list of hw_ids or None."""
        with sqlite3.connect(self.db_path) as conn:
            rig = conn.execute(
                "SELECT id FROM mining_rigs WHERE id = ? AND user_id = ? AND guild_id = ?",
                (rig_id, user_id, guild_id),
            ).fetchone()
            if not rig:
                return None

            components = conn.execute(
                "SELECT hardware_id FROM rig_components WHERE rig_id = ?",
                (rig_id,),
            ).fetchall()

            now = time.time()
            hw_ids = []
            for (hw_id,) in components:
                conn.execute(
                    "INSERT INTO hardware_inventory (user_id, guild_id, hardware_id, acquired_at) VALUES (?, ?, ?, ?)",
                    (user_id, guild_id, hw_id, now),
                )
                hw_ids.append(hw_id)

            conn.execute("DELETE FROM rig_components WHERE rig_id = ?", (rig_id,))
            conn.execute("DELETE FROM mining_rigs WHERE id = ?", (rig_id,))

            conn.commit()
            return hw_ids

    def update_rig_collection(self, rig_id, btc_mined):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE mining_rigs SET last_collected = ?, total_btc_mined = total_btc_mined + ? WHERE id = ?",
                (time.time(), btc_mined, rig_id),
            )
            conn.commit()

    def shutdown_rig(self, rig_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE mining_rigs SET is_running = 0, started_at = NULL WHERE id = ?",
                (rig_id,),
            )
            conn.commit()

    # ── BTC Wallet ───────────────────────────────────────────────────────

    def get_btc_balance(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT balance FROM btc_wallet WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = cursor.fetchone()
            return row[0] if row else 0.0

    def add_btc(self, user_id, guild_id, amount):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO btc_wallet (user_id, guild_id, balance) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, guild_id) DO UPDATE SET balance = balance + EXCLUDED.balance""",
                (user_id, guild_id, amount),
            )
            conn.commit()

    def remove_btc(self, user_id, guild_id, amount):
        """Remove BTC. Returns True if balance was sufficient."""
        bal = self.get_btc_balance(user_id, guild_id)
        if bal < amount:
            return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE btc_wallet SET balance = balance - ? WHERE user_id = ? AND guild_id = ?",
                (amount, user_id, guild_id),
            )
            conn.commit()
        return True

    # ── BTC Market ───────────────────────────────────────────────────────

    def get_btc_price(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT current_price, last_updated FROM btc_market WHERE guild_id = ?",
                (guild_id,),
            )
            row = cursor.fetchone()
            if row:
                return row[0], row[1]
            return 50.0, time.time()

    def set_btc_price(self, guild_id, price):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO btc_market (guild_id, current_price, last_updated) VALUES (?, ?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET current_price = EXCLUDED.current_price, last_updated = EXCLUDED.last_updated""",
                (guild_id, price, time.time()),
            )
            conn.commit()

    # ── Cooldowns ────────────────────────────────────────────────────────

    def get_cooldown(self, user_id, guild_id, command):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT last_used FROM mining_cooldowns WHERE user_id = ? AND guild_id = ? AND command = ?",
                (user_id, guild_id, command),
            )
            row = cursor.fetchone()
            return row[0] if row else 0.0

    def reset_cooldowns(self, user_id, guild_id):
        """Wipe all cooldowns for a user. Hidden dev command."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM mining_cooldowns WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            conn.commit()

    def set_cooldown(self, user_id, guild_id, command):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO mining_cooldowns (user_id, guild_id, command, last_used) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, guild_id, command) DO UPDATE SET last_used = EXCLUDED.last_used""",
                (user_id, guild_id, command, time.time()),
            )
            conn.commit()

    # ── Environmental Ledger ────────────────────────────────────────────

    def get_total_kwh(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT total_kwh FROM environmental_ledger WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = cursor.fetchone()
            return row[0] if row else 0.0

    def add_kwh(self, user_id, guild_id, kwh):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO environmental_ledger (user_id, guild_id, total_kwh) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, guild_id) DO UPDATE SET total_kwh = total_kwh + EXCLUDED.total_kwh""",
                (user_id, guild_id, kwh),
            )
            conn.commit()

    # ── Parts Market ─────────────────────────────────────────────────────

    def get_market_refresh_time(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT last_refreshed FROM parts_market_refresh WHERE guild_id = ?",
                (guild_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else 0.0

    def set_market_refresh_time(self, guild_id, timestamp):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO parts_market_refresh (guild_id, last_refreshed) VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET last_refreshed = EXCLUDED.last_refreshed""",
                (guild_id, timestamp),
            )
            conn.commit()

    def set_market_stock(self, guild_id, items):
        """items is a list of (slot, hardware_id, btc_price) tuples."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM parts_market WHERE guild_id = ?", (guild_id,))
            conn.executemany(
                "INSERT INTO parts_market (guild_id, slot, hardware_id, btc_price) VALUES (?, ?, ?, ?)",
                [(guild_id, slot, hw_id, price) for slot, hw_id, price in items],
            )
            conn.commit()

    def get_market_stock(self, guild_id):
        """Returns [(slot, hardware_id, btc_price), ...]."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT slot, hardware_id, btc_price FROM parts_market WHERE guild_id = ? ORDER BY slot",
                (guild_id,),
            )
            return cursor.fetchall()

    def remove_market_slot(self, guild_id, slot):
        """Remove a single slot from the market. Returns True if it existed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM parts_market WHERE guild_id = ? AND slot = ?",
                (guild_id, slot),
            )
            conn.commit()
            return cursor.rowcount > 0
