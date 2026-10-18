import threading
import time

import serial

from hardware.ring_buffer import InterpRingBuffer
from config import SERIAL_BAUD, SERIAL_TIMEOUT

DXL_KT_NM_PER_A = 1.150
DXL_CURRENT_LIMIT_MA = 500.0
DXL_POSITION_DEG_PER_RAW = 0.088
DXL_VELOCITY_RPM_PER_RAW = 0.229


class SerialWorker:
    """
    Manages a background thread that reads the exo UART stream and
    parses incoming CSV lines into the ring buffer.

    Usage:
        worker = SerialWorker()
        worker.connect("COM3", on_success=..., on_error=...)
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
        self.raw_recorder = None
        self.dxl_control_mode = "extended_position"
        self.dxl_goal_current_mA = 0.0
        self.dxl_goal_velocity_dps = 0.0
        self._last_full_sample = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

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

    def set_raw_recorder(self, raw_recorder):
        self.raw_recorder = raw_recorder

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
        rx_buffer = bytearray()
        while not self.stop_evt.is_set():
            try:
                data = self.ser.read(self.ser.in_waiting or 1)
            except Exception:
                data = b""
            if not data:
                continue
            rx_buffer.extend(data)
            if len(rx_buffer) > 8192:
                rx_buffer = rx_buffer[-4096:]
            while b"\n" in rx_buffer:
                line, _sep, remaining = rx_buffer.partition(b"\n")
                rx_buffer = bytearray(remaining)
                raw = line.decode("utf-8", errors="ignore").strip()
                host_rx_ns = time.perf_counter_ns()
                if not raw:
                    continue
                parsed = self._parse(raw)
                if parsed is not None:
                    self.ring.append(host_rx_ns / 1_000_000_000.0, parsed)
                    self._record_openrb_raw(raw, parsed, host_rx_ns)

    def _parse(self, line: str):
        """Parse slow 8-field samples or fast motor-only M packets."""
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[0].upper() == "M":
            return self._parse_motor_fast(parts)
        if len(parts) < 8:
            return None
        try:
            sample = [float(p) for p in parts[:8]]
        except Exception:
            return None
        if len(self._last_full_sample) > 8:
            sample.extend(self._last_full_sample[8:])
        self._last_full_sample = list(sample)
        return sample

    def _parse_motor_fast(self, parts):
        try:
            if len(parts) in (7, 9):
                sequence = float(parts[1])
                t_us = float(parts[2])
                position_raw = float(parts[3])
                velocity_raw = float(parts[4])
                event_id = float(parts[5])
                event_flags = float(parts[6])
                command_rx_us = float(parts[7]) if len(parts) == 9 and parts[7] != "" else 0.0
                dxl_write_done_us = float(parts[8]) if len(parts) == 9 and parts[8] != "" else 0.0
            elif len(parts) == 4:
                sequence = 0.0
                t_us = float(parts[1])
                position_raw = float(parts[2])
                velocity_raw = float(parts[3])
                event_id = 0.0
                event_flags = 0.0
                command_rx_us = 0.0
                dxl_write_done_us = 0.0
            else:
                return None
        except Exception:
            return None

        sample = list(self._last_full_sample)
        if len(sample) < 8:
            sample = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        while len(sample) < 14:
            sample.append(0.0)
        sample[0] = t_us / 1000.0
        sample[6] = position_raw * DXL_POSITION_DEG_PER_RAW
        sample[7] = position_raw
        sample[8] = velocity_raw * DXL_VELOCITY_RPM_PER_RAW
        sample[9] = sequence
        sample[10] = event_id
        sample[11] = event_flags
        sample[12] = command_rx_us
        sample[13] = dxl_write_done_us
        self._last_full_sample = sample
        return sample

    def _record_openrb_raw(self, raw_line: str, sample: list, host_rx_ns: int):
        recorder = self.raw_recorder
        if recorder is None:
            return
        try:
            recorder.record_openrb(raw_line, sample, host_rx_ns)
        except Exception:
            pass


    def send(self, cmd: str):
        if not (self.ser and self.ser.is_open):
            return
        clean = cmd.strip()
        with self._wlock:
            try:
                self.ser.write((clean + "\n").encode("utf-8"))
                self._track_dxl_command(clean)
            except Exception:
                pass

    def send_velocity_dps_event(self, event_id: int, wrist_velocity_deg_s: float) -> bool:
        if not (self.ser and self.ser.is_open):
            return False
        clean = f"SET_VEL_DPS_EVT:{int(event_id)},{float(wrist_velocity_deg_s):.3f}"
        with self._wlock:
            try:
                self.ser.write((clean + "\n").encode("utf-8"))
                self.ser.flush()
                self._track_dxl_command(f"SET_VEL_DPS:{float(wrist_velocity_deg_s):.3f}")
                return True
            except Exception:
                return False

    def _track_dxl_command(self, cmd: str):
        if cmd == "MODE_CUR":
            self.dxl_control_mode = "current"
            self.dxl_goal_current_mA = 0.0
            self.dxl_goal_velocity_dps = 0.0
            return
        if cmd == "MODE_EXT":
            self.dxl_control_mode = "extended_position"
            self.dxl_goal_current_mA = 0.0
            self.dxl_goal_velocity_dps = 0.0
            return
        if cmd == "MODE_VEL":
            self.dxl_control_mode = "velocity"
            self.dxl_goal_current_mA = 0.0
            self.dxl_goal_velocity_dps = 0.0
            return
        if cmd.startswith("SET_ANG:"):
            self.dxl_control_mode = "extended_position"
            self.dxl_goal_current_mA = 0.0
            self.dxl_goal_velocity_dps = 0.0
            return
        if cmd in ("TORQUE_ON", "TORQUE_OFF", "ZERO_CUR", "ZERO_VEL"):
            if self.dxl_control_mode == "current":
                self.dxl_goal_current_mA = 0.0
            if self.dxl_control_mode == "velocity":
                self.dxl_goal_velocity_dps = 0.0
            return
        if cmd.startswith("SET_VEL_DPS:"):
            try:
                val = float(cmd[12:])
            except ValueError:
                return
            self.dxl_control_mode = "velocity"
            self.dxl_goal_current_mA = 0.0
            self.dxl_goal_velocity_dps = val
            return
        if cmd.startswith("SET_VEL_RPM:"):
            try:
                rpm = float(cmd[12:])
            except ValueError:
                return
            self.dxl_control_mode = "velocity"
            self.dxl_goal_current_mA = 0.0
            self.dxl_goal_velocity_dps = rpm * 6.0
            return
        if cmd.startswith("SET_CUR:"):
            try:
                val = float(cmd[8:])
            except ValueError:
                return
            self.dxl_control_mode = "current"
            self.dxl_goal_current_mA = max(-DXL_CURRENT_LIMIT_MA, min(DXL_CURRENT_LIMIT_MA, val))
            return
        if cmd.startswith("SET_TAU:"):
            try:
                torque_nm = float(cmd[8:])
            except ValueError:
                return
            self.dxl_control_mode = "current"
            current_mA = torque_nm / DXL_KT_NM_PER_A * 1000.0
            self.dxl_goal_current_mA = max(-DXL_CURRENT_LIMIT_MA, min(DXL_CURRENT_LIMIT_MA, current_mA))

    def disconnect(self):
        self.connected = False
        self.stop_evt.set()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
