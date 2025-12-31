import requests
import time
import os
import hashlib
import threading
from collections import defaultdict

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MOVIE_CODE = os.getenv("MOVIE_CODE")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "12"))

if not BOT_TOKEN or not MOVIE_CODE:
    raise RuntimeError("Missing BOT_TOKEN or MOVIE_CODE")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ================= STORAGE =================
user_state = {}              # chat_id -> state
user_cities = defaultdict(set)  # chat_id -> set(cities)
page_fingerprint = defaultdict(dict)  # chat_id -> city -> hash

# ================= HELPERS =================
def send(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)

def get_states_and_cities():
    """
    Fetches all states and cities from BookMyShow
    """
    url = "https://in.bookmyshow.com/api/explore/v1/discover/regions"
    data = requests.get(url, timeout=10).json()

    states = defaultdict(list)
    for r in data.get("regions", []):
        state = r.get("state")
        city = r.get("slug")
        if state and city:
            states[state].append(city)

    return states

def fingerprint(html):
    lines = [l for l in html.splitlines() if "/buytickets/" in l]
    return hashlib.md5("".join(lines).encode()).hexdigest()

# ================= BOT LOOP =================
def bot_loop():
    offset = None
    states = get_states_and_cities()

    while True:
        updates = requests.get(
            f"{TG_API}/getUpdates",
            params={"timeout": 20, "offset": offset},
            timeout=25
        ).json()

        for upd in updates.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message")
            if not msg:
                continue

            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")

            if text == "/start":
                kb = {
                    "keyboard": [[s] for s in sorted(states.keys())],
                    "resize_keyboard": True,
                    "one_time_keyboard": True
                }
                send(chat_id, "📍 Select your STATE:", kb)

            elif text in states:
                user_state[chat_id] = text
                cities = states[text]
                kb = {
                    "keyboard": [[c] for c in cities],
                    "resize_keyboard": True,
                    "one_time_keyboard": True
                }
                send(chat_id, f"🏙 Select CITY in {text}:", kb)

            elif chat_id in user_state and text in states[user_state[chat_id]]:
                user_cities[chat_id].add(text)
                send(
                    chat_id,
                    f"✅ Monitoring started\n\n"
                    f"State: {user_state[chat_id]}\n"
                    f"City: {text}\n\n"
                    f"You’ll get alerts when new shows are added 🎬"
                )

        time.sleep(1)

# ================= MONITOR LOOP =================
def monitor_loop():
    while True:
        for chat_id, cities in user_cities.items():
            for city in cities:
                try:
                    url = f"https://in.bookmyshow.com/movies/{city}/jana-nayagan/buytickets/{MOVIE_CODE}"
                    r = requests.get(url, timeout=10)
                    if r.status_code != 200:
                        continue

                    fp = fingerprint(r.text)

                    if city not in page_fingerprint[chat_id]:
                        page_fingerprint[chat_id][city] = fp
                        continue

                    if fp != page_fingerprint[chat_id][city]:
                        send(
                            chat_id,
                            f"🚨 NEW SHOW / THEATRE ADDED!\n\n"
                            f"🏙 City: {city.capitalize()}\n"
                            f"🎬 Jana Nayagan\n\n"
                            f"🔗 {url}"
                        )
                        page_fingerprint[chat_id][city] = fp

                except Exception as e:
                    print("Monitor error:", e)

        time.sleep(CHECK_INTERVAL)

# ================= START =================
send_me = lambda msg: print(msg)
send_me("🟢 Telegram BookMyShow bot started")

threading.Thread(target=bot_loop, daemon=True).start()
monitor_loop()
