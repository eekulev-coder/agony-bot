import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_PHONE = os.getenv("TG_PHONE")
TG_BOT_USERNAME = os.getenv("TG_BOT_USERNAME", "gta5rp_helperbot")

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_ALERT_CHANNEL_ID = int(os.getenv("DISCORD_ALERT_CHANNEL_ID"))
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
PING_ROLE_ID = int(os.getenv("PING_ROLE_ID", "0"))

# Настройки
FAMILY_NAME = os.getenv("FAMILY_NAME", "Mexican Mafia")

# Ключевая фраза для фильтрации
TRIGGER_PHRASE = "Вам забили войну"

# История: сколько сообщений подгрузить при старте
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "50"))

# Автооповещения: за сколько минут напоминать
REMIND_BEFORE_MINUTES = [15, 5, 1]  # напомним за 15, 5 и 1 минуту

# Файл для хранения обработанных сообщений (чтобы не дублировать)
PROCESSED_FILE = "processed_messages.json"
