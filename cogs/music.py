import asyncio
import os
import re
import time

import aiohttp
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/track/([a-zA-Z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"open\.spotify\.com/album/([a-zA-Z0-9]+)")

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch1",
    "nocheckcertificate": True,
    "ignoreerrors": True,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class Track:
    def __init__(self, title: str, url: str, stream_url: str, requester: discord.Member):
        self.title = title
        self.url = url
        self.stream_url = stream_url
        self.requester = requester


class GuildMusicState:
    def __init__(self):
        self.queue: list[Track] = []
        self.loop_mode = "off"  # off, queue, current
        self.current: Track | None = None
        self.voice_client: discord.VoiceClient | None = None


class Music(commands.Cog):
    """Play music in a voice channel via YouTube search or a Spotify link
    (Spotify links are resolved to track info only â actual audio always
    streams from YouTube, since Spotify audio itself can't be extracted)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}
        self.session: aiohttp.ClientSession | None = None
        self._spotify_token = None
        self._spotify_token_expires = 0

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildMusicState()
        return self.states[guild_id]

    # ---------- spotify ----------

    async def _get_spotify_token(self):
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            return None
        if self._spotify_token and time.time() < self._spotify_token_expires:
            return self._spotify_token
        auth = aiohttp.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
        try:
            async with self.session.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=auth,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception:
            return None
        self._spotify_token = data["access_token"]
        self._spotify_token_expires = time.time() + data.get("expires_in", 3600) - 60
        return self._spotify_token

    async def _resolve_spotify(self, url: str) -> list[str]:
        """Returns 'title artist' search strings for a Spotify track/playlist/album link."""
        token = await self._get_spotify_token()
        if token is None:
            return []
        headers = {"Authorization": f"Bearer {token}"}

        track_match = SPOTIFY_TRACK_RE.search(url)
        if track_match:
            try:
                async with self.session.get(
                    f"https://api.spotify.com/v1/tracks/{track_match.group(1)}", headers=headers
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            except Exception:
                return []
            artists = ", ".join(a["name"] for a in data.get("artists", []))
            return [f"{data['name']} {artists}"]

        playlist_match = SPOTIFY_PLAYLIST_RE.search(url)
        album_match = SPOTIFY_ALBUM_RE.search(url)
        if playlist_match or album_match:
            kind = "playlists" if playlist_match else "albums"
            item_id = (playlist_match or album_match).group(1)
            try:
                async with self.session.get(
                    f"https://api.spotify.com/v1/{kind}/{item_id}/tracks?limit=25", headers=headers
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            except Exception:
                return []
            queries = []
            for item in data.get("items", []):
                track = item.get("track", item)
                if not track:
                    continue
                artists = ", ".join(a["name"] for a in track.get("artists", []))
                queries.append(f"{track['name']} {artists}")
            return queries

        return []

    # ---------- youtube ----------

    async def _search_youtube(self, query: str):
        loop = asyncio.get_event_loop()

        def extract():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                info = ydl.extract_info(query, download=False)
                if info is None:
                    return None
                if "entries" in info:
                    info = info["entries"][0] if info["entries"] else None
                return info

        info = await loop.run_in_executor(None, extract)
        if info is None:
            return None
        return Track(
            title=info.get("title", query),
            url=info.get("webpage_url", ""),
            stream_url=info["url"],
            requester=None,
        )

    # ---------- playback ----------

    async def _play_next(self, guild: discord.Guild):
        state = self.get_state(guild.id)

        if state.loop_mode == "current" and state.current:
            next_track = state.current
        elif state.queue:
            next_track = state.queue.pop(0)
            if state.loop_mode == "queue" and state.current:
                state.queue.append(state.current)
        else:
            state.current = None
            return

        state.current = next_track
        if state.voice_client is None or not state.voice_client.is_connected():
            return

        source = discord.FFmpegPCMAudio(next_track.stream_url, **FFMPEG_OPTIONS)

        def after_playing(error):
            fut = asyncio.run_coroutine_threadsafe(self._play_next(guild), self.bot.loop)
            try:
                fut.result()
            except Exception:
                pass

        state.voice_client.play(source, after=after_playing)

    @commands.hybrid_command(name="play", aliases=["p"], description="Queue a track.")
    @app_commands.describe(query="Song name, YouTube link, or Spotify link")
    async def play(self, ctx: commands.Context, *, query: str):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            return await ctx.send("You need to be in a voice channel first.")

        state = self.get_state(ctx.guild.id)
        if state.voice_client is None or not state.voice_client.is_connected():
            state.voice_client = await ctx.author.voice.channel.connect()

        if ctx.interaction:
            await ctx.interaction.response.defer()

        if "open.spotify.com" in query:
            queries = await self._resolve_spotify(query)
            if not queries:
                return await ctx.send(
                    "Couldn't read that Spotify link â make sure `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` "
                    "are set, or the link might be invalid/private."
                )
        else:
            queries = [query]

        added = []
        for q in queries:
            track = await self._search_youtube(q)
            if track:
                track.requester = ctx.author
                state.queue.append(track)
                added.append(track)

        if not added:
            return await ctx.send("Couldn't find anything to play for that.")

        if len(added) == 1:
            await ctx.send(f"ðµ Queued **{added[0].title}**")
        else:
            await ctx.send(f"ðµ Queued **{len(added)}** tracks.")

        if not state.voice_client.is_playing() and state.current is None:
            await self._play_next(ctx.guild)

    @commands.hybrid_command(name="skip", aliases=["next", "sk"], description="Skip to the next track.")
    async def skip(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
            await ctx.send("â­ï¸ Skipped.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.hybrid_command(name="disconnect", aliases=["stop", "dc"], description="Disconnect and stop playing music.")
    async def disconnect(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        state.queue.clear()
        state.current = None
        state.loop_mode = "off"
        if state.voice_client:
            await state.voice_client.disconnect()
            state.voice_client = None
        await ctx.send("ð Disconnected.")

    @commands.hybrid_command(name="repeat", aliases=["loop"], description="Change the current loop mode.")
    @app_commands.describe(option="off, queue, or current")
    async def repeat(self, ctx: commands.Context, option: str):
        option = option.lower()
        if option not in ("off", "queue", "current"):
            return await ctx.send("Option must be one of: `off`, `queue`, `current`")
        state = self.get_state(ctx.guild.id)
        state.loop_mode = option
        await ctx.send(f"ð Loop mode set to **{option}**.")

    @commands.hybrid_command(name="queue", description="Show the current queue.")
    async def queue_cmd(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        if not state.current and not state.queue:
            return await ctx.send("Nothing is queued.")
        lines = []
        if state.current:
            lines.append(f"**Now playing:** {state.current.title}")
        for i, t in enumerate(state.queue[:10], start=1):
            lines.append(f"{i}. {t.title}")
        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
