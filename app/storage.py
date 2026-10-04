"""
storage.py - survive restarts.

WHY THIS EXISTS
---------------
On a free tier the process is killed whenever the service scales to zero. With
everything in RAM, a judge opening the URL sees an empty dashboard and an empty
alert history - the evidence of your demo is gone. That is the single worst
failure mode you can have in front of someone evaluating the project.

So alerts, per-device state and the spectrum history are written to disk as they
happen and reloaded at startup.

HONEST LIMITATION
-----------------
Koyeb's free Instance has EPHEMERAL local disk. This survives a process
restart, but NOT the recreation of the instance itself. So it is a big
improvement, not a guarantee. Anything that must survive permanently needs a
real database - out of scope for a hackathon prototype, but do not claim
otherwise to a judge.
"""

import json
import os
import threading
from datetime import datetime

DATA_DIR = os.environ.get("PDM_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
ALERT_FILE = os.path.join(DATA_DIR, "alerts.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

_lock = threading.Lock()
_dirty = {"alerts": False, "state": False}


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _atomic_write(path, payload):
    """Write to a temp file then rename.

    os.replace() is atomic on POSIX and Windows. Without this, a scale-to-zero
    kill mid-write leaves a truncated JSON file, and the next startup dies on a
    parse error - losing everything. That is exactly the failure we are trying
    to prevent.
    """
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # A corrupt file must never take the whole service down.
        return default
    except Exception:
        return default


def load_alerts(max_alerts=100):
    data = _read(ALERT_FILE, [])
    return data if isinstance(data, list) else []


def save_alerts(alerts, max_alerts=100):
    _atomic_write(ALERT_FILE, alerts[:max_alerts])


def load_state():
    data = _read(STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_state(state):
    _atomic_write(STATE_FILE, state)


def mark_dirty(what):
    with _lock:
        _dirty[what] = True


def is_dirty(what):
    with _lock:
        return _dirty.get(what, False)


def clear_dirty(what):
    with _lock:
        _dirty[what] = False


def flush(alerts, state):
    """Persist whatever changed. Called after each write and on shutdown."""
    wrote = []
    if is_dirty("alerts"):
        try:
            save_alerts(alerts)
            clear_dirty("alerts")
            wrote.append("alerts")
        except Exception as e:
            print("[STORAGE] alert persist failed: %s" % e)
    if is_dirty("state"):
        try:
            save_state(state)
            clear_dirty("state")
            wrote.append("state")
        except Exception as e:
            print("[STORAGE] state persist failed: %s" % e)
    return wrote


def startup_banner():
    _ensure_dir()
    print("[STORAGE] data dir: %s" % DATA_DIR)
    print("[STORAGE] loaded %d alerts, state keys: %s"
          % (len(load_alerts()), ", ".join(load_state().keys()) or "none"))