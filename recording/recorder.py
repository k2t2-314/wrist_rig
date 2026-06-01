import csv
import math
import threading
import time
from config import GEAR_RATIO


def _quat_to_imu(qw: float, qx: float, qy: float, qz: float) -> float:
    sinz = 2.0 * (qw * qz + qx * qy)
    cosz = 1.0 - 2.0 * (qy * qy + qz * qz)
    return -math.degrees(math.atan2(sinz, cosz))


class BaseCsvRecorder:
    HEADER = [
        "t_rel",
        "lc_fx", "lc_fy", "lc_fz", "lc_tx", "lc_ty", "lc_tz",
        "imu_deg", "wrist_deg", "current_mA",
        "guide_pos_deg",
        "marker",
    ]

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
        self._t0        = 0.0
        self._rows      = 0
        self._marker    = 0

    # Public API

    def start(self, filepath: str, mode):
        """Open CSV file and start recording thread."""
        if self.recording:
            return
        self._mode_ref = mode
        self._t0       = time.perf_counter()
        self._rows     = 0
        self._marker   = 0
        self._fp       = open(filepath, "w", newline="", encoding="utf-8")
        self._writer   = csv.writer(self._fp)
        self._writer.writerow(self.HEADER)
        self._fp.flush()
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
        try:
            if self._fp:
                self._fp.flush()
                self._fp.close()
        except Exception:
            pass
        self._fp = None

    def get_rows(self) -> int:
        with self._lock:
            return self._rows

    def set_marker(self, value: int):
        self._marker = value

    @property
    def t0(self) -> float:
        return self._t0
    
    @property
    def current_marker(self) -> int:
        return self._marker

    # Internal
    def _run(self):
        last_ts = self._t0
        while not self._stop_evt.is_set():
            frames = self._get_serial_frames(last_ts)
            if not frames:
                time.sleep(0.005)
                continue
            for ts, s in frames:
                last_ts = ts
                row = self._build_row(ts, s)
                try:
                    self._writer.writerow(row)
                except Exception:
                    pass
                with self._lock:
                    self._rows += 1
            try:
                self._fp.flush()
            except Exception:
                pass
            time.sleep(0.005)

    def _get_serial_frames(self, last_ts: float) -> list:
        """Return new frames from serial ring, or empty list if unavailable."""
        if self.serial is None:
            return []
        try:
            return self.serial.ring.get_since(last_ts)
        except Exception:
            return []

    def _build_row(self, ts: float, s: list) -> list:
        # wrist + IMU
        if s and len(s) >= 7:
            wrist_deg = (s[6] - self._get_wrist_zero()) / GEAR_RATIO
            imu_deg   = _quat_to_imu(s[1], s[2], s[3], s[4]) - self._get_imu_zero()
            current   = s[5] if len(s) > 5 else ""
            wrist_str   = f"{wrist_deg:.4f}"
            imu_str     = f"{imu_deg:.4f}"
            current_str = f"{current:.2f}" if isinstance(current, float) else ""
        else:
            wrist_str = imu_str = current_str = ""

        # load cell
        lc_row = self._get_lc_row(ts)

        # guide_pos_deg (ActiveMovement only)
        mode = self._mode_ref
        guide_str = f"{mode.green_pos:.4f}" \
            if (mode and hasattr(mode, "green_pos")) else ""

        return [
            f"{ts:.6f}",
            *lc_row,
            imu_str, wrist_str, current_str,
            guide_str,
            str(self._marker),
        ]

    def _get_lc_row(self, ts: float) -> list:
        """Return 6 LC values interpolated at ts, or 6 empty strings."""
        if self.lc is None:
            return [""] * 6
        try:
            lc_vals, _ = self.lc.ring.get_interpolated(ts, max_dt=0.05)
            if lc_vals and len(lc_vals) >= 6:
                return [f"{v:.6f}" for v in lc_vals[:6]]
        except Exception:
            pass
        return [""] * 6
    
