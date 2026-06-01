import serial

from config import TRIGGER_BAUD, TRIGGER_TIMEOUT


class TriggerWorker:
    """Serial sender for the dedicated TMSi trigger Arduino."""

    def __init__(self):
        self.ser = None
        self.connected = False

    def connect(self, port: str, on_success=None, on_error=None):
        if self.connected:
            return
        try:
            self.ser = serial.Serial(port, TRIGGER_BAUD, timeout=TRIGGER_TIMEOUT)
            self.connected = True
            if on_success:
                on_success()
        except Exception as exc:
            self.ser = None
            self.connected = False
            if on_error:
                on_error(str(exc))

    def send_marker_code(self, code: int):
        if not (self.ser and self.ser.is_open):
            return
        code = int(code)
        if code == 0:
            return
        try:
            self.ser.write(f"TRIG:{code}\n".encode("utf-8"))
        except Exception:
            pass

    def send_vicon_start(self):
        if not (self.ser and self.ser.is_open):
            return
        try:
            self.ser.write(b"VICON:START\n")
        except Exception:
            pass

    def send_vicon_stop(self):
        if not (self.ser and self.ser.is_open):
            return
        try:
            self.ser.write(b"VICON:STOP\n")
        except Exception:
            pass

    def send_vicon_marker(self):
        if not (self.ser and self.ser.is_open):
            return
        try:
            self.ser.write(b"VICON:MARKER\n")
        except Exception:
            pass

    def disconnect(self):
        self.connected = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
