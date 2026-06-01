"""
hardware/mock_workers.py — mock hardware workers for --mock mode

MockSerialWorker:
  - wrist_motor_deg and IMU track SET_ANG commands exactly (no sin wave)
    so CalibrationMode / RestMode / RAHMode hold-detection works correctly
  - current_mA uses a sin wave so the live monitor shows something moving

MockLCWorker:
  - all 6 channels use distinct sin waves so force charts are readable
"""

import math
import threading
import time

from hardware.ring_buffer import InterpRingBuffer


class MockSerialWorker:
    """
    Simulates SerialWorker at 100 Hz.

    8-field sample: [t_ms, qw, qx, qy, qz, current_mA, wrist_motor_deg, dec_count]

    wrist_motor_deg tracks SET_ANG commands directly.
    IMU quaternion is derived from wrist_motor_deg so encoder and IMU agree.
    current_mA = 80 + 40*sin(1.3t) — varies so live monitor is not dead.
    """

    def __init__(self):
        self.ring          = InterpRingBuffer(maxlen=2000)
        self.connected     = True
        self._stop_evt     = threading.Event()
        self._wrist_motor  = 0.0   # tracks last SET_ANG command
        self._lock         = threading.Lock()
        self._thread       = threading.Thread(target=self._run, daemon=True)
        self._thread.start()


    def _run(self):
        t = 0.0
        while not self._stop_evt.is_set():
            t += 0.01

            with self._lock:
                wrist_motor = self._wrist_motor

            current_mA = 80.0 + 40.0 * math.sin(1.3 * t)

            # derive quaternion from wrist_motor_deg so IMU matches encoder
            wrist_rad = math.radians(wrist_motor / 6.25)  # motor→wrist→rad
            half = wrist_rad / 2.0
            qw = math.cos(half)
            qx = 0.0
            qy = 0.0
            qz = math.sin(half)

            sample = [
                t * 1000,    # t_ms
                qw, qx, qy, qz,
                current_mA,
                wrist_motor,
                0,           # dec_count
            ]
            self.ring.append(time.perf_counter(), sample)
            time.sleep(0.01)

    def send(self, cmd: str):
        print(f"[MockSerial] send: {cmd}")
        cmd = cmd.strip()
        if cmd.startswith("SET_ANG:"):
            try:
                val = float(cmd[8:])
                with self._lock:
                    self._wrist_motor = val
            except ValueError:
                pass

    def connect(self, port: str = None, on_success=None, on_error=None):
        if on_success:
            on_success()

    def disconnect(self):
        self._stop_evt.set()


class MockLCWorker:
    """
    Simulates LCWorker at 100 Hz.

    6-channel load cell with distinct sin waves per channel:
        Fx :  2.0 * sin(0.7t)
        Fy :  1.5 * sin(1.1t + 1.0)
        Fz :  3.0 * sin(0.4t + 2.0)
        Tx :  0.3 * sin(1.5t + 0.5)
        Ty :  0.2 * sin(0.9t + 1.5)
        Tz :  0.4 * sin(1.2t + 3.0)
    """

    def __init__(self):
        self.ring      = InterpRingBuffer(maxlen=4000)
        self.connected = True
        self.status    = "LC: Mock"
        self.bias      = [0.0] * 7
        self._stop_evt = threading.Event()
        self._offset = [0.0] * 6
        self._thread   = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        t = 0.0
        while not self._stop_evt.is_set():
            t += 0.01
            sample = [
                2.0 * math.sin(0.7 * t)         - self._offset[0],
                1.5 * math.sin(1.1 * t + 1.0)   - self._offset[1],
                3.0 * math.sin(0.4 * t + 2.0)   - self._offset[2],
                0.3 * math.sin(1.5 * t + 0.5)   - self._offset[3],
                0.2 * math.sin(0.9 * t + 1.5)   - self._offset[4],
                0.4 * math.sin(1.2 * t + 3.0)   - self._offset[5],
            ]
            self.ring.append(time.perf_counter(), sample)
            time.sleep(0.01)

    def connect(self, port: str = None, on_success=None, on_error=None):
        if on_success:
            on_success()

    def retare(self):
        samples = [s for _, s in self.ring.get_since(time.perf_counter() - 0.5)]
        if not samples:
            return
        n = len(samples)
        self._offset = [sum(s[i] for s in samples) / n for i in range(6)]

    def disconnect(self):
        self._stop_evt.set()
