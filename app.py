import os, time, threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

# ===== DEFAULT DEVICE STATE =====
DEFAULTS = dict(
    armed=True,
    thr=35,
    hold_ms=900,
    cooldown_s=30,

    # NEW: RMS pencere süresi (ESP tarafındaki WINDOW_MS)
    window_ms=900,

    # watchdog / state
    last_ping=0,
    last_alarm=0,

    # NEW: calib state
    calib_req_ts=0,        # telegram /calib ile setlenir
    calib_result=None,     # float/int
    calib_result_ts=0      # result geldiği zaman
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
    dev = request.args.get("dev", "EGE")  # default device changed
    ensure_dev(dev)
    s = DEVICES[dev]
    return jsonify(
        armed=s["armed"],
        thr=s["thr"],
        hold_ms=s["hold_ms"],
        cooldown_s=s["cooldown_s"],

        # NEW
        window_ms=s["window_ms"],
        calib_req_ts=s["calib_req_ts"],

        server_time=int(time.time())
    )

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "EGE")  # default device changed
    ensure_dev(dev)
    DEVICES[dev]["last_ping"] = time.time()
    return jsonify(ok=True, t=int(time.time()))

@app.post("/event")
def event():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "EGE")  # default device changed
    rms = data.get("rms", None)

    ensure_dev(dev)
    DEVICES[dev]["last_alarm"] = time.time()

    tg_send(f"🚨 {dev}: Ağlama algılandı (RMS={rms})")
    return jsonify(ok=True)

# NEW: ESP 15 saniyelik oda ölçüm sonucunu buraya yollar
@app.post("/calib")
def calib_result():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "EGE")  # default device changed
    rms_avg = data.get("rms_avg", None)   # float/int bekliyoruz
    dur_s = data.get("dur_s", 15)

    ensure_dev(dev)
    s = DEVICES[dev]
    s["calib_result"] = rms_avg
    s["calib_result_ts"] = int(time.time())

    # İstersen otomatik telegrama da bas:
    tg_send(f"📏 {dev}: Oda ölçümü hazır ({dur_s}s) | RMS_avg={rms_avg}")

    return jsonify(ok=True)

@app.post("/telegram")
def telegram():
    data = request.get_json(silent=True) or {}
    msg = (data.get("message", {}) or {}).get("text", "")
    msg = (msg or "").strip()

    dev = "EGE"  # default device changed
    ensure_dev(dev)
    s = DEVICES[dev]

    def reply(t): tg_send(t)

    if msg in ["/start", "/help"]:
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

    if msg == "/on":
        s["armed"] = True
        reply("🟢 Sistem AKTİF")
        return jsonify(ok=True)

    if msg == "/off":
        s["armed"] = False
        reply("🔴 Sistem KAPALI")
        return jsonify(ok=True)

    if msg == "/status":
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

    # NEW: /calib komutu
    if msg == "/calib" or msg.lower() == "calib":
        # Eğer taze sonuç varsa direkt dön
        if s["calib_result_ts"] and (time.time() - s["calib_result_ts"] <= 60) and (s["calib_result"] is not None):
            reply(f"✅ Oda RMS (15s ort): {s['calib_result']}  (taze)")
            return jsonify(ok=True)

        # Yoksa yeni ölçüm iste
        s["calib_req_ts"] = int(time.time())
        s["calib_result"] = None
        s["calib_result_ts"] = 0
        reply("📏 15sn oda ölçümü başlatıldı. ~15-20sn sonra tekrar /calib yaz.")
        return jsonify(ok=True)

    if msg.startswith("/set"):
        try:
            _, key, val = msg.split()
            val = int(val)
        except:
            reply("❌ Format: /set thr|hold|cooldown|window değer")
            return jsonify(ok=True)

        # min-max updated to your requested limits
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
