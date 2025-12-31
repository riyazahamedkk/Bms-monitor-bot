import asyncio
import logging
import sqlite3
import json
import os
import sys
import random
import subprocess
import warnings
from datetime import datetime

# ================= SELF-REPAIR: INSTALL BROWSER =================
# This forces Railway to install the browser if it's missing
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("📦 Installing Playwright...")
    subprocess.run([sys.executable, "-m", "pip", "install", "playwright"])
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])

# Check if browser binary exists, if not, install it
if not os.path.exists("/root/.cache/ms-playwright"):
    print("🛠️ Browser binary missing. Installing Chromium now... (This takes 1 min)")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
    subprocess.run([sys.executable, "-m", "playwright", "install-deps"])

# ================= IMPORTS =================
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
from fake_useragent import UserAgent

# Silence Warnings
from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN") 
HEADLESS_MODE = True
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "180")) 

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

# ================= DATABASE =================
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

# ================= BROWSER MANAGER =================
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
            "--window-size=1920,1080",
        ]

    async def search_movie(self, query):
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=HEADLESS_MODE, args=self.get_stealth_args())
                context = await browser.new_context(user_agent=self.ua.random, viewport={"width":1920,"height":1080})
                page = await context.new_page()
                
                logger.info(f"🔎 Searching: {query}")
                await page.goto("https://in.bookmyshow.com/explore/home/", timeout=60000)
                
                # Robust Search Clicker
                try:
                    search_box = page.locator("span:has-text('Search'), input[type='text'], #4").first
                    if await search_box.is_visible():
                        await search_box.click()
                except: pass
                
                await page.locator("input").fill(query)
                await asyncio.sleep(4) # Wait for AJAX

                await page.wait_for_selector("a[href*='/movies/']", timeout=15000)
                links = await page.locator("a[href*='/movies/']").all()

                results = []
                seen_urls = set()
                for link in links[:6]:
                    url = await link.get_attribute("href")
                    title = await link.inner_text()
                    if title and url and url not in seen_urls:
                        if "bookmyshow.com" not in url:
                            url = "https://in.bookmyshow.com" + url
                        results.append({"title": title.strip(), "url": url})
                        seen_urls.add(url)
                
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
                
                logger.info(f"🌍 Fetching: {url} | City: {city}")
                
                # 1. Navigate to Generic Movie URL
                response = await page.goto(url, timeout=60000, wait_until="domcontentloaded")
                if response.status == 403: raise Exception("403 Forbidden")

                # 2. Handle City Selection Popup / Change Region
                try:
                    # If popup exists, type city and click
                    city_input = page.get_by_placeholder("Search for your city")
                    if await city_input.is_visible(timeout=5000):
                        await city_input.fill(city)
                        await asyncio.sleep(1)
                        await page.get_by_text(city, exact=False).first.click()
                        await asyncio.sleep(5) # Wait for page reload/redirect
                except: pass

                # 3. If no shows, check if we need to click "Book Tickets" to see venue list
                try:
                    book_btn = page.get_by_role("button", name="Book tickets")
                    if await book_btn.is_visible(timeout=3000):
                        await book_btn.click()
                        await asyncio.sleep(3)
                        # Handle Format selection (2D/3D/IMAX) if it pops up
                        if await page.locator("ul#filterFormat").is_visible():
                            await page.locator("li").first.click()
                except: pass

                # 4. Scrape Data
                await asyncio.sleep(3)
                if not await page.get_by_text("No shows available").is_visible():
                    venue_elements = await page.locator("li.list-group-item").all()
                    for venue in venue_elements:
                        name = await venue.locator("a.body-text").first.inner_text()
                        times = await venue.locator(".showtime-pill .time-text").all_inner_texts()
                        if times:
                            data[name] = sorted([t.strip() for t in times])
                
                await browser.close()
            except Exception as e:
                error = str(e)
                logger.error(f"Fetch Error: {e}")
        
        return data, error

browser_manager = BrowserManager()

# ================= CONVERSATION HANDLERS =================
SEARCH, SELECT_MOVIE, SELECT_CITY, SELECT_MODE, SEARCH_MANUAL = range(5)

# 🟢 CUSTOM LIST: Add trending movies here to show as buttons
TRENDING_MOVIES = ["Jana Nayagan", "Viduthalai Part 2", "Interstellar", "Mufasa"]
MAJOR_CITIES = ["Bengaluru", "Chennai", "Mumbai", "Hyderabad", "Kochi", "Delhi"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.update_user(update.effective_user.id, update.effective_chat.id)
    await update.message.reply_text("👋 Bot Online!\n\nUse /setup to start monitoring.")

async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🟢 NEW: Show Trending Buttons + Search Option
    keyboard = [[InlineKeyboardButton(m, callback_data=f"trend_{m}")] for m in TRENDING_MOVIES]
    keyboard.append([InlineKeyboardButton("🔍 Search Another Movie", callback_data="search_manual")])
    
    await update.message.reply_text(
        "🎬 **Select a Movie** or search manually:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SEARCH

async def search_decision_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "search_manual":
        await query.edit_message_text("🔍 **Type the movie name:**")
        return SEARCH_MANUAL
    
    # User clicked a Trending Movie Button
    movie_name = data.split("_")[1]
    await query.edit_message_text(f"🔎 Auto-detecting link for: **{movie_name}**...")
    
    # Auto-search the link
    results = await browser_manager.search_movie(movie_name)
    if not results:
        await query.message.reply_text("❌ Could not auto-detect link. Please type the name manually:")
        return SEARCH_MANUAL

    # Auto-select the first result for trending movies
    context.user_data["movie"] = results[0]
    
    # Move to City Selection
    return await send_city_selection(update, context)

async def manual_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    msg = await update.message.reply_text("🔎 Searching BookMyShow...")
    
    results = await browser_manager.search_movie(query_text)
    if not results:
        await msg.edit_text("❌ No movies found. Try exact name:")
        return SEARCH_MANUAL
    
    context.user_data["results"] = results
    keyboard = [[InlineKeyboardButton(r['title'], callback_data=f"mov_{i}")] for i, r in enumerate(results)]
    await msg.edit_text("✅ **Select Correct Movie:**", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    context.user_data["movie"] = context.user_data["results"][idx]
    return await send_city_selection(update, context)

async def send_city_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🟢 NEW: Show City Buttons
    keyboard = []
    # Create 2 columns of cities
    row = []
    for city in MAJOR_CITIES:
        row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("✍️ Type Other City", callback_data="city_manual")])
    
    text = f"✅ Selected: **{context.user_data['movie']['title']}**\n\n📍 **Select City:**"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    
    return SELECT_CITY

async def city_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "city_manual":
        await query.edit_message_text("✍️ **Please type your city name:**")
        return SELECT_CITY # Wait for text input
    
    # User clicked a City Button
    city = data.split("_")[1]
    context.user_data["city"] = city
    return await send_notify_mode(update, context)

async def city_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = update.message.text.strip()
    return await send_notify_mode(update, context)

async def send_notify_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [[InlineKeyboardButton("Theatres", callback_data="THEATRE"), InlineKeyboardButton("Shows", callback_data="SHOW"), InlineKeyboardButton("Both", callback_data="BOTH")]]
    
    text = f"📍 City: **{context.user_data['city']}**\n\n🔔 **Notify me on:**"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="Markdown")
    
    return SELECT_MODE

async def mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = context.user_data
    
    db.update_user(
        user.id, query.message.chat_id, 
        movie_name=data["movie"]["title"], 
        movie_url=data["movie"]["url"], 
        city=data["city"], 
        notify_mode=query.data
    )
    db.save_snapshot(user.id, {})
    
    await query.edit_message_text(
        f"✅ **Setup Complete!**\n\n"
        f"🎥 {data['movie']['title']}\n"
        f"📍 {data['city']}\n"
        f"🔗 Auto-Link: {data['movie']['url']}\n\n"
        "I will now monitor this movie in this city automatically."
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.get_active_users()
    active = next((u for u in users if u['user_id'] == update.effective_user.id), None)
    await update.message.reply_text(f"🟢 Monitoring: {active['movie_name']} in {active['city']}" if active else "🔴 Not monitoring.")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.stop_monitoring(update.effective_user.id)
    await update.message.reply_text("🛑 Stopped.")

# ================= BACKGROUND TASK =================
async def monitor_task(app: Application):
    logger.info("🟢 Background Task Started")
    while True:
        try:
            users = db.get_active_users()
            if users:
                for user in users:
                    await asyncio.sleep(10) 
                    curr, err = await browser_manager.fetch_movie_data(user['movie_url'], user['city'])
                    
                    if not err:
                        last = db.get_snapshot(user['user_id'])
                        new_theatres = [t for t in curr if t not in last]
                        
                        msg = ""
                        if user['notify_mode'] in ['THEATRE', 'BOTH'] and new_theatres:
                            msg = f"🚨 **New Theatres in {user['city']}:**\n" + "\n".join(new_theatres)
                        
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
    
    request = HTTPXRequest(connect_timeout=60, read_timeout=60)
    app = Application.builder().token(BOT_TOKEN).request(request).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            SEARCH: [CallbackQueryHandler(search_decision_handler)],
            SEARCH_MANUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_search_handler)],
            SELECT_MOVIE: [CallbackQueryHandler(movie_selection_handler)],
            SELECT_CITY: [
                CallbackQueryHandler(city_button_handler, pattern="^city_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, city_text_handler)
            ],
            SELECT_MODE: [CallbackQueryHandler(mode_handler)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(conv)

    print("🚀 Bot Started (Smart Setup + Auto Repair)")
    
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except telegram_error.Conflict:
        logger.warning("⚠️ Conflict detected. Retrying...")
        import time
        time.sleep(10)
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
