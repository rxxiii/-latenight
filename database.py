"""
Shared SQLite database layer.

Every cog imports `db` (a Database instance) from here and calls its async
methods. Using one shared aiosqlite connection keeps things simple for a
single-process bot.
"""

import json
import os
import time
import aiosqlite

# On Railway we point this at a mounted Volume (e.g. /app/data/bleedclone.sqlite3)
# so the database survives redeploys. Locally, it just defaults to a file in
# this folder.
DB_PATH = os.getenv("DB_PATH", "bleedclone.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    prefix TEXT DEFAULT ',',
    welcome_channel_id INTEGER,
    welcome_message TEXT,
    goodbye_channel_id INTEGER,
    goodbye_message TEXT,
    mute_role_id INTEGER,
    starboard_channel_id INTEGER,
    starboard_threshold INTEGER DEFAULT 3,
    voicemaster_category_id INTEGER,
    voicemaster_join_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    moderator_id INTEGER,
    reason TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS autoresponders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    trigger TEXT,
    response TEXT,
    exact_match INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reaction_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    message_id INTEGER,
    channel_id INTEGER,
    emoji TEXT,
    role_id INTEGER
);

CREATE TABLE IF NOT EXISTS button_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    message_id INTEGER,
    channel_id INTEGER,
    label TEXT,
    role_id INTEGER,
    custom_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS starboard_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    original_message_id INTEGER UNIQUE,
    starboard_message_id INTEGER,
    star_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ticket_panels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    message_id INTEGER,
    category_id INTEGER,
    support_role_id INTEGER,
    panel_title TEXT,
    panel_description TEXT,
    button_label TEXT DEFAULT 'Open Ticket',
    custom_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER UNIQUE,
    owner_id INTEGER,
    panel_id INTEGER,
    status TEXT DEFAULT 'open',
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    message_id INTEGER UNIQUE,
    prize TEXT,
    winner_count INTEGER,
    host_id INTEGER,
    end_time INTEGER,
    ended INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS voicemaster_channels (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    owner_id INTEGER
);

CREATE TABLE IF NOT EXISTS antinuke_config (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    vanity INTEGER DEFAULT 1,
    botadd INTEGER DEFAULT 1,
    ban INTEGER DEFAULT 1,
    kick INTEGER DEFAULT 1,
    role INTEGER DEFAULT 1,
    log_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS antinuke_whitelist (
    guild_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS antiraid_config (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    massjoin_count INTEGER DEFAULT 10,
    massjoin_seconds INTEGER DEFAULT 10,
    min_account_age_days INTEGER DEFAULT 0,
    raid_mode INTEGER DEFAULT 0,
    log_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS antiraid_whitelist (
    guild_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS filter_words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    word TEXT
);

CREATE TABLE IF NOT EXISTS filter_config (
    guild_id INTEGER PRIMARY KEY,
    invites INTEGER DEFAULT 0,
    spam INTEGER DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    # ---------- guild config ----------

    async def get_guild_config(self, guild_id: int) -> aiosqlite.Row:
        cur = await self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO guild_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
            )
            row = await cur.fetchone()
        return row

    async def set_guild_config(self, guild_id: int, **fields):
        await self.get_guild_config(guild_id)  # ensure row exists
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self.conn.execute(
            f"UPDATE guild_config SET {cols} WHERE guild_id = ?", values
        )
        await self.conn.commit()

    # ---------- warnings ----------

    async def add_warning(self, guild_id, user_id, moderator_id, reason):
        await self.conn.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, reason, int(time.time())),
        )
        await self.conn.commit()

    async def get_warnings(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC",
            (guild_id, user_id),
        )
        return await cur.fetchall()

    async def clear_warnings(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    # ---------- autoresponders ----------

    async def add_autoresponder(self, guild_id, trigger, response, exact_match=False):
        await self.conn.execute(
            "INSERT INTO autoresponders (guild_id, trigger, response, exact_match) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, trigger.lower(), response, int(exact_match)),
        )
        await self.conn.commit()

    async def remove_autoresponder(self, guild_id, trigger):
        await self.conn.execute(
            "DELETE FROM autoresponders WHERE guild_id = ? AND trigger = ?",
            (guild_id, trigger.lower()),
        )
        await self.conn.commit()

    async def get_autoresponders(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM autoresponders WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    # ---------- reaction roles ----------

    async def add_reaction_role(self, guild_id, message_id, channel_id, emoji, role_id):
        await self.conn.execute(
            "INSERT INTO reaction_roles (guild_id, message_id, channel_id, emoji, role_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, message_id, channel_id, emoji, role_id),
        )
        await self.conn.commit()

    async def remove_reaction_role(self, message_id, emoji):
        await self.conn.execute(
            "DELETE FROM reaction_roles WHERE message_id = ? AND emoji = ?",
            (message_id, emoji),
        )
        await self.conn.commit()

    async def get_reaction_role(self, message_id, emoji):
        cur = await self.conn.execute(
            "SELECT * FROM reaction_roles WHERE message_id = ? AND emoji = ?",
            (message_id, emoji),
        )
        return await cur.fetchone()

    # ---------- button roles ----------

    async def add_button_role(self, guild_id, message_id, channel_id, label, role_id, custom_id):
        await self.conn.execute(
            "INSERT INTO button_roles (guild_id, message_id, channel_id, label, role_id, custom_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, message_id, channel_id, label, role_id, custom_id),
        )
        await self.conn.commit()

    async def get_button_role(self, custom_id):
        cur = await self.conn.execute(
            "SELECT * FROM button_roles WHERE custom_id = ?", (custom_id,)
        )
        return await cur.fetchone()

    async def get_all_button_roles(self):
        cur = await self.conn.execute("SELECT * FROM button_roles")
        return await cur.fetchall()

    # ---------- starboard ----------

    async def get_starboard_post(self, original_message_id):
        cur = await self.conn.execute(
            "SELECT * FROM starboard_posts WHERE original_message_id = ?",
            (original_message_id,),
        )
        return await cur.fetchone()

    async def upsert_starboard_post(self, guild_id, original_message_id, starboard_message_id, star_count):
        existing = await self.get_starboard_post(original_message_id)
        if existing:
            await self.conn.execute(
                "UPDATE starboard_posts SET star_count = ? WHERE original_message_id = ?",
                (star_count, original_message_id),
            )
        else:
            await self.conn.execute(
                "INSERT INTO starboard_posts (guild_id, original_message_id, starboard_message_id, star_count) "
                "VALUES (?, ?, ?, ?)",
                (guild_id, original_message_id, starboard_message_id, star_count),
            )
        await self.conn.commit()

    # ---------- tickets ----------

    async def add_ticket_panel(self, guild_id, channel_id, message_id, category_id,
                                support_role_id, title, description, button_label, custom_id):
        await self.conn.execute(
            "INSERT INTO ticket_panels (guild_id, channel_id, message_id, category_id, "
            "support_role_id, panel_title, panel_description, button_label, custom_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_id, category_id, support_role_id,
             title, description, button_label, custom_id),
        )
        await self.conn.commit()

    async def get_ticket_panel(self, custom_id):
        cur = await self.conn.execute(
            "SELECT * FROM ticket_panels WHERE custom_id = ?", (custom_id,)
        )
        return await cur.fetchone()

    async def create_ticket(self, guild_id, channel_id, owner_id, panel_id):
        await self.conn.execute(
            "INSERT INTO tickets (guild_id, channel_id, owner_id, panel_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, owner_id, panel_id, int(time.time())),
        )
        await self.conn.commit()

    async def get_ticket(self, channel_id):
        cur = await self.conn.execute(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        )
        return await cur.fetchone()

    async def close_ticket(self, channel_id):
        await self.conn.execute(
            "UPDATE tickets SET status = 'closed' WHERE channel_id = ?", (channel_id,)
        )
        await self.conn.commit()

    async def get_open_ticket_for_user(self, guild_id, owner_id, panel_id):
        cur = await self.conn.execute(
            "SELECT * FROM tickets WHERE guild_id = ? AND owner_id = ? AND panel_id = ? AND status = 'open'",
            (guild_id, owner_id, panel_id),
        )
        return await cur.fetchone()

    # ---------- giveaways ----------

    async def create_giveaway(self, guild_id, channel_id, message_id, prize, winner_count, host_id, end_time):
        await self.conn.execute(
            "INSERT INTO giveaways (guild_id, channel_id, message_id, prize, winner_count, host_id, end_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, message_id, prize, winner_count, host_id, end_time),
        )
        await self.conn.commit()

    async def get_giveaway(self, message_id):
        cur = await self.conn.execute(
            "SELECT * FROM giveaways WHERE message_id = ?", (message_id,)
        )
        return await cur.fetchone()

    async def get_active_giveaways(self):
        cur = await self.conn.execute(
            "SELECT * FROM giveaways WHERE ended = 0"
        )
        return await cur.fetchall()

    async def end_giveaway(self, message_id):
        await self.conn.execute(
            "UPDATE giveaways SET ended = 1 WHERE message_id = ?", (message_id,)
        )
        await self.conn.commit()

    async def add_giveaway_entry(self, giveaway_id, user_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
            (giveaway_id, user_id),
        )
        await self.conn.commit()

    async def remove_giveaway_entry(self, giveaway_id, user_id):
        await self.conn.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
        await self.conn.commit()

    async def get_giveaway_entrants(self, giveaway_id):
        cur = await self.conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
        )
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def has_entered_giveaway(self, giveaway_id, user_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
        return (await cur.fetchone()) is not None

    # ---------- voicemaster ----------

    async def add_voicemaster_channel(self, channel_id, guild_id, owner_id):
        await self.conn.execute(
            "INSERT INTO voicemaster_channels (channel_id, guild_id, owner_id) VALUES (?, ?, ?)",
            (channel_id, guild_id, owner_id),
        )
        await self.conn.commit()

    async def remove_voicemaster_channel(self, channel_id):
        await self.conn.execute(
            "DELETE FROM voicemaster_channels WHERE channel_id = ?", (channel_id,)
        )
        await self.conn.commit()

    async def get_voicemaster_channel(self, channel_id):
        cur = await self.conn.execute(
            "SELECT * FROM voicemaster_channels WHERE channel_id = ?", (channel_id,)
        )
        return await cur.fetchone()

    # ---------- antinuke ----------

    async def get_antinuke_config(self, guild_id: int):
        cur = await self.conn.execute(
            "SELECT * FROM antinuke_config WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO antinuke_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT * FROM antinuke_config WHERE guild_id = ?", (guild_id,)
            )
            row = await cur.fetchone()
        return row

    async def set_antinuke_config(self, guild_id: int, **fields):
        await self.get_antinuke_config(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self.conn.execute(
            f"UPDATE antinuke_config SET {cols} WHERE guild_id = ?", values
        )
        await self.conn.commit()

    async def antinuke_whitelist_add(self, guild_id, user_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO antinuke_whitelist (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def antinuke_whitelist_remove(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM antinuke_whitelist WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def antinuke_whitelist_list(self, guild_id):
        cur = await self.conn.execute(
            "SELECT user_id FROM antinuke_whitelist WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def antinuke_is_whitelisted(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM antinuke_whitelist WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return (await cur.fetchone()) is not None

    # ---------- antiraid ----------

    async def get_antiraid_config(self, guild_id: int):
        cur = await self.conn.execute(
            "SELECT * FROM antiraid_config WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO antiraid_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT * FROM antiraid_config WHERE guild_id = ?", (guild_id,)
            )
            row = await cur.fetchone()
        return row

    async def set_antiraid_config(self, guild_id: int, **fields):
        await self.get_antiraid_config(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self.conn.execute(
            f"UPDATE antiraid_config SET {cols} WHERE guild_id = ?", values
        )
        await self.conn.commit()

    async def antiraid_whitelist_add(self, guild_id, user_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO antiraid_whitelist (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def antiraid_whitelist_remove(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM antiraid_whitelist WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def antiraid_whitelist_list(self, guild_id):
        cur = await self.conn.execute(
            "SELECT user_id FROM antiraid_whitelist WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def antiraid_is_whitelisted(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM antiraid_whitelist WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return (await cur.fetchone()) is not None

    # ---------- filter ----------

    async def get_filter_config(self, guild_id: int):
        cur = await self.conn.execute(
            "SELECT * FROM filter_config WHERE guild_id = ?", (guild_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO filter_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self.conn.commit()
            cur = await self.conn.execute(
                "SELECT * FROM filter_config WHERE guild_id = ?", (guild_id,)
            )
            row = await cur.fetchone()
        return row

    async def set_filter_config(self, guild_id: int, **fields):
        await self.get_filter_config(guild_id)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id]
        await self.conn.execute(
            f"UPDATE filter_config SET {cols} WHERE guild_id = ?", values
        )
        await self.conn.commit()

    async def add_filter_word(self, guild_id, word):
        await self.conn.execute(
            "INSERT INTO filter_words (guild_id, word) VALUES (?, ?)",
            (guild_id, word.lower()),
        )
        await self.conn.commit()

    async def remove_filter_word(self, guild_id, word):
        await self.conn.execute(
            "DELETE FROM filter_words WHERE guild_id = ? AND word = ?",
            (guild_id, word.lower()),
        )
        await self.conn.commit()

    async def get_filter_words(self, guild_id):
        cur = await self.conn.execute(
            "SELECT word FROM filter_words WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["word"] for r in rows]


db = Database()
