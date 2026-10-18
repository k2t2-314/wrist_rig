import csv
import os
import queue
import threading

from config import GEAR_RATIO, WRIST_ENCODER_SIGN


ATI_SAMPLE_ESTIMATE_DELAY_NS = None
DXL_POSITION_DEG_PER_RAW = 0.088
DXL_VELOCITY_RPM_PER_RAW = 0.229
RPM_TO_DEG_S = 6.0


class _AsyncCsvWriter:
    def __init__(self, fieldnames: list[str], name: str):
        self._fieldnames = list(fieldnames)
        self._name = str(name)
        self._queue = queue.Queue()
        self._thread = None
        self._fp = None
        self._writer = None
        self._active = False
        self._lock = threading.Lock()
        self._path = ""
        self._rows_written = 0
        self._dropped_rows = 0
        self._write_errors = 0
        self._start_errors = 0
        self._last_error = ""

    @property
    def active(self) -> bool:
        return self._active

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": self._name,
                "active": self._active,
                "path": self._path,
                "rows_written": self._rows_written,
                "dropped_rows": self._dropped_rows,
                "write_errors": self._write_errors,
                "start_errors": self._start_errors,
                "last_error": self._last_error,
                "backlog": self._queue.qsize(),
            }

    def start(self, filepath: str):
        self.stop()
        with self._lock:
            self._path = filepath
            self._rows_written = 0
            self._dropped_rows = 0
            self._write_errors = 0
            self._last_error = ""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            self._fp = open(filepath, "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._fp, fieldnames=self._fieldnames)
            self._writer.writeheader()
            self._active = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except Exception as exc:
            try:
                if self._fp:
                    self._fp.close()
            except Exception:
                pass
            with self._lock:
                self._start_errors += 1
                self._write_errors += 1
                self._last_error = str(exc)
            self._active = False
            self._fp = None
            self._writer = None
            raise

    def write(self, row: dict):
        if not self._active:
            with self._lock:
                self._dropped_rows += 1
            return
        self._queue.put(dict(row))

    def stop(self):
        if not self._active and self._fp is None:
            return
        self._active = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                with self._lock:
                    self._write_errors += 1
                    self._last_error = "CSV writer thread did not stop within 2.0 s"
        self._thread = None
        try:
            if self._fp:
                self._fp.flush()
                self._fp.close()
        except Exception as exc:
            with self._lock:
                self._write_errors += 1
                self._last_error = str(exc)
        self._fp = None
        self._writer = None

    def _run(self):
        rows_since_flush = 0
        while True:
            row = self._queue.get()
            if row is None:
                break
            try:
                if self._writer is not None:
                    self._writer.writerow(row)
                    with self._lock:
                        self._rows_written += 1
                    rows_since_flush += 1
                    if rows_since_flush >= 1000 and self._fp is not None:
                        self._fp.flush()
                        rows_since_flush = 0
            except Exception as exc:
                with self._lock:
                    self._write_errors += 1
                    self._last_error = str(exc)


class RawSyncRecorder:
    LC_HEADER = [
        "record_index",
        "host_rx_ns",
        "host_rx_rel_ns",
        "ati_sample_est_ns",
        "ati_sample_est_rel_ns",
        "status_flag",
        "raw_fx_counts",
        "raw_fy_counts",
        "raw_fz_counts",
        "raw_tx_counts",
        "raw_ty_counts",
        "raw_tz_counts",
        "fx_N",
        "fy_N",
        "fz_N",
        "tx_Nmm",
        "ty_Nmm",
        "tz_Nmm",
    ]

    OPENRB_HEADER = [
        "record_index",
        "host_rx_ns",
        "host_rx_rel_ns",
        "openrb_sequence",
        "openrb_motor_us",
        "openrb_motor_rel_us",
        "position_raw",
        "velocity_raw",
        "openrb_event_id",
        "openrb_event_flags",
        "motor_deg",
        "wrist_deg",
        "motor_velocity_rpm",
        "wrist_velocity_deg_s",
        "raw_line",
        "openrb_command_received_us",
        "openrb_dxl_write_finished_us",
    ]

    def __init__(self, get_wrist_zero=None):
        self._get_wrist_zero = get_wrist_zero or (lambda: 0.0)
        self._lock = threading.Lock()
        self._experiment_t0_ns = ""
        self._lc_index = 0
        self._openrb_index = 0
        self._first_openrb_motor_us = None
        self._lc_writer = _AsyncCsvWriter(self.LC_HEADER, "lc_raw")
        self._openrb_writer = _AsyncCsvWriter(self.OPENRB_HEADER, "openrb_raw")

    @property
    def active(self) -> bool:
        return (
            self._lc_writer.active
            or self._openrb_writer.active
        )

    def start(self, *, lc_path: str, openrb_path: str, experiment_t0_s, openrb_command_path: str = ""):
        self.stop()
        with self._lock:
            self._experiment_t0_ns = self._to_ns(experiment_t0_s)
            self._lc_index = 0
            self._openrb_index = 0
            self._first_openrb_motor_us = None
        try:
            if lc_path:
                self._lc_writer.start(lc_path)
            if openrb_path:
                self._openrb_writer.start(openrb_path)
        except Exception:
            self.stop()
            raise

    def stop(self):
        self._lc_writer.stop()
        self._openrb_writer.stop()

    def stats(self) -> dict:
        writers = [
            self._lc_writer.stats(),
            self._openrb_writer.stats(),
        ]
        return {
            "writers": writers,
            "rows_written": sum(int(w.get("rows_written", 0) or 0) for w in writers),
            "dropped_rows": sum(int(w.get("dropped_rows", 0) or 0) for w in writers),
            "write_errors": sum(int(w.get("write_errors", 0) or 0) for w in writers),
            "backlog": sum(int(w.get("backlog", 0) or 0) for w in writers),
            "last_error": next((w.get("last_error", "") for w in writers if w.get("last_error")), ""),
        }

    def record_lc(self, raw: list, scaled: list | None, host_rx_ns: int):
        if not self._lc_writer.active or raw is None or len(raw) < 7:
            return
        with self._lock:
            self._lc_index += 1
            record_index = self._lc_index
            experiment_t0_ns = self._experiment_t0_ns
        host_rx_ns = int(host_rx_ns)
        if ATI_SAMPLE_ESTIMATE_DELAY_NS is None:
            ati_sample_est_ns = ""
        else:
            ati_sample_est_ns = host_rx_ns - int(ATI_SAMPLE_ESTIMATE_DELAY_NS)
        row = {
            "record_index": record_index,
            "host_rx_ns": host_rx_ns,
            "host_rx_rel_ns": self._delta_ns(host_rx_ns, experiment_t0_ns),
            "ati_sample_est_ns": ati_sample_est_ns,
            "ati_sample_est_rel_ns": self._delta_ns(ati_sample_est_ns, experiment_t0_ns),
            "status_flag": int(raw[0]),
            "raw_fx_counts": int(raw[1]),
            "raw_fy_counts": int(raw[2]),
            "raw_fz_counts": int(raw[3]),
            "raw_tx_counts": int(raw[4]),
            "raw_ty_counts": int(raw[5]),
            "raw_tz_counts": int(raw[6]),
        }
        for key, idx in [
            ("fx_N", 0),
            ("fy_N", 1),
            ("fz_N", 2),
            ("tx_Nmm", 3),
            ("ty_Nmm", 4),
            ("tz_Nmm", 5),
        ]:
            row[key] = self._fmt_float(scaled[idx]) if scaled and len(scaled) > idx else ""
        self._lc_writer.write(row)

    def record_openrb(self, raw_line: str, sample: list | None, host_rx_ns: int):
        if not self._openrb_writer.active:
            return
        parts = [p.strip() for p in str(raw_line).split(",")]
        if len(parts) < 4 or parts[0].upper() != "M":
            return
        try:
            if len(parts) >= 7:
                openrb_sequence = int(float(parts[1]))
                openrb_motor_us = int(float(parts[2]))
                position_raw = int(float(parts[3]))
                velocity_raw = int(float(parts[4]))
                openrb_event_id = int(float(parts[5]))
                openrb_event_flags = int(float(parts[6]))
                openrb_command_received_us = int(float(parts[7])) if len(parts) >= 9 and parts[7] != "" else ""
                openrb_dxl_write_finished_us = int(float(parts[8])) if len(parts) >= 9 and parts[8] != "" else ""
            else:
                openrb_sequence = ""
                openrb_motor_us = int(float(parts[1]))
                position_raw = int(float(parts[2]))
                velocity_raw = int(float(parts[3]))
                openrb_event_id = 0
                openrb_event_flags = 0
                openrb_command_received_us = ""
                openrb_dxl_write_finished_us = ""
        except Exception:
            return

        with self._lock:
            self._openrb_index += 1
            record_index = self._openrb_index
            experiment_t0_ns = self._experiment_t0_ns
            if self._first_openrb_motor_us is None:
                self._first_openrb_motor_us = openrb_motor_us
            first_openrb_motor_us = self._first_openrb_motor_us

        host_rx_ns = int(host_rx_ns)
        motor_deg = position_raw * DXL_POSITION_DEG_PER_RAW
        try:
            wrist_zero = float(self._get_wrist_zero())
        except Exception:
            wrist_zero = 0.0
        wrist_deg = WRIST_ENCODER_SIGN * (motor_deg - wrist_zero) / GEAR_RATIO
        motor_velocity_rpm = velocity_raw * DXL_VELOCITY_RPM_PER_RAW
        wrist_velocity_deg_s = WRIST_ENCODER_SIGN * motor_velocity_rpm * RPM_TO_DEG_S / GEAR_RATIO

        self._openrb_writer.write({
            "record_index": record_index,
            "host_rx_ns": host_rx_ns,
            "host_rx_rel_ns": self._delta_ns(host_rx_ns, experiment_t0_ns),
            "openrb_sequence": openrb_sequence,
            "openrb_motor_us": openrb_motor_us,
            "openrb_motor_rel_us": int(openrb_motor_us - first_openrb_motor_us),
            "position_raw": position_raw,
            "velocity_raw": velocity_raw,
            "openrb_event_id": openrb_event_id,
            "openrb_event_flags": openrb_event_flags,
            "motor_deg": self._fmt_float(motor_deg),
            "wrist_deg": self._fmt_float(wrist_deg),
            "motor_velocity_rpm": self._fmt_float(motor_velocity_rpm),
            "wrist_velocity_deg_s": self._fmt_float(wrist_velocity_deg_s),
            "raw_line": raw_line,
            "openrb_command_received_us": openrb_command_received_us,
            "openrb_dxl_write_finished_us": openrb_dxl_write_finished_us,
        })


    @staticmethod
    def _to_ns(value):
        if value in ("", None):
            return ""
        try:
            return int(round(float(value) * 1_000_000_000))
        except Exception:
            return ""

    @staticmethod
    def _delta_ns(value, base):
        if value in ("", None) or base in ("", None):
            return ""
        try:
            return int(value) - int(base)
        except Exception:
            return ""

    @staticmethod
    def _fmt_float(value) -> str:
        try:
            return f"{float(value):.6f}"
        except Exception:
            return ""
