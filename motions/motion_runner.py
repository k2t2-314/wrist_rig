"""
motions/motion_runner.py — 100 Hz control loop executor
"""

import math
import threading
import time

from motions.base import ModeBase, Command


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

    def __init__(self, serial_worker, get_wrist_zero=None, get_imu_zero=None):
        """
        Args:
            serial_worker : SerialWorker or MockSerialWorker.
            get_wrist_zero: callable → float, encoder zero offset (motor deg).
            get_imu_zero  : callable → float, IMU angle zero offset (deg).
        """
        self.serial          = serial_worker
        self._get_wrist_zero = get_wrist_zero or (lambda: 0.0)
        self._get_imu_zero   = get_imu_zero   or (lambda: 0.0)

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

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "running":     self._running,
                "mode_name":   self._mode.name if self._mode else "",
                "phase":       self._phase,
                "loops":       self._loop_count,
                "elapsed_s":   self._elapsed,
                "last_servo":  self._last_servo,
                "last_damper": self._last_damper,
            }

    def start(self, mode: ModeBase):
        """Start running a motion mode. Stops any previous mode first."""
        if self._running:
            self.stop()
        self._mode = mode
        self._mode.reset()
        self._stop_evt.clear()
        self._loop_count  = 0
        self._elapsed     = 0.0
        self._last_servo  = None
        self._last_damper = None
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
        if release and self.serial.connected:
            self._send_damper(0)

    # ── Control loop ──────────────────────────────────────────────────────────

    def _run(self):
        t_start         = time.perf_counter()
        prev_servo_cmd  = None
        prev_damper_cmd = None

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

            # send only on change to reduce serial traffic
            if cmd.servo is not None:
                servo_rounded = round(cmd.servo, 1)
                if servo_rounded != prev_servo_cmd:
                    self._send_servo(servo_rounded)
                    prev_servo_cmd = servo_rounded
                    with self._lock:
                        self._last_servo = servo_rounded

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
            gear_deg = (sample[6] - wrist_zero) / 6.25
            raw_imu  = _quat_to_wrist_deg(sample[1], sample[2],
                                           sample[3], sample[4])
            return {
                "t":          t,
                "dt":         dt,
                "wrist_deg":  gear_deg,
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
            "imu_deg":    0.0,
            "current_mA": 0.0,
            "qw":         1.0,
            "qx":         0.0,
            "qy":         0.0,
            "qz":         0.0,
            "t_ms":       0.0,
        }

    # ── Serial commands ───────────────────────────────────────────────────────

    def _send_servo(self, gear_angle: float):
        if self.serial.connected:
            wrist_zero = self._get_wrist_zero()
            motor_ang  = gear_angle * 6.25 + wrist_zero
            self.serial.send(f"SET_ANG:{motor_ang:.1f}")

    def _send_damper(self, pwm: int):
        if self.serial.connected:
            self.serial.send(f"SET_DMP:{pwm}")
