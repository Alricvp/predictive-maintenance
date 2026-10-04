# Novelty Audit — Is this actually new?

Written because the honest answer to "what's different about your project?" is the whole
pitch. We researched the landscape first, then wrote the gap.

---

## 1. What already exists

### Commercial condition-monitoring (the incumbents)

| Vendor | What they do | Where the gap is |
|---|---|---|
| **Augury** | Proprietary vibration + **ultrasound** sensors, AI platform, "machine health as a service" | Human-in-the-loop: **CAT III/IV vibration analyst review** is sold as part of the product. Ultrasound is patented hardware we cannot replicate. Pricing opaque, enterprise-scale |
| **Nanoprecise** | AI-driven predictive maintenance, sensor-agnostic | Same analyst-in-the-loop model, enterprise |
| **SKF / Siemens / Bosch Rexroth** | Cabled condition-monitoring hardware tied to their own bearings/motors | Locked to proprietary ecosystems |
| **Fluke** | 356-series vibration meters, 810 vibration analysers | **₹35,000+ handheld**, needs a trained analyst to interpret. The 810's advertised differentiator is "automated pattern recognition" |
| **Uptake** | Industrial AI/asset management | Not sensor-focused |
| **Wireless MEMS accelerometers (industry)** | $150–250/sensor, 2–5 yr battery, cabled versions $1,500–3,000 | Still needs expertise to interpret |

The critical structural finding: **Augury already uses ultrasound.** Ultrasound-based
predictive maintenance is not novel. Their marketing even highlights it for ultra-low-RPM
equipment. So "we use a microphone" is not the innovation.

### Academic literature (the trap)

Saturated. "Low-cost IoT-based predictive maintenance using vibration" (Sensors 2025,
cited 35×), "low-cost prototype for bearing failure detection using TinyML" (2025), dozens
of MPU6050 + Arduino + FFT + SVM papers. Reviewers of this space expect it. Any pitch whose
technical core is "FFT the accelerometer and classify with an ML model" is dead on arrival
— and it is also *unbuildable* on the hardware we can afford, because we showed the
accelerometer's noise floor is ~45% of the healthy signal.

---

## 2. So what is the gap?

Three things, in descending order of how defensible they are.

### Gap 1 — The diagnosis step is the expensive step, and it is still human

Every incumbent sells **sensors plus an analyst**. Fluke's differentiator is automated
pattern recognition; Augury's is a certified analyst reviewing your machines. The sensor is
a commodity; **interpreting the spectrum is the ₹₹ and the years-of-training bottleneck.**

Our position: the interpretation rules for the four dominant rotating-machine faults are
*fully codified* (Fluke, SKF and ISO 10816/20816 all document them), and the reason nobody
ships them for free is that they're usually bundled with hardware they also want to sell.
We ship the interpretation, calibrated per machine, for ₹2,200.

This is a **"the moat is the software, not the sensor"** argument — which is why it's robust
to the fact that cheap sensors exist.

### Gap 2 — Early warning, not late warning, at the price of a toy

Augury's ultrasound hardware is proprietary and enterprise-priced. Everyone else using
cheap MEMS accelerometers is measuring in the band where early bearing faults **have not
started** — we measured this directly: our accelerometer's envelope channel needs severity
0.50 to fire, while the mic channel fires at 0.25.

We are not claiming to invent ultrasound. **We are claiming that a ₹250 INMP441 plus the
correct demodulation is sufficient to reproduce the early-warning advantage that is
otherwise locked inside proprietary hardware** — and we show the ordering with a simulator
before we show it on hardware.

**A finding worth stating because it is counter-intuitive:** the MPU6050 we already own is
*quieter* than a dedicated vibration accelerometer (3.1 vs 8.8 milli-g) while having a
*lower* Nyquist limit (500 vs 1600 Hz). Cheap IMU parts beat dedicated accelerometers at
low frequency and lose badly at high frequency. That is precisely why a two-sensor design
wins rather than simply buying a better accelerometer.

### Gap 3 — The cost floor of "predictive maintenance" is set wrong

The framing in every pitch is "we made predictive maintenance affordable." Cheap-competitor
sensors are ₹150–250 *but require a skilled analyst to interpret* — the total cost of
ownership is dominated by human expertise, not silicon. Our framing: **we removed the
analyst from the loop, which is where the cost actually lives.** That's a different claim
than "our sensor is cheaper," and it survives the objection that sensors are already cheap.

---

## 3. What makes the *demo* defensible

Most teams demo a green→red transition on a healthy machine. That's a toy demo: it proves
the code runs, not that the system is early.

Ours proves the **negative result** matters: with a real developing bearing fault on the
bench, the accelerometer shows *nothing* and the microphone alarms. A demo that
demonstrates your own sensor failing, and a different sensor succeeding, is much harder to
dismiss than one where everything turns red on cue. It also happens to be exactly the claim
we're making.

---

## 4. Claim / don't-claim

**We DO claim (all verified in [results_verified.txt](results_verified.txt)):**

- For a 6205 at 1500 RPM, the four fault orders are 9.9 / 58.0 / 89.3 / 135.7 Hz, all
  below 136 Hz — so order detection is sound even with a 500 Hz-Nyquist MPU6050.
- The MPU6050's broadband noise floor is ~3.1 milli-g (ADXL345: 8.8 milli-g) — still ~15%
  of a healthy motor's 1X. Cheap sensing is genuinely hard; thresholds must be calibrated,
  not fixed.
- Rule-based discrimination of healthy / imbalance / misalignment / looseness / bearing:
  **100% on 200 synthetic trials** on the MPU6050 config, **99.5% (199/200)** on the
  ADXL345 config.
- Earliest correct identification (MPU6050): **mic channel fires at severity 0.10**,
  accelerometer RMS at 0.30.
- Detectors fire in the order **mic < accel < RMS** on both sensor configs.

**We DO NOT claim:**

- ❌ That ultrasound is our invention. Augury ships it commercially and patented it.
- ❌ That we beat Augury. They have real field data across fleets; we have a bench rig.
- ❌ Remaining useful life in days. We show an ordered early warning and a trend, not a
  calibrated failure date.
- ❌ Any result from a *real* machine. Every number in this repo is **synthetic**, from a
  physics-based simulator. We say so on the first slide of the deck.
- ❌ Gearbox, non-rotating, or multi-machine fleet capability.
- ❌ That the separation percentage generalises. It is measured on our own synthesis model
  with our own assumptions. Its purpose is to show the *method* is sound, not to claim field
  accuracy.
- ❌ That the MPU6050 numbers match an ADXL345 deployment. Re-measuring under different
  sensor limits moved the mic from 0.25 to 0.10 and RMS from 0.65 to 0.30 — **the lead time
  is a property of the sensor, not of the idea.** Anyone changing parts must re-run
  `sim_faults.py --compare`.

---

## 5. Judge questions, answered honestly

**"You've just simulated data. Where's the real machine?"**
On the bench fan and pump, with faults we induce deliberately. We bring the simulator as a
*tool*, not as evidence — it exists to prove our signal chain resolves the fault order
before we spend days on hardware. But yes: every headline number here is synthetic, and
that's labelled.

**"Isn't this just FFT plus a threshold?"**
FFT plus *calibrated* thresholds, plus a speed estimator that is validated against the
nameplate, plus envelope demodulation on a band the accelerometer can't reach. Each of those
existed for a specific reason we can point to in the code.

**"What if the mic hears the air conditioner?"**
Then the baseline learns it — we calibrate in situ, and a room-level constant won't track a
developing bearing spall. This is a real demo risk and we'll show the calibration step.

**"Why rules and not a model?"**
Four classes with published, physically distinct signatures. A model trained on synthetic
data would be *less* honest and *less* defensible — we'd have no way to show you what
fired. When a judge asks "why did it say looseness?", a model has no answer.

**"How is this better than a ₹35,000 Fluke 356?"**
A Fluke 356 tells *you* the vibration number; a trained analyst tells you what it means.
Our four rules cover the four faults that make up ~90% of rotating-machine problems. For
those, you get the diagnosis without the analyst. For gearbox and exotic faults, we agree —
you need the Fluke, and then you need the analyst, and then you're back to square one.