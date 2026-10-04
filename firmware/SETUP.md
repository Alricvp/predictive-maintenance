# Predictive Maintenance Node — Setup & First Run

**Goal of the first session:** flash the ESP32, capture 4 seconds of real vibration
from your fan, and find out whether the **bearing order (BPFO ≈ 89 Hz at 1500 RPM) is
actually visible**. That single result decides whether this project works.

Everything here uses parts you already own. The INMP441 microphone comes later.

---

## 1. Wiring

| Module | Pin | ESP32 |
|---|---|---|
| MPU6050 | VCC | 3V3 |
| | GND | GND |
| | SDA | GPIO 21 |
| | SCL | GPIO 22 |
| DS18B20 *(optional)* | VCC | 3V3 |
| | GND | GND |
| | DATA | GPIO 4 **+ 4.7 kΩ pull-up to 3V3** |

Same SDA/SCL pins as your Landsafe node, so the wiring is familiar. **ADDR is 0** on most
breakouts, so no AD0 jumper.

### ⚠️ The one thing that differs from Landsafe: sample rate

`tilt_detector.ino` runs the MPU at **100 Hz** (`SMPLRT_DIV = 9`). That is correct for tilt
and **fatal for this project**:

- Shaft at 1500 RPM = **25 Hz**
- BPFO = **89.3 Hz**, BPFI = **135.7 Hz**
- At 100 Hz sampling, Nyquist = 50 Hz — **BPFO and BPFI are physically unrepresentable**

The new firmware runs the accelerometer at **1 kHz** (`ACCEL_CONFIG2 = 0x00`, DLPF 44 Hz).
If you copy settings from the Landsafe sketch, you will get a clean-looking capture in which
the fault frequency does not exist. That is the most likely way to wrongly conclude "no
fault detected."

### ⚠️ One register decides whether the whole thing works

`ACCEL_CONFIG2` (0x1D) bit 1 — `ACCEL_FCHOICE_B`:

| Value | Accelerometer rate |
|---|---|
| `0x00` (bit clear) | **1 kHz** ← what we use |
| `0x02` (bit set) | 92.16 / 184.32 / 242.56 / **333.28** Hz |

Setting that bit caps the accelerometer at **333 Hz**, not 1 kHz. At 333 Hz the Nyquist
limit is 167 Hz — BPFI (135.7 Hz) barely fits and BPFO (89.3 Hz) sits uncomfortably close.
**It fails silently**: the sketch returns perfectly plausible numbers at the wrong rate, and
every frequency in the FFT is rescaled, so a real 89 Hz peak plots somewhere else entirely
and looks absent.

Press **`i`** in the serial monitor to confirm. It prints the registers and **warns you
explicitly** if that bit is set.

---

## 2. Mounting — this *is* the measurement

Not an accessory. A loosely mounted MPU reads gravity and noise, and no amount of software
recovers the signal.

- **Rigidly couple to the bearing housing**, not the motor feet or a plastic shroud. The
  housing carries the structural ring.
- Use **double-sided tape plus a cable tie**, or a 3D-printed clamp. It must not hang on a
  wire.
- Keep the **board flat and rigid**; the flexing PCB itself radiates vibration.
- Orientation matters: mount so one axis is **radial** (horizontal, through the shaft) and
  one is **axial** (along the shaft). The classifier uses the axial/radial ratio to separate
  misalignment from imbalance. Note which way is which.
- **Short wires** — I2C at 400 kHz picks up noise on long leads.

First check before anything else: run `s` in the serial monitor. A running machine should
show RMS ≈ 0.02–0.2 g. If it reads ~1.0 g steadily, you are measuring **gravity, not
vibration** — the sensor is not coupled to anything.

---

## 3. Flash

1. Arduino IDE → board **ESP32 Dev Module**, upload speed 921600 (or 115200 if it fails)
2. Upload `pred_maintenance.ino`
3. Serial Monitor at **115200 baud**
4. You should see `WHO_AM_I = 0x68` and `DS18B20: found` (or `not found`, which is fine)

If `WHO_AM_I` is `0x00`, run `i2c_scanner/i2c_scanner.ino` from the firmware folder first —
it will tell you whether the device is on the bus at all.

### Serial commands

| Key | Action |
|---|---|
| `s` | Fast sanity check — RMS, peak, gyro. **Start here.** |
| `b` | Capture one 4096-sample burst and stream it to the PC |
| `c` | Continuous bursts every 3 s |
| `t` | Read DS18B20 |
| `i` | Print current register config |
| `h` | Help |

---

## 4. The moment of truth

```bash
pip install pyserial numpy
cd firmware/pred_maintenance
python bench_capture.py --selftest          # 1. prove the analysis path works (no hardware)
python bench_capture.py --rpm 1500          # 2. capture and analyse the real fan
```

Pass `--rpm` with the machine's **actual** speed if you know it — it removes the 1X-vs-2X
ambiguity that broke four features during simulator development.

### Reading the output

```
BEARING ORDERS (the ones that matter):
  BPFO outer race    xx.x dB  (target 89.1 Hz)  <-- RESOLVED
```

- **BPFO > 18 dB** → the chain works. The thesis holds. Proceed to fault injection.
- **BPFO < 18 dB on a *healthy* machine** → also expected, and fine. A healthy bearing has no
  fault to detect. This number only becomes meaningful once you introduce a fault.

So the honest first target is **not** "find BPFO on a healthy fan." It is:

1. **Verify the chain** — confirm `1X` appears at the right frequency and the RMS is sane.
2. **Then inject a fault** — stick tape on a fan blade (imbalance) or loosen a base bolt
   (looseness) — and confirm the classifier names it correctly.

### If BPFO does not resolve

Check in this order:

1. **Is it rigidly coupled?** Run `s`. RMS ≈ 1.0 g means you're measuring gravity.
2. **Is ACTUAL_FS close to 1000 Hz?** I2C overruns silently mis-scale every frequency, so a
   real 89 Hz peak gets plotted at the wrong place and looks absent.
3. **Is the machine at the speed you think?** Try `--rpm 1420` (real induction motors run
   below nominal).
4. **Widen the DLPF** — change `REG_CONFIG` from `0x03` (44 Hz) to `0x02` (92 Hz) or `0x01`
   (184 Hz) in `initMPU()`. This trades noise for bandwidth; re-run `s` to watch the RMS
   floor rise.
5. **Check `i`** — if `ACCEL_CONFIG2` bit 1 is set you are capped at 333 Hz regardless of
   anything else you change.

---

## 5. Suggested order for the first two evenings

1. Flash, run `s`, confirm the sensor sees the machine (30 min)
2. Run `bench_capture.py --selftest` on the PC (10 min)
3. Capture the healthy fan, confirm **1X at 25 Hz** (30 min)
4. Stick tape on a blade → confirm **imbalance** (20 min)
5. Loosen a base bolt → confirm **looseness** (20 min)

Only after steps 3–5 work do you buy the INMP441 and attempt the bearing demo. That last one
is the hard part, and it is the entire thesis — everything above is the fallback that still
gives you a working four-fault classifier.

## 6. What this firmware does NOT do

- **No FFT on the ESP32.** It cannot do it reliably in Arduino, and a plausible-but-wrong
  spectrum is worse than none. Raw data out, numpy maths on the PC.
- **No Wi-Fi.** Deliberately. Get the measurements right first; networking comes later.
- **No alerting.** Same reason.

Each of those is a deliberate sequencing decision, not an oversight.