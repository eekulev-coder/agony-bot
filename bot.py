import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from telethon import TelegramClient, events

import config

# ============ ЛОГИРОВАНИЕ ============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("agony-bot")

# ============ ФАЙЛ ОБРАБОТАННЫХ СООБЩЕНИЙ ============
PROCESSED_FILE = "processed.json"


def load_processed() -> set:
    """Загружает ID уже обработанных сообщений (за последние 7 дней)"""
    if not os.path.exists(PROCESSED_FILE):
        return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            cutoff = (datetime.now() - timedelta(days=7)).timestamp()
            return {int(k) for k, v in data.items() if v > cutoff}
    except Exception as e:
        log.warning(f"Не удалось загрузить {PROCESSED_FILE}: {e}")
        return set()


def save_processed(msg_id: int):
    """Добавляет ID в файл обработанных"""
    data = {}
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data[str(msg_id)] = datetime.now().timestamp()

    # Чистим старые (>7 дней)
    cutoff = (datetime.now() - timedelta(days=7)).timestamp()
    data = {k: v for k, v in data.items() if v > cutoff}

    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


processed_ids = load_processed()


# ============ ПАРСЕР ============
def is_war_message(text: str) -> bool:
    """Только настоящие забивы. Игнорируем вагонетки/флаги/дропы."""
    if not text:
        return False
    
    text_lower = text.lower()
    
    if "вам забили войну" not in text_lower:
        return False
    
    # Стоп-слова — если есть в сообщении, игнорируем
    stop_words = ["вагонетк", "флаг", "дроп", "точк", "склад", "поставк"]
    for stop in stop_words:
        if stop in text_lower:
            return False
    
    return True


def parse_war(text: str) -> dict:
    """Парсит сообщение о забиве войны"""
    data = {"raw": text, "time": None, "format": None, "weapon": None, "extras": None}

    m = re.search(r"на\s+(\d{1,2}[:.]\d{2})", text)
    if m:
        data["time"] = m.group(1).replace(".", ":")

    m = re.search(r"(\d+\s*[хx]\s*\d+)", text, re.IGNORECASE)
    if m:
        data["format"] = m.group(1).replace(" ", "")

    m = re.search(r"на\s+([\d.]+\s*mm)", text, re.IGNORECASE)
    if m:
        data["weapon"] = m.group(1).replace(" ", "")

    m = re.search(r"mm,?\s*(.+?)\.?$", text, re.IGNORECASE)
    if m:
        extras = re.sub(r"\s*разрешен[оы]\.?$", "", m.group(1).strip())
        data["extras"] = extras.strip(" .,")

    return data


def is_war_expired(war_time_str: str, msg_date: datetime = None) -> bool:
    """Прошёл ли уже забив (с запасом 15 минут). Работает по МСК (UTC+3)."""
    if not war_time_str:
        return False
    
    try:
        hour, minute = map(int, war_time_str.replace(".", ":").split(":"))
    except (ValueError, AttributeError):
        return False
    
    # МСК = UTC+3
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    
    if msg_date:
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        base_date = msg_date.astimezone(msk_tz)
    else:
        base_date = now_msk
    
    war_time = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return war_time < (now_msk - timedelta(minutes=15))


# ============ DISCORD ============
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


async def send_alert(parsed: dict):
    """Минималистичное уведомление о забиве"""
    channel = bot.get_channel(config.DISCORD_ALERT_CHANNEL_ID)
    if not channel:
        log.error(f"Канал {config.DISCORD_ALERT_CHANNEL_ID} не найден!")
        return

    embed = discord.Embed(
        title="⚔️ Забив",
        color=0xE74C3C,
        timestamp=datetime.now()
    )

    embed.add_field(name="Время", value=parsed.get("time") or "—", inline=True)
    embed.add_field(name="Формат", value=parsed.get("format") or "—", inline=True)
    embed.add_field(name="Оружие", value=parsed.get("weapon") or "—", inline=True)

    if parsed.get("extras"):
        embed.add_field(name="Доп.", value=parsed["extras"], inline=False)

    ping = f"<@&{config.PING_ROLE_ID}>" if config.PING_ROLE_ID else "@everyone"

    try:
        await channel.send(
            content=ping,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True)
        )
        log.info(f"✅ Отправлено оповещение о забиве ({parsed.get('time')})")
    except Exception as e:
        log.error(f"Ошибка отправки в Discord: {e}")


async def handle_message(msg_id: int, text: str, msg_date: datetime = None, from_history: bool = False):
    """Обработка сообщения (для новых и из истории)"""
    # Защита от дублей
    if msg_id in processed_ids:
        return

    # Фильтр вагонетки/флаги/дропы
    if not is_war_message(text):
        return

    parsed = parse_war(text)

    # 🕐 Не постим прошедшие забивы
    if is_war_expired(parsed.get("time"), msg_date):
        log.info(f"⏰ Забив на {parsed.get('time')} уже прошёл — пропуск (msg_id={msg_id})")
        # Всё равно сохраняем в processed, чтобы не проверять снова
        processed_ids.add(msg_id)
        save_processed(msg_id)
        return

    src = "истории" if from_history else "нового сообщения"
    log.info(f"🎯 Новый забив из {src}: {parsed.get('time')}")

    await send_alert(parsed)

    processed_ids.add(msg_id)
    save_processed(msg_id)


# ============ TELEGRAM ============
tg = TelegramClient("session_name", config.TG_API_ID, config.TG_API_HASH)


@tg.on(events.NewMessage())
async def on_new_message(event):
    """Слушает новые сообщения"""
    try:
        sender = await event.get_sender()
        username = getattr(sender, "username", None)

        if username != config.TG_BOT_USERNAME:
            return

        text = event.message.message or ""
        log.info(f"📨 Новое от @{username}: {text[:80]}...")

        await handle_message(
            msg_id=event.message.id,
            text=text,
            msg_date=event.message.date,
            from_history=False
        )

    except Exception as e:
        log.error(f"Ошибка обработки TG сообщения: {e}", exc_info=True)


async def load_history():
    """Загружает историю за последние 24 часа при старте"""
    log.info(f"📚 Загружаю историю от @{config.TG_BOT_USERNAME}...")

    try:
        entity = await tg.get_entity(config.TG_BOT_USERNAME)
    except Exception as e:
        log.error(f"Не удалось найти @{config.TG_BOT_USERNAME}: {e}")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    checked = 0
    found = 0

    async for message in tg.iter_messages(entity, limit=100):
        checked += 1

        if message.date < cutoff:
            break

        text = message.message or ""
        if is_war_message(text):
            await handle_message(
                msg_id=message.id,
                text=text,
                msg_date=message.date,
                from_history=True
            )
            found += 1

    log.info(f"✅ История загружена: просмотрено {checked}, найдено забивов {found}")


# ============ DISCORD EVENTS ============
@bot.event
async def on_ready():
    log.info(f"🤖 Discord бот запущен как {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="забивы"
        )
    )


@bot.command(name="test_war")
@commands.has_permissions(administrator=True)
async def test_war(ctx):
    """Тест уведомления о забиве"""
    test = "Вам забили войну на 23:59, 6х6 на 11.43mm, алкоголь/анальгетики, косяки/SPANK разрешены."
    await send_alert(parse_war(test))
    await ctx.send("✅ Тест отправлен!", delete_after=5)


@bot.command(name="reload_history")
@commands.has_permissions(administrator=True)
async def reload_history_cmd(ctx):
    """Перезагрузить историю с TG"""
    await ctx.send("📚 Загружаю историю...")
    await load_history()
    await ctx.send("✅ Готово!")


@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"🏓 Pong! {round(bot.latency * 1000)}ms")


# ============ ЗАПУСК ============
async def main():
    log.info("🚀 Запуск бота...")

    await tg.start(phone=config.TG_PHONE)
    log.info("✅ Telegram авторизован")

    async def run_discord():
        await bot.start(config.DISCORD_TOKEN)

    async def run_telegram():
        await bot.wait_until_ready()
        log.info("✅ Discord готов, загружаем историю...")
        await load_history()
        log.info(f"👂 Слушаю новые сообщения от @{config.TG_BOT_USERNAME}")
        await tg.run_until_disconnected()

    await asyncio.gather(run_discord(), run_telegram())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Остановлен")
