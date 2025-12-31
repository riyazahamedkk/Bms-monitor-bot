import os
import time
import json
import requests
import hashlib
from telegram import Bot

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_URL = os.getenv("MOVIE_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

if not BOT_TOKEN or not MOVIE_URL:
    raise RuntimeError("Missing BOT_TOKEN or MOVIE_URL")

bot = Bot(token=BOT_TOKEN)

STATE_FILE = "state.json"
# =========================================


def send_message(text: str):
    try:
        bot.send_message(chat_id="@JanaNayaganAlert", text=text)
    except Exception as e:
        print("Telegram error:", e)


def fetch_page():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    r = requests.get(MOVIE_URL, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def fingerprint(html: str):
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def load_previous():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("fingerprint")


def save_current(fp):
    with open(STATE_FILE, "w") as f:
        json.dump({"fingerprint": fp}, f)


def monitor_loop():
    print("🟢 Telegram BookMyShow monitor started")

    previous_fp = load_previous()

    while True:
        try:
            html = fetch_page()
            current_fp = fingerprint(html)

            if previous_fp and current_fp != previous_fp:
                send_message(
                    "🚨 **JANA NAYAGAN UPDATE DETECTED**\n\n"
                    "🎭 New theatre or showtime added!\n"
                    "🎟️ Book immediately on BookMyShow!"
                )
                print("✅ Change detected → Notification sent")

            previous_fp = current_fp
            save_current(current_fp)

        except Exception as e:
            print("Monitor error:", e)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    monitor_loop()
