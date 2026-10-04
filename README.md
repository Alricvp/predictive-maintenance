# Predictive Maintenance — Real-Time Machine Condition Monitor

**SIH 2026 · Track: Wildcard — Beyond the Obvious · Team SCAPEGOATS**

Moves industrial maintenance from reactive (*"repair after the breakdown"*) to
predictive (*"detect the problem before failure"*) — using an MPU6050 and a MEMS
microphone, for under ₹800 of additional hardware.

---

## The idea in one paragraph

A 1.5 kW motor at 1500 RPM has a shaft frequency of 25 Hz, and the four classic
bearing defect orders for a 6205 sit at **9.9 / 58.0 / 89.3 / 135.7 Hz** — all
below 136 Hz. So an MPU6050 (500 Hz Nyquist) can identify *which* mechanical
fault is present. But an early-stage bearing spall announces itself as a
**12–20 kHz** structural ring, which is *entirely above* what that sensor can
sample. The fault is present in the machine and invisible to the accelerometer.
A ₹250 INMP441 microphone covers that band. Measured on simulated data, the mic
detects a developing bearing fault **20% of the degradation path earlier** than
the accelerometer.

## What is verified, and what is not

| Claim | Status |
|---|---|
| Bearing orders for a 6205 @ 1500 RPM | Derived from geometry — check `tools/sim_faults.py` |
| Classifies healthy / imbalance / misalignment / looseness / bearing | **100% on 200 synthetic trials** (`results/results_mpu6050.txt`) |
| Mic detects earlier than the accelerometer | **20% lead**, simulated — `results/` |
| MPU6050 noise floor ≈ 3.1 milli-g vs ~20 milli-g healthy 1X | Modelled from datasheet |
| **Any real hardware measurement** | ❌ **None yet.** No INMP441 has been bought. |

All numbers in `results/` come from a physics-based simulator, not from a
physical machine. This is stated on the dashboard too. Do not overclaim it.

## Repository layout

```
app/          FastAPI server + dashboard + PWA files (this is what deploys)
firmware/     ESP32 + MPU6050 firmware, and the bench analysis harness
tools/        Fault simulator (sim_faults.py) and the demo data feeder
results/      Committed simulator output backing every number above
docs/         Full engineering solution + novelty audit
Dockerfile    Pinned build for Koyeb
```

`app/` is self-contained and has no dependency on any other project.

## Run it locally

```bash
pip install -r requirements.txt
cd app && python -m uvicorn main:app --reload --port 8000
```

Then feed it simulated data and open <http://localhost:8000>:

```bash
python tools/live_sim.py --url http://localhost:8000
```

The simulator injects a scripted fault timeline: healthy → imbalance → healthy →
looseness → healthy → a bearing fault that develops over 90 s. Use
`--speedup 6` to play it faster.

## Deploy to Koyeb (free)

1. Push this repo to its own GitHub repository.
2. Koyeb Dashboard → **Create → Web Service** → connect the repo.
3. Koyeb detects the `Dockerfile`. Settings:
   - Region: **Frankfurt or Washington DC** (the only ones on the free tier)
   - Instance: **Free** (512 MB, 0.1 vCPU)
4. Deploy. You get a public HTTPS URL.

Two things that break PaaS deploys, both already handled in `main.py`:

- **Bind address must be `0.0.0.0`.** Binding `127.0.0.1` inside a container
  makes every external request fail with connection refused.
- **Port must come from `$PORT`,** not a hardcoded 8000.

### Why Koyeb rather than Render

Render's free web services spin down after **15 minutes** of inactivity with a
30–60 second cold start. Koyeb's free Instance scales to zero after **1 hour**,
so a link opened during judging is far less likely to show a loading page. It
also usually needs no credit card.

## Known limitations — read these before demoing

- **All data is simulated.** The dashboard shows a `SIMULATED` badge on
  purpose. If a judge discovers the data was passed off as live, the project
  loses all credibility. Present the simulator as the system running on
  synthetic data, and the one real-hardware measurement as the proof of the
  core claim.
- **The free Instance sleeps.** Alerts and device state are written to disk
  (`app/data/`) so they survive a *process* restart, but Koyeb's local disk is
  **ephemeral** — an instance recreation loses them. Persistence is a big
  improvement, not a guarantee.
- **The firmware has never been compiled.** No Arduino toolchain was available
  when it was written. `ACCEL_CONFIG2` **must** be `0x00`; setting bit 1 caps
  the accelerometer at 333 Hz and silently rescales every frequency.
- **SMS is 1 message/day** on TextBelt's free key.
- **Only a 6205 at 1500 RPM** is modelled. Not 1440 RPM, not under varying load,
  not another bearing type.
- **The ultrasonic channel is synthesised.** No microphone is wired up.

## Reference: what the order bars mean

| Signature | Fault |
|---|---|
| 1X radial only | Imbalance |
| Axial 1X **and** 2X | Misalignment |
| 3X / 4X harmonic series | Looseness |
| Non-integer order in the resonance envelope | Bearing fault |

Looseness must be tested **before** misalignment: both show a strong axial 1X
and a strong 2X, so the conventional ordering mislabels every looseness case.
A loose structure radiates a full harmonic series; true misalignment is
dominated by 1X/2X only.

See `docs/SOLUTION.md` for the full derivation and `docs/INNOVATION.md` for the
competitive audit.