#include "config.h"
#include <Dynamixel2Arduino.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <Adafruit_BNO055.h>   // Adafruit BNO055 (I2C 0x28)
#include <utility/imumaths.h>

Dynamixel2Arduino dxl(Serial1);
Adafruit_INA219  ina219;
Adafruit_BNO055  bno(55, 0x28);   // sensor_id=55, I2C addr=0x28

//I2C Device Map (Hardware Verification)
// 0x28: BNO055 IMU
// 0x40: INA219 Current Sensor

// ====== streaming config ======
bool streaming = true;
uint16_t out_hz = 100;   // 100 Hz output carries IMU data
uint16_t cur_hz = 100;
uint16_t ang_hz = 100;
uint16_t dec_hz = 200;

// ====== I2C config ======
constexpr uint8_t INA219_ADDR = 0x40;
constexpr unsigned long I2C_RETRY_MS = 1000;
constexpr unsigned long I2C_RECOVERY_COOLDOWN_MS = 100;

// ====== cached values ======
float current_mA = 0;
float wrist_deg  = 0;
long  dec_count  = 0;

// Quaternion from BNO055 NDOF fusion
float imu_qw = 1.0f, imu_qx = 0.0f, imu_qy = 0.0f, imu_qz = 0.0f;
bool  imu_ok  = false;
bool  ina_ok  = false;

// ====== timers ======
unsigned long t_last_out = 0, t_last_cur = 0, t_last_ang = 0, t_last_dec = 0;
unsigned long t_last_i2c_retry = 0;
unsigned long t_last_i2c_recover = 0;

void reset_imu_cache() {
  imu_qw = 1.0f;
  imu_qx = 0.0f;
  imu_qy = 0.0f;
  imu_qz = 0.0f;
}

void reset_current_cache() {
  current_mA = 0.0f;
}

void configure_wire() {
  Wire.begin();
  Wire.setClock(100000);
  Wire.setTimeout(5);
}

bool wire_timed_out() {
#if defined(WIRE_HAS_TIMEOUT)
  if (Wire.getWireTimeoutFlag()) {
    Wire.clearWireTimeoutFlag();
    return true;
  }
#endif
  return false;
}

bool i2c_ping(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

bool init_ina219(bool verbose = true) {
  ina_ok = ina219.begin();
  if (ina_ok) {
    ina219.setCalibration_32V_2A();
    if (verbose) {
      Serial.println("INA219 OK");
    }
  } else {
    reset_current_cache();
    if (verbose) {
      Serial.println("Error: INA219 not found");
    }
  }
  return ina_ok;
}

bool init_bno055(bool verbose = true) {
  imu_ok = bno.begin();
  if (imu_ok) {
    bno.setExtCrystalUse(true);
    if (verbose) {
      Serial.println("BNO055 OK");
    }
  } else {
    reset_imu_cache();
    if (verbose) {
      Serial.println("Error: BNO055 not found — streaming qw=1,qx=0,qy=0,qz=0");
    }
  }
  return imu_ok;
}

void recover_i2c_bus(const char* reason) {
  const unsigned long now = millis();
  if ((now - t_last_i2c_recover) < I2C_RECOVERY_COOLDOWN_MS) {
    return;
  }
  t_last_i2c_recover = now;
  Serial.print("I2C recover: ");
  Serial.println(reason);
  configure_wire();
}

void retry_i2c_devices() {
  const unsigned long now = millis();
  if ((now - t_last_i2c_retry) < I2C_RETRY_MS) {
    return;
  }
  t_last_i2c_retry = now;

  if (!ina_ok) {
    init_ina219();
  }
  if (!imu_ok) {
    init_bno055();
  }
}

// ── IMU helper ───────────────────────────────────────────────────────────────
void imu_update() {
  if (!imu_ok) return;

  imu::Quaternion q = bno.getQuat();
  imu_qw = (float)q.w();
  imu_qx = (float)q.x();
  imu_qy = (float)q.y();
  imu_qz = (float)q.z();
}

long read_decoder_count() {
  return (long)dxl.getPresentPosition(WRIST_ID, UNIT_RAW);
}

void setup() {
  Serial.begin(DEBUG_BAUD_RATE);
  pinMode(DAMPER_PIN, OUTPUT);
  analogWrite(DAMPER_PIN, 255);  // start with no resistance

  dxl.begin(DYNAMIXEL_BAUD_RATE);
  dxl.setPortProtocolVersion(2.0);
  dxl.torqueOff(WRIST_ID);
  dxl.setOperatingMode(WRIST_ID, OP_EXTENDED_POSITION);
  dxl.torqueOn(WRIST_ID);

  configure_wire();

  // INA219
  init_ina219();

  // BNO055 (NDOF fusion mode — provides calibrated quaternion)
  init_bno055();

  Serial.setTimeout(5);
}

void loop() {
  const unsigned long now = millis();

  // ---- command interface ----
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();

    if (input == "STREAM_ON") streaming = true;
    else if (input == "STREAM_OFF") streaming = false;
    else if (input.startsWith("OUT_HZ:")) out_hz = input.substring(7).toInt();
    else if (input.startsWith("CUR_HZ:")) cur_hz = input.substring(7).toInt();
    else if (input.startsWith("ANG_HZ:")) ang_hz = input.substring(7).toInt();
    else if (input.startsWith("DEC_HZ:")) dec_hz = input.substring(7).toInt();
    else if (input.startsWith("SET_ANG:")) {
      float ang = input.substring(8).toFloat();
      dxl.setGoalPosition(WRIST_ID, ang, UNIT_DEGREE);
    }
    else if (input.startsWith("SET_DMP:")) {
      int val = constrain(input.substring(8).toInt(), 0, 255);
      analogWrite(DAMPER_PIN, 255 - val);
    }
    else if (input == "TORQUE_OFF") {
      dxl.torqueOff(WRIST_ID);
    }
    else if (input == "TORQUE_ON") {
      dxl.torqueOn(WRIST_ID);
      dxl.setOperatingMode(WRIST_ID, OP_POSITION);
      dxl.torqueOn(WRIST_ID);
    }
  }

  retry_i2c_devices();

  // // ---- IMU update (polled every loop for maximum freshness) ----
  // imu_update();

  // ---- current update ----
  if (cur_hz > 0 && (now - t_last_cur) >= (1000UL / cur_hz)) {
    t_last_cur = now;
    if (ina_ok) {
      if (!i2c_ping(INA219_ADDR)) {
        ina_ok = false;
        reset_current_cache();
        recover_i2c_bus("INA219 ping failed");
      } else {
        current_mA = ina219.getCurrent_mA();
        if (wire_timed_out()) {
          ina_ok = false;
          reset_current_cache();
          recover_i2c_bus("INA219 read timeout");
        }
      }
    }
  }

  // ---- motor angle update ----
  if (ang_hz > 0 && (now - t_last_ang) >= (1000UL / ang_hz)) {
    t_last_ang = now;
    wrist_deg = dxl.getPresentPosition(WRIST_ID, UNIT_DEGREE);
  }

  // ---- decoder update ----
  if (dec_hz > 0 && (now - t_last_dec) >= (1000UL / dec_hz)) {
    t_last_dec = now;
    dec_count = read_decoder_count();
  }

  // ---- stream out ----
  // Format: t_ms, qw, qx, qy, qz, current_mA, wrist_deg, dec_count  (8 fields)
  if (streaming && out_hz > 0 && (now - t_last_out) >= (1000UL / out_hz)) {
    imu_update();
    t_last_out = now;
    Serial.print(now);            Serial.print(",");
    Serial.print(imu_qw, 5);     Serial.print(",");
    Serial.print(imu_qx, 5);     Serial.print(",");
    Serial.print(imu_qy, 5);     Serial.print(",");
    Serial.print(imu_qz, 5);     Serial.print(",");
    Serial.print(current_mA, 3); Serial.print(",");
    Serial.print(wrist_deg, 3);  Serial.print(",");
    Serial.println(dec_count);
  }
}
