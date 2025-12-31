import asyncio
import logging
import os
import random
import subprocess
import sys

# Telegram Imports
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.request import HTTPXRequest

# Playwright & Stealth Imports
from playwright.async_api import async_playwright
# If you don't have playwright-stealth, remove the import and the await stealth_async(page) line
try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

from fake_useragent import UserAgent

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN", "8405700631:AAHQFlEBRcdqzL6d8ek_0pfBOVuwiVYYYlg")
MOVIE_URL = os.getenv("MOVIE_URL", "https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/ET00430817/20260109")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "099ee81831f919a57cce86729ef5bef7")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")
MOVIE_NAME = "Jana Nayagan"

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- AUTO-INSTALLER (THE FIX) ---
def install_browser():
    """
    Automatically installs Chromium if missing.
    This fixes the 'Executable doesn't exist' error.
    """
    logger.info("⬇️ Checking/Installing Playwright Browsers...")
    try:
        # Installs just Chromium to save space/time
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        logger.info("✅ Browser installation successful.")
    except Exception as e:
        logger.error(f"⚠️ Failed to install browser: {e}")

# --- BROWSER MANAGEMENT ---
async def check_ticket_availability():
    ua = UserAgent()
    user_agent = ua.random

    proxy_config = None
    if SCRAPER_API_KEY:
        logger.info("🛡️ Using ScraperAPI Proxy...")
        proxy_config = {
            "server": "http://proxy-server.scraperapi.com:8001",
            "username": "scraperapi",
            "password": SCRAPER_API_KEY
        }

    async with async_playwright() as p:
        try:
            # Launch Browser
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080'
                ]
            )

            context = await browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1920, 'height': 1080}
            )

            if SCRAPER_API_KEY:
                context.set_default_timeout(60000) 

            page = await context.new_page()
            
            # Apply Stealth if available
            if stealth_async:
                await stealth_async(page)

            logger.info(f"🔎 Checking BMS for: {MOVIE_NAME}")
            
            try:
                await page.goto(MOVIE_URL, timeout=90000, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"⚠️ Page load slow ({e}), checking selectors anyway...")

            # --- SMART SELECTORS ---
            try:
                # Look for booking buttons/links
                found_element = await page.wait_for_selector(
                    "a.showtime-pill, .showtime-pill-container, button:has-text('Book'), a[href*='buytickets']",
                    state="visible",
                    timeout=20000
                )

                if found_element:
                    # Check if 'Sold Out' text exists nearby
                    is_sold_out = await page.query_selector("text=Sold Out")
                    if not is_sold_out:
                        logger.info("🎉 TICKETS DETECTED!")
                        screenshot_path = "success.png"
                        await page.screenshot(path=screenshot_path)
                        await browser.close()
                        return True, screenshot_path
                
            except Exception:
                # Check for "Coming Soon"
                content = await page.content()
                if "Coming Soon" in content:
                    logger.info("ℹ️ Status: Coming Soon")
                else:
                    logger.info("ℹ️ No showtimes found.")

            await browser.close()
            return False, None

        except Exception as e:
            logger.error(f"⚠️ Browser Error: {e}")
            return False, None

# --- BACKGROUND TASK ---
async def monitor_task(app: Application):
    logger.info(f"🟢 Monitor Task Started. Checking every {CHECK_INTERVAL}s")
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

                if TARGET_CHAT_ID:
                    try:
                        await app.bot.send_message(chat_id=TARGET_CHAT_ID, text=msg, parse_mode='HTML')
                        if screenshot and os.path.exists(screenshot):
                            await app.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=open(screenshot, 'rb'))
                            os.remove(screenshot)
                    except Exception as e:
                        logger.error(f"❌ Telegram Error: {e}")
                
                await asyncio.sleep(900) # Wait 15 mins if found

            else:
                sleep_time = CHECK_INTERVAL + random.randint(5, 30)
                await asyncio.sleep(sleep_time)

        except Exception as e:
            logger.error(f"⚠️ Loop Error: {e}")
            await asyncio.sleep(60)

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_html(
        f"👋 <b>Bot Online!</b>\n"
        f"Monitoring: {MOVIE_NAME}\n"
        f"Chat ID: <code>{chat_id}</code>"
    )

# --- MAIN ---
def main():
    # 1. RUN AUTO-INSTALLER FIRST
    install_browser()

    if not TOKEN:
        logger.critical("❌ FATAL: BOT_TOKEN is missing!")
        return

    # 2. NETWORK CONFIG (Fixes Railway Crashes)
    request_config = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        http_version="1.1"
    )

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request_config)
        .build()
    )

    application.add_handler(CommandHandler("start", start))

    async def post_init(app: Application):
        asyncio.create_task(monitor_task(app))
    
    application.post_init = post_init

    logger.info("🚀 Bot is starting...")

    # 3. RUN WITH RETRIES
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        bootstrap_retries=-1
    )

if __name__ == "__main__":
    main()
