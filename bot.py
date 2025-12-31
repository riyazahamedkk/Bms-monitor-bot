import os
import time
import threading
import hashlib
import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")  # ET00430817
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "2"))

if not BOT_TOKEN or not MOVIE_CODE:
    raise RuntimeError("Missing BOT_TOKEN or MOVIE_CODE")

USER_DATA = {}          # user_id → state
SEEN_SHOWS = set()      # fingerprints

print("🟢 Telegram BookMyShow bot started")

# ================= TELEGRAM =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Tamil Nadu", callback_data="state:Tamil Nadu")],
        [InlineKeyboardButton("Karnataka", callback_data="state:Karnataka")],
        [InlineKeyboardButton("Kerala", callback_data="state:Kerala")],
        [InlineKeyboardButton("Andhra Pradesh", callback_data="state:Andhra Pradesh")]
    ]
    await update.message.reply_text(
        "📍 Select your STATE to start monitoring:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    state = query.data.split(":", 1)[1]
    USER_DATA[query.from_user.id] = state

    await query.edit_message_text(
        f"✅ State selected: *{state}*\n\n"
        "🎬 Live monitoring started.\n"
        "You’ll be notified ONLY when:\n"
        "• New theatre added\n"
        "• New show date\n"
        "• New show timing",
        parse_mode="Markdown"
    )

# ================= SCRAPER =================

def fetch_shows():
    url = f"https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/{MOVIE_CODE}"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, headers=headers, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    results = []

    theatres = soup.select("div.__venue-name")
    for theatre in theatres:
        theatre_name = theatre.get_text(strip=True)

        parent = theatre.find_parent("div", class_="__venue-details")
        if not parent:
            continue

        times = parent.select("a.showtime-pill")

        for t in times:
            show_time = t.get_text(strip=True)
            fingerprint = f"{theatre_name}|{show_time}"
            results.append(fingerprint)

    return results

# ================= MONITOR =================

def monitor_loop(app):
    global SEEN_SHOWS

    while True:
        try:
            shows = fetch_shows()

            for show in shows:
                h = hashlib.md5(show.encode()).hexdigest()

                if h not in SEEN_SHOWS:
                    SEEN_SHOWS.add(h)

                    theatre, time_str = show.split("|")

                    for user_id in USER_DATA:
                        app.bot.send_message(
                            chat_id=user_id,
                            text=(
                                "🚨 *NEW SHOW DETECTED*\n\n"
                                f"🎭 Theatre: {theatre}\n"
                                f"⏰ Time: {time_str}\n\n"
                                "👉 Book now on BookMyShow!"
                            ),
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
