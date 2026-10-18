import csv
import os
import threading


class TriggerTimingRecorder:
    """Write one timing-audit row for every trigger command sent to Arduino."""

    HEADER = [
        "event_index",
        "trigger_code",
        "event_id",
        "ack_status",
        "t_send_ns",
        "t_write_done_ns",
        "t_ack_ns",
        "host_round_trip_ns",
        "mcu_rx_us",
        "mcu_output_us",
        "mcu_ack_us",
        "rx_to_output_ns",
        "output_to_ack_ns",
        "rx_to_ack_ns",
        "edge_lower_ns",
        "edge_upper_ns",
        "estimated_edge_ns",
        "uncertainty_ns",
        "experiment_t0_ns",
        "estimated_event_rel_ns",
        "lc_latest_sample_ns",
        "lc_dt_to_edge_ns",
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self._fp = None
        self._writer = None
        self._path = ""
        self._event_index = 0
        self._rows_written = 0
        self._write_errors = 0
        self._start_errors = 0
        self._last_error = ""

    @property
    def active(self) -> bool:
        return self._writer is not None

    @property
    def path(self) -> str:
        return self._path

    def stats(self) -> dict:
        with self._lock:
            return {
                "name": "trigger_timing",
                "active": self._writer is not None,
                "path": self._path,
                "rows_written": self._rows_written,
                "write_errors": self._write_errors,
                "start_errors": self._start_errors,
                "last_error": self._last_error,
            }

    def start(self, filepath: str):
        self.stop()
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            self._fp = open(filepath, "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._fp, fieldnames=self.HEADER)
            self._writer.writeheader()
            with self._lock:
                self._path = filepath
                self._event_index = 0
                self._rows_written = 0
                self._write_errors = 0
                self._last_error = ""
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
                self._fp = None
                self._writer = None
                self._path = filepath
            raise

    def stop(self):
        with self._lock:
            fp = self._fp
            self._fp = None
            self._writer = None
            self._path = ""
        if fp:
            try:
                fp.close()
            except Exception as exc:
                with self._lock:
                    self._write_errors += 1
                    self._last_error = str(exc)

    def record(
        self,
        *,
        prev_marker,
        new_marker,
        trigger_code,
        timing,
        experiment_t0_s=None,
        lc_worker=None,
    ):
        with self._lock:
            writer = self._writer
            if writer is None:
                return
            self._event_index += 1
            event_index = self._event_index

            timing = dict(timing or {})
            edge_ns = timing.get("estimated_edge_ns")

            experiment_t0_ns = self._to_ns(experiment_t0_s)
            lc_latest_ns = self._latest_lc_sample_time_ns(lc_worker)
            raw_response = str(timing.get("raw_response", "") or "")
            ack_status = raw_response.split(",", 1)[0] if raw_response else "missing"
            row = {
                "event_index": event_index,
                "trigger_code": trigger_code,
                "event_id": timing.get("event_id", ""),
                "ack_status": ack_status,
                "t_send_ns": timing.get("t_send_ns", ""),
                "t_write_done_ns": timing.get("t_write_done_ns", ""),
                "t_ack_ns": timing.get("t_ack_ns", ""),
                "host_round_trip_ns": timing.get("host_round_trip_ns", ""),
                "mcu_rx_us": timing.get("mcu_rx_us", ""),
                "mcu_output_us": timing.get("mcu_output_us", ""),
                "mcu_ack_us": timing.get("mcu_ack_us", ""),
                "rx_to_output_ns": timing.get("rx_to_output_ns", ""),
                "output_to_ack_ns": timing.get("output_to_ack_ns", ""),
                "rx_to_ack_ns": timing.get("rx_to_ack_ns", ""),
                "edge_lower_ns": timing.get("edge_lower_ns", ""),
                "edge_upper_ns": timing.get("edge_upper_ns", ""),
                "estimated_edge_ns": edge_ns or "",
                "uncertainty_ns": timing.get("uncertainty_ns", ""),
                "experiment_t0_ns": experiment_t0_ns,
                "estimated_event_rel_ns": self._fmt_delta_ns(edge_ns, experiment_t0_ns),
                "lc_latest_sample_ns": lc_latest_ns,
                "lc_dt_to_edge_ns": self._fmt_delta_ns(lc_latest_ns, edge_ns),
            }
            try:
                writer.writerow(row)
                self._rows_written += 1
            except Exception as exc:
                self._write_errors += 1
                self._last_error = str(exc)
                return
            try:
                self._fp.flush()
            except Exception as exc:
                self._write_errors += 1
                self._last_error = str(exc)

    @staticmethod
    def _latest_lc_sample_time_ns(lc_worker):
        if lc_worker is None or not getattr(lc_worker, "connected", False):
            return ""
        try:
            _sample, lc_latest_s = lc_worker.ring.get_latest()
            return TriggerTimingRecorder._to_ns(lc_latest_s)
        except Exception:
            return ""

    @staticmethod
    def _to_ns(value):
        if value in ("", None):
            return ""
        try:
            return int(round(float(value) * 1_000_000_000))
        except Exception:
            return ""

    @staticmethod
    def _fmt_delta_ns(value, base):
        if value in ("", None) or base in ("", None):
            return ""
        try:
            return int(value) - int(base)
        except Exception:
            return ""
