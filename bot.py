import os
import json
import time
import html
import threading
from datetime import datetime, timezone

import requests
from flask import Flask
from telegram import Bot


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# FINAL DESTINATION
CHANNEL_USERNAME = "@AllDEXPaidAlerts"

# Check every 15 seconds
CHECK_INTERVAL = 15

DEX_API = "https://api.dexscreener.com"

SEEN_FILE = "seen_tokens.json"

# Latest DEX Screener paid/visibility feeds
FEEDS = {
    "BOOST": "/token-boosts/latest/v1",
    "AD": "/ads/latest/v1",
    "COMMUNITY TAKEOVER": "/community-takeovers/latest/v1",
    "TOKEN PROFILE": "/token-profiles/latest/v1",
}


# =========================================================
# BASIC CHECK
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN secret is missing. Add BOT_TOKEN in GitHub Secrets."
    )


bot = Bot(token=BOT_TOKEN)

app = Flask(__name__)


@app.route("/")
def home():
    return "AllDEXPaidAlerts Bot is running."


@app.route("/health")
def health():
    return "OK"


# =========================================================
# SEEN DATABASE
# =========================================================

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
        # Keep database small
        items = list(seen)[-5000:]

        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f)

    except Exception as e:
        print("Could not save seen database:", e)


seen = load_seen()


# =========================================================
# HTTP SESSION
# =========================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "AllDEXPaidAlerts/1.0",
    "Accept": "application/json",
})


# =========================================================
# HELPERS
# =========================================================

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

    if len(address) <= 14:
        return address

    return address[:6] + "..." + address[-6:]


def age_text(timestamp):
    if not timestamp:
        return "N/A"

    try:
        # DexScreener pairCreatedAt is milliseconds
        created = float(timestamp) / 1000

        now = time.time()
        seconds = max(0, now - created)

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


# =========================================================
# SOCIAL LINK DETECTION
# =========================================================

def find_socials(info):
    telegram = None
    twitter = None

    if not info:
        return telegram, twitter

    socials = info.get("socials") or []

    if isinstance(socials, list):

        for social in socials:

            if not isinstance(social, dict):
                continue

            platform = str(
                social.get("platform") or ""
            ).lower()

            handle = str(
                social.get("handle") or ""
            ).strip()

            if platform in ("telegram", "tg"):

                if handle.startswith("http"):
                    telegram = handle

                elif handle:
                    telegram = "https://t.me/" + handle.lstrip("@")

            elif platform in ("twitter", "x"):

                if handle.startswith("http"):
                    twitter = handle

                elif handle:
                    twitter = "https://x.com/" + handle.lstrip("@")

    # Websites / links
    websites = info.get("websites") or []

    if isinstance(websites, list):

        for website in websites:

            if not isinstance(website, dict):
                continue

            url = clean_url(website.get("url"))

            low = url.lower()

            if not telegram and (
                "t.me/" in low
                or "telegram.me/" in low
                or "telegram.dog/" in low
            ):
                telegram = url

            if not twitter and (
                "twitter.com/" in low
                or "x.com/" in low
            ):
                twitter = url

    return telegram, twitter


def find_telegram_from_links(links):
    if not isinstance(links, list):
        return None

    for item in links:

        if not isinstance(item, dict):
            continue

        url = clean_url(item.get("url"))

        low = url.lower()

        if (
            "t.me/" in low
            or "telegram.me/" in low
            or "telegram.dog/" in low
        ):
            return url

    return None


def find_twitter_from_links(links):
    if not isinstance(links, list):
        return None

    for item in links:

        if not isinstance(item, dict):
            continue

        url = clean_url(item.get("url"))

        low = url.lower()

        if "twitter.com/" in low or "x.com/" in low:
            return url

    return None


# =========================================================
# GET LATEST FEED
# =========================================================

def get_feed(endpoint):

    try:

        url = DEX_API + endpoint

        response = session.get(
            url,
            timeout=12
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

        if isinstance(data, dict):

            # Some API responses may be wrapped
            for key in ("data", "tokens", "results"):

                if isinstance(data.get(key), list):
                    return data[key]

        return []

    except Exception as e:

        print(
            "Feed exception:",
            endpoint,
            e
        )

        return []


# =========================================================
# GET TOKEN PAIR INFORMATION
# =========================================================

def get_token_data(chain_id, token_address):

    if not chain_id or not token_address:
        return None

    url = (
        f"{DEX_API}/token-pairs/v1/"
        f"{chain_id}/{token_address}"
    )

    try:

        response = session.get(
            url,
            timeout=12
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not isinstance(data, list):
            return None

        if not data:
            return None

        # Choose highest-liquidity pair
        best = max(
            data,
            key=lambda x: safe_number(
                (x.get("liquidity") or {}).get("usd")
            )
        )

        return best

    except Exception as e:

        print(
            "Token data error:",
            token_address,
            e
        )

        return None


# =========================================================
# VERIFY PAID ORDER
# =========================================================

def check_paid_order(chain_id, token_address):

    if not chain_id or not token_address:
        return False

    url = (
        f"{DEX_API}/orders/v1/"
        f"{chain_id}/{token_address}"
    )

    try:

        response = session.get(
            url,
            timeout=12
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


# =========================================================
# CREATE TELEGRAM ALERT
# =========================================================

def build_message(item, source_type, pair):

    chain = str(
        item.get("chainId")
        or (pair or {}).get("chainId")
        or "unknown"
    )

    token_address = (
        item.get("tokenAddress")
        or (pair or {}).get("baseToken", {}).get("address")
    )

    if not token_address:
        return None

    base = (pair or {}).get("baseToken") or {}

    token_name = (
        base.get("name")
        or "Unknown Token"
    )

    symbol = (
        base.get("symbol")
        or "UNKNOWN"
    )

    market_cap = (
        (pair or {}).get("marketCap")
        or (pair or {}).get("fdv")
        or 0
    )

    liquidity = (
        ((pair or {}).get("liquidity") or {}).get("usd")
        or 0
    )

    volume = (
        ((pair or {}).get("volume") or {}).get("h24")
        or 0
    )

    price_change = (
        ((pair or {}).get("priceChange") or {}).get("h24")
        or 0
    )

    pair_created = (pair or {}).get(
        "pairCreatedAt"
    )

    dex_url = (
        (pair or {}).get("url")
        or item.get("url")
        or f"https://dexscreener.com/{chain}/{token_address}"
    )

    info = (pair or {}).get("info") or {}

    telegram, twitter = find_socials(info)

    if not telegram:
        telegram = find_telegram_from_links(
            item.get("links")
        )

    if not twitter:
        twitter = find_twitter_from_links(
            item.get("links")
        )

    # IMPORTANT:
    # We only alert projects which have Telegram.
    if not telegram:
        return None

    safe_name = html.escape(str(token_name))
    safe_symbol = html.escape(str(symbol))
    safe_ca = html.escape(str(token_address))
    safe_chain = html.escape(chain)

    source = html.escape(source_type)

    tg_link = html.escape(telegram, quote=True)
    dex_link = html.escape(dex_url, quote=True)

    if twitter:
        twitter_link = html.escape(
            twitter,
            quote=True
        )

        twitter_line = (
            f'│ 𝕏 <a href="{twitter_link}">Twitter / X</a>\n'
        )

    else:
        twitter_line = ""

    message = (
        "🚨 <b>NEW DEX PAID ALERT</b>\n"
        "\n"
        f"💊 <b>{safe_name}</b> "
        f"(<code>${safe_symbol}</code>)\n"
        "\n"
        f"💰 <b>MC:</b> {money(market_cap)}\n"
        f"💧 <b>Liquidity:</b> {money(liquidity)}\n"
        f"📊 <b>24H Volume:</b> {money(volume)}\n"
        f"📈 <b>24H Change:</b> {safe_number(price_change):+.2f}%\n"
        f"🕐 <b>Age:</b> {age_text(pair_created)}\n"
        f"⛓ <b>Chain:</b> {safe_chain}\n"
        "\n"
        f"💳 <b>Paid Type:</b> {source}\n"
        "\n"
        "🔗 <b>Links</b>\n"
        f'│ 📡 <a href="{tg_link}">Telegram</a>\n'
        f"{twitter_line}"
        f'│ 📊 <a href="{dex_link}">DexScreener</a>\n'
        "\n"
        "📝 <b>CA:</b>\n"
        f"<code>{safe_ca}</code>\n"
        "\n"
        "⚡ <b>All DEX Paid Alerts</b>"
    )

    return message


# =========================================================
# SEND TELEGRAM
# =========================================================

def send_alert(message):

    if not message:
        return False

    try:

        bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=False,
        )

        print(
            "ALERT SENT ->",
            CHANNEL_USERNAME
        )

        return True

    except Exception as e:

        print(
            "Telegram send error:",
            e
        )

        return False


# =========================================================
# PROCESS ONE PAID ITEM
# =========================================================

def process_item(item, source_type):

    if not isinstance(item, dict):
        return

    chain_id = item.get("chainId")
    token_address = item.get("tokenAddress")

    if not chain_id or not token_address:
        return

    # Only Solana for now
    # Remove this if you want every DEX chain.
    if str(chain_id).lower() != "solana":
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

    # Get token market information
    pair = get_token_data(
        chain_id,
        token_address
    )

    if not pair:
        print(
            "No pair data:",
            token_address
        )

        # Mark it seen so we don't hammer API
        seen.add(unique_id)
        save_seen(seen)
        return

    # =====================================================
    # PAID VERIFICATION
    # =====================================================

    # For boost/ad/profile/community feeds,
    # the feed itself is the paid/visibility signal.
    #
    # We also check the official order endpoint when
    # possible. If it is approved, we have stronger
    # confirmation.
    #
    # Do not reject the feed only because the order
    # endpoint has not indexed it yet.

    approved = check_paid_order(
        chain_id,
        token_address
    )

    if approved:
        print(
            "Approved paid order:",
            token_address
        )

    # Build alert
    message = build_message(
        item,
        source_type,
        pair
    )

    if message:

        success = send_alert(message)

        if success:
            seen.add(unique_id)
            save_seen(seen)

    else:

        # No Telegram = don't alert
        print(
            "Skipped (no Telegram):",
            token_address
        )

        seen.add(unique_id)
        save_seen(seen)


# =========================================================
# MAIN DEX SCANNER
# =========================================================

def scan():

    print(
        "Scanning DEX Screener paid feeds..."
    )

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

                # Small delay to stay friendly
                time.sleep(0.15)

            except Exception as e:

                print(
                    "Processing error:",
                    e
                )


# =========================================================
# BACKGROUND WORKER
# =========================================================

def scanner_loop():

    print(
        "===================================="
    )

    print(
        "AllDEXPaidAlerts scanner started"
    )

    print(
        "Destination:",
        CHANNEL_USERNAME
    )

    print(
        "Interval:",
        CHECK_INTERVAL,
        "seconds"
    )

    print(
        "===================================="
    )

    while True:

        try:

            scan()

        except Exception as e:

            print(
                "Scanner error:",
                e
            )

        time.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    # Start scanner in background
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
