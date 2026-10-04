def send_alert(message):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": CHANNEL_USERNAME,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        print("Telegram status:", response.status_code)
        print("Telegram response:", response.text)

        if response.status_code == 200:
            print("Telegram alert sent successfully")
            return True

        print("Telegram alert failed")
        return False

    except Exception as e:
        print("Telegram send error:", e)
        return False
