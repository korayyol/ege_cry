import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram env missing: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_notification": False}
    try:
        r = requests.post(url, json=payload, timeout=8)
        print("Telegram status:", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False

@app.get("/")
def home():
    return "ok", 200

@app.post("/event")
def event():
    data = request.get_json(silent=True) or {}
    dev = str(data.get("dev", "unknown"))
    typ = str(data.get("type", ""))

    # sadece cry dinliyoruz
    if typ == "cry":
        level = data.get("level", None)
        text = f"👶 Ağlama algılandı\nDevice: {dev}"
        if level is not None:
            text += f"\nLevel: {level}"
        ok = send_telegram(text)
        return jsonify({"ok": True, "telegram": ok}), 200

    # diğer her şey yok say
    return jsonify({"ok": True, "ignored": True}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
