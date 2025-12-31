import os
import time
import json
import hashlib
from urllib.parse import urlparse
from curl_cffi import requests # 🚨 IMPORT FROM CURL_CFFI NOW

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("MOVIE_URL:", MOVIE_URL)

if not BOT_TOKEN or not MOVIE_URL:
    print("❌ Missing env vars.")
    while True:
        time.sleep(60)

# ================= PARSE URL =================
try:
    path_parts = urlparse(MOVIE_URL).path.strip("/").split("/")
    CITY = path_parts[1]
    MOVIE_SLUG = path_parts[2]
    MOVIE_CODE = path_parts[4]
    print(f"🎯 Parsed CITY: {CITY}")
    print(f"🎬 Parsed CODE: {MOVIE_CODE}")

except Exception as e:
    print(f"❌ Error parsing URL: {e}")
    raise SystemExit("Check MOVIE_URL format.")

# ================= CONFIG =================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"
CHAT_ID = None

API_URL = f"https://in.bookmyshow.com/api/explore/v1/movies/{MOVIE_CODE}/showtimes?region={CITY}&bmsId={MOVIE_CODE}"

# 🚨 HEADERS: Minimal headers needed when using curl_cffi
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://in.bookmyshow.com/movies/{CITY}/{MOVIE_SLUG}/buytickets/{MOVIE_CODE}/",
}

# ================= FUNCTIONS =================

def get_chat_id():
    global CHAT_ID
    # Standard requests is fine for Telegram
    import requests as std_requests
    try:
        r = std_requests.get(f"{TELEGRAM_API}/getUpdates", timeout=10).json()
        if r.get("result"):
            CHAT_ID = r["result"][-1]["message"]["chat"]["id"]
            print("✅ Chat ID detected:", CHAT_ID)
    except Exception as e:
        print(f"⚠️ Could not detect Chat ID: {e}")

def send_message(text):
    import requests as std_requests
    if CHAT_ID is None:
        get_chat_id()
        if CHAT_ID is None:
            print("⏳ Send /start to the bot in Telegram")
            return

    try:
        std_requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"❌ Failed to send message: {e}")

def fetch_show_data():
    try:
        # 🚨 THE MAGIC FIX: impersonate="chrome"
        r = requests.get(
            API_URL, 
            headers=HEADERS, 
            impersonate="chrome110", 
            timeout=15
        )
        
        if r.status_code == 403:
            print("⚠️ Still 403. IP might be blacklisted.")
            return None
            
        return r.json()
    except Exception as e:
        print(f"⚠️ Fetch Error: {e}")
        return None

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
    print("🟢 Monitor Started (curl_cffi engine)")
    prev_fp = load_state()

    while True:
        data = fetch_show_data()
        
        if data:
            cur_fp = fingerprint(data)
            
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
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
