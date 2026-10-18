import threading
import time

import serial

from config import TRIGGER_BAUD, TRIGGER_TIMEOUT

UINT32_MASK = 0xFFFFFFFF
TRIGGER_READY_LINE = "READY,2"
TRIGGER_HANDSHAKE_TIMEOUT_S = 2.5


class TriggerWorker:
    """Serial sender for the timestamped TMSi/Vicon trigger Arduino."""

    def __init__(self):
        self.ser = None
        self.thread = None
        self.connected = False
        self.connecting = False
        self.verified = False
        self.firmware_ready = ""
        self.last_pong = ""
        self._seq = 0
        self._event_id = int(time.perf_counter_ns()) & UINT32_MASK
        if self._event_id == 0:
            self._event_id = 1
        self._lock = threading.Lock()
        self._wlock = threading.Lock()

    def _next_event_id(self) -> int:
        self._event_id = (self._event_id + 1) & UINT32_MASK
        if self._event_id == 0:
            self._event_id = 1
        return self._event_id

    def reserve_event_id(self) -> int:
        with self._lock:
            return self._next_event_id()

    @staticmethod
    def _u32_delta(later: int, earlier: int) -> int:
        return (int(later) - int(earlier)) & UINT32_MASK

    def _parse_timed_response(
        self,
        line: str,
        t_send_ns: int,
        t_write_done_ns: int,
        t_ack_ns: int,
    ):
        parts = line.split(",")
        if parts[0] == "ACK" and len(parts) == 6:
            event_id = int(parts[1])
            code = int(parts[2])
            rx_us = int(parts[3])
            stable_us = int(parts[4])
            tx_us = int(parts[5])
            rx_to_output_ns = self._u32_delta(stable_us, rx_us) * 1000
            output_to_ack_ns = self._u32_delta(tx_us, stable_us) * 1000
            rx_to_ack_ns = self._u32_delta(tx_us, rx_us) * 1000
            edge_lower_ns = int(t_send_ns) + rx_to_output_ns
            edge_upper_ns = int(t_ack_ns) - output_to_ack_ns
            transport_window_ns = max(0, edge_upper_ns - edge_lower_ns)
            return {
                "event_id": event_id,
                "code": code,
                "t_send_ns": int(t_send_ns),
                "t_write_done_ns": int(t_write_done_ns),
                "t_ack_ns": int(t_ack_ns),
                "mcu_rx_us": rx_us,
                "mcu_output_us": stable_us,
                "mcu_ack_us": tx_us,
                "rx_to_output_ns": rx_to_output_ns,
                "output_to_ack_ns": output_to_ack_ns,
                "rx_to_ack_ns": rx_to_ack_ns,
                "edge_lower_ns": edge_lower_ns,
                "edge_upper_ns": edge_upper_ns,
                "estimated_edge_ns": (edge_lower_ns + edge_upper_ns) // 2,
                "uncertainty_ns": transport_window_ns // 2,
                "host_round_trip_ns": int(t_ack_ns) - int(t_send_ns),
                "raw_response": line,
            }

        if parts[0] == "VACK" and len(parts) == 5:
            event_id = int(parts[1])
            rx_us = int(parts[2])
            edge_us = int(parts[3])
            tx_us = int(parts[4])
            rx_to_output_ns = self._u32_delta(edge_us, rx_us) * 1000
            output_to_ack_ns = self._u32_delta(tx_us, edge_us) * 1000
            rx_to_ack_ns = self._u32_delta(tx_us, rx_us) * 1000
            edge_lower_ns = int(t_send_ns) + rx_to_output_ns
            edge_upper_ns = int(t_ack_ns) - output_to_ack_ns
            transport_window_ns = max(0, edge_upper_ns - edge_lower_ns)
            return {
                "event_id": event_id,
                "t_send_ns": int(t_send_ns),
                "t_write_done_ns": int(t_write_done_ns),
                "t_ack_ns": int(t_ack_ns),
                "mcu_rx_us": rx_us,
                "mcu_output_us": edge_us,
                "mcu_ack_us": tx_us,
                "rx_to_output_ns": rx_to_output_ns,
                "output_to_ack_ns": output_to_ack_ns,
                "rx_to_ack_ns": rx_to_ack_ns,
                "edge_lower_ns": edge_lower_ns,
                "edge_upper_ns": edge_upper_ns,
                "estimated_edge_ns": (edge_lower_ns + edge_upper_ns) // 2,
                "uncertainty_ns": transport_window_ns // 2,
                "host_round_trip_ns": int(t_ack_ns) - int(t_send_ns),
                "raw_response": line,
            }

        print(f"[TriggerArduino] bad timed response: {line}", flush=True)
        return None

    def _read_timed_response(
        self,
        event_id: int,
        prefixes: tuple[str, ...],
        t_send_ns: int,
        t_write_done_ns: int,
    ):
        deadline = time.perf_counter() + max(float(TRIGGER_TIMEOUT), 0.25)
        while time.perf_counter() < deadline:
            raw = self.ser.readline()
            t_ack_ns = time.perf_counter_ns()
            if not raw:
                continue

            line = raw.decode("ascii", errors="replace").strip()
            parts = line.split(",")
            if len(parts) >= 2 and parts[0] in {"ERR", "BUSY"}:
                try:
                    response_id = int(parts[1])
                except ValueError:
                    response_id = -1
                if response_id in {0, event_id}:
                    print(f"[TriggerArduino] {line}", flush=True)
                    return None

            if not line.startswith(prefixes):
                continue
            try:
                response_id = int(parts[1])
            except (IndexError, ValueError):
                continue
            if response_id == event_id:
                return self._parse_timed_response(
                    line,
                    t_send_ns,
                    t_write_done_ns,
                    t_ack_ns,
                )

        print(f"[TriggerArduino] no timed response for event {event_id}", flush=True)
        return None

    @staticmethod
    def _read_line_until(ser, deadline: float) -> str:
        while time.perf_counter() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if line:
                return line
        return ""

    def _handshake(self, ser) -> tuple[bool, str, str]:
        deadline = time.perf_counter() + TRIGGER_HANDSHAKE_TIMEOUT_S
        ready_line = ""
        event_id = self.reserve_event_id()
        last_ping_at = 0.0
        while time.perf_counter() < deadline:
            now = time.perf_counter()
            if now - last_ping_at >= 0.25:
                try:
                    ser.write(f"PING,{event_id}\n".encode("ascii"))
                    ser.flush()
                except Exception as exc:
                    return False, ready_line, f"PING write failed: {exc}"
                last_ping_at = now
            line = self._read_line_until(ser, deadline)
            if not line:
                continue
            if line == TRIGGER_READY_LINE:
                ready_line = line
            parts = line.split(",")
            if (
                len(parts) == 4
                and parts[0] == "PONG"
                and parts[1].isdigit()
                and int(parts[1]) == event_id
            ):
                return True, ready_line or "PING/PONG", line
        return False, ready_line, "missing valid PONG"

    def _send_timed_command(self, command: str, event_id: int, prefixes: tuple[str, ...]):
        if not (self.ser and self.ser.is_open):
            print(f"[TriggerArduino] {command} skipped: not connected", flush=True)
            return None

        try:
            with self._wlock:
                t_send_ns = time.perf_counter_ns()
                self.ser.write(f"{command}\n".encode("ascii"))
                self.ser.flush()
                t_write_done_ns = time.perf_counter_ns()
                timing = self._read_timed_response(
                    event_id,
                    prefixes,
                    t_send_ns,
                    t_write_done_ns,
                )
                if timing is None:
                    t_ack_ns = time.perf_counter_ns()
                    timing = {
                        "event_id": event_id,
                        "t_send_ns": t_send_ns,
                        "t_write_done_ns": t_write_done_ns,
                        "t_ack_ns": t_ack_ns,
                        "host_round_trip_ns": t_ack_ns - t_send_ns,
                    }
                timing["command"] = command
            if timing and timing.get("raw_response"):
                print(
                    f"[TriggerArduino] sent {command} ack={timing['raw_response']}",
                    flush=True,
                )
            else:
                print(f"[TriggerArduino] sent {command} ack=missing", flush=True)
            return timing
        except Exception as exc:
            print(f"[TriggerArduino] {command} error ({exc})", flush=True)
            return None

    def _send_timed_command_async(
        self,
        command: str,
        event_id: int,
        prefixes: tuple[str, ...],
        on_complete=None,
    ):
        def _run():
            timing = self._send_timed_command(command, event_id, prefixes)
            if on_complete is not None:
                try:
                    on_complete(timing)
                except Exception as exc:
                    print(f"[TriggerArduino] async callback error ({exc})", flush=True)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return {
            "event_id": event_id,
            "queued": True,
            "command": command,
        }

    def connect(self, port: str, on_success=None, on_error=None):
        with self._lock:
            if self.connected or self.connecting:
                return
            self.connecting = True
            self._seq += 1
            seq = self._seq
        self.thread = threading.Thread(
            target=self._run_connect,
            args=(seq, port, on_success, on_error),
            daemon=True,
        )
        self.thread.start()

    def _run_connect(self, seq: int, port: str, on_success, on_error):
        ser = None
        try:
            ser = serial.Serial(
                port,
                TRIGGER_BAUD,
                timeout=TRIGGER_TIMEOUT,
                write_timeout=TRIGGER_TIMEOUT,
            )
            try:
                ser.reset_output_buffer()
            except Exception:
                pass
            ok, ready_line, pong_or_error = self._handshake(ser)
            if not ok:
                raise RuntimeError(
                    f"Trigger Arduino handshake failed: {pong_or_error}"
                )
            with self._lock:
                if seq != self._seq:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    return
                self.ser = ser
                self.connected = True
                self.verified = True
                self.firmware_ready = ready_line
                self.last_pong = pong_or_error
                self.connecting = False
            if on_success:
                on_success()
        except Exception as exc:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            with self._lock:
                if seq == self._seq:
                    self.ser = None
                    self.connected = False
                    self.verified = False
                    self.firmware_ready = ""
                    self.last_pong = ""
                    self.connecting = False
            if on_error:
                on_error(str(exc))


    def send_marker_code_with_vicon(self, code: int, event_id: int | None = None):
        code = int(code)
        if code == 0:
            print("[TriggerArduino] TRIGVICON skipped: code 0", flush=True)
            return None
        if event_id is None:
            event_id = self.reserve_event_id()
        return self._send_timed_command(f"TRIGVICON,{event_id},{code}", event_id, ("ACK,",))

    def send_marker_code_with_vicon_async(self, code: int, event_id: int | None = None, on_complete=None):
        code = int(code)
        if code == 0:
            print("[TriggerArduino] TRIGVICON skipped: code 0", flush=True)
            return None
        if event_id is None:
            event_id = self.reserve_event_id()
        return self._send_timed_command_async(
            f"TRIGVICON,{event_id},{code}",
            event_id,
            ("ACK,",),
            on_complete=on_complete,
        )




    def disconnect(self):
        with self._lock:
            self._seq += 1
            self.connected = False
            self.verified = False
            self.firmware_ready = ""
            self.last_pong = ""
            self.connecting = False
            ser = self.ser
            self.ser = None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
