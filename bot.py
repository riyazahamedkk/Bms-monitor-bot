import os
import time
import json
import hashlib
import requests  # Use standard requests, ScraperAPI handles the heavy lifting
from bs4 import BeautifulSoup

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
# Add your ScraperAPI Key here
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY") 
# Increase interval to save API credits (e.g., 300s = 5 mins)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300")) 

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("SCRAPER_API:", "SET" if SCRAPER_API_KEY else "MISSING")

if not BOT_TOKEN or not MOVIE_URL or not SCRAPER_API_KEY:
    print("❌ Missing env vars (Need BOT_TOKEN, MOVIE_URL, and SCRAPER_API_KEY).")
    while True:
        time.sleep(60)

# ================= CONFIG =================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"
CHAT_ID = None

# ================= FUNCTIONS =================

def get_chat_id():
    global CHAT_ID
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", timeout=10).json()
        if r.get("result"):
            CHAT_ID = r["result"][-1]["message"]["chat"]["id"]
            print("✅ Chat ID detected:", CHAT_ID)
    except Exception as e:
        print(f"⚠️ Could not detect Chat ID: {e}")

def send_message(text):
    if CHAT_ID is None:
        get_chat_id()
        if CHAT_ID is None:
            return

    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"❌ Failed to send message: {e}")

def fetch_via_proxy():
    """
    Routes the request through ScraperAPI to bypass the 403 block.
    """
    payload = {
        'api_key': SCRAPER_API_KEY,
        'url': MOVIE_URL,
        'keep_headers': 'true', # Pass our headers through
        'country_code': 'in',   # Route through India (Important for BMS)
    }
    
    try:
        r = requests.get('http://api.scraperapi.com', params=payload, timeout=60)
        
        if r.status_code == 200:
            return r.text
        elif r.status_code == 403:
            print("⚠️ ScraperAPI was also blocked (Rare).")
        else:
            print(f"⚠️ ScraperAPI Error: {r.status_code}")
            
        return None
    except Exception as e:
        print(f"⚠️ Proxy Error: {e}")
        return None

def extract_shows(html):
    if not html: return set()
    soup = BeautifulSoup(html, "html.parser")
    shows = set()

    # Strategy: Look for theatre names
    for venue in soup.select('a.__venue-name'):
        shows.add(venue.get_text(strip=True))
        
    # Strategy: Look for JSON data (React/NextJS)
    if not shows:
        script = soup.find("script", id="__NEXT_DATA__")
        if script:
            shows.add("JSON_DATA_FOUND")
            shows.add(hashlib.md5(script.string.encode()).hexdigest())

    return shows

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try: return set(json.load(f).get("shows", []))
            except: return set()
    return set()

def save_state(shows):
    with open(STATE_FILE, "w") as f:
        json.dump({"shows": list(shows)}, f)

def monitor():
    print("🟢 Monitor Started (Proxy Mode)")
    last_shows = load_state()

    while True:
        html = fetch_via_proxy()
        
        if html:
            current_shows = extract_shows(html)
            
            if current_shows:
                diff = current_shows - last_shows
                if diff and last_shows:
                    msg = f"🚨 *TICKETS AVAILABLE*\n\nUpdates detected on BMS!\n🔗 {MOVIE_URL}"
                    send_message(msg)
                    print("✅ Notification sent!")
                else:
                    print(f"💤 No changes. (Data points: {len(current_shows)})")

                save_state(current_shows)
                last_shows = current_shows
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
