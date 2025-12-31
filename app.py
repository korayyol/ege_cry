import os
import json
import time
import threading
from typing import Any, Dict
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# Telegram
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

DEFAULT_STATE: Dict[str, Any] = {
    # Sistem modu (UI’dan setlenir, ESP bunu config’ten okur)
    "armed": False,

    # RMS / event ayarları
    "threshold_rms": 80.0,
    "hold_ms": 800,
    "cooldown_ms": 8000,

    # Dinamik haberleşme ayarları (ESP bunu config’ten okur)
    "report_ms_armed": 10000,       # 10 sn
    "report_ms_disarmed": 150000,   # 150 sn
    "wifi_sleep_armed": "none",     # "none" | "light"
    "wifi_sleep_disarmed": "light", # "none" | "light"

    # Dinamik offline alarm eşikleri (server watchdog kullanır)
    "offline_timeout_s_armed": 90,      # 60-120 öneri
    "offline_timeout_s_disarmed": 300,  # 300 sn

    # Kalibrasyon
    "calibration": {
        "pending": False,
        "duration_ms": 5000,
        "baseline_rms": 0.0,
        "ts": 0
    },

    # Cihazdan gelen son durum
    "device": {
        "last_seen_ts": 0,
        "rms": 0.0,
        "raw": 0,
        "triggered": False,
        "esp_armed": False,
        "cooldown_active": False,
        "uptime_ms": 0,
        "ip": ""
    },

    # Bağlantı alarm state (edge-trigger)
    "conn": {
        "is_offline": True,          # ilk açılışta offline varsayalım
        "offline_alert_sent": False,
        "last_offline_ts": 0,
        "last_online_ts": 0
    }
}

STATE = None
_state_lock = threading.Lock()
_watchdog_started = False


def _auth_ok(req) -> bool:
    if not API_KEY:
        return True
    got = req.headers.get("X-API-KEY", "").strip()
    return got == API_KEY


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


def deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_update(dst[k], v)
        else:
            dst[k] = v


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = json.loads(json.dumps(DEFAULT_STATE))
        deep_update(merged, data)
        return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_state() -> Dict[str, Any]:
    global STATE
    with _state_lock:
        if STATE is None:
            STATE = load_state()
        return STATE


def current_offline_timeout_s(st: Dict[str, Any]) -> int:
    # Offline eşik, sistemin ARM durumuna göre değişir
    if bool(st.get("armed", False)):
        return int(st.get("offline_timeout_s_armed", 90))
    return int(st.get("offline_timeout_s_disarmed", 300))


def watchdog_loop():
    """
    10 saniyede bir last_seen kontrol eder.
    age > timeout ise offline telegram 1 kere atar.
    geri gelince online telegram atar.
    """
    while True:
        try:
            with _state_lock:
                st = get_state()
                now = int(time.time())
                last_seen = int(st["device"].get("last_seen_ts", 0))
                age = now - last_seen if last_seen else 10**9

                timeout_s = current_offline_timeout_s(st)
                is_offline_now = age > timeout_s

                conn = st["conn"]

                # OFFLINE'e geçiş
                if is_offline_now and not conn.get("is_offline", True):
                    conn["is_offline"] = True
                    conn["last_offline_ts"] = now
                    conn["offline_alert_sent"] = False
                    save_state(st)

                # OFFLINE alarmı (1 kere)
                if is_offline_now and not conn.get("offline_alert_sent", False):
                    msg = (
                        "⚠️ Cihaz bağlantısı koptu.\n"
                        f"Mode armed: {bool(st.get('armed', False))}\n"
                        f"Son görülme: {age} sn önce\n"
                        f"Offline eşiği: {timeout_s} sn"
                    )
                    send_telegram(msg)
                    conn["offline_alert_sent"] = True
                    save_state(st)

                # ONLINE'a dönüş
                if (not is_offline_now) and conn.get("is_offline", True):
                    conn["is_offline"] = False
                    conn["last_online_ts"] = now
                    conn["offline_alert_sent"] = False
                    save_state(st)

                    msg = f"✅ Cihaz tekrar online.\nSon RMS: {st['device'].get('rms', 0):.1f}"
                    send_telegram(msg)

        except Exception as e:
            print("Watchdog error:", e)

        time.sleep(10)


def start_watchdog_once():
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    threading.Thread(target=watchdog_loop, daemon=True).start()


start_watchdog_once()


@app.get("/")
def home():
    return "ok", 200


# ---------- APP/UI ----------
@app.get("/api/status")
def api_status():
    if not _auth_ok(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    st = get_state()
    now = int(time.time())
    last_seen = st["device"]["last_seen_ts"]
    age = (now - last_seen) if last_seen else None
    timeout_s = current_offline_timeout_s(st)

    return jsonify({
        "ok": True,
        "armed": st["armed"],
        "threshold_rms": st["threshold_rms"],
        "hold_ms": st["hold_ms"],
        "cooldown_ms": st["cooldown_ms"],

        "report_ms_armed": st["report_ms_armed"],
        "report_ms_disarmed": st["report_ms_disarmed"],
        "wifi_sleep_armed": st["wifi_sleep_armed"],
        "wifi_sleep_disarmed": st["wifi_sleep_disarmed"],

        "offline_timeout_s_armed": st["offline_timeout_s_armed"],
        "offline_timeout_s_disarmed": st["offline_timeout_s_disarmed"],
        "offline_timeout_s_current": timeout_s,

        "calibration": st["calibration"],
        "device": st["device"],
        "conn": st["conn"],
        "device_last_seen_age_s": age
    })


@app.post("/api/arm")
def api_arm():
    if not _auth_ok(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    if "armed" not in body:
        return jsonify({"ok": False, "error": "missing 'armed'"}), 400

    with _state_lock:
        st = get_state()
        st["armed"] = bool(body["armed"])
        save_state(st)

    return jsonify({"ok": True, "armed": get_state()["armed"]})


@app.post("/api/config")
def api_config_set():
    """
    UI’dan her şeyi set edebileceğin endpoint.
    """
    if not _auth_ok(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}

    def clamp_int(v, lo, hi):
        v = int(v)
        return max(lo, min(hi, v))

    with _state_lock:
        st = get_state()

        # RMS event config
        if "threshold_rms" in body:
            st["threshold_rms"] = float(body["threshold_rms"])
        if "hold_ms" in body:
            st["hold_ms"] = clamp_int(body["hold_ms"], 50, 60000)
        if "cooldown_ms" in body:
            st["cooldown_ms"] = clamp_int(body["cooldown_ms"], 0, 600000)

        # ESP comm config
        if "report_ms_armed" in body:
            st["report_ms_armed"] = clamp_int(body["report_ms_armed"], 1000, 300000)
        if "report_ms_disarmed" in body:
            st["report_ms_disarmed"] = clamp_int(body["report_ms_disarmed"], 5000, 900000)

        if "wifi_sleep_armed" in body:
            val = str(body["wifi_sleep_armed"]).lower()
            if val in ("none", "light"):
                st["wifi_sleep_armed"] = val
        if "wifi_sleep_disarmed" in body:
            val = str(body["wifi_sleep_disarmed"]).lower()
            if val in ("none", "light"):
                st["wifi_sleep_disarmed"] = val

        # OFFLINE thresholds (işte istediğin kısım)
        if "offline_timeout_s_armed" in body:
            st["offline_timeout_s_armed"] = clamp_int(body["offline_timeout_s_armed"], 30, 600)
        if "offline_timeout_s_disarmed" in body:
            st["offline_timeout_s_disarmed"] = clamp_int(body["offline_timeout_s_disarmed"], 60, 3600)

        save_state(st)

    st = get_state()
    return jsonify({"ok": True, "config": {
        "armed": st["armed"],
        "threshold_rms": st["threshold_rms"],
        "hold_ms": st["hold_ms"],
        "cooldown_ms": st["cooldown_ms"],

        "report_ms_armed": st["report_ms_armed"],
        "report_ms_disarmed": st["report_ms_disarmed"],
        "wifi_sleep_armed": st["wifi_sleep_armed"],
        "wifi_sleep_disarmed": st["wifi_sleep_disarmed"],

        "offline_timeout_s_armed": st["offline_timeout_s_armed"],
        "offline_timeout_s_disarmed": st["offline_timeout_s_disarmed"],
        "offline_timeout_s_current": current_offline_timeout_s(st),
    }})


@app.post("/api/calibrate/start")
def api_calibrate_start():
    if not _auth_ok(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    duration_ms = int(body.get("duration_ms", get_state()["calibration"]["duration_ms"]))

    with _state_lock:
        st = get_state()
        st["calibration"]["pending"] = True
        st["calibration"]["duration_ms"] = max(500, duration_ms)
        save_state(st)

    return jsonify({"ok": True, "calibration": get_state()["calibration"]})


# ---------- DEVICE ----------
@app.get("/device/config")
def device_config():
    if not _auth_ok(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or ""
    with _state_lock:
        st = get_state()
        st["device"]["ip"] = ip
        save_state(st)

    st = get_state()
    return jsonify({
        "ok": True,
        "armed": st["armed"],
        "threshold_rms": st["threshold_rms"],
        "hold_ms": st["hold_ms"],
        "cooldown_ms": st["cooldown_ms"],

        # ESP davranışını dinamik ayarlıyoruz
        "report_ms_armed": st["report_ms_armed"],
        "report_ms_disarmed": st["report_ms_disarmed"],
        "wifi_sleep_armed": st["wifi_sleep_armed"],
        "wifi_sleep_disarmed": st["wifi_sleep_disarmed"],

        "calibration": {
            "pending": st["calibration"]["pending"],
            "duration_ms": st["calibration"]["duration_ms"],
            "baseline_rms": st["calibration"]["baseline_rms"],
        }
    })


@app.post("/device/report")
def device_report():
    if not _auth_ok(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    now = int(time.time())

    with _state_lock:
        st = get_state()
        dev = st["device"]
        dev["last_seen_ts"] = now
        dev["rms"] = float(body.get("rms", dev.get("rms", 0.0)))
        dev["raw"] = int(body.get("raw", dev.get("raw", 0)))
        dev["triggered"] = bool(body.get("triggered", dev.get("triggered", False)))
        dev["esp_armed"] = bool(body.get("armed", dev.get("esp_armed", False)))
        dev["cooldown_active"] = bool(body.get("cooldown_active", dev.get("cooldown_active", False)))
        dev["uptime_ms"] = int(body.get("uptime_ms", dev.get("uptime_ms", 0)))

        # kalibrasyon sonucu
        if "baseline_rms" in body:
            st["calibration"]["baseline_rms"] = float(body["baseline_rms"])
            st["calibration"]["ts"] = now
            st["calibration"]["pending"] = False

        # report geldiyse online say
        conn = st["conn"]
        if conn.get("is_offline", True):
            conn["is_offline"] = False
            conn["last_online_ts"] = now
            conn["offline_alert_sent"] = False

        save_state(st)

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
