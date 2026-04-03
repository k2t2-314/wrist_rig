import threading
import time

import serial
from pylsl import local_clock

from hardware.ring_buffer import InterpRingBuffer
from config import LC_BAUD, LC_COM_PORT, LC_SCALE_FACTORS, LC_SER_TIMEOUT


class LCWorker:
    """
    Manages a background thread that reads the Nano17 load cell stream,
    applies bias removal and scaling, and stores results in a ring buffer.

    Usage:
        worker = LCWorker()
        worker.connect("COM3", on_success=..., on_error=...)
        worker.disconnect()
    """

    def __init__(self):
        self.ser       = None
        self.thread    = None
        self.stop_evt  = threading.Event()
        self.connected = False
        self.ring      = InterpRingBuffer(maxlen=4000)
        self.bias      = [0.0] * 7
        self.status    = "LC: Disconnected"
        self._offset   = [0.0] * 6

    def connect(self, port: str = LC_COM_PORT, on_success=None, on_error=None):
        if self.connected:
            return
        self.stop_evt.clear()
        self.ring.clear()
        self._offset = [0.0] * 6   # reset offset on every connect
        self.thread = threading.Thread(
            target=self._run,
            args=(port, on_success, on_error),
            daemon=True)
        self.thread.start()

    def _run(self, port: str, on_success, on_error):
        try:
            self.ser = serial.Serial(port, LC_BAUD, timeout=LC_SER_TIMEOUT)
        except Exception as e:
            self.status = f"LC: Error ({e})"
            if on_error:
                on_error(str(e))
            return

        time.sleep(1.0)

        # Tare (bias capture)
        try:
            self.ser.write(b"CD R\rQS\r")
            time.sleep(1.5)
            self.ser.reset_input_buffer()
            orig = self.ser.timeout
            self.ser.timeout = 2.0
            for _ in range(20):
                line  = self.ser.readline().decode("utf-8", errors="ignore").strip()
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 7:
                    continue
                try:
                    vals = [float(x) for x in parts[:7]]
                    if vals[0] != 0:
                        continue
                    self.bias = vals[:7]
                    break
                except ValueError:
                    continue
            self.ser.timeout = orig
        except Exception:
            pass

        self.connected = True
        self.status    = f"LC: Connected ({port})"
        if on_success:
            on_success()

        while not self.stop_evt.is_set():
            try:
                raw = self.ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                raw = ""
            if not raw:
                continue
            parsed = self._parse(raw)
            if parsed is not None:
                self.ring.append(local_clock(), parsed)

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
        if raw[0] != 0:
            return None
        sf = LC_SCALE_FACTORS
        return [
            (raw[1] - self.bias[1]) / sf[1] - self._offset[0],
            (raw[2] - self.bias[2]) / sf[2] - self._offset[1],
            (raw[3] - self.bias[3]) / sf[3] - self._offset[2],
            (raw[4] - self.bias[4]) / sf[4] - self._offset[3],
            (raw[5] - self.bias[5]) / sf[5] - self._offset[4],
            (raw[6] - self.bias[6]) / sf[6] - self._offset[5],
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

    def retare(self):
        """Re-zero LC by reconnecting (triggers hardware tare)."""
        # offset reset happens in connect()
        pass

    @property
    def current_marker(self) -> int:
        return 0
