from __future__ import annotations

import asyncio
import os
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

SERVER_NAMES = {
    "01": "Downtown",
    "02": "Strawberry",
    "03": "Vinewood",
    "04": "Blackberry",
    "05": "Insquad",
    "06": "Sunrise",
    "07": "Rainbow",
    "08": "Richman",
    "09": "Eclipse",
    "10": "Lamesa",
    "11": "Burton",
    "12": "Rockford",
    "13": "Alta",
}

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


def sname(sid: str) -> str:
    s = str(sid).zfill(2)
    return SERVER_NAMES.get(s, f"Server #{s}")


def fmt_time(ms: int) -> str:
    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def fmt_datetime(ms: int) -> str:
    if not ms:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M • %d.%m.%Y")


def split_players(total: int) -> tuple[int, int]:
    if not total or total < 2:
        return (0, 0)
    half = (total + 1) // 2
    return (half, half)


def has_stats_role(member: discord.Member) -> bool:
    if is_admin(member):
        return True
    return any(r.id == STATS_ROLE_ID for r in member.roles)


class BattleMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.first_run = True
        self.check_battles.start()

    def cog_unload(self):
        self.check_battles.cancel()

    def get_targets((self) -> list[dict]:
        """Возвращает список отслеживаемых фракций и серверов"""
        targets = []
        
        # Инвайт 1 (основной)
        f1 = os.getenv("OUR_FACTION", OUR_FACTION)
        s1 = str(os.getenv("OUR_SERVER_SID", OUR_SERVER_SID)).zfill(2)
        if f1:
            targets.append({"faction": f1, "sid": s1})

        # Инвайт 2
        f2 = os.getenv("OUR_FACTION_2", "Армянская Мафия")
        s2 = str(os.getenv("OUR_SERVER_SID_2", "06")).zfill(2)
        if f2 and f2.upper() != "NONE":
            targets.append({"faction": f2, "sid": s2})

        return targets

    def load_state(self):
        return read_json(STATE_PATH, {"seen_battles": [], "attackers": {}})

    def save_state(self, state):
        if len(state.get("seen_battles", [])) > MAX_SEEN_STORED:
            state["seen_battles"] = state["seen_battles"][-MAX_SEEN_STORED:]
        write_json(STATE_PATH, state)

    def _headers(self, sid: str):
        return {
            "Accept": "text/x-component",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": NEXT_ACTION,
            "Next-Router-State-Tree": NEXT_ROUTER_STATE_TREE,
            "Origin": "https://gta5rp-bot.com",
            "Referer": f"https://gta5rp-bot.com/battles?sid={sid}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
        }

    async def fetch_page(self, session: aiohttp.ClientSession, sid: str, cursor: str = "") -> str:
        payload = json.dumps([sid, "", "", cursor])
        async with session.post(
            f"{BASE_URL}?sid={sid}",
            headers=self._headers(sid),
            data=payload
        ) as resp:
            if resp.status != 200:
                return ""
            return await resp.text()

    async def fetch_latest_battles(self, sid: str) -> list[dict]:
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                text = await self.fetch_page(session, sid)
        except Exception as e:
            print(f"[BATTLE_MONITOR] fetch error for SID {sid}: {e}")
            return []
        return self.parse_battles(text)

    async def fetch_all_battles_for_sid(self, sid: str, max_pages: int = 50) -> list[dict]:
        all_battles: list[dict] = []
        cursor = ""
        seen_keys: set[str] = set()
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _ in range(max_pages):
                try:
                    text = await self.fetch_page(session, sid, cursor)
                except Exception as e:
                    print(f"[BATTLE_MONITOR] page fetch error for SID {sid}: {e}")
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

    def is_our_battle(self, b: dict, our_faction: str, our_sid: str) -> bool:
        return (
            b["sid"] == our_sid
            and (b["attack"] == our_faction or b["defense"] == our_faction)
        )

    def btype(self, b: dict) -> str:
        return "Бизвар" if b["isBizwar"] else "Капт"

    # ========= HISTORY =========
    def build_history_embed(self, b: dict, our_faction: str) -> discord.Embed:
        win = b["winner"] == our_faction
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
        srv = sname(b["sid"])

        description = (
            f"**{result} • {bt}**  •  `{srv}`\n\n"
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
    def build_alert_embed(self, b: dict, our_faction: str) -> tuple[discord.Embed, bool] | None:
        role = "attack" if b["attack"] == our_faction else "defense"
        bt = self.btype(b)

        atk_flag = flag(b["attack"])
        def_flag = flag(b["defense"])
        atk_short = short(b["attack"])
        def_short = short(b["defense"])
        atk_p, dfn_p = split_players(b["players"])
        srv = sname(b["sid"])

        # НАС АТАКУЮТ
        if role == "defense" and not b["winner"]:
            title = "🚨 НАМ ЗАБИЛИ ДЭФ"
            color = 0xFF3300
            ping = True
            desc = (
                f"{atk_flag} **{atk_short}**  ⚔️  {def_flag} **Мы ({short(our_faction)})**\n"
                f"🌐 Сервер: `{srv}` • 📍 `{b['place'] or '—'}` • {bt}\n"
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
                f"{atk_flag} **Мы ({short(our_faction)})**  ⚔️  {def_flag} **{def_short}**\n"
                f"🌐 Сервер: `{srv}` • 📍 `{b['place'] or '—'}` • {bt}\n"
                f"👥 `{atk_p} vs {dfn_p}`\n"
                f"🕐 Начало: `{fmt_time(b['createdAt'])}`"
            )
            embed = discord.Embed(title=title, description=desc, color=color)
            return embed, ping

        return None

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def check_battles(self):
        state = self.load_state()
        seen = set(state.get("seen_battles", []))
        attackers = state.get("attackers", {})

        targets = self.get_targets()
        our_new = []

        for t in targets:
            fac = t["faction"]
            sid = t["sid"]
            try:
                battles = await self.fetch_latest_battles(sid)
                for b in battles:
                    if self.is_our_battle(b, fac, sid) and b["eventId"] not in seen:
                        b["_our_faction"] = fac
                        our_new.append(b)
            except Exception as e:
                print(f"[BATTLE_MONITOR] check error for {fac} ({sid}): {e}")

        if self.first_run:
            for t in targets:
                try:
                    battles = await self.fetch_latest_battles(t["sid"])
                    for b in battles:
                        seen.add(b["eventId"])
                except Exception:
                    pass
            state["seen_battles"] = list(seen)
            self.save_state(state)
            self.first_run = False
            print(f"[BATTLE_MONITOR] initial sync complete for {len(targets)} targets")
            return

        if not our_new:
            return

        our_new.sort(key=lambda b: b["createdAt"])

        alert_ch = self.bot.get_channel(ALERT_CHANNEL_ID)
        history_ch = self.bot.get_channel(HISTORY_CHANNEL_ID)

        for battle in our_new:
            ofac = battle["_our_faction"]
            if alert_ch:
                alert_result = self.build_alert_embed(battle, ofac)
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
                        embed=self.build_history_embed(battle, ofac),
                        allowed_mentions=discord.AllowedMentions.none()
                    )
                except Exception as e:
                    print(f"[BATTLE_MONITOR] history error: {e}")

            role = "attack" if battle["attack"] == ofac else "defense"
            if role == "defense" and battle["attack"]:
                atk_key = f"{battle['attack']} ({sname(battle['sid'])})"
                attackers[atk_key] = attackers.get(atk_key, 0) + 1

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

    @app_commands.command(name="whoattacks", description="Топ атакующих на наши фракции")
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
        for i, (name_key, count) in enumerate(sorted_atk[:15]):
            medal = medals[i] if i < 3 else f"`{i + 1}.`"
            # name_key looks like "Русская Мафия (Richman)"
            lines.append(f"{medal} **{name_key}** — `{count}`")

        embed = discord.Embed(
            title="⚔️ Топ атакующих на наши фракции",
            description="\n".join(lines),
            color=EMBED_COLOR
        )
        embed.set_footer(text=f"Всего атак: {sum(attackers.values())}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="winrate", description="Винрейт наших фракций")
    async def winrate(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not has_stats_role(interaction.user):
            return await interaction.response.send_message(
                "❌ У тебя нет прав использовать эту команду.",
                ephemeral=True
            )

        await interaction.response.defer(thinking=True)

        history_ch = self.bot.get_channel(HISTORY_CHANNEL_ID)
        if not history_ch:
            return await interaction.followup.send("Канал истории не найден.")

        targets = self.get_targets()
        stats = {t["faction"]: {"wins": 0, "losses": 0, "sid": t["sid"]} for t in targets}

        total_wins = 0
        total_losses = 0

        try:
            async for msg in history_ch.history(limit=None):
                if msg.author.id != self.bot.user.id or not msg.embeds:
                    continue

                for embed in msg.embeds:
                    desc = embed.description or ""
                    is_win = "🏆 WIN" in desc
                    is_loss = "💀 LOSS" in desc

                    if not (is_win or is_loss):
                        continue

                    for t in targets:
                        fac = t["faction"]
                        fac_short = short(fac)
                        if f"**{fac_short}**" in desc or f"**{fac}**" in desc:
                            if is_win:
                                stats[fac]["wins"] += 1
                                total_wins += 1
                            elif is_loss:
                                stats[fac]["losses"] += 1
                                total_losses += 1
                            break
        except Exception as e:
            return await interaction.followup.send(f"Ошибка чтения истории: `{e}`")

        grand_total = total_wins + total_losses

        if grand_total == 0:
            return await interaction.followup.send("В канале истории нет данных для подсчёта.")

        lines = []
        for fac, data in stats.items():
            w = data["wins"]
            l = data["losses"]
            tot = w + l
            srv = sname(data["sid"])
            if tot > 0:
                p = round(w / tot * 100)
                lines.append(f"{flag(fac)} **{short(fac)}** (`{srv}`): `{w}W / {l}L` (`{p}%`) ")

        overall_pct = round(total_wins / grand_total * 100)
        lines.append(f"\n📊 **Общий винрейт**: `{total_wins}W / {total_losses}L` (`{overall_pct}%`) ")

        color = 0x1F8B4C if overall_pct >= 60 else (0xF39C12 if overall_pct >= 45 else 0xE74C3C)

        embed = discord.Embed(
            title="📊 Winrate наших фракций",
            description="\n".join(lines),
            color=color
        )
        embed.set_footer(text=f"Посчитано по {grand_total} битвам в истории")

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
            f"⏳ Загружаю битвы всех фракций за последние {days} дн...",
            ephemeral=True
        )

        targets = self.get_targets()
        our_battles = []
        cutoff_ms = (dt.datetime.now() - dt.timedelta(days=days)).timestamp() * 1000

        for t in targets:
            fac = t["faction"]
            sid = t["sid"]
            try:
                battles = await self.fetch_all_battles_for_sid(sid, max_pages=50)
                for b in battles:
                    if self.is_our_battle(b, fac, sid) and b["createdAt"] >= cutoff_ms:
                        b["_our_faction"] = fac
                        our_battles.append(b)
            except Exception as e:
                print(f"[BACKFILL] error fetching for {fac} ({sid}): {e}")

        our_battles.sort(key=lambda b: b["createdAt"])

        if not our_battles:
            return await interaction.followup.send(
                f"За последние {days} дн битв не найдено.",
                ephemeral=True
            )

        state = self.load_state()
        seen = set(state.get("seen_battles", []))
        for b in our_battles:
            seen.add(b["eventId"])
        state["seen_battles"] = list(seen)
        self.save_state(state)

        try:
            sep = discord.Embed(
                title=f"📥 Backfill: {days} дн",
                description=f"Загружаем **{len(our_battles)}** битв...",
                color=EMBED_COLOR
            )
            await history_ch.send(embed=sep, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

        sent = 0
        failed = 0
        for i, battle in enumerate(our_battles, start=1):
            try:
                ofac = battle["_our_faction"]
                await history_ch.send(
                    embed=self.build_history_embed(battle, ofac),
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
            f"✅ Готово: {sent} из {len(our_battles)} в {history_ch.mention}",
            ephemeral=True
        )

    @app_commands.command(name="testalert", description="Тестовое уведомление (админ)")
    @app_commands.describe(scenario="Сценарий")
    @app_commands.choices(scenario=[
        app_commands.Choice(name="🚨 Атака на нас (Richman)", value="attack_on_us"),
        app_commands.Choice(name="⚔️ Мы забили атаку (Sunrise)", value="we_attack_sunrise"),
    ])
    async def testalert(
        self,
        interaction: discord.Interaction,
        scenario: app_commands.Choice[str]
    ):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            return await interaction.response.send_message("Нет прав.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        targets = self.get_targets()
        f1 = targets[0]["faction"] if len(targets) > 0 else "Bloods"
        s1 = targets[0]["sid"] if len(targets) > 0 else "08"

        f2 = targets[1]["faction"] if len(targets) > 1 else "Армянская Мафия"
        s2 = targets[1]["sid"] if len(targets) > 1 else "06"

        now_ms = int(dt.datetime.now().timestamp() * 1000)

        scenarios = {
            "attack_on_us": {
                "sid": s1, "ofac": f1,
                "isBizwar": True, "place": "Ammunation #1",
                "attack": "Русская Мафия", "defense": f1,
                "winner": "", "players": 13
            },
            "we_attack_sunrise": {
                "sid": s2, "ofac": f2,
                "isBizwar": True, "place": "Barbershop #2",
                "attack": f2, "defense": "Японская Мафия",
                "winner": "", "players": 11
            },
        }

        base = scenarios[scenario.value]
        battle = {
            "eventId": "test",
            "sid": base["sid"],
            "createdAt": now_ms,
            "isBizwar": base["isBizwar"],
            "place": base["place"],
            "attack": base["attack"],
            "defense": base["defense"],
            "winner": base["winner"],
            "players": base["players"]
        }

        alert_ch = self.bot.get_channel(ALERT_CHANNEL_ID)

        if alert_ch:
            alert_result = self.build_alert_embed(battle, base["ofac"])
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
