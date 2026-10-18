#if __has_include("config.h")
#include "config.h"
#endif

#ifndef DEBUG_BAUD_RATE
#define DEBUG_BAUD_RATE 1000000
#endif
#ifndef ENABLE_BNO055_IMU
#define ENABLE_BNO055_IMU 0
#endif
#ifndef ENABLE_INA219_CURRENT
#define ENABLE_INA219_CURRENT 0
#endif
#ifndef DAMPER_PIN
#define DAMPER_PIN 5
#endif
#ifndef WRIST_ID
#define WRIST_ID 0
#endif

#include <Dynamixel2Arduino.h>
#include <Wire.h>
#if ENABLE_INA219_CURRENT
#include <Adafruit_INA219.h>
#endif
#include <Adafruit_BNO055.h>   // Adafruit BNO055 (I2C 0x28)
#include <utility/imumaths.h>
using namespace ControlTableItem;
Dynamixel2Arduino dxl(Serial1);
#if ENABLE_INA219_CURRENT
Adafruit_INA219  ina219;
#endif
Adafruit_BNO055  bno(55, 0x28);   // sensor_id=55, I2C addr=0x28

//I2C Device Map (Hardware Verification)
// 0x28: BNO055 IMU
// 0x40: INA219 Current Sensor

// ====== streaming config ======
bool streaming = true;
uint16_t out_hz = 0;     // slow full output carries IMU/current data
uint16_t fast_hz = 500;  // fast motor-only output
uint16_t cur_hz = 0;
uint16_t imu_hz = 0;

// ====== I2C config ======
constexpr unsigned long I2C_RETRY_MS = 1000;
constexpr unsigned long I2C_RECOVERY_COOLDOWN_MS = 100;

// ====== cached values ======
float current_mA = 0;
float wrist_deg  = 0;
long  dec_count  = 0;

// XC/XM/X-series present velocity(128) and position(132) are contiguous.
struct __attribute__((packed)) MotorFeedback {
  int32_t velocity_raw;
  int32_t position_raw;
};

MotorFeedback motor_feedback = {0, 0};
float dxl_velocity_rpm = 0.0f;
uint32_t motor_timestamp_us = 0;
uint32_t motor_read_failures = 0;

constexpr uint32_t HIGH_FREQ_DYNAMIXEL_BAUD_RATE = 1000000UL;
constexpr uint32_t MOTOR_PERIOD_US = 2000UL;  // 500 Hz
constexpr uint16_t DXL_PRESENT_VELOCITY_ADDR = 128;
constexpr uint16_t DXL_PRESENT_VELOCITY_POSITION_LEN = 8;
constexpr float DXL_POSITION_DEG_PER_RAW = 0.088f;

// Quaternion from BNO055 NDOF fusion
float imu_qw = 1.0f, imu_qx = 0.0f, imu_qy = 0.0f, imu_qz = 0.0f;
bool  imu_ok  = false;
bool  ina_ok  = false;

// ====== timers ======
unsigned long t_last_out = 0, t_last_cur = 0, t_last_imu = 0;
unsigned long t_last_i2c_retry = 0;
unsigned long t_last_i2c_recover = 0;
uint32_t t_next_motor_us = 0;

constexpr size_t CMD_BUFFER_LEN = 96;
char cmd_buffer[CMD_BUFFER_LEN];
size_t cmd_len = 0;

constexpr uint8_t EVENT_REPEAT_PACKETS = 5;
constexpr uint16_t EVENT_MOTOR_COMMAND_ACCEPTED = 1;
constexpr uint16_t EVENT_MOTOR_COMMAND_FAILED = 2;

uint32_t motor_sequence = 0;
uint32_t pending_motor_event_id = 0;
uint16_t pending_motor_event_flags = 0;
uint32_t pending_motor_event_command_rx_us = 0;
uint32_t pending_motor_event_dxl_write_done_us = 0;
uint8_t pending_motor_event_repeats = 0;

// // ====== DXL current / torque mode config ======
constexpr int16_t XC330_CURRENT_LIMIT_MA = 500;

// XL330-M288-T approximate output torque constant
constexpr float WRIST_KT_NM_PER_A = 1.150f;
constexpr float WRIST_COMMAND_SIGN = 1.0f;
constexpr float DXL_VELOCITY_RPM_PER_RAW = 0.229f;

uint8_t wrist_mode = OP_EXTENDED_POSITION;
int16_t dxl_goal_current_mA = 0;
float dxl_goal_velocity_rpm = 0.0f;
uint32_t wrist_profile_velocity_raw = 0;
uint32_t wrist_profile_acceleration_raw = 0;

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

bool init_ina219(bool verbose = true) {
#if ENABLE_INA219_CURRENT
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
#else
  reset_current_cache();
  ina_ok = false;
  return false;
#endif
}

bool init_bno055(bool verbose = true) {
#if ENABLE_BNO055_IMU
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
#else
  reset_imu_cache();
  imu_ok = false;
  return false;
#endif
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

  #if ENABLE_INA219_CURRENT
  if (!ina_ok) {
    init_ina219();
  }
  #endif
  if (ENABLE_BNO055_IMU && !imu_ok) {
    init_bno055();
  }
}

// ── IMU helper ───────────────────────────────────────────────────────────────
void imu_update() {
#if ENABLE_BNO055_IMU
  if (!imu_ok) return;

  imu::Quaternion q = bno.getQuat();
  imu_qw = (float)q.w();
  imu_qx = (float)q.x();
  imu_qy = (float)q.y();
  imu_qz = (float)q.z();
#endif
}

bool update_motor_feedback() {
  int32_t received = dxl.read(
    WRIST_ID,
    DXL_PRESENT_VELOCITY_ADDR,
    DXL_PRESENT_VELOCITY_POSITION_LEN,
    reinterpret_cast<uint8_t*>(&motor_feedback),
    sizeof(motor_feedback),
    2
  );
  if (received <= 0) {
    motor_read_failures++;
    return false;
  }

  motor_timestamp_us = micros();
  dec_count = (long)motor_feedback.position_raw;
  wrist_deg = motor_feedback.position_raw * DXL_POSITION_DEG_PER_RAW;
  dxl_velocity_rpm = motor_feedback.velocity_raw * DXL_VELOCITY_RPM_PER_RAW;
  return true;
}

void stream_motor_fast() {
  if (!streaming || fast_hz == 0) {
    return;
  }

  uint32_t event_id = 0;
  uint16_t event_flags = 0;
  uint32_t event_command_rx_us = 0;
  uint32_t event_dxl_write_done_us = 0;
  if (pending_motor_event_repeats > 0) {
    event_id = pending_motor_event_id;
    event_flags = pending_motor_event_flags;
    event_command_rx_us = pending_motor_event_command_rx_us;
    event_dxl_write_done_us = pending_motor_event_dxl_write_done_us;
    pending_motor_event_repeats--;
    if (pending_motor_event_repeats == 0) {
      pending_motor_event_id = 0;
      pending_motor_event_flags = 0;
      pending_motor_event_command_rx_us = 0;
      pending_motor_event_dxl_write_done_us = 0;
    }
  }

  motor_sequence++;
  Serial.print("M,");
  Serial.print(motor_sequence);
  Serial.print(",");
  Serial.print(motor_timestamp_us);
  Serial.print(",");
  Serial.print(motor_feedback.position_raw);
  Serial.print(",");
  Serial.print(motor_feedback.velocity_raw);
  Serial.print(",");
  Serial.print(event_id);
  Serial.print(",");
  Serial.print(event_flags);
  if (event_id != 0 || event_flags != 0) {
    Serial.print(",");
    Serial.print(event_command_rx_us);
    Serial.print(",");
    Serial.print(event_dxl_write_done_us);
  }
  Serial.println();
}

void queue_motor_event(uint32_t event_id, uint16_t flags,
                       uint32_t command_rx_us = 0,
                       uint32_t dxl_write_done_us = 0) {
  pending_motor_event_id = event_id;
  pending_motor_event_flags = flags;
  pending_motor_event_command_rx_us = command_rx_us;
  pending_motor_event_dxl_write_done_us = dxl_write_done_us;
  pending_motor_event_repeats = EVENT_REPEAT_PACKETS;
}

// Current / velocity mode helpers
float motor_deg_s_to_motor_rpm(float motor_deg_s) {
  return motor_deg_s / 6.0f;
}

int32_t rpm_to_velocity_raw(float rpm) {
  return (int32_t)round(rpm / DXL_VELOCITY_RPM_PER_RAW);
}

bool configure_wrist_position_profile() {
  bool ok_acc = dxl.writeControlTableItem(PROFILE_ACCELERATION, WRIST_ID, wrist_profile_acceleration_raw);
  bool ok_vel = dxl.writeControlTableItem(PROFILE_VELOCITY, WRIST_ID, wrist_profile_velocity_raw);
  return ok_acc && ok_vel;
}

void stop_wrist_command() {
  if (wrist_mode == OP_CURRENT) {
    dxl.setGoalCurrent(WRIST_ID, 0, UNIT_MILLI_AMPERE);
    dxl_goal_current_mA = 0;
  } else if (wrist_mode == OP_VELOCITY) {
    dxl.setGoalVelocity(WRIST_ID, 0);
    dxl_goal_velocity_rpm = 0.0f;
  }
}

int16_t clamp_current_mA(float current_mA_cmd) {
  if (current_mA_cmd > XC330_CURRENT_LIMIT_MA) {
    return XC330_CURRENT_LIMIT_MA;
  }
  if (current_mA_cmd < -XC330_CURRENT_LIMIT_MA) {
    return -XC330_CURRENT_LIMIT_MA;
  }
  return (int16_t)current_mA_cmd;
}

bool set_wrist_current_mode() {
  stop_wrist_command();
  delay(5);
  dxl.torqueOff(WRIST_ID);

  // dxl.writeControlTableItem(CURRENT_LIMIT, WRIST_ID, XC330_CURRENT_LIMIT_MA);

  bool ok = dxl.setOperatingMode(WRIST_ID, OP_CURRENT);

  if (!ok) {
    wrist_mode = OP_EXTENDED_POSITION;
    dxl.torqueOn(WRIST_ID);
    return false;
  }

  dxl.setGoalCurrent(WRIST_ID, 0, UNIT_MILLI_AMPERE);
  dxl.torqueOn(WRIST_ID);

  wrist_mode = OP_CURRENT;
  dxl_goal_current_mA = 0;

  return true;
}

bool set_wrist_extended_position_mode() {
  stop_wrist_command();
  delay(5);

  long hold_pos = dec_count;

  dxl.torqueOff(WRIST_ID);
  bool ok = dxl.setOperatingMode(WRIST_ID, OP_EXTENDED_POSITION);

  if (!ok) {
    dxl.torqueOn(WRIST_ID);
    return false;
  }

  configure_wrist_position_profile();
  dxl.setGoalPosition(WRIST_ID, hold_pos, UNIT_RAW);
  dxl.torqueOn(WRIST_ID);

  wrist_mode = OP_EXTENDED_POSITION;

  return true;
}

bool set_wrist_position_mode() {
  stop_wrist_command();
  delay(5);

  long hold_pos = dec_count;

  dxl.torqueOff(WRIST_ID);
  bool ok = dxl.setOperatingMode(WRIST_ID, OP_POSITION);

  configure_wrist_position_profile();
  dxl.setGoalPosition(WRIST_ID, hold_pos, UNIT_RAW);
  dxl.torqueOn(WRIST_ID);

  wrist_mode = OP_POSITION;

  return ok;
}

bool set_wrist_current_mA(float current_mA_cmd) {
  if (wrist_mode != OP_CURRENT) {
    return false;
  }

  int16_t cmd = clamp_current_mA(current_mA_cmd);
  bool ok = dxl.setGoalCurrent(WRIST_ID, cmd, UNIT_MILLI_AMPERE);

  if (ok) {
    dxl_goal_current_mA = cmd;
  }

  return ok;
}

bool set_wrist_torque_Nm(float torque_Nm_cmd) {
  float current_mA_cmd = torque_Nm_cmd / WRIST_KT_NM_PER_A * 1000.0f;
  return set_wrist_current_mA(current_mA_cmd);
}


bool set_wrist_velocity_mode() {
  stop_wrist_command();
  delay(5);

  dxl.torqueOff(WRIST_ID);
  bool ok = dxl.setOperatingMode(WRIST_ID, OP_VELOCITY);

  if (!ok) {
    dxl.torqueOn(WRIST_ID);
    return false;
  }

  dxl.setGoalVelocity(WRIST_ID, 0);
  dxl.torqueOn(WRIST_ID);

  wrist_mode = OP_VELOCITY;
  dxl_goal_velocity_rpm = 0.0f;

  return true;
}

bool set_wrist_velocity_rpm(float rpm_cmd) {
  if (wrist_mode != OP_VELOCITY) {
    return false;
  }

  bool ok = dxl.setGoalVelocity(WRIST_ID, rpm_to_velocity_raw(rpm_cmd));

  if (ok) {
    dxl_goal_velocity_rpm = rpm_cmd;
  }

  return ok;
}

bool set_wrist_velocity_deg_s(float motor_deg_s_cmd) {
  return set_wrist_velocity_rpm(motor_deg_s_to_motor_rpm(motor_deg_s_cmd));
}

void process_command(String input) {
  input.trim();
  if (input.length() == 0) {
    return;
  }
  const uint32_t command_received_us = micros();
  if (input == "STREAM_ON") streaming = true;
  else if (input == "STREAM_OFF") streaming = false;
  else if (input.startsWith("OUT_HZ:")) out_hz = input.substring(7).toInt();
  else if (input.startsWith("FAST_HZ:")) fast_hz = input.substring(8).toInt();
  else if (input.startsWith("MOTOR_HZ:")) fast_hz = input.substring(9).toInt();
  else if (input.startsWith("CUR_HZ:")) cur_hz = input.substring(7).toInt();
  else if (input.startsWith("IMU_HZ:")) imu_hz = input.substring(7).toInt();
  else if (input.startsWith("ANG_HZ:")) fast_hz = input.substring(7).toInt();
  else if (input.startsWith("DEC_HZ:")) fast_hz = input.substring(7).toInt();
  else if (input == "PROFILE_FAST") {
    wrist_profile_acceleration_raw = 0;
    wrist_profile_velocity_raw = 0;
    bool ok = configure_wrist_position_profile();
    Serial.println(ok ? "PROFILE_FAST_OK" : "PROFILE_FAST_FAIL");
  }
  else if (input.startsWith("SET_PROFILE_VEL_RAW:")) {
    wrist_profile_velocity_raw = (uint32_t)input.substring(20).toInt();
    bool ok = configure_wrist_position_profile();
    Serial.println(ok ? "SET_PROFILE_VEL_RAW_OK" : "SET_PROFILE_VEL_RAW_FAIL");
  }
  else if (input.startsWith("SET_PROFILE_ACC_RAW:")) {
    wrist_profile_acceleration_raw = (uint32_t)input.substring(20).toInt();
    bool ok = configure_wrist_position_profile();
    Serial.println(ok ? "SET_PROFILE_ACC_RAW_OK" : "SET_PROFILE_ACC_RAW_FAIL");
  }
  else if (input.startsWith("SET_ANG:")) {
    if (wrist_mode == OP_EXTENDED_POSITION) {
      float ang = input.substring(8).toFloat();
      dxl.setGoalPosition(WRIST_ID, ang, UNIT_DEGREE);
    } else {
      Serial.println("SET_ANG_FAIL_NOT_EXT_MODE");
    }
  }
  else if (input.startsWith("SET_DMP:")) {
    int val = constrain(input.substring(8).toInt(), 0, 255);
    analogWrite(DAMPER_PIN, 255 - val);
  }
  else if (input == "TORQUE_OFF") {
    stop_wrist_command();
    delay(5);
    dxl.torqueOff(WRIST_ID);
  }
  else if (input == "TORQUE_ON") {
    if (wrist_mode == OP_CURRENT || wrist_mode == OP_VELOCITY) {
      stop_wrist_command();
      delay(5);
    }
    dxl.torqueOn(WRIST_ID);
  }
  else if (input == "MODE_CUR") {
    bool ok = set_wrist_current_mode();
    Serial.println(ok ? "MODE_CUR_OK" : "MODE_CUR_FAIL");
  }
  else if (input == "MODE_EXT") {
    bool ok = set_wrist_extended_position_mode();
    Serial.println(ok ? "MODE_EXT_OK" : "MODE_EXT_FAIL");
  }
  else if (input == "MODE_VEL") {
    bool ok = set_wrist_velocity_mode();
    Serial.println(ok ? "MODE_VEL_OK" : "MODE_VEL_FAIL");
  }
  else if (input.startsWith("SET_VEL_RPM:")) {
    float rpm = input.substring(12).toFloat();
    bool ok = set_wrist_velocity_rpm(rpm);
    Serial.println(ok ? "SET_VEL_RPM_OK" : "SET_VEL_RPM_FAIL");
  }
  else if (input.startsWith("SET_VEL_DPS:")) {
    float dps = input.substring(12).toFloat();
    bool ok = set_wrist_velocity_deg_s(dps);
    if (!ok) {
      Serial.println("SET_VEL_DPS_FAIL");
    }
  }
  else if (input.startsWith("SET_VEL_DPS_EVT:")) {
    int comma = input.indexOf(',');
    if (comma < 0) {
      queue_motor_event(0, EVENT_MOTOR_COMMAND_FAILED, command_received_us, micros());
    } else {
      uint32_t event_id = (uint32_t)strtoul(input.substring(16, comma).c_str(), nullptr, 10);
      float dps = input.substring(comma + 1).toFloat();
      bool ok = set_wrist_velocity_deg_s(dps);
      const uint32_t dxl_write_done_us = micros();
      queue_motor_event(
        event_id,
        ok ? EVENT_MOTOR_COMMAND_ACCEPTED : EVENT_MOTOR_COMMAND_FAILED,
        command_received_us,
        dxl_write_done_us
      );
    }
  }
  else if (input == "ZERO_VEL") {
    bool ok = set_wrist_velocity_rpm(0.0);
    Serial.println(ok ? "ZERO_VEL_OK" : "ZERO_VEL_FAIL");
  }
  else if (input.startsWith("SET_CUR:")) {
    float cur = input.substring(8).toFloat();
    bool ok = set_wrist_current_mA(cur);
    Serial.println(ok ? "SET_CUR_OK" : "SET_CUR_FAIL");
  }
  else if (input.startsWith("SET_TAU:")) {
    float tau = input.substring(8).toFloat();
    bool ok = set_wrist_torque_Nm(tau);
    Serial.println(ok ? "SET_TAU_OK" : "SET_TAU_FAIL");
  }
  else if (input == "ZERO_CUR") {
    bool ok = set_wrist_current_mA(0);
    Serial.println(ok ? "ZERO_CUR_OK" : "ZERO_CUR_FAIL");
  }
}

void poll_serial_commands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      cmd_buffer[cmd_len] = '\0';
      process_command(String(cmd_buffer));
      cmd_len = 0;
      continue;
    }
    if (cmd_len < CMD_BUFFER_LEN - 1) {
      cmd_buffer[cmd_len++] = c;
    } else {
      cmd_len = 0;
    }
  }
}

void setup() {
  Serial.begin(DEBUG_BAUD_RATE);
  pinMode(DAMPER_PIN, OUTPUT);
  analogWrite(DAMPER_PIN, 255);  // start with no resistance

  dxl.begin(HIGH_FREQ_DYNAMIXEL_BAUD_RATE);
  dxl.setPortProtocolVersion(2.0);
  dxl.torqueOff(WRIST_ID);
  dxl.setOperatingMode(WRIST_ID, OP_EXTENDED_POSITION);
  configure_wrist_position_profile();
  dxl.torqueOn(WRIST_ID);
  wrist_mode = OP_EXTENDED_POSITION;
  update_motor_feedback();
  t_next_motor_us = micros() + MOTOR_PERIOD_US;

  #if ENABLE_INA219_CURRENT || ENABLE_BNO055_IMU
  configure_wire();
  #endif

  // INA219 current sensing is disabled by default in the high-frequency build.
  #if ENABLE_INA219_CURRENT
  init_ina219();
  #else
  reset_current_cache();
  #endif

  // BNO055 (NDOF fusion mode — provides calibrated quaternion)
  if (ENABLE_BNO055_IMU) {
    init_bno055();
  } else {
    reset_imu_cache();
  }

  Serial.setTimeout(1);
}

void loop() {
  const uint32_t now_us = micros();
  const unsigned long now = millis();

  poll_serial_commands();

  if (fast_hz > 0 && (int32_t)(now_us - t_next_motor_us) >= 0) {
    const uint32_t motor_period_us = 1000000UL / fast_hz;
    t_next_motor_us += motor_period_us;
    if ((int32_t)(now_us - t_next_motor_us) >= (int32_t)motor_period_us) {
      t_next_motor_us = now_us + motor_period_us;
    }
    if (update_motor_feedback()) {
      stream_motor_fast();
    }
  }

  #if ENABLE_INA219_CURRENT || ENABLE_BNO055_IMU
  retry_i2c_devices();
  #endif

  // ---- IMU update ----
  if (imu_hz > 0 && (now - t_last_imu) >= (1000UL / imu_hz)) {
    t_last_imu = now;
    imu_update();
  }

  // ---- current update ----
  #if ENABLE_INA219_CURRENT
  if (cur_hz > 0 && (now - t_last_cur) >= (1000UL / cur_hz)) {
    t_last_cur = now;
    if (ina_ok) {
      current_mA = ina219.getCurrent_mA();
      if (wire_timed_out()) {
        ina_ok = false;
        reset_current_cache();
        recover_i2c_bus("INA219 read timeout");
      }
    }
  }
  #endif

  // ---- stream out ----
  // Format: t_ms, qw, qx, qy, qz, current_mA, wrist_deg, dec_count  (8 fields)
  if (streaming && out_hz > 0 && (now - t_last_out) >= (1000UL / out_hz)) {
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
