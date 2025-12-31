import os
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")
CITY = os.getenv("CITY", "bengaluru")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

if not BOT_TOKEN or not MOVIE_CODE:
    raise RuntimeError("Missing BOT_TOKEN or MOVIE_CODE")

BASE_URL = f"https://in.bookmyshow.com/movies/{CITY}/jana-nayagan/buytickets/{MOVIE_CODE}"
HEADERS = {"User-Agent": "Mozilla/5.0"}

last_snapshot = set()
subscribers = set()

# ================= SCRAPER =================
def scrape_shows():
    try:
        r = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return set()

        soup = BeautifulSoup(r.text, "html.parser")
        result = set()

        for theatre in soup.select('[data-testid="theatre-card"]'):
            name = theatre.select_one("h3, h4")
            if not name:
                continue

            theatre_name = name.get_text(strip=True)

            for show in theatre.select("a[href*='/buytickets/']"):
                time_txt = show.get_text(strip=True)
                link = show.get("href", "").split("?")[0]
                result.add(f"{theatre_name} | {time_txt} | {link}")

        return result
    except Exception as e:
        print("SCRAPE ERROR:", e)
        return set()

# ================= MONITOR =================
async def monitor(context: ContextTypes.DEFAULT_TYPE):
    global last_snapshot

    current = scrape_shows()
    if not current:
        return

    if last_snapshot:
        diff = current - last_snapshot
        if diff:
            theatres = {x.split("|")[0].strip() for x in diff}
            msg = (
                "🎬 *Jana Nayagan – New Shows Added!*\n\n"
                f"🎭 Theatres: {len(theatres)}\n"
                f"🎟 Shows: {len(diff)}\n\n"
                f"🔗 {BASE_URL}"
            )

            for chat_id in subscribers:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print("SEND ERROR:", e)

    last_snapshot = current

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)
    await update.message.reply_text(
        "🟢 Jana Nayagan Monitor ON\n\n"
        f"⏱ Checking every {CHECK_INTERVAL} seconds.\n"
        "You’ll be alerted when NEW shows or theatres appear."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🟢 Running\nCity: {CITY}\nInterval: {CHECK_INTERVAL}s"
    )

# ================= MAIN =================
def main():
    print("🟢 Telegram BookMyShow bot started")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))

    app.job_queue.run_repeating(monitor, interval=CHECK_INTERVAL, first=10)

    app.run_polling()

if __name__ == "__main__":
    main()
