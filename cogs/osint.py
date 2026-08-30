import asyncio
import ipaddress
import json
import html as html_lib
import os
import re
import socket
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

HUNTER_PLATFORMS = {
    "YouTube": ("https://www.youtube.com/@{u}", True),
    "Instagram": ("https://www.instagram.com/{u}/", True),
    "X": ("https://x.com/{u}", False),
    "GitHub": ("https://github.com/{u}", True),
    "BandLab": ("https://www.bandlab.com/{u}", True),
    "TikTok": ("https://www.tiktok.com/@{u}", True),
    "LinkedIn": ("https://www.linkedin.com/in/{u}", False),
    "Facebook": ("https://www.facebook.com/{u}", False),
}

OATHNET_BASE = "https://oathnet.org/api"


class OSINT(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session = None
        self.oath_key = os.getenv("OATHNET_API_KEY", "").strip()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15), headers=BROWSER_HEADERS
        )

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def _json(self, url, *, method="GET", payload=None, headers=None):
        h = headers or {}
        if method == "POST":
            async with self.session.post(url, json=payload, headers=h) as r:
                return r.status, await r.json(content_type=None)
        async with self.session.get(url, headers=h) as r:
            return r.status, await r.json(content_type=None)

    async def _oath_get(self, endpoint, params=None):
        if not self.oath_key:
            raise RuntimeError("OATHNET_API_KEY is not configured in Railway.")
        headers = {"x-api-key": self.oath_key, "Accept": "application/json"}
        async with self.session.get(
            f"{OATHNET_BASE}{endpoint}", params=params or {}, headers=headers
        ) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {"message": (await r.text())[:500]}
            return r.status, data

    async def _oath_error(self, ctx, status, data):
        if status == 401:
            return "OathNet API key is invalid or missing."
        if status == 403:
            return "Your OathNet plan does not have access to this endpoint."
        if status == 429:
            return "OathNet rate limit reached. Try again later."
        return str(data.get("message") or "OathNet lookup failed.")[:500]

    # ==================== Roblox ====================

    @commands.hybrid_group(name="username", aliases=["roblox"], invoke_without_command=True, description="Look up a Roblox profile by username.")
    @commands.cooldown(3, 10, commands.BucketType.user)
    @app_commands.describe(name="Roblox username to look up")
    async def username(self, ctx: commands.Context, *, name: str):
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
            return await ctx.send("Invalid Roblox username.")
        status, data = await self._json("https://users.roblox.com/v1/usernames/users", method="POST", payload={"usernames": [name], "excludeBannedUsers": False})
        if status != 200 or not data.get("data"):
            return await ctx.send("No Roblox user found.")
        user = data["data"][0]
        uid = user["id"]
        profile_url = f"https://www.roblox.com/users/{uid}/profile"
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"{user.get('displayName', name)} (@{user.get('name', name)})", url=profile_url)
        try:
            s, d = await self._json(f"https://users.roblox.com/v1/users/{uid}")
            if s == 200:
                embed.add_field(name="Created", value=d.get("created", "")[:10] or "Unknown")
        except Exception:
            pass
        embed.add_field(name="ID", value=str(uid))
        try:
            s, d = await self._json(f"https://friends.roblox.com/v1/users/{uid}/followers/count")
            if s == 200: embed.add_field(name="Followers", value=str(d.get("count", "Unknown")))
            s, d = await self._json(f"https://friends.roblox.com/v1/users/{uid}/followings/count")
            if s == 200: embed.add_field(name="Following", value=str(d.get("count", "Unknown")))
        except Exception:
            pass
        try:
            s, d = await self._json(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=png")
            if s == 200 and d.get("data"): embed.set_thumbnail(url=d["data"][0]["imageUrl"])
        except Exception:
            pass
        embed.add_field(name="Profile", value=f"[View Roblox profile]({profile_url})", inline=False)
        await ctx.send(embed=embed)

    @username.command(name="hunter", description="Check a username across several platforms at once.")
    @commands.cooldown(1, 20, commands.BucketType.user)
    @app_commands.describe(name="Username to search for")
    async def username_hunter(self, ctx: commands.Context, *, name: str):
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,30}", name):
            return await ctx.send("Invalid username — letters, numbers, `.`, `_`, and `-` only.")
        if ctx.interaction: await ctx.interaction.response.defer()
        results = []
        async def check(platform, template, reliable):
            try:
                async with self.session.get(template.format(u=name), allow_redirects=True) as resp:
                    if resp.status < 400: results.append((platform, "✅ Found" if reliable else "❔ Possibly found", template.format(u=name)))
                    elif resp.status == 404: results.append((platform, "❌ Not found", None))
                    else: results.append((platform, f"❔ Unknown (status {resp.status})", None))
            except Exception: results.append((platform, "❔ Couldn't check", None))
        await asyncio.gather(*(check(p,t,r) for p,(t,r) in HUNTER_PLATFORMS.items()))
        try:
            s,d=await self._json("https://users.roblox.com/v1/usernames/users",method="POST",payload={"usernames":[name],"excludeBannedUsers":False})
            results.append(("Roblox", "✅ Found" if s==200 and d.get("data") else "❌ Not found", f"https://www.roblox.com/users/{d['data'][0]['id']}/profile" if s==200 and d.get("data") else None))
        except Exception: results.append(("Roblox","❔ Couldn't check",None))
        results.append(("Discord", "⚠️ No public lookup available", None))
        embed=discord.Embed(title=f"Username Hunter: {name}",color=discord.Color.blurple())
        embed.description="\n".join(f"**{p}** — {s}"+(f"\n{u}" if u else "") for p,s,u in results)
        await ctx.send(embed=embed)

    # ==================== OathNet ====================

    @commands.hybrid_group(name="oath", invoke_without_command=True, description="OathNet public OSINT lookups.")
    async def oath(self, ctx: commands.Context):
        await ctx.send("Usage: `,oath discord <id>`, `,oath history <id>`, `,oath roblox <username>`, `,oath email <email>`")

    @oath.command(name="discord", description="Look up public Discord profile information through OathNet.")
    @app_commands.describe(discord_id="Discord user ID")
    @commands.cooldown(2, 20, commands.BucketType.user)
    async def oath_discord(self, ctx: commands.Context, discord_id: str):
        if not re.fullmatch(r"\d{14,19}", discord_id): return await ctx.send("Invalid Discord user ID.")
        try: status,data=await self._oath_get("/service/discord-userinfo", {"discord_id":discord_id})
        except RuntimeError as e: return await ctx.send(str(e))
        if status!=200 or not data.get("success"): return await ctx.send(await self._oath_error(ctx,status,data))
        d=data.get("data") or {}
        embed=discord.Embed(title="OathNet — Discord User",color=discord.Color.blurple())
        embed.add_field(name="Username",value=str(d.get("username","Unknown")))
        embed.add_field(name="Global Name",value=str(d.get("global_name","Unknown")))
        embed.add_field(name="ID",value=str(d.get("id",discord_id)))
        embed.add_field(name="Created",value=str(d.get("creation_date","Unknown")),inline=False)
        if d.get("avatar_url"): embed.set_thumbnail(url=d["avatar_url"])
        await ctx.send(embed=embed)

    @oath.command(name="history", description="Look up public Discord username history through OathNet.")
    @app_commands.describe(discord_id="Discord user ID")
    async def oath_history(self, ctx: commands.Context, discord_id: str):
        if not re.fullmatch(r"\d{14,19}",discord_id): return await ctx.send("Invalid Discord user ID.")
        try: status,data=await self._oath_get("/service/discord-username-history",{"discord_id":discord_id})
        except RuntimeError as e: return await ctx.send(str(e))
        if status!=200 or not data.get("success"): return await ctx.send(await self._oath_error(ctx,status,data))
        history=(data.get("data") or {}).get("history") or []
        lines=[]
        for item in history[:20]:
            if isinstance(item,dict):
                name=item.get("name") or item.get("username") or "Unknown"; when=item.get("time") or item.get("changed_at") or ""
                if isinstance(name,list): name=name[0] if name else "Unknown"
                if isinstance(when,list): when=when[0] if when else ""
                lines.append(f"`{name}`"+(f" — {when}" if when else ""))
        embed=discord.Embed(title="OathNet — Discord Username History",description="\n".join(lines) or "No history returned.",color=discord.Color.blurple())
        await ctx.send(embed=embed)

    @oath.command(name="roblox", description="Look up Roblox information through OathNet.")
    @app_commands.describe(username="Roblox username")
    async def oath_roblox(self, ctx: commands.Context, *, username: str):
        username=username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}",username): return await ctx.send("Invalid Roblox username.")
        try: status,data=await self._oath_get("/service/roblox-userinfo",{"username":username})
        except RuntimeError as e: return await ctx.send(str(e))
        if status!=200 or not data.get("success"): return await ctx.send(await self._oath_error(ctx,status,data))
        d=data.get("data") or {}
        embed=discord.Embed(title="OathNet — Roblox User",color=discord.Color.blurple())
        for key,label in (("username","Username"),("Display Name","Display Name"),("user_id","ID"),("Old Usernames","Old Usernames"),("Join Date","Created")):
            if d.get(key) is not None: embed.add_field(name=label,value=str(d[key]),inline=False)
        if d.get("Avatar URL"): embed.set_thumbnail(url=d["Avatar URL"])
        await ctx.send(embed=embed)

    @oath.command(name="email", description="Check public service associations for an email using OathNet Holehe.")
    @app_commands.describe(email="Email address")
    async def oath_email(self, ctx: commands.Context, email: str):
        email=email.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email): return await ctx.send("Invalid email address.")
        try: status,data=await self._oath_get("/service/holehe",{"email":email})
        except RuntimeError as e: return await ctx.send(str(e))
        if status!=200 or not data.get("success"): return await ctx.send(await self._oath_error(ctx,status,data))
        d=data.get("data") or {}; domains=d.get("domains") or []
        if isinstance(domains,dict): domains=list(domains.keys())
        embed=discord.Embed(title="OathNet — Email Account Check",color=discord.Color.blurple())
        embed.add_field(name="Services",value="\n".join(f"• {str(x)}" for x in domains[:50]) or "No services reported.",inline=False)
        await ctx.send(embed=embed)

    # ==================== Domain / IP ====================

    @commands.hybrid_group(name="domain",invoke_without_command=True)
    async def domain(self,ctx): await ctx.send("Usage: `,domain lookup <domain>`")

    @domain.command(name="lookup",description="Look up public DNS records for a domain.")
    async def domain_lookup(self,ctx,domain:str):
        domain=domain.strip().lower()
        if domain.startswith(("http://","https://")): domain=urlparse(domain).hostname or ""
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}",domain): return await ctx.send("Invalid domain.")
        try: infos=await self.bot.loop.run_in_executor(None,lambda:socket.getaddrinfo(domain,None)); ips=sorted({x[4][0] for x in infos})
        except socket.gaierror: return await ctx.send("Could not resolve that domain.")
        e=discord.Embed(title="Domain Lookup",color=discord.Color.blurple()); e.add_field(name="Domain",value=domain,inline=False); e.add_field(name="Resolved IPs",value="\n".join(ips[:10]) or "None"); await ctx.send(embed=e)

    @commands.hybrid_group(name="ip",invoke_without_command=True)
    async def ip(self,ctx): await ctx.send("Usage: `,ip lookup <ip address>`")

    @ip.command(name="lookup",description="Look up public IP geolocation metadata.")
    async def ip_lookup(self,ctx,address:str):
        try: addr=ipaddress.ip_address(address.strip())
        except ValueError: return await ctx.send("Invalid IP address.")
        if addr.is_private or addr.is_loopback or addr.is_reserved: return await ctx.send("That is not a public IP address.")
        status,data=await self._json(f"https://ipwho.is/{addr}")
        if status!=200 or not data.get("success",True): return await ctx.send("Could not retrieve public IP metadata.")
        e=discord.Embed(title="Public IP Lookup",color=discord.Color.blurple())
        e.add_field(name="IP",value=str(addr)); e.add_field(name="Country",value=str(data.get("country","Unknown"))); e.add_field(name="Region",value=str(data.get("region","Unknown"))); e.add_field(name="City",value=str(data.get("city","Unknown"))); e.add_field(name="ISP",value=str((data.get("connection") or {}).get("isp","Unknown")),inline=False); await ctx.send(embed=e)

    # ==================== Instagram / TikTok ====================

    @commands.hybrid_command(name="instagram",description="Look up public Instagram profile stats.")
    async def instagram(self,ctx,username:str):
        username=username.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}",username): return await ctx.send("Invalid Instagram username.")
        headers=dict(BROWSER_HEADERS); headers.update({"X-IG-App-ID":"936619743392459","X-Requested-With":"XMLHttpRequest","Accept":"application/json, text/plain, */*","Referer":f"https://www.instagram.com/{username}/"})
        for endpoint in (f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"):
            try:
                async with self.session.get(endpoint,headers=headers) as r:
                    if r.status==404:return await ctx.send("No Instagram account found with that username.")
                    if r.status!=200:continue
                    d=await r.json(content_type=None); u=(d.get("data") or {}).get("user")
                    if not u:continue
                    e=discord.Embed(color=discord.Color.from_rgb(225,48,108)); url=f"https://www.instagram.com/{username}/"; e.set_author(name=("🔒 " if u.get("is_private") else "")+f"{u.get('full_name') or username} (@{username})",url=url)
                    e.add_field(name="Posts",value=str((u.get("edge_owner_to_timeline_media") or {}).get("count","Unknown")),inline=False); e.add_field(name="Following",value=str((u.get("edge_follow") or {}).get("count","Unknown")),inline=False); e.add_field(name="Followers",value=str((u.get("edge_followed_by") or {}).get("count","Unknown")),inline=False)
                    pic=u.get("profile_pic_url_hd") or u.get("profile_pic_url")
                    if pic:e.set_thumbnail(url=pic)
                    e.add_field(name="Instagram",value=url,inline=False); return await ctx.send(embed=e)
            except Exception:continue
        try:
            async with self.session.get(f"https://www.instagram.com/{username}/",headers=BROWSER_HEADERS) as r:
                if r.status==404:return await ctx.send("No Instagram account found with that username.")
                if r.status!=200:return await ctx.send("Instagram is currently blocking profile lookups from the bot's network. Try again later.")
                html=await r.text(errors="ignore")
        except Exception:return await ctx.send("Couldn't reach Instagram right now.")
        def meta(prop):
            for p in (rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']',rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']{re.escape(prop)}["\']'):
                m=re.search(p,html,re.I)
                if m:return html_lib.unescape(m.group(1))
        title=meta("og:title") or f"@{username}"; image=meta("og:image"); desc=meta("og:description") or ""
        e=discord.Embed(color=discord.Color.from_rgb(225,48,108)); url=f"https://www.instagram.com/{username}/"; e.set_author(name=title,url=url)
        def stat(label):
            m=re.search(rf"([\d,.]+(?:[KMB])?)\s+{label}",desc,re.I); return m.group(1) if m else "Unknown"
        e.add_field(name="Posts",value=stat("Posts"),inline=False); e.add_field(name="Following",value=stat("Following"),inline=False); e.add_field(name="Followers",value=stat("Followers"),inline=False)
        if image:e.set_thumbnail(url=image)
        e.add_field(name="Instagram",value=url,inline=False); await ctx.send(embed=e)

    @commands.hybrid_command(name="tiktok",description="Look up public TikTok profile stats.")
    async def tiktok(self,ctx,username:str):
        username=username.strip().lstrip("@");
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}",username):return await ctx.send("Invalid TikTok username.")
        try:
            async with self.session.get(f"https://www.tiktok.com/@{username}") as r:
                if r.status==404:return await ctx.send("No TikTok account found with that username.")
                if r.status!=200:return await ctx.send("TikTok may be blocking the request.")
                html=await r.text()
        except Exception:return await ctx.send("Couldn't reach TikTok right now.")
        m=re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',html,re.S)
        if not m:return await ctx.send("Couldn't read that profile — TikTok may have changed its page layout.")
        try:
            data=json.loads(m.group(1)); ui=data["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]; stats=ui["stats"]; u=ui["user"]
        except (KeyError,json.JSONDecodeError):return await ctx.send("Couldn't parse that profile's data.")
        url=f"https://www.tiktok.com/@{username}"; e=discord.Embed(color=discord.Color.dark_teal()); e.set_author(name=f"{u.get('nickname',username)} (@{username})",url=url); e.add_field(name="Likes",value=str(stats.get("heartCount","Unknown"))); e.add_field(name="Followers",value=str(stats.get("followerCount","Unknown"))); e.add_field(name="Following",value=str(stats.get("followingCount","Unknown"))); 
        if u.get("avatarLarger"):e.set_thumbnail(url=u["avatarLarger"])
        e.add_field(name="Profile",value=url,inline=False); await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(OSINT(bot))
