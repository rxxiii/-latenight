import asyncio
import logging
import random
import urllib.parse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("bleedclone.games")

# ==================== Black Tea (word bomb game) ====================

TRIGRAMS = [
    # -ing / -tion / -ment / common suffix families
    "ing", "ion", "tio", "ent", "ers", "est", "ate", "ess", "ess", "ive",
    "ous", "ful", "les", "led", "ing", "ism", "ist", "ity", "ize", "ise",
    "ant", "ent", "shp", "hod", "ard", "ise", "abl", "ibl", "ang", "ung",
    # common word chunks / infixes
    "and", "the", "ere", "her", "his", "was", "for", "not", "you", "all",
    "are", "but", "out", "our", "day", "get", "has", "him", "how", "man",
    "new", "now", "old", "see", "two", "way", "who", "boy", "did", "its",
    "let", "put", "say", "she", "too", "use", "own", "any", "few", "got",
    "act", "add", "age", "ago", "aid", "air", "arm", "art", "ash", "ask",
    "bad", "bag", "ban", "bar", "bat", "bay", "bed", "bee", "beg", "bet",
    "bid", "big", "bit", "box", "bug", "bun", "bus", "buy", "cab", "cap",
    "car", "cat", "cop", "cow", "cry", "cup", "cut", "dad", "dam", "den",
    "dig", "dim", "dip", "dog", "dot", "dry", "due", "ear", "eat", "egg",
    "elk", "end", "eye", "fan", "far", "fat", "fee", "fig", "fin", "fit",
    "fix", "fly", "fog", "fox", "fun", "gap", "gas", "gem", "gun", "gym",
    "ham", "hat", "hay", "hen", "hip", "hit", "hop", "hot", "hut", "ice",
    "ink", "inn", "ivy", "jam", "jar", "jaw", "jet", "job", "jog", "joy",
    "key", "kid", "kit", "lab", "lap", "law", "lay", "leg", "lid", "lip",
    "log", "lot", "low", "mad", "map", "mat", "mix", "mud", "mug", "nap",
    "net", "nut", "oak", "oil", "opt", "orb", "owe", "owl", "pad", "pan",
    "paw", "pay", "pea", "peg", "pen", "pet", "pie", "pig", "pin", "pit",
    "pop", "pot", "pub", "pun", "pup", "rag", "ram", "rat", "raw", "ray",
    "rib", "rim", "rip", "rob", "rod", "row", "rub", "rug", "rum", "run",
    "sad", "sap", "sat", "saw", "sea", "set", "sew", "shy", "sin", "sip",
    "sit", "six", "ski", "sky", "sly", "sob", "sod", "son", "sow", "spa",
    "spy", "sum", "sun", "tab", "tag", "tan", "tap", "tar", "tax", "tea",
    "ten", "tie", "tin", "tip", "toe", "top", "toy", "try", "tub", "tug",
    "van", "vat", "vet", "vow", "wag", "wax", "web", "wed", "wet", "wig",
    "win", "wit", "wow", "yak", "yam", "yes", "yet", "you", "zip", "zoo",
    # longer/common English chunks that appear in many words
    "ock", "old", "one", "ood", "ool", "oon", "oot", "orn", "ory", "ose",
    "oud", "our", "own", "ude", "ure", "ame", "ave", "ace", "ade", "age",
    "ail", "ain", "air", "ake", "ale", "alk", "all", "and", "ang", "ank",
    "ant", "ape", "arb", "arc", "ard", "are", "ark", "arm", "art", "ash",
    "ask", "ass", "ast", "ate", "ath", "aut", "awn", "ays", "aze", "eal",
    "ean", "ear", "eat", "eck", "eed", "eek", "eel", "eem", "een", "eep",
    "eer", "ees", "eet", "eft", "egg", "eld", "elf", "ell", "elt", "elp",
    "eme", "emp", "end", "ene", "ent", "eon", "erb", "erd", "erk", "erm",
    "ern", "err", "ert", "erv", "esh", "ess", "est", "eve", "ewd", "ewn",
    "ewy", "exp", "eye", "ick", "ide", "ift", "ign", "ike", "ild", "ile",
    "ilk", "ill", "ilt", "imb", "ime", "imp", "nch", "ind", "ine", "ing",
    "ink", "int", "ipe", "ird", "irl", "irm", "irt", "ish", "isk", "isl",
    "iss", "ist", "ite", "ith", "itt", "ive", "ize", "obe", "ock", "odd",
    "ode", "off", "oft", "oil", "oke", "old", "ole", "olt", "omb", "ome",
    "omp", "ond", "one", "ong", "ony", "ood", "oof", "ook", "ool", "oom",
    "oon", "oop", "oor", "oot", "ope", "orb", "orc", "ord", "ore", "ork",
    "orm", "orn", "ort", "ory", "ose", "osh", "oss", "ost", "ote", "oth",
    "oud", "oul", "oun", "our", "out", "ove", "owl", "own", "ows", "uce",
    "uck", "uct", "ude", "uff", "uge", "ugg", "uid", "uil", "uke", "ule",
    "ulk", "ull", "ult", "umb", "ump", "und", "urk", "une", "ung", "unk",
    "unt", "urb", "urd", "ure", "url", "urn", "url", "use", "ush", "usk",
    "ust", "ute", "uth", "uzz",
]


class BlackTeaGame:
    def __init__(self, channel: discord.TextChannel, session: aiohttp.ClientSession):
        self.channel = channel
        self.session = session
        self.players: list[discord.Member] = []
        self.lives: dict[int, int] = {}
        self.used_words: set[str] = set()

    def add_player(self, member: discord.Member):
        if member.id not in self.lives:
            self.players.append(member)
            self.lives[member.id] = 2

    def alive_players(self):
        return [p for p in self.players if self.lives[p.id] > 0]

    async def is_real_word(self, word: str) -> bool:
        try:
            async with self.session.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return True  # if the dictionary API is having issues, don't punish the player for it

    async def run(self, bot: commands.Bot):
        order = self.alive_players()
        random.shuffle(order)
        idx = 0
        while len(self.alive_players()) > 1:
            order = self.alive_players()
            if not order:
                break
            player = order[idx % len(order)]
            idx += 1

            group = random.choice(TRIGRAMS)
            await self.channel.send(f"🍵 **{group}** — {player.mention}, you have 10 seconds!")

            def check(m, group=group, player=player):
                content = m.content.lower().strip()
                return (
                    m.author.id == player.id
                    and m.channel.id == self.channel.id
                    and group in content
                    and content not in self.used_words
                    and content.isalpha()
                    and len(content) >= 3
                )

            failed_turn = False
            timed_out = False
            try:
                msg = await bot.wait_for("message", check=check, timeout=10)
                word = msg.content.lower().strip()
                if await self.is_real_word(word):
                    self.used_words.add(word)
                    try:
                        await msg.add_reaction("✅")
                    except discord.HTTPException:
                        pass
                else:
                    failed_turn = True
                    try:
                        await msg.add_reaction("❌")
                    except discord.HTTPException:
                        pass
            except asyncio.TimeoutError:
                failed_turn = True
                timed_out = True

            if failed_turn:
                self.lives[player.id] -= 1
                if self.lives[player.id] <= 0:
                    await self.channel.send(f"💀 {player.mention} is out of lives and eliminated!")
                elif timed_out:
                    await self.channel.send(
                        f"⏱️ Time's up! {player.mention} loses a life ({self.lives[player.id]} left)."
                    )
                else:
                    await self.channel.send(
                        f"❌ Not a real word! {player.mention} loses a life ({self.lives[player.id]} left)."
                    )

        survivors = self.alive_players()
        if survivors:
            await self.channel.send(f"🏆 {survivors[0].mention} wins Black Tea!")
        else:
            await self.channel.send("Game over — no survivors!")


# ==================== Tic-Tac-Toe ====================

class TicTacToeButton(discord.ui.Button):
    def __init__(self, x: int, y: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=y)
        self.x = x
        self.y = y

    async def callback(self, interaction: discord.Interaction):
        view: "TicTacToeView" = self.view
        if interaction.user.id != view.current_player.id:
            return await interaction.response.send_message("It's not your turn.", ephemeral=True)
        if view.board[self.y][self.x] is not None:
            return await interaction.response.send_message("That cell is already taken.", ephemeral=True)

        symbol = "X" if view.current_player.id == view.player_x.id else "O"
        view.board[self.y][self.x] = symbol
        self.label = symbol
        self.style = discord.ButtonStyle.danger if symbol == "X" else discord.ButtonStyle.primary
        self.disabled = True

        winner_symbol = view.check_winner()
        if winner_symbol:
            for item in view.children:
                item.disabled = True
            winner = view.player_x if winner_symbol == "X" else view.player_o
            await interaction.response.edit_message(content=f"🎉 {winner.mention} wins!", view=view)
            view.stop()
            return

        if view.is_draw():
            for item in view.children:
                item.disabled = True
            await interaction.response.edit_message(content="It's a draw!", view=view)
            view.stop()
            return

        view.current_player = view.player_o if view.current_player.id == view.player_x.id else view.player_x
        await interaction.response.edit_message(
            content=f"**{view.player_x.display_name}** vs **{view.player_o.display_name}**\n{view.current_player.mention}, your turn.",
            view=view,
        )


class TicTacToeView(discord.ui.View):
    def __init__(self, player_x: discord.Member, player_o: discord.Member):
        super().__init__(timeout=300)
        self.player_x = player_x
        self.player_o = player_o
        self.current_player = player_x
        self.board = [[None, None, None] for _ in range(3)]
        for y in range(3):
            for x in range(3):
                self.add_item(TicTacToeButton(x, y))

    def check_winner(self):
        b = self.board
        lines = list(b)
        lines += [[b[r][c] for r in range(3)] for c in range(3)]
        lines.append([b[i][i] for i in range(3)])
        lines.append([b[i][2 - i] for i in range(3)])
        for line in lines:
            if line[0] is not None and line[0] == line[1] == line[2]:
                return line[0]
        return None

    def is_draw(self):
        return all(cell is not None for row in self.board for cell in row) and not self.check_winner()


# ==================== GeoGuess ====================

# (Wikipedia article title, country name, accepted alternate answers)
LANDMARKS = [
    ("Eiffel_Tower", "France", ["france"]),
    ("Great_Wall_of_China", "China", ["china"]),
    ("Christ_the_Redeemer_(statue)", "Brazil", ["brazil"]),
    ("Taj_Mahal", "India", ["india"]),
    ("Sydney_Opera_House", "Australia", ["australia"]),
    ("Big_Ben", "United Kingdom", ["uk", "united kingdom", "england", "britain"]),
    ("Statue_of_Liberty", "United States", ["usa", "united states", "america"]),
    ("Colosseum", "Italy", ["italy"]),
    ("Machu_Picchu", "Peru", ["peru"]),
    ("Burj_Khalifa", "United Arab Emirates", ["uae", "united arab emirates", "dubai"]),
    ("Petra", "Jordan", ["jordan"]),
    ("Chichen_Itza", "Mexico", ["mexico"]),
    ("Mount_Fuji", "Japan", ["japan"]),
    ("Neuschwanstein_Castle", "Germany", ["germany"]),
    ("Acropolis_of_Athens", "Greece", ["greece"]),
    ("Table_Mountain", "South Africa", ["south africa"]),
    ("CN_Tower", "Canada", ["canada"]),
    ("Angkor_Wat", "Cambodia", ["cambodia"]),
    ("Red_Square", "Russia", ["russia"]),
    ("Golden_Gate_Bridge", "United States", ["usa", "united states", "america"]),
]


def flag_emoji(iso_code: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso_code.upper())


# Maps a guessable country name/alias (lowercase) to its ISO 3166-1 alpha-2
# code, used to show a flag next to ANY guess, right or wrong.
COUNTRY_CODES = {
    "france": "FR", "china": "CN", "brazil": "BR", "india": "IN", "australia": "AU",
    "uk": "GB", "united kingdom": "GB", "england": "GB", "britain": "GB", "scotland": "GB",
    "usa": "US", "united states": "US", "america": "US", "italy": "IT", "peru": "PE",
    "uae": "AE", "united arab emirates": "AE", "dubai": "AE", "jordan": "JO", "mexico": "MX",
    "japan": "JP", "germany": "DE", "greece": "GR", "south africa": "ZA", "canada": "CA",
    "cambodia": "KH", "russia": "RU", "spain": "ES", "portugal": "PT", "netherlands": "NL",
    "egypt": "EG", "turkey": "TR", "thailand": "TH", "vietnam": "VN", "indonesia": "ID",
    "south korea": "KR", "korea": "KR", "north korea": "KP", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "nigeria": "NG", "kenya": "KE", "morocco": "MA", "saudi arabia": "SA",
    "israel": "IL", "iran": "IR", "iraq": "IQ", "pakistan": "PK", "bangladesh": "BD",
    "philippines": "PH", "malaysia": "MY", "singapore": "SG", "new zealand": "NZ",
    "ireland": "IE", "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI",
    "poland": "PL", "ukraine": "UA", "switzerland": "CH", "austria": "AT", "belgium": "BE",
    "czech republic": "CZ", "hungary": "HU", "romania": "RO", "croatia": "HR", "iceland": "IS",
    "cuba": "CU", "jamaica": "JM", "ecuador": "EC", "venezuela": "VE", "bolivia": "BO",
    "uruguay": "UY", "paraguay": "PY", "panama": "PA", "costa rica": "CR", "guatemala": "GT",
    "ethiopia": "ET", "ghana": "GH", "algeria": "DZ", "tunisia": "TN", "libya": "LY",
    "sri lanka": "LK", "nepal": "NP", "myanmar": "MM", "laos": "LA", "mongolia": "MN",
    "kazakhstan": "KZ", "afghanistan": "AF", "syria": "SY", "lebanon": "LB", "qatar": "QA",
    "kuwait": "KW", "oman": "OM", "yemen": "YE", "georgia": "GE", "armenia": "AM",
    "azerbaijan": "AZ", "belarus": "BY", "serbia": "RS", "slovakia": "SK", "slovenia": "SI",
    "bulgaria": "BG", "estonia": "EE", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "malta": "MT", "cyprus": "CY", "monaco": "MC", "andorra": "AD", "liechtenstein": "LI",
    "vatican": "VA", "san marino": "SM",
}


class Games(commands.Cog):
    """Black Tea, Tic-Tac-Toe, and GeoGuess mini-games."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_blacktea: set[int] = set()
        self.active_geo: set[int] = set()
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "DiscordBleedClone/1.0 (contact: bot-owner@example.com)"}
        )

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    # ---------- blacktea ----------

    @commands.hybrid_command(name="blacktea", description="Start a Black Tea word game.")
    async def blacktea(self, ctx: commands.Context):
        if ctx.channel.id in self.active_blacktea:
            return await ctx.send("A Black Tea game is already running in this channel.")
        self.active_blacktea.add(ctx.channel.id)

        try:
            game = BlackTeaGame(ctx.channel, self.session)
            embed = discord.Embed(
                title="⏰ Waiting for players, react with ✅ to join. The game will begin in 30 seconds.",
                description=(
                    "**GOAL:** You have **10** seconds to say a word containing the given group of **3** "
                    "letters. Failure to do so within the 10 seconds will lose a life. Each player has "
                    "**2** lives to begin with.\n\n"
                    "**NOTES:** A word can only be used **once** through the course of the game."
                ),
                color=discord.Color.dark_teal(),
            )
            join_message = await ctx.send(embed=embed)
            await join_message.add_reaction("✅")

            await asyncio.sleep(30)

            join_message = await ctx.channel.fetch_message(join_message.id)
            reaction = discord.utils.get(join_message.reactions, emoji="✅")
            if reaction:
                async for user in reaction.users():
                    if not user.bot:
                        game.add_player(user)

            if len(game.players) < 2:
                return await ctx.send("Not enough players joined — need at least 2.")

            await ctx.send(f"Starting with {len(game.players)} players: {', '.join(p.mention for p in game.players)}")
            await game.run(self.bot)
        finally:
            self.active_blacktea.discard(ctx.channel.id)

    # ---------- tic-tac-toe ----------

    @commands.hybrid_command(name="ttt", description="Play Tic-Tac-Toe against another member.")
    @app_commands.describe(opponent="Who you want to challenge")
    async def ttt(self, ctx: commands.Context, opponent: discord.Member):
        if opponent.bot or opponent.id == ctx.author.id:
            return await ctx.send("Pick a real opponent who isn't you or a bot.")
        view = TicTacToeView(ctx.author, opponent)
        await ctx.send(
            f"**{ctx.author.display_name}** vs **{opponent.display_name}**\n{ctx.author.mention}, your turn.",
            view=view,
        )

    # ---------- geoguess ----------

    async def _fetch_wiki_image(self, title: str):
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning("GeoGuess: Wikipedia returned %s for %s", resp.status, title)
                    return None
                data = await resp.json()
        except Exception as e:
            log.warning("GeoGuess: request failed for %s: %s", title, e)
            return None
        thumb = data.get("originalimage") or data.get("thumbnail")
        return thumb.get("source") if thumb else None

    @commands.hybrid_command(name="geoguess", aliases=["geoguessr"], description="5-round game — guess the country shown in each photo.")
    @app_commands.describe(points_per_round="Points awarded for a correct guess each round (default 3)")
    async def geoguess(self, ctx: commands.Context, points_per_round: commands.Range[int, 1, 20] = 3):
        if ctx.channel.id in self.active_geo:
            return await ctx.send("A GeoGuess game is already running in this channel.")
        self.active_geo.add(ctx.channel.id)

        try:
            scores: dict[int, int] = {}
            names: dict[int, str] = {}
            pool = LANDMARKS.copy()
            random.shuffle(pool)

            await ctx.send("🌍 **GeoGuess** starting — 5 rounds, guess the **country** shown in each photo! Everyone has **20 seconds** per round.")

            rounds_played = 0
            for title, country, aliases in pool:
                if rounds_played >= 5:
                    break
                image_url = await self._fetch_wiki_image(title)
                if image_url is None:
                    continue
                rounds_played += 1

                embed = discord.Embed(title=f"Round {rounds_played}/5 — Where is this?", color=discord.Color.green())
                embed.set_image(url=image_url)
                embed.add_field(name="Guesses", value="*Waiting for guesses...*", inline=False)
                round_message = await ctx.send(embed=embed)

                valid_answers = {country.lower()} | {a.lower() for a in aliases}
                guess_lines: list[str] = []
                correct_this_round: list[discord.Member] = []
                seen_correct: set[int] = set()

                def check(m):
                    return m.channel.id == ctx.channel.id and not m.author.bot and m.content.strip()

                loop = asyncio.get_event_loop()
                end_time = loop.time() + 20
                no_ping = discord.AllowedMentions(users=False)

                while True:
                    remaining = end_time - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        msg = await self.bot.wait_for("message", check=check, timeout=remaining)
                    except asyncio.TimeoutError:
                        break

                    guess_text = msg.content.strip()
                    guess_lower = guess_text.lower()
                    is_correct = guess_lower in valid_answers
                    iso = COUNTRY_CODES.get(guess_lower)
                    flag = flag_emoji(iso) + " " if iso else ""

                    line = f"{flag}{msg.author.mention}: {guess_text}"
                    if is_correct:
                        line += " ✅"
                        if msg.author.id not in seen_correct:
                            seen_correct.add(msg.author.id)
                            correct_this_round.append(msg.author)
                            scores[msg.author.id] = scores.get(msg.author.id, 0) + points_per_round
                            names[msg.author.id] = msg.author.display_name
                    guess_lines.append(line)

                    embed.set_field_at(0, name="Guesses", value="\n".join(guess_lines[-15:]), inline=False)
                    try:
                        await round_message.edit(embed=embed, allowed_mentions=no_ping)
                    except discord.HTTPException:
                        pass

                if correct_this_round:
                    mentions = ", ".join(p.mention for p in correct_this_round)
                    await ctx.send(f"⏱️ Round over! It was **{country}**. Correct: {mentions} (+{points_per_round} each)", allowed_mentions=no_ping)
                else:
                    await ctx.send(f"⏱️ Round over! No one got it — it was **{country}**.")

            if rounds_played == 0:
                return await ctx.send("Couldn't load any location images right now — try again in a bit.")
            if not scores:
                return await ctx.send("Game over — no one scored any points!")

            leaderboard = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            lines = [f"**{i + 1}.** {names[uid]} — {pts} pts" for i, (uid, pts) in enumerate(leaderboard)]
            embed = discord.Embed(title="🏁 GeoGuess Results", description="\n".join(lines), color=discord.Color.gold())
            await ctx.send(embed=embed)
        finally:
            self.active_geo.discard(ctx.channel.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
