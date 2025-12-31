import asyncio
import logging
import os
import random
from datetime import datetime

# Telegram Imports
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError

# Playwright Imports
from playwright.async_api import async_playwright
from fake_useragent import UserAgent

# --- CONFIGURATION ---
# 1. Get these from Railway Variables or hardcode them for testing
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Add this in Railway Variables
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID") # Add this in Railway Variables

# 2. Movie Details (From your logs)
MOVIE_URL = "https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/ET00430817/20260109"
MOVIE_NAME = "Jana Nayagan"

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- BROWSER MANAGEMENT ---
async def check_ticket_availability():
    """
    Checks the BookMyShow URL for ticket availability using Playwright.
    Returns True if tickets are found (booking button is active), False otherwise.
    """
    ua = UserAgent()
    user_agent = ua.random
    
    async with async_playwright() as p:
        # Launch browser (headless for Railway)
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', 
                    '--disable-setuid-sandbox', 
                    '--disable-dev-shm-usage',
                    '--disable-gpu'
                ]
            )
            
            context = await browser.new_context(user_agent=user_agent)
            page = await context.new_page()

            logger.info(f"🔎 Checking BMS for: {MOVIE_NAME}")
            
            # Set a rigorous timeout for loading the page
            await page.goto(MOVIE_URL, timeout=60000, wait_until="domcontentloaded")
            
            # --- SCRAPING LOGIC ---
            # Wait for the main container or specific indicators of availability.
            # BookMyShow usually has a 'Book tickets' button or showtime pills.
            # If the page says "Coming Soon" or has no showtimes, we return False.
            
            # We look for the existence of showtime elements (generic class check)
            # Adjust this selector if BMS changes their layout
            try:
                # Wait up to 10 seconds for a "book ticket" related element or showtime list
                # This selector looks for showtime links/buttons
                found_showtimes = await page.wait_for_selector(
                    "a.showtime-pill, .showtime-pill, button:has-text('Book')", 
                    timeout=10000
                )
                
                if found_showtimes:
                    logger.info("🎉 TICKETS DETECTED! Found showtime elements.")
                    screenshot_path = "success.png"
                    await page.screenshot(path=screenshot_path)
                    return True, screenshot_path
                
            except Exception:
                logger.info("ℹ️ No showtimes found (Timeout waiting for selector).")
            
            await browser.close()
            return False, None

        except Exception as e:
            logger.error(f"⚠️ Error during scraping: {e}")
            return False, None

# --- BACKGROUND TASK ---
async def monitor_task(app: Application):
    """
    Continuous loop that checks for tickets every few minutes.
    """
    logger.info("🟢 Monitor Task Started")
    
    # Wait a bit on startup to let the bot initialize fully
    await asyncio.sleep(10)

    while True:
        try:
            tickets_found, screenshot = await check_ticket_availability()

            if tickets_found and TARGET_CHAT_ID:
                msg = (
                    f"🚨 <b>TICKETS AVAILABLE!</b> 🚨\n\n"
                    f"🎬 <b>Movie:</b> {MOVIE_NAME}\n"
                    f"🔗 <a href='{MOVIE_URL}'>Book Now on BookMyShow</a>"
                )
                
                # Send text alert
                await app.bot.send_message(
                    chat_id=TARGET_CHAT_ID, 
                    text=msg, 
                    parse_mode='HTML'
                )

                # Send screenshot if available
                if screenshot and os.path.exists(screenshot):
                    await app.bot.send_photo(chat_id=TARGET_CHAT_ID, photo=open(screenshot, 'rb'))
                    os.remove(screenshot)
                
                # If found, maybe slow down checks or stop? 
                # For now, we sleep longer to avoid spamming yourself
                await asyncio.sleep(600) 

            else:
                logger.info("❌ No tickets found yet.")
                # Wait random time between 3-5 minutes to behave like a human
                await asyncio.sleep(random.randint(180, 300))

        except Exception as e:
            logger.error(f"⚠️ Error in main loop: {e}")
            await asyncio.sleep(60)

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_html(
        f"👋 Hi {user.mention_html()}!\n\n"
        f"I am monitoring <b>{MOVIE_NAME}</b>.\n"
        f"I will alert you here: {update.effective_chat.id}\n\n"
        f"<i>(Make sure to set this Chat ID in your environment variables if different)</i>"
    )

# --- MAIN SETUP ---
def main():
    if not TOKEN:
        logger.critical("❌ FATAL: TELEGRAM_BOT_TOKEN is missing!")
        return

    # [CRITICAL FIX] Advanced Network Configuration for Railway
    # ---------------------------------------------------------
    # This specifically fixes the 'httpx.ConnectTimeout' and 
    # 'RuntimeError: ExtBot is not properly initialized' errors.
    request_config = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,  # Increased from 5s to 60s
        read_timeout=60.0,     # Increased from 5s to 60s
        write_timeout=60.0,
        pool_timeout=60.0,
    )

    # Build Application
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request_config)  # Apply network fixes
        .get_updates_http_version("1.1") # Force HTTP/1.1 for stability
        .build()
    )

    # Add Handlers
    application.add_handler(CommandHandler("start", start))

    # Register Background Task
    async def post_init(app: Application):
        asyncio.create_task(monitor_task(app))
    
    application.post_init = post_init

    logger.info("🚀 Bot is starting with Advanced Network Repair...")

    # [CRITICAL FIX] Run Polling with Retry Logic
    # -------------------------------------------
    # bootstrap_retries=-1 prevents the bot from crashing if it
    # fails to connect to Telegram immediately on startup.
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        bootstrap_retries=-1
    )

if __name__ == "__main__":
    main()
