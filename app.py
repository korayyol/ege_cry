import os
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.status_code == 200
    except Exception as e:
        print(e)
        return False

@app.get("/")
def home():
    return "ok", 200

@app.post("/event")
def event():
    data = request.get_json(silent=True) or {}

    if data.get("type") == "cry":
        dev = data.get("dev", "unknown")
        adc = data.get("adc", "-")
        send_telegram(f"👶 Bebek ağlama algılandı\nDevice: {dev}\nADC: {adc}")
        return jsonify(ok=True), 200

    return jsonify(ok=True), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
