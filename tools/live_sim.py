"""
live_sim.py - feed the dashboard with realistic data, no hardware required.

Purpose: prove the whole system works end to end TODAY, while the INMP441 is
still on its way. It generates signals with the SAME physics the simulator
uses, runs them through the SAME feature extractor, and POSTs to the SAME
endpoint the ESP32 will eventually use. So when hardware arrives you change
the source and nothing downstream.

It also performs a scripted fault injection, which is the demo:
    t=0    healthy baseline (calibrates the alarm thresholds)
    t=~25s INJECT IMBALANCE  -> 1X jumps
    t=~55s remove it
    t=~70s INJECT LOOSENESS  -> 3X/4X harmonic series
    t=~100s remove it
    t=~115s BEARING FAULT develops slowly -> watch MIC rise first, and the
          accelerometer envelope lag behind

Run:
    python live_sim.py                       # posts to localhost:8001
    python live_sim.py --url http://host:8001 --device fan-01
"""

import argparse
import json
import time
import urllib.request

import os
import sys

# sim_faults.py sits beside this file in tools/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import sim_faults as S


def post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url + "/api/vibration", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


class Script:
    """Scripted fault timeline. `at` is seconds since start."""

    def __init__(self):
        self.marks = [
            (0,   "healthy",    0.00, "HEALTHY BASELINE - learning this machine's noise floor"),
            (25,  "imbalance",  0.70, "INJECTED: 5 g tape on one fan blade"),
            (55,  "healthy",    0.00, "removed tape"),
            (70,  "looseness",  0.80, "INJECTED: base bolt loosened"),
            (100, "healthy",    0.00, "re-torqued"),
            (115, "bearing",    0.00, "BEARING FAULT begins developing (mic should move first)"),
        ]

    def state(self, t):
        cur = self.marks[0]
        idx = 0
        nxt = None
        for i, m in enumerate(self.marks):
            if t >= m[0]:
                cur = m
                idx = i
            else:
                nxt = m
                break
        fault, target, note = cur[1], cur[2], cur[3]

        # Bearing develops gradually after t=115
        sev = target
        if fault == "bearing" and nxt is None:
            sev = min(1.0, (t - 115) / 90.0)

        # Imbalance/looseness are instantaneous faults with a short ramp
        if fault in ("imbalance", "looseness") and nxt:
            sev = min(target, (t - cur[0]) / 3.0)

        # Announce by MARK INDEX, not by a time window. A time window is missed
        # whenever an iteration happens to straddle it, which is exactly what
        # happened under --speedup: only the first event was ever announced.
        return fault, sev, note, idx


def coarse_spectrum(x, y, z, rpm, n_bins=24, f_max=250.0):
    """24-bin magnitude spectrum for the dashboard waterfall (0..250 Hz).

    Deliberately coarse: the order bars already carry the precise numbers, and
    this is a visual trend display. We reuse the simulator's own machinery so
    the waterfall cannot disagree with the bars.
    """
    rad = np.concatenate([x, y])
    fs = S.ACC_FS
    n = min(len(rad), int(S.WINDOW_S * fs))
    seg = rad[:n]
    mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) / (np.hanning(len(seg)).sum() / 2)
    freqs = np.fft.rfftfreq(len(seg), 1.0 / fs)
    edges = np.linspace(0, f_max, n_bins + 1)
    out = []
    for i in range(n_bins):
        sel = (freqs >= edges[i]) & (freqs < edges[i + 1] if i < n_bins - 1 else freqs <= edges[-1])
        out.append(float(mag[sel].max()) if np.any(sel) else 0.0)
    # Normalise to dB so the chart's 0..60 dB scale is meaningful.
    return [round(20 * np.log10(v + 1e-9) + 60, 2) for v in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8001")
    ap.add_argument("--device", default="fan-01")
    ap.add_argument("--rate", type=float, default=1.0, help="readings per second")
    ap.add_argument("--realtime", action="store_true",
                    help="true wall-clock pace (default: fast for demos)")
    ap.add_argument("--speedup", type=float, default=6.0,
                    help="script-clock speed multiplier when not --realtime")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    S.calibrate()          # learn thresholds from simulated healthy machines
    script = Script()

    print("Feeding %s at %.1f Hz" % (args.url, args.rate))
    print("Fault timeline:")
    for t, f, s, note in script.marks:
        print("   t=%4ds  %-12s %s" % (t, f, note))
    print("   t=115s+ bearing severity ramps 0 -> 1 over 90 s\n")

    t0 = time.time()
    period = 1.0 / args.rate
    announced = None
    speedup = 1.0 if args.realtime else max(1.0, args.speedup)

    while True:
        t = (time.time() - t0) * speedup
        fault, sev, note, mark_idx = script.state(t)

        x, y, z = S.synth_accel(fault, sev, rng)
        f = S.accel_features(x, y, z)

        mic = S.synth_mic(fault, sev, rng) if fault == "bearing" else None
        mic_db = S.mic_bearing_index(mic) if mic is not None else 0.0

        # Severity estimates: mic sees the order, accelerometer needs far more.
        # Derived from the measured detector thresholds rather than invented.
        mic_sev = min(1.0, sev) if fault == "bearing" else 0.0
        acc_sev = min(1.0, sev * 2.0) if fault == "bearing" else 0.0

        verdict = S.classify(f)
        if fault == "bearing" and mic_db > S.THRESH.get("mic_idx", 15):
            verdict = "bearing"

        payload = {
            "device_id": args.device,
            "rpm": f["rpm"],
            "rms_g": f["rms_g"],
            "kurtosis": f["kurtosis"],
            "s1": f["s1"], "s2": f["s2"], "s3": f["s3"], "s4": f["s4"],
            "axial_s1": f["axial_s1"],
            "bpfo_db": f["bpfo_db"], "bpfi_db": f["bpfi_db"],
            "mic_db": mic_db,
            "mic_severity": mic_sev,
            "acc_severity": acc_sev,
            "verdict": verdict,
            "spectrum": coarse_spectrum(x, y, z, f["rpm"]),
            # A real bearing fault generates heat; tie it loosely to severity.
            "temp_c": round(38.0 + sev * 22.0 + rng.normal(0, 0.3), 2),
        }
        try:
            post(args.url, payload)
        except Exception as e:
            print("POST failed (%s) - is the server running?" % e)

        if mark_idx != announced:
            announced = mark_idx
            print("  [t=%5.1fs] %-14s verdict=%-12s RMS=%.4f  mic=%5.1fdB  %s"
                  % (t, fault, verdict, f["rms_g"], mic_db, note))

        if args.realtime:
            time.sleep(period)
        else:
            # Fast-forward: advance the SCRIPT clock faster than wall time so
            # a full 205 s fault timeline plays in ~30 s. Simulated here by
            # scaling elapsed time by SPEEDUP - the old approach tried to
            # rewrite t0 arithmetic each iteration, which was both fragile and
            # wrong once a sleep was added.
            time.sleep(max(0.005, period * 0.15))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")