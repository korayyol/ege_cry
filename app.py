import os
import time
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

def get_env(*names, default=""):
    """Return first non-empty env value among given names."""
    for n in names:
        v = os.environ.get(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default

BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
CHAT_ID   = get_env("TELEGRAM_CHAT_ID", "CHAT_ID")

# Debug: Render loglarında env isimlerini gör (değerleri gizli)
print("Booting...")
print("Has TELEGRAM_BOT_TOKEN:", bool(os.environ.get("TELEGRAM_BOT_TOKEN")))
print("Has BOT_TOKEN:", bool(os.environ.get("BOT_TOKEN")))
print("Has TELEGRAM_CHAT_ID:", bool(os.environ.get("TELEGRAM_CHAT_ID")))
print("Has CHAT_ID:", bool(os.environ.get("CHAT_ID")))
print("BOT_TOKEN set:", bool(BOT_TOKEN))
print("CHAT_ID set:", bool(CHAT_ID))

LAST_PING = {}

def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing token/chat id. Please set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (or BOT_TOKEN + CHAT_ID).")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}

    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram status:", r.status_code)
        if r.status_code != 200:
            print("Telegram response:", r.text[:300])
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", repr(e))
        return False

@app.get("/")
def home():
    return "ok", 200

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "unknown")
    ts = int(time.time())
    LAST_PING[dev] = ts
    return jsonify(ok=True, dev=dev, ts=ts), 200

@app.post("/event")
def event():
    data = request.get_json(silent=True) or {}
    dev = str(data.get("dev", "unknown"))
    typ = str(data.get("type", "")).strip().lower()

    if typ == "cry":
        adc = data.get("adc", None)
        text = f"👶 Ağlama algılandı\nDevice: {dev}"
        if adc is not None:
            text += f"\nADC: {adc}"
        ok = send_telegram(text)
        return jsonify(ok=True, telegram=ok), 200

    return jsonify(ok=True, ignored=True), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
