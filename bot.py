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
# 1. Bot Token
# Get from Railway Variables or use default
TOKEN = os.getenv("BOT_TOKEN", "8405700631:AAHQFlEBRcdqzL6d8ek_0pfBOVuwiVYYYlg")

# 2. Movie URL
MOVIE_URL = os.getenv("MOVIE_URL", "https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/ET00430817/20260109")

# 3. Check Interval (in seconds)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))

# 4. Scraper API Key (Proxy)
# I included your key here as a fallback, but it's better to use Railway Variables.
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "099ee81831f919a57cce86729ef5bef7")

# 5. Target Chat ID
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")

MOVIE_NAME = "Jana Nayagan"

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- BROWSER MANAGEMENT ---
async def check_ticket_availability():
    """
    Checks BookMyShow for tickets. 
    Returns: (bool: found?, str: screenshot_path)
    """
    ua = UserAgent()
    user_agent = ua.random

    # Configure Proxy (if key exists)
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
                    '--window-size=1280,800'
                ]
            )

            context = await browser.new_context(
                user_agent=user_agent,
                viewport={'width': 1280, 'height': 800},
                device_scale_factor=1
            )

            # Increase timeout if using proxy
            if SCRAPER_API_KEY:
                context.set_default_timeout(60000) 

            page = await context.new_page()
            
            logger.info(f"🔎 Checking BMS for: {MOVIE_NAME}")
            
            # Navigate
            try:
                await page.goto(MOVIE_URL, timeout=90000, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"⚠️ Timeout loading page, but continuing to check selectors... ({e})")

            # --- SMART SELECTORS ---
            # We look for ANY indication of booking availability:
            # 1. ".showtime-pill" -> Standard desktop showtime buttons
            # 2. ".showtime-pill-container" -> Container for times
            # 3. "button:has-text('Book')" -> Sometimes appears on overlays
            # 4. "a[href*='booktickets']" -> Direct booking links
            
            try:
                found_element = await page.wait_for_selector(
                    "a.showtime-pill, .showtime-pill, .showtime-pill-container, button:has-text('Book'), a[data-id='book-tickets']",
                    state="attached",
                    timeout=20000
                )

                if found_element:
                    logger.info("🎉 TICKETS DETECTED!")
                    screenshot_path = "success.png"
                    await page.screenshot(path=screenshot_path)
                    await browser.close()
                    return True, screenshot_path
                
            except Exception:
                # Double check: if we see "Coming Soon" text, we know definitely NOT available
                content = await page.content()
                if "Coming Soon" in content or "tickets are not available" in content:
                    logger.info("ℹ️ Status: Coming Soon / Not Available")
                else:
                    logger.info("ℹ️ No showtimes found.")

            await browser.close()
            return False, None

        except Exception as e:
            logger.error(f"⚠️ Critical Browser Error: {e}")
            return False, None

# --- BACKGROUND TASK ---
async def monitor_task(app: Application):
    logger.info(f"🟢 Monitor Task Started. Checking every {CHECK_INTERVAL}s")
    await asyncio.sleep(10) # Warmup

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
                        logger.error(f"❌ Failed to send Telegram alert: {e}")
                else:
                    logger.warning("Tickets found, but TARGET_CHAT_ID is missing!")
                
                # Wait 15 mins to avoid spamming alerts
                await asyncio.sleep(900)

            else:
                # Wait for the next check
                await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"⚠️ Error in main loop: {e}")
            await asyncio.sleep(60)

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_html(
        f"👋 <b>Bot is Online!</b>\n\n"
        f"🎬 Monitoring: {MOVIE_NAME}\n"
        f"⏱ Interval: {CHECK_INTERVAL}s\n"
        f"🆔 Chat ID: <code>{chat_id}</code>\n"
        f"<i>(Add this Chat ID to Railway Variables if not done yet)</i>"
    )

# --- MAIN EXECUTION ---
def main():
    if not TOKEN:
        logger.critical("❌ FATAL: BOT_TOKEN is missing! Please set it in Railway Variables.")
        return

    # [FIX] Advanced Network Config
    # We set http_version HERE to avoid the RuntimeError you were getting.
    request_config = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        http_version="1.1" # Forces HTTP/1.1 for stability
    )

    # Build Application
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request_config) # Inject the custom network config
        .build()
    )

    application.add_handler(CommandHandler("start", start))

    # Initialize Background Task
    async def post_init(app: Application):
        asyncio.create_task(monitor_task(app))
    
    application.post_init = post_init

    logger.info("🚀 Bot is starting...")

    # Run Polling with Infinite Retries
    # This prevents the bot from crashing if Railway has a network hiccup on startup.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        bootstrap_retries=-1
    )

if __name__ == "__main__":
    main()
