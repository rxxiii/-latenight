import asyncio
import random
import urllib.parse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# ==================== Black Tea (word bomb game) ====================

TRIGRAMS = [
    "ing", "ate", "est", "ent", "ers", "ove", "ake", "ind", "ost", "and",
    "ear", "ide", "ock", "all", "ant", "art", "ase", "ath", "eat", "eek",
    "eer", "ell", "elt", "eme", "ene", "ill", "ine", "ink", "ion", "ish",
    "ist", "ite", "old", "one", "ood", "ool", "oon", "oot", "orn", "ory",
    "ose", "oud", "our", "ous", "own", "ude", "ure", "ame", "ave", "ace",
]


class BlackTeaGame:
    def __init__(self, channel: discord.TextChannel):
        self.channel = channel
        self.players: list[discord.Member] = []
        self.lives: dict[int, int] = {}
        self.used_words: set[str] = set()

    def add_player(self, member: discord.Member):
        if member.id not in self.lives:
            self.players.append(member)
            self.lives[member.id] = 2

    def alive_players(self):
        return [p for p in self.players if self.lives[p.id] > 0]

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

            try:
                msg = await bot.wait_for("message", check=check, timeout=10)
                self.used_words.add(msg.content.lower().strip())
                try:
                    await msg.add_reaction("✅")
                except discord.HTTPException:
                    pass
            except asyncio.TimeoutError:
                self.lives[player.id] -= 1
                if self.lives[player.id] <= 0:
                    await self.channel.send(f"💀 {player.mention} is out of lives and eliminated!")
                else:
                    await self.channel.send(
                        f"⏱️ Time's up! {player.mention} loses a life ({self.lives[player.id]} left)."
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


class Games(commands.Cog):
    """Black Tea, Tic-Tac-Toe, and GeoGuess mini-games."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_blacktea: set[int] = set()
        self.active_geo: set[int] = set()
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

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
            game = BlackTeaGame(ctx.channel)
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
                    return None
                data = await resp.json()
        except Exception:
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

            await ctx.send("🌍 **GeoGuess** starting — 5 rounds, guess the **country** shown in each photo!")

            rounds_played = 0
            for title, country, aliases in pool:
                if rounds_played >= 5:
                    break
                image_url = await self._fetch_wiki_image(title)
                if image_url is None:
                    continue
                rounds_played += 1

                embed = discord.Embed(title=f"Round {rounds_played}/5", description="Where is this?", color=discord.Color.green())
                embed.set_image(url=image_url)
                await ctx.send(embed=embed)

                valid_answers = {country.lower()} | {a.lower() for a in aliases}

                def check(m, valid_answers=valid_answers):
                    return m.channel.id == ctx.channel.id and not m.author.bot and m.content.lower().strip() in valid_answers

                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=20)
                    scores[msg.author.id] = scores.get(msg.author.id, 0) + points_per_round
                    names[msg.author.id] = msg.author.display_name
                    await ctx.send(f"✅ {msg.author.mention} got it! It was **{country}**. (+{points_per_round} points)")
                except asyncio.TimeoutError:
                    await ctx.send(f"⏱️ Time's up! It was **{country}**.")

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
