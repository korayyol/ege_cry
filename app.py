import os, time, threading
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ["8502495764:AAGb87crRWTPoqgQU6xy_oe4Y0O9VQNB-TU"]
CHAT_ID   = os.environ["7521725282"]
SECRET    = os.environ.get("SECRET", "1234")  # webhook koruması

PING_TIMEOUT_S = 260        # 260 sn ping yoksa uyar
PING_WARN_COOLDOWN_S = 600  # 10 dk’da bir tekrar uyar
CHECK_PERIOD_S = 10

DEFAULTS = dict(
    armed=True,
    thr=35,
    hold_ms=900,
    cooldown_s=30,
    last_ping=0,
    last_ping_warn=0,
    calib_pending_s=0,  # >0 ise ESP bir sonraki ping’de kalibrasyon yapacak
)

state = {}  # dev -> settings

def ensure_dev(dev: str):
    if dev not in state:
        state[dev] = dict(DEFAULTS)

def tg_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)

def watchdog():
    while True:
        now = time.time()
        for dev, s in list(state.items()):
            lp = s.get("last_ping", 0)
            if lp and (now - lp) > PING_TIMEOUT_S:
                if (now - s.get("last_ping_warn", 0)) > PING_WARN_COOLDOWN_S:
                    s["last_ping_warn"] = now
                    tg_send(f"⚠️ {dev}: {PING_TIMEOUT_S} sn ping yok. Bağlantı/besleme kontrol et.")
        time.sleep(CHECK_PERIOD_S)

threading.Thread(target=watchdog, daemon=True).start()

@app.get("/ping")
def ping():
    dev = request.args.get("dev", "baby1")
    ensure_dev(dev)
    s = state[dev]
    s["last_ping"] = time.time()

    resp = {
        "ok": True,
        "dev": dev,
        "armed": s["armed"],
        "thr": s["thr"],
        "hold_ms": s["hold_ms"],
        "cooldown_s": s["cooldown_s"],
        "calib_s": s["calib_pending_s"],  # ESP bunu görürse calib yapacak
        "server_time": int(time.time()),
    }

    # calib komutu “tek sefer” çalışsın: ping cevabıyla birlikte sıfırla
    s["calib_pending_s"] = 0
    return jsonify(resp)

@app.post("/event")
def event():
    data = request.get_json(force=True)
    dev = data.get("dev", "baby1")
    ensure_dev(dev)

    typ = data.get("type", "unknown")

    if typ == "cry":
        rms = data.get("rms", None)
        tg_send(f"👶 {dev}: Ağlama algılandı! (rms={rms})")
        return jsonify({"ok": True})

    if typ == "calib":
        noise_rms = data.get("noise_rms")
        max_rms   = data.get("max_rms")
        thr_sug   = data.get("thr_suggest")
        tg_send(
            f"📏 {dev} Kalibrasyon:\n"
            f"noise_rms={noise_rms}\n"
            f"max_rms={max_rms}\n"
            f"önerilen thr={thr_sug}\n\n"
            f"Uygulamak için: /set thr {thr_sug}"
        )
        return jsonify({"ok": True})

    return jsonify({"ok": True})

@app.post("/telegram")
def telegram():
    # basit shared secret kontrolü
    sec = request.args.get("secret", "")
    if sec != SECRET:
        return ("forbidden", 403)

    upd = request.get_json(force=True)
    msg = (upd.get("message") or {}).get("text", "") or ""
    msg = msg.strip()

    dev = "baby1"
    ensure_dev(dev)
    s = state[dev]

    def reply(t): tg_send(t)

    if msg in ["/start", "/help"]:
        reply(
            "Komutlar:\n"
            "/on\n/off\n/status\n"
            "/set thr 35\n/set hold 900\n/set cooldown 30\n"
            "/calib 30  (ortam ölçümü)\n"
        )
        return jsonify(ok=True)

    if msg == "/on":
        s["armed"] = True
        reply("✅ Sistem AÇIK")
        return jsonify(ok=True)

    if msg == "/off":
        s["armed"] = False
        reply("⛔ Sistem KAPALI (ses ölçümü durdu, ping devam)")
        return jsonify(ok=True)

    if msg == "/status":
        reply(
            f"Durum ({dev}):\n"
            f"armed={s['armed']}\n"
            f"thr={s['thr']}\n"
            f"hold_ms={s['hold_ms']}\n"
            f"cooldown_s={s['cooldown_s']}\n"
            f"last_ping={int(s['last_ping'])}"
        )
        return jsonify(ok=True)

    if msg.startswith("/set"):
        parts = msg.split()
        if len(parts) == 3:
            key, val = parts[1], parts[2]
            try:
                ival = int(val)
            except:
                reply("❌ Değer sayı olmalı.")
                return jsonify(ok=True)

            if key == "thr":
                s["thr"] = max(1, min(300, ival))
                reply(f"✅ thr={s['thr']}")
            elif key == "hold":
                s["hold_ms"] = max(100, min(5000, ival))
                reply(f"✅ hold_ms={s['hold_ms']}")
            elif key == "cooldown":
                s["cooldown_s"] = max(1, min(3600, ival))
                reply(f"✅ cooldown_s={s['cooldown_s']}")
            else:
                reply("❌ Parametre: thr/hold/cooldown")
            return jsonify(ok=True)

    if msg.startswith("/calib"):
        parts = msg.split()
        if len(parts) == 2:
            try:
                secs = int(parts[1])
            except:
                reply("❌ Örn: /calib 30")
                return jsonify(ok=True)

            secs = max(5, min(120, secs))
            s["calib_pending_s"] = secs
            reply(f"🕒 Kalibrasyon istendi: {secs} sn. (ESP en geç 30 sn içinde başlayacak)")
            return jsonify(ok=True)

    reply("❓ Anlamadım. /help yaz.")
    return jsonify(ok=True)

@app.get("/")
def home():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
