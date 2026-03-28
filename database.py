import sqlite3

class CreditDB:
    def __init__(self, db_path="social_credit.db"):
        self.db_path = db_path
        self._create_tables()

    def _create_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            # Main economy table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS economy (
                    user_id INTEGER,
                    guild_id INTEGER,
                    social_credit REAL DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            # Banned words table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS banned_words (
                    guild_id INTEGER,
                    word TEXT,
                    penalty REAL,
                    PRIMARY KEY (guild_id, word)
                )
            """)
            # Praised words table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS praised_words (
                    guild_id INTEGER,
                    word TEXT,
                    reward REAL,
                    PRIMARY KEY (guild_id, word)
                )
            """)
            # Guild-specific settings
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    output_channel_id INTEGER
                )
            """)
            
            # --- Safely add new columns to guild_settings ---
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(guild_settings)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'slush_fund' not in columns:
                conn.execute("ALTER TABLE guild_settings ADD COLUMN slush_fund REAL NOT NULL DEFAULT 0")

            # Lottery tickets table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lottery_tickets (
                    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL
                )
            """)

            conn.commit()

    def get_credit(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT social_credit FROM economy WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
            row = cursor.fetchone()
            return row[0] if row else 0.0

    def update_credit(self, user_id, guild_id, amount):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO economy (user_id, guild_id, social_credit) 
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET 
                social_credit = social_credit + EXCLUDED.social_credit
            """, (user_id, guild_id, amount))
            conn.commit()

    def reset_score(self, user_id, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO economy (user_id, guild_id, social_credit) 
                VALUES (?, ?, 0)
                ON CONFLICT(user_id, guild_id) DO UPDATE SET 
                social_credit = 0
            """, (user_id, guild_id))
            conn.commit()

    def get_leaderboard(self, guild_id, top_n=10, bottom_n=3):
        with sqlite3.connect(self.db_path) as conn:
            top_cursor = conn.execute("SELECT user_id, social_credit FROM economy WHERE guild_id = ? ORDER BY social_credit DESC LIMIT ?", (guild_id, top_n))
            top_users = top_cursor.fetchall()
            
            bottom_cursor = conn.execute("SELECT user_id, social_credit FROM economy WHERE guild_id = ? AND social_credit < 0 ORDER BY social_credit ASC LIMIT ?", (guild_id, bottom_n))
            bottom_users = bottom_cursor.fetchall()
            
            return top_users, bottom_users

    # --- Banned Words Methods ---
    def add_banned_word(self, guild_id, word, penalty):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO banned_words (guild_id, word, penalty) VALUES (?, ?, ?)", (guild_id, word.lower(), penalty))
            conn.commit()

    def remove_banned_word(self, guild_id, word):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM banned_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower()))
            conn.commit()
            return cursor.rowcount > 0

    def get_banned_words(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT word, penalty FROM banned_words WHERE guild_id = ?", (guild_id,))
            return cursor.fetchall()

    # --- Praised Words Methods ---
    def add_praised_word(self, guild_id, word, reward):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO praised_words (guild_id, word, reward) VALUES (?, ?, ?)", (guild_id, word.lower(), reward))
            conn.commit()

    def remove_praised_word(self, guild_id, word):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM praised_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower()))
            conn.commit()
            return cursor.rowcount > 0

    def get_praised_words(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT word, reward FROM praised_words WHERE guild_id = ?", (guild_id,))
            return cursor.fetchall()

    # --- Guild Settings Methods ---
    def set_output_channel(self, guild_id, channel_id):
        with sqlite3.connect(self.db_path) as conn:
            # Use INSERT OR IGNORE + UPDATE to avoid overwriting other settings
            conn.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            conn.execute("UPDATE guild_settings SET output_channel_id = ? WHERE guild_id = ?", (channel_id, guild_id))
            conn.commit()

    def get_output_channel(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT output_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
            row = cursor.fetchone()
            # Handle case where column might exist but is NULL
            return row[0] if row and row[0] is not None else None

    # --- Slush Fund Methods ---
    def get_slush_fund(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT slush_fund FROM guild_settings WHERE guild_id = ?", (guild_id,))
            row = cursor.fetchone()
            return row[0] if row else 0.0

    def add_to_slush_fund(self, guild_id, amount):
        with sqlite3.connect(self.db_path) as conn:
            # Ensure a row exists before trying to update it
            conn.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            # Now safely update the slush fund
            conn.execute("UPDATE guild_settings SET slush_fund = slush_fund + ? WHERE guild_id = ?", (amount, guild_id))
            conn.commit()

    # --- Lottery Methods ---
    def add_lottery_tickets(self, guild_id, user_id, ticket_count):
        with sqlite3.connect(self.db_path) as conn:
            tickets = [(guild_id, user_id) for _ in range(ticket_count)]
            conn.executemany("INSERT INTO lottery_tickets (guild_id, user_id) VALUES (?, ?)", tickets)
            conn.commit()

    def count_lottery_tickets(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM lottery_tickets WHERE guild_id = ?", (guild_id,))
            return cursor.fetchone()[0]

    def get_user_ticket_count(self, guild_id, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM lottery_tickets WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            return cursor.fetchone()[0]

    def get_all_lottery_entries(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT user_id FROM lottery_tickets WHERE guild_id = ?", (guild_id,))
            return [row[0] for row in cursor.fetchall()]

    def clear_lottery_tickets(self, guild_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM lottery_tickets WHERE guild_id = ?", (guild_id,))
            conn.commit()