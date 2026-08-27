from __future__ import annotations

import asyncio
import re
import json
import aiohttp
import discord
import datetime as dt
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    ALERT_CHANNEL_ID,
    HISTORY_CHANNEL_ID,
    OUR_FACTION,
    OUR_SERVER_SID,
    EMBED_COLOR,
)
from helpers import read_json, write_json, is_admin


BASE_URL = "https://gta5rp-bot.com/battles"

NEXT_ACTION = "78135e8e2d10edf69e428860511f3fd0b68cbdd168"

NEXT_ROUTER_STATE_TREE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%22(public)%22%2C%7B%22children"
    "%22%3A%5B%22battles%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C"
    "%7B%7D%2C%22refetch%22%2C%22refetch%22%5D%7D%2C%22refetch%22%2C"
    "%22refetch%22%5D%7D%2C%22refetch%22%2C%22refetch%22%5D%7D%5D"
)

STATE_PATH = "data/battles_state.json"
CHECK_INTERVAL_MINUTES = 3
MAX_SEEN_STORED = 500

# Роль, которая может пользоваться /whoattacks и /winrate
STATS_ROLE_ID = 1523021893844730011

# Сокращения фракций
FACTION_SHORT = {
    "Мексиканская Мафия": "MM",
    "Русская Мафия": "RM",
    "Итальянская Мафия": "LCN",
    "Армянская Мафия": "AM",
    "Японская Мафия": "YAK",
    "The Families": "FAM",
    "The Ballas Gang": "BAL",
    "East Side Ballas": "ESB",
    "Marabunta Grande": "MG",
    "Los Santos Vagos": "LSV",
    "Bloods Street Gang": "BSG",
    "Bloods": "BLD",
}

FACTION_FLAGS = {
    "Мексиканская Мафия": "🇲🇽",
    "Русская Мафия": "🇷🇺",
    "Итальянская Мафия": "🇮🇹",
    "Армянская Мафия": "🇦🇲",
    "Японская Мафия": "🇯🇵",
    "The Families": "🟢",
    "The Ballas Gang": "🟣",
    "East Side Ballas": "🟣",
    "Marabunta Grande": "🟡",
    "Los Santos Vagos": "🟠",
    "Bloods Street Gang": "🔴",
    "Bloods": "🔴",
}


def flag(name: str) -> str:
    return FACTION_FLAGS.get(name, "⚫")


def short(name: str) -> str:
    return FACTION_SHORT.get(name, name)


def fmt_time(ms: int) -> str:
    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def fmt_datetime(ms: int) -> str:
    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M • %d.%m.%Y")


def split_players(total: int) -> tuple[int, int]:
    """
    Разбивка игроков поровну. Если нечётное — обе стороны округляются вверх.
    Пример: 13 → 7 vs 7, 15 → 8 vs 8, 10 → 5 vs 5
    """
    if not total or total < 2:
        return (0, 0)
    half = (total + 1) // 2
    return (half, half)


def has_stats_role(member: discord.Member) -> bool:
    """Проверка что у пользователя есть роль для статистики или админка"""
    if is_admin(member):
        return True
    return any(r.id == STATS_ROLE_ID for r in member.roles)


class BattleMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.our_faction = OUR_FACTION
        self.our_sid = OUR_SERVER_SID
        self.first_run = True
        self.check_battles.start()

    def cog_unload(self):
        self.check_battles.cancel()

    def load_state(self):
        return read_json(STATE_PATH, {"seen_battles": [], "attackers": {}})

    def save_state(self, state):
        if len(state.get("seen_battles", [])) > MAX_SEEN_STORED:
            state["seen_battles"] = state["seen_battles"][-MAX_SEEN_STORED:]
        write_json(STATE_PATH, state)

    def _headers(self):
        return {
            "Accept": "text/x-component",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": NEXT_ACTION,
            "Next-Router-State-Tree": NEXT_ROUTER_STATE_TREE,
            "Origin": "https://gta5rp-bot.com",
            "Referer": f"https://gta5rp-bot.com/battles?sid={self.our_sid}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
        }

    async def fetch_page(self, session: aiohttp.ClientSession, cursor: str = "") -> str:
        payload = json.dumps([self.our_sid, "", "", cursor])
        async with session.post(
            f"{BASE_URL}?sid={self.our_sid}",
            headers=self._headers(),
            data=payload
        ) as resp:
            if resp.status != 200:
                return ""
            return await resp.text()

    async def fetch_latest_battles(self) -> list[dict]:
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                text = await self.fetch_page(session)
        except Exception as e:
            print(f"[BATTLE_MONITOR] fetch error: {e}")
            return []
        return self.parse_battles(text)

    async def fetch_all_battles(self, max_pages: int = 50) -> list[dict]:
        all_battles: list[dict] = []
        cursor = ""
        seen_keys: set[str] = set()
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _ in range(max_pages):
                try:
                    text = await self.fetch_page(session, cursor)
                except Exception as e:
                    print(f"[BATTLE_MONITOR] page fetch error: {e}")
                    break

                if not text:
                    break

                battles = self.parse_battles(text)
                new_count = 0
                for b in battles:
                    k = f"{b['eventId']}_{b['createdAt']}"
                    if k not in seen_keys:
                        seen_keys.add(k)
                        all_battles.append(b)
                        new_count += 1

                if new_count == 0:
                    break

                m = re.search(r'"nextCursor"\s*:\s*"([^"]+)"', text)
                if not m:
                    break
                new_cursor = m.group(1)
                if new_cursor == cursor:
                    break
                cursor = new_cursor

                await asyncio.sleep(0.2)

        return all_battles

    def parse_battles(self, text: str) -> list[dict]:
        battles = []
        pattern = re.compile(
            r'"eventInternalId"\s*:\s*"([^"]+)"'
            r'(?:.*?"sid"\s*:\s*"([^"]+)")?'
            r'(?:.*?"isBizwar"\s*:\s*(true|false))?'
            r'(?:.*?"place"\s*:\s*"([^"]*)")?'
            r'(?:.*?"attack"\s*:\s*"([^"]*)")?'
            r'(?:.*?"defense"\s*:\s*"([^"]*)")?'
            r'(?:.*?"winner"\s*:\s*"([^"]*)")?'
            r'(?:.*?"players"\s*:\s*(\d+))?'
            r'(?:.*?"attackKills"\s*:\s*(\d+))?'
            r'(?:.*?"defenseKills"\s*:\s*(\d+))?'
            r'(?:.*?"createdAt"\s*:\s*(\d+))?',
            re.DOTALL
        )

        for m in pattern.finditer(text):
            eid, sid, is_biz, place, atk, dfn, win, pl, ak, dk, cr = m.groups()
            battles.append({
                "eventId": eid,
                "sid": sid or "",
                "isBizwar": is_biz == "true",
                "place": (place or "").strip(),
                "attack": (atk or "").strip(),
                "defense": (dfn or "").strip(),
                "winner": (win or "").strip(),
                "players": int(pl) if pl else 0,
                "attackKills": int(ak) if ak else 0,
                "defenseKills": int(dk) if dk else 0,
                "createdAt": int(cr) if cr else 0,
            })

        return battles

    def is_our_battle(self, b: dict) -> bool:
        return (
            b["sid"] == self.our_sid
            and (b["attack"] == self.our_faction or b["defense"] == self.our_faction)
        )

    def our_role(self, b: dict) -> str:
        return "attack" if b["attack"] == self.our_faction else "defense"

    def is_win(self, b: dict) -> bool:
        return b["winner"] == self.our_faction

    def btype(self, b: dict) -> str:
        return "Бизвар" if b["isBizwar"] else "Капт"

    # ========= HISTORY =========
    def build_history_embed(self, b: dict) -> discord.Embed:
        win = self.is_win(b)
        bt = self.btype(b)

        if win:
            color = 0x1F8B4C
            result = "🏆 WIN"
        else:
            color = 0xE74C3C
            result = "💀 LOSS"

        atk_flag = flag(b["attack"])
        def_flag = flag(b["defense"])
        atk_short = short(b["attack"])
        def_short = short(b["defense"])

        atk_p, dfn_p = split_players(b["players"])
        players_line = f"⚔️  `{atk_p} vs {dfn_p}`" if atk_p > 0 else "⚔️"

        description = (
            f"**{result} • {bt}**\n\n"
            f"{atk_flag} **{atk_short}**\n"
            f"{players_line}\n"
            f"{def_flag} **{def_short}**\n\n"
            f"-# 📍 {b['place'] or '—'}\n"
            f"-# 🕐 {fmt_datetime(b['createdAt'])}"
        )

        embed = discord.Embed(
            description=description,
            color=color,
        )

        return embed

    # ========= ALERTS =========
    def build_alert_embed(self, b: dict) -> tuple[discord.Embed, bool] | None:
        role = self.our_role(b)
        bt = self.btype(b)

        atk_flag = flag(b["attack"])
        def_flag = flag(b["defense"])
        atk_short = short(b["attack"])
        def_short = short(b["defense"])
        atk_p, dfn_p = split_players(b["players"])

        # НАС АТАКУЮТ
        if role == "defense" and not b["winner"]:
            title = "🚨 НАМ ЗАБИЛИ ДЭФ"
            color = 0xFF3300
            ping = True
            desc = (
                f"{atk_flag} **{atk_short}**  ⚔️  {def_flag} **Мы**\n"
                f"📍 `{b['place'] or '—'}` • {bt}\n"
                f"👥 `{atk_p} vs {dfn_p}`\n"
                f"🕐 Начало: `{fmt_time(b['createdAt'])}`"
            )
            embed = discord.Embed(title=title, description=desc, color=color)
            return embed, ping

        # МЫ ЗАБИЛИ АТАКУ
        if role == "attack" and not b["winner"]:
            title = "⚔️ МЫ ЗАБИЛИ АТАКУ"
            color = 0x1F8B4C
            ping = True
            desc = (
                f"{atk_flag} **Мы**  ⚔️  {def_flag} **{def_short}**\n"
                f"📍 `{b['place'] or '—'}` • {bt}\n"
                f"👥 `{atk_p} vs {dfn_p}`\n"
                f"🕐 Начало: `{fmt_time(b['createdAt'])}`"
            )
            embed = discord.Embed(title=title, description=desc, color=color)
            return embed, ping

        return None

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_battles(self):
        try:
            battles = await self.fetch_latest_battles()
        except Exception as e:
            print(f"[BATTLE_MONITOR] check error: {e}")
            return

        if not battles:
            return

        state = self.load_state()
        seen = set(state.get("seen_battles", []))
        attackers = state.get("attackers", {})

        our_new = [b for b in battles if self.is_our_battle(b) and b["eventId"] not in seen]

        if self.first_run:
            for b in battles:
                seen.add(b["eventId"])
            state["seen_battles"] = list(seen)
            self.save_state(state)
            self.first_run = False
            print(f"[BATTLE_MONITOR] initial sync: {len(battles)} marked as seen")
            return

        if not our_new:
            return

        our_new.sort(key=lambda b: b["createdAt"])

        alert_ch = self.bot.get_channel(ALERT_CHANNEL_ID)
        history_ch = self.bot.get_channel(HISTORY_CHANNEL_ID)

        for battle in our_new:
            if alert_ch:
                alert_result = self.build_alert_embed(battle)
                if alert_result is not None:
                    embed, ping = alert_result
                    try:
                        content = "@everyone" if ping else None
                        await alert_ch.send(
                            content=content,
                            embed=embed,
                            allowed_mentions=discord.AllowedMentions(everyone=True)
                        )
                    except Exception as e:
                        print(f"[BATTLE_MONITOR] alert error: {e}")

            if history_ch:
                try:
                    await history_ch.send(
                        embed=self.build_history_embed(battle),
                        allowed_mentions=discord.AllowedMentions.none()
                    )
                except Exception as e:
                    print(f"[BATTLE_MONITOR] history error: {e}")

            if self.our_role(battle) == "defense" and battle["attack"]:
                attackers[battle["attack"]] = attackers.get(battle["attack"], 0) + 1

            seen.add(battle["eventId"])

        state["seen_battles"] = list(seen)
        state["attackers"] = attackers
        self.save_state(state)

        print(f"[BATTLE_MONITOR] processed {len(our_new)} new battles")

    @check_battles.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    # ================== COMMANDS ==================

    @app_commands.command(name="whoattacks", description="Топ атакующих на нашу мафию")
    async def whoattacks(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not has_stats_role(interaction.user):
            return await interaction.response.send_message(
                "❌ У тебя нет прав использовать эту команду.",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        state = self.load_state()
        attackers = state.get("attackers", {})

        if not attackers:
            return await interaction.followup.send(
                "Пока нет данных об атаках.",
                ephemeral=True
            )

        sorted_atk = sorted(attackers.items(), key=lambda x: x[1], reverse=True)

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (name, count) in enumerate(sorted_atk[:15]):
            medal = medals[i] if i < 3 else f"`{i + 1}.`"
            lines.append(f"{medal} {flag(name)} **{short(name)}** — `{count}`")

        embed = discord.Embed(
            title=f"⚔️ Топ атакующих на {short(self.our_faction)}",
            description="\n".join(lines),
            color=EMBED_COLOR
        )
        embed.set_footer(text=f"Всего атак: {sum(attackers.values())}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="winrate", description="Винрейт нашей мафии")
    async def winrate(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not has_stats_role(interaction.user):
            return await interaction.response.send_message(
                "❌ У тебя нет прав использовать эту команду.",
                ephemeral=True
            )

        # публичный ответ — видят все
        await interaction.response.defer(thinking=True)

        history_ch = self.bot.get_channel(HISTORY_CHANNEL_ID)
        if not history_ch:
            return await interaction.followup.send("Канал истории не найден.")

        wins = 0
        losses = 0
        our_short_name = short(self.our_faction)

        try:
            async for msg in history_ch.history(limit=None):
                if msg.author.id != self.bot.user.id:
                    continue
                if not msg.embeds:
                    continue

                for embed in msg.embeds:
                    desc = embed.description or ""
                    # Считаем только те бои, где участвует текущая фракция (например **BLD** или **BSG**)
                    if f"**{our_short_name}**" in desc or f"**{self.our_faction}**" in desc:
                        if "🏆 WIN" in desc:
                            wins += 1
                        elif "💀 LOSS" in desc:
                            losses += 1
        except Exception as e:
            return await interaction.followup.send(f"Ошибка чтения истории: `{e}`")

        total = wins + losses

        if total == 0:
            return await interaction.followup.send(f"В канале истории не найдено боев для фракции **{self.our_faction}**.")

        pct = round(wins / total * 100)

        if pct >= 60:
            color = 0x1F8B4C
        elif pct >= 45:
            color = 0xF39C12
        else:
            color = 0xE74C3C

        embed = discord.Embed(
            title=f"📊 Winrate • {short(self.our_faction)}",
            description=f"**{wins}W / {losses}L**  •  `{pct}%`",
            color=color
        )
        embed.set_footer(text=f"Посчитано по {total} битвам {short(self.our_faction)} в истории")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="backfill", description="Залить историю битв за N дней (админ)")
    @app_commands.describe(days="За сколько дней залить (1-30)")
    async def backfill(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 30] = 7
    ):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        history_ch = self.bot.get_channel(HISTORY_CHANNEL_ID)
        if not history_ch:
            return await interaction.followup.send("Канал истории не найден.", ephemeral=True)

        await interaction.followup.send(
            f"⏳ Загружаю битвы за последние {days} дн...",
            ephemeral=True
        )

        try:
            all_battles = await self.fetch_all_battles(max_pages=50)
        except Exception as e:
            return await interaction.followup.send(f"Ошибка загрузки: `{e}`", ephemeral=True)

        cutoff_ms = (dt.datetime.now() - dt.timedelta(days=days)).timestamp() * 1000

        our = [
            b for b in all_battles
            if self.is_our_battle(b) and b["createdAt"] >= cutoff_ms
        ]
        our.sort(key=lambda b: b["createdAt"])

        if not our:
            return await interaction.followup.send(
                f"За последние {days} дн битв не найдено.",
                ephemeral=True
            )

        state = self.load_state()
        seen = set(state.get("seen_battles", []))
        for b in our:
            seen.add(b["eventId"])
        state["seen_battles"] = list(seen)
        self.save_state(state)

        try:
            sep = discord.Embed(
                title=f"📥 Backfill: {days} дн",
                description=f"Загружаем **{len(our)}** битв...",
                color=EMBED_COLOR
            )
            await history_ch.send(embed=sep, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

        sent = 0
        failed = 0
        for i, battle in enumerate(our, start=1):
            try:
                await history_ch.send(
                    embed=self.build_history_embed(battle),
                    allowed_mentions=discord.AllowedMentions.none()
                )
                sent += 1
                await asyncio.sleep(0.7 if i % 5 else 2)
            except Exception as e:
                print(f"[BACKFILL] send error: {e}")
                failed += 1

        try:
            final = discord.Embed(
                title="✅ Backfill завершён",
                description=f"Загружено: **{sent}** / Ошибок: **{failed}**",
                color=0x1F8B4C
            )
            await history_ch.send(embed=final, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

        await interaction.followup.send(
            f"✅ Готово: {sent} из {len(our)} в {history_ch.mention}",
            ephemeral=True
        )

    @app_commands.command(name="testalert", description="Тестовое уведомление (админ)")
    @app_commands.describe(scenario="Сценарий")
    @app_commands.choices(scenario=[
        app_commands.Choice(name="🚨 Атака на нас", value="attack_on_us"),
        app_commands.Choice(name="⚔️ Мы забили атаку", value="we_attack"),
    ])
    async def testalert(
        self,
        interaction: discord.Interaction,
        scenario: app_commands.Choice[str]
    ):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        now_ms = int(dt.datetime.now().timestamp() * 1000)

        scenarios = {
            "attack_on_us": {
                "isBizwar": True, "place": "Ammunation #1",
                "attack": "Русская Мафия", "defense": self.our_faction,
                "winner": "", "players": 13, "attackKills": 0, "defenseKills": 0
            },
            "we_attack": {
                "isBizwar": True, "place": "Ammunation #3",
                "attack": self.our_faction, "defense": "Японская Мафия",
                "winner": "", "players": 11, "attackKills": 0, "defenseKills": 0
            },
        }

        base = scenarios[scenario.value]
        battle = {
            "eventId": "test",
            "sid": self.our_sid,
            "createdAt": now_ms,
            **base
        }

        alert_ch = self.bot.get_channel(ALERT_CHANNEL_ID)

        if alert_ch:
            alert_result = self.build_alert_embed(battle)
            if alert_result:
                embed, _ = alert_result
                await alert_ch.send(
                    content="🧪 ТЕСТ",
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none()
                )

        await interaction.followup.send(f"✅ Отправлено: **{scenario.name}**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BattleMonitor(bot))
