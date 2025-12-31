import asyncio
import logging
import sqlite3
import json
import os
import sys
import random
from datetime import datetime

# Third-party imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, error as telegram_error
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from telegram.request import HTTPXRequest
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from fake_useragent import UserAgent

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN") 

# Railway Settings
HEADLESS_MODE = True
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "180")) 
USER_DATA_DIR = "./browser_data" 

# Database Path
if os.path.exists("/app/data"):
    DB_FILE = "/app/data/monitor.db"
else:
    DB_FILE = "monitor.db"

# ================= LOGGING =================
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
logger = logging.getLogger(__name__)

# ================= DATABASE MANAGER =================
class Database:
    def __init__(self, db_file):
        self.db_file = db_file
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

    def update_user(self, user_id, chat_id, **kwargs):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone()
            if not exists:
                cursor.execute("INSERT INTO users (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id))
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

# ================= BROWSER MANAGER (ROBUST SEARCH) =================
class BrowserManager:
    def __init__(self):
        self.ua = UserAgent()

    def get_stealth_args(self):
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage", 
            "--disable-gpu",
            "--disable-extensions",
            "--window-size=1920,1080", # HD Res helps with layout
        ]

    async def search_movie(self, query):
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=HEADLESS_MODE, args=self.get_stealth_args())
                context = await browser.new_context(user_agent=self.ua.random, viewport={"width":1920,"height":1080})
                page = await context.new_page()
                
                logger.info(f"🔎 Searching: {query}")
                # 1. Go to Home
                await page.goto("https://in.bookmyshow.com/explore/home/", timeout=60000)
                
                # 2. Aggressively find search bar
                search_found = False
                potential_selectors = [
                    "input[type='text']", 
                    "span#4", 
                    "div.sc-fFTswy", # Common random class
                    "span:has-text('Search')",
                ]
                
                for selector in potential_selectors:
                    try:
                        if await page.locator(selector).first.is_visible(timeout=2000):
                            await page.locator(selector).first.click(timeout=1000)
                            search_found = True
                            break
                    except: continue

                # Fallback: Just type if we think we are focused, or force click center
                if not search_found:
                    logger.warning("⚠️ Search bar elusive, trying generic input fill...")
                
                # 3. Type and Wait
                await page.locator("input").fill(query)
                
                # 4. Wait longer for AJAX results (Cloud is slow)
                await asyncio.sleep(5) 

                # 5. Extract
                await page.wait_for_selector("a[href*='/movies/']", timeout=15000)
                links = await page.locator("a[href*='/movies/']").all()

                results = []
                for link in links[:5]:
                    url = await link.get_attribute("href")
                    title = await link.inner_text()
                    if title and url:
                        if "bookmyshow.com" not in url:
                            url = "https://in.bookmyshow.com" + url
                        results.append({"title": title.strip(), "url": url})
                
                await browser.close()
                return results
            except Exception as e:
                logger.error(f"Search Error: {e}")
                return []

    async def fetch_movie_data(self, url, city):
        data = {}
        error = None
        
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=HEADLESS_MODE, args=self.get_stealth_args())
                context = await browser.new_context(user_agent=self.ua.random, locale="en-IN")
                page = await context.new_page()
                
                logger.info(f"🌍 Fetching: {url}")
                response = await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                
                if response.status == 403:
                    raise Exception("403 Forbidden")

                # Handle City Selection
                try:
                    if await page.get_by_placeholder("Search for your city").is_visible(timeout=5000):
                        await page.get_by_placeholder("Search for your city").fill(city)
                        await page.get_by_text(city, exact=False).first.click()
                        await asyncio.sleep(3)
                except: pass

                # Check for "No Shows" or "Venues"
                try:
                    # Wait for container to stabilize
                    await asyncio.sleep(3)
                    
                    if await page.get_by_text("No shows available").is_visible():
                        pass # Valid result, just empty
                    else:
                        venue_elements = await page.locator("li.list-group-item").all()
                        for venue in venue_elements:
                            name = await venue.locator("a.body-text").first.inner_text()
                            times = await venue.locator(".showtime-pill .time-text").all_inner_texts()
                            if times:
                                data[name] = sorted([t.strip() for t in times])
                except: pass
                
                await browser.close()
            except Exception as e:
                error = str(e)
                logger.error(f"Fetch Error: {e}")
        
        return data, error

browser_manager = BrowserManager()

# ================= HANDLERS =================
SEARCH, SELECT_MOVIE, SELECT_CITY, SELECT_MODE = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.update_user(update.effective_user.id, update.effective_chat.id)
    await update.message.reply_text("👋 Bot is Online! Use /setup to start.")

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Enter Movie Name:")
    return SEARCH

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔎 Searching... (Waiting 10s for results)")
    results = await browser_manager.search_movie(update.message.text)
    if not results:
        await msg.edit_text("❌ No movies found. Try searching for a simpler term (e.g. 'Jana').")
        return SEARCH
    
    keyboard = [[InlineKeyboardButton(r['title'], callback_data=f"m_{i}")] for i, r in enumerate(results)]
    context.user_data["results"] = results
    await msg.edit_text("Select Movie:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    context.user_data["movie"] = context.user_data["results"][idx]
    await query.edit_message_text(f"Selected: {context.user_data['movie']['title']}\n\n📍 Enter City (e.g., Chennai):")
    return SELECT_CITY

async def city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = update.message.text
    btns = [[InlineKeyboardButton("Theatres", callback_data="THEATRE"), InlineKeyboardButton("Shows", callback_data="SHOW"), InlineKeyboardButton("Both", callback_data="BOTH")]]
    await update.message.reply_text("🔔 Notify on:", reply_markup=InlineKeyboardMarkup(btns))
    return SELECT_MODE

async def mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = context.user_data
    db.update_user(user.id, query.message.chat_id, movie_name=data["movie"]["title"], movie_url=data["movie"]["url"], city=data["city"], notify_mode=query.data)
    db.save_snapshot(user.id, {})
    await query.edit_message_text("✅ Setup Complete! I will check every 3 minutes.")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.get_active_users()
    active = next((u for u in users if u['user_id'] == update.effective_user.id), None)
    await update.message.reply_text(f"🟢 Monitoring: {active['movie_name']}" if active else "🔴 Not monitoring.")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.stop_monitoring(update.effective_user.id)
    await update.message.reply_text("🛑 Stopped.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# ================= BACKGROUND TASK =================
async def monitor_task(app: Application):
    logger.info("🟢 Background Task Started")
    while True:
        try:
            users = db.get_active_users()
            if users:
                for user in users:
                    await asyncio.sleep(10) 
                    logger.info(f"Checking {user['movie_name']} for {user['user_id']}")
                    curr, err = await browser_manager.fetch_movie_data(user['movie_url'], user['city'])
                    
                    if not err:
                        last = db.get_snapshot(user['user_id'])
                        new_theatres = [t for t in curr if t not in last]
                        
                        msg = ""
                        if user['notify_mode'] in ['THEATRE', 'BOTH'] and new_theatres:
                            msg = f"🚨 **New Theatres:**\n" + "\n".join(new_theatres)
                        
                        if msg:
                            await app.bot.send_message(user['chat_id'], msg)
                            db.save_snapshot(user['user_id'], curr)
                        elif curr != last:
                            db.save_snapshot(user['user_id'], curr)

            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logger.error(f"Monitor Crash: {e}")
            await asyncio.sleep(60)

async def post_init(app: Application):
    asyncio.create_task(monitor_task(app))

# ================= MAIN =================
def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN missing")
        sys.exit(1)
    
    db.init_db()
    
    # 60s timeout for stability
    request = HTTPXRequest(connect_timeout=60, read_timeout=60)
    app = Application.builder().token(BOT_TOKEN).request(request).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler)],
            SELECT_MOVIE: [CallbackQueryHandler(movie_select_handler)],
            SELECT_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_handler)],
            SELECT_MODE: [CallbackQueryHandler(mode_handler)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(conv)

    print("🚀 Bot Started (Conflict Handled + Robust Search)")
    
    # Conflict Handling: If the old bot is still dying, we catch the error and retry
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except telegram_error.Conflict:
        logger.warning("⚠️ Conflict detected (Old bot still running). Retrying in 10 seconds...")
        import time
        time.sleep(10)
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
