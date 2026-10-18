"""COM3 position/passive/active control + COM4 LC-to-UDP GUI.

Place this file and udp_sender.py beside EMG_control2/run.py.
UDP encoding, layout, validation and rate-limited sending live in udp_sender.py.
Reuses the LC worker/config:
115200 baud, ATI 20-byte binary records, checksum, bias removal and scaling.
Connect opens the port only. Start configures/tares the LC, then reads/sends.
Torque is in Nmm; force is in N, matching the existing rig calibration.
COM3 uses the original serial baud/settling time and newline commands:
Encoder-guided position moves use velocity commands capped at 25 wrist deg/s.
TORQUE_OFF releases the motor; Active holds the current encoder position.
Position input is wrist degrees (-90..90), with the existing command sign
and gear ratio. Zero Wrist sets the current encoder angle as the origin.
The software zero is kept for this GUI session; restarting resets it.

Usage: python run_UDP_GUI.py
       python run_UDP_GUI.py --target-ip 10.0.0.6 --target-port 10020
Dependency: pyserial (plus Python's built-in tkinter).
"""

import argparse
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serial

from config import (
    GEAR_RATIO, LC_BAUD, LC_COM_PORT, LC_INTER_BYTE_TIMEOUT, LC_SER_TIMEOUT,
    SERIAL_BAUD, SERIAL_COM_PORT, SERIAL_TIMEOUT, WRIST_COMMAND_SIGN,
    WRIST_ENCODER_SIGN, WRIST_VELOCITY_COMMAND_SIGN,
)
from hardware.lc_worker import LCWorker
from udp_sender import (
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SAMPLE_RATE,
    OUTPUT_LAYOUT, UDPSender, output_row, validate_target,
)


def position_motor_angle(wrist_deg):
    wrist_deg = float(wrist_deg)
    if not math.isfinite(wrist_deg) or not -90 <= wrist_deg <= 90:
        raise ValueError("Wrist angle must be between -90 and 90 degrees")
    # Matches _manual_target_to_motor_deg(), with default wrist_zero=0.
    return wrist_deg * WRIST_COMMAND_SIGN * GEAR_RATIO


MAX_WRIST_SPEED_DPS = 25.0
ENCODER_TIMEOUT_S = 0.35
POSITION_TOLERANCE_DEG = 0.2
DXL_POSITION_DEG_PER_RAW = 0.088
DXL_VELOCITY_RPM_PER_RAW = 0.229


def decode_encoder_line(line):
    """Return wrist encoder degrees from slow CSV or 4/7/9-field M packets."""
    parts = [part.strip() for part in line.split(",")]
    try:
        if parts[0].upper() == "M":
            if len(parts) == 4:
                motor_deg = float(parts[2]) * DXL_POSITION_DEG_PER_RAW
            elif len(parts) in (7, 9):
                motor_deg = float(parts[3]) * DXL_POSITION_DEG_PER_RAW
            else:
                return None
        elif len(parts) >= 8:
            motor_deg = float(parts[6])
        else:
            return None
        angle = motor_deg * WRIST_ENCODER_SIGN / GEAR_RATIO
    except (ValueError, ZeroDivisionError):
        return None
    return angle if math.isfinite(angle) else None


def limited_motor_rpm(direction):
    """Round down to motor velocity steps so wrist speed cannot exceed 25 deg/s.

    The firmware rounds RPM / 0.229 to a motor register value. Sending an
    exact register multiple avoids upward rounding of the 25 deg/s limit.
    Units: https://emanual.robotis.com/docs/en/dxl/x/xc330-m288/#goal-velocity104
    """
    if direction not in (-1, 1):
        raise ValueError("Direction must be -1 or +1")
    if not math.isfinite(GEAR_RATIO) or GEAR_RATIO <= 0:
        raise ValueError("Gear ratio must be finite and positive")
    raw = math.floor(MAX_WRIST_SPEED_DPS * GEAR_RATIO / (6.0 * DXL_VELOCITY_RPM_PER_RAW))
    if raw < 1:
        raise ValueError("Gear ratio is too small for a motor speed below 25 deg/s")
    return direction * WRIST_VELOCITY_COMMAND_SIGN * raw * DXL_VELOCITY_RPM_PER_RAW


class MotorSerialBridge:
    """COM3 reads encoder feedback and controls bounded-speed position moves."""

    def __init__(self):
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.ser = None
        self.connected = False
        self.mode = "Unknown"
        self._rx = bytearray()
        self._feedback_lock = threading.Lock()
        self._feedback = (None, None)
        self._wrist_zero = 0.0  # Offset in signed wrist degrees, like the original GUI.
        self._target = None
        self._velocity_mode = False
        self._mode_reply = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def request(self, command, *args):
        self.commands.put((command, args))

    def snapshot(self):
        """Return the encoder angle relative to the software wrist zero."""
        with self._feedback_lock:
            angle, timestamp = self._feedback
            return (None if angle is None else angle - self._wrist_zero, timestamp)

    def _reset_feedback(self):
        with self._feedback_lock:
            self._feedback = (None, None)

    def _current_angle(self):
        angle, timestamp = self.snapshot()
        if angle is None or time.perf_counter() - timestamp > ENCODER_TIMEOUT_S:
            raise RuntimeError("No recent encoder feedback. Position control is stopped.")
        return angle

    def _state(self, message):
        self.events.put((self.connected, self.mode, message))

    def _connect(self, port):
        if self.ser is not None:
            raise RuntimeError("Motor serial is already connected")
        self.ser = serial.Serial(port, SERIAL_BAUD, timeout=SERIAL_TIMEOUT, write_timeout=1.0)
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self._rx.clear()
        self._reset_feedback()
        self._target = None
        self._velocity_mode = False
        self.connected = True
        self.mode = "Unknown"
        self._send("STREAM_ON")
        self._state(f"Motor connected: {port} / {SERIAL_BAUD} baud. Waiting for encoder.")

    def _send(self, command):
        if not self.connected or self.ser is None or not self.ser.is_open:
            raise RuntimeError("Connect COM3 motor serial first")
        payload = (command.strip() + "\n").encode("utf-8")
        if self.ser.write(payload) != len(payload):
            raise OSError("Incomplete motor command write")

    def _send_mode(self, command):
        # Consume old replies before matching the next mode confirmation.
        self._read_replies()
        self._mode_reply = None
        self._send(command)
        deadline = time.perf_counter() + 1.0
        while self._mode_reply != command + "_OK":
            if time.perf_counter() >= deadline:
                raise RuntimeError(f"No confirmation for {command}; motion cancelled.")
            self._read_replies()
        self._velocity_mode = command == "MODE_VEL"

    def _hold_current(self):
        self._current_angle()
        self._target = None
        if self._velocity_mode:
            self._send("ZERO_VEL")
        # MODE_EXT sets the hold goal to the current motor position in firmware.
        self._send_mode("MODE_EXT")
        self.mode = "Active"

    def _active(self):
        self._hold_current()
        self._send("TORQUE_ON")
        self._state("Active: holding the current encoder position.")

    def _passive(self):
        self._target = None
        self._send("TORQUE_OFF")
        self.mode = "Passive"
        self._state("Passive command sent.")

    def _zero_wrist(self):
        if not self.connected:
            raise RuntimeError("Connect COM3 motor serial first")
        self._current_angle()  # Reject missing or stale encoder feedback.
        if self._target is not None:
            self._hold_current()  # Stop the old move before changing coordinates.
            stopped_at = time.perf_counter()
            # Discard queued samples from before the stop and use a new frame.
            self.ser.reset_input_buffer()
            self._rx.clear()
            deadline = stopped_at + ENCODER_TIMEOUT_S
            while True:
                angle, timestamp = self.snapshot()
                if angle is not None and timestamp > stopped_at:
                    break
                if time.perf_counter() >= deadline:
                    raise RuntimeError("No encoder feedback after stopping; wrist zero was not set.")
                self._read_replies()
        self._current_angle()
        with self._feedback_lock:
            self._wrist_zero = self._feedback[0]
        self._state("Wrist zero set: current angle = 0.00 deg.")

    def _position(self, wrist_deg):
        position_motor_angle(wrist_deg)  # Validate the same +/-90 degree input.
        self._current_angle()
        self._target = None
        if self._velocity_mode:
            self._send("ZERO_VEL")
        self._send_mode("MODE_VEL")
        angle = self._current_angle()
        gap = float(wrist_deg) - angle
        if abs(gap) <= POSITION_TOLERANCE_DEG:
            self._hold_current()
            self._state(f"Already at target: encoder {angle:+.2f} deg.")
            return
        direction = 1 if gap > 0 else -1
        rpm = limited_motor_rpm(direction)
        self._send(f"SET_VEL_RPM:{rpm:.3f}")
        speed = abs(rpm) * 6.0 / GEAR_RATIO
        deadline = time.perf_counter() + abs(gap) / speed + 2.0
        self._target = (float(wrist_deg), direction, deadline)
        self.mode = "Active"
        self._state(f"Moving to {float(wrist_deg):+.2f} deg; wrist speed {speed:.2f} deg/s (max 25).")

    def _update_move(self):
        if self._target is None:
            return
        target, direction, deadline = self._target
        angle = self._current_angle()
        gap = target - angle
        if abs(gap) <= POSITION_TOLERANCE_DEG or gap * direction <= 0:
            self._hold_current()
            self._state(f"Position reached: encoder {angle:+.2f} deg / target {target:+.2f} deg.")
        elif time.perf_counter() > deadline:
            raise RuntimeError("Position move timed out; motor released.")

    def _read_replies(self):
        if not self.ser.is_open:
            raise OSError("Motor serial port was closed")
        data = self.ser.read(min(self.ser.in_waiting, 4096) or 1)
        if not data:
            return
        self._rx.extend(data)
        while b"\n" in self._rx:
            line, _, remaining = self._rx.partition(b"\n")
            self._rx = bytearray(remaining)
            text = line.decode("utf-8", errors="ignore").strip()
            angle = decode_encoder_line(text)
            if angle is not None:
                with self._feedback_lock:
                    self._feedback = (angle, time.perf_counter())
            elif text.startswith("MODE_") and text.endswith("_OK"):
                self._mode_reply = text
            elif "_FAIL" in text:
                raise RuntimeError(f"Motor reply: {text}")
            elif text.upper().startswith("ERROR"):
                self._state(f"Motor reply: {text}")
        if len(self._rx) > 8192:
            self._rx = self._rx[-4096:]

    def _disconnect(self):
        self._target = None
        self._reset_feedback()
        if self.ser is not None:
            try:
                if self.connected and self.ser.is_open:
                    self._send("TORQUE_OFF")
                    self.ser.flush()
            finally:
                try:
                    self.ser.close()
                finally:
                    self.ser = None
                    self.connected = False
                    self.mode = "Unknown"
                    self._velocity_mode = False

    def _run(self):
        try:
            while True:
                try:
                    command, args = self.commands.get(timeout=0 if self.connected else 0.05)
                except queue.Empty:
                    command, args = None, ()
                if command == "close":
                    break
                try:
                    if command == "connect":
                        self._connect(*args)
                    elif command == "disconnect":
                        self._disconnect()
                        self._state("Motor disconnected; Passive command sent.")
                    elif command == "position":
                        self._position(*args)
                    elif command == "passive":
                        self._passive()
                    elif command == "active":
                        self._active()
                    elif command == "zero_wrist":
                        self._zero_wrist()
                    if self.connected:
                        self._read_replies()
                        self._update_move()
                except Exception as exc:
                    try:
                        self._disconnect()
                    except Exception:
                        pass
                    self._state(f"Motor error: {exc}")
        finally:
            try:
                self._disconnect()
            except Exception:
                pass

class LCUDPBridge:
    """One thread owns the serial port and UDP socket; Tk uses queues only."""

    def __init__(self):
        self.lc = LCWorker()
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.running = False
        self.udp = None
        self._lock = threading.Lock()
        self._snapshot = (None, 0, 0, 0.0)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def request(self, command, *args):
        self.commands.put((command, args))

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def _state(self, message):
        self.events.put((self.lc.connected, self.running, message))

    def _connect(self, port):
        if self.lc.ser is not None:
            raise RuntimeError("LC is already connected")
        self.lc.stop_evt.clear()
        self.lc.ser = serial.Serial(
            port, LC_BAUD, timeout=LC_SER_TIMEOUT,
            inter_byte_timeout=LC_INTER_BYTE_TIMEOUT,
        )
        time.sleep(1.0)  # Same settling time as the existing LC connection.
        self.lc.connected = True
        self.lc._binary_rx.clear()
        with self._lock:
            self._snapshot = (None, 0, 0, 0.0)
        self._state(f"LC connected: {port} / {LC_BAUD} baud. Ready to acquire.")

    def _start(self, host, port, rate):
        if not self.lc.connected:
            raise RuntimeError("Connect LC first")
        if self.running:
            raise RuntimeError("Acquisition is already running")
        host, port, rate = validate_target(host, port, rate)
        self.lc._binary_rx.clear()
        self.lc.bias = [0.0] * 7
        with self.lc._offset_lock:
            self.lc._offset = [0.0] * 6
        self.lc._configure_binary_resolved_stream()
        samples = []
        deadline = time.perf_counter() + 2.0
        while len(samples) < 20 and time.perf_counter() < deadline:
            raw = self.lc._read_binary_resolved_record()
            if raw is not None and raw[0] == 0:
                samples.append(raw)
        if not samples:
            raise RuntimeError("No valid LC records received during tare. Check COM port/cable.")
        self.lc.bias = [sum(row[i] for row in samples) / len(samples) for i in range(7)]
        self.udp = UDPSender(host, port, rate)
        self.started_at = self.last_valid = time.perf_counter()
        self.received = self.sent = 0
        with self._lock:
            self._snapshot = (None, 0, 0, 0.0)
        self.running = True
        self._state(f"Acquiring + sending to {host}:{port}, target {rate:g} Hz.")

    def _read_and_send(self):
        if self.lc.ser is None or not self.lc.ser.is_open:
            raise RuntimeError("LC port was closed")
        sample = self.lc._read_scaled_binary_resolved()
        now = time.perf_counter()
        if sample is None:
            if now - self.last_valid > 2.0:
                raise RuntimeError("No valid LC data for 2 seconds. Reconnect and restart.")
            time.sleep(0.001)
            return
        row = output_row(sample)
        self.last_valid = now
        self.received += 1
        if self.udp.send_row(row, now=now):
            self.sent = self.udp.sent
        with self._lock:
            self._snapshot = (row, self.received, self.sent, now - self.started_at)

    def _stop(self):
        self.running = False
        if self.udp is not None:
            self.udp.close()
            self.udp = None
        # Keep COM connected. The next Start flushes/reconfigures/tares the LC.
        self._state("Acquisition and UDP stopped. LC remains connected.")

    def _disconnect(self):
        self.running = False
        if self.udp is not None:
            self.udp.close()
            self.udp = None
        self.lc.disconnect()

    def _run(self):
        try:
            while True:
                try:
                    command, args = self.commands.get(timeout=0 if self.running else 0.05)
                except queue.Empty:
                    command, args = None, ()
                if command == "close":
                    break
                try:
                    if command == "connect":
                        self._connect(*args)
                    elif command == "start":
                        self._start(*args)
                    elif command == "stop":
                        self._stop()
                    elif command == "disconnect":
                        self._disconnect()
                        self._state("LC disconnected.")
                    if self.running:
                        self._read_and_send()
                except Exception as exc:
                    self._disconnect()
                    self._state(f"Error: {exc}")
        finally:
            self._disconnect()


class LCUDPApp(tk.Tk):
    def __init__(self, args):
        super().__init__()
        self.title("LC -> UDP + Motor | COM4 / COM3")
        self.resizable(False, False)
        self.bridge = LCUDPBridge()
        self.motor = MotorSerialBridge()
        self.connected = self.running = self.busy = self.closing = False
        self.motor_connected = self.motor_busy = False
        self.motor_port_var = tk.StringVar(value=getattr(args, "serial_port", SERIAL_COM_PORT))
        self.position_var = tk.StringVar(value="0")
        self.encoder_var = tk.StringVar(value="--")
        self.motor_mode_var = tk.StringVar(value="Requested mode: Unknown")
        self.motor_status_var = tk.StringVar(value="Motor serial disconnected.")
        self.port_var = tk.StringVar(value=args.com_port)
        self.host_var = tk.StringVar(value=args.target_ip)
        self.udp_port_var = tk.StringVar(value=str(args.target_port))
        self.rate_var = tk.StringVar(value=f"{args.sample_rate:g}")
        self.status_var = tk.StringVar(value="LC disconnected. Connect, then start acquisition.")
        self.stats_var = tk.StringVar(value="LC samples: 0    UDP packets sent: 0    Send rate: 0.0 Hz")
        self.values = [tk.StringVar(value="--") for _ in OUTPUT_LAYOUT]
        self._build_ui()
        self._refresh_controls()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.grid()
        motor = ttk.LabelFrame(root, text="COM3 motor: position / passive / active", padding=10)
        motor.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(motor, text="COM port:").grid(row=0, column=0, sticky="w")
        self.motor_port_entry = ttk.Entry(motor, textvariable=self.motor_port_var, width=9)
        self.motor_port_entry.grid(row=0, column=1, padx=8, sticky="w")
        self.motor_connect_btn = ttk.Button(motor, text="Connect Serial", command=self._motor_connect)
        self.motor_connect_btn.grid(row=0, column=2, padx=4)
        self.motor_disconnect_btn = ttk.Button(motor, text="Disconnect", command=self._motor_disconnect)
        self.motor_disconnect_btn.grid(row=0, column=3, padx=4)
        ttk.Label(motor, text="Wrist angle (deg):").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.position_entry = ttk.Entry(motor, textvariable=self.position_var, width=9)
        self.position_entry.grid(row=1, column=1, padx=8, sticky="w", pady=(10, 0))
        self.position_btn = ttk.Button(motor, text="Set Position", command=self._motor_position)
        self.position_btn.grid(row=1, column=2, padx=4, pady=(10, 0))
        ttk.Label(motor, text="-90 .. +90 deg").grid(row=1, column=3, sticky="w", pady=(10, 0))
        self.passive_btn = ttk.Button(motor, text="Passive", command=lambda: self._motor_mode("passive"))
        self.passive_btn.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.active_btn = ttk.Button(motor, text="Active", command=lambda: self._motor_mode("active"))
        self.active_btn.grid(row=2, column=1, sticky="w", pady=(10, 0))
        ttk.Label(motor, textvariable=self.motor_mode_var).grid(row=2, column=2, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(motor, text="Encoder (wrist):").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(motor, textvariable=self.encoder_var, font=("Consolas", 12)).grid(
            row=3, column=1, sticky="w", pady=(10, 0))
        self.zero_wrist_btn = ttk.Button(motor, text="Zero Wrist", command=self._motor_zero_wrist)
        self.zero_wrist_btn.grid(row=3, column=2, padx=4, pady=(10, 0))
        ttk.Label(motor, text="Max move speed: 25 deg/s").grid(
            row=3, column=3, sticky="w", pady=(10, 0))
        ttk.Label(motor, textvariable=self.motor_status_var, wraplength=600).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        lc = ttk.LabelFrame(root, text="1. LC connection", padding=10)
        lc.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(lc, text="COM port:").grid(row=0, column=0)
        self.com_entry = ttk.Entry(lc, textvariable=self.port_var, width=9)
        self.com_entry.grid(row=0, column=1, padx=8)
        self.connect_btn = ttk.Button(lc, text="Connect LC", command=self._connect)
        self.connect_btn.grid(row=0, column=2, padx=4)
        self.disconnect_btn = ttk.Button(lc, text="Disconnect", command=self._disconnect)
        self.disconnect_btn.grid(row=0, column=3, padx=4)

        udp = ttk.LabelFrame(root, text="2. Acquire LC + send UDP", padding=10)
        udp.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.target_entries = []
        for col, (label, variable, width) in enumerate((
            ("Receiver IPv4", self.host_var, 17),
            ("UDP port", self.udp_port_var, 8),
            ("Send Hz", self.rate_var, 8),
        )):
            ttk.Label(udp, text=label).grid(row=0, column=col, sticky="w")
            entry = ttk.Entry(udp, textvariable=variable, width=width)
            entry.grid(row=1, column=col, sticky="w", padx=(0, 12), pady=(4, 8))
            self.target_entries.append(entry)
        self.start_btn = ttk.Button(udp, text="Start acquisition + UDP", command=self._start)
        self.start_btn.grid(row=2, column=0, columnspan=2, sticky="w")
        self.stop_btn = ttk.Button(udp, text="Stop", command=self._stop)
        self.stop_btn.grid(row=2, column=2, sticky="w")
        ttk.Label(udp, text="Start tares the LC: keep it unloaded during initialization.").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        live = ttk.LabelFrame(root, text="UDP order: [tx, ty, tz, fx, fy, fz]", padding=10)
        live.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        for col, name in enumerate(OUTPUT_LAYOUT):
            unit = "Nmm" if col < 3 else "N"
            ttk.Label(live, text=f"{name} ({unit})", width=11, anchor="center").grid(row=0, column=col)
            ttk.Label(live, textvariable=self.values[col], font=("Consolas", 11),
                      width=11, anchor="center").grid(row=1, column=col, pady=8)
        ttk.Label(root, textvariable=self.stats_var).grid(row=4, column=0, sticky="w")
        ttk.Label(root, textvariable=self.status_var, wraplength=600).grid(
            row=5, column=0, sticky="w", pady=(8, 0))

    def _refresh_controls(self):
        idle = not self.busy and not self.closing
        for widget, enabled in (
            (self.connect_btn, idle and not self.connected),
            (self.disconnect_btn, idle and self.connected),
            (self.start_btn, idle and self.connected and not self.running),
            (self.stop_btn, idle and self.running),
            (self.com_entry, idle and not self.connected),
        ):
            widget.configure(state="normal" if enabled else "disabled")
        for entry in self.target_entries:
            entry.configure(state="normal" if idle and not self.running else "disabled")
        motor_idle = not self.motor_busy and not self.closing
        angle, timestamp = self.motor.snapshot()
        feedback_fresh = angle is not None and time.perf_counter() - timestamp <= ENCODER_TIMEOUT_S
        for widget, enabled in (
            (self.motor_connect_btn, motor_idle and not self.motor_connected),
            (self.motor_port_entry, motor_idle and not self.motor_connected),
            (self.motor_disconnect_btn, motor_idle and self.motor_connected),
            (self.position_entry, motor_idle and self.motor_connected),
            (self.position_btn, motor_idle and self.motor_connected and feedback_fresh),
            (self.zero_wrist_btn, motor_idle and self.motor_connected and feedback_fresh),
            (self.passive_btn, motor_idle and self.motor_connected),
            (self.active_btn, motor_idle and self.motor_connected and feedback_fresh),
        ):
            widget.configure(state="normal" if enabled else "disabled")

    def _motor_submit(self, message, command, *args):
        if self.motor_busy or self.closing:
            return
        self.motor_busy = True
        self.motor_status_var.set(message)
        self._refresh_controls()
        self.motor.request(command, *args)

    def _motor_connect(self):
        port = self.motor_port_var.get().strip()
        if not port or port.upper() == self.port_var.get().strip().upper():
            messagebox.showerror("Motor serial", "Enter a motor COM port different from the LC port.", parent=self)
            return
        self._motor_submit(f"Opening {port}...", "connect", port)

    def _motor_disconnect(self):
        self._motor_submit("Releasing and disconnecting motor...", "disconnect")

    def _motor_position(self):
        try:
            wrist = float(self.position_var.get())
            position_motor_angle(wrist)
        except ValueError as exc:
            messagebox.showerror("Position", str(exc), parent=self)
            return
        self._motor_submit("Sending position...", "position", wrist)

    def _motor_zero_wrist(self):
        self._motor_submit("Setting current encoder angle to zero...", "zero_wrist")

    def _motor_mode(self, mode):
        self._motor_submit(f"Sending {mode.capitalize()}...", mode)

    def _submit(self, message, command, *args):
        if self.busy or self.closing:
            return
        self.busy = True
        self.status_var.set(message)
        self._refresh_controls()
        self.bridge.request(command, *args)

    def _connect(self):
        port = self.port_var.get().strip()
        if not port or port.upper() == self.motor_port_var.get().strip().upper():
            messagebox.showerror("LC", "Enter an LC COM port different from the motor port.", parent=self)
            return
        self._submit(f"Opening {port}...", "connect", port)

    def _disconnect(self):
        self._submit("Disconnecting LC...", "disconnect")

    def _start(self):
        try:
            target = validate_target(self.host_var.get(), self.udp_port_var.get(), self.rate_var.get())
        except ValueError as exc:
            messagebox.showerror("UDP settings", str(exc), parent=self)
            return
        self._submit("Configuring and taring LC...", "start", *target)

    def _stop(self):
        self._submit("Stopping acquisition and UDP...", "stop")

    def _poll(self):
        while True:
            try:
                self.motor_connected, mode, message = self.motor.events.get_nowait()
            except queue.Empty:
                break
            self.motor_busy = False
            self.motor_mode_var.set(f"Requested mode: {mode}")
            self.motor_status_var.set(message)
            if message.startswith("Wrist zero set:"):
                self.position_var.set("0")
        angle, timestamp = self.motor.snapshot()
        if not self.motor_connected:
            self.encoder_var.set("--")
        elif angle is None:
            self.encoder_var.set("Waiting...")
        elif time.perf_counter() - timestamp > ENCODER_TIMEOUT_S:
            self.encoder_var.set("-- (stale)")
        else:
            self.encoder_var.set(f"{angle:+.2f} deg")
        while True:
            try:
                self.connected, self.running, message = self.bridge.events.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            self.status_var.set(message)
        row, received, sent, elapsed = self.bridge.snapshot()
        for variable, value in zip(self.values, row if row is not None else [None] * 6):
            variable.set("--" if value is None else f"{value:+.4f}")
        rate = sent / elapsed if elapsed > 0 else 0.0
        self.stats_var.set(f"LC samples: {received}    UDP packets sent: {sent}    Send rate: {rate:.1f} Hz")
        self._refresh_controls()
        if self.closing and not self.bridge.thread.is_alive() and not self.motor.thread.is_alive():
            self.destroy()
            return
        self.after(100, self._poll)

    def _close(self):
        if self.closing:
            return
        self.closing = True
        self.status_var.set("Closing LC, UDP and motor serial...")
        self._refresh_controls()
        self.bridge.request("close")
        self.motor.request("close")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--com-port", default=LC_COM_PORT)
    parser.add_argument("--serial-port", default=SERIAL_COM_PORT)
    parser.add_argument("--target-ip", default=DEFAULT_HOST)
    parser.add_argument("--target-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE)
    args = parser.parse_args()
    try:
        validate_target(args.target_ip, args.target_port, args.sample_rate)
    except ValueError as exc:
        parser.error(str(exc))
    return args


if __name__ == "__main__":
    LCUDPApp(parse_args()).mainloop()
