import threading
import time

import serial

from hardware.ring_buffer import InterpRingBuffer
from config import LC_BAUD, LC_COM_PORT, LC_INTER_BYTE_TIMEOUT, LC_SCALE_FACTORS, LC_SER_TIMEOUT


ATI_BINARY_RESOLVED_DATA_BYTES = 19
ATI_BINARY_RESOLVED_RECORD_BYTES = 20
ATI_BINARY_STATUS_FLAGS = set(range(16))


class LCWorker:
    """
    Manages a background thread that reads the Nano17 load cell stream,
    applies bias removal and scaling, and stores results in a ring buffer.

    Usage:
        worker = LCWorker()
        worker.connect("COM4", on_success=..., on_error=...)
        worker.disconnect()
    """

    def __init__(self):
        self.ser       = None
        self.thread    = None
        self.stop_evt  = threading.Event()
        self.connected = False
        self.ring      = InterpRingBuffer(maxlen=4000)
        self.raw_recorder = None
        self.bias      = [0.0] * 7
        self.status    = "LC: Disconnected"
        self._offset   = [0.0] * 6
        self._offset_lock = threading.Lock()
        self.data_mode = "binary_resolved"
        self._binary_rx = bytearray()
        self.status_flag_counts = {flag: 0 for flag in ATI_BINARY_STATUS_FLAGS}
        self._last_status_flag_report_t = 0.0
        self._last_record_host_rx_ns = None

    def connect(self, port: str = LC_COM_PORT, on_success=None, on_error=None):
        if self.connected:
            return
        self.stop_evt.clear()
        self.ring.clear()
        self._offset = [0.0] * 6   # reset offset on every connect
        self._binary_rx = bytearray()
        self.status_flag_counts = {flag: 0 for flag in ATI_BINARY_STATUS_FLAGS}
        self._last_status_flag_report_t = 0.0
        self.thread = threading.Thread(
            target=self._run,
            args=(port, on_success, on_error),
            daemon=True)
        self.thread.start()

    def set_raw_recorder(self, raw_recorder):
        self.raw_recorder = raw_recorder

    def _run(self, port: str, on_success, on_error):
        try:
            self.ser = serial.Serial(
                port,
                LC_BAUD,
                timeout=LC_SER_TIMEOUT,
                inter_byte_timeout=LC_INTER_BYTE_TIMEOUT,
            )
        except Exception as e:
            self.status = f"LC: Error ({e})"
            if on_error:
                on_error(str(e))
            return

        time.sleep(1.0)

        # Configure resolved force/torque output in binary mode, then tare
        # from the first valid records. Keep the output as counts internally;
        # scaling to physical units happens after bias removal.
        try:
            self._configure_binary_resolved_stream()
            orig = self.ser.timeout
            self.ser.timeout = 2.0
            bias_samples = []
            deadline = time.perf_counter() + 2.0
            while len(bias_samples) < 20 and time.perf_counter() < deadline:
                vals = self._read_binary_resolved_record()
                if vals is not None and vals[0] == 0:
                    bias_samples.append(vals)
            if bias_samples:
                self.bias = [
                    sum(s[i] for s in bias_samples) / len(bias_samples)
                    for i in range(7)
                ]
            self.ser.timeout = orig
        except Exception as e:
            self.status = f"LC: Binary setup error ({e})"
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            if on_error:
                on_error(str(e))
            return

        self.connected = True
        self.status    = f"LC: Connected ({port}, binary)"
        if on_success:
            on_success()

        while not self.stop_evt.is_set():
            parsed = self._read_scaled_binary_resolved()
            if parsed is not None:
                host_rx_ns = self._last_record_host_rx_ns or time.perf_counter_ns()
                self.ring.append(host_rx_ns / 1_000_000_000.0, parsed)

    def _configure_binary_resolved_stream(self):
        self.ser.reset_input_buffer()
        try:
            self.ser.reset_output_buffer()
        except Exception:
            pass
        commands = [
            "CD A",
            "CV 3F",
            "CF 0",
            "SF 600",
            "SA 0",
            "SM 0",
            "CD E",
            "CD R",
            "CD B",
        ]
        for command in commands:
            self._write_ascii_command(command)
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        self.ser.write(b"QS\r")
        time.sleep(0.02)

    def _write_ascii_command(self, command: str):
        self.ser.write((command.strip() + "\r").encode("ascii"))
        time.sleep(0.05)

    @staticmethod
    def _signed24_be(b0: int, b1: int, b2: int) -> int:
        value = (int(b0) << 16) | (int(b1) << 8) | int(b2)
        if value & 0x800000:
            value -= 0x1000000
        return value

    def _binary_record_plausible(self, raw: list) -> bool:
        if len(raw) != 7:
            return False
        return int(raw[0]) in ATI_BINARY_STATUS_FLAGS

    @staticmethod
    def _binary_checksum_ok(record: bytes | bytearray) -> bool:
        if len(record) != ATI_BINARY_RESOLVED_RECORD_BYTES:
            return False
        return (sum(record[:ATI_BINARY_RESOLVED_DATA_BYTES]) & 0xFF) == record[ATI_BINARY_RESOLVED_DATA_BYTES]

    def _decode_binary_resolved_candidate(self, record: bytes | bytearray):
        if len(record) != ATI_BINARY_RESOLVED_RECORD_BYTES:
            return None
        if record[0] not in ATI_BINARY_STATUS_FLAGS:
            return None
        if not self._binary_checksum_ok(record):
            return None
        raw = [record[0]]
        payload = record[1:ATI_BINARY_RESOLVED_DATA_BYTES]
        for idx in range(0, 18, 3):
            raw.append(self._signed24_be(payload[idx], payload[idx + 1], payload[idx + 2]))
        if self._binary_record_plausible(raw):
            return raw
        return None

    def _read_binary_resolved_record(self):
        while not self.stop_evt.is_set():
            try:
                need = max(1, ATI_BINARY_RESOLVED_RECORD_BYTES - len(self._binary_rx))
                chunk = self.ser.read(need)
            except Exception:
                return None
            if not chunk:
                return None
            self._binary_rx.extend(chunk)
            while len(self._binary_rx) >= ATI_BINARY_RESOLVED_RECORD_BYTES:
                max_start = len(self._binary_rx) - ATI_BINARY_RESOLVED_RECORD_BYTES
                for start in range(max_start + 1):
                    record = self._binary_rx[start:start + ATI_BINARY_RESOLVED_RECORD_BYTES]
                    raw = self._decode_binary_resolved_candidate(record)
                    if raw is not None:
                        del self._binary_rx[:start + ATI_BINARY_RESOLVED_RECORD_BYTES]
                        return raw
                if len(self._binary_rx) > ATI_BINARY_RESOLVED_RECORD_BYTES - 1:
                    del self._binary_rx[:-(ATI_BINARY_RESOLVED_RECORD_BYTES - 1)]
                    break
        return None

    def _read_scaled_binary_resolved(self):
        raw = self._read_binary_resolved_record()
        if raw is None:
            return None
        host_rx_ns = time.perf_counter_ns()
        self._last_record_host_rx_ns = host_rx_ns
        status_flag = int(raw[0])
        scaled = self._scale_counts(raw) if status_flag == 0 else None
        self._record_lc_raw(raw, scaled, host_rx_ns)
        if status_flag != 0:
            self._record_status_flag(status_flag)
            return None
        return scaled

    def _record_lc_raw(self, raw: list, scaled: list | None, host_rx_ns: int):
        recorder = self.raw_recorder
        if recorder is None:
            return
        try:
            recorder.record_lc(raw, scaled, host_rx_ns)
        except Exception:
            pass

    def _record_status_flag(self, status_flag: int):
        if status_flag not in ATI_BINARY_STATUS_FLAGS:
            return
        self.status_flag_counts[status_flag] = self.status_flag_counts.get(status_flag, 0) + 1
        now = time.perf_counter()
        if now - self._last_status_flag_report_t < 0.5:
            return
        self._last_status_flag_report_t = now
        self.status = (
            f"LC: Connected (binary), status flag {status_flag} "
            f"count={self.status_flag_counts[status_flag]}"
        )

    def _parse(self, line: str):
        if (not line) or line.startswith(">"):
            return None
        parts = line.split(",")
        if len(parts) < 7:
            return None
        try:
            raw = [float(x) for x in parts[:7]]
        except ValueError:
            return None
        status_flag = int(raw[0])
        if status_flag != 0:
            self._record_status_flag(status_flag)
            return None
        return self._scale_counts(raw)

    def _scale_counts(self, raw: list):
        sf = LC_SCALE_FACTORS
        with self._offset_lock:
            offset = list(self._offset)
        return [
            (raw[1] - self.bias[1]) / sf[1] - offset[0],
            (raw[2] - self.bias[2]) / sf[2] - offset[1],
            (raw[3] - self.bias[3]) / sf[3] - offset[2],
            (raw[4] - self.bias[4]) / sf[4] - offset[3],
            (raw[5] - self.bias[5]) / sf[5] - offset[4],
            (raw[6] - self.bias[6]) / sf[6] - offset[5],
        ]

    def disconnect(self):
        self.connected = False
        self.stop_evt.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def retare(self, window_s: float = 0.5, min_samples: int = 20):
        """Soft re-zero using the recent output mean (no reconnect, no stream pause).

        Returns:
            (ok: bool, message: str)
        """
        if not self.connected:
            return False, "LC not connected"

        now = time.perf_counter()
        recent = [sample for _, sample in self.ring.get_since(now - max(0.1, window_s))]
        if len(recent) < min_samples:
            return False, f"Not enough LC samples to zero ({len(recent)}/{min_samples})"

        means = [sum(s[i] for s in recent) / len(recent) for i in range(6)]
        with self._offset_lock:
            self._offset = [self._offset[i] + means[i] for i in range(6)]

        return True, "LC soft zero complete"

    @property
    def current_marker(self) -> int:
        return 0
