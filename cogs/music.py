import asyncio
import os
import re
import time
import shutil
from collections import OrderedDict

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
    "retries": 1,
    "fragment_retries": 1,
    "extractor_retries": 1,
    # Prefer clients that are generally usable for server-side playback.
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"],
        }
    },
}

# yt-dlp now recommends a supported JavaScript runtime for YouTube extraction.
# Only enable Deno when it is actually installed on the host.
_DENO_PATHS = [
    shutil.which("deno"),
    os.path.expanduser("~/.deno/bin/deno"),
]
_DENO_PATH = next((path for path in _DENO_PATHS if path and os.path.exists(path)), None)
if _DENO_PATH:
    YDL_OPTS["js_runtimes"] = {"deno": {"path": _DENO_PATH}}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -ar 48000 -ac 2 -b:a 192k",
}

# name -> (ffmpeg audio filter, short description)
EQ_PRESETS = {
    "Flat": ("", "No equalizer effects — the original mix."),
    "Bass Boost": ("bass=g=12", "Boosted low end for a heavier bass sound."),
    "Bass Reducer": ("bass=g=-8", "Reduced low end for a lighter, thinner sound."),
    "Treble Boost": ("treble=g=10", "Boosted high end for crisper highs."),
    "Vocal Boost": ("equalizer=f=3000:width_type=h:width=1000:g=6", "Emphasizes the vocal range."),
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
        self.volume = 1.0  # 1.0 = 100%
        self.audio_source: discord.PCMVolumeTransformer | None = None
        self.eq_filter = ""  # current ffmpeg audio filter, "" = flat
        self.eq_name = "Flat"
        self.suppress_next_advance = False  # True while restarting the current track for an EQ change
        self.position = 0.0  # seconds into the current track, as of segment_start_time
        self.segment_start_time = 0.0  # time.time() when the current ffmpeg segment started


class EqualizerSelect(discord.ui.Select):
    def __init__(self, cog: "Music", guild_id: int):
        options = [
            discord.SelectOption(label=name, description=desc[:100])
            for name, (_, desc) in EQ_PRESETS.items()
        ]
        super().__init__(placeholder="Choose an EQ preset...", options=options)
        self.cog = cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        preset_name = self.values[0]
        filt, desc = EQ_PRESETS[preset_name]
        state = self.cog.get_state(self.guild_id)
        state.eq_filter = filt
        state.eq_name = preset_name

        embed = discord.Embed(
            title="🎚️ Equalizer",
            description=f"Preset set to **{preset_name}** — {desc}\nRestarting the current track with the new EQ...",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self.view)
        await self.cog._restart_current_track(interaction.guild)


class EqualizerView(discord.ui.View):
    def __init__(self, cog: "Music", guild_id: int, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.add_item(EqualizerSelect(cog, guild_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who ran this command can use this menu.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Music(commands.Cog):
    """Play music in a voice channel via YouTube search or a Spotify link
    (Spotify links are resolved to track info only — actual audio always
    streams from YouTube, since Spotify audio itself can't be extracted)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: dict[int, GuildMusicState] = {}
        self.session: aiohttp.ClientSession | None = None
        self._spotify_token = None
        self._spotify_token_expires = 0

        # YouTube extraction is deliberately throttled. A single shared lock
        # prevents multiple guilds from hammering YouTube at the same time.
        self._youtube_lock = asyncio.Lock()
        self._youtube_last_request = 0.0
        self._youtube_min_interval = 2.0
        self._youtube_cache: OrderedDict[str, tuple[float, Track]] = OrderedDict()
        self._youtube_cache_ttl = 300.0
        self._youtube_cache_max = 100
        self._youtube_backoff_until = 0.0

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

    async def _resolve_spotify_oembed(self, url: str):
        """Keyless lookup that works for a single track link — no API credentials needed."""
        try:
            async with self.session.get(
                "https://open.spotify.com/oembed", params={"url": url}
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception:
            return None
        return data.get("title")

    async def _resolve_spotify(self, url: str) -> list[str]:
        """Returns 'title artist' search strings for a Spotify track/playlist/album link."""
        track_match = SPOTIFY_TRACK_RE.search(url)

        # Single tracks: try the free, keyless oEmbed lookup first.
        if track_match:
            title = await self._resolve_spotify_oembed(url)
            if title:
                return [title]

        token = await self._get_spotify_token()
        if token is None:
            return []
        headers = {"Authorization": f"Bearer {token}"}

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
        """Resolve a YouTube query without hammering YouTube.

        The cache avoids repeating the same extraction, while the shared lock
        and minimum interval serialize requests across all guilds. If YouTube
        returns a rate-limit response, we back off instead of immediately
        retrying over and over.
        """
        now = time.monotonic()
        cached = self._youtube_cache.get(query)
        if cached:
            cached_at, track = cached
            if now - cached_at < self._youtube_cache_ttl:
                self._youtube_cache.move_to_end(query)
                return Track(track.title, track.url, track.stream_url, None)
            self._youtube_cache.pop(query, None)

        async with self._youtube_lock:
            now = time.monotonic()
            if now < self._youtube_backoff_until:
                wait = self._youtube_backoff_until - now
                raise RuntimeError(
                    f"YouTube is temporarily rate-limiting the bot. Try again in about {max(1, int(wait))} seconds."
                )

            delay = self._youtube_min_interval - (now - self._youtube_last_request)
            if delay > 0:
                await asyncio.sleep(delay)

            self._youtube_last_request = time.monotonic()
            loop = asyncio.get_running_loop()

            def extract():
                with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                    return ydl.extract_info(query, download=False)

            try:
                info = await loop.run_in_executor(None, extract)
            except Exception as exc:
                text = str(exc).lower()
                if "429" in text or "too many requests" in text or "rate limit" in text:
                    # Back off for 60 seconds. Do not automatically retry.
                    self._youtube_backoff_until = time.monotonic() + 60
                    raise RuntimeError(
                        "YouTube is rate-limiting the bot right now. Please wait about a minute before trying again."
                    ) from exc
                raise

            if info is None:
                return None
            if "entries" in info:
                info = next((entry for entry in info["entries"] if entry), None)
            if not info or not info.get("url"):
                return None

            track = Track(
                title=info.get("title", query),
                url=info.get("webpage_url") or info.get("original_url") or "",
                # Do not retain the direct googlevideo URL. It can expire or
                # return 403 when reused later. Playback refreshes it.
                stream_url="",
                requester=None,
            )
            self._youtube_cache[query] = (time.monotonic(), track)
            self._youtube_cache.move_to_end(query)
            while len(self._youtube_cache) > self._youtube_cache_max:
                self._youtube_cache.popitem(last=False)
            return Track(track.title, track.url, "", None)

    async def _refresh_stream_url(self, track: "Track") -> str:
        """Get a fresh media URL immediately before FFmpeg starts.

        YouTube's googlevideo URLs are temporary, so storing one in the queue
        is unreliable and can produce HTTP 403 errors when playback starts later.
        """
        target = track.url or track.title
        loop = asyncio.get_running_loop()

        def extract():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                return ydl.extract_info(target, download=False)

        info = await loop.run_in_executor(None, extract)
        if info and "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info or not info.get("url"):
            raise RuntimeError("YouTube did not return a playable audio stream.")
        return info["url"]

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
        try:
            await self._start_playing(guild, next_track, seek=0.0)
        except Exception as exc:
            state.current = None
            if guild.system_channel:
                try:
                    await guild.system_channel.send(f"⚠️ Couldn't start **{next_track.title}**: `{exc}`")
                except Exception:
                    pass
            # Continue to the next queued item rather than leaving the player stuck.
            if state.queue:
                await self._play_next(guild)

    async def _start_playing(self, guild: discord.Guild, track: "Track", seek: float = 0.0):
        state = self.get_state(guild.id)
        if state.voice_client is None or not state.voice_client.is_connected():
            return

        try:
            stream_url = await self._refresh_stream_url(track)
        except Exception as exc:
            # If a stale direct URL ever exists on a queued Track, do not pass
            # it to FFmpeg. Surface the extraction error instead.
            raise RuntimeError(f"Could not refresh YouTube audio: {exc}") from exc

        before_options = FFMPEG_OPTIONS["before_options"]
        if seek > 0:
            before_options = f"-ss {seek:.2f} " + before_options

        options = "-vn -ar 48000 -ac 2 -b:a 192k"
        if state.eq_filter:
            options += f' -af "{state.eq_filter}"'

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream_url, before_options=before_options, options=options),
            volume=state.volume,
        )
        state.audio_source = source
        state.position = seek
        state.segment_start_time = time.time()

        def after_playing(error):
            if state.suppress_next_advance:
                # This "end" was us restarting the track for an EQ change,
                # not a real end — don't advance the queue.
                state.suppress_next_advance = False
                return
            fut = asyncio.run_coroutine_threadsafe(self._play_next(guild), self.bot.loop)
            try:
                fut.result()
            except Exception:
                pass

        state.voice_client.play(source, after=after_playing)

    async def _restart_current_track(self, guild: discord.Guild):
        state = self.get_state(guild.id)
        if state.current is None or state.voice_client is None:
            return
        elapsed = time.time() - state.segment_start_time
        resume_at = max(0.0, state.position + elapsed)
        state.suppress_next_advance = True
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            state.voice_client.stop()
        await self._start_playing(guild, state.current, seek=resume_at)

    @commands.hybrid_command(name="play", aliases=["p"], description="Queue a track.")
    @app_commands.describe(query="Song name, YouTube link, or Spotify link")
    async def play(self, ctx: commands.Context, *, query: str):
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            return await ctx.send("You need to be in a voice channel first.")

        state = self.get_state(ctx.guild.id)
        if state.voice_client is None or not state.voice_client.is_connected():
            state.voice_client = await ctx.author.voice.channel.connect()
            try:
                # Discord's default voice encoder bitrate is fairly low (64kbps) —
                # bump it up for noticeably better music quality, capped to what
                # the channel itself allows (boosted servers allow higher).
                channel_max = ctx.author.voice.channel.bitrate or 96000
                state.voice_client.encoder.bitrate = min(channel_max, 192000) // 1000
            except Exception:
                pass

        if ctx.interaction:
            await ctx.interaction.response.defer()

        if "open.spotify.com" in query:
            queries = await self._resolve_spotify(query)
            if not queries:
                return await ctx.send(
                    "Couldn't read that Spotify link — make sure `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` "
                    "are set, or the link might be invalid/private."
                )
        else:
            queries = [query]

        added = []
        try:
            for q in queries:
                track = await self._search_youtube(q)
                if track:
                    track.requester = ctx.author
                    state.queue.append(track)
                    added.append(track)
        except RuntimeError as exc:
            if added:
                await ctx.send(f"Queued {len(added)} track(s), but YouTube is temporarily rate-limiting further searches. {exc}")
            else:
                await ctx.send(str(exc))
            return
        except Exception:
            await ctx.send("YouTube search failed. Please try again later.")
            return

        if not added:
            return await ctx.send("Couldn't find anything to play for that.")

        if len(added) == 1:
            await ctx.send(f"🎵 Queued **{added[0].title}**")
        else:
            await ctx.send(f"🎵 Queued **{len(added)}** tracks.")

        if not state.voice_client.is_playing() and state.current is None:
            await self._play_next(ctx.guild)

    @commands.hybrid_command(name="skip", aliases=["next", "sk"], description="Skip to the next track.")
    async def skip(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
            await ctx.send("⏭️ Skipped.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.hybrid_command(name="pause", description="Pause the current track.")
    async def pause(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        if state.voice_client is None or not state.voice_client.is_playing():
            return await ctx.send("Nothing is playing right now.")
        # Bank the elapsed time so far, so a later ,eq change (or anything
        # else relying on playback position) resumes from the right spot.
        state.position += time.time() - state.segment_start_time
        state.voice_client.pause()
        await ctx.send("⏸️ Paused.")

    @commands.hybrid_command(name="resume", description="Resume the paused track.")
    async def resume(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        if state.voice_client is None or not state.voice_client.is_paused():
            return await ctx.send("Nothing is paused right now.")
        state.segment_start_time = time.time()  # restart the clock from the resume point
        state.voice_client.resume()
        await ctx.send("▶️ Resumed.")

    @commands.hybrid_command(name="disconnect", aliases=["stop", "dc"], description="Disconnect and stop playing music.")
    async def disconnect(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        state.queue.clear()
        state.current = None
        state.loop_mode = "off"
        if state.voice_client:
            await state.voice_client.disconnect()
            state.voice_client = None
        await ctx.send("👋 Disconnected.")

    @commands.hybrid_command(name="repeat", aliases=["loop"], description="Change the current loop mode.")
    @app_commands.describe(option="off, queue, or current")
    async def repeat(self, ctx: commands.Context, option: str):
        option = option.lower()
        if option not in ("off", "queue", "current"):
            return await ctx.send("Option must be one of: `off`, `queue`, `current`")
        state = self.get_state(ctx.guild.id)
        state.loop_mode = option
        await ctx.send(f"🔁 Loop mode set to **{option}**.")

    @commands.hybrid_command(name="volume", aliases=["vol"], description="Set playback volume (0-200%).")
    @app_commands.describe(percent="Volume percentage, 0-200 (100 = normal)")
    async def volume(self, ctx: commands.Context, percent: commands.Range[int, 0, 200]):
        state = self.get_state(ctx.guild.id)
        state.volume = percent / 100
        if state.audio_source:
            state.audio_source.volume = state.volume
        await ctx.send(f"🔊 Volume set to **{percent}%**.")

    @commands.hybrid_command(name="equalizer", aliases=["eq"], description="Adjust the audio equalizer for the current track.")
    async def equalizer(self, ctx: commands.Context):
        state = self.get_state(ctx.guild.id)
        if state.voice_client is None or not state.voice_client.is_connected() or state.current is None:
            return await ctx.send("Nothing is playing right now.")

        embed = discord.Embed(
            title="🎚️ Equalizer",
            description=(
                f"Current preset: **{state.eq_name}**\n\n"
                "Pick a preset below — playback will resume from this same spot with the new EQ "
                "(a brief sub-second blip while it reconnects, not a restart). Only you can use this menu."
            ),
            color=discord.Color.blurple(),
        )
        view = EqualizerView(self, ctx.guild.id, ctx.author.id)
        await ctx.send(embed=embed, view=view)

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
