import os
import time
import json
import hashlib
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests # Used for Direct Mode

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")

# 🚨 SAFE LOADING for CHECK_INTERVAL (Prevents crashes)
try:
    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))
except ValueError:
    print("⚠️ Invalid CHECK_INTERVAL format. Using default 300s.")
    CHECK_INTERVAL = 300

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("MOVIE_URL:", MOVIE_URL)
print(f"MODE: {'PROXY (ScraperAPI)' if SCRAPER_API_KEY else 'DIRECT (Safari Impersonation)'}")

if not BOT_TOKEN or not MOVIE_URL:
    print("❌ Critical Missing: BOT_TOKEN or MOVIE_URL.")
    while True: time.sleep(60)

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

def fetch_data():
    """
    Decides whether to use the Proxy or Direct Mode
    """
    # OPTION A: PROXY MODE (Best for Cloud)
    if SCRAPER_API_KEY:
        try:
            payload = {
                'api_key': SCRAPER_API_KEY,
                'url': MOVIE_URL,
                'keep_headers': 'true',
                'country_code': 'in',
            }
            r = requests.get('http://api.scraperapi.com', params=payload, timeout=60)
            if r.status_code == 200: return r.text
            print(f"⚠️ Proxy Status: {r.status_code}")
        except Exception as e:
            print(f"⚠️ Proxy Error: {e}")

    # OPTION B: DIRECT MODE (Best for Local PC)
    else:
        try:
            r = curl_requests.get(
                MOVIE_URL,
                impersonate="safari15_5", # Mimic Apple Safari
                timeout=20
            )
            if r.status_code == 200: return r.text
            if r.status_code == 403: print("⚠️ Direct Mode 403 (Blocked).")
        except Exception as e:
            print(f"⚠️ Direct Fetch Error: {e}")
            
    return None

def extract_hash(html):
    """
    Extracts a unique fingerprint of the movie data.
    """
    if not html: return None
    soup = BeautifulSoup(html, "html.parser")
    
    data_points = []

    # 1. Theatre Names (Visible Links)
    for venue in soup.select('a.__venue-name'):
        data_points.append(venue.get_text(strip=True))

    # 2. Hidden JSON Data (React/NextJS) - This changes when tickets are added
    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        # We hash the hidden data block because parsing it is complex
        data_points.append(hashlib.md5(script.string.encode()).hexdigest())

    if not data_points:
        return None

    # Create a single hash of everything found
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
    print("🟢 Monitor Started")
    last_hash = load_state()

    while True:
        html = fetch_data()
        
        if html:
            current_hash = extract_hash(html)
            
            if current_hash:
                # Compare Hashes
                if last_hash and current_hash != last_hash:
                    msg = (
                        "🚨 *JANA NAYAGAN UPDATE*\n\n"
                        "Changes detected on BookMyShow!\n"
                        "Possible new shows or unlocked rows.\n\n"
                        f"🔗 {MOVIE_URL}"
                    )
                    send_message(msg)
                    print("✅ Change detected → Alert sent!")
                
                elif not last_hash:
                    print(f"ℹ️ First run. Baseline set. (Hash: {current_hash[:10]}...)")
                else:
                    print("💤 No changes.")

                save_state(current_hash)
                last_hash = current_hash
            else:
                print("⚠️ Page loaded, but no movie data found (Sold out or Blocked).")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
