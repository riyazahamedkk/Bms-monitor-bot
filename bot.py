import asyncio
import logging
import os
import sys
import subprocess
import random

# Telegram Imports
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.request import HTTPXRequest

# Playwright Imports
from playwright.async_api import async_playwright
from fake_useragent import UserAgent

# Try importing stealth, but don't crash if missing
try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
# If you didn't set variables, use these fallbacks (replace with your own if needed)
if not TOKEN:
    TOKEN = "8405700631:AAHQFlEBRcdqzL6d8ek_0pfBOVuwiVYYYlg"

MOVIE_URL = os.getenv("MOVIE_URL", "https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/ET00430817/20260109")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY") # Optional
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")
MOVIE_NAME = "Jana Nayagan"

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Silence noisy logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- [CRITICAL FIX] AUTO-INSTALLER ---
def install_browser():
    """
    Checks and installs the Chromium browser automatically.
    This FIXES the 'Executable doesn't exist' error.
    """
    logger.info("⬇️ System Check: Verifying Browser...")
    try:
        # This command installs the chromium binary required by Playwright
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"], 
            check=True
        )
        logger.info("✅ Browser installed successfully.")
    except Exception as e:
        logger.error(f"⚠️ Browser installation warning: {e}")
        # We continue anyway, as it might already be installed in a different path

# --- BROWSER LOGIC ---
async def check_ticket_availability():
    ua = UserAgent()
    user_agent = ua.random

    # Proxy Config
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
            # We explicitly use chromium which is lighter and faster
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
            
            # Apply Stealth if installed
            if STEALTH_AVAILABLE:
                await stealth_async(page)

            logger.info(f"🔎 Checking BMS for: {MOVIE_NAME}")

            try:
                await page.goto(MOVIE_URL, timeout=90000, wait_until="domcontentloaded")
            except Exception:
                logger.warning("⚠️ Page load timeout, checking selectors anyway...")

            # --- SELECTOR LOGIC ---
            # We look for POSITIVE signs (Booking buttons)
            try:
                found_element = await page.wait_for_selector(
                    "a.showtime-pill, .showtime-pill-container, button:has-text('Book'), a[href*='buytickets']",
                    state="visible", 
                    timeout=20000
                )

                if found_element:
                    # Double check for "Sold Out" text
                    is_sold_out = await page.query_selector("text=Sold Out")
                    if not is_sold_out:
                        logger.info("🎉 TICKETS DETECTED!")
                        screenshot_path = "success.png"
                        await page.screenshot(path=screenshot_path)
                        await browser.close()
                        return True, screenshot_path

            except Exception:
                logger.info("ℹ️ No showtimes found.")

            await browser.close()
            return False, None

        except Exception as e:
            logger.error(f"⚠️ Browser Error: {e}")
            return False, None

# --- BOT TASK ---
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
                    f"🔗 <a href='{MOVIE_URL}'>Book Now</a>"
                )
                
                if TARGET_CHAT_ID:
                    try:
                        await app.bot.send_message(chat_id=TARGET_CHAT_ID, text=msg, parse_mode='HTML')
                        if screenshot and os.path.exists(screenshot):
                            with open(screenshot, 'rb') as photo:
                                await app.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=photo)
                            os.remove(screenshot)
                    except Exception as e:
                        logger.error(f"❌ Telegram Error: {e}")
                
                await asyncio.sleep(900) # Sleep 15 mins if found
            else:
                # Random jitter to avoid detection
                await asyncio.sleep(CHECK_INTERVAL + random.randint(1, 15))

        except Exception as e:
            logger.error(f"⚠️ Loop Error: {e}")
            await asyncio.sleep(60)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        f"👋 <b>Bot is Online!</b>\n"
        f"Monitoring: {MOVIE_NAME}\n"
        f"Chat ID: <code>{update.effective_chat.id}</code>"
    )

# --- MAIN ---
def main():
    # 1. INSTALL BROWSER FIRST
    install_browser()

    if not TOKEN:
        logger.critical("❌ FATAL: BOT_TOKEN is missing!")
        return

    # 2. NETWORK CONFIG (Robust)
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
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        bootstrap_retries=-1
    )

if __name__ == "__main__":
    main()
