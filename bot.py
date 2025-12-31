import asyncio
import logging
import os
import random

# Telegram Imports
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.request import HTTPXRequest

# Playwright Imports
from playwright.async_api import async_playwright
from fake_useragent import UserAgent

# --- CONFIGURATION ---
# We look for the Environment Variable first.
# If not found, we use defaults (BUT we keep your secrets out of the code!)

# 1. Bot Token
# Add 'BOT_TOKEN' to your Railway Variables
TOKEN = os.getenv("BOT_TOKEN")

# 2. Movie URL
MOVIE_URL = os.getenv("MOVIE_URL", "https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/ET00430817/20260109")

# 3. Check Interval (in seconds)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))

# 4. Scraper API Key (Proxy)
# Add 'SCRAPER_API_KEY' to your Railway Variables for security.
# We set default to None so it doesn't leak your key if you share code.
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", None)

# 5. Target Chat ID
# Add 'TARGET_CHAT_ID' to your Railway Variables
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

MOVIE_NAME = "Jana Nayagan"

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- BROWSER MANAGEMENT ---
async def check_ticket_availability():
    """
    Checks the BookMyShow URL for ticket availability using Playwright.
    Uses ScraperAPI proxy if available to avoid blocks.
    """
    ua = UserAgent()
    user_agent = ua.random

    # Configure Proxy if API Key is present in Env Vars
    proxy_config = None
    if SCRAPER_API_KEY:
        logger.info("🛡️ Using ScraperAPI Proxy for protection...")
        proxy_config = {
            "server": "http://proxy-server.scraperapi.com:8001",
            "username": "scraperapi",
            "password": SCRAPER_API_KEY
        }

    async with async_playwright() as p:
        try:
            # Launch browser
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,  # Use the proxy if configured
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu'
                ]
            )

            # Create a new context with a realistic user agent and viewport
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1280, 'height': 800}
            )

            # ScraperAPI specific: verify SSL=False often helps with proxies
            if SCRAPER_API_KEY:
                context.set_default_timeout(60000)  # Give proxy more time

            page = await context.new_page()

            logger.info(f"🔎 Checking BMS for: {MOVIE_NAME}")

            # Go to the URL
            await page.goto(MOVIE_URL, timeout=90000, wait_until="domcontentloaded")

            # --- SCRAPING LOGIC ---
            # Check for specific "Book" buttons or showtime availability
            try:
                # Look for the 'Book tickets' button or showtime pills
                # We wait up to 15s because proxies can be slightly slower
                found_showtimes = await page.wait_for_selector(
                    "a.showtime-pill, .showtime-pill, button:has-text('Book'), #showtimes",
                    timeout=15000
                )

                if found_showtimes:
                    logger.info("🎉 TICKETS DETECTED! Found showtime elements.")
                    screenshot_path = "success.png"
                    await page.screenshot(path=screenshot_path)
                    await browser.close()
                    return True, screenshot_path

            except Exception:
                logger.info("ℹ️ No showtimes found (Selector timeout).")

            await browser.close()
            return False, None

        except Exception as e:
            logger.error(f"⚠️ Error during scraping: {e}")
            return False, None

# --- BACKGROUND TASK ---
async def monitor_task(app: Application):
    """
    Continuous loop that checks for tickets based on CHECK_INTERVAL.
    """
    logger.info(f"🟢 Monitor Task Started. Checking every {CHECK_INTERVAL} seconds.")

    # Initial warm-up wait
    await asyncio.sleep(10)

    while True:
        try:
            tickets_found, screenshot = await check_ticket_availability()

            if tickets_found:
                msg = (
                    f"🚨 <b>TICKETS AVAILABLE!</b> 🚨\n\n"
                    f"🎬 <b>Movie:</b> {MOVIE_NAME}\n"
                    f"🔗 <a href='{MOVIE_URL}'>Book Now on BookMyShow</a>"
                )

                # Alert the user if Chat ID is set
                if TARGET_CHAT_ID:
                    await app.bot.send_message(
                        chat_id=TARGET_CHAT_ID,
                        text=msg,
                        parse_mode='HTML'
                    )

                    if screenshot and os.path.exists(screenshot):
                        await app.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=open(screenshot, 'rb'))
                        os.remove(screenshot)
                else:
                    logger.warning("Tickets found, but TARGET_CHAT_ID is not set! Check Railway variables.")

                # Sleep for 10 minutes if found to avoid spamming
                await asyncio.sleep(600)

            else:
                logger.info(f"❌ No tickets. Sleeping for {CHECK_INTERVAL}s...")
                await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"⚠️ Error in main loop: {e}")
            await asyncio.sleep(60)

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_html(
        f"👋 Bot is Online!\n\n"
        f"Monitoring: <b>{MOVIE_NAME}</b>\n"
        f"Check Interval: <b>{CHECK_INTERVAL}s</b>\n"
        f"Proxy Enabled: <b>{'Yes' if SCRAPER_API_KEY else 'No'}</b>\n\n"
        f"🆔 Your Chat ID: <code>{chat_id}</code>\n"
        f"<i>(Copy this ID and add it to Railway variables as TARGET_CHAT_ID if you haven't already!)</i>"
    )

# --- MAIN SETUP ---
def main():
    if not TOKEN:
        logger.critical("❌ FATAL: BOT_TOKEN is missing! Add it to Railway Variables.")
        return

    # Advanced Network Configuration (Fixes Railway Timeouts & HTTP Version Error)
    request_config = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        http_version="1.1"  # [FIX] Set HTTP version HERE, not in the builder
    )

    # Build Application
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request_config)  # Apply network fixes
        .build()
    )

    # Add Handlers
    application.add_handler(CommandHandler("start", start))

    # Register Background Task
    async def post_init(app: Application):
        asyncio.create_task(monitor_task(app))

    application.post_init = post_init

    logger.info("🚀 Bot is starting...")

    # Infinite retry loop on startup (Fixes initialization crashes)
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        bootstrap_retries=-1
    )

if __name__ == "__main__":
    main()
