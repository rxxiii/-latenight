"""
Socials commands.

Provides:
    ,socials instagram <username>
    ,socials tiktok <username>

Also available as slash commands:
    /socials instagram
    /socials tiktok

No database changes are required.
"""

import json
import logging
import re
from html import unescape

import aiohttp
import discord
from discord.ext import commands


log = logging.getLogger("bleedclone.socials")


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
}


USERNAME_RE = re.compile(
    r"^[A-Za-z0-9._]{1,30}$"
)


class Socials(commands.Cog):
    """
    Social-media profile lookup commands.

    Only Instagram and TikTok are included.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None

    # ========================================================
    # SESSION
    # ========================================================

    async def cog_load(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers=BROWSER_HEADERS,
            )

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

        self.session = None

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def clean_username(username: str) -> str:
        username = username.strip()

        if username.startswith("@"):
            username = username[1:]

        return username

    @staticmethod
    def valid_username(username: str) -> bool:
        return bool(USERNAME_RE.fullmatch(username))

    async def get_page(
        self,
        url: str,
        *,
        headers: dict | None = None,
    ):
        """
        Fetch a public profile page.

        Returns:
            (status, text)
        """

        if self.session is None or self.session.closed:
            await self.cog_load()

        request_headers = dict(BROWSER_HEADERS)

        if headers:
            request_headers.update(headers)

        try:
            async with self.session.get(
                url,
                headers=request_headers,
                allow_redirects=True,
            ) as response:

                text = await response.text(
                    errors="ignore"
                )

                return response.status, text

        except aiohttp.ClientError as exc:
            log.warning(
                "HTTP request failed for %s: %s",
                url,
                exc,
            )
            return None, None

        except TimeoutError:
            log.warning(
                "Request timed out for %s",
                url,
            )
            return None, None

    @staticmethod
    def meta_content(
        html: str,
        property_name: str,
    ) -> str | None:
        """
        Extract an OpenGraph/meta property while supporting
        either attribute order.
        """

        patterns = (
            rf'<meta[^>]+property=["\']'
            rf'{re.escape(property_name)}["\'][^>]+'
            rf'content=["\']([^"\']*)["\']',

            rf'<meta[^>]+content=["\']'
            rf'([^"\']*)["\'][^>]+property=["\']'
            rf'{re.escape(property_name)}["\']',
        )

        for pattern in patterns:
            match = re.search(
                pattern,
                html,
                re.IGNORECASE,
            )

            if match:
                return unescape(match.group(1))

        return None

    @staticmethod
    def extract_json_scripts(
        html: str,
    ) -> list[dict]:
        """
        Extract JSON objects from common script tags.

        This is intentionally tolerant because social-media
        websites change their HTML structure frequently.
        """

        results: list[dict] = []

        patterns = (
            r'<script[^>]+type=["\']application/json["\'][^>]*>'
            r'(.*?)</script>',

            r'<script[^>]+id=["\'][^"\']+["\'][^>]*>'
            r'(.*?)</script>',
        )

        for pattern in patterns:
            for match in re.finditer(
                pattern,
                html,
                re.IGNORECASE | re.DOTALL,
            ):
                raw = match.group(1).strip()

                if not raw:
                    continue

                try:
                    value = json.loads(raw)

                    if isinstance(value, dict):
                        results.append(value)

                except (json.JSONDecodeError, TypeError):
                    continue

        return results

    @staticmethod
    def find_recursive(
        data,
        keys: set[str],
    ):
        """
        Recursively search nested dictionaries/lists for
        any requested key.
        """

        if isinstance(data, dict):

            for key in keys:
                if key in data:
                    return data[key]

            for value in data.values():
                found = Socials.find_recursive(
                    value,
                    keys,
                )

                if found is not None:
                    return found

        elif isinstance(data, list):

            for value in data:
                found = Socials.find_recursive(
                    value,
                    keys,
                )

                if found is not None:
                    return found

        return None

    @staticmethod
    def first_value(
        data,
        keys: tuple[str, ...],
        default="Unknown",
    ):
        for key in keys:
            value = Socials.find_recursive(
                data,
                {key},
            )

            if value is not None and value != "":
                return value

        return default

    @staticmethod
    def format_number(value) -> str:
        if value is None:
            return "Unknown"

        if isinstance(value, bool):
            return "Unknown"

        try:
            number = int(value)

            if number >= 1_000_000_000:
                return f"{number / 1_000_000_000:.1f}B"

            if number >= 1_000_000:
                return f"{number / 1_000_000:.1f}M"

            if number >= 1_000:
                return f"{number / 1_000:.1f}K"

            return f"{number:,}"

        except (ValueError, TypeError):
            return str(value)

    # ========================================================
    # SOCIALS GROUP
    # ========================================================

    @commands.hybrid_group(
        name="socials",
        invoke_without_command=True,
        description="Look up public Instagram and TikTok profiles.",
    )
    async def socials(self, ctx: commands.Context):
        """
        Social-media lookup commands.
        """

        await ctx.send(
            "Use `,socials instagram <username>` or "
            "`,socials tiktok <username>`."
        )

    # ========================================================
    # INSTAGRAM
    # ========================================================

    @socials.command(
        name="instagram",
        description="Look up a public Instagram profile.",
    )
    async def socials_instagram(
        self,
        ctx: commands.Context,
        username: str,
    ):
        username = self.clean_username(username)

        if not self.valid_username(username):
            await ctx.send(
                "Invalid Instagram username."
            )
            return

        url = (
            f"https://www.instagram.com/"
            f"{username}/"
        )

        if ctx.interaction:
            await ctx.defer()

        # ----------------------------------------------------
        # Try Instagram's public web profile endpoint first.
        # ----------------------------------------------------

        endpoint = (
            "https://www.instagram.com/api/v1/"
            "users/web_profile_info/"
            f"?username={username}"
        )

        headers = {
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Referer": url,
        }

        status, body = await self.get_page(
            endpoint,
            headers=headers,
        )

        user = None

        if status == 200 and body:
            try:
                data = json.loads(body)

                user = (
                    data
                    .get("data", {})
                    .get("user")
                )

            except (
                json.JSONDecodeError,
                AttributeError,
                TypeError,
            ):
                user = None

        # ----------------------------------------------------
        # Fallback: normal Instagram profile page.
        # ----------------------------------------------------

        page_html = None

        if not user:

            status, page_html = await self.get_page(
                url
            )

            if status == 404:
                await ctx.send(
                    "No Instagram account was found "
                    f"for `{username}`."
                )
                return

            if status != 200 or not page_html:
                await ctx.send(
                    "Instagram is currently blocking profile "
                    "lookups from the bot's network. "
                    "Try again later."
                )
                return

            # Look for embedded user information.
            scripts = self.extract_json_scripts(
                page_html
            )

            for script in scripts:

                candidate = self.find_recursive(
                    script,
                    {"user"},
                )

                if isinstance(candidate, dict):

                    possible_username = (
                        candidate.get("username")
                    )

                    if (
                        possible_username
                        and possible_username.lower()
                        == username.lower()
                    ):
                        user = candidate
                        break

        # ----------------------------------------------------
        # If structured data worked.
        # ----------------------------------------------------

        if isinstance(user, dict):

            display_name = (
                user.get("full_name")
                or user.get("name")
                or username
            )

            actual_username = (
                user.get("username")
                or username
            )

            is_private = bool(
                user.get("is_private")
            )

            title = (
                f"🔒 {display_name}"
                if is_private
                else str(display_name)
            )

            embed = discord.Embed(
                title="Instagram",
                color=discord.Color.from_rgb(
                    225,
                    48,
                    108,
                ),
                url=url,
            )

            embed.set_author(
                name=(
                    f"{title} "
                    f"(@{actual_username})"
                ),
                url=url,
            )

            posts = self.find_recursive(
                user,
                {
                    "media_count",
                    "post_count",
                },
            )

            if posts is None:
                timeline = user.get(
                    "edge_owner_to_timeline_media"
                )

                if isinstance(timeline, dict):
                    posts = timeline.get("count")

            followers = self.find_recursive(
                user,
                {
                    "follower_count",
                    "followers",
                },
            )

            if followers is None:
                edge = user.get(
                    "edge_followed_by"
                )

                if isinstance(edge, dict):
                    followers = edge.get("count")

            following = self.find_recursive(
                user,
                {
                    "following_count",
                    "following",
                },
            )

            if following is None:
                edge = user.get(
                    "edge_follow"
                )

                if isinstance(edge, dict):
                    following = edge.get("count")

            embed.add_field(
                name="Followers",
                value=self.format_number(
                    followers
                ),
                inline=True,
            )

            embed.add_field(
                name="Following",
                value=self.format_number(
                    following
                ),
                inline=True,
            )

            embed.add_field(
                name="Posts",
                value=self.format_number(
                    posts
                ),
                inline=True,
            )

            profile_picture = (
                user.get("profile_pic_url_hd")
                or user.get("profile_pic_url")
                or user.get("hd_profile_pic_url_info", {})
                .get("url")
                if isinstance(
                    user.get("hd_profile_pic_url_info"),
                    dict,
                )
                else None
            )

            if profile_picture:
                embed.set_thumbnail(
                    url=profile_picture
                )

            bio = (
                user.get("biography")
                or user.get("biography_with_entities", {})
                .get("raw_text")
                if isinstance(
                    user.get(
                        "biography_with_entities"
                    ),
                    dict,
                )
                else user.get("biography")
            )

            if bio:
                bio = str(bio).strip()

                if len(bio) > 1000:
                    bio = bio[:997] + "..."

                embed.add_field(
                    name="Bio",
                    value=bio,
                    inline=False,
                )

            embed.add_field(
                name="Profile",
                value=f"[Open Instagram]({url})",
                inline=False,
            )

            await ctx.send(embed=embed)
            return

        # ----------------------------------------------------
        # Final fallback: OpenGraph metadata.
        # ----------------------------------------------------

        if not page_html:
            status, page_html = await self.get_page(
                url
            )

        if not page_html:
            await ctx.send(
                "Couldn't reach Instagram right now."
            )
            return

        title = (
            self.meta_content(
                page_html,
                "og:title",
            )
            or f"@{username}"
        )

        description = (
            self.meta_content(
                page_html,
                "og:description",
            )
            or ""
        )

        image = self.meta_content(
            page_html,
            "og:image",
        )

        embed = discord.Embed(
            title="Instagram",
            color=discord.Color.from_rgb(
                225,
                48,
                108,
            ),
            url=url,
        )

        embed.set_author(
            name=title,
            url=url,
        )

        def extract_stat(
            label: str,
        ) -> str:

            match = re.search(
                rf"([\d,.]+(?:[KMB])?)\s+"
                rf"{label}",
                description,
                re.IGNORECASE,
            )

            return (
                match.group(1)
                if match
                else "Unknown"
            )

        embed.add_field(
            name="Followers",
            value=extract_stat(
                "Followers"
            ),
            inline=True,
        )

        embed.add_field(
            name="Following",
            value=extract_stat(
                "Following"
            ),
            inline=True,
        )

        embed.add_field(
            name="Posts",
            value=extract_stat(
                "Posts"
            ),
            inline=True,
        )

        if image:
            embed.set_thumbnail(
                url=image
            )

        embed.add_field(
            name="Profile",
            value=f"[Open Instagram]({url})",
            inline=False,
        )

        await ctx.send(embed=embed)

    # ========================================================
    # TIKTOK
    # ========================================================

    @socials.command(
        name="tiktok",
        description="Look up a public TikTok profile.",
    )
    async def socials_tiktok(
        self,
        ctx: commands.Context,
        username: str,
    ):
        username = self.clean_username(username)

        if not self.valid_username(username):
            await ctx.send(
                "Invalid TikTok username."
            )
            return

        url = (
            f"https://www.tiktok.com/"
            f"@{username}"
        )

        if ctx.interaction:
            await ctx.defer()

        status, html = await self.get_page(
            url
        )

        if status == 404:
            await ctx.send(
                "No TikTok account was found "
                f"for `{username}`."
            )
            return

        if status != 200 or not html:
            await ctx.send(
                "TikTok is currently blocking "
                "profile lookups from the bot's network. "
                "Try again later."
            )
            return

        # ----------------------------------------------------
        # TikTok embeds profile information in several
        # different JSON formats depending on the page.
        # ----------------------------------------------------

        user = None
        stats = None

        # ----------------------------------------------------
        # Format 1:
        # __UNIVERSAL_DATA_FOR_REHYDRATION__
        # ----------------------------------------------------

        universal_match = re.search(
            r'<script[^>]+id=["\']'
            r'__UNIVERSAL_DATA_FOR_REHYDRATION__'
            r'["\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        )

        if universal_match:

            try:
                data = json.loads(
                    universal_match.group(1)
                )

                default_scope = (
                    data
                    .get("__DEFAULT_SCOPE__", {})
                )

                detail = (
                    default_scope.get(
                        "webapp.user-detail"
                    )
                    or {}
                )

                info = detail.get(
                    "userInfo"
                ) or {}

                user = info.get("user")
                stats = info.get("stats")

            except (
                json.JSONDecodeError,
                AttributeError,
                TypeError,
            ):
                user = None
                stats = None

        # ----------------------------------------------------
        # Format 2: SIGI_STATE / embedded JSON.
        # ----------------------------------------------------

        if not user or not stats:

            scripts = self.extract_json_scripts(
                html
            )

            for script in scripts:

                candidate_user = self.find_recursive(
                    script,
                    {
                        "userInfo",
                    },
                )

                if isinstance(
                    candidate_user,
                    dict,
                ):
                    possible_user = (
                        candidate_user.get(
                            "user"
                        )
                    )

                    possible_stats = (
                        candidate_user.get(
                            "stats"
                        )
                    )

                    if isinstance(
                        possible_user,
                        dict,
                    ):
                        user = possible_user

                    if isinstance(
                        possible_stats,
                        dict,
                    ):
                        stats = possible_stats

                    if user:
                        break

        # ----------------------------------------------------
        # Format 3: Extract the stats directly from the HTML.
        # ----------------------------------------------------

        if not user:
            # Try to find a username/nickname in embedded
            # JSON without depending on one exact structure.

            nickname_match = re.search(
                r'"nickname"\s*:\s*"([^"]+)"',
                html,
            )

            unique_id_match = re.search(
                r'"uniqueId"\s*:\s*"([^"]+)"',
                html,
            )

            avatar_match = re.search(
                r'"avatarLarger"\s*:\s*"([^"]+)"',
                html,
            )

            if (
                nickname_match
                or unique_id_match
            ):
                user = {
                    "nickname": (
                        nickname_match.group(1)
                        if nickname_match
                        else username
                    ),
                    "uniqueId": (
                        unique_id_match.group(1)
                        if unique_id_match
                        else username
                    ),
                }

                if avatar_match:
                    user["avatarLarger"] = (
                        avatar_match.group(1)
                        .replace("\\/", "/")
                    )

                stats = {}

                patterns = {
                    "followerCount": (
                        r'"followerCount"\s*:\s*(\d+)'
                    ),
                    "followingCount": (
                        r'"followingCount"\s*:\s*(\d+)'
                    ),
                    "heartCount": (
                        r'"heartCount"\s*:\s*(\d+)'
                    ),
                    "videoCount": (
                        r'"videoCount"\s*:\s*(\d+)'
                    ),
                }

                for key, pattern in patterns.items():

                    match = re.search(
                        pattern,
                        html,
                    )

                    if match:
                        stats[key] = int(
                            match.group(1)
                        )

        # ----------------------------------------------------
        # Nothing could be extracted.
        # ----------------------------------------------------

        if not isinstance(user, dict):
            await ctx.send(
                "Couldn't read that TikTok profile. "
                "TikTok may have changed its page format "
                "or blocked the bot's network."
            )
            return

        if not isinstance(stats, dict):
            stats = {}

        nickname = (
            user.get("nickname")
            or user.get("displayName")
            or username
        )

        actual_username = (
            user.get("uniqueId")
            or username
        )

        followers = stats.get(
            "followerCount"
        )

        following = stats.get(
            "followingCount"
        )

        likes = stats.get(
            "heartCount"
        )

        videos = stats.get(
            "videoCount"
        )

        embed = discord.Embed(
            title="TikTok",
            color=discord.Color.dark_teal(),
            url=url,
        )

        embed.set_author(
            name=(
                f"{nickname} "
                f"(@{actual_username})"
            ),
            url=url,
        )

        embed.add_field(
            name="Followers",
            value=self.format_number(
                followers
            ),
            inline=True,
        )

        embed.add_field(
            name="Following",
            value=self.format_number(
                following
            ),
            inline=True,
        )

        embed.add_field(
            name="Likes",
            value=self.format_number(
                likes
            ),
            inline=True,
        )

        if videos is not None:
            embed.add_field(
                name="Videos",
                value=self.format_number(
                    videos
                ),
                inline=True,
            )

        avatar = (
            user.get("avatarLarger")
            or user.get("avatarMedium")
            or user.get("avatarThumb")
        )

        if avatar:
            avatar = str(avatar).replace(
                "\\/",
                "/",
            )

            embed.set_thumbnail(
                url=avatar
            )

        signature = (
            user.get("signature")
            or user.get("bio")
        )

        if signature:
            signature = str(
                signature
            ).strip()

            if len(signature) > 1000:
                signature = (
                    signature[:997]
                    + "..."
                )

            embed.add_field(
                name="Bio",
                value=signature,
                inline=False,
            )

        embed.add_field(
            name="Profile",
            value=f"[Open TikTok]({url})",
            inline=False,
        )

        await ctx.send(embed=embed)


# ============================================================
# SETUP
# ============================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        Socials(bot)
    )
