import csv
import math
import threading
import time
from collections import deque
from config import GEAR_RATIO, WRIST_ENCODER_SIGN

DXL_KT_NM_PER_A = 1.150
RPM_TO_DEG_S = 6.0
CSV_MARKER_ALIGNMENT_DELAY_S = 0.05


def _quat_to_imu(qw: float, qx: float, qy: float, qz: float) -> float:
    sinz = 2.0 * (qw * qz + qx * qy)
    cosz = 1.0 - 2.0 * (qy * qy + qz * qz)
    return -math.degrees(math.atan2(sinz, cosz))


class BaseCsvRecorder:
    RING_WAIT_TIMEOUT_S = 0.05

    HEADER = [
        "t_rel",
        "lc_fx", "lc_fy", "lc_fz", "lc_tx", "lc_ty", "lc_tz",
        "imu_deg", "wrist_deg", "current_mA",
        "torque_nmm",
        "damper_pwm",
        "guide_pos_deg",
        "guide_speed_deg_s",
        "marker",
    ]
    MARKER_COL = HEADER.index("marker")

    def __init__(self, serial_worker=None, lc_worker=None,
                 get_wrist_zero=None, get_imu_zero=None):
        """
        Args:
            serial_worker : SerialWorker or MockSerialWorker. May be None.
            lc_worker     : LCWorker or MockLCWorker. May be None.
            get_wrist_zero: callable → float, encoder zero offset in motor deg.
            get_imu_zero  : callable → float, IMU zero offset in deg.
        """
        self.serial = serial_worker
        self.lc     = lc_worker
        self._get_wrist_zero = get_wrist_zero or (lambda: 0.0)
        self._get_imu_zero   = get_imu_zero   or (lambda: 0.0)

        self._thread    = None
        self._stop_evt  = threading.Event()
        self._lock      = threading.Lock()

        self.recording  = False
        self._fp        = None
        self._writer    = None
        self._mode_ref  = None
        self._extra_headers = []
        self._extra_row_provider = None
        self._t0        = 0.0
        self._rows      = 0
        self._marker    = 0
        self._csv_marker = 0
        self._marker_pulse = None
        self._row_buffer = deque()
        self._pending_marker_pulses = []
        self._last_serial_query_ts = 0.0
        self._held_serial_sample = None
        self._pending_serial_frames = []

    # Public API

    def start(self, filepath: str, mode, extra_headers=None, extra_row_provider=None):
        """Open CSV file and start recording thread."""
        if self.recording:
            return
        self._mode_ref = mode
        self._extra_headers = list(extra_headers or [])
        self._extra_row_provider = extra_row_provider
        self._t0       = time.perf_counter()
        self._rows     = 0
        self._marker   = 0
        self._csv_marker = 0
        self._marker_pulse = None
        self._row_buffer.clear()
        self._pending_marker_pulses = []
        self._last_serial_query_ts = self._t0
        self._held_serial_sample = None
        self._pending_serial_frames = []
        if self.serial is not None and getattr(self.serial, "connected", False):
            try:
                self._held_serial_sample, serial_ts = self.serial.ring.get_latest()
                if serial_ts is not None:
                    self._last_serial_query_ts = serial_ts
            except Exception:
                self._held_serial_sample = None
        self._fp       = open(filepath, "w", newline="", encoding="utf-8")
        self._writer   = csv.writer(self._fp)
        self._writer.writerow([
            *self.HEADER,
            *self._extra_headers,
            "wrist_velocity_deg_s",
        ])
        self._stop_evt.clear()
        self.recording = True
        self._thread   = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop recording thread and close file."""
        if not self.recording:
            return
        self.recording = False
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._drain_row_buffer(force=True)
        try:
            if self._fp:
                self._fp.close()
        except Exception:
            pass
        self._fp = None
        self._extra_headers = []
        self._extra_row_provider = None

    def get_rows(self) -> int:
        with self._lock:
            return self._rows

    def set_marker(self, value: int):
        with self._lock:
            marker = int(value)
            self._marker = marker
            self._csv_marker = marker

    def pulse_marker(self, value: int):
        with self._lock:
            self._marker_pulse = int(value)

    def align_marker_transition(self, prev_marker: int, new_marker: int, edge_ns):
        """Move the CSV marker transition to the estimated hardware edge."""
        if edge_ns in ("", None):
            self.set_marker(new_marker)
            return
        try:
            edge_ns = int(edge_ns)
            prev_marker = int(prev_marker)
            new_marker = int(new_marker)
        except Exception:
            self.set_marker(new_marker)
            return
        with self._lock:
            self._marker = new_marker
            self._csv_marker = new_marker
            for _ts, row_ns, row in self._row_buffer:
                row[self.MARKER_COL] = str(new_marker if row_ns >= edge_ns else prev_marker)

    def align_marker_pulse(self, marker: int, edge_ns):
        """Place a one-row CSV marker pulse on the first sample at/after edge_ns."""
        if edge_ns in ("", None):
            self.pulse_marker(marker)
            return
        try:
            marker = int(marker)
            edge_ns = int(edge_ns)
        except Exception:
            self.pulse_marker(marker)
            return
        with self._lock:
            self._pending_marker_pulses.append((edge_ns, marker))
            self._apply_pending_marker_pulses_locked()

    @property
    def t0(self) -> float:
        return self._t0
    
    @property
    def current_marker(self) -> int:
        with self._lock:
            return self._marker

    # Internal
    def _consume_marker(self):
        with self._lock:
            if self._marker_pulse is not None:
                marker = self._marker_pulse
                self._marker_pulse = None
                return marker
            return self._csv_marker

    def _run(self):
        last_ts = self._t0
        while not self._stop_evt.is_set():
            if self.lc is not None and getattr(self.lc, "connected", False):
                frames = self._get_lc_frames(last_ts)
            else:
                frames = self._get_serial_frames(last_ts)
            if not frames:
                continue
            for frame in frames:
                if len(frame) == 3:
                    ts, s, lc_sample = frame
                else:
                    ts, s = frame
                    lc_sample = None
                last_ts = ts
                row = self._build_row(ts, s, lc_sample=lc_sample)
                self._enqueue_row(ts, row)
                self._drain_row_buffer()

    def _get_serial_frames(self, last_ts: float) -> list:
        """Return new frames from serial ring, or empty list if unavailable."""
        if self.serial is None:
            return []
        try:
            return self.serial.ring.wait_since(last_ts, timeout=self.RING_WAIT_TIMEOUT_S)
        except Exception:
            return []

    def _enqueue_row(self, ts: float, row: list):
        try:
            row_ns = int(round(float(ts) * 1_000_000_000))
        except Exception:
            row_ns = time.perf_counter_ns()
        with self._lock:
            self._row_buffer.append((float(ts), row_ns, row))
            self._apply_pending_marker_pulses_locked()

    def _drain_row_buffer(self, force: bool = False):
        if self._writer is None:
            return
        rows = []
        cutoff = time.perf_counter() - CSV_MARKER_ALIGNMENT_DELAY_S
        with self._lock:
            while self._row_buffer and (force or self._row_buffer[0][0] <= cutoff):
                rows.append(self._row_buffer.popleft()[2])
        if not rows:
            return
        for row in rows:
            try:
                self._writer.writerow(row)
            except Exception:
                pass
            with self._lock:
                self._rows += 1

    def _apply_pending_marker_pulses_locked(self):
        if not self._pending_marker_pulses or not self._row_buffer:
            return
        remaining = []
        for edge_ns, marker in self._pending_marker_pulses:
            applied = False
            for _ts, row_ns, row in self._row_buffer:
                if row_ns >= edge_ns:
                    row[self.MARKER_COL] = str(marker)
                    applied = True
                    break
            if not applied:
                remaining.append((edge_ns, marker))
        self._pending_marker_pulses = remaining

    def _get_lc_frames(self, last_ts: float) -> list:
        """Return LC-timed frames. Serial samples are sample-and-held to LC timestamps."""
        if self.lc is None:
            return []
        try:
            self._update_serial_cache()
            out = []
            for ts, lc_sample in self.lc.ring.wait_since(last_ts, timeout=self.RING_WAIT_TIMEOUT_S):
                out.append((ts, self._serial_sample_at_or_before(ts), lc_sample))
            return out
        except Exception:
            return []

    def _update_serial_cache(self):
        if self.serial is None or not getattr(self.serial, "connected", False):
            return
        try:
            frames = self.serial.ring.get_since(self._last_serial_query_ts)
        except Exception:
            return
        if not frames:
            return
        self._pending_serial_frames.extend(frames)
        self._last_serial_query_ts = max(ts for ts, _sample in frames)

    def _serial_sample_at_or_before(self, ts: float):
        if self.serial is None or not getattr(self.serial, "connected", False):
            return None
        remaining = []
        for frame_ts, sample in self._pending_serial_frames:
            if frame_ts <= ts:
                self._held_serial_sample = sample
            else:
                remaining.append((frame_ts, sample))
        self._pending_serial_frames = remaining
        return self._held_serial_sample

    def _build_row(self, ts: float, s: list, lc_sample=None) -> list:
        # wrist + IMU
        wrist_velocity_str = ""
        if s and len(s) >= 7:
            wrist_deg = WRIST_ENCODER_SIGN * (s[6] - self._get_wrist_zero()) / GEAR_RATIO
            imu_deg   = _quat_to_imu(s[1], s[2], s[3], s[4]) - self._get_imu_zero()
            current   = s[5] if len(s) > 5 else ""
            wrist_str   = f"{wrist_deg:.4f}"
            imu_str     = f"{imu_deg:.4f}"
            current_str = f"{current:.2f}" if isinstance(current, float) else ""
            wrist_velocity_str = self._get_servo_wrist_velocity_deg_s(s)
        else:
            wrist_str = imu_str = current_str = ""

        # load cell
        lc_row = self._get_lc_row(ts, lc_sample=lc_sample)

        # guide_pos_deg
        mode = self._mode_ref
        torque_str = self._get_torque_nmm()
        damper_str = self._get_damper_pwm(mode)
        guide_str = f"{mode.green_pos:.4f}" \
            if (mode and hasattr(mode, "green_pos")) else ""
        guide_speed_str = self._get_guide_speed_deg_s(mode)

        extra_row = self._get_extra_row(ts)

        marker = self._consume_marker()
        try:
            t_rel = max(0.0, float(ts) - float(self._t0))
        except Exception:
            t_rel = 0.0

        return [
            f"{t_rel:.6f}",
            *lc_row,
            imu_str, wrist_str, current_str,
            torque_str,
            damper_str,
            guide_str,
            guide_speed_str,
            str(marker),
            *extra_row,
            wrist_velocity_str,
        ]

    def _get_servo_wrist_velocity_deg_s(self, s: list) -> str:
        try:
            if not s or len(s) <= 8:
                return ""
            motor_rpm = float(s[8])
            wrist_velocity = WRIST_ENCODER_SIGN * motor_rpm * RPM_TO_DEG_S / GEAR_RATIO
            return f"{wrist_velocity:.6f}"
        except Exception:
            return ""

    def _get_guide_speed_deg_s(self, mode) -> str:
        if mode is None:
            return ""
        try:
            if hasattr(mode, "current_guide_speed"):
                return f"{float(mode.current_guide_speed()):.6f}"
            if hasattr(mode, "current_speed") and getattr(mode, "sub_phase", "") in ("go", "return"):
                return f"{float(mode.current_speed()):.6f}"
        except Exception:
            pass
        return ""

    def _get_torque_nmm(self) -> str:
        serial = self.serial
        if serial is None:
            return ""
        try:
            if getattr(serial, "dxl_control_mode", "") != "current":
                return ""
            goal_mA = float(getattr(serial, "dxl_goal_current_mA", 0.0) or 0.0)
            torque_nmm = goal_mA * DXL_KT_NM_PER_A
            return f"{torque_nmm:.6f}"
        except Exception:
            return ""

    def _get_damper_pwm(self, mode) -> str:
        if mode is None:
            return ""
        try:
            if hasattr(mode, "params") and "damper_pwm" in mode.params:
                return str(int(round(float(mode.params.get("damper_pwm", 0)))))
            if getattr(mode, "name", "") == "ramp_and_hold":
                return "255"
            if getattr(mode, "name", "") == "rest":
                return "0"
        except Exception:
            pass
        return ""

    def _get_extra_row(self, ts: float) -> list:
        if self._extra_row_provider is None:
            return []
        try:
            row = self._extra_row_provider(ts)
            if row is None:
                return [""] * len(self._extra_headers)
            row = list(row)
            if len(row) < len(self._extra_headers):
                row.extend([""] * (len(self._extra_headers) - len(row)))
            return row[:len(self._extra_headers)]
        except Exception:
            return [""] * len(self._extra_headers)

    def _get_lc_row(self, ts: float, lc_sample=None) -> list:
        """Return 6 LC values interpolated at ts, or 6 empty strings."""
        if lc_sample and len(lc_sample) >= 6:
            return [f"{v:.6f}" for v in lc_sample[:6]]
        if self.lc is None:
            return [""] * 6
        try:
            lc_vals, _ = self.lc.ring.get_interpolated(ts, max_dt=0.05)
            if lc_vals and len(lc_vals) >= 6:
                return [f"{v:.6f}" for v in lc_vals[:6]]
        except Exception:
            pass
        return [""] * 6
    
