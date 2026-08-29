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
        self.invalid_groups: set[str] = set()  # groups that are words themselves

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

    async def choose_group(self) -> str:
        """Pick a 3-letter sequence that is not itself a valid English word.

        This prevents prompts such as `art`, where answering `art` would
        satisfy the challenge without actually extending the sequence.
        """
        candidates = TRIGRAMS.copy()
        random.shuffle(candidates)
        for group in candidates:
            group = group.lower()
            if group in self.invalid_groups:
                continue
            if await self.is_real_word(group):
                self.invalid_groups.add(group)
                continue
            return group
        # Fallback: use a non-word-looking sequence from the pool.
        return random.choice([g for g in TRIGRAMS if g not in self.invalid_groups])

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

            group = await self.choose_group()
            await self.channel.send(f"🍵 **{group}** — {player.mention}, you have 10 seconds!")

            def check(m, group=group, player=player):
                content = m.content.lower().strip()
                return (
                    m.author.id == player.id
                    and m.channel.id == self.channel.id
                    and group in content
                    and content not in self.used_words
                    and content.isalpha()
                    and len(content) > len(group)
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
    ("Sagrada_Família", "Spain", ["spain"]),
    ("Leaning_Tower_of_Pisa", "Italy", ["italy"]),
    ("Stonehenge", "United Kingdom", ["uk", "united kingdom", "england", "britain"]),
    ("Mount_Rushmore", "United States", ["usa", "united states", "america"]),
    ("Niagara_Falls", "Canada", ["canada"]),
    ("Great_Sphinx_of_Giza", "Egypt", ["egypt"]),
    ("Hagia_Sophia", "Turkey", ["turkey"]),
    ("Blue_Mosque", "Turkey", ["turkey"]),
    ("Forbidden_City", "China", ["china"]),
    ("Potala_Palace", "China", ["china"]),
    ("Mount_Kilimanjaro", "Tanzania", ["tanzania"]),
    ("Victoria_Falls", "Zambia", ["zambia"]),
    ("Uluru", "Australia", ["australia"]),
    ("Great_Barrier_Reef", "Australia", ["australia"]),
    ("Moai", "Chile", ["chile"]),
    ("Salar_de_Uyuni", "Bolivia", ["bolivia"]),
    ("Iguazu_Falls", "Argentina", ["argentina"]),
    ("Perito_Moreno_Glacier", "Argentina", ["argentina"]),
    ("Amsterdam", "Netherlands", ["netherlands"]),
    ("Brandenburg_Gate", "Germany", ["germany"]),
    ("Charles_Bridge", "Czech Republic", ["czech republic", "czechia"]),
    ("Prague_Castle", "Czech Republic", ["czech republic", "czechia"]),
    ("Buckingham_Palace", "United Kingdom", ["uk", "united kingdom", "england", "britain"]),
    ("Edinburgh_Castle", "United Kingdom", ["uk", "united kingdom", "scotland"]),
    ("Cliffs_of_Moher", "Ireland", ["ireland"]),
    ("Trolltunga", "Norway", ["norway"]),
    ("Geirangerfjord", "Norway", ["norway"]),
    ("Little_Mermaid_(statue)", "Denmark", ["denmark"]),
    ("Matterhorn", "Switzerland", ["switzerland"]),
    ("Schönbrunn_Palace", "Austria", ["austria"]),
    ("Wat_Arun", "Thailand", ["thailand"]),
    ("Grand_Palace", "Thailand", ["thailand"]),
    ("Ha_Long_Bay", "Vietnam", ["vietnam"]),
    ("Borobudur", "Indonesia", ["indonesia"]),
    ("Petronas_Towers", "Malaysia", ["malaysia"]),
    ("Marina_Bay_Sands", "Singapore", ["singapore"]),
    ("Gyeongbokgung", "South Korea", ["south korea", "korea"]),
    ("Pyramids_of_Giza", "Egypt", ["egypt"]),
    ("Jerusalem", "Israel", ["israel"]),
    ("Dome_of_the_Rock", "Israel", ["israel"]),
    ("Alhambra", "Spain", ["spain"]),
    ("Park_Güell", "Spain", ["spain"]),
    ("Christ_of_the_Abyss", "Italy", ["italy"]),
    ("Venice", "Italy", ["italy"]),
    ("Mount_Vesuvius", "Italy", ["italy"]),
    ("Notre-Dame_de_Paris", "France", ["france"]),
    ("Mont_Saint-Michel", "France", ["france"]),
    ("Palace_of_Versailles", "France", ["france"]),
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



# Representative country points used for the distance-based scoring system.
# These are intentionally approximate geographic reference points rather than
# claims about Fizbo's private scoring implementation.
COUNTRY_COORDS = {
    "france": (46.2276, 2.2137),
    "china": (35.8617, 104.1954),
    "brazil": (-14.2350, -51.9253),
    "india": (20.5937, 78.9629),
    "australia": (-25.2744, 133.7751),
    "united kingdom": (55.3781, -3.4360),
    "united states": (37.0902, -95.7129),
    "italy": (41.8719, 12.5674),
    "peru": (-9.1900, -75.0152),
    "united arab emirates": (23.4241, 53.8478),
    "jordan": (30.5852, 36.2384),
    "mexico": (23.6345, -102.5528),
    "japan": (36.2048, 138.2529),
    "germany": (51.1657, 10.4515),
    "greece": (39.0742, 21.8243),
    "south africa": (-30.5595, 22.9375),
    "canada": (56.1304, -106.3468),
    "cambodia": (12.5657, 104.9910),
    "russia": (61.5240, 105.3188),
    "spain": (40.4637, -3.7492),
    "netherlands": (52.1326, 5.2913),
    "egypt": (26.8206, 30.8025),
    "turkey": (38.9637, 35.2433),
    "thailand": (15.8700, 100.9925),
    "vietnam": (14.0583, 108.2772),
    "indonesia": (-0.7893, 113.9213),
    "south korea": (35.9078, 127.7669),
    "north korea": (40.3399, 127.5101),
    "argentina": (-38.4161, -63.6167),
    "chile": (-35.6751, -71.5430),
    "colombia": (4.5709, -74.2973),
    "nigeria": (9.0820, 8.6753),
    "kenya": (-0.0236, 37.9062),
    "morocco": (31.7917, -7.0926),
    "saudi arabia": (23.8859, 45.0792),
    "israel": (31.0461, 34.8516),
    "iran": (32.4279, 53.6880),
    "iraq": (33.2232, 43.6793),
    "pakistan": (30.3753, 69.3451),
    "bangladesh": (23.6850, 90.3563),
    "philippines": (12.8797, 121.7740),
    "malaysia": (4.2105, 101.9758),
    "singapore": (1.3521, 103.8198),
    "new zealand": (-40.9006, 174.8860),
    "ireland": (53.1424, -7.6921),
    "sweden": (60.1282, 18.6435),
    "norway": (60.4720, 8.4689),
    "denmark": (56.2639, 9.5018),
    "finland": (61.9241, 25.7482),
    "poland": (51.9194, 19.1451),
    "ukraine": (48.3794, 31.1656),
    "switzerland": (46.8182, 8.2275),
    "austria": (47.5162, 14.5501),
    "belgium": (50.5039, 4.4699),
    "czech republic": (49.8175, 15.4730),
    "hungary": (47.1625, 19.5033),
    "romania": (45.9432, 24.9668),
    "croatia": (45.1000, 15.2000),
    "iceland": (64.9631, -19.0208),
    "cuba": (21.5218, -77.7812),
    "jamaica": (18.1096, -77.2975),
    "ecuador": (-1.8312, -78.1834),
    "venezuela": (6.4238, -66.5897),
    "bolivia": (-16.2902, -63.5887),
    "uruguay": (-32.5228, -55.7658),
    "paraguay": (-23.4425, -58.4438),
    "panama": (8.5380, -80.7821),
    "costa rica": (9.7489, -83.7534),
    "guatemala": (15.7835, -90.2308),
    "ethiopia": (9.1450, 40.4897),
    "ghana": (7.9465, -1.0232),
    "algeria": (28.0339, 1.6596),
    "tunisia": (33.8869, 9.5375),
    "libya": (26.3351, 17.2283),
    "sri lanka": (7.8731, 80.7718),
    "nepal": (28.3949, 84.1240),
    "myanmar": (21.9162, 95.9560),
    "laos": (19.8563, 102.4955),
    "mongolia": (46.8625, 103.8467),
    "kazakhstan": (48.0196, 66.9237),
    "afghanistan": (33.9391, 67.7100),
    "syria": (34.8021, 38.9968),
    "lebanon": (33.8547, 35.8623),
    "qatar": (25.3548, 51.1839),
    "kuwait": (29.3117, 47.4818),
    "oman": (21.4735, 55.9754),
    "yemen": (15.5527, 48.5164),
    "georgia": (42.3154, 43.3569),
    "armenia": (40.0691, 45.0382),
    "azerbaijan": (40.1431, 47.5769),
    "belarus": (53.7098, 27.9534),
    "serbia": (44.0165, 21.0059),
    "slovakia": (48.6690, 19.6990),
    "slovenia": (46.1512, 14.9955),
    "bulgaria": (42.7339, 25.4858),
    "estonia": (58.5953, 25.0136),
    "latvia": (56.8796, 24.6032),
    "lithuania": (55.1694, 23.8813),
    "luxembourg": (49.8153, 6.1296),
    "malta": (35.9375, 14.3754),
    "cyprus": (35.1264, 33.4299),
    "monaco": (43.7384, 7.4246),
    "andorra": (42.5063, 1.5218),
    "liechtenstein": (47.1660, 9.5554),
    "vatican": (41.9029, 12.4534),
    "san marino": (43.9424, 12.4578),
    "tanzania": (-6.3690, 34.8888),
    "zambia": (-13.1339, 27.8493),
}

COUNTRY_CANONICAL_BY_CODE = {
    code: canonical
    for canonical, code in {
        "france": "FR", "china": "CN", "brazil": "BR", "india": "IN",
        "australia": "AU", "united kingdom": "GB", "united states": "US",
        "italy": "IT", "peru": "PE", "united arab emirates": "AE",
        "jordan": "JO", "mexico": "MX", "japan": "JP", "germany": "DE",
        "greece": "GR", "south africa": "ZA", "canada": "CA", "cambodia": "KH",
        "russia": "RU", "spain": "ES", "netherlands": "NL", "egypt": "EG",
        "turkey": "TR", "thailand": "TH", "vietnam": "VN", "indonesia": "ID",
        "south korea": "KR", "north korea": "KP", "argentina": "AR",
        "chile": "CL", "colombia": "CO", "nigeria": "NG", "kenya": "KE",
        "morocco": "MA", "saudi arabia": "SA", "israel": "IL", "iran": "IR",
        "iraq": "IQ", "pakistan": "PK", "bangladesh": "BD", "philippines": "PH",
        "malaysia": "MY", "singapore": "SG", "new zealand": "NZ",
        "ireland": "IE", "sweden": "SE", "norway": "NO", "denmark": "DK",
        "finland": "FI", "poland": "PL", "ukraine": "UA", "switzerland": "CH",
        "austria": "AT", "belgium": "BE", "czech republic": "CZ",
        "hungary": "HU", "romania": "RO", "croatia": "HR", "iceland": "IS",
        "cuba": "CU", "jamaica": "JM", "ecuador": "EC", "venezuela": "VE",
        "bolivia": "BO", "uruguay": "UY", "paraguay": "PY", "panama": "PA",
        "costa rica": "CR", "guatemala": "GT", "ethiopia": "ET", "ghana": "GH",
        "algeria": "DZ", "tunisia": "TN", "libya": "LY", "sri lanka": "LK",
        "nepal": "NP", "myanmar": "MM", "laos": "LA", "mongolia": "MN",
        "kazakhstan": "KZ", "afghanistan": "AF", "syria": "SY", "lebanon": "LB",
        "qatar": "QA", "kuwait": "KW", "oman": "OM", "yemen": "YE",
        "georgia": "GE", "armenia": "AM", "azerbaijan": "AZ", "belarus": "BY",
        "serbia": "RS", "slovakia": "SK", "slovenia": "SI", "bulgaria": "BG",
        "estonia": "EE", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
        "malta": "MT", "cyprus": "CY", "monaco": "MC", "andorra": "AD",
        "liechtenstein": "LI", "vatican": "VA", "san marino": "SM",
        "tanzania": "TZ", "zambia": "ZM",
    }.items()
}

# Add common aliases to the existing code table.
COUNTRY_CODES.update({
    "czechia": "CZ",
    "u.s.a.": "US",
    "u.s.": "US",
    "united states of america": "US",
    "uae": "AE",
    "south korea": "KR",
    "republic of korea": "KR",
    "russia": "RU",
    "viet nam": "VN",
    "the netherlands": "NL",
    "tanzania": "TZ",
    "zambia": "ZM",
})

# LANDMARKS remains the existing pool in the file. It contains real-world
# photographed locations and is used as the photo source.

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

    # ---------- Guess The Country ----------

    async def _fetch_wiki_image(self, title: str):
        """Fetch a real-world image from the Wikipedia article for a location."""
        url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title)
        )
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        "GuessCountry: Wikipedia returned %s for %s",
                        resp.status,
                        title,
                    )
                    return None

                data = await resp.json(content_type=None)

        except Exception as exc:
            log.warning("GuessCountry: image request failed for %s: %s", title, exc)
            return None

        image = data.get("originalimage") or data.get("thumbnail")
        if not image:
            return None

        return image.get("source")

    @staticmethod
    def _normalise_country(value: str) -> str:
        """Normalize country guesses so harmless punctuation/casing differences work."""
        value = value.casefold().strip()
        value = value.replace("&", "and")
        value = value.replace("’", "'")
        value = "".join(ch if ch.isalnum() or ch in " -'" else " " for ch in value)
        return " ".join(value.split())

    @staticmethod
    def _country_distance_km(country_a: str, country_b: str) -> float | None:
        """Great-circle distance between representative points for two countries."""
        a = COUNTRY_COORDS.get(country_a)
        b = COUNTRY_COORDS.get(country_b)

        if not a or not b:
            return None

        from math import asin, cos, radians, sin, sqrt

        lat1, lon1 = map(radians, a)
        lat2, lon2 = map(radians, b)

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        h = (
            sin(dlat / 2) ** 2
            + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        )

        return 6371.0088 * 2 * asin(sqrt(h))

    @staticmethod
    def _country_points(distance_km: float | None) -> int:
        """
        Recreation scoring curve.

        Exact country = 50 points.
        As distance increases, the score smoothly falls toward zero.
        This intentionally does not claim to reproduce a private Fizbo formula.
        """
        if distance_km is None:
            return 0

        if distance_km <= 75:
            return 50

        # Smooth geographic falloff. The 1500 km scale makes nearby countries
        # substantially more valuable than distant guesses.
        from math import exp

        score = round(50 * exp(-distance_km / 1500))
        return max(1, min(50, score))

    @staticmethod
    def _format_distance(distance_km: float | None) -> str:
        if distance_km is None:
            return "unknown distance"
        if distance_km < 1000:
            return f"{round(distance_km)} km"
        return f"{distance_km / 1000:.1f}k km"

    def _country_aliases_for(self, country: str) -> set[str]:
        canonical = self._normalise_country(country)
        return {
            alias
            for alias, code in COUNTRY_CODES.items()
            if COUNTRY_CANONICAL_BY_CODE.get(code) == canonical
        } | {canonical}

    async def _run_guess_country_round(
        self,
        ctx: commands.Context,
        round_number: int,
        total_rounds: int,
        location,
        scores: dict[int, int],
        names: dict[int, str],
        round_results: list[dict],
    ):
        title, country, aliases = location

        image_url = await self._fetch_wiki_image(title)
        if not image_url:
            return False

        canonical_country = self._normalise_country(country)
        valid_answers = {
            self._normalise_country(country),
            *[self._normalise_country(a) for a in aliases],
        }

        embed = discord.Embed(
            title=f"🌎 Guess The Country • Round {round_number}/{total_rounds}",
            description=(
                "📸 **Where was this photograph taken?**\n\n"
                "Send the **country name** in chat.\n"
                "The closer your country is geographically to the answer, "
                "the more points you earn."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url=image_url)
        embed.add_field(
            name="⏱️ Time",
            value="25 seconds",
            inline=True,
        )
        embed.add_field(
            name="🏆 Maximum",
            value="50 points",
            inline=True,
        )
        embed.add_field(
            name="🧠 Scoring",
            value="Closer country = more points",
            inline=True,
        )
        embed.set_footer(text="Type a country name • One scored guess per player")

        round_message = await ctx.send(embed=embed)

        # Each player can score only once in a round. We keep accepting messages
        # from other users until the timer expires.
        answered: set[int] = set()
        guesses: list[tuple[discord.Member, str, int, float | None, bool]] = []

        loop = asyncio.get_running_loop()
        end_time = loop.time() + 25

        def check(message: discord.Message):
            return (
                message.channel.id == ctx.channel.id
                and not message.author.bot
                and bool(message.content.strip())
            )

        while True:
            remaining = end_time - loop.time()
            if remaining <= 0:
                break

            try:
                message = await self.bot.wait_for(
                    "message",
                    check=check,
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                break

            user_id = message.author.id
            active_players = getattr(self, "_guess_country_players", {})
            if user_id not in active_players:
                continue
            if user_id in answered:
                continue

            guess = self._normalise_country(message.content)
            guessed_code = COUNTRY_CODES.get(guess)

            # Ignore ordinary conversation. Only recognized countries are scored.
            if guessed_code is None:
                continue

            guessed_country = COUNTRY_CANONICAL_BY_CODE.get(guessed_code)
            if not guessed_country:
                continue

            answered.add(user_id)

            exact = guess in valid_answers
            if exact:
                distance = 0.0
                points = 50
            else:
                distance = self._country_distance_km(
                    canonical_country,
                    guessed_country,
                )
                points = self._country_points(distance)

            scores[user_id] = scores.get(user_id, 0) + points
            names[user_id] = message.author.display_name

            guesses.append(
                (
                    message.author,
                    guessed_country,
                    points,
                    distance,
                    exact,
                )
            )

            # Keep the live embed compact so it doesn't exceed Discord limits.
            sorted_guesses = sorted(
                guesses,
                key=lambda item: item[2],
                reverse=True,
            )

            lines = []
            for player, guessed, pts, dist, is_exact in sorted_guesses[-10:]:
                if is_exact:
                    suffix = "🎯 **EXACT**"
                else:
                    suffix = f"📍 {self._format_distance(dist)} away"

                lines.append(
                    f"{player.mention} — {flag_emoji(COUNTRY_CODES[guessed])} "
                    f"**{guessed.title()}** — **+{pts}** • {suffix}"
                )

            embed.set_field_at(
                0,
                name="📨 Guesses received",
                value="\n".join(lines) or "*Waiting for guesses...*",
                inline=False,
            )
            embed.set_footer(
                text=f"{len(answered)} player(s) have submitted a guess • "
                     "One scored guess per player"
            )

            try:
                await round_message.edit(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

        # Reveal and round leaderboard.
        round_results.append({
            "round": round_number,
            "country": country,
            "guesses": guesses,
        })

        if guesses:
            result_lines = []
            for player, guessed, pts, dist, exact in sorted(
                guesses,
                key=lambda item: item[2],
                reverse=True,
            ):
                result_lines.append(
                    f"{player.mention} — {flag_emoji(COUNTRY_CODES[guessed])} "
                    f"{guessed.title()} → **+{pts}**"
                    + (" 🎯" if exact else f" ({self._format_distance(dist)})")
                )

            result_embed = discord.Embed(
                title=f"📍 Round {round_number} complete",
                description=(
                    f"The correct country was **{country}** "
                    f"{flag_emoji(COUNTRY_CODES.get(canonical_country, ''))}\n\n"
                    + "\n".join(result_lines[:15])
                ),
                color=discord.Color.green(),
            )
        else:
            result_embed = discord.Embed(
                title=f"📍 Round {round_number} complete",
                description=(
                    f"The correct country was **{country}** "
                    f"{flag_emoji(COUNTRY_CODES.get(canonical_country, ''))}\n\n"
                    "Nobody submitted a recognized country guess."
                ),
                color=discord.Color.orange(),
            )

        await ctx.send(embed=result_embed)

        # Current overall scoreboard.
        if scores and round_number < total_rounds:
            leaderboard = sorted(
                scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )

            board = []
            medals = ["🥇", "🥈", "🥉"]
            for index, (uid, points) in enumerate(leaderboard[:10]):
                medal = medals[index] if index < 3 else f"`{index + 1}.`"
                board.append(f"{medal} **{names.get(uid, str(uid))}** — {points} pts")

            await ctx.send(
                embed=discord.Embed(
                    title="📊 Overall scoreboard",
                    description="\n".join(board),
                    color=discord.Color.blurple(),
                )
            )

        return True

    @commands.hybrid_command(
        name="guesscountry",
        aliases=["geoguess", "geoguessr", "guessthecountry"],
        description="6-round multiplayer photo-based country guessing game.",
    )
    @app_commands.describe(
        lobby_seconds="How long players have to join the game (10–90 seconds).",
    )
    async def geoguess(
        self,
        ctx: commands.Context,
        lobby_seconds: commands.Range[int, 10, 90] = 30,
    ):
        """
        Multiplayer Guess The Country.

        Six rounds. Players join the lobby, each round shows a real-world
        location photograph, and guesses are scored by geographic closeness.
        """
        channel_id = ctx.channel.id

        if channel_id in self.active_geo:
            return await ctx.send(
                "🌎 A **Guess The Country** game is already running in this channel."
            )

        self.active_geo.add(channel_id)

        try:
            # ---------------- Lobby ----------------
            players: dict[int, discord.Member] = {ctx.author.id: ctx.author}

            class LobbyView(discord.ui.View):
                def __init__(self, owner_id: int):
                    super().__init__(timeout=lobby_seconds)
                    self.owner_id = owner_id
                    self.started = asyncio.Event()
                    self.cancelled = False

                def refresh_button(self):
                    if self.join_button:
                        self.join_button.label = f"Join Game ({len(players)}/8)"

                @discord.ui.button(
                    label="Join Game (1/8)",
                    style=discord.ButtonStyle.success,
                    emoji="🎮",
                )
                async def join_button(
                    self,
                    interaction: discord.Interaction,
                    button: discord.ui.Button,
                ):
                    if interaction.user.bot:
                        return

                    if interaction.user.id not in players:
                        if len(players) >= 8:
                            return await interaction.response.send_message(
                                "The lobby is full (8 players).",
                                ephemeral=True,
                            )
                        players[interaction.user.id] = interaction.user

                    self.refresh_button()
                    await interaction.response.edit_message(
                        embed=make_lobby_embed(),
                        view=self,
                    )

                @discord.ui.button(
                    label="Start Now",
                    style=discord.ButtonStyle.primary,
                    emoji="▶️",
                )
                async def start_button(
                    self,
                    interaction: discord.Interaction,
                    button: discord.ui.Button,
                ):
                    if interaction.user.id != self.owner_id:
                        return await interaction.response.send_message(
                            "Only the person who started the game can start it early.",
                            ephemeral=True,
                        )

                    if len(players) < 2:
                        return await interaction.response.send_message(
                            "You need at least **2 players**.",
                            ephemeral=True,
                        )

                    self.started.set()
                    self.stop()
                    await interaction.response.defer()

                @discord.ui.button(
                    label="Cancel",
                    style=discord.ButtonStyle.danger,
                    emoji="✖️",
                )
                async def cancel_button(
                    self,
                    interaction: discord.Interaction,
                    button: discord.ui.Button,
                ):
                    if interaction.user.id != self.owner_id:
                        return await interaction.response.send_message(
                            "Only the game host can cancel this game.",
                            ephemeral=True,
                        )

                    self.cancelled = True
                    self.started.set()
                    self.stop()
                    await interaction.response.defer()

            def make_lobby_embed():
                mentions = "\n".join(
                    f"• {member.mention}" for member in players.values()
                )

                return discord.Embed(
                    title="🌎 GUESS THE COUNTRY",
                    description=(
                        "A new **6-round** photo geography game is starting!\n\n"
                        "**How it works**\n"
                        "📸 A real-world photograph appears each round.\n"
                        "🌍 Guess the country where it was taken.\n"
                        "📍 The geographically closer your guess is, "
                        "the more points you earn.\n"
                        "🏆 Highest total after round 6 wins.\n\n"
                        f"**Players ({len(players)}/8)**\n{mentions}"
                    ),
                    color=discord.Color.blurple(),
                ).set_footer(
                    text=f"Game starts in {lobby_seconds}s • Minimum 2 players"
                )

            lobby = LobbyView(ctx.author.id)
            lobby.refresh_button()

            lobby_message = await ctx.send(
                embed=make_lobby_embed(),
                view=lobby,
            )

            try:
                await asyncio.wait_for(
                    lobby.started.wait(),
                    timeout=lobby_seconds,
                )
            except asyncio.TimeoutError:
                pass

            lobby.stop()

            if lobby.cancelled:
                return await lobby_message.edit(
                    embed=discord.Embed(
                        title="🌎 Guess The Country",
                        description="Game cancelled by the host.",
                        color=discord.Color.red(),
                    ),
                    view=None,
                )

            if len(players) < 2:
                return await lobby_message.edit(
                    embed=discord.Embed(
                        title="🌎 Guess The Country",
                        description=(
                            "The game did not start because at least **2 players** "
                            "are required."
                        ),
                        color=discord.Color.orange(),
                    ),
                    view=None,
                )

            await lobby_message.edit(
                embed=discord.Embed(
                    title="🌎 Guess The Country",
                    description=(
                        f"**{len(players)} players** are locked in!\n"
                        "Get ready for **6 rounds** of real-world photo geography."
                    ),
                    color=discord.Color.green(),
                ),
                view=None,
            )

            # ---------------- Game ----------------
            scores: dict[int, int] = {uid: 0 for uid in players}
            self._guess_country_players = players
            names: dict[int, str] = {
                uid: member.display_name for uid, member in players.items()
            }
            round_results: list[dict] = []

            pool = LANDMARKS.copy()
            random.shuffle(pool)

            rounds_played = 0

            for location in pool:
                if rounds_played >= 6:
                    break

                success = await self._run_guess_country_round(
                    ctx,
                    rounds_played + 1,
                    6,
                    location,
                    scores,
                    names,
                    round_results,
                )

                if not success:
                    continue

                rounds_played += 1

            if rounds_played < 6:
                await ctx.send(
                    f"⚠️ Only **{rounds_played}/6** rounds could be loaded because "
                    "some location photos were unavailable."
                )

            if rounds_played == 0:
                return await ctx.send(
                    "❌ I couldn't load any location photos right now. "
                    "Please try again later."
                )

            # ---------------- Final results ----------------
            leaderboard = sorted(
                scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )

            # Only players who actually participated in the lobby are displayed.
            leaderboard = [
                (uid, points)
                for uid, points in leaderboard
                if uid in players
            ]

            medals = ["🥇", "🥈", "🥉"]
            lines = []

            for index, (uid, points) in enumerate(leaderboard):
                if index < 3:
                    prefix = medals[index]
                else:
                    prefix = f"`{index + 1}.`"

                lines.append(
                    f"{prefix} **{names.get(uid, players[uid].display_name)}** "
                    f"— **{points} pts**"
                )

            winner = leaderboard[0] if leaderboard else None

            final = discord.Embed(
                title="🏁 GUESS THE COUNTRY — FINAL RESULTS",
                description="\n".join(lines) or "No scores recorded.",
                color=discord.Color.gold(),
            )

            if winner:
                final.add_field(
                    name="🏆 Winner",
                    value=(
                        f"**{names.get(winner[0], players[winner[0]].display_name)}** "
                        f"with **{winner[1]} points**!"
                    ),
                    inline=False,
                )

            final.add_field(
                name="🎮 Game",
                value=f"{rounds_played}/6 rounds completed",
                inline=True,
            )
            final.add_field(
                name="👥 Players",
                value=str(len(players)),
                inline=True,
            )
            final.set_footer(
                text="Guess The Country • closer country guesses earn more points"
            )

            await ctx.send(embed=final)

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Guess The Country crashed in channel %s", channel_id)
            await ctx.send(
                "❌ Something went wrong while running Guess The Country. "
                "Check the bot logs for details."
            )
        finally:
            self._guess_country_players = {}
            self.active_geo.discard(channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
