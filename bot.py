import os
import time
import json
import hashlib
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")

try:
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
except ValueError:
    CHECK_INTERVAL = 300

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("MOVIE_URL:", MOVIE_URL)
print(f"MODE: {'PROXY (ScraperAPI)' if SCRAPER_API_KEY else 'DIRECT'}")

if not BOT_TOKEN or not MOVIE_URL:
    print("❌ Critical Missing: BOT_TOKEN or MOVIE_URL.")
    while True: time.sleep(60)

# ================= CONFIG =================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"
CHAT_ID = None

def force_start_connection():
    """
    Loops until it finds a user's /start message and replies to it.
    """
    global CHAT_ID
    print("⏳ Waiting for user to send /start in Telegram...")
    
    while CHAT_ID is None:
        try:
            r = requests.get(f"{TELEGRAM_API}/getUpdates", timeout=10).json()
            results = r.get("result", [])
            
            if results:
                # Get the last message sent to the bot
                last_msg = results[-1].get("message", {})
                chat_id = last_msg.get("chat", {}).get("id")
                
                if chat_id:
                    CHAT_ID = chat_id
                    print(f"✅ Found User! Chat ID: {CHAT_ID}")
                    
                    # Send a welcome message to confirm it's working
                    send_message(
                        "🟢 **Jana Nayagan Monitor is ONLINE**\n\n"
                        "I am now checking BookMyShow every 5 minutes.\n"
                        "I will alert you when tickets open!"
                    )
                    return
        except Exception as e:
            print(f"⚠️ Connection Error: {e}")
        
        time.sleep(5)

def send_message(text):
    if CHAT_ID is None:
        return
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"❌ Failed to send message: {e}")

def fetch_data():
    if SCRAPER_API_KEY:
        try:
            payload = {
                'api_key': SCRAPER_API_KEY,
                'url': MOVIE_URL,
                'country_code': 'in',
                'premium': 'true',
                'render': 'true', 
            }
            # Extended timeout for render mode
            r = requests.get('http://api.scraperapi.com', params=payload, timeout=80)
            if r.status_code == 200: return r.text
            print(f"⚠️ Proxy Status: {r.status_code}")
        except Exception as e:
            print(f"⚠️ Proxy Error: {e}")
    else:
        try:
            r = curl_requests.get(MOVIE_URL, impersonate="safari15_5", timeout=20)
            if r.status_code == 200: return r.text
            print(f"⚠️ Direct Status: {r.status_code}")
        except Exception as e:
            print(f"⚠️ Direct Fetch Error: {e}")
            
    return None

def extract_hash(html):
    if not html: return None
    soup = BeautifulSoup(html, "html.parser")
    
    page_title = soup.title.string.strip() if soup.title else "No Title"
    print(f"📄 DEBUG: Title is '{page_title}'")

    if "Just a moment" in page_title or "Access Denied" in page_title:
        print("⚠️ BLOCKED: Cloudflare.")
        return None

    data_points = []
    venues = soup.select('a.__venue-name')
    for venue in venues:
        data_points.append(venue.get_text(strip=True))

    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        data_points.append(hashlib.md5(script.string.encode()).hexdigest())

    # Logic: 0 venues + Correct Title = Coming Soon (Valid State)
    if not data_points:
        if "Jana Nayagan" in page_title:
             print("ℹ️ Page Valid. Status: NO SHOWS YET.")
             return hashlib.sha256(b"NO_SHOWS_YET").hexdigest()
        else:
             return None

    print(f"📊 Success! Found {len(venues)} theatres.")
    raw = json.dumps(sorted(data_points))
    return hashlib.sha256(raw.encode()).hexdigest()

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f).get("hash")
    return None

def save_state(h):
    with open(STATE_FILE, "w") as f:
        json.dump({"hash": h}, f)

def monitor():
    # 1. Establish Connection FIRST
    force_start_connection()

    # 2. Start Monitoring
    print("🟢 Monitor Loop Started")
    last_hash = load_state()

    while True:
        html = fetch_data()
        
        if html:
            current_hash = extract_hash(html)
            
            if current_hash:
                if last_hash and current_hash != last_hash:
                    msg = f"🚨 *UPDATE DETECTED*\n\nStatus Changed on BookMyShow!\n🔗 {MOVIE_URL}"
                    send_message(msg)
                    print("✅ Change detected! Message sent.")
                elif not last_hash:
                    print("ℹ️ Baseline set.")
                else:
                    print("💤 No changes.")
                save_state(current_hash)
                last_hash = current_hash
            else:
                print("⚠️ HTML loaded, but logic skipped update (Check DEBUG Title).")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
