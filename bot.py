import os
import time
import json
import hashlib
import requests

# ================= SAFE ENV READ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

print("🔍 ENV CHECK")
print("BOT_TOKEN:", "SET" if BOT_TOKEN else "MISSING")
print("MOVIE_URL:", MOVIE_URL or "MISSING")
print("CHECK_INTERVAL:", CHECK_INTERVAL)

if not BOT_TOKEN or not MOVIE_URL:
    print("⚠️ Env vars missing. Bot will NOT crash. Waiting...")
    while True:
        time.sleep(60)

# ================= CONFIG =================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
STATE_FILE = "state.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}
CHAT_ID = None  # will auto-detect first chat
# ==========================================


def fetch_html():
    r = requests.get(MOVIE_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("fp")


def save_state(fp):
    with open(STATE_FILE, "w") as f:
        json.dump({"fp": fp}, f)


def send_message(text):
    global CHAT_ID

    # Auto-discover chat id
    if CHAT_ID is None:
        updates = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            timeout=10
        ).json()

        if not updates.get("result"):
            print("⏳ Waiting for /start message in Telegram…")
            return

        CHAT_ID = updates["result"][-1]["message"]["chat"]["id"]
        print("✅ Chat ID detected:", CHAT_ID)

    requests.post(
        TELEGRAM_API,
        data={"chat_id": CHAT_ID, "text": text},
        timeout=10
    )


def monitor():
    print("🟢 Jana Nayagan monitor started")
    prev_fp = load_state()

    while True:
        try:
            html = fetch_html()
            cur_fp = fingerprint(html)

            if prev_fp and cur_fp != prev_fp:
                send_message(
                    "🚨 JANA NAYAGAN UPDATE DETECTED\n\n"
                    "🎭 New theatre or show added!\n"
                    "🎟️ Book now on BookMyShow"
                )
                print("✅ Change detected → Notification sent")

            save_state(cur_fp)
            prev_fp = cur_fp

        except Exception as e:
            print("❌ Monitor error:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    monitor()
