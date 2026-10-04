"""
Predictive Maintenance - FastAPI server (standalone repository)

Self-contained: this repository does NOT depend on the Landsafe landslide
project and shares no code with it. Both can be deployed independently.

What it does
------------
Receives vibration feature readings from a sensor node (or the simulator),
classifies the fault, and pushes live updates to any number of browsers over
WebSocket. Alert history and device state persist to disk so evidence survives
a scale-to-zero restart.

Deployment note (Render and most PaaS): the port comes from the PORT env var and
the bind address MUST be 0.0.0.0. Binding 127.0.0.1 inside a container makes
every external request fail with connection refused.
"""

import os
import json
import urllib.request
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import storage

HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Predictive Maintenance", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- In-memory working set (mirrored to disk) ----
sensor_data = []
MAX_READINGS = 900
spectrum_history = []
MAX_SPECTRUM = 120
connected_clients = []
sms_recipients = []

# Reload persisted state at import so a restarted process comes back populated.
storage.startup_banner()
alert_log = storage.load_alerts()
device_state = storage.load_state()
if alert_log:
    print("[STARTUP] restored %d prior alerts" % len(alert_log))


class VibrationReading(BaseModel):
    device_id: str = "fan-01"
    rpm: float = 1500.0
    rms_g: float = 0.0
    kurtosis: float = 3.0
    s1: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    s4: float = 0.0
    axial_s1: float = 0.0
    bpfo_db: float = 0.0
    bpfi_db: float = 0.0
    mic_db: float = 0.0
    mic_severity: float | None = None
    acc_severity: float | None = None
    verdict: str = "healthy"
    temp_c: float | None = None
    ip: str = ""
    spectrum: list[float] | None = None


def health_index(r: dict) -> int:
    """0-100 health score. Weighted by how diagnostic each signal is, not by
    raw dB, so the score starts moving before the machine feels different."""
    penalty = 0.0
    penalty += max(0.0, r.get("s1", 0) - 45) * 0.5
    penalty += max(0.0, r.get("s2", 0) - 14) * 0.8
    penalty += max(0.0, r.get("s3", 0) - 14) * 1.2
    penalty += max(0.0, r.get("s4", 0) - 14) * 1.0
    penalty += max(0.0, r.get("axial_s1", 0) - 40) * 0.6
    penalty += max(0.0, r.get("bpfo_db", 0) - 18) * 1.0
    penalty += max(0.0, r.get("bpfi_db", 0) - 18) * 1.0
    penalty += max(0.0, r.get("mic_db", 0) - 15) * 1.6   # ultrasonic, weighted highest
    return int(max(0, min(100, 100.0 - penalty)))


ADVICE = {
    "healthy": ("HEALTHY", "All orders within calibrated baseline."),
    "imbalance": ("IMBALANCE",
                  "Radial 1X elevated. Check blade fouling, missing blade weights, "
                  "or a bent impeller. Re-balance after repair."),
    "misalignment": ("MISALIGNMENT",
                    "Axial 1X and 2X elevated together. Re-align shafts and check "
                    "soft foot / pipe strain."),
    "looseness": ("LOOSENESS",
                  "Harmonic series (3X, 4X) elevated. Re-torque base bolts and check "
                  "structural mounts for movement."),
    "bearing": ("BEARING FAULT",
                "Non-integer fault order present in the resonance envelope. Replace "
                "the bearing this shift - the spall is already audible in the "
                "ultrasonic channel."),
}


def severity_note(r: dict):
    """If the mic saw it before the accelerometer, say so. This is the claim."""
    mic, acc = r.get("mic_severity"), r.get("acc_severity")
    if mic is None or acc is None or mic >= acc:
        return None
    return ("ULTRASONIC EARLY WARNING",
            "Mic detected at severity %.2f; accelerometer only at %.2f. "
            "Lead time %.0f%% of the degradation path." % (mic, acc, 100 * (acc - mic)))


def send_sms(phone: str, message: str) -> dict:
    """SMS via TextBelt.

    LIMITATION, stated plainly: the free key allows ONE SMS per day per
    number. Fine for a demo, useless in production. To change provider, edit
    this function alone.
    """
    phone = phone.strip()
    if not phone.startswith("+"):
        if phone.startswith("0"):
            phone = "+91" + phone[1:]
        elif len(phone) == 10:
            phone = "+91" + phone
        else:
            phone = "+" + phone
    try:
        import urllib.parse
        data = urllib.parse.urlencode({
            "phone": phone, "message": message[:160], "key": "textbelt"}).encode()
        req = urllib.request.Request(
            "https://textbelt.com/text", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        result = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        return {"sent": bool(result.get("success")), "via": "TextBelt"}
    except Exception as e:
        print("[SMS] failed: %s" % e)
        return {"sent": False, "via": "fallback", "error": str(e)}


async def broadcast(payload: dict):
    message = json.dumps(payload)
    dead = []
    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            dead.append(client)
    for c in dead:
        if c in connected_clients:
            connected_clients.remove(c)


@app.post("/api/vibration")
async def receive_vibration(reading: VibrationReading):
    global alert_log
    entry = reading.model_dump()
    entry["timestamp"] = datetime.now().isoformat()
    entry["health"] = health_index(entry)

    prev = sensor_data[-1]["verdict"] if sensor_data else None
    sensor_data.append(entry)
    if len(sensor_data) > MAX_READINGS:
        sensor_data.pop(0)

    if entry.get("spectrum"):
        spectrum_history.append(entry["spectrum"])
        del spectrum_history[:-MAX_SPECTRUM]

    # Edge-triggered: alert on a NEW fault only, not on every reading.
    if entry["verdict"] not in ("healthy",) and entry["verdict"] != prev:
        title, advice = ADVICE.get(entry["verdict"], ("ALERT", ""))
        alert = {
            "type": "fault", "verdict": entry["verdict"], "title": title,
            "advice": advice, "health": entry["health"],
            "device": entry["device_id"],
            "mic_severity": entry.get("mic_severity"),
            "acc_severity": entry.get("acc_severity"),
            "early_warning": severity_note(entry),
            "timestamp": entry["timestamp"],
        }
        alert_log.insert(0, alert)
        del alert_log[100:]
        storage.mark_dirty("alerts")
        await broadcast({"type": "alert", "data": alert})
        print("[ALERT] %s (%s) health %d" % (title, entry["device_id"], entry["health"]))
        for phone in list(sms_recipients):
            send_sms(phone, "[%s] %s: %s (health %d/100)"
                     % (entry["device_id"], title, advice, entry["health"]))

    device_state[entry["device_id"]] = {
        "device_id": entry["device_id"], "verdict": entry["verdict"],
        "health": entry["health"], "rpm": entry["rpm"],
        "updated": entry["timestamp"],
    }
    storage.mark_dirty("state")
    wrote = storage.flush(alert_log, device_state)
    if wrote:
        print("[STORAGE] persisted %s" % ", ".join(wrote))

    await broadcast({"type": "sensor", "data": entry})
    return {"ok": True, "health": entry["health"], "verdict": entry["verdict"]}


@app.get("/api/latest")
async def get_latest():
    return {"data": sensor_data[-1] if sensor_data else None}


@app.get("/api/history")
async def get_history():
    return {"data": sensor_data}


@app.get("/api/alerts")
async def get_alerts():
    return {"data": alert_log}


@app.get("/api/devices")
async def get_devices():
    return {"data": list(device_state.values())}


@app.get("/api/spectrum")
async def get_spectrum():
    return {"data": spectrum_history}


@app.get("/api/stats")
async def get_stats():
    if not sensor_data:
        return {"data": {"readings": 0, "alerts": len(alert_log)}}
    h = [r["health"] for r in sensor_data]
    return {"data": {"min_health": min(h), "avg_health": sum(h) / len(h),
                     "alerts": len(alert_log), "readings": len(sensor_data),
                     "devices": len(device_state)}}


@app.post("/api/sms/register")
async def register_sms(payload: dict):
    phone = payload.get("phone", "")
    if phone and phone not in sms_recipients:
        sms_recipients.append(phone)
    return {"ok": True, "recipients": sms_recipients}


@app.post("/api/sms/send")
async def sms_send(payload: dict):
    phone, message = payload.get("phone", ""), payload.get("message", "")
    if phone and phone not in sms_recipients:
        sms_recipients.append(phone)
    return {"ok": True, **send_sms(phone, message)}


@app.get("/api/sms/recipients")
async def get_sms_recipients():
    return {"recipients": sms_recipients}


@app.get("/api/config")
async def get_config():
    return {"data": {"sms_recipients": len(sms_recipients),
                     "readings": len(sensor_data), "devices": len(device_state)}}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        if sensor_data:
            await websocket.send_text(json.dumps({"type": "sensor",
                                                  "data": sensor_data[-1]}))
        if alert_log:
            await websocket.send_text(json.dumps({"type": "alerts",
                                                  "data": alert_log}))
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


app.mount("/media", StaticFiles(directory=HERE), name="media")


@app.get("/sw.js")
async def serve_sw():
    return FileResponse(os.path.join(HERE, "sw.js"),
                        media_type="application/javascript")


@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(os.path.join(HERE, "manifest.json"),
                        media_type="application/json")


@app.get("/favicon.ico")
async def serve_favicon():
    # Browsers probe /favicon.ico even when a link icon is declared.
    return FileResponse(os.path.join(HERE, "favicon.png"),
                        media_type="image/png")


@app.get("/sg-logo.png")
async def serve_logo():
    return FileResponse(os.path.join(HERE, "sg-logo.png"),
                        media_type="image/png")


@app.get("/health")
async def health():
    return {"ok": True, "readings": len(sensor_data),
            "alerts": len(alert_log), "clients": len(connected_clients)}


@app.get("/")
async def serve_dashboard():
    return FileResponse(os.path.join(HERE, "dashboard.html"),
                        media_type="text/html")


if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 is mandatory in a container. PORT comes from the PaaS.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))