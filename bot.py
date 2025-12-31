import os
import time
import json
import hashlib
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")
CITY = os.getenv("CITY", "bengaluru")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("MOVIE_CODE:", MOVIE_CODE)
print("CITY:", CITY)
print("CHECK_INTERVAL:", CHECK_INTERVAL)

if not BOT_TOKEN or not MOVIE_CODE:
    print("❌ Missing env vars. Waiting...")
    while True:
        time.sleep(60)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"
CHAT_ID = None

API_URL = (
    f"https://in.bookmyshow.com/api/explore/v1/movies/"
    f"{MOVIE_CODE}/showtimes?region={CITY}"
)

HEADERS = {
    "User-Agent": "BookMyShow-App",
    "Accept": "application/json"
}


def get_chat_id():
    global CHAT_ID
    r = requests.get(f"{TELEGRAM_API}/getUpdates", timeout=10).json()
    if r.get("result"):
        CHAT_ID = r["result"][-1]["message"]["chat"]["id"]
        print("✅ Chat ID detected:", CHAT_ID)


def send_message(text):
    if CHAT_ID is None:
        get_chat_id()
        if CHAT_ID is None:
            print("⏳ Waiting for /start in Telegram…")
            return

    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=10
    )


def fetch_show_data():
    r = requests.get(API_URL, headers=HEADERS, timeout=15)
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
    print("🟢 Jana Nayagan API monitor started")
    prev_fp = load_state()

    while True:
        try:
            data = fetch_show_data()
            cur_fp = fingerprint(data)

            if prev_fp and cur_fp != prev_fp:
                send_message(
                    "🚨 JANA NAYAGAN UPDATE!\n\n"
                    "🎭 New theatre or showtime detected.\n"
                    "🎟️ Check BookMyShow now!"
                )
                print("✅ Change detected → Notification sent")

            save_state(cur_fp)
            prev_fp = cur_fp

        except Exception as e:
            print("❌ Monitor error:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    monitor()
