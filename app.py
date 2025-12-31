# app.py
import os
import time
import threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ===== ENV =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SECRET    = os.environ.get("SECRET", "")  # opsiyonel: webhook güvenliği için

# ===== Subscribers (bildirim alacak chat'ler) =====
SUBSCRIBERS = set()  # chat_id (int)

# ===== DEFAULT DEVICE STATE =====
DEFAULTS = dict(
    armed=True,
    thr=35,
    hold_ms=900,
    cooldown_s=30,
    window_ms=360,         # ms

    last_ping=0.0,
    last_alarm=0.0,

    calib_req_ts=0,
    calib_result=None,
    calib_result_ts=0
)
DEVICES = {}

PING_TIMEOUT_S = 260
CHECK_PERIOD_S = 10

# ---------- helpers ----------
def ensure_dev(dev: str):
    if dev not in DEVICES:
        DEVICES[dev] = DEFAULTS.copy()

def tg_send(chat_id: int, msg: str):
    if not BOT_TOKEN:
        print("Telegram env missing: BOT_TOKEN")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=8)
        print("Telegram send:", r.status_code, r.text[:180])
    except Exception as e:
        print("Telegram send error:", e)

def tg_broadcast(msg: str):
    for cid in list(SUBSCRIBERS):
        tg_send(int(cid), msg)

def clamp(v: int, vmin: int, vmax: int) -> int:
    return max(vmin, min(vmax, v))

# ---------- watchdog ----------
def watchdog():
    while True:
        now = time.time()
        for dev, s in DEVICES.items():
            if s["last_ping"] and (now - s["last_ping"] > PING_TIMEOUT_S):
                # aynı uyarıyı spamlamamak için last_alarm kullan
                if (now - s["last_alarm"]) > PING_TIMEOUT_S:
                    tg_broadcast(f"⚠️ {dev}: {PING_TIMEOUT_S}s ping yok (ESP offline olabilir)")
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
        armed=bool(s["armed"]),
        thr=int(s["thr"]),
        hold_ms=int(s["hold_ms"]),
        cooldown_s=int(s["cooldown_s"]),
        window_ms=int(s["window_ms"]),
        calib_req_ts=int(s["calib_req_ts"]),
        server_time=int(time.time()),
    ), 200

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "EGE")
    ensure_dev(dev)
    DEVICES[dev]["last_ping"] = time.time()
    return jsonify(ok=True, t=int(time.time())), 200

@app.post("/event")
def event():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "EGE")
    rms = data.get("rms", None)

    ensure_dev(dev)
    DEVICES[dev]["last_alarm"] = time.time()

    tg_broadcast(f"🚨 {dev}: Ağlama algılandı (RMS={rms})")
    return jsonify(ok=True), 200

@app.post("/calib")
def calib():
    data = request.get_json(force=True) or {}
    dev = data.get("dev", "EGE")
    rms_avg = data.get("rms_avg", None)
    dur_s = data.get("dur_s", 15)

    ensure_dev(dev)
    s = DEVICES[dev]
    s["calib_result"] = rms_avg
    s["calib_result_ts"] = int(time.time())

    tg_broadcast(f"📏 {dev}: Oda ölçümü hazır ({dur_s}s) | RMS_avg={rms_avg}")
    return jsonify(ok=True), 200

@app.post("/telegram")
def telegram():
    # opsiyonel webhook güvenliği (Render -> Telegram webhook araya girerse)
    if SECRET:
        got = request.args.get("secret", "")
        if got != SECRET:
            return jsonify(ok=True), 200

    update = request.get_json(silent=True) or {}

    msg_obj = update.get("message") or update.get("edited_message") or {}
    chat = msg_obj.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg_obj.get("text") or "").strip()

    if chat_id is None:
        return jsonify(ok=True), 200

    chat_id = int(chat_id)

    def reply(t: str):
        tg_send(chat_id, t)

    # ---- /start: tekrar dahil ol ----
    if text == "/start":
        SUBSCRIBERS.add(chat_id)
        reply(
            "✅ Bot aktif. Bildirimler açıldı.\n"
            "/help yazabilirsin."
        )
        return jsonify(ok=True), 200

    # ---- /leave: tamamen çık ----
    if text == "/leave":
        if chat_id in SUBSCRIBERS:
            SUBSCRIBERS.remove(chat_id)
        reply("👋 Bot kapatıldı. Bildirim almayacaksın.\nTekrar için /start yaz.")
        return jsonify(ok=True), 200

    # /leave sonrası “alarm + cevap yok” için:
    # abone değilse /start dışında her şeyi sessizce yok say.
    if chat_id not in SUBSCRIBERS:
        return jsonify(ok=True), 200

    # ---- help ----
    if text in ["/help", "help"]:
        reply(
            "/start → botu aç (bildirim al)\n"
            "/leave → botu kapat (bildirim alma)\n"
            "/on → alarm sistemi aç\n"
            "/off → alarm sistemi kapat\n"
            "/calib → 15sn oda ölçümü\n"
            "/set thr X (1..1023)\n"
            "/set hold X (1..10000 ms)\n"
            "/set cooldown X (1..600 s)\n"
            "/set window X (18..7200 ms)\n"
            "/status"
        )
        return jsonify(ok=True), 200

    # tek cihaz state'i (istersen /set dev ile genişletiriz; şimdilik sabit)
    dev = "EGE"
    ensure_dev(dev)
    s = DEVICES[dev]

    # ---- on/off ----
    if text == "/on":
        s["armed"] = True
        reply("🟢 Sistem AKTİF")
        return jsonify(ok=True), 200

    if text == "/off":
        s["armed"] = False
        reply("🔴 Sistem KAPALI")
        return jsonify(ok=True), 200

    # ---- status ----
    if text == "/status":
        now = time.time()
        last_ping_ago = int(now - s["last_ping"]) if s["last_ping"] else -1
        calib_age = int(now - s["calib_result_ts"]) if s["calib_result_ts"] else -1
        reply(
            f"dev={dev}\n"
            f"armed={s['armed']}\n"
            f"thr={s['thr']}\n"
            f"hold_ms={s['hold_ms']}\n"
            f"cooldown_s={s['cooldown_s']}\n"
            f"window_ms={s['window_ms']}\n"
            f"last_ping={last_ping_ago}s önce\n"
            f"last_calib_rms={s['calib_result']} ({calib_age}s önce)"
        )
        return jsonify(ok=True), 200

    # ---- calib ----
    if text == "/calib" or text.lower() == "calib":
        # son sonuç tazeyse direkt göster
        if s["calib_result_ts"] and (time.time() - s["calib_result_ts"] <= 60) and (s["calib_result"] is not None):
            reply(f"✅ Oda RMS (15s ort): {s['calib_result']}  (taze)")
            return jsonify(ok=True), 200

        s["calib_req_ts"] = int(time.time())
        s["calib_result"] = None
        s["calib_result_ts"] = 0
        reply("📏 15sn oda ölçümü başlatıldı. ~15-20sn sonra tekrar /calib yaz.")
        return jsonify(ok=True), 200

    # ---- set ----
    if text.startswith("/set"):
        try:
            _, key, val = text.split()
            val = int(val)
        except:
            reply("❌ Format: /set thr|hold|cooldown|window değer")
            return jsonify(ok=True), 200

        if key == "thr":
            s["thr"] = clamp(val, 1, 1023)
        elif key == "hold":
            s["hold_ms"] = clamp(val, 1, 10000)
        elif key == "cooldown":
            s["cooldown_s"] = clamp(val, 1, 600)
        elif key in ["window", "window_ms"]:
            s["window_ms"] = clamp(val, 18, 7200)
        else:
            reply("❌ Bilinmeyen parametre")
            return jsonify(ok=True), 200

        reply(f"✅ {key} güncellendi")
        return jsonify(ok=True), 200

    # bilinmeyen komut
    reply("❓ /help yaz")
    return jsonify(ok=True), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
