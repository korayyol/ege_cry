import os
import time
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# İstersen dev bazlı son ping zamanını RAM'de tutar (deploy restart olursa sıfırlanır)
LAST_PING = {}

def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=8)
        print("Telegram:", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as e:
        print("Telegram error:", e)
        return False

@app.get("/")
def home():
    return "ok", 200

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "unknown")
    LAST_PING[dev] = int(time.time())
    # sadece "ok" dönüyoruz (ESP tarafı 200 görsün yeter)
    return jsonify(ok=True, dev=dev, ts=LAST_PING[dev]), 200

@app.post("/event")
def event():
    data = request.get_json(silent=True) or {}
    dev = str(data.get("dev", "unknown"))
    typ = str(data.get("type", ""))

    # sadece ağlama
    if typ == "cry":
        adc = data.get("adc", None)
        text = f"👶 Ağlama algılandı\nDevice: {dev}"
        if adc is not None:
            text += f"\nADC: {adc}"
        ok = send_telegram(text)
        return jsonify(ok=True, telegram=ok), 200

    return jsonify(ok=True, ignored=True), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
