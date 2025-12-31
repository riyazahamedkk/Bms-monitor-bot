import os
import time
import threading
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")  # ET00430817
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "5"))

if not BOT_TOKEN or not MOVIE_CODE:
    raise RuntimeError("Missing BOT_TOKEN or MOVIE_CODE")

# Store user selections
USER_DATA = {}

print("🟢 Telegram BookMyShow bot started")

# ================= TELEGRAM HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Tamil Nadu", callback_data="state:Tamil Nadu")],
        [InlineKeyboardButton("Karnataka", callback_data="state:Karnataka")],
        [InlineKeyboardButton("Kerala", callback_data="state:Kerala")],
        [InlineKeyboardButton("Andhra Pradesh", callback_data="state:Andhra Pradesh")]
    ]
    await update.message.reply_text(
        "📍 Select your STATE:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("state:"):
        state = data.split(":", 1)[1]
        USER_DATA[query.from_user.id] = {"state": state}

        await query.edit_message_text(
            f"✅ State selected: {state}\n\n🎬 Monitoring started!\nYou’ll get alerts when new shows/theatres are added."
        )

# ================= MONITOR LOOP =================

def monitor_loop(app):
    last_seen = set()

    while True:
        try:
            url = f"https://in.bookmyshow.com/buytickets/{MOVIE_CODE}"
            headers = {
                "User-Agent": "Mozilla/5.0"
            }

            r = requests.get(url, headers=headers, timeout=15)
            content = r.text

            # Very basic change detection
            if content and hash(content) not in last_seen:
                last_seen.add(hash(content))

                for user_id in USER_DATA:
                    app.bot.send_message(
                        chat_id=user_id,
                        text="🚨 New update detected for *Jana Nayagan*!\nCheck BookMyShow now 👀",
                        parse_mode="Markdown"
                    )

        except Exception as e:
            print("⚠ Monitor error:", e)

        time.sleep(CHECK_INTERVAL)

# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_selection))

    threading.Thread(target=monitor_loop, args=(app,), daemon=True).start()

    app.run_polling()

if __name__ == "__main__":
    main()
