import threading
import time

import serial
from pylsl import local_clock

from hardware.ring_buffer import InterpRingBuffer
from config import SERIAL_BAUD, SERIAL_TIMEOUT


class SerialWorker:
    """
    Manages a background thread that reads the exo UART stream and
    parses incoming CSV lines into the ring buffer.

    Usage:
        worker = SerialWorker()
        worker.connect("COM7", on_success=..., on_error=...)
        worker.send("SET_ANG:30.0")
        worker.disconnect()
    """

    def __init__(self):
        self.ser       = None
        self.thread    = None
        self.stop_evt  = threading.Event()
        self.connected = False
        self._wlock    = threading.Lock()
        self.ring      = InterpRingBuffer(maxlen=2000)

    def connect(self, port: str, on_success=None, on_error=None):
        if self.connected:
            return
        self.stop_evt.clear()
        self.ring.clear()
        self.thread = threading.Thread(
            target=self._run,
            args=(port, on_success, on_error),
            daemon=True)
        self.thread.start()

    def _run(self, port: str, on_success, on_error):
        try:
            self.ser = serial.Serial(port, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
        except Exception as e:
            if on_error:
                on_error(str(e))
            return
        time.sleep(2.0)
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass
        self.connected = True
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

    @staticmethod
    def _parse(line: str):
        """Parse a comma-separated 8-field line into a list of floats."""
        parts = line.split(",")
        if len(parts) < 8:
            return None
        try:
            return [float(p.strip()) for p in parts[:8]]
        except Exception:
            return None

    def send(self, cmd: str):
        if not (self.ser and self.ser.is_open):
            return
        with self._wlock:
            try:
                self.ser.write((cmd.strip() + "\n").encode("utf-8"))
            except Exception:
                pass

    def disconnect(self):
        self.connected = False
        self.stop_evt.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None