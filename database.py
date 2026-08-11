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
    voicemaster_join_channel_id INTEGER,
    jail_role_id INTEGER,
    jail_channel_id INTEGER,
    voicemaster_default_name TEXT DEFAULT '{user}''s Channel',
    voicemaster_default_bitrate INTEGER,
    voicemaster_default_region TEXT,
    voicemaster_join_role_id INTEGER,
    starboard_locked INTEGER DEFAULT 0,
    starboard_emoji TEXT DEFAULT '⭐',
    starboard_selfstar INTEGER DEFAULT 1,
    starboard_color TEXT,
    starboard_timestamp INTEGER DEFAULT 1,
    starboard_jumpurl INTEGER DEFAULT 1,
    starboard_attachments INTEGER DEFAULT 1,
    booster_base_role_id INTEGER,
    booster_award_role_id INTEGER,
    vanity_phrase TEXT,
    vanity_message TEXT,
    vanity_award_channel_id INTEGER,
    content_filter_enabled INTEGER DEFAULT 0,
    content_filter_log_channel_id INTEGER,
    content_filter_action TEXT DEFAULT 'delete',
    image_mute_role_id INTEGER,
    reaction_mute_role_id INTEGER
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
    ended INTEGER DEFAULT 0,
    description TEXT,
    thumbnail_url TEXT,
    image_url TEXT,
    required_roles TEXT
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

CREATE TABLE IF NOT EXISTS staff_roles (
    guild_id INTEGER,
    role_id INTEGER,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS temp_bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    unban_time INTEGER
);

CREATE TABLE IF NOT EXISTS hardbans (
    guild_id INTEGER,
    user_id INTEGER,
    reason TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS jailed_members (
    guild_id INTEGER,
    user_id INTEGER,
    previous_roles TEXT,
    jail_until INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS command_aliases (
    guild_id INTEGER,
    alias TEXT,
    command TEXT,
    PRIMARY KEY (guild_id, alias)
);

CREATE TABLE IF NOT EXISTS afk_status (
    guild_id INTEGER,
    user_id INTEGER,
    reason TEXT,
    set_at INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS starboard_ignored (
    guild_id INTEGER,
    channel_id INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS booster_roles (
    guild_id INTEGER,
    user_id INTEGER,
    role_id INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS vanity_roles (
    guild_id INTEGER,
    role_id INTEGER,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS vanity_members (
    guild_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS log_config (
    guild_id INTEGER,
    category TEXT,
    channel_id INTEGER,
    PRIMARY KEY (guild_id, category)
);

CREATE TABLE IF NOT EXISTS log_ignored (
    guild_id INTEGER,
    channel_id INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS fake_permissions (
    guild_id INTEGER,
    role_id INTEGER,
    permission TEXT,
    PRIMARY KEY (guild_id, role_id, permission)
);

CREATE TABLE IF NOT EXISTS welcome_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS goodbye_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS boost_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    message TEXT
);

CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    added_at INTEGER
);

CREATE TABLE IF NOT EXISTS permitted_users (
    user_id INTEGER PRIMARY KEY,
    added_at INTEGER
);

CREATE TABLE IF NOT EXISTS global_filter_words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS mod_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    moderator_id INTEGER,
    action TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS nuke_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER UNIQUE,
    interval_minutes INTEGER,
    message TEXT,
    next_run INTEGER
);

CREATE TABLE IF NOT EXISTS forced_nicknames (
    guild_id INTEGER,
    user_id INTEGER,
    nickname TEXT,
    PRIMARY KEY (guild_id, user_id)
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
        await self._migrate()

    async def _migrate(self):
        """Add columns to tables that already existed before this field was
        introduced. CREATE TABLE IF NOT EXISTS only creates missing tables,
        it never alters existing ones, so new columns need to be added here."""
        migrations = [
            # Prefix exists in current schemas, but add it for databases created
            # by an older/custom build that did not have the column.
            ("guild_config", "prefix", "TEXT DEFAULT ','"),
            ("guild_config", "jail_role_id", "INTEGER"),
            ("guild_config", "jail_channel_id", "INTEGER"),
            ("giveaways", "description", "TEXT"),
            ("giveaways", "thumbnail_url", "TEXT"),
            ("giveaways", "image_url", "TEXT"),
            ("giveaways", "required_roles", "TEXT"),
            ("guild_config", "voicemaster_default_name", "TEXT DEFAULT '{user}''s Channel'"),
            ("guild_config", "voicemaster_default_bitrate", "INTEGER"),
            ("guild_config", "voicemaster_default_region", "TEXT"),
            ("guild_config", "voicemaster_join_role_id", "INTEGER"),
            ("guild_config", "starboard_locked", "INTEGER DEFAULT 0"),
            ("guild_config", "starboard_emoji", "TEXT DEFAULT '⭐'"),
            ("guild_config", "starboard_selfstar", "INTEGER DEFAULT 1"),
            ("guild_config", "starboard_color", "TEXT"),
            ("guild_config", "starboard_timestamp", "INTEGER DEFAULT 1"),
            ("guild_config", "starboard_jumpurl", "INTEGER DEFAULT 1"),
            ("guild_config", "starboard_attachments", "INTEGER DEFAULT 1"),
            ("guild_config", "booster_base_role_id", "INTEGER"),
            ("guild_config", "booster_award_role_id", "INTEGER"),
            ("guild_config", "vanity_phrase", "TEXT"),
            ("guild_config", "vanity_message", "TEXT"),
            ("guild_config", "vanity_award_channel_id", "INTEGER"),
            ("guild_config", "content_filter_enabled", "INTEGER DEFAULT 0"),
            ("guild_config", "content_filter_log_channel_id", "INTEGER"),
            ("guild_config", "content_filter_action", "TEXT DEFAULT 'delete'"),
            ("guild_config", "image_mute_role_id", "INTEGER"),
            ("guild_config", "reaction_mute_role_id", "INTEGER"),
            ("jailed_members", "jail_until", "INTEGER"),
        ]
        for table, column, coltype in migrations:
            try:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                await self.conn.commit()
            except aiosqlite.OperationalError:
                pass  # column already exists

        # Bot 38 changed the word filter from per-server to global. Copy the
        # existing Bot 37 words forward once so upgrading does not silently
        # lose every word that was already configured. INSERT OR IGNORE makes
        # this safe to run every startup.
        await self.conn.execute(
            "INSERT OR IGNORE INTO global_filter_words (word) "
            "SELECT word FROM filter_words WHERE word IS NOT NULL AND TRIM(word) != ''"
        )
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

    async def get_reaction_roles_for_guild(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM reaction_roles WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    async def remove_reaction_roles_for_message(self, message_id):
        await self.conn.execute("DELETE FROM reaction_roles WHERE message_id = ?", (message_id,))
        await self.conn.commit()

    async def reset_reaction_roles(self, guild_id):
        await self.conn.execute("DELETE FROM reaction_roles WHERE guild_id = ?", (guild_id,))
        await self.conn.commit()

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

    async def get_button_roles_for_guild(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM button_roles WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    async def remove_button_role(self, custom_id):
        await self.conn.execute("DELETE FROM button_roles WHERE custom_id = ?", (custom_id,))
        await self.conn.commit()

    async def remove_button_roles_for_message(self, message_id):
        await self.conn.execute("DELETE FROM button_roles WHERE message_id = ?", (message_id,))
        await self.conn.commit()

    async def reset_button_roles(self, guild_id):
        await self.conn.execute("DELETE FROM button_roles WHERE guild_id = ?", (guild_id,))
        await self.conn.commit()

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

    async def get_active_giveaways_for_guild(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM giveaways WHERE ended = 0 AND guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    async def edit_giveaway(self, message_id, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [message_id]
        await self.conn.execute(
            f"UPDATE giveaways SET {cols} WHERE message_id = ?", values
        )
        await self.conn.commit()

    async def delete_giveaway(self, message_id):
        await self.conn.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = (SELECT id FROM giveaways WHERE message_id = ?)",
            (message_id,),
        )
        await self.conn.execute("DELETE FROM giveaways WHERE message_id = ?", (message_id,))
        await self.conn.commit()

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

    # ---------- staff roles ----------

    async def add_staff_role(self, guild_id, role_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO staff_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
        )
        await self.conn.commit()

    async def remove_staff_role(self, guild_id, role_id):
        await self.conn.execute(
            "DELETE FROM staff_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await self.conn.commit()

    async def list_staff_roles(self, guild_id):
        cur = await self.conn.execute(
            "SELECT role_id FROM staff_roles WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["role_id"] for r in rows]

    async def is_staff_member(self, guild_id, member) -> bool:
        """True if this member has manage_guild perms, is admin, or holds a bound staff role."""
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return True
        staff_roles = await self.list_staff_roles(guild_id)
        return any(r.id in staff_roles for r in member.roles)

    # ---------- temp bans ----------

    async def add_temp_ban(self, guild_id, user_id, unban_time):
        await self.conn.execute(
            "INSERT INTO temp_bans (guild_id, user_id, unban_time) VALUES (?, ?, ?)",
            (guild_id, user_id, unban_time),
        )
        await self.conn.commit()

    async def get_expired_temp_bans(self, now: int):
        cur = await self.conn.execute(
            "SELECT * FROM temp_bans WHERE unban_time <= ?", (now,)
        )
        return await cur.fetchall()

    async def remove_temp_ban(self, temp_ban_id):
        await self.conn.execute(
            "DELETE FROM temp_bans WHERE id = ?", (temp_ban_id,)
        )
        await self.conn.commit()

    # ---------- hardbans ----------

    async def add_hardban(self, guild_id, user_id, reason):
        await self.conn.execute(
            "INSERT OR REPLACE INTO hardbans (guild_id, user_id, reason) VALUES (?, ?, ?)",
            (guild_id, user_id, reason),
        )
        await self.conn.commit()

    async def remove_hardban(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM hardbans WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def is_hardbanned(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM hardbans WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return (await cur.fetchone()) is not None

    # ---------- jail ----------

    async def set_jailed(self, guild_id, user_id, previous_roles_csv, jail_until=None):
        await self.conn.execute(
            "INSERT OR REPLACE INTO jailed_members (guild_id, user_id, previous_roles, jail_until) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, previous_roles_csv, jail_until),
        )
        await self.conn.commit()

    async def get_jailed(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT * FROM jailed_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()

    async def remove_jailed(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM jailed_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    # ---------- command aliases ----------

    async def add_alias(self, guild_id, alias, command):
        await self.conn.execute(
            "INSERT OR REPLACE INTO command_aliases (guild_id, alias, command) VALUES (?, ?, ?)",
            (guild_id, alias.lower(), command),
        )
        await self.conn.commit()

    async def remove_alias(self, guild_id, alias):
        await self.conn.execute(
            "DELETE FROM command_aliases WHERE guild_id = ? AND alias = ?",
            (guild_id, alias.lower()),
        )
        await self.conn.commit()

    async def get_alias(self, guild_id, alias):
        cur = await self.conn.execute(
            "SELECT command FROM command_aliases WHERE guild_id = ? AND alias = ?",
            (guild_id, alias.lower()),
        )
        row = await cur.fetchone()
        return row["command"] if row else None

    async def list_aliases(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM command_aliases WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    # ---------- afk ----------

    async def set_afk(self, guild_id, user_id, reason, set_at):
        await self.conn.execute(
            "INSERT OR REPLACE INTO afk_status (guild_id, user_id, reason, set_at) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, reason, set_at),
        )
        await self.conn.commit()

    async def get_afk(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT * FROM afk_status WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()

    async def remove_afk(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM afk_status WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    # ---------- starboard ignore ----------

    async def starboard_ignore_add(self, guild_id, channel_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO starboard_ignored (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def starboard_ignore_remove(self, guild_id, channel_id):
        await self.conn.execute(
            "DELETE FROM starboard_ignored WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def starboard_ignore_list(self, guild_id):
        cur = await self.conn.execute(
            "SELECT channel_id FROM starboard_ignored WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["channel_id"] for r in rows]

    async def is_starboard_ignored(self, guild_id, channel_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM starboard_ignored WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        return (await cur.fetchone()) is not None

    # ---------- booster roles ----------

    async def set_booster_role(self, guild_id, user_id, role_id):
        await self.conn.execute(
            "INSERT OR REPLACE INTO booster_roles (guild_id, user_id, role_id) VALUES (?, ?, ?)",
            (guild_id, user_id, role_id),
        )
        await self.conn.commit()

    async def get_booster_role(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT * FROM booster_roles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cur.fetchone()

    async def remove_booster_role(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM booster_roles WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def list_booster_roles(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM booster_roles WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    # ---------- vanity ----------

    async def vanity_role_add(self, guild_id, role_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO vanity_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
        )
        await self.conn.commit()

    async def vanity_role_remove(self, guild_id, role_id):
        await self.conn.execute(
            "DELETE FROM vanity_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await self.conn.commit()

    async def vanity_role_list(self, guild_id):
        cur = await self.conn.execute(
            "SELECT role_id FROM vanity_roles WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["role_id"] for r in rows]

    async def vanity_member_has(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM vanity_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return (await cur.fetchone()) is not None

    async def vanity_member_set(self, guild_id, user_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO vanity_members (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        await self.conn.commit()

    async def vanity_member_clear(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM vanity_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.conn.commit()

    # ---------- logging ----------

    async def set_log_channel(self, guild_id, category, channel_id):
        await self.conn.execute(
            "INSERT OR REPLACE INTO log_config (guild_id, category, channel_id) VALUES (?, ?, ?)",
            (guild_id, category, channel_id),
        )
        await self.conn.commit()

    async def remove_log_channel(self, guild_id, category):
        await self.conn.execute(
            "DELETE FROM log_config WHERE guild_id = ? AND category = ?",
            (guild_id, category),
        )
        await self.conn.commit()

    async def get_log_channel(self, guild_id, category):
        cur = await self.conn.execute(
            "SELECT channel_id FROM log_config WHERE guild_id = ? AND category = ?",
            (guild_id, category),
        )
        row = await cur.fetchone()
        return row["channel_id"] if row else None

    async def list_log_channels(self, guild_id):
        cur = await self.conn.execute(
            "SELECT * FROM log_config WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchall()

    async def log_ignore_add(self, guild_id, channel_id):
        await self.conn.execute(
            "INSERT OR IGNORE INTO log_ignored (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def log_ignore_remove(self, guild_id, channel_id):
        await self.conn.execute(
            "DELETE FROM log_ignored WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def log_ignore_list(self, guild_id):
        cur = await self.conn.execute(
            "SELECT channel_id FROM log_ignored WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["channel_id"] for r in rows]

    async def is_log_ignored(self, guild_id, channel_id):
        cur = await self.conn.execute(
            "SELECT 1 FROM log_ignored WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        return (await cur.fetchone()) is not None

    # ---------- fake permissions ----------

    async def add_fake_permission(self, guild_id, role_id, permission):
        await self.conn.execute(
            "INSERT OR IGNORE INTO fake_permissions (guild_id, role_id, permission) VALUES (?, ?, ?)",
            (guild_id, role_id, permission.lower()),
        )
        await self.conn.commit()

    async def remove_fake_permission(self, guild_id, role_id, permission):
        await self.conn.execute(
            "DELETE FROM fake_permissions WHERE guild_id = ? AND role_id = ? AND permission = ?",
            (guild_id, role_id, permission.lower()),
        )
        await self.conn.commit()

    async def list_fake_permissions(self, guild_id, role_id=None):
        if role_id:
            cur = await self.conn.execute(
                "SELECT * FROM fake_permissions WHERE guild_id = ? AND role_id = ?",
                (guild_id, role_id),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM fake_permissions WHERE guild_id = ?", (guild_id,)
            )
        return await cur.fetchall()

    async def has_fake_permission(self, guild_id, role_ids, permission):
        if not role_ids:
            return False
        placeholders = ",".join("?" for _ in role_ids)
        cur = await self.conn.execute(
            f"SELECT 1 FROM fake_permissions WHERE guild_id = ? AND permission = ? AND role_id IN ({placeholders})",
            (guild_id, permission.lower(), *role_ids),
        )
        return (await cur.fetchone()) is not None

    async def reset_fake_permissions(self, guild_id):
        await self.conn.execute("DELETE FROM fake_permissions WHERE guild_id = ?", (guild_id,))
        await self.conn.commit()

    # ---------- welcome / goodbye / boost multi-messages ----------
    # table is always one of a fixed set of literals we control internally
    # (welcome_messages / goodbye_messages / boost_messages), never user
    # input, so building the query with an f-string here is safe.

    async def add_event_message(self, table, guild_id, channel_id, message):
        await self.conn.execute(
            f"INSERT INTO {table} (guild_id, channel_id, message) VALUES (?, ?, ?)",
            (guild_id, channel_id, message),
        )
        await self.conn.commit()
        cur = await self.conn.execute("SELECT last_insert_rowid() AS id")
        row = await cur.fetchone()
        return row["id"]

    async def remove_event_message(self, table, message_row_id):
        await self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (message_row_id,))
        await self.conn.commit()

    async def get_event_message(self, table, message_row_id):
        cur = await self.conn.execute(f"SELECT * FROM {table} WHERE id = ?", (message_row_id,))
        return await cur.fetchone()

    async def list_event_messages(self, table, guild_id):
        cur = await self.conn.execute(f"SELECT * FROM {table} WHERE guild_id = ?", (guild_id,))
        return await cur.fetchall()

    # ---------- blacklist ----------

    async def blacklist_add(self, user_id, reason, added_at):
        await self.conn.execute(
            "INSERT OR REPLACE INTO blacklist (user_id, reason, added_at) VALUES (?, ?, ?)",
            (user_id, reason, added_at),
        )
        await self.conn.commit()

    async def blacklist_remove(self, user_id):
        await self.conn.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def blacklist_check(self, user_id):
        cur = await self.conn.execute("SELECT * FROM blacklist WHERE user_id = ?", (user_id,))
        return await cur.fetchone()

    async def blacklist_list(self):
        cur = await self.conn.execute("SELECT * FROM blacklist")
        return await cur.fetchall()

    # ---------- permitted users (lockdown mode) ----------

    async def permit_add(self, user_id, added_at):
        await self.conn.execute(
            "INSERT OR REPLACE INTO permitted_users (user_id, added_at) VALUES (?, ?)",
            (user_id, added_at),
        )
        await self.conn.commit()

    async def permit_remove(self, user_id):
        await self.conn.execute("DELETE FROM permitted_users WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def permit_check(self, user_id):
        cur = await self.conn.execute("SELECT 1 FROM permitted_users WHERE user_id = ?", (user_id,))
        return (await cur.fetchone()) is not None

    async def permit_list(self):
        cur = await self.conn.execute("SELECT * FROM permitted_users")
        return await cur.fetchall()

    # ---------- global filter words ----------

    async def add_global_filter_word(self, word):
        await self.conn.execute(
            "INSERT OR IGNORE INTO global_filter_words (word) VALUES (?)", (word.lower(),)
        )
        await self.conn.commit()

    async def remove_global_filter_word(self, word):
        await self.conn.execute(
            "DELETE FROM global_filter_words WHERE word = ?", (word.lower(),)
        )
        await self.conn.commit()

    async def get_global_filter_words(self):
        cur = await self.conn.execute("SELECT word FROM global_filter_words")
        rows = await cur.fetchall()
        return [r["word"] for r in rows]

    # ---------- mod action stats ----------

    async def log_mod_action(self, guild_id, moderator_id, action, created_at):
        await self.conn.execute(
            "INSERT INTO mod_actions (guild_id, moderator_id, action, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, moderator_id, action, created_at),
        )
        await self.conn.commit()

    async def get_mod_action_counts(self, guild_id, moderator_id):
        cur = await self.conn.execute(
            "SELECT action, COUNT(*) as count FROM mod_actions WHERE guild_id = ? AND moderator_id = ? GROUP BY action",
            (guild_id, moderator_id),
        )
        rows = await cur.fetchall()
        return {r["action"]: r["count"] for r in rows}

    # ---------- scheduled nukes ----------

    async def add_nuke_schedule(self, guild_id, channel_id, interval_minutes, message, next_run):
        await self.conn.execute(
            "INSERT OR REPLACE INTO nuke_schedule (guild_id, channel_id, interval_minutes, message, next_run) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, interval_minutes, message, next_run),
        )
        await self.conn.commit()

    async def remove_nuke_schedule(self, channel_id):
        await self.conn.execute("DELETE FROM nuke_schedule WHERE channel_id = ?", (channel_id,))
        await self.conn.commit()

    async def get_nuke_schedule(self, channel_id):
        cur = await self.conn.execute("SELECT * FROM nuke_schedule WHERE channel_id = ?", (channel_id,))
        return await cur.fetchone()

    async def get_due_nuke_schedules(self, now):
        cur = await self.conn.execute("SELECT * FROM nuke_schedule WHERE next_run <= ?", (now,))
        return await cur.fetchall()

    async def update_nuke_schedule_next_run(self, channel_id, next_run):
        await self.conn.execute(
            "UPDATE nuke_schedule SET next_run = ? WHERE channel_id = ?", (next_run, channel_id)
        )
        await self.conn.commit()

    # ---------- forced nicknames ----------

    async def set_forced_nickname(self, guild_id, user_id, nickname):
        await self.conn.execute(
            "INSERT OR REPLACE INTO forced_nicknames (guild_id, user_id, nickname) VALUES (?, ?, ?)",
            (guild_id, user_id, nickname),
        )
        await self.conn.commit()

    async def remove_forced_nickname(self, guild_id, user_id):
        await self.conn.execute(
            "DELETE FROM forced_nicknames WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        await self.conn.commit()

    async def get_forced_nickname(self, guild_id, user_id):
        cur = await self.conn.execute(
            "SELECT * FROM forced_nicknames WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return await cur.fetchone()

    # ---------- jail (timed) ----------

    async def get_expired_jails(self, now):
        cur = await self.conn.execute(
            "SELECT * FROM jailed_members WHERE jail_until IS NOT NULL AND jail_until <= ?", (now,)
        )
        return await cur.fetchall()


db = Database()
