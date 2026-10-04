import os
import time
import json
import html
import threading
import requests

from flask import Flask


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# FINAL TELEGRAM DESTINATION
CHANNEL_USERNAME = "@All1DEXPaidAlerts"

# Check DexScreener every 15 seconds
CHECK_INTERVAL = 15

DEX_API = "https://api.dexscreener.com"

SEEN_FILE = "seen_tokens.json"

FEEDS = {
    "BOOST": "/token-boosts/latest/v1",
    "AD": "/ads/latest/v1",
    "COMMUNITY": "/community-takeovers/latest/v1",
    "TOKEN_PROFILE": "/token-profiles/latest/v1",
}


# ============================================================
# BASIC CHECK
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN secret is missing. Add BOT_TOKEN in Render Environment Variables."
    )


# ============================================================
# FLASK WEB SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "All1DEXPaidAlerts Bot is running."


@app.route("/health")
def health():
    return "OK"


# ============================================================
# SEEN DATABASE
# ============================================================

def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

            if isinstance(data, list):
                return set(data)

    except Exception:
        pass

    return set()


def save_seen(seen):
    try:
        items = list(seen)[-5000:]

        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f)

    except Exception as e:
        print("Could not save seen database:", e)


seen = load_seen()


# ============================================================
# HELPERS
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "All1DEXPaidAlerts/1.0",
    "Accept": "application/json",
})


def safe_number(value, default=0):
    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def money(value):
    value = safe_number(value)

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:,.0f}"


def short_address(address):
    if not address:
        return "N/A"

    address = str(address)

    if len(address) <= 14:
        return address

    return address[:6] + "..." + address[-6:]


def age_text(timestamp):
    if not timestamp:
        return "N/A"

    try:
        created = float(timestamp) / 1000
        seconds = max(0, time.time() - created)

        minutes = int(seconds / 60)

        if minutes < 60:
            return f"{minutes}m"

        hours = int(minutes / 60)

        if hours < 24:
            return f"{hours}h"

        days = int(hours / 24)

        return f"{days}d"

    except Exception:
        return "N/A"


def clean_url(url):
    if not url:
        return ""

    return str(url).strip()


# ============================================================
# SOCIAL LINKS
# ============================================================

def find_socials(info):
    telegram = None
    twitter = None

    if not isinstance(info, dict):
        return telegram, twitter

    socials = info.get("socials") or []

    if isinstance(socials, list):

        for item in socials:

            if not isinstance(item, dict):
                continue

            url = clean_url(item.get("url"))

            if not url:
                continue

            low = url.lower()

            if (
                "t.me/" in low
                or "telegram.me/" in low
                or "telegram.dog/" in low
            ):
                if not telegram:
                    telegram = url

            if (
                "twitter.com/" in low
                or "x.com/" in low
            ):
                if not twitter:
                    twitter = url

    websites = info.get("websites") or []

    if isinstance(websites, list):

        for item in websites:

            if not isinstance(item, dict):
                continue

            url = clean_url(item.get("url"))

            if not url:
                continue

            low = url.lower()

            if (
                not telegram
                and (
                    "t.me/" in low
                    or "telegram.me/" in low
                    or "telegram.dog/" in low
                )
            ):
                telegram = url

            if (
                not twitter
                and (
                    "twitter.com/" in low
                    or "x.com/" in low
                )
            ):
                twitter = url

    return telegram, twitter


# ============================================================
# GET DEX FEED
# ============================================================

def get_feed(endpoint):

    try:

        url = DEX_API + endpoint

        response = session.get(
            url,
            timeout=15
        )

        if response.status_code != 200:

            print(
                "Feed error:",
                endpoint,
                response.status_code
            )

            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:

        print(
            "Feed request error:",
            endpoint,
            e
        )

        return []


# ============================================================
# GET TOKEN DATA
# ============================================================

def get_token_data(chain_id, token_address):

    try:

        url = (
            f"{DEX_API}/token-pairs/v1/"
            f"{chain_id}/"
            f"{token_address}"
        )

        response = session.get(
            url,
            timeout=15
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not isinstance(data, list):
            return None

        if not data:
            return None

        # Pick highest liquidity pair
        best_pair = None
        best_liquidity = -1

        for pair in data:

            if not isinstance(pair, dict):
                continue

            liquidity = safe_number(
                (pair.get("liquidity") or {}).get("usd")
            )

            if liquidity > best_liquidity:
                best_liquidity = liquidity
                best_pair = pair

        return best_pair

    except Exception as e:

        print(
            "Token data error:",
            token_address,
            e
        )

        return None


# ============================================================
# PAID ORDER CHECK
# ============================================================

def check_paid_order(chain_id, token_address):

    url = (
        f"{DEX_API}/orders/v1/"
        f"{chain_id}/"
        f"{token_address}"
    )

    try:

        response = session.get(
            url,
            timeout=15
        )

        if response.status_code != 200:
            return False

        data = response.json()

        if not isinstance(data, list):
            return False

        for order in data:

            if not isinstance(order, dict):
                continue

            status = str(
                order.get("status") or ""
            ).lower()

            if status == "approved":
                return True

        return False

    except Exception as e:

        print(
            "Order check error:",
            token_address,
            e
        )

        return False


# ============================================================
# CREATE TELEGRAM ALERT
# ============================================================

def build_message(item, source_type, pair):

    chain = str(
        item.get("chainId")
        or pair.get("chainId")
        or "unknown"
    )

    token_address = (
        item.get("tokenAddress")
        or (pair.get("baseToken") or {}).get("address")
        or ""
    )

    if not token_address:
        return None

    base = pair.get("baseToken") or {}

    token_name = (
        base.get("name")
        or "Unknown Token"
    )

    symbol = (
        base.get("symbol")
        or "UNKNOWN"
    )

    market_cap = (
        pair.get("marketCap")
        or pair.get("fdv")
        or 0
    )

    liquidity = (
        (pair.get("liquidity") or {}).get("usd")
        or 0
    )

    volume = (
        (pair.get("volume") or {}).get("h24")
        or 0
    )

    price_change = (
        (pair.get("priceChange") or {}).get("h24")
        or 0
    )

    pair_created = pair.get("pairCreatedAt")

    dex_url = (
        pair.get("url")
        or item.get("url")
        or f"https://dexscreener.com/{chain}/{token_address}"
    )

    info = pair.get("info") or {}

    telegram, twitter = find_socials(info)

    safe_name = html.escape(str(token_name))
    safe_symbol = html.escape(str(symbol))
    safe_ca = html.escape(str(token_address))
    safe_chain = html.escape(str(chain))
    safe_source = html.escape(str(source_type))

    lines = []

    lines.append("🚨 <b>NEW DEX PAID ALERT</b>")
    lines.append("")
    lines.append(
        f"🪙 <b>{safe_name}</b> "
        f"<code>${safe_symbol}</code>"
    )
    lines.append("")
    lines.append(
        f"⛓ <b>Chain:</b> {safe_chain}"
    )
    lines.append(
        f"💰 <b>MC:</b> {money(market_cap)}"
    )
    lines.append(
        f"💧 <b>Liquidity:</b> {money(liquidity)}"
    )
    lines.append(
        f"📊 <b>24H Volume:</b> {money(volume)}"
    )
    lines.append(
        f"📈 <b>24H Change:</b> "
        f"{safe_number(price_change):+.2f}%"
    )
    lines.append(
        f"🕒 <b>Age:</b> {age_text(pair_created)}"
    )
    lines.append(
        f"💳 <b>Paid Source:</b> {safe_source}"
    )
    lines.append("")
    lines.append(
        f"🧬 <b>CA:</b>\n"
        f"<code>{safe_ca}</code>"
    )
    lines.append("")
    lines.append("🔗 <b>Links</b>")

    if telegram:
        safe_tg = html.escape(
            telegram,
            quote=True
        )

        lines.append(
            f'💬 <a href="{safe_tg}">Telegram</a>'
        )

    if twitter:
        safe_tw = html.escape(
            twitter,
            quote=True
        )

        lines.append(
            f'🐦 <a href="{safe_tw}">Twitter / X</a>'
        )

    safe_dex = html.escape(
        dex_url,
        quote=True
    )

    lines.append(
        f'📊 <a href="{safe_dex}">DexScreener Chart</a>'
    )

    lines.append("")
    lines.append(
        "⚡ <b>All1DEXPaidAlerts</b>"
    )

    return "\n".join(lines)


# ============================================================
# SEND TELEGRAM MESSAGE
# ============================================================

def send_alert(message):

    if not message:
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHANNEL_USERNAME,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 200:

            data = response.json()

            if data.get("ok"):

                print(
                    "Telegram alert sent successfully."
                )

                return True

        print(
            "Telegram send failed:",
            response.status_code,
            response.text
        )

        return False

    except Exception as e:

        print(
            "Telegram send error:",
            e
        )

        return False


# ============================================================
# PROCESS ONE PAID ITEM
# ============================================================

def process_item(item, source_type):

    if not isinstance(item, dict):
        return

    chain_id = str(
        item.get("chainId")
        or ""
    )

    token_address = str(
        item.get("tokenAddress")
        or ""
    ).strip()

    if not chain_id or not token_address:
        return

    # Only Solana
    if chain_id.lower() != "solana":
        return

    unique_id = (
        f"{source_type}:"
        f"{chain_id}:"
        f"{token_address}"
    )

    if unique_id in seen:
        return

    print(
        "New candidate:",
        source_type,
        chain_id,
        token_address
    )

    # Get token market data
    pair = get_token_data(
        chain_id,
        token_address
    )

    if not pair:

        print(
            "No pair data:",
            token_address
        )

        return

    # --------------------------------------------------------
    # PAID VERIFICATION
    # --------------------------------------------------------

    approved = check_paid_order(
        chain_id,
        token_address
    )

    if approved:

        print(
            "Approved paid order:",
            token_address
        )

    else:

        print(
            "Feed signal accepted:",
            token_address
        )

    # --------------------------------------------------------
    # BUILD ALERT
    # --------------------------------------------------------

    message = build_message(
        item,
        source_type,
        pair
    )

    if not message:
        return

    # --------------------------------------------------------
    # SEND ALERT
    # --------------------------------------------------------

    sent = send_alert(message)

    if sent:

        seen.add(unique_id)

        save_seen(seen)

        print(
            "Saved:",
            token_address
        )


# ============================================================
# MAIN SCANNER
# ============================================================

def scan():

    print("Scanning DexScreener paid feeds...")

    for source_type, endpoint in FEEDS.items():

        items = get_feed(endpoint)

        print(
            source_type,
            "items:",
            len(items)
        )

        # Process newest items first
        for item in items:

            try:

                process_item(
                    item,
                    source_type
                )

                time.sleep(0.15)

            except Exception as e:

                print(
                    "Processing error:",
                    e
                )


# ============================================================
# BACKGROUND WORKER
# ============================================================

def scanner_loop():

    print("=" * 50)
    print("All1DEXPaidAlerts scanner started")
    print("Destination:", CHANNEL_USERNAME)
    print("Interval:", CHECK_INTERVAL, "seconds")
    print("=" * 50)

    while True:

        try:

            scan()

        except Exception as e:

            print(
                "Scanner error:",
                e
            )

        time.sleep(CHECK_INTERVAL)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Start scanner
    scanner_thread = threading.Thread(
        target=scanner_loop,
        daemon=True
    )

    scanner_thread.start()

    # Render web server
    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
)
