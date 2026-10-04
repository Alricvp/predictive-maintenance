"""
bench_capture.py - does the REAL signal chain resolve the bearing order?

This is the script that decides whether the project works. It reads a raw
burst from the ESP32 over serial, converts counts to g using the MPU6050's
+-2g scale, and runs the EXACT feature extractor the simulator uses
(imported from machin/sim_faults.py - not a reimplementation). If BPFO does
not appear here on real hardware, the whole pitch is wrong and no amount of
dashboard work will save it.

Usage:
    python bench_capture.py                    # serial auto-detect
    python bench_capture.py --port COM5
    python bench_capture.py --selftest         # no hardware needed
    python bench_capture.py --rpm 1420         # if you know the real speed

Why the ACTUAL sample rate matters more than it looks
-----------------------------------------------------
The firmware prints ACTUAL_FS. We use THAT, not the nominal CAPTURE_FS. If I2C
overruns and the real rate is 870 Hz while the firmware claims 1000, every
frequency axis is wrong by 13% - a real BPFO peak at 89.3 Hz would be plotted
at 78 Hz, we'd look in the wrong place, and we'd wrongly conclude "no fault
order detected". Always trust ACTUAL_FS.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import sim_faults as S  # noqa: E402


def parse_burst(text):
    """Parse the firmware's BURST_BEGIN..BURST_END block."""
    lines = text.splitlines()
    header = {}
    rows = []
    inside = False
    for ln in lines:
        ln = ln.strip()
        if ln == "BURST_BEGIN":
            inside = True
            continue
        if ln == "BURST_END":
            break
        if not inside:
            continue
        if "," not in ln:
            continue
        parts = ln.split(",")
        if parts[0] == "FS":
            header["fs"] = float(parts[1])
        elif parts[0] == "N":
            header["n"] = int(parts[1])
        elif parts[0] == "ACTUAL_FS":
            header["actual_fs"] = float(parts[1])
        elif parts[0].isdigit() or (parts[0] == "-" and parts[0][1:].isdigit()):
            rows.append([int(v) for v in parts[:5]])
    return header, np.asarray(rows, dtype=float)


# MPU-6050 accelerometer at +-2 g range: 16384 LSB per g.
MPU_SENS_2G = 16384.0


def counts_to_g(rows, sens=MPU_SENS_2G):
    """MPU6050 at +-2 g: 16384 LSB/g."""
    return rows[:, 1] / sens, rows[:, 2] / sens, rows[:, 3] / sens


def analyse(x, y, z, fs, rpm=None, label="REAL"):
    """Run the shared extractor. rpm=None lets it estimate; we prefer the
    user-supplied value because a known speed removes the 1X-vs-2X ambiguity
    that broke four features during simulator development."""
    if rpm:
        # Force the same 1X the extractor would otherwise have to find.
        orig = S.estimate_rpm
        S.estimate_rpm = lambda _x, _fs, **_kw: float(rpm)
        try:
            f = S.accel_features(x, y, z, fs=fs)
        finally:
            S.estimate_rpm = orig
    else:
        f = S.accel_features(x, y, z, fs=fs)

    bf = S.bearing_freqs(rpm if rpm else f["rpm"])
    print("\n" + "=" * 70)
    print("%s  -  feature report" % label)
    print("=" * 70)
    print("  sample rate used : %.1f Hz   (Nyquist %.1f Hz)" % (fs, fs / 2))
    print("  samples          : %d  (%.2f s)" % (len(x), len(x) / fs))
    print("  shaft speed      : %.0f RPM  (1X = %.2f Hz)" % (f["rpm"], f["rpm"] / 60))
    print("  RMS              : %.4f g" % f["rms_g"])
    print("  kurtosis         : %.2f" % f["kurtosis"])
    print("\n  harmonic orders (dB above broadband floor):")
    for k in ("s1", "s2", "s3", "s4"):
        print("    %-3s %6.1f dB   %s" % (k, f[k], "<-- HIGH" if f[k] > 25 else ""))
    print("    axial 1X %6.1f dB   %s" % (f["axial_s1"],
                                          "<-- HIGH" if f["axial_s1"] > 25 else ""))
    print("\n  BEARING ORDERS (the ones that matter):")
    for label, key, v in (("BPFO outer race", "BPFO", f["bpfo_db"]),
                          ("BPFI inner race", "BPFI", f["bpfi_db"]),
                          ("BSF  ball spin", "BSF", f["bsf_db"])):
        print("    %-16s %6.1f dB  (target %.1f Hz)  %s"
              % (label, v, bf[key],
                 "<-- RESOLVED" if v > 18 else "not visible"))
    print("\n  classifier verdict : %s" % S.classify(f))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="serial port, e.g. COM5 or /dev/ttyUSB0")
    ap.add_argument("--rpm", type=float, help="known shaft speed (recommended)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the whole chain on synthetic data; no hardware")
    args = ap.parse_args()

    if args.selftest:
        rng = np.random.default_rng(S.RNG_SEED)
        x, y, z = S.synth_accel("bearing", 0.7, rng)
        analyse(x, y, z, S.ACC_FS, label="SELFTEST (synthetic bearing @ sev 0.7)")
        print("\n  selftest OK: if BPFO is RESOLVED above, the analysis path works.")
        print("  If this FAILS on synthetic data, the harness is broken - do not")
        print("  go looking for faults in your fan.")
        return 0

    try:
        import serial
    except ImportError:
        print("pyserial not installed:  pip install pyserial", file=sys.stderr)
        return 2

    port = args.port
    if not port:
        import serial.tools.list_ports as lp
        ports = list(lp.comports())
        if not ports:
            print("No serial ports found. Plug in the ESP32 and retry.", file=sys.stderr)
            return 2
        print("Found ports: %s" % ", ".join(p.device for p in ports))
        port = ports[0].device
    print("Opening %s ..." % port)

    ser = serial.Serial(port, 115200, timeout=1)
    ser.reset_input_buffer()
    time.sleep(1.0)
    ser.write(b"b")          # request one burst
    ser.flush()

    print("Waiting for burst (4096 samples, several seconds)...")
    t0 = time.time()
    chunks = []
    while time.time() - t0 < 120:
        line = ser.readline().decode("ascii", "ignore")
        if not line:
            continue
        chunks.append(line)
        if "BURST_END" in line:
            break
    ser.close()

    header, rows = parse_burst("".join(chunks))
    if rows.size == 0:
        print("No samples parsed. Did the firmware halt on 'no MPU found'?",
              file=sys.stderr)
        return 1

    # Prefer the firmware's measured rate.
    fs = header.get("actual_fs") or header.get("fs") or S.ACC_FS
    print("\nParsed %d samples. Firmware claims fs=%s, achieved %s"
          % (len(rows), header.get("fs"), header.get("actual_fs")))
    if header.get("actual_fs") and abs(header["actual_fs"] - header.get("fs", fs)) > 0.05 * fs:
        print("  !! Sample-rate mismatch: I2C cannot keep up. Lower CAPTURE_FS")
        print("     or shorten the wires, else every frequency is mis-scaled.")

    x, y, z = counts_to_g(rows)
    analyse(x, y, z, fs, rpm=args.rpm, label="REAL HARDWARE")

    f = S.accel_features(x, y, z, fs=fs)
    print("\n" + "=" * 70)
    print("NEXT STEP")
    print("=" * 70)
    if f["bpfo_db"] > 18 or f["bpfi_db"] > 18:
        print("  Bearing order IS resolvable on real hardware. The thesis holds.")
        print("  Next: run the same command on a machine with a known loose base,")
        print("  then with a bearing fault, and watch BPFO grow.")
    else:
        print("  Bearing order NOT resolved yet. Do not conclude the fault is")
        print("  absent - check, in this order:")
        print("    1. Is the sensor RIGIDLY coupled? A loose MPU reads gravity.")
        print("    2. Is ACTUAL_FS close to 1000 Hz?")
        print("    3. Is the machine actually at the speed you think? Try --rpm.")
        print("    4. Widen DLPF (CONFIG 3 -> 2 -> 1) at the cost of noise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())