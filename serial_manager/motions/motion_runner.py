"""
motions/motion_runner.py — 100 Hz control loop executor
"""

import math
import threading
import time

from config import GEAR_RATIO, WRIST_COMMAND_SIGN, WRIST_ENCODER_SIGN, WRIST_VELOCITY_COMMAND_SIGN
from motions.base import ModeBase, Command

RPM_TO_DEG_S = 6.0


def _quat_to_wrist_deg(qw: float, qx: float, qy: float, qz: float) -> float:
    """Yaw from quaternion, negated to match encoder sign: +extend, -flex."""
    sinz = 2.0 * (qw * qz + qx * qy)
    cosz = 1.0 - 2.0 * (qy * qy + qz * qz)
    return -math.degrees(math.atan2(sinz, cosz))


class MotionRunner:
    """
    Background thread that runs a MotionMode at ~100 Hz.

    The runner:
    1. Reads the latest parsed serial data from serial_worker.ring
    2. Builds a state dict
    3. Calls mode.compute(state) → Command
    4. Sends servo/damper commands only when values change
    5. Stops when mode.done or stop() is called

    Usage:
        runner = MotionRunner(serial_worker,
                              get_wrist_zero=lambda: app.wrist_zero,
                              get_imu_zero=lambda: app.imu_zero)
        runner.start(mode)
        runner.stop()
        runner.is_running
        runner.active_mode
    """

    LOOP_PERIOD = 0.010   # 10 ms → 100 Hz

    def __init__(
        self,
        serial_worker,
        get_wrist_zero=None,
        get_imu_zero=None,
        on_marker_pulse=None,
        reserve_marker_event_id=None,
    ):
        """
        Args:
            serial_worker : SerialWorker or MockSerialWorker.
            get_wrist_zero: callable → float, encoder zero offset (motor deg).
            get_imu_zero  : callable → float, IMU angle zero offset (deg).
        """
        self.serial          = serial_worker
        self._get_wrist_zero = get_wrist_zero or (lambda: 0.0)
        self._get_imu_zero   = get_imu_zero   or (lambda: 0.0)
        self._on_marker_pulse = on_marker_pulse
        self._reserve_marker_event_id = reserve_marker_event_id

        self._thread   = None
        self._stop_evt = threading.Event()
        self._mode     = None
        self._lock     = threading.Lock()

        self._running     = False
        self._phase       = ""
        self._loop_count  = 0
        self._elapsed     = 0.0
        self._last_servo  = None
        self._last_damper = None
        self._velocity_mode_active = False
        self._last_velocity_send_t = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_mode(self):
        return self._mode

    @property
    def phase(self) -> str:
        return self._phase


    def start(self, mode: ModeBase, reset: bool = True):
        """Start running a motion mode. Stops any previous mode first."""
        if self._running:
            self.stop()
        self._mode = mode
        if reset:
            self._mode.reset()
        self._stop_evt.clear()
        self._loop_count  = 0
        self._elapsed     = 0.0
        self._last_servo  = None
        self._last_damper = None
        self._velocity_mode_active = False
        self._last_velocity_send_t = 0.0
        self._phase       = ""
        self._running     = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, release: bool = True):
        """
        Stop the control loop.

        Args:
            release: if True, send SET_DMP:0 to release the damper.
        """
        if not self._running:
            return
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._running = False
        self._mode    = None
        if self.serial.connected:
            if self._velocity_mode_active:
                self._send_velocity_stop()
                self._velocity_mode_active = False
            if release:
                self._send_damper(0)

    # ── Control loop ──────────────────────────────────────────────────────────

    def _run(self):
        t_start         = time.perf_counter()
        prev_servo_cmd  = None
        prev_damper_cmd = None
        prev_velocity_cmd = None
        pending_marker_pulse = None

        while not self._stop_evt.is_set():
            loop_start = time.perf_counter()
            t_now      = loop_start - t_start
            dt         = self.LOOP_PERIOD

            state = self._read_state(t_now, dt)
            mode  = self._mode
            if mode is None:
                break

            try:
                cmd = mode.compute(state)
            except Exception:
                cmd = Command()

            velocity_cmd = getattr(cmd, "velocity", None)
            marker_pulse = getattr(cmd, "marker_pulse", None)
            if marker_pulse is not None:
                if cmd.servo is None and velocity_cmd is None:
                    self._emit_marker_pulse(int(marker_pulse))
                else:
                    pending_marker_pulse = int(marker_pulse)

            if velocity_cmd is not None:
                sent_velocity = False
                marker_event_id = None
                velocity_value = float(velocity_cmd)
                if abs(velocity_value) <= 1e-9:
                    if cmd.servo is None:
                        if not self._velocity_mode_active:
                            self._send_velocity_mode()
                            time.sleep(0.02)
                            self._velocity_mode_active = True
                            self._last_velocity_send_t = 0.0
                            prev_servo_cmd = None
                        resend_due = (loop_start - self._last_velocity_send_t) >= 0.20
                        if prev_velocity_cmd != 0.0 or resend_due:
                            sent_velocity = self._send_velocity_zero()
                            if sent_velocity:
                                self._last_velocity_send_t = loop_start
                            prev_velocity_cmd = 0.0
                        else:
                            sent_velocity = True
                    else:
                        if self._velocity_mode_active or prev_velocity_cmd not in (None, 0.0):
                            sent_velocity = self._send_velocity_stop()
                        else:
                            sent_velocity = True
                        self._velocity_mode_active = False
                        prev_velocity_cmd = 0.0
                else:
                    velocity_rounded = round(velocity_value, 3)
                    if not self._velocity_mode_active:
                        self._send_velocity_mode()
                        time.sleep(0.02)
                        self._velocity_mode_active = True
                        self._last_velocity_send_t = 0.0
                        prev_velocity_cmd = None
                        prev_servo_cmd = None
                    resend_due = (loop_start - self._last_velocity_send_t) >= 0.05
                    if pending_marker_pulse is not None:
                        marker_event_id = self._new_marker_event_id()
                    if velocity_rounded != prev_velocity_cmd or resend_due:
                        sent_velocity = self._send_velocity(velocity_rounded, event_id=marker_event_id)
                        if sent_velocity:
                            self._last_velocity_send_t = loop_start
                        prev_velocity_cmd = velocity_rounded
                    else:
                        sent_velocity = True
                if sent_velocity and pending_marker_pulse is not None:
                    self._emit_marker_pulse(pending_marker_pulse, event_id=marker_event_id)
                    pending_marker_pulse = None

            # send only on change to reduce serial traffic
            can_send_servo = velocity_cmd is None or (abs(float(velocity_cmd)) <= 1e-9 and cmd.servo is not None)
            if cmd.servo is not None and can_send_servo:
                if self._velocity_mode_active:
                    self._send_velocity_stop()
                    self._velocity_mode_active = False
                    prev_velocity_cmd = 0.0
                servo_rounded = round(cmd.servo, 1)
                if servo_rounded != prev_servo_cmd:
                    sent_servo = self._send_servo(servo_rounded)
                    prev_servo_cmd = servo_rounded
                    with self._lock:
                        self._last_servo = servo_rounded
                    if sent_servo and pending_marker_pulse is not None:
                        self._emit_marker_pulse(pending_marker_pulse)
                        pending_marker_pulse = None

            if cmd.damper is not None:
                damper_val = int(max(0, min(255, cmd.damper)))
                if damper_val != prev_damper_cmd:
                    self._send_damper(damper_val)
                    prev_damper_cmd = damper_val
                    with self._lock:
                        self._last_damper = damper_val

            with self._lock:
                self._loop_count += 1
                self._elapsed     = t_now
                if hasattr(mode, "phase"):
                    self._phase = mode.phase

            if mode.done:
                break

            elapsed    = time.perf_counter() - loop_start
            sleep_time = self.LOOP_PERIOD - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        self._running = False

    # ── State reading ─────────────────────────────────────────────────────────

    def _read_state(self, t: float, dt: float) -> dict:
        sample, _ts  = self.serial.ring.get_latest()
        wrist_zero   = self._get_wrist_zero()
        imu_zero     = self._get_imu_zero()

        if sample and len(sample) >= 8:
            gear_deg = WRIST_ENCODER_SIGN * (sample[6] - wrist_zero) / GEAR_RATIO
            wrist_velocity_deg_s = (
                WRIST_ENCODER_SIGN * float(sample[8]) * RPM_TO_DEG_S / GEAR_RATIO
                if len(sample) > 8 else 0.0
            )
            raw_imu  = _quat_to_wrist_deg(sample[1], sample[2],
                                           sample[3], sample[4])
            return {
                "t":          t,
                "dt":         dt,
                "wrist_deg":  gear_deg,
                "wrist_velocity_deg_s": wrist_velocity_deg_s,
                "imu_deg":    raw_imu - imu_zero,
                "current_mA": sample[5],
                "qw":         sample[1],
                "qx":         sample[2],
                "qy":         sample[3],
                "qz":         sample[4],
                "t_ms":       sample[0],
            }
        return {
            "t":          t,
            "dt":         dt,
            "wrist_deg":  0.0,
            "wrist_velocity_deg_s": 0.0,
            "imu_deg":    0.0,
            "current_mA": 0.0,
            "qw":         1.0,
            "qx":         0.0,
            "qy":         0.0,
            "qz":         0.0,
            "t_ms":       0.0,
        }

    # ── Serial commands ───────────────────────────────────────────────────────

    def _new_marker_event_id(self):
        callback = self._reserve_marker_event_id
        if callback is None:
            return None
        try:
            return int(callback())
        except Exception:
            return None

    def _emit_marker_pulse(self, code: int, event_id=None):
        callback = self._on_marker_pulse
        if callback is None:
            return
        try:
            callback(int(code), event_id=event_id)
        except Exception:
            pass

    def _send_servo(self, gear_angle: float):
        if self.serial.connected:
            wrist_zero = self._get_wrist_zero()
            motor_ang  = gear_angle * WRIST_COMMAND_SIGN * GEAR_RATIO + wrist_zero
            self.serial.send(f"SET_ANG:{motor_ang:.1f}")
            return True
        return False

    def _send_velocity_mode(self):
        if self.serial.connected:
            self.serial.send("MODE_VEL")
            return True
        return False

    def _send_velocity(self, wrist_velocity_deg_s: float, event_id=None):
        if self.serial.connected:
            command_velocity = WRIST_VELOCITY_COMMAND_SIGN * float(wrist_velocity_deg_s) * GEAR_RATIO
            if event_id is not None and hasattr(self.serial, "send_velocity_dps_event"):
                return bool(self.serial.send_velocity_dps_event(int(event_id), command_velocity))
            self.serial.send(f"SET_VEL_DPS:{command_velocity:.3f}")
            return True
        return False

    def _send_velocity_zero(self):
        if self.serial.connected:
            self.serial.send("ZERO_VEL")
            return True
        return False

    def _send_velocity_stop(self):
        if self.serial.connected:
            self.serial.send("ZERO_VEL")
            self.serial.send("MODE_EXT")
            time.sleep(0.02)
            return True
        return False

    def _send_damper(self, pwm: int):
        if self.serial.connected:
            self.serial.send(f"SET_DMP:{pwm}")
