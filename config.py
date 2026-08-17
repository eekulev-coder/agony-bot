import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
SPECIAL_ROLE_ID = int(os.getenv("SPECIAL_ROLE_ID", "0"))

TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
TICKET_LOG_CHANNEL_ID = int(os.getenv("TICKET_LOG_CHANNEL_ID", "0"))
SERVER_LOG_CHANNEL_ID = int(os.getenv("SERVER_LOG_CHANNEL_ID", "0"))

ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0"))
HISTORY_CHANNEL_ID = int(os.getenv("HISTORY_CHANNEL_ID", "0"))
OUR_FACTION = os.getenv("OUR_FACTION", "Мексиканская Мафия")
OUR_SERVER_SID = os.getenv("OUR_SERVER_SID", "08")

EMBED_COLOR = 0x2B2D31
MAX_OPEN_TICKETS_PER_USER = 2
CLEAR_LIMIT = 100