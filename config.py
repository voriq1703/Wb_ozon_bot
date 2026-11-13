import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Webhook URL для отправки заявки Менеджеру карточек в Grok Bot
GROK_WEBHOOK_URL = os.getenv("GROK_WEBHOOK_URL", "")

DB_PATH = "bot.db"
