import os
import time
import json
import hashlib
import requests
from urllib.parse import urlparse

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
# Increased default check interval to 60s to avoid rate limits
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("MOVIE_URL:", MOVIE_URL)

if not BOT_TOKEN or not MOVIE_URL:
    print("❌ Missing env vars. Waiting…")
    while True:
        time.sleep(60)

# ================= PARSE MOVIE_URL (FIXED) =================
# URL Structure: 
# https://in.bookmyshow.com/movies/bengaluru/jana-nayagan/buytickets/ET00430817/20260109
try:
    path_parts = urlparse(MOVIE_URL).path.strip("/").split("/")
    
    # Correct Indices based on standard BMS URL
    CITY = path_parts[1]       # 'bengaluru'
    MOVIE_SLUG = path_parts[2] # 'jana-nayagan'
    MOVIE_CODE = path_parts[4] # 'ET00430817'
    DATE_CODE = path_parts[5] if len(path_parts) > 5 else None

    print(f"🎯 Parsed CITY: {CITY}")
    print(f"🎬 Parsed CODE: {MOVIE_CODE}")

except Exception as e:
    print(f"❌ Error parsing URL: {e}")
    # Stop execution if URL is wrong
    raise SystemExit("Check your MOVIE_URL format.")

# ================= CONFIG =================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"
CHAT_ID = None

# Using the standard API endpoint
API_URL = f"https://in.bookmyshow.com/api/explore/v1/movies/{MOVIE_CODE}/showtimes?region={CITY}&bmsId={MOVIE_CODE}"

# 🚨 IMPORTANT: Pretend to be a Desktop Browser (Chrome), NOT the App
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": f"https://in.bookmyshow.com/movies/{CITY}/{MOVIE_SLUG}/buytickets/{MOVIE_CODE}/",
    "Accept-Language": "en-US,en;q=0.9"
}

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
            print("⏳ Send /start to the bot in Telegram")
            return

    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"❌ Failed to send message: {e}")

def fetch_show_data():
    # Use a session to look more like a real user
    with requests.Session() as s:
        s.headers.update(HEADERS)
        r = s.get(API_URL, timeout=15)
        
        # If still 403, warn the user explicitly
        if r.status_code == 403:
            print("⚠️ 403 Forbidden. The server is blocking the request.")
            return None
            
        r.raise_for_status()
        return r.json()

def fingerprint(data):
    raw = json.dumps(data, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f).get("fp")
    return None

def save_state(fp):
    with open(STATE_FILE, "w") as f:
        json.dump({"fp": fp}, f)

def monitor():
    print("🟢 Monitor Started")
    prev_fp = load_state()

    while True:
        try:
            data = fetch_show_data()
            
            if data:
                cur_fp = fingerprint(data)
                
                # Compare fingerprints
                if prev_fp and cur_fp != prev_fp:
                    msg = (
                        f"🚨 *UPDATE DETECTED for {CITY.upper()}*\n\n"
                        f"Check BookMyShow now!\n"
                        f"{MOVIE_URL}"
                    )
                    send_message(msg)
                    print("✅ Change detected → Notification sent")
                elif not prev_fp:
                    print("ℹ️ First run. Saving baseline.")
                else:
                    print(f"💤 No changes. (Code: {MOVIE_CODE})")

                save_state(cur_fp)
                prev_fp = cur_fp
            
        except Exception as e:
            print("❌ Monitor error:", e)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
