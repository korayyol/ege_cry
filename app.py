import os, time, threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")
# SECRET kaldırıldı (403 sorununu bitirmek için)

# ===== DEFAULT DEVICE STATE =====
DEFAULTS = dict(
    armed=True,
    thr=35,
    hold_ms=900,
    cooldown_s=30,
    last_ping=0,
    last_alarm=0
)

DEVICES = {}

PING_TIMEOUT_S = 260
CHECK_PERIOD_S = 10

# ---------- helpers ----------
def tg_send(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram env missing")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=8)
        print("sendMessage:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram send error:", e)

def ensure_dev(dev):
    if dev not in DEVICES:
        DEVICES[dev] = DEFAULTS.copy()

# ---------- watchdog ----------
def watchdog():
    while True:
        now = time.time()
        for dev, s in DEVICES.items():
            if s["last_ping"] > 0 and now - s["last_ping"] > PING_TIMEOUT_S:
                if now - s["last_alarm"] > PING_TIMEOUT_S:
                    tg_send(f"⚠️ {dev}: {PING_TIMEOUT_S}s ping yok (ESP offline?)")
                    s["last_alarm"] = now
        time.sleep(CHECK_PERIOD_S)

threading.Thread(target=watchdog, daemon=True).start()

# ---------- routes ----------
@app.get("/")
def home():
    return "ok", 200

@app.get("/cfg")
def cfg():
    dev = request.args.get("dev", "baby1")
    ensure_dev(dev)
    s = DEVICES[dev]
    return jsonify(
        armed=s["armed"],
        thr=s["thr"],
        hold_ms=s["hold_ms"],
        cooldown_s=s["cooldown_s"],
        server_time=int(time.time())
    )

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "baby1")
    ensure_dev(dev)
    DEVICES[dev]["last_ping"] = time.time()
    return jsonify(ok=True, t=int(time.time()))

@app.post("/event")
def event():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "baby1")
    rms = data.get("rms", None)

    ensure_dev(dev)
    DEVICES[dev]["last_alarm"] = time.time()

    tg_send(f"🚨 {dev}: Ağlama algılandı (RMS={rms})")
    return jsonify(ok=True)

@app.post("/telegram")
def telegram():
    # SECRET kontrolü kaldırıldı -> 403 artık yok

    data = request.get_json(silent=True) or {}
    msg = (data.get("message", {}) or {}).get("text", "")
    msg = (msg or "").strip()

    dev = "baby1"
    ensure_dev(dev)
    s = DEVICES[dev]

    def reply(t): tg_send(t)

    if msg in ["/start", "/help"]:
        reply(
            "/on → sistemi aç\n"
            "/off → sistemi kapat\n"
            "/set thr 35\n"
            "/set hold 900\n"
            "/set cooldown 30\n"
            "/status"
        )
        return jsonify(ok=True)

    if msg == "/on":
        s["armed"] = True
        reply("🟢 Sistem AKTİF")
        return jsonify(ok=True)

    if msg == "/off":
        s["armed"] = False
        reply("🔴 Sistem KAPALI")
        return jsonify(ok=True)

    if msg == "/status":
        reply(
            f"armed={s['armed']}\n"
            f"thr={s['thr']}\n"
            f"hold_ms={s['hold_ms']}\n"
            f"cooldown_s={s['cooldown_s']}\n"
            f"last_ping={int(time.time()-s['last_ping'])}s önce"
        )
        return jsonify(ok=True)

    if msg.startswith("/set"):
        try:
            _, key, val = msg.split()
            val = int(val)
        except:
            reply("❌ Format: /set thr|hold|cooldown değer")
            return jsonify(ok=True)

        if key == "thr":
            s["thr"] = max(1, min(1023, val))
        elif key == "hold":
            s["hold_ms"] = max(100, min(5000, val))
        elif key == "cooldown":
            s["cooldown_s"] = max(5, min(600, val))
        else:
            reply("❌ Bilinmeyen parametre")
            return jsonify(ok=True)

        reply(f"✅ {key} güncellendi")
        return jsonify(ok=True)

    reply("❓ /help yaz")
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
