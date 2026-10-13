import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("8823524687:AAGTIIRQDsPbqAyUO-_KwySI-znQAASi3VA", "").strip()
WEBAPP_URL = os.environ.get("https://test123etn.netlify.app", "").strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WEBAPP_URL.startswith("https://"):
        await update.message.reply_text(
            "Бот запущен, но WEBAPP_URL не настроен.\n"
            "Нужен публичный HTTPS-адрес (например, через Cloudflare Tunnel)."
        )
        return
    keyboard = [[InlineKeyboardButton(
        "Открыть ETN RAPIRA ONLINE",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )]]
    await update.message.reply_text(
        "Добро пожаловать в ETN RAPIRA ONLINE!\n"
        "Нажми кнопку ниже, чтобы открыть турнирную платформу.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def main():
    if not BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN в переменных окружения.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("ETN Telegram bot started. Stop with Ctrl+C.")
    app.run_polling()

if __name__ == "__main__":
    main()
