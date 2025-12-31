import os
import logging
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# 1. Setup Logging (Essential for debugging errors)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Load Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")
CITY = os.getenv("CITY", "bengaluru")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

# Validate essentials immediately
if not BOT_TOKEN or not MOVIE_CODE:
    logger.error("CRITICAL: BOT_TOKEN or MOVIE_CODE environment variables are missing!")
    # For local testing, you can hardcode them here, but don't share that file:
    # BOT_TOKEN = "YOUR_TOKEN_HERE"
    # MOVIE_CODE = "ET00412403" 

BASE_URL = f"https://in.bookmyshow.com/movies/{CITY}/jana-nayagan/buytickets/{MOVIE_CODE}"

# 3. Enhanced Headers (To prevent being blocked as a bot)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://in.bookmyshow.com/"
}

last_snapshot = set()
subscribers = set()

# ================= SCRAPER =================
def scrape_shows():
    try:
        r = requests.get(BASE_URL, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            logger.warning(f"Failed to fetch page. Status code: {r.status_code}")
            return set()

        soup = BeautifulSoup(r.text, "html.parser")
        result = set()

        # Updated selector logic for current BMS layout
        theatres = soup.find_all('li', class_='list')
        
        for theatre in theatres:
            name_el = theatre.find('a', class_='__venue-name')
            if not name_el:
                continue
            
            theatre_name = name_el.get_text(strip=True)
            
            # Find all available show times
            shows = theatre.find_all('div', class_='showtime-pill-container')
            for show in shows:
                time_txt = show.get_text(strip=True)
                # We use theatre + time as a unique ID for the show
                result.add(f"{theatre_name} | {time_txt}")

        return result
    except Exception as e:
        logger.error(f"Scrape Error: {e}")
        return set()

# ================= MONITOR =================
async def monitor(context: ContextTypes.DEFAULT_TYPE):
    global last_snapshot

    current = scrape_shows()
    if not current:
        return

    # If this is the first run, just establish the baseline
    if not last_snapshot:
        last_snapshot = current
        logger.info(f"Initial snapshot created. Found {len(current)} shows.")
        return

    # Check for new additions
    diff = current - last_snapshot
    if diff:
        theatres = {x.split("|")[0].strip() for x in diff}
        msg = (
            "🎬 *Jana Nayagan – New Shows Added!*\n\n"
            f"🎭 *Theatres:* {len(theatres)}\n"
            f"🎟 *New Slots:* {len(diff)}\n\n"
            f"🔗 [Book Now on BookMyShow]({BASE_URL})"
        )

        for chat_id in list(subscribers):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=False
                )
            except Exception as e:
                logger.error(f"Error sending to {chat_id}: {e}")

    last_snapshot = current

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers.add(update.effective_chat.id)
    await update.message.reply_text(
        "🟢 *Jana Nayagan Monitor ON*\n\n"
        f"📍 City: {CITY.capitalize()}\n"
        f"⏱ Interval: {CHECK_INTERVAL}s\n"
        "You'll get an alert when new shows appear!",
        parse_mode="Markdown"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = len(last_snapshot)
    await update.message.reply_text(
        f"🟢 *Bot Status*\n\n"
        f"📍 City: {CITY}\n"
        f"📊 Current Shows: {count}\n"
        f"👥 Subscribers: {len(subscribers)}",
        parse_mode="Markdown"
    )

# ================= MAIN =================
def main():
    if not BOT_TOKEN:
        return # Prevent startup if token is missing

    # Build the application
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))

    # Check if JobQueue is available
    if application.job_queue:
        application.job_queue.run_repeating(monitor, interval=CHECK_INTERVAL, first=5)
        logger.info("Job Queue started successfully.")
    else:
        logger.error("Job Queue is not available. Install with: pip install 'python-telegram-bot[job-queue]'")

    logger.info("Bot is polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
