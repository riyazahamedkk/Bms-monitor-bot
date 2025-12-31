import asyncio
import logging
import sqlite3
import json
import os
import sys
import random
from datetime import datetime

# Third-party imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from fake_useragent import UserAgent

# ================= CONFIGURATION =================
# ⚠️ RAILWAY ENV VARS
BOT_TOKEN = os.getenv("BOT_TOKEN") 

# Railway Settings
HEADLESS_MODE = True  # Must be True for Railway
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120")) 
USER_DATA_DIR = "./browser_data" 

# Database Path (Railway Persistence Check)
# If a volume is mounted at /app/data, use it. Otherwise, use local file.
if os.path.exists("/app/data"):
    DB_FILE = "/app/data/monitor.db"
else:
    DB_FILE = "monitor.db"

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ================= DATABASE MANAGER (FIXED) =================
class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        # Ensure directory exists
        if "/" in db_file:
            os.makedirs(os.path.dirname(db_file), exist_ok=True)

    def init_db(self):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    movie_name TEXT,
                    movie_url TEXT,
                    city TEXT,
                    notify_mode TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    user_id INTEGER PRIMARY KEY,
                    data_json TEXT,
                    last_updated TIMESTAMP
                )
            """)
            conn.commit()

    def get_active_users(self):
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE is_active = 1 AND movie_url IS NOT NULL")
            return [dict(row) for row in cursor.fetchall()]

    # 🟢 FIXED: Handles empty updates correctly to prevent SQL crashes
    def update_user(self, user_id, chat_id, **kwargs):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            
            # 1. Check if user exists
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone()
            
            # 2. Insert if new
            if not exists:
                cursor.execute("INSERT INTO users (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id))

            # 3. Update fields ONLY if there is data to update
            if kwargs:
                set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
                values = list(kwargs.values()) + [user_id]
                cursor.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", values)
            
            conn.commit()

    def get_snapshot(self, user_id):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data_json FROM snapshots WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return json.loads(row[0]) if row and row[0] else {}

    def save_snapshot(self, user_id, data):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO snapshots (user_id, data_json, last_updated) VALUES (?, ?, ?)",
                (user_id, json.dumps(data), datetime.now())
            )
            conn.commit()

    def stop_monitoring(self, user_id):
        with sqlite3.connect(self.db_file) as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
            conn.commit()

db = Database(DB_FILE)

# ================= STEALTH BROWSER MANAGER =================
class BrowserManager:
    def __init__(self):
        self.ua = UserAgent()

    def get_stealth_args(self):
        """Arguments to hide that we are a bot running on a server"""
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-zygote",
            "--disable-gpu",
            "--window-size=1920,1080",
        ]

    async def search_movie(self, query):
        async with async_playwright() as p:
            user_agent = self.ua.random
            browser = await p.chromium.launch(
                headless=HEADLESS_MODE,
                args=self.get_stealth_args()
            )
            context = await browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080},
                locale="en-IN",
                timezone_id="Asia/Kolkata"
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = await context.new_page()
            results = []
            
            try:
                logger.info(f"🔎 Searching BMS for: {query}")
                await page.goto("https://in.bookmyshow.com/explore/home/", timeout=60000)
                
                # Robust Search Interaction
                try:
                    search_box = page.locator("span#4, input[type='text']").first
                    await search_box.click(force=True, timeout=5000)
                except:
                    # Fallback for mobile view or different layout
                    await page.get_by_role("button", name="Search").click()
                
                input_field = page.locator("input")
                await input_field.fill(query)
                await asyncio.sleep(2) 

                # Scrape results
                await page.wait_for_selector("a[href*='/movies/']", timeout=10000)
                links = await page.locator("a[href*='/movies/']").all()

                count = 0
                for link in links:
                    if count >= 5: break
                    url = await link.get_attribute("href")
                    title = await link.inner_text()
                    
                    if title and url and query.lower() in title.lower():
                        if "bookmyshow.com" not in url:
                            url = "https://in.bookmyshow.com" + url
                        results.append({"title": title.strip(), "url": url})
                        count += 1
                        
            except Exception as e:
                logger.error(f"Search failed: {e}")
            finally:
                await browser.close()
            
            return results

    async def fetch_movie_data(self, url, city):
        data = {}
        error = None
        
        async with async_playwright() as p:
            try:
                user_agent = self.ua.random
                browser = await p.chromium.launch(
                    headless=HEADLESS_MODE,
                    args=self.get_stealth_args()
                )
                
                context = await browser.new_context(
                    user_agent=user_agent,
                    viewport={"width": 1920, "height": 1080},
                    locale="en-IN",
                    timezone_id="Asia/Kolkata"
                )
                await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                page = await context.new_page()
                
                logger.info(f"🌍 Fetching: {url} | UA: {user_agent[:20]}...")
                response = await page.goto(url, timeout=90000, wait_until="domcontentloaded")
                
                # Check for Block
                if response.status == 403:
                    raise Exception("BMS Blocked Request (403)")

                # Handle City Modal
                try:
                    city_input = page.get_by_placeholder("Search for your city")
                    if await city_input.is_visible(timeout=5000):
                        await city_input.fill(city)
                        await page.get_by_text(city, exact=False).first.click()
                        await asyncio.sleep(2)
                except PlaywrightTimeout:
                    pass

                # Check "No Shows"
                if await page.get_by_text("No shows available").is_visible():
                    await browser.close()
                    return {}, None

                # Scrape Venues
                try:
                    await page.wait_for_selector("li.list-group-item", timeout=10000)
                except:
                    await browser.close()
                    return {}, None

                venue_elements = await page.locator("li.list-group-item").all()
                for venue in venue_elements:
                    name_el = venue.locator("a.body-text")
                    if await name_el.count() > 0:
                        venue_name = await name_el.first.inner_text()
                        
                        times = []
                        time_elements = await venue.locator(".showtime-pill .time-text").all()
                        for t in time_elements:
                            times.append((await t.inner_text()).strip())
                        
                        if times:
                            data[venue_name] = sorted(times)

            except Exception as e:
                error = f"Fetch Error: {str(e)}"
                logger.error(error)
            finally:
                await browser.close()
                
        return data, error

browser_manager = BrowserManager()

# ================= TELEGRAM HANDLERS =================
SEARCH, SELECT_MOVIE, SELECT_CITY, SELECT_MODE = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # 🟢 FIXED: Safe DB update
    db.update_user(user.id, update.effective_chat.id)
    
    await update.message.reply_text(
        f"👋 Hi {user.first_name}!\n\n"
        "I am running on **Railway Cloud** ☁️.\n"
        "I monitor BookMyShow for new theatres and showtimes.\n\n"
        "**Commands:**\n"
        "/setup - Start monitoring a movie\n"
        "/status - Check active monitors\n"
        "/stop - Stop all monitoring"
    )

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 **Movie Search**\nEnter the movie name:")
    return SEARCH

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    msg = await update.message.reply_text("🔍 Searching... (Takes 10-20s on Cloud)")
    results = await browser_manager.search_movie(query)
    
    if not results:
        await msg.edit_text("❌ No movies found. Try again:")
        return SEARCH

    keyboard = []
    for idx, r in enumerate(results[:5]):
        context.user_data[f"movie_{idx}"] = r
        keyboard.append([InlineKeyboardButton(r['title'], callback_data=f"sel_mov_{idx}")])
    
    await msg.edit_text("✅ **Select your movie:**", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[-1])
    context.user_data["selected_movie"] = context.user_data.get(f"movie_{idx}")
    await query.edit_message_text(f"Selected: **{context.user_data['selected_movie']['title']}**\n\nEnter City Name (e.g., 'Bengaluru'):")
    return SELECT_CITY

async def city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("New Theatres Only", callback_data="THEATRE")],
        [InlineKeyboardButton("New Shows Only", callback_data="SHOW")],
        [InlineKeyboardButton("Both", callback_data="BOTH")]
    ]
    await update.message.reply_text("🔔 **Notify me on:**", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODE

async def mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data
    user_id = query.from_user.id
    data = context.user_data
    
    db.update_user(
        user_id, query.message.chat_id,
        movie_name=data["selected_movie"]["title"],
        movie_url=data["selected_movie"]["url"],
        city=data["city"],
        notify_mode=mode
    )
    db.save_snapshot(user_id, {})
    await query.edit_message_text("✅ **Setup Complete!**\nMonitoring started. I'll check every 2 minutes.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.stop_monitoring(update.effective_user.id)
    await update.message.reply_text("🛑 Stopped.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.get_active_users()
    active = next((u for u in users if u['user_id'] == update.effective_user.id), None)
    if active:
        await update.message.reply_text(f"🟢 Monitoring: {active['movie_name']} ({active['city']})")
    else:
        await update.message.reply_text("🔴 Not monitoring.")

# ================= BACKGROUND TASK (FIXED) =================
async def monitor_task(app: Application):
    """
    Background task managed by post_init.
    """
    logger.info("🟢 Monitor Background Task Started")
    while True:
        try:
            users = db.get_active_users()
            if not users:
                await asyncio.sleep(60)
                continue

            for user in users:
                # Random jitter (5-15s) to avoid bot detection patterns
                await asyncio.sleep(random.uniform(5, 15))
                
                try:
                    current_data, error = await browser_manager.fetch_movie_data(user['movie_url'], user['city'])
                    
                    if error:
                        logger.warning(f"⚠️ Fetch failed for {user['user_id']}: {error}")
                        continue

                    last_data = db.get_snapshot(user['user_id'])
                    
                    # Logic: New Theatres vs New Shows
                    new_theatres = [t for t in current_data if t not in last_data]
                    new_shows = {}
                    
                    for t, times in current_data.items():
                        if t in last_data:
                            diff = set(times) - set(last_data[t])
                            if diff: new_shows[t] = list(diff)

                    msg = ""
                    mode = user['notify_mode']
                    
                    if (mode in ['THEATRE', 'BOTH']) and new_theatres:
                        msg += f"🏛️ **New Theatres: {user['movie_name']}**\n" + "\n".join([f"• {t}" for t in new_theatres]) + "\n"
                    
                    if (mode in ['SHOW', 'BOTH']) and new_shows:
                        msg += f"\n⏰ **New Shows: {user['movie_name']}**\n"
                        for t, times in new_shows.items():
                            msg += f"• {t}: {', '.join(times)}\n"

                    if msg:
                        logger.info(f"🚀 Alert sent to {user['user_id']}")
                        await app.bot.send_message(user['chat_id'], msg, parse_mode='Markdown')
                        db.save_snapshot(user['user_id'], current_data)
                    elif current_data != last_data:
                        # Quiet update (e.g. removed shows)
                        db.save_snapshot(user['user_id'], current_data)

                except Exception as e:
                    logger.error(f"Error processing user {user['user_id']}: {e}")

            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"❌ CRITICAL Monitor Loop Error: {e}")
            await asyncio.sleep(60)

# ================= STARTUP HOOK =================
async def post_init(application: Application):
    """
    Correctly starts the background task after bot init.
    """
    asyncio.create_task(monitor_task(application))

# ================= MAIN =================
def main():
    if not BOT_TOKEN:
        print("❌ CRITICAL: BOT_TOKEN not found in Env Vars.")
        sys.exit(1)
        
    db.init_db()

    # 🟢 FIXED: Use post_init to avoid 'No Event Loop' warnings
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler)],
            SELECT_MOVIE: [CallbackQueryHandler(movie_select_handler)],
            SELECT_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_handler)],
            SELECT_MODE: [CallbackQueryHandler(mode_handler)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False # 🟢 FIXED: Silences PTBUserWarning
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(conv)

    print("🚀 Bot deployed on Railway! (Stealth Mode)")
    app.run_polling()

if __name__ == "__main__":
    main()
