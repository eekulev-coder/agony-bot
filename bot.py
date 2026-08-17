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
    """Загружает ID уже обработанных сообщений"""
    if not os.path.exists(PROCESSED_FILE):
        return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Оставляем только за последние 48 часов
            cutoff = (datetime.now() - timedelta(hours=48)).timestamp()
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

    # Чистим старые (>48ч)
    cutoff = (datetime.now() - timedelta(hours=48)).timestamp()
    data = {k: v for k, v in data.items() if v > cutoff}

    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


processed_ids = load_processed()


# ============ ПАРСЕР ============
def is_war_message(text: str) -> bool:
    """Проверяет, что это уведомление о забиве войны"""
    return "Вам забили войну" in text


def parse_war(text: str) -> dict:
    """Парсит сообщение о забиве войны"""
    data = {"raw": text, "time": None, "format": None, "weapon": None, "extras": None}

    # Время (22:10)
    m = re.search(r"на\s+(\d{1,2}[:.]\d{2})", text)
    if m:
        data["time"] = m.group(1).replace(".", ":")

    # Формат 6х6 / 5x5
    m = re.search(r"(\d+\s*[хx]\s*\d+)", text, re.IGNORECASE)
    if m:
        data["format"] = m.group(1).replace(" ", "")

    # Оружие
    m = re.search(r"на\s+([\d.]+\s*mm)", text, re.IGNORECASE)
    if m:
        data["weapon"] = m.group(1).replace(" ", "")

    # Дополнительно (алкоголь, косяки и т.д.)
    m = re.search(r"mm,?\s*(.+?)\.?$", text, re.IGNORECASE)
    if m:
        extras = re.sub(r"\s*разрешен[оы]\.?$", "", m.group(1).strip())
        data["extras"] = extras.strip(" .,")

    return data


# ============ DISCORD ============
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


class WarView(discord.ui.View):
    """Кнопки Буду / Не смогу"""

    def __init__(self):
        super().__init__(timeout=None)
        self.going = set()
        self.skip = set()

    def _update_labels(self):
        for child in self.children:
            if child.custom_id == "war_go":
                child.label = f"✅ Буду ({len(self.going)})"
            elif child.custom_id == "war_no":
                child.label = f"❌ Не смогу ({len(self.skip)})"

    def _rebuild_embed(self, orig: discord.Embed) -> discord.Embed:
        guild = bot.get_guild(config.DISCORD_GUILD_ID)
        going_names, skip_names = [], []

        for uid in self.going:
            m = guild.get_member(uid) if guild else None
            if m:
                going_names.append(m.display_name)

        for uid in self.skip:
            m = guild.get_member(uid) if guild else None
            if m:
                skip_names.append(m.display_name)

        new_embed = discord.Embed.from_dict(orig.to_dict())
        keep = [f for f in new_embed.fields if f.name not in ("✅ Идут", "❌ Не смогут")]
        new_embed.clear_fields()
        for f in keep:
            new_embed.add_field(name=f.name, value=f.value, inline=f.inline)

        if going_names:
            new_embed.add_field(
                name=f"✅ Идут ({len(going_names)})",
                value="\n".join(f"• {n}" for n in going_names),
                inline=True
            )
        if skip_names:
            new_embed.add_field(
                name=f"❌ Не смогут ({len(skip_names)})",
                value="\n".join(f"• {n}" for n in skip_names),
                inline=True
            )
        return new_embed

    @discord.ui.button(label="✅ Буду (0)", style=discord.ButtonStyle.success, custom_id="war_go")
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.going.add(interaction.user.id)
        self.skip.discard(interaction.user.id)
        self._update_labels()
        embed = self._rebuild_embed(interaction.message.embeds[0])
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="❌ Не смогу (0)", style=discord.ButtonStyle.danger, custom_id="war_no")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.skip.add(interaction.user.id)
        self.going.discard(interaction.user.id)
        self._update_labels()
        embed = self._rebuild_embed(interaction.message.embeds[0])
        await interaction.response.edit_message(embed=embed, view=self)


async def send_alert(parsed: dict):
    """Отправка оповещения в Discord"""
    channel = bot.get_channel(config.DISCORD_ALERT_CHANNEL_ID)
    if not channel:
        log.error(f"Канал {config.DISCORD_ALERT_CHANNEL_ID} не найден!")
        return

    embed = discord.Embed(
        title="⚔️ ЗАБИЛИ ВОЙНУ!",
        description=f"**{config.FAMILY_NAME}** — приготовиться к бою!",
        color=0xFF0000,
        timestamp=datetime.now()
    )

    if parsed.get("time"):
        embed.add_field(name="🕐 Время", value=f"`{parsed['time']}`", inline=True)
    if parsed.get("format"):
        embed.add_field(name="👥 Формат", value=f"`{parsed['format']}`", inline=True)
    if parsed.get("weapon"):
        embed.add_field(name="🔫 Оружие", value=f"`{parsed['weapon']}`", inline=True)
    if parsed.get("extras"):
        embed.add_field(name="💊 Дополнительно", value=f"`{parsed['extras']}`", inline=False)

    embed.add_field(
        name="📩 Оригинал",
        value=f"```{parsed['raw'][:900]}```",
        inline=False
    )
    embed.set_footer(text="Отметьтесь кнопками ниже 👇")

    ping = f"<@&{config.PING_ROLE_ID}>" if config.PING_ROLE_ID else "@everyone"
    view = WarView()

    try:
        await channel.send(
            content=f"{ping} ⚔️ **ЗАБИВ!**",
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True)
        )
        log.info(f"✅ Отправлено оповещение о забиве ({parsed.get('time')})")
    except Exception as e:
        log.error(f"Ошибка отправки в Discord: {e}")


async def handle_message(msg_id: int, text: str):
    """Общая обработка сообщения (для новых и из истории)"""
    if msg_id in processed_ids:
        log.info(f"⏭️ Сообщение {msg_id} уже обработано")
        return

    if not is_war_message(text):
        return

    parsed = parse_war(text)
    log.info(f"🎯 Забив: {parsed}")

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

        await handle_message(event.message.id, text)

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
            log.info(f"📜 Найден забив в истории (msg_id={message.id})")
            await handle_message(message.id, text)
            found += 1

    log.info(f"✅ История загружена: просмотрено {checked}, найдено забивов {found}")


# ============ DISCORD EVENTS ============
@bot.event
async def on_ready():
    log.info(f"🤖 Discord бот запущен как {bot.user}")
    bot.add_view(WarView())
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"забивы {config.FAMILY_NAME}"
        )
    )


@bot.command(name="test_war")
@commands.has_permissions(administrator=True)
async def test_war(ctx):
    """Тест: имитирует уведомление о забиве"""
    test = "Вам забили войну на 22:10, 6х6 на 11.43mm, алкоголь/анальгетики, косяки/SPANK разрешены."
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
