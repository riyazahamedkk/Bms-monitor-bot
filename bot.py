import os
import time
import json
import threading
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")
CITY = os.getenv("CITY", "bengaluru")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

if not BOT_TOKEN or not MOVIE_CODE:
    raise RuntimeError("Missing BOT_TOKEN or MOVIE_CODE")

# ================== GLOBALS ==================
BASE_URL = f"https://in.bookmyshow.com/movies/{CITY}/jana-nayagan/buytickets/{MOVIE_CODE}"
HEADERS = {
    "User-Agent": "Mozilla/5.0"
}
last_fingerprint = set()
subscribers = set()

# ================== HELPERS ==================
def fetch_shows():
    """Scrape BookMyShow and return a set fingerprint"""
    try:
        res = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return set()

        soup = BeautifulSoup(res.text, "html.parser")
        fingerprint = set()

        theatres = soup.select('[data-testid="theatre-card"]')
        for theatre in theatres:
            name_el = theatre.select_one("h3, h4")
            if not name_el:
                continue

            theatre_name = name_el.get_text(strip=True)

            shows = theatre.select("a[href*='/buytickets/']")
            for show in shows:
                show_time = show.get_text(strip=True)
                link = show.get("href").split("?")[0]
                fingerprint.add(f"{theatre_name} | {show_time} | {link}")

        return fingerprint
    except Exception as e:
        print("SCRAPE ERROR:", e)
        return set()

# ================== MONITOR LOOP ==================
def monitor_loop(app):
    global last_fingerprint
    print("🟢 Monitor started")

    while True:
        current = fetch_shows()

        if current and last_fingerprint:
            new_items = current - last_fingerprint

            if new_items:
                theatres = {i.split("|")[0].strip() for i in new_items}
                message = (
                    "🎬 *Jana Nayagan – New Shows Added!*\n\n"
                    f"🎭 Theatres: {len(theatres)}\n"
                    f"🎟 Shows: {len(new_items)}\n\n"
                    f"🔗 {BASE_URL}"
                )

                for chat_id in subscribers:
                    try:
                        app.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        print("SEND ERROR:", e)

        last_fingerprint = current
        time.sleep(CHECK_INTERVAL)

# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers.add(chat_id)
    await update.message.reply_text(
        "🟢 Jana Nayagan Monitor Activated\n\n"
        "You will receive alerts when NEW theatres or shows are added.\n"
        f"⏱ Checking every {CHECK_INTERVAL} seconds."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🟢 Bot is running\n"
        f"City: {CITY}\n"
        f"Movie Code: {MOVIE_CODE}\n"
        f"Interval: {CHECK_INTERVAL}s"
    )

# ================== MAIN ==================
def main():
    print("🟢 Telegram BookMyShow bot started")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))

    # Background monitor thread
    thread = threading.Thread(target=monitor_loop, args=(app,), daemon=True)
    thread.start()

    app.run_polling()

if __name__ == "__main__":
    main()
