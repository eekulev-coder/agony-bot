from __future__ import annotations

import io
import json
import os
import re
import aiohttp
import discord
import datetime as dt

from zoneinfo import ZoneInfo
from config import ADMIN_ROLE_ID, SPECIAL_ROLE_ID

MSK = ZoneInfo("Europe/Moscow")


def ensure_dirs():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists("data/ticket_state.json"):
        with open("data/ticket_state.json", "w", encoding="utf-8") as f:
            json.dump({"counter": 0}, f, ensure_ascii=False, indent=2)
    if not os.path.exists("data/battles_state.json"):
        with open("data/battles_state.json", "w", encoding="utf-8") as f:
            json.dump({"seen_battles": [], "attackers": {}}, f, ensure_ascii=False, indent=2)


def read_json(path: str, default):
    if not os.path.exists(path):
        write_json(path, default)
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_msk():
    return dt.datetime.now(MSK)


def fmt_dt(date: dt.datetime | None) -> str:
    if date is None:
        return "Неизвестно"
    if date.tzinfo is None:
        date = date.replace(tzinfo=dt.timezone.utc)
    return date.astimezone(MSK).strftime("%d.%m.%Y %H:%M:%S MSK")


def sanitize_channel_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:40] or "user"


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or any(r.id == ADMIN_ROLE_ID for r in member.roles)


def is_special(member: discord.Member) -> bool:
    return any(r.id == SPECIAL_ROLE_ID for r in member.roles)


def extract_ticket_owner_id(topic: str | None) -> int | None:
    if not topic:
        return None
    match = re.search(r"ticket_owner:(\d+)", topic)
    return int(match.group(1)) if match else None


def escape_text(text: str | None) -> str:
    if not text:
        return "—"
    return discord.utils.escape_mentions(text)


async def url_to_discord_file(
    session: aiohttp.ClientSession,
    url: str,
    filename: str,
    prefix: str = ""
) -> discord.File | None:
    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
        fp = io.BytesIO(data)
        safe_name = f"{prefix}{filename}" if prefix else filename
        return discord.File(fp=fp, filename=safe_name[:240])
    except Exception:
        return None


def split_chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]