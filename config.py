import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_PHONE = os.getenv("TG_PHONE")
TG_BOT_USERNAME = os.getenv("TG_BOT_USERNAME", "gta5rp_helperbot")

# Discord (используем твои существующие имена из .env)
DISCORD_TOKEN = os.getenv("BOT_TOKEN")
DISCORD_ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID"))
DISCORD_GUILD_ID = int(os.getenv("GUILD_ID"))
PING_ROLE_ID = int(os.getenv("PING_ROLE_ID", "0"))

# Оформление
FAMILY_NAME = os.getenv("OUR_FACTION", "Agony")

# История
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "50"))