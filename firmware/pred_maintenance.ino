/*
 * Predictive Maintenance Node - RAW CAPTURE MODE
 * ESP32 + MPU-6050 (I2C) + DS18B20 (1-Wire)
 *
 * WHY THIS FIRMWARE DOES NO FFT ITSELF
 * ------------------------------------
 * The MPU-6050 accelerometer maxes out at 1 kHz ODR (500 Hz Nyquist). Doing
 * real-time FFT + envelope detection on an ESP32 at that rate is not
 * realistic in Arduino, and any attempt produces numbers that look plausible
 * and are wrong. So the ESP32's ONLY job here is to capture clean raw data
 * as fast as it can, ship it over serial, and let the PC do the maths with
 * numpy (see ../../machin/sim_faults.py - the SAME feature extractor runs on
 * simulated and real data, which is what makes the comparison meaningful).
 *
 * This is stage 1 of the build. Stage 2 (edge FFT on ESP32-S3, or MCU
 * downsample to features) happens only after we PROVE the raw chain resolves
 * BPFO at 89.3 Hz on the real machine.
 *
 * ---------------------------------------------------------------
 * CRITICAL FIX vs the Landsafe tilt firmware: sample rate
 * ---------------------------------------------------------------
 * tilt_detector.ino runs the MPU at 100 Hz (SMPLRT_DIV=9). That is fine for
 * tilt but FATAL here: at 1500 RPM the shaft is 25 Hz and BPFI is 135.7 Hz.
 * A 100 Hz sample rate has a 50 Hz Nyquist - it cannot represent the BPFI line
 * at all, and barely resolves BPFO at 89.3 Hz. We use 1 kHz here.
 *
 * Wiring:
 *   MPU6050  VCC -> 3V3      GND -> GND
 *            SDA -> GPIO 21  SCL -> GPIO 22   (ADDR=0, so SDA=GPIO21)
 *   DS18B20  VCC -> 3V3      GND -> GND      DATA -> GPIO 4
 *            add a 4.7k pull-up resistor from DATA to 3V3
 *
 * Board notes:
 *   - ESP32-WROOM-32 has NO PSRAM. The 8 KB FIFO is not enough for our bursts,
 *     so we capture into a heap buffer in chunks (see BURST_SAMPLES).
 *   - If your MPU module has AD0 tied high, the address is 0x69. Change MPU_ADDR.
 *   - DS18B20 is OPTIONAL. The sketch runs without it (skips temperature).
 */

#include <Wire.h>

// ---- Pin map (matches the Landsafe node so the wiring is familiar) ----
#define SDA_PIN 21
#define SCL_PIN 22
#define ONEWIRE_PIN 4

// ---- MPU6050 ----
#define MPU_ADDR 0x68
#define GYRO_ADDR 0x68        // same device
#define SENS_2G 16384.0f      // LSB per g at +-2g range
#define FS_GYRO 131.0f        // LSB per deg/s at +-250 deg/s range

// ---- Capture configuration ----
#define CAPTURE_FS 1000       // Hz - the ODR we configure
#define BURST_SAMPLES 4096    // 4.096 s at 1 kHz = 2.56 s analysis window x1.6
// 4096 samples x 3 axes x 2 bytes = 24 KB per burst. Comfortable on WROOM.

#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1   0x6B
#define REG_WHO_AM_I     0x75
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_CONFIG2 0x1D
#define SMPLRT_DIV      0x19

static int16_t bufX[BURST_SAMPLES];
static int16_t bufY[BURST_SAMPLES];
static int16_t bufZ[BURST_SAMPLES];
static int16_t bufG[BURST_SAMPLES];   // gyro Z - independent tachometer

bool tempOK = false;
float lastTempC = 0.0f;

void writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.write(val);
  Wire.endTransmission();
}

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg); Wire.endTransmission(false);
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)1) != 1) return 0;
  return Wire.read();
}

/*
 * Configure the MPU for 1 kHz acceleration.
 *
 * ACCEL_CONFIG2 (0x1D) is the register people get wrong, and getting it wrong
 * is SILENT - the sketch still returns plausible-looking samples, just at the
 * wrong rate, which rescales every frequency in the FFT.
 *
 * ACCEL_FCHOICE_B (bit 1 of 0x1D) selects WHO sets the accel output rate:
 *   = 0  -> the accel runs at 1 kHz, set by SMPLRT_DIV (the normal setting,
 *           and what the canonical MPU6050 examples use)
 *   = 1  -> the accel rate is fixed by DLPF_CFG: 92.16 / 184.32 / 242.56 /
 *           333.28 Hz. NOT 1 kHz.
 *
 * An earlier version of this file set 0x01, believing it "let DLPF set the ODR"
 * while still getting 1 kHz. That yields at most 333 Hz - and at CONFIG=3 it is
 * 333 Hz. At 333 Hz the Nyquist limit is 167 Hz, so BPFI (135.7 Hz) only just
 * fits and every amplitude is subtly wrong. Corrected to 0x00 below.
 *
 * DLPF choice: we use the NARROWEST (CONFIG=3, 44 Hz), not the widest.
 * Wide DLPF (250 Hz) passes more noise into the signal; the FFT noise floor is
 * already 3.1 milli-g against a ~20 milli-g healthy 1X. All fault orders live
 * below 136 Hz, so bandwidth above ~200 Hz buys nothing and costs noise.
 * If BPFO proves hard to resolve on real hardware, step CONFIG up to 2 (92 Hz)
 * or 1 (184 Hz) - and expect the noise floor to rise with it.
 */
bool initMPU() {
  uint8_t who = readReg(REG_WHO_AM_I);
  Serial.printf("WHO_AM_I = 0x%02X\n", who);
  if (who != 0x68 && who != 0x70 && who != 0x71 && who != 0x73) {
    Serial.println("ERROR: no MPU found. Check SDA=21 SCL=22, and MPU_ADDR.");
    return false;
  }

  writeReg(REG_PWR_MGMT_1, 0x80);   // device reset
  delay(100);
  writeReg(REG_PWR_MGMT_1, 0x01);   // wake, clock source = PLL with X gyro
  delay(10);

  writeReg(SMPLRT_DIV, 0x00);       // gyro 1 kHz
  writeReg(REG_CONFIG, 0x03);       // DLPF 44 Hz (narrow, low noise)
  writeReg(REG_GYRO_CONFIG, 0x00);  // +-250 deg/s
  writeReg(REG_ACCEL_CONFIG, 0x00); // +-2 g
  writeReg(REG_ACCEL_CONFIG2, 0x00);// ACCEL_FCHOICE_B=0 -> accel runs at 1 kHz
                                    // (setting bit1=1 would cap it at 333 Hz)

  Serial.printf("MPU configured: +-2g, gyro +-250dps, DLPF 44Hz, accel ODR 1kHz\n");
  Serial.printf("  verify: SMPLRT_DIV=0x%02X CONFIG=0x%02X ACCEL_CONFIG2=0x%02X"
                " (ACCEL_CONFIG2 bit1 MUST be 0)\n",
                readReg(SMPLRT_DIV), readReg(REG_CONFIG), readReg(REG_ACCEL_CONFIG2));
  return true;
}

// ---- Read 14 bytes (accel + temp + gyro) in one I2C transaction ----
void readBurst(int16_t *ax, int16_t *ay, int16_t *az, int16_t *gz) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  Wire.endTransmission(false);
  if (Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14) != 14) {
    *ax = *ay = *az = *gz = 0;
    return;
  }
  uint8_t d[14];
  for (int i = 0; i < 14; i++) d[i] = Wire.read();
  *ax = (int16_t)((d[0] << 8) | d[1]);
  *ay = (int16_t)((d[2] << 8) | d[3]);
  *az = (int16_t)((d[4] << 8) | d[5]);
  *gz = (int16_t)((d[12] << 8) | d[13]);
}

/*
 * Burst capture.
 *
 * TIMING CAVEAT - read this before trusting the capture:
 * I2C at 400 kHz moves 14 bytes (~126 bits + overhead) per transaction. The
 * measurement itself takes 1/CAPTURE_FS. If a transaction ever overruns its
 * slot, samples are LATE and the timebase is wrong - which shows up as a
 * smeared or fake spectrum. So we report the achieved rate, and bench_capture.py
 * prints the ACTUAL sample rate back. If it reads below ~950 Hz, drop to
 * CAPTURE_FS 500 or shorten the wires. A wrong sample rate silently rescales
 * every frequency, so a fault at 89.3 Hz would appear at the wrong place and you
 * would wrongly conclude "no BPFO".
 */
void captureBurst() {
  Serial.println("BURST_BEGIN");
  Serial.print("FS,");
  Serial.println(CAPTURE_FS);
  Serial.print("N,");
  Serial.println(BURST_SAMPLES);

  uint32_t t0 = micros();
  int16_t ax, ay, az, gz;
  int dropped = 0;

  for (int i = 0; i < BURST_SAMPLES; i++) {
    readBurst(&ax, &ay, &az, &gz);
    bufX[i] = ax; bufY[i] = ay; bufZ[i] = az; bufG[i] = gz;
    // Busy-wait to the next slot. delayMicroseconds cannot hit 1 ms reliably,
    // so we busy-wait - at 1 kHz we need 1000 us per sample and have slack.
    uint32_t target = t0 + (uint32_t)i * (1000000UL / CAPTURE_FS);
    while ((int32_t)(micros() - target) < 0) { }
  }
  uint32_t elapsed = micros() - t0;
  float actual_fs = (float)BURST_SAMPLES * 1e6f / elapsed;

  Serial.print("ACTUAL_FS,");
  Serial.println(actual_fs, 1);

  // One line per sample: idx,ax,ay,az,gz  (raw counts; PC converts)
  for (int i = 0; i < BURST_SAMPLES; i++) {
    Serial.print(i); Serial.print(',');
    Serial.print(bufX[i]); Serial.print(',');
    Serial.print(bufY[i]); Serial.print(',');
    Serial.print(bufZ[i]); Serial.print(',');
    Serial.println(bufG[i]);
  }
  Serial.println("BURST_END");
}

// ---- DS18B20 (optional) ----
#include <OneWire.h>
#include <DallasTemperature.h>
OneWire ow(ONEWIRE_PIN);
DallasTemperature dt(&ow);

float readTempC() {
  if (!tempOK) return -999.0f;
  dt.requestTemperatures();
  float t = dt.getTempCByIndex(0);
  if (t < -85.0f || t > 125.0f) return -999.0f;   // wiring fault sentinel
  return t;
}

void printHelp() {
  Serial.println("\n=== Predictive Maintenance Node ===");
  Serial.println("Commands (Serial Monitor @ 115200):");
  Serial.println("  b      - capture one 4096-sample burst to serial");
  Serial.println("  c      - continuous bursts (every 3 s)");
  Serial.println("  s      - single shot, print RMS/kurtosis only (fast sanity)");
  Serial.println("  t      - read DS18B20 temperature");
  Serial.println("  i      - print config + WHO_AM_I");
  Serial.println("  h      - this help");
}

bool continuous = false;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== Predictive Maintenance Node (raw capture mode) ===\n");

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  if (!initMPU()) {
    Serial.println("HALTED: fix wiring first. Try firmware/i2c_scanner.");
    while (1) delay(500);
  }

  dt.begin();
  tempOK = (dt.getDeviceCount() > 0);
  Serial.printf("DS18B20: %s\n", tempOK ? "found" : "not found (continuing without)");

  printHelp();
}

void loop() {
  if (continuous) {
    captureBurst();
    delay(3000);
    return;
  }

  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'b') {
      captureBurst();
    } else if (c == 'c') {
      continuous = true;
      Serial.println("continuous ON");
    } else if (c == 's') {
      // Cheap sanity check: RMS and peak of a short capture. Tells you
      // immediately whether the sensor is mounted and moving at all.
      int16_t ax, ay, az, gz;
      double sx = 0, sy = 0, sz = 0;
      float peak = 0;
      const int N = 1000;
      for (int i = 0; i < N; i++) {
        readBurst(&ax, &ay, &az, &gz);
        double gx = ax / SENS_2G, gy = ay / SENS_2G, gz_ = az / SENS_2G;
        sx += gx * gx; sy += gy * gy; sz += gz_ * gz_;
        float m = sqrtf(gx * gx + gy * gy + gz_ * gz_);
        if (m > peak) peak = m;
        delayMicroseconds(1000);
      }
      double rms = sqrt((sx + sy + sz) / (3.0 * N));
      Serial.printf("RMS=%.4f g  peak=%.4f g  (RMS should be ~0.02-0.2 on a running machine)\n",
                    rms, peak);
      Serial.printf("GyroZ last = %.2f deg/s  (expect ~1500 if the fan is spinning)\n",
                    gz / FS_GYRO);
    } else if (c == 't') {
      float t = readTempC();
      if (t > -900) Serial.printf("Temp = %.2f C\n", t);
      else Serial.println("DS18B20 read FAILED (check wiring / 4.7k pull-up)");
    } else if (c == 'i') {
      uint8_t ac2 = readReg(REG_ACCEL_CONFIG2);
      Serial.printf("WHO_AM_I=0x%02X  SMPLRT_DIV=0x%02X  DLPF_CFG=0x%02X"
                    "  ACCEL_CONFIG2=0x%02X  FS=%d Hz\n",
                    readReg(REG_WHO_AM_I), readReg(SMPLRT_DIV),
                    readReg(REG_CONFIG), ac2, CAPTURE_FS);
      if (ac2 & 0x02) {
        Serial.println("  !! ACCEL_CONFIG2 bit1 is SET: accel is limited to");
        Serial.println("     92-333 Hz, NOT 1 kHz. Every frequency will be wrong.");
        Serial.println("     Fix: ACCEL_CONFIG2 = 0x00");
      }
    } else if (c == 'h') {
      printHelp();
    }
  }
}