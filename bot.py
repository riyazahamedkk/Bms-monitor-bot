import os
import time
import json
import hashlib
from urllib.parse import urlparse
from curl_cffi import requests
from bs4 import BeautifulSoup

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

# ================= CONFIG =================
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"
CHAT_ID = None

# ================= FUNCTIONS =================

def get_chat_id():
    global CHAT_ID
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
            return

    try:
        std_requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"❌ Failed to send message: {e}")

def fetch_html_data():
    try:
        # 🚨 TACTIC SWITCH: We mimic Safari on macOS
        # We hit the MOVIE_URL directly (not the API)
        r = requests.get(
            MOVIE_URL,
            impersonate="safari15_5",
            timeout=20
        )
        
        if r.status_code == 403:
            print("⚠️ HTML Page 403. IP is heavily blocked.")
            return None
        
        # If we get a 200 OK, we parse the HTML
        if r.status_code == 200:
            return r.text
            
        return None
    except Exception as e:
        print(f"⚠️ Fetch Error: {e}")
        return None

def extract_shows(html):
    """
    Looks for theatre names in the HTML. 
    BMS lists theatres in <a> tags with specific classes or inside JSON data.
    """
    if not html: 
        return set()

    soup = BeautifulSoup(html, "html.parser")
    shows = set()

    # Strategy 1: Look for the specific 'venue-name' link usually found in BMS lists
    for venue in soup.select('a.__venue-name'):
        shows.add(venue.get_text(strip=True))

    # Strategy 2: If the page uses Client Side Rendering, look for the JSON blob
    # BMS often puts data in a script tag with id="__NEXT_DATA__"
    if not shows:
        script = soup.find("script", id="__NEXT_DATA__")
        if script:
            try:
                data = json.loads(script.string)
                # This path changes often, but we try to hash the whole availability block
                # If the JSON exists, it's a good sign the page loaded.
                # We will just hash the whole script content to detect change.
                shows.add("JSON_DATA_FOUND") 
                shows.add(hashlib.md5(script.string.encode()).hexdigest())
            except:
                pass

    return shows

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return set(json.load(f).get("shows", []))
            except:
                return set()
    return set()

def save_state(shows):
    with open(STATE_FILE, "w") as f:
        json.dump({"shows": list(shows)}, f)

def monitor():
    print("🟢 Monitor Started (HTML Mode)")
    last_shows = load_state()

    while True:
        html = fetch_html_data()
        
        if html:
            current_shows = extract_shows(html)
            
            if current_shows:
                # If we found JSON data but no theatres (Strategy 2), it's a hash comparison
                if "JSON_DATA_FOUND" in current_shows:
                     diff = current_shows - last_shows
                     if diff and last_shows:
                        print("✅ JSON Data Changed!")
                        send_message(f"🚨 *BMS UPDATE DETECTED*\n\nPage data changed for: {MOVIE_URL}")
                
                # Standard Strategy 1 (HTML elements)
                else:
                    new_theatres = current_shows - last_shows
                    if new_theatres:
                        msg = "🎬 *New Theatres/Shows Added!*\n\n" + "\n".join(new_theatres) + f"\n\n🔗 {MOVIE_URL}"
                        send_message(msg)
                        print(f"✅ Alert sent for: {new_theatres}")
                    else:
                        print(f"💤 No new theatres found. (Total visible: {len(current_shows)})")

                save_state(current_shows)
                last_shows = current_shows
            else:
                print("⚠️ Page loaded, but no show data found (might be fully sold out or layout changed).")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()
