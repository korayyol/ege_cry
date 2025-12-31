import os, time, threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")   # sadece bu yeterli

# ===== Subscribers (botu kullanan herkes) =====
SUBSCRIBERS = set()  # chat_id (int)

# ===== DEFAULT DEVICE STATE =====
DEFAULTS = dict(
    armed=True,
    thr=35,
    hold_ms=900,
    cooldown_s=30,

    window_ms=900,

    last_ping=0,
    last_alarm=0,

    calib_req_ts=0,
    calib_result=None,
    calib_result_ts=0
)

DEVICES = {}

PING_TIMEOUT_S = 260
CHECK_PERIOD_S = 10

# ---------- Telegram helpers ----------
def tg_send(chat_id: int, msg: str):
    if not BOT_TOKEN:
        print("Telegram env missing: BOT_TOKEN")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=8)
        print("sendMessage:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram send error:", e)

def tg_broadcast(msg: str):
    # tüm abonelere gönder
    for cid in list(SUBSCRIBERS):
        tg_send(cid, msg)

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
                    tg_broadcast(f"⚠️ {dev}: {PING_TIMEOUT_S}s ping yok (ESP offline?)")
                    s["last_alarm"] = now
        time.sleep(CHECK_PERIOD_S)

threading.Thread(target=watchdog, daemon=True).start()

# ---------- routes ----------
@app.get("/")
def home():
    return "ok", 200

@app.get("/cfg")
def cfg():
    dev = request.args.get("dev", "EGE")
    ensure_dev(dev)
    s = DEVICES[dev]
    return jsonify(
        armed=s["armed"],
        thr=s["thr"],
        hold_ms=s["hold_ms"],
        cooldown_s=s["cooldown_s"],
        window_ms=s["window_ms"],
        calib_req_ts=s["calib_req_ts"],
        server_time=int(time.time())
    )

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "EGE")
    ensure_dev(dev)
    DEVICES[dev]["last_ping"] = time.time()
    return jsonify(ok=True, t=int(time.time()))

@app.post("/event")
def event():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "EGE")
    rms = data.get("rms", None)

    ensure_dev(dev)
    DEVICES[dev]["last_alarm"] = time.time()

    tg_broadcast(f"🚨 {dev}: Ağlama algılandı (RMS={rms})")
    return jsonify(ok=True)

@app.post("/calib")
def calib_result():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "EGE")
    rms_avg = data.get("rms_avg", None)
    dur_s = data.get("dur_s", 15)

    ensure_dev(dev)
    s = DEVICES[dev]
    s["calib_result"] = rms_avg
    s["calib_result_ts"] = int(time.time())

    tg_broadcast(f"📏 {dev}: Oda ölçümü hazır ({dur_s}s) | RMS_avg={rms_avg}")
    return jsonify(ok=True)

@app.post("/telegram")
def telegram():
    update = request.get_json(silent=True) or {}

    # Telegram Update içinden chat id + mesajı çek
    msg_obj = update.get("message") or update.get("edited_message") or {}
    chat = msg_obj.get("chat") or {}
    chat_id = chat.get("id")  # int
    text = (msg_obj.get("text") or "").strip()

    # Chat id yoksa çık
    if chat_id is None:
        return jsonify(ok=True)

    # bu chat'i abone yap (private veya grup fark etmez)
    SUBSCRIBERS.add(int(chat_id))

    dev = "EGE"
    ensure_dev(dev)
    s = DEVICES[dev]

    def reply(t): tg_send(int(chat_id), t)

    if text in ["/start", "/help"]:
        reply(
            "/on → sistemi aç\n"
            "/off → sistemi kapat\n"
            "/calib → 15sn oda RMS ölçümü (sonuç için tekrar /calib)\n"
            "/set thr 35\n"
            "/set hold 900\n"
            "/set cooldown 30\n"
            "/set window 900   (WINDOW_MS)\n"
            "/status"
        )
        return jsonify(ok=True)

    if text == "/on":
        s["armed"] = True
        reply("🟢 Sistem AKTİF")
        return jsonify(ok=True)

    if text == "/off":
        s["armed"] = False
        reply("🔴 Sistem KAPALI")
        return jsonify(ok=True)

    if text == "/status":
        last_ping_ago = int(time.time() - s["last_ping"]) if s["last_ping"] else -1
        calib_age = int(time.time() - s["calib_result_ts"]) if s["calib_result_ts"] else -1
        reply(
            f"armed={s['armed']}\n"
            f"thr={s['thr']}\n"
            f"hold_ms={s['hold_ms']}\n"
            f"cooldown_s={s['cooldown_s']}\n"
            f"window_ms={s['window_ms']}\n"
            f"last_ping={last_ping_ago}s önce\n"
            f"last_calib_rms={s['calib_result']} ({calib_age}s önce)"
        )
        return jsonify(ok=True)

    if text == "/calib" or text.lower() == "calib":
        if s["calib_result_ts"] and (time.time() - s["calib_result_ts"] <= 60) and (s["calib_result"] is not None):
            reply(f"✅ Oda RMS (15s ort): {s['calib_result']}  (taze)")
            return jsonify(ok=True)

        s["calib_req_ts"] = int(time.time())
        s["calib_result"] = None
        s["calib_result_ts"] = 0
        reply("📏 15sn oda ölçümü başlatıldı. ~15-20sn sonra tekrar /calib yaz.")
        return jsonify(ok=True)

    if text.startswith("/set"):
        try:
            _, key, val = text.split()
            val = int(val)
        except:
            reply("❌ Format: /set thr|hold|cooldown|window değer")
            return jsonify(ok=True)

        # senin istediğin min-max limitler
        if key == "thr":
            s["thr"] = max(1, min(1023, val))
        elif key == "hold":
            s["hold_ms"] = max(1, min(10000, val))
        elif key == "cooldown":
            s["cooldown_s"] = max(1, min(600, val))
        elif key in ["window", "window_ms"]:
            s["window_ms"] = max(18, min(7200, val))
        else:
            reply("❌ Bilinmeyen parametre")
            return jsonify(ok=True)

        reply(f"✅ {key} güncellendi")
        return jsonify(ok=True)

    reply("❓ /help yaz")
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
