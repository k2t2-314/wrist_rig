"""
gui/app.py — Unified Experiment GUI
====================================
Entry point:
    python run.py           # real hardware
    python run.py --mock    # synthetic data, no hardware needed
"""

from cmath import phase
import ctypes
import sys
from config import WRIST_LIMIT_DEG

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import collections
import math as _math
import os
import shutil
import time
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

from pylsl import local_clock

from config import (
    GEAR_RATIO, DATA_ROOT,
    SERIAL_COM_PORT, LC_COM_PORT,
    UDP_HOST, UDP_SEND_PORT, UDP_LISTEN_PORT,
)
from motions import (
    RestMode, CalibrationMode, ROMMode, AANMode,
    PassiveMovementMode, ActiveMovementMode,
    RAHMode, MotionRunner,
)
from recording.recorder import BaseCsvRecorder
from gui.udp import UDPManager

from gui.display import DisplayWindow


class ExpGUI(tk.Tk):

    def __init__(self, use_mock: bool = False):
        super().__init__()
        self.title("Wrist Rig — Experiment")
        self.resizable(True, True)

        # Hardware
        if use_mock:
            from hardware.mock_workers import MockSerialWorker, MockLCWorker
            self.serial_worker = MockSerialWorker()
            self.lc_worker     = MockLCWorker()
        else:
            from hardware.serial_worker import SerialWorker
            from hardware.lc_worker     import LCWorker
            self.serial_worker = SerialWorker()
            self.lc_worker     = LCWorker()

        self.wrist_zero = 0.0
        self.imu_zero   = 0.0

        self.motion_runner = MotionRunner(
            self.serial_worker,
            get_wrist_zero=lambda: self.wrist_zero,
            get_imu_zero=lambda: self.imu_zero,
        )

        self.recorder = BaseCsvRecorder(
            self.serial_worker, self.lc_worker,
            get_wrist_zero=lambda: self.wrist_zero,
            get_imu_zero=lambda: self.imu_zero,
        )

        self.udp = UDPManager(UDP_HOST, UDP_SEND_PORT, UDP_LISTEN_PORT)

        # Shared tk vars
        self.handedness_var  = tk.StringVar(value="Right")
        self.direction_var   = tk.StringVar(value="1")
        self.port_var        = tk.StringVar(value=SERIAL_COM_PORT)
        self.lc_port_var     = tk.StringVar(value=LC_COM_PORT)
        self.trial_var       = tk.StringVar(value="1")
        self.serial_status   = tk.StringVar(value="Serial: Disconnected")
        self.lc_status_var   = tk.StringVar(value="LC: Disconnected")
        self.angle_var       = tk.StringVar(value="--- deg")
        self.imu_var         = tk.StringVar(value="--- deg")
        self.current_var     = tk.StringVar(value="--- mA")
        self.motion_status   = tk.StringVar(value="Idle")
        self.rec_status_var  = tk.StringVar(value="Recording: OFF")
        self.fx_max_var      = tk.StringVar(value="10.0")
        self.fx_live_var     = tk.StringVar(value="--- ")
        self.lc_channel_var  = tk.StringVar(value="Fnorm")
        self._fx_history     = collections.deque(maxlen=300)

        self.udp_host_var   = tk.StringVar(value=UDP_HOST)
        self.udp_port_var   = tk.StringVar(value=str(UDP_SEND_PORT))
        self.udp_listen_var = tk.StringVar(value=str(UDP_LISTEN_PORT))

        # Rest
        self.rest_angle_var  = tk.StringVar(value="0.0")
        self.rest_damper_var = tk.IntVar(value=0)

        # Calibration
        self.cal_start_angle_var = tk.StringVar(value="0.0")
        self.cal_damper_var      = tk.IntVar(value=0)
        self.cal_phase_var       = tk.StringVar(value="—")
        self.cal_mode_var      = tk.StringVar(value="single")
        self.cal_hold_time_var = tk.StringVar(value="3.0")
        self.cal_rest_time_var = tk.StringVar(value="3.0")
        self.cal_reps_var      = tk.StringVar(value="3")

        # ROM
        self.calib_damper_var      = tk.IntVar(value=0)
        self.rom_passive_speed_var = tk.StringVar(value="20.0")
        self.rom_info_var          = tk.StringVar(value="—")
        self.rom_mode_var         = tk.StringVar(value="single")
        self.rom_hold_time_var    = tk.StringVar(value="3.0")
        self.rom_rest_time_var    = tk.StringVar(value="3.0")
        self.rom_reps_var         = tk.StringVar(value="3")
        self.rom_return_tol_var   = tk.StringVar(value="5.0")
        self.rom_return_timeout_var = tk.StringVar(value="5.0")

        # AAN
        self.aan_active_end_var  = tk.StringVar(value="30.0")
        self.aan_passive_end_var = tk.StringVar(value="50.0")
        self.aan_speed_var       = tk.StringVar(value="20.0")
        self.aan_timeout_var     = tk.StringVar(value="5.0")
        self.aan_pause_var       = tk.StringVar(value="0.0")
        self.aan_damper_var      = tk.IntVar(value=0)
        self.aan_phase_var       = tk.StringVar(value="—")
        self.aan_mode_var           = tk.StringVar(value="single")
        self.aan_hold_time_var      = tk.StringVar(value="3.0")
        self.aan_rest_time_var      = tk.StringVar(value="3.0")
        self.aan_reps_var           = tk.StringVar(value="3")
        self.aan_return_tol_var     = tk.StringVar(value="5.0")
        self.aan_return_timeout_var = tk.StringVar(value="5.0")
        self.aan_return_time_var   = tk.StringVar(value="3.0")
        self.aan_damper_return_var = tk.IntVar(value=1)

        # Passive Movement
        self.pm_target_var    = tk.StringVar(value="40.0")
        self.pm_speed_var     = tk.StringVar(value="20.0")
        self.pm_hold_var      = tk.StringVar(value="2.0")
        self.pm_decel_var     = tk.IntVar(value=0)
        self.pm_damper_var    = tk.IntVar(value=0)
        self.pm_phase_var     = tk.StringVar(value="—")
        self.pm_reps_var = tk.StringVar(value="1")
        self.pm_rest_var = tk.StringVar(value="2.0")
        self.pm_active_end_var = tk.StringVar(value="20.0")

        # Active Movement
        self.am_return_tol_var = tk.StringVar(value="5.0")
        self.am_rest_var       = tk.StringVar(value="2.0")
        self.am_reps_var       = tk.StringVar(value="1")
        self.am_damper_var    = tk.IntVar(value=0)
        self.am_phase_var     = tk.StringVar(value="—")

        # # Back and Forth
        # self.bf_left_var   = tk.StringVar(value="-30.0")
        # self.bf_right_var  = tk.StringVar(value="30.0")
        # self.bf_speed_var  = tk.StringVar(value="20.0")
        # self.bf_reps_var   = tk.StringVar(value="10")
        # self.bf_damper_var = tk.IntVar(value=0)
        # self.bf_phase_var  = tk.StringVar(value="—")

        # RAH
        self.rah_limit_var   = tk.StringVar(value="10.0")
        self.rah_channel_var = tk.StringVar(value="Fx")
        self.rah_phase_var   = tk.StringVar(value="—")
        self._rah_t0         = None

        # Internal state
        self._csv_path              = ""
        self._udp_log_path          = ""
        self._motion_polling        = False
        self._cal_hold_marker_sent  = False
        self._cal_return_start_ts   = None
        self._cal_return_stop_ts    = None

        self._build_ui()
        self._serial_poll()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # another window for live display
        self.display = DisplayWindow(self)

    # ══════════════════════════════════════════════════════════════════════════
    #  UI construction
    # ══════════════════════════════════════════════════════════════════════════
    def _build_ui(self):
        pad = {"padx": 8, "pady": 5}
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left  = ttk.Frame(self)
        right = ttk.Frame(self)
        left.grid( row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)

        # Trial + Handedness
        top_row = ttk.Frame(left)
        top_row.pack(fill="x", padx=8, pady=(5, 2))
        top_row.columnconfigure(0, weight=0)
        top_row.columnconfigure(1, weight=1)
        trial_frm = ttk.LabelFrame(top_row, text="Trial")
        trial_frm.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tr_row = ttk.Frame(trial_frm)
        tr_row.pack(padx=6, pady=5)
        ttk.Label(tr_row, text="No:").pack(side="left")
        ttk.Entry(tr_row, textvariable=self.trial_var, width=5).pack(side="left", padx=4)
        ttk.Button(tr_row, text="+1", command=self._trial_increment, width=4).pack(side="left")
        hand_frm = ttk.LabelFrame(top_row, text="Handedness")
        hand_frm.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ttk.Radiobutton(hand_frm, text="Right  (+wrist = ext)",
                        variable=self.handedness_var, value="Right").pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(hand_frm, text="Left  (+wrist = flex)",
                        variable=self.handedness_var, value="Left").pack(side="left", padx=8, pady=4)

        # UDP
        frm = ttk.LabelFrame(left, text="UDP Marker Broadcast")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Host:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(frm, textvariable=self.udp_host_var, width=14).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Port:").grid(row=0, column=2, sticky="w", padx=(12, 2))
        ttk.Entry(frm, textvariable=self.udp_port_var, width=8).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Listen Port:").grid(row=1, column=0, sticky="w", padx=6, pady=(0, 4))
        ttk.Entry(frm, textvariable=self.udp_listen_var, width=8).grid(row=1, column=1, sticky="w", padx=4)

        # Serial + LC
        conn_row = ttk.Frame(left)
        conn_row.pack(fill="x", padx=8, pady=(2, 2))
        conn_row.columnconfigure(0, weight=1)
        conn_row.columnconfigure(1, weight=1)
        ser_frm = ttk.LabelFrame(conn_row, text="Serial (Exo)")
        ser_frm.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        ser_frm.columnconfigure(1, weight=1)
        ttk.Label(ser_frm, text="Port:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        ttk.Entry(ser_frm, textvariable=self.port_var, width=7).grid(row=0, column=1, sticky="w", padx=3)
        self.btn_connect = ttk.Button(ser_frm, text="Connect",
                                      command=self.on_serial_connect, width=8)
        self.btn_connect.grid(row=0, column=2, padx=3)
        self.btn_disconnect = ttk.Button(ser_frm, text="Disconnect",
                                         command=self.on_serial_disconnect,
                                         state="disabled", width=9)
        self.btn_disconnect.grid(row=0, column=3, padx=3)
        ttk.Label(ser_frm, textvariable=self.serial_status,
                  font=("Segoe UI", 8)).grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(0, 3))
        lc_frm = ttk.LabelFrame(conn_row, text="Load Cell (Nano17)")
        lc_frm.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        lc_frm.columnconfigure(1, weight=1)
        ttk.Label(lc_frm, text="Port:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        ttk.Entry(lc_frm, textvariable=self.lc_port_var, width=7).grid(row=0, column=1, sticky="w", padx=3)
        self.btn_lc_connect = ttk.Button(lc_frm, text="Connect",
                                         command=self.on_lc_connect, width=8)
        self.btn_lc_connect.grid(row=0, column=2, padx=3)
        self.btn_lc_disconnect = ttk.Button(lc_frm, text="Disconnect",
                                            command=self.on_lc_disconnect,
                                            state="disabled", width=9)
        self.btn_lc_disconnect.grid(row=0, column=3, padx=3)
        ttk.Label(lc_frm, textvariable=self.lc_status_var,
                  foreground="gray", font=("Segoe UI", 8)).grid(
            row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(0, 3))

        # Direction
        frm = ttk.LabelFrame(left, text="ROM Direction")
        frm.pack(fill="x", **pad)
        ttk.Radiobutton(frm, text="Extension  (+1)",
                        variable=self.direction_var, value="1").pack(side="left", padx=12, pady=4)
        ttk.Radiobutton(frm, text="Flexion  (-1)",
                        variable=self.direction_var, value="-1").pack(side="left", padx=12, pady=4)

        # Manual Controls
        frm = ttk.LabelFrame(left, text="Manual Controls")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Servo Angle (gear°):").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.servo_scale = ttk.Scale(frm, from_=-90, to=90, orient="horizontal",
                                     command=self._on_servo_scale)
        self.servo_scale.set(0)
        self.servo_scale.grid(row=0, column=1, sticky="we", padx=4)
        self.servo_entry = ttk.Entry(frm, width=6)
        self.servo_entry.insert(0, "0")
        self.servo_entry.grid(row=0, column=2, padx=4)
        ttk.Button(frm, text="Set", command=self.on_set_servo, width=6).grid(row=0, column=3, padx=4)
        ttk.Label(frm, text="Damper (0-255):").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.manual_damper_scale = ttk.Scale(frm, from_=0, to=255, orient="horizontal",
                                             command=self._on_manual_damper_scale)
        self.manual_damper_scale.set(0)
        self.manual_damper_scale.grid(row=1, column=1, sticky="we", padx=4)
        self.manual_damper_entry = ttk.Entry(frm, width=6)
        self.manual_damper_entry.insert(0, "0")
        self.manual_damper_entry.grid(row=1, column=2, padx=4)
        ttk.Button(frm, text="Set", command=self.on_set_damper, width=6).grid(row=1, column=3, padx=4)
        mode_row = ttk.Frame(frm)
        mode_row.grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 6))
        ttk.Button(mode_row, text="Passive Mode (torque off)",
                   command=lambda: self.serial_worker.send("TORQUE_OFF"),
                   width=22).pack(side="left", padx=(0, 6))
        ttk.Button(mode_row, text="Active Mode (torque on)",
                   command=lambda: self.serial_worker.send("TORQUE_ON"),
                   width=22).pack(side="left", padx=(0, 6))
        ttk.Button(mode_row, text="Zero Wrist",
                   command=self.on_zero, width=12).pack(side="left")
        ttk.Button(mode_row, text="Sync IMU",
           command=self.on_sync_imu, width=12).pack(side="left", padx=(6, 0))
        ttk.Button(mode_row, text="Zero LC",
                command=self.on_zero_lc, width=10).pack(side="left", padx=(6, 0))

        # Tabs
        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        tab_rest = ttk.Frame(self.notebook)
        tab_cal  = ttk.Frame(self.notebook)
        tab_rom  = ttk.Frame(self.notebook)
        tab_real = ttk.Frame(self.notebook)
        tab_pm   = ttk.Frame(self.notebook)
        # tab_bf   = ttk.Frame(self.notebook)
        tab_am   = ttk.Frame(self.notebook)
        tab_rah  = ttk.Frame(self.notebook)
        self.notebook.add(tab_rest, text="  Rest  ")
        self.notebook.add(tab_cal,  text="  Calibration  ")
        self.notebook.add(tab_rom,  text="  ROM Assessment  ")
        self.notebook.add(tab_pm,   text="  Passive Movement  ")
        # self.notebook.add(tab_bf,   text="  Back and Forth  ")
        self.notebook.add(tab_am,   text="  Active Movement  ")
        self.notebook.add(tab_real, text="  AAN  ")
        self.notebook.add(tab_rah,  text="  Ramp & Hold  ")
        self._build_rest_tab(tab_rest)
        self._build_calibration_tab(tab_cal)
        self._build_rom_tab(tab_rom)
        self._build_pm_tab(tab_pm)
        # self._build_bf_tab(tab_bf)
        self._build_am_tab(tab_am)
        self._build_aan_tab(tab_real)
        self._build_rah_tab(tab_rah)

        # RIGHT panel
        frm = ttk.LabelFrame(right, text="Live Monitoring")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1); frm.columnconfigure(3, weight=1)
        ttk.Label(frm, text="Wrist Angle:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Label(frm, textvariable=self.angle_var,
                  font=("Segoe UI", 13, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(frm, text="IMU:").grid(row=0, column=2, sticky="w", padx=(20, 6))
        ttk.Label(frm, textvariable=self.imu_var,
                  font=("Segoe UI", 9), foreground="#1e90ff").grid(row=0, column=3, sticky="w")
        ttk.Label(frm, text="Current:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        ttk.Label(frm, textvariable=self.current_var,
                  font=("Segoe UI", 11, "bold")).grid(row=1, column=1, sticky="w")

        frm = ttk.LabelFrame(right, text="Recording")
        frm.pack(fill="x", **pad)
        ttk.Label(frm, textvariable=self.rec_status_var).pack(anchor="w", padx=8, pady=6)

        frm = ttk.LabelFrame(right, text="Motion Status")
        frm.pack(fill="x", **pad)
        ttk.Label(frm, textvariable=self.motion_status,
                  font=("Segoe UI", 9, "bold"), foreground="#00e676").pack(anchor="w", padx=8, pady=4)
        ttk.Label(frm, textvariable=self.aan_phase_var,
                  font=("Segoe UI", 8), foreground="gray",
                  wraplength=520, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # LC Monitor
        self.fx_frm = ttk.LabelFrame(right, text="LC Monitor (real-time)")
        self.fx_frm.pack(fill="x", **pad)
        row1 = ttk.Frame(self.fx_frm)
        row1.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(row1, text="Channel:").pack(side="left")
        ttk.Combobox(row1, textvariable=self.lc_channel_var,
                     values=["Fx","Fy","Fz","Tx","Ty","Tz","Fnorm","Tnorm"],
                     width=7, state="readonly").pack(side="left", padx=(4, 16))
        ttk.Label(row1, text="Baseline (N or Nm):").pack(side="left")
        ttk.Entry(row1, textvariable=self.fx_max_var, width=8).pack(side="left", padx=6)
        ttk.Label(row1, text="— dashed line", foreground="gray",
                  font=("Segoe UI", 8)).pack(side="left")
        self.fx_canvas = tk.Canvas(self.fx_frm, width=560, height=160,
                                   bg="#0d1117", highlightthickness=1,
                                   highlightbackground="#333")
        self.fx_canvas.pack(padx=6, pady=(2, 4))
        ttk.Label(self.fx_frm, textvariable=self.fx_live_var,
                  font=("Segoe UI", 10, "bold"), foreground="#ff6b6b").pack(
                      anchor="w", padx=8, pady=(0, 6))

        # ROM canvas
        self.rom_frm = ttk.LabelFrame(right, text="ROM Tracker")
        self.rom_frm.pack(fill="x", **pad)
        self.canvas = tk.Canvas(self.rom_frm, width=560, height=200,
                                bg="#1a1a2e", highlightthickness=1,
                                highlightbackground="#444")
        self.canvas.pack(padx=6, pady=6)
        ttk.Label(self.rom_frm, textvariable=self.rom_info_var,
                  font=("Segoe UI", 8), foreground="gray",
                  wraplength=540, justify="left").pack(anchor="w", padx=6, pady=(0, 6))

        # RAH monitor
        self.rah_frm = ttk.LabelFrame(right, text="Ramp & Hold Monitor")
        self.rah_canvas = tk.Canvas(self.rah_frm, width=560, height=380,
                                    bg="#0d1117", highlightthickness=1,
                                    highlightbackground="#333")
        self.rah_canvas.pack(fill="both", expand=True, padx=6, pady=6)

        self._draw_canvas_idle()
        self._draw_fx_canvas_empty()
        self._fx_monitor_loop()
        self._draw_rah_canvas_idle()
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ══════════════════════════════════════════════════════════════════════════
    #  Tab builders
    # ══════════════════════════════════════════════════════════════════════════
    def _build_rest_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Rest Control")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Hold Angle (deg):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.rest_angle_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Damper PWM:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Scale(frm, from_=0, to=255, orient="horizontal",
                  variable=self.rest_damper_var, length=140).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, textvariable=self.rest_damper_var, width=4).grid(row=1, column=2, sticky="w", padx=4)
        ttk.Label(frm, text="Servo ramps to hold angle then records baseline.",
                  foreground="gray", font=("Segoe UI", 8),
                  wraplength=380, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))
        r1 = ttk.Frame(frm); r1.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 6))
        self.rest_btn_start = ttk.Button(r1, text="Start", command=self.rest_on_start, width=12)
        self.rest_btn_start.pack(side="left", padx=(0, 6))
        self.rest_btn_stop = ttk.Button(r1, text="Stop", command=self.rest_on_stop,
                                        state="disabled", width=12)
        self.rest_btn_stop.pack(side="left")

    def _build_calibration_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Calibration Control")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="Start Angle (deg):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.cal_start_angle_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Damper PWM:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Scale(frm, from_=0, to=255, orient="horizontal",
                  variable=self.cal_damper_var, length=140).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, textvariable=self.cal_damper_var, width=4).grid(row=1, column=2, sticky="w", padx=4)
        ttk.Label(frm, textvariable=self.cal_phase_var,
                  foreground="gray", font=("Segoe UI", 8),
                  wraplength=420, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 6))
        r1 = ttk.Frame(frm); r1.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 6))
        self.cal_btn_start = ttk.Button(r1, text="Start", command=self.calibration_on_start, width=12)
        self.cal_btn_start.pack(side="left", padx=(0, 6))
        self.cal_btn_return = ttk.Button(r1, text="Return", command=self.calibration_on_return,
                                         state="disabled", width=12)
        self.cal_btn_return.pack(side="left", padx=(0, 6))
        self.cal_btn_final = ttk.Button(r1, text="Final Pos", command=self.calibration_on_final,
                                        state="disabled", width=12)
        self.cal_btn_final.pack(side="left", padx=(0, 6))
        self.cal_btn_stop = ttk.Button(r1, text="Stop", command=self.calibration_on_stop,
                                       state="disabled", width=12)
        self.cal_btn_stop.pack(side="left")

    def _cal_mode_switch(self):
        if self.cal_mode_var.get() == "single":
            self._cal_loop_frm.pack_forget()
            self._cal_single_frm.pack(fill="x", padx=6, pady=4)
        else:
            self._cal_single_frm.pack_forget()
            self._cal_loop_frm.pack(fill="x", padx=6, pady=4)

    def _build_rom_tab(self, parent):
        pad = {"padx": 6, "pady": 4}

        # Parameters
        shared = ttk.LabelFrame(parent, text="Parameters")
        shared.pack(fill="x", **pad)
        damp_row = ttk.Frame(shared)
        damp_row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(damp_row, text="Passive drive damper:").pack(side="left")
        ttk.Scale(damp_row, from_=0, to=255, orient="horizontal",
                  variable=self.calib_damper_var, length=120).pack(side="left", padx=6)
        ttk.Label(damp_row, textvariable=self.calib_damper_var, width=4).pack(side="left")
        speed_row = ttk.Frame(shared)
        speed_row.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Label(speed_row, text="Passive drive speed (wrist deg/s):").pack(side="left")
        ttk.Entry(speed_row, textvariable=self.rom_passive_speed_var, width=8).pack(side="left", padx=6)

        # Repetition Parameters
        frm2 = ttk.LabelFrame(parent, text="Repetition Parameters")
        frm2.pack(fill="x", **pad)
        frm2.columnconfigure(1, weight=1); frm2.columnconfigure(3, weight=1)
        ttk.Label(frm2, text="Hold time (s):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm2, textvariable=self.rom_hold_time_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm2, text="Rest time (s):").grid(row=0, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm2, textvariable=self.rom_rest_time_var, width=8).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(frm2, text="Reps (1 = single):").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm2, textvariable=self.rom_reps_var, width=8).grid(row=1, column=1, sticky="w", padx=4)

        # Control
        frm3 = ttk.LabelFrame(parent, text="Experiment Control")
        frm3.pack(fill="x", **pad)
        r1 = ttk.Frame(frm3); r1.pack(fill="x", padx=6, pady=(6, 2))
        self.calib_btn_start = ttk.Button(r1, text="Start", command=self.rom_on_start, width=12)
        self.calib_btn_start.pack(side="left", padx=(0, 6))
        self.calib_btn_stop = ttk.Button(r1, text="Stop", command=self.rom_on_stop,
                                         state="disabled", width=12)
        self.calib_btn_stop.pack(side="left")
        r2 = ttk.Frame(frm3); r2.pack(fill="x", padx=6, pady=(2, 6))
        self.calib_btn_active_end = ttk.Button(r2, text="✓ Mark Active End",
                                               command=self.rom_on_mark_active_end,
                                               state="disabled", width=20)
        self.calib_btn_active_end.pack(side="left", padx=(0, 6))
        self.calib_btn_passive_end = ttk.Button(r2, text="✓ Mark Passive End",
                                                command=self.rom_on_mark_passive_end,
                                                state="disabled", width=20)
        self.calib_btn_passive_end.pack(side="left")

    def _rom_mode_switch(self):
        if self.rom_mode_var.get() == "single":
            self._rom_loop_frm.pack_forget()
            self._rom_single_frm.pack(fill="x", padx=6, pady=4)
        else:
            self._rom_single_frm.pack_forget()
            self._rom_loop_frm.pack(fill="x", padx=6, pady=4)

    def _rom_mode_switch(self):
        if self.rom_mode_var.get() == "single":
            self._rom_loop_frm.pack_forget()
            self._rom_single_frm.pack(fill="x", padx=6, pady=4)
        else:
            self._rom_single_frm.pack_forget()
            self._rom_loop_frm.pack(fill="x", padx=6, pady=4)

    def _build_aan_tab(self, parent):
        pad = {"padx": 6, "pady": 4}

        # AAN Parameters
        frm = ttk.LabelFrame(parent, text="AAN Parameters")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1); frm.columnconfigure(3, weight=1)
        ttk.Label(frm, text="Active End (deg):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.aan_active_end_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Passive End (deg):").grid(row=0, column=2, sticky="w", padx=(16,6), pady=5)
        ttk.Entry(frm, textvariable=self.aan_passive_end_var, width=8).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Passive drive speed (wrist deg/s):").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.aan_speed_var, width=8).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Active timeout (s):").grid(row=1, column=2, sticky="w", padx=(16,6), pady=5)
        ttk.Entry(frm, textvariable=self.aan_timeout_var, width=8).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Pre-passive pause (s, 0=skip):").grid(row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.aan_pause_var, width=8).grid(row=2, column=1, sticky="w", padx=4)
        damp_row = ttk.Frame(frm)
        damp_row.grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(2,6))
        ttk.Label(damp_row, text="Damper PWM (active/passive):").pack(side="left")
        ttk.Scale(damp_row, from_=0, to=255, orient="horizontal",
                  variable=self.aan_damper_var, length=120).pack(side="left", padx=6)
        ttk.Label(damp_row, textvariable=self.aan_damper_var, width=4).pack(side="left")

        # Repetition Parameters
        frm2 = ttk.LabelFrame(parent, text="Repetition Parameters")
        frm2.pack(fill="x", **pad)
        frm2.columnconfigure(1, weight=1); frm2.columnconfigure(3, weight=1)
        ttk.Label(frm2, text="Hold time (s):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm2, textvariable=self.aan_hold_time_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm2, text="Return window (s):").grid(row=0, column=2, sticky="w", padx=(16,6), pady=5)
        ttk.Entry(frm2, textvariable=self.aan_return_time_var, width=8).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(frm2, text="Rest time (s):").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm2, textvariable=self.aan_rest_time_var, width=8).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm2, text="Reps (1 = single):").grid(row=1, column=2, sticky="w", padx=(16,6), pady=5)
        ttk.Entry(frm2, textvariable=self.aan_reps_var, width=8).grid(row=1, column=3, sticky="w", padx=4)
        damp_ret_row = ttk.Frame(frm2)
        damp_ret_row.grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(2,6))
        ttk.Checkbutton(damp_ret_row, text="Damper ON during return (default: on)",
                        variable=self.aan_damper_return_var).pack(side="left")

        # Control
        frm3 = ttk.LabelFrame(parent, text="Experiment Control")
        frm3.pack(fill="x", **pad)
        r1 = ttk.Frame(frm3); r1.pack(fill="x", padx=6, pady=(6,6))
        self.aan_btn_start = ttk.Button(r1, text="▶  Start", command=self.aan_on_start, width=14)
        self.aan_btn_start.pack(side="left", padx=(0,6))
        self.aan_btn_stop = ttk.Button(r1, text="■  Stop", command=self.aan_on_stop,
                                       state="disabled", width=14)
        self.aan_btn_stop.pack(side="left")

    def _aan_mode_switch(self):
        if self.aan_mode_var.get() == "single":
            self._aan_loop_frm.pack_forget()
            self._aan_single_frm.pack(fill="x", padx=6, pady=4)
        else:
            self._aan_single_frm.pack_forget()
            self._aan_loop_frm.pack(fill="x", padx=6, pady=4)

    def _build_rah_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Parameters")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1); frm.columnconfigure(3, weight=1)
        ttk.Label(frm, text="Force/Torque limit (N or Nm):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.rah_limit_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="LC Channel:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Combobox(frm, textvariable=self.rah_channel_var,
                     values=["Fx","Fy","Fz","Tx","Ty","Tz","Fnorm","Tnorm"],
                     width=7, state="readonly").grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Servo holds at current wrist angle. Physical lock required.",
                  foreground="gray", font=("Segoe UI", 8),
                  wraplength=300, justify="left").grid(row=1, column=2, columnspan=2, sticky="w", padx=(16, 6))
        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(fill="x", **pad)
        r1 = ttk.Frame(frm2); r1.pack(fill="x", padx=6, pady=(6, 2))
        self.rah_btn_start = ttk.Button(r1, text="▶  Start", command=self.rah_on_start, width=14)
        self.rah_btn_start.pack(side="left", padx=(0, 6))
        self.rah_btn_stop = ttk.Button(r1, text="■  Stop", command=self.rah_on_stop,
                                       state="disabled", width=14)
        self.rah_btn_stop.pack(side="left")
        ttk.Label(frm2, textvariable=self.rah_phase_var,
                  font=("Segoe UI", 9, "bold"), foreground="#ffd700").pack(
                      anchor="w", padx=10, pady=(4, 8))

    def _build_pm_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Movement Parameters")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1); frm.columnconfigure(3, weight=1)
        # ttk.Label(frm, text="Direction:").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        # dir_row = ttk.Frame(frm)
        # dir_row.grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
        # ttk.Radiobutton(dir_row, text="Extension (+1)", variable=self.pm_direction_var, value="1").pack(side="left", padx=(0, 10))
        # ttk.Radiobutton(dir_row, text="Flexion (-1)",   variable=self.pm_direction_var, value="-1").pack(side="left")
        ttk.Label(frm, text="Target (wrist deg):").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.pm_target_var, width=8).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Active end (wrist deg):").grid(row=1, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.pm_active_end_var, width=8).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Speed (wrist deg/s):").grid(row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.pm_speed_var, width=8).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Hold duration (s):").grid(row=2, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.pm_hold_var, width=8).grid(row=2, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Rest time (s):").grid(row=3, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.pm_rest_var, width=8).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Reps (1 = single):").grid(row=3, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.pm_reps_var, width=8).grid(row=3, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Damper PWM:").grid(row=4, column=0, sticky="w", padx=6, pady=5)
        ttk.Scale(frm, from_=0, to=255, orient="horizontal",
                  variable=self.pm_damper_var, length=100).grid(row=4, column=1, sticky="w", padx=4)
        ttk.Label(frm, textvariable=self.pm_damper_var, width=4).grid(row=4, column=2, sticky="w", padx=4)
        decel_row = ttk.Frame(frm)
        decel_row.grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 6))
        ttk.Checkbutton(decel_row, text="Cos deceleration", variable=self.pm_decel_var).pack(side="left")
        ttk.Label(decel_row, text="  Active end: sub-marker threshold (1.0→1.5 go, 2.5→3.0 return)",
                  foreground="gray", font=("Segoe UI", 8)).pack(side="left", padx=8)
        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(fill="x", **pad)
        r1 = ttk.Frame(frm2); r1.pack(fill="x", padx=6, pady=(6, 2))
        self.pm_btn_start = ttk.Button(r1, text="▶  Start", command=self.pm_on_start, width=14)
        self.pm_btn_start.pack(side="left", padx=(0, 6))
        self.pm_btn_stop = ttk.Button(r1, text="■  Stop", command=self.pm_on_stop,
                                      state="disabled", width=14)
        self.pm_btn_stop.pack(side="left")
        ttk.Label(frm2, textvariable=self.pm_phase_var,
                  font=("Segoe UI", 8), foreground="gray").pack(anchor="w", padx=10, pady=(2, 6))
    # def _build_bf_tab(self, parent):
    #     pad = {"padx": 6, "pady": 4}
    #     frm = ttk.LabelFrame(parent, text="Parameters")
    #     frm.pack(fill="x", **pad)
    #     frm.columnconfigure(1, weight=1); frm.columnconfigure(3, weight=1)
    #     ttk.Label(frm, text="Left boundary (wrist deg):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
    #     ttk.Entry(frm, textvariable=self.bf_left_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
    #     ttk.Label(frm, text="Right boundary (wrist deg):").grid(row=0, column=2, sticky="w", padx=(16, 6), pady=5)
    #     ttk.Entry(frm, textvariable=self.bf_right_var, width=8).grid(row=0, column=3, sticky="w", padx=4)
    #     ttk.Label(frm, text="Speed (wrist deg/s):").grid(row=1, column=0, sticky="w", padx=6, pady=5)
    #     ttk.Entry(frm, textvariable=self.bf_speed_var, width=8).grid(row=1, column=1, sticky="w", padx=4)
    #     ttk.Label(frm, text="Total reps (boundary touches):").grid(row=1, column=2, sticky="w", padx=(16, 6), pady=5)
    #     ttk.Entry(frm, textvariable=self.bf_reps_var, width=8).grid(row=1, column=3, sticky="w", padx=4)
    #     ttk.Label(frm, text="Damper PWM:").grid(row=2, column=0, sticky="w", padx=6, pady=5)
    #     ttk.Scale(frm, from_=0, to=255, orient="horizontal",
    #               variable=self.bf_damper_var, length=120).grid(row=2, column=1, sticky="w", padx=4)
    #     ttk.Label(frm, textvariable=self.bf_damper_var, width=4).grid(row=2, column=2, sticky="w", padx=4)
    #     frm2 = ttk.LabelFrame(parent, text="Experiment Control")
    #     frm2.pack(fill="x", **pad)
    #     r1 = ttk.Frame(frm2); r1.pack(fill="x", padx=6, pady=(6, 2))
    #     self.bf_btn_start = ttk.Button(r1, text="▶  Start", command=self.bf_on_start, width=14)
    #     self.bf_btn_start.pack(side="left", padx=(0, 6))
    #     self.bf_btn_stop = ttk.Button(r1, text="■  Stop", command=self.bf_on_stop,
    #                                   state="disabled", width=14)
    #     self.bf_btn_stop.pack(side="left")
    #     ttk.Label(frm2, textvariable=self.bf_phase_var,
    #               font=("Segoe UI", 8), foreground="gray").pack(anchor="w", padx=10, pady=(2, 6))

    def _build_am_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Parameters")
        frm.pack(fill="x", **pad)
        frm.columnconfigure(1, weight=1); frm.columnconfigure(3, weight=1)
        # ttk.Label(frm, text="Direction:").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        # dir_row = ttk.Frame(frm)
        # dir_row.grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
        # ttk.Radiobutton(dir_row, text="Extension (+1)", variable=self.am_direction_var, value="1").pack(side="left", padx=(0, 10))
        # ttk.Radiobutton(dir_row, text="Flexion (-1)",   variable=self.am_direction_var, value="-1").pack(side="left")
        ttk.Label(frm, text="Damper PWM:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Scale(frm, from_=0, to=255, orient="horizontal",
                  variable=self.am_damper_var, length=120).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, textvariable=self.am_damper_var, width=4).grid(row=1, column=2, sticky="w", padx=4)
        ttk.Label(frm, text="Return tol (deg):").grid(row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.am_return_tol_var, width=8).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Rest time (s):").grid(row=2, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.am_rest_var, width=8).grid(row=2, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Reps (1 = single):").grid(row=3, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.am_reps_var, width=8).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Torque off throughout. Operator marks end.",
                  foreground="gray", font=("Segoe UI", 8)).grid(
                      row=3, column=2, columnspan=2, sticky="w", padx=(16, 6))
        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(fill="x", **pad)
        r1 = ttk.Frame(frm2); r1.pack(fill="x", padx=6, pady=(6, 2))
        self.am_btn_start = ttk.Button(r1, text="▶  Start", command=self.am_on_start, width=14)
        self.am_btn_start.pack(side="left", padx=(0, 6))
        self.am_btn_end = ttk.Button(r1, text="✓ Mark End", command=self.am_on_end,
                                     state="disabled", width=14)
        self.am_btn_end.pack(side="left", padx=(0, 6))
        self.am_btn_stop = ttk.Button(r1, text="■  Stop", command=self.am_on_stop,
                                      state="disabled", width=14)
        self.am_btn_stop.pack(side="left")
        ttk.Label(frm2, textvariable=self.am_phase_var,
                  font=("Segoe UI", 8), foreground="gray").pack(anchor="w", padx=10, pady=(2, 6))

    # ══════════════════════════════════════════════════════════════════════════
    #  Shared helpers
    # ══════════════════════════════════════════════════════════════════════════
    def _udp_send(self, prev: int, new: int):
        host = self.udp_host_var.get().strip()
        try:
            send_port   = int(self.udp_port_var.get().strip())
            listen_port = int(self.udp_listen_var.get().strip())
        except ValueError:
            send_port   = UDP_SEND_PORT
            listen_port = UDP_LISTEN_PORT
        self.udp.host        = host
        self.udp.send_port   = send_port
        self.udp.listen_port = listen_port
        self.udp.send(prev, new, self._udp_log_path, lambda: self.recorder.t0)

    def _cur_marker(self, mode) -> int:
        """Read current marker from mode.PHASE_TO_MARKER, default 1."""
        if mode is None:
            return 1
        return getattr(mode, "PHASE_TO_MARKER", {}).get(mode.sub_phase, 1)

    def _trial_increment(self):
        try:
            self.trial_var.set(str(int(self.trial_var.get()) + 1))
        except ValueError:
            self.trial_var.set("1")

    def _trial_str(self) -> str:
        try:
            n = int(self.trial_var.get())
        except ValueError:
            n = 1
        return f"trial_{n:02d}"

    def _make_file_paths(self, subdir: str, stem: str):
        root = str(DATA_ROOT)
        sub  = os.path.join(root, subdir)
        os.makedirs(root, exist_ok=True)
        os.makedirs(sub,  exist_ok=True)
        self._csv_path         = os.path.join(root, f"{stem}.csv")
        self._udp_log_path     = os.path.join(root, f"{stem}_udp.txt")
        self._csv_path_sub     = os.path.join(sub,  f"{stem}.csv")
        self._udp_log_path_sub = os.path.join(sub,  f"{stem}_udp.txt")

    def _copy_to_subdir(self):
        for src, dst in [
            (self._csv_path,     getattr(self, "_csv_path_sub",     None)),
            (self._udp_log_path, getattr(self, "_udp_log_path_sub", None)),
        ]:
            if src and dst and os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass

    def _write_udp_header(self, params: dict):
        try:
            with open(self._udp_log_path, "w", encoding="utf-8") as f:
                f.write(f"experiment:       {params.pop('experiment', '?')}\n")
                f.write(f"start_time_local: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}\n")
                f.write(f"trial:            {self._trial_str()}\n")
                f.write(f"handedness:       {self.handedness_var.get()}\n")
                for k, v in params.items():
                    f.write(f"{k+':':<28}{v}\n")
                f.write("\n")
                f.write("type\tt_rel(s)\tdatetime_local\tprev_marker\tnew_marker\tround_trip_s\n")
        except Exception:
            pass

    def _parse_float(self, var, default: float) -> float:
        try:
            return float(var.get())
        except ValueError:
            return default

    def _get_direction(self) -> int:
        try:
            return int(self.direction_var.get())
        except ValueError:
            return 1

    def _get_wrist(self) -> float:
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 7:
            return (sample[6] - self.wrist_zero) / GEAR_RATIO
        return 0.0

    @staticmethod
    def _quat_to_imu(qw, qx, qy, qz) -> float:
        sinz = 2.0 * (qw * qz + qx * qy)
        cosz = 1.0 - 2.0 * (qy * qy + qz * qz)
        return -_math.degrees(_math.atan2(sinz, cosz))

    def _on_tab_changed(self, event=None):
        try:
            tab_text = self.notebook.tab(self.notebook.select(), "text").strip()
        except Exception:
            return
        pad = {"padx": 6, "pady": 4}
        is_rah = (tab_text == "Ramp & Hold")
        if is_rah:
            self.fx_frm.pack_forget()
            self.rom_frm.pack_forget()
            if not self.rah_frm.winfo_ismapped():
                self.rah_frm.pack(fill="both", expand=True, **pad)
        else:
            self.rah_frm.pack_forget()
            if not self.fx_frm.winfo_ismapped():
                self.fx_frm.pack(fill="x", **pad)
            if not self.rom_frm.winfo_ismapped():
                self.rom_frm.pack(fill="x", **pad)

    # ══════════════════════════════════════════════════════════════════════════
    #  Hardware controls
    # ══════════════════════════════════════════════════════════════════════════
    def on_serial_connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Enter a COM port.")
            return
        def _ok():
            self.after(0, lambda: (
                self.serial_status.set(f"Serial: Connected ({port})"),
                self.btn_connect.config(state="disabled"),
                self.btn_disconnect.config(state="normal"),
            ))
        def _err(e):
            self.after(0, lambda: self.serial_status.set(f"Serial: Error ({e})"))
        self.serial_status.set("Serial: Connecting…")
        self.serial_worker.connect(port, on_success=_ok, on_error=_err)

    def on_serial_disconnect(self):
        self.serial_worker.disconnect()
        self.serial_status.set("Serial: Disconnected")
        self.btn_connect.config(state="normal")
        self.btn_disconnect.config(state="disabled")

    def on_lc_connect(self):
        port = self.lc_port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Enter LC COM port.")
            return
        if self.lc_worker.connected:
            return
        def _ok():
            self.after(0, lambda: (
                self.lc_status_var.set(self.lc_worker.status),
                self.btn_lc_connect.config(state="disabled"),
                self.btn_lc_disconnect.config(state="normal"),
            ))
        def _err(e):
            self.after(0, lambda: self.lc_status_var.set(f"LC: Error ({e})"))
        self.lc_status_var.set("LC: Connecting + taring…")
        self.lc_worker.connect(port=port, on_success=_ok, on_error=_err)

    def on_lc_disconnect(self):
        self.lc_worker.disconnect()
        self.lc_status_var.set("LC: Disconnected")
        self.btn_lc_connect.config(state="normal")
        self.btn_lc_disconnect.config(state="disabled")

    def on_zero(self):
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 8:
            self.wrist_zero = sample[6]
            self.imu_zero   = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4])

    def on_sync_imu(self):
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 7:
            encoder_wrist = (sample[6] - self.wrist_zero) / GEAR_RATIO
            imu_raw = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4])
            self.imu_zero = imu_raw - encoder_wrist

    def on_zero_lc(self):
        if not self.lc_worker.connected:
            messagebox.showerror("Error", "LC not connected.")
            return
        self.lc_worker.retare()

    def _on_servo_scale(self, val):
        if not hasattr(self, "servo_entry"):
            return
        self.servo_entry.delete(0, tk.END)
        self.servo_entry.insert(0, str(round(float(val), 1)))

    def _on_manual_damper_scale(self, val):
        if not hasattr(self, "manual_damper_entry"):
            return
        self.manual_damper_entry.delete(0, tk.END)
        self.manual_damper_entry.insert(0, str(int(float(val))))

    def on_set_servo(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Serial not connected.")
            return
        try:
            gear_ang = float(self.servo_entry.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Angle must be a number.")
            return
        if not (-90 <= gear_ang <= 90):
            messagebox.showerror("Error", "Angle out of range (-90 to 90).")
            return
        RAMP_SPEED = 30.0
        INTERVAL   = 0.01
        sample, _ = self.serial_worker.ring.get_latest()
        current_wrist = (sample[6] - self.wrist_zero) / GEAR_RATIO \
                        if (sample and len(sample) >= 7) else 0.0
        def _ramp(current=current_wrist, target=gear_ang):
            gap  = target - current
            step = _math.copysign(min(abs(gap), RAMP_SPEED * INTERVAL), gap) if gap != 0 else 0
            current += step
            motor = current * GEAR_RATIO + self.wrist_zero
            self.serial_worker.send(f"SET_ANG:{motor:.1f}")
            if abs(current - target) > 0.2:
                self.after(int(INTERVAL * 1000), lambda c=current: _ramp(c, target))
        _ramp()

    def on_set_damper(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Serial not connected.")
            return
        try:
            val = int(float(self.manual_damper_entry.get().strip()))
        except ValueError:
            messagebox.showerror("Error", "Damper must be an integer.")
            return
        if not (0 <= val <= 255):
            messagebox.showerror("Error", "Damper out of range (0-255).")
            return
        self.serial_worker.send(f"SET_DMP:{val}")

    def _serial_poll(self, interval_ms=100):
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 8:
            wrist = (sample[6] - self.wrist_zero) / GEAR_RATIO
            imu   = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4]) - self.imu_zero
            self.angle_var.set(f"{wrist:.2f} deg")
            self.imu_var.set(f"{imu:.2f} deg")
            self.current_var.set(f"{sample[5]:.2f} mA")
        self.after(interval_ms, self._serial_poll)

    # ══════════════════════════════════════════════════════════════════════════
    #  Rest handlers
    # ══════════════════════════════════════════════════════════════════════════
    def rest_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
                return
        rest_angle = self._parse_float(self.rest_angle_var, 0.0)
        damper     = int(self.rest_damper_var.get())
        mode = RestMode(rest_angle_deg=rest_angle, damper_pwm=damper)
        mode.reset()
 
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if self.handedness_var.get() == "Right" else "L"
        stem     = f"REST_{hand_str}_{ts}_{self._trial_str()}"
        self._make_file_paths("rest", stem)
        self._write_udp_header({"experiment": "rest",
                                 "hold_angle_deg": f"{rest_angle:.3f}",
                                 "damper_pwm": damper})
        self.rest_btn_start.config(state="disabled")
        self.rest_btn_stop.config(state="disabled")
        self.motion_status.set("REST — starting in 3…")
 
        def _launch():
            self.motion_runner.start(mode)
            self.serial_worker.send("TORQUE_ON")
            self.recorder.start(self._csv_path, mode)
            self.recorder.set_marker(1)
            get_t0 = lambda: self.recorder.t0
            self.udp.start_listen(self._udp_log_path, get_t0)
            self._udp_send(0, 1)
            self.motion_status.set(f"REST — servo holding {rest_angle:.1f}°")
            self.aan_phase_var.set(mode.phase)
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.rest_btn_stop.config(state="normal")
 
        self.display.countdown(_launch)

    def rest_on_stop(self):
        self._udp_send(1, 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.aan_phase_var.set("—")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.rest_btn_start.config(state="normal")
        self.rest_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

# ══════════════════════════════════════════════════════════════════════════
    #  Calibration handlers
    # ══════════════════════════════════════════════════════════════════════════
    def calibration_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
                return
        start_angle = self._parse_float(self.cal_start_angle_var, 0.0)
        damper      = int(self.cal_damper_var.get())

        mode = CalibrationMode(start_angle_deg=start_angle, damper_pwm=damper)
        mode.reset()

        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"CAL_{ts}_{self._trial_str()}"
        self._make_file_paths("calibration", stem)
        self._cal_hold_marker_sent = False
        self._cal_return_start_ts  = None
        self._cal_return_stop_ts   = None
        self._write_udp_header({"experiment":      "calibration",
                                 "start_angle_deg": f"{start_angle:.3f}",
                                 "damper_pwm":      damper})

        self.cal_btn_start.config(state="disabled")
        self.cal_btn_return.config(state="disabled")
        self.cal_btn_final.config(state="disabled")
        self.cal_btn_stop.config(state="disabled")
        self.motion_status.set("CALIBRATION — starting in 3…")

        def _launch():
            self.motion_runner.start(mode)
            self.serial_worker.send("TORQUE_ON")
            self.recorder.start(self._csv_path, mode)
            self.recorder.set_marker(1)
            get_t0 = lambda: self.recorder.t0
            self.udp.start_listen(self._udp_log_path, get_t0)
            self.udp.send(0, 1, self._udp_log_path, get_t0)

            def _on_phase(prev_sp, new_sp):
                prev_m = CalibrationMode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m  = CalibrationMode.PHASE_TO_MARKER.get(new_sp, 0)
                self.recorder.set_marker(new_m)
                if prev_m != new_m:
                    self.udp.send(prev_m, new_m, self._udp_log_path, get_t0)
                self.after(0, lambda s=new_sp: self._cal_on_phase_ui(s))

            mode.on_phase_change = _on_phase
            self.motion_status.set("CALIBRATION — moving to start angle")
            self.cal_phase_var.set("moving — waiting for encoder-confirmed arrival")
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.cal_btn_stop.config(state="normal")
            self._motion_polling = True
            self._calibration_ui_loop()

        self.display.countdown(_launch)

    def _cal_on_phase_ui(self, phase: str):
        mode = self.motion_runner.active_mode
        self.cal_phase_var.set(mode.phase if mode else "—")

        status_map = {
            "hold":   "CALIBRATION — hold at start angle, press Return when ready",
            "return": "CALIBRATION — returning, press Final Pos when at neutral",
            "done":   "CALIBRATION — complete",
        }
        self.motion_status.set(status_map.get(phase, f"CALIBRATION — {phase}"))

        self.cal_btn_return.config(state="normal" if phase == "hold"   else "disabled")
        self.cal_btn_final.config( state="normal" if phase == "return" else "disabled")

        if phase == "return":
            self.serial_worker.send("TORQUE_OFF")
            self._cal_return_start_ts = local_clock()

        if phase == "done":
            self.after(200, self.calibration_on_stop)

    def calibration_on_return(self):
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "calibration":
            return
        mode.mark_return()

    def calibration_on_final(self):
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "calibration":
            return
        self._cal_return_stop_ts = local_clock()
        mode.mark_final()

    def calibration_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._udp_send(cur, 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        avg_speed = self._compute_calibration_return_speed()
        self._write_calibration_summary(avg_speed)
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        if avg_speed is None:
            self.cal_phase_var.set("saved — average return speed unavailable")
        else:
            self.cal_phase_var.set(f"saved — average return speed = {avg_speed:.2f} deg/s")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.cal_btn_start.config(state="normal")
        self.cal_btn_return.config(state="disabled")
        self.cal_btn_final.config(state="disabled")
        self.cal_btn_stop.config(state="disabled")
        self._trial_increment()

    def _calibration_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "calibration":
            self.after(interval_ms, self._calibration_ui_loop)
            return
        rows = self.recorder.get_rows()
        self.rec_status_var.set(
            f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.cal_phase_var.set(mode.phase)
        if mode.sub_phase == "hold" and not self._cal_hold_marker_sent:
            self._cal_hold_marker_sent = True
            self.recorder.set_marker(2)
            self.udp.send(1, 2, self._udp_log_path, lambda: self.recorder.t0)
            self.cal_btn_return.config(state="normal")
            self.motion_status.set("CALIBRATION — hold at start angle, press Return when ready")
        self.after(interval_ms, self._calibration_ui_loop)

    def _compute_calibration_return_speed(self):
        try:
            if self._cal_return_start_ts is None or self._cal_return_stop_ts is None:
                return None
            frames = []
            for ts, s in self.serial_worker.ring.get_since(self._cal_return_start_ts - 1e-6):
                if ts < self._cal_return_start_ts:
                    continue
                if ts > self._cal_return_stop_ts:
                    break
                wrist = (s[6] - self.wrist_zero) / GEAR_RATIO
                frames.append((ts, wrist))
            if len(frames) < 2:
                return None
            t0, w0 = frames[0]; t1, w1 = frames[-1]
            dt = t1 - t0
            if dt <= 1e-6:
                return None
            path = sum(abs(frames[i][1] - frames[i-1][1]) for i in range(1, len(frames)))
            return path / dt
        except Exception:
            return None

    def _write_calibration_summary(self, avg_speed_deg_s):
        try:
            with open(self._udp_log_path, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 44 + "\n")
                f.write("Calibration Summary\n")
                f.write(f"start_angle_deg:              {self._parse_float(self.cal_start_angle_var, 0.0):.3f}\n")
                if self._cal_return_start_ts is not None and self._cal_return_stop_ts is not None:
                    f.write(f"return_duration_s:            {self._cal_return_stop_ts - self._cal_return_start_ts:.6f}\n")
                if avg_speed_deg_s is None:
                    f.write("average_return_speed_deg_s:   NA\n")
                else:
                    f.write(f"average_return_speed_deg_s:   {avg_speed_deg_s:.6f}\n")
                f.write("=" * 44 + "\n")
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════════
    #  ROM handlers
    # ══════════════════════════════════════════════════════════════════════════
    def rom_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
                return
        direction     = self._get_direction()
        damper        = int(self.calib_damper_var.get())
        passive_speed = self._parse_float(self.rom_passive_speed_var, 20.0)
        h             = 1 if self.handedness_var.get() == "Right" else -1
        try:
            hold_time  = float(self.rom_hold_time_var.get())
            rest_time  = float(self.rom_rest_time_var.get())
            total_reps = int(self.rom_reps_var.get())
        except ValueError:
            hold_time = 3.0; rest_time = 3.0; total_reps = 1

        mode = ROMMode(
            direction=direction, damper_pwm=damper,
            passive_speed_deg_s=passive_speed,
            hold_time_s=hold_time, rest_time_s=rest_time,
            total_reps=total_reps,
        )
        mode.set_handedness(h)
        mode.reset()

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        dir_str  = "ext" if direction >= 0 else "flex"
        stem     = f"ROM_{hand_str}_{dir_str}_{ts}_{self._trial_str()}"
        self._make_file_paths("rom", stem)
        self._write_udp_header({"experiment":           "rom_assessment",
                                 "direction":           "extension" if direction >= 0 else "flexion",
                                 "passive_speed_deg_s": passive_speed,
                                 "damper_pwm":          damper,
                                 "hold_time_s":         hold_time,
                                 "rest_time_s":         rest_time,
                                 "total_reps":          total_reps})

        self.calib_btn_start.config(state="disabled")
        self.calib_btn_stop.config(state="disabled")
        self.calib_btn_active_end.config(state="disabled")
        self.calib_btn_passive_end.config(state="disabled")
        self.motion_status.set("ROM — starting in 3…")

        def _launch():
            self.motion_runner.start(mode)
            self.serial_worker.send("TORQUE_OFF")
            self.recorder.start(self._csv_path, mode)
            self.recorder.set_marker(1)
            get_t0 = lambda: self.recorder.t0
            self.udp.start_listen(self._udp_log_path, get_t0)
            self._udp_send(0, 1)

            def _on_phase(prev_sp, new_sp):
                prev_m = ROMMode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m  = ROMMode.PHASE_TO_MARKER.get(new_sp, 0)
                self.recorder.set_marker(new_m)
                if prev_m != new_m:
                    self.udp.send(prev_m, new_m, self._udp_log_path, get_t0)
                self.after(0, lambda s=new_sp: self._rom_on_phase_ui(s))

            mode.on_phase_change = _on_phase
            self.motion_status.set("ROM — active phase, subject moves wrist")
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.calib_btn_stop.config(state="normal")
            self.calib_btn_active_end.config(state="normal")
            self._motion_polling = True
            self._rom_ui_loop()

        self.display.countdown(_launch)

    def _rom_on_phase_ui(self, phase: str):
        mode = self.motion_runner.active_mode
        damper = int(round(mode.params.get("damper_pwm", 0))) if mode else 0
        if mode:
            self.rom_info_var.set(mode.phase)

        status_map = {
            "active":     "ROM — active phase, subject moves wrist",
            "passive":    "ROM — passive phase, servo driving",
            "hold":       "ROM — holding at passive end",
            "servo_back": "ROM — servo returning hand to 0°",
            "rest":       "ROM — resting before next rep",
            "done":       "ROM — complete",
        }
        self.motion_status.set(status_map.get(phase, f"ROM — {phase}"))

        self.calib_btn_active_end.config(
            state="normal" if phase == "active" else "disabled")
        self.calib_btn_passive_end.config(
            state="normal" if phase == "passive" else "disabled")

        if phase == "passive":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "hold":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "servo_back":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "rest":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "active":
            self.serial_worker.send("TORQUE_OFF")
            self.serial_worker.send(f"SET_DMP:{damper}")
        if phase == "done":
            self.after(200, self.rom_on_stop)

    def rom_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._udp_send(cur, 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.rom_info_var.set("—")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.calib_btn_start.config(state="normal")
        self.calib_btn_stop.config(state="disabled")
        self.calib_btn_active_end.config(state="disabled")
        self.calib_btn_passive_end.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

    def rom_on_mark_active_end(self):
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "rom_assessment":
            return
        wrist = self._get_wrist()
        mode.mark_active_end(wrist)

    def rom_on_mark_passive_end(self):
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "rom_assessment":
            return
        wrist = self._get_wrist()
        mode.mark_passive_end(wrist)

    def _rom_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None:
            self.after(interval_ms, self._rom_ui_loop)
            return

        wrist = self._get_wrist()
        d     = int(round(mode.params.get("direction", 1)))
        self._draw_rom_canvas(wrist, mode.sub_phase,
                              mode.active_rom, mode.passive_rom, d)

        if mode.sub_phase in ("hold", "servo_back", "rest") \
                and not mode.txt_written and mode.passive_rom is not None:
            mode.txt_written = True
            self._write_rom_report(mode)

        total    = int(round(mode.params.get("total_reps", 1)))
        hold_dur = float(mode.params.get("hold_time_s", 3.0))
        rest_dur = float(mode.params.get("rest_time_s", 3.0))
        if mode.sub_phase == "hold":
            remaining = max(0.0, hold_dur - mode.hold_t)
            self.motion_status.set(
                f"ROM — hold {remaining:.1f}s remaining  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "rest":
            remaining = max(0.0, rest_dur - mode.rest_t)
            self.motion_status.set(
                f"ROM — rest {remaining:.1f}s remaining  |  rep {mode.rep+1}/{total}")

        rows = self.recorder.get_rows()
        self.rec_status_var.set(
            f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.after(interval_ms, self._rom_ui_loop)

# ══════════════════════════════════════════════════════════════════════════
    #  AAN handlers
    # ══════════════════════════════════════════════════════════════════════════
    def aan_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
                return
        direction   = self._get_direction()
        active_end  = self._parse_float(self.aan_active_end_var,  30.0)
        passive_end = self._parse_float(self.aan_passive_end_var, 50.0)
        speed       = self._parse_float(self.aan_speed_var,       20.0)
        timeout     = self._parse_float(self.aan_timeout_var,      5.0)
        pause       = self._parse_float(self.aan_pause_var,        0.0)
        damper      = int(self.aan_damper_var.get())
        h           = 1 if self.handedness_var.get() == "Right" else -1
        damper_ret  = int(self.aan_damper_return_var.get())
        try:
            hold_time   = float(self.aan_hold_time_var.get())
            return_time = float(self.aan_return_time_var.get())
            rest_time   = float(self.aan_rest_time_var.get())
            total_reps  = int(self.aan_reps_var.get())
        except ValueError:
            hold_time = 3.0; return_time = 3.0; rest_time = 3.0; total_reps = 1

        if active_end <= 0 or passive_end <= 0:
            messagebox.showerror("Error", "Active/Passive end must be > 0.")
            return
        if speed <= 0:
            messagebox.showerror("Error", "Passive drive speed must be > 0.")
            return
        if active_end >= passive_end:
            messagebox.showwarning("Warning",
                f"Active end ({active_end}°) ≥ passive end ({passive_end}°).\n"
                "Servo will have nothing to drive to — check values.")

        mode = AANMode(
            direction=direction, active_end=active_end,
            passive_end=passive_end, speed_deg_s=speed,
            active_timeout_s=timeout, pre_passive_pause_s=pause,
            damper_pwm=damper, damper_on_return=damper_ret,
            hold_time_s=hold_time, return_time_s=return_time,
            rest_time_s=rest_time, total_reps=total_reps,
        )
        mode.set_handedness(h)
        mode.reset()

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        dir_str  = "ext" if direction >= 0 else "flex"
        stem     = f"AAN_{hand_str}_{dir_str}_{ts}_{self._trial_str()}"
        self._make_file_paths("aan", stem)
        self._write_udp_header({"experiment":           "aan",
                                 "direction":           "extension" if direction >= 0 else "flexion",
                                 "active_end_deg":      active_end,
                                 "passive_end_deg":     passive_end,
                                 "passive_speed_deg_s": speed,
                                 "active_timeout_s":    timeout,
                                 "pre_passive_pause_s": pause,
                                 "damper_pwm":          damper,
                                 "damper_on_return":    damper_ret,
                                 "hold_time_s":         hold_time,
                                 "return_time_s":       return_time,
                                 "rest_time_s":         rest_time,
                                 "total_reps":          total_reps})

        self.aan_btn_start.config(state="disabled")
        self.aan_btn_stop.config(state="disabled")
        self.motion_status.set("AAN — starting in 3…")

        def _launch():
            self.recorder.start(self._csv_path, mode)
            self.recorder.set_marker(1)
            get_t0 = lambda: self.recorder.t0
            self.udp.start_listen(self._udp_log_path, get_t0)
            self.udp.send(0, 1, self._udp_log_path, get_t0)

            def _on_phase(prev_sp, new_sp):
                prev_m = AANMode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m  = AANMode.PHASE_TO_MARKER.get(new_sp, 0)
                self.recorder.set_marker(new_m)
                if prev_m != new_m:
                    self.udp.send(prev_m, new_m, self._udp_log_path, get_t0)
                self.after(0, lambda s=new_sp: self._aan_on_phase_ui(
                    s, passive_end, speed, pause, return_time, damper_ret))

            self.motion_runner.start(mode)
            mode.on_phase_change = _on_phase
            self.serial_worker.send("TORQUE_OFF")
            self.motion_status.set("AAN — active phase, subject moves toward active end")
            self.aan_phase_var.set(f"active | A-end={active_end}° P-end={passive_end}°")
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.aan_btn_stop.config(state="normal")
            self._motion_polling = True
            self._aan_ui_loop()

        self.display.countdown(_launch)

    def _aan_on_phase_ui(self, phase: str, passive_end: float, speed: float,
                         pause: float, return_time: float, damper_ret: int):
        mode = self.motion_runner.active_mode
        damper = int(round(mode.params.get("damper_pwm", 0))) if mode else 0
        self.aan_phase_var.set(mode.phase if mode else "—")

        status_map = {
            "pre_passive": f"AAN — pause {pause:.1f}s before passive",
            "passive":     f"AAN — PASSIVE, servo driving to {passive_end}° at {speed}°/s",
            "hold":        "AAN — HOLD at passive end",
            "return":      f"AAN — RETURN window {return_time:.1f}s, damper {'ON' if damper_ret else 'OFF'}",
            "servo_back":  "AAN — servo returning hand to 0°",
            "rest":        "AAN — resting before next rep",
            "done":        "AAN — complete",
        }
        self.motion_status.set(status_map.get(phase, f"AAN — {phase}"))

        if phase == "active":
            damper = int(round(mode.params.get("damper_pwm", 0))) if mode else 0
            self.serial_worker.send("TORQUE_OFF")
            self.serial_worker.send(f"SET_DMP:{damper}")
        if phase in ("pre_passive",) and mode and mode.timed_out:
            self.serial_worker.send("TORQUE_ON")
        if phase == "passive":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "hold":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "return":
            self.serial_worker.send("TORQUE_OFF")
            dmp_val = 255 if damper_ret else 0
            self.serial_worker.send(f"SET_DMP:{dmp_val}")
        if phase == "servo_back":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "rest":
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:0")
        if phase == "done":
            self.after(200, self.aan_on_stop)



    def aan_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._udp_send(cur, 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.aan_phase_var.set("—")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.aan_btn_start.config(state="normal")
        self.aan_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

    def _aan_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "aan":
            self.after(interval_ms, self._aan_ui_loop)
            return

        wrist = self._get_wrist()
        ae    = abs(self._parse_float(self.aan_active_end_var,  30.0))
        pe    = abs(self._parse_float(self.aan_passive_end_var, 50.0))
        self._draw_real_canvas(mode, wrist)

        total       = int(round(mode.params.get("total_reps", 1)))
        timeout     = float(mode.params.get("active_timeout_s", 5.0))
        hold_dur    = float(mode.params.get("hold_time_s",      3.0))
        return_dur  = float(mode.params.get("return_time_s",    3.0))
        rest_dur    = float(mode.params.get("rest_time_s",      3.0))

        if mode.sub_phase == "active":
            remaining = max(0.0, timeout - mode.active_t)
            self.aan_phase_var.set(
                f"ACTIVE — move to {ae}°, timeout in {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "pre_passive":
            pause_dur = float(mode.params.get("pre_passive_pause_s", 0.0))
            remaining = max(0.0, pause_dur - mode.pause_t)
            self.aan_phase_var.set(
                f"PRE-PASSIVE — {'servo holding' if mode.timed_out else 'waiting'} {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "hold":
            remaining = max(0.0, hold_dur - mode.hold_t)
            self.aan_phase_var.set(
                f"HOLD {remaining:.1f}s remaining  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "return":
            remaining = max(0.0, return_dur - mode.return_t)
            self.aan_phase_var.set(
                f"RETURN — {remaining:.1f}s until servo takes over  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "rest":
            remaining = max(0.0, rest_dur - mode.rest_t)
            self.aan_phase_var.set(
                f"REST {remaining:.1f}s remaining  |  rep {mode.rep+1}/{total}")
        else:
            self.aan_phase_var.set(mode.phase)

        rows = self.recorder.get_rows()
        self.rec_status_var.set(
            f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.after(interval_ms, self._aan_ui_loop)

# ══════════════════════════════════════════════════════════════════════════
    #  Passive Movement handlers
    # ══════════════════════════════════════════════════════════════════════════
    def pm_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
                return
        try:
            direction = int(self._get_direction())
        except ValueError:
            direction = 1
        target     = self._parse_float(self.pm_target_var,     40.0)
        active_end = self._parse_float(self.pm_active_end_var, 20.0)
        speed      = self._parse_float(self.pm_speed_var,      20.0)
        hold       = self._parse_float(self.pm_hold_var,        2.0)
        rest       = self._parse_float(self.pm_rest_var,        2.0)
        use_decel  = int(self.pm_decel_var.get())
        damper     = int(self.pm_damper_var.get())
        h          = 1 if self.handedness_var.get() == "Right" else -1
        try:
            total_reps = int(self.pm_reps_var.get())
        except ValueError:
            total_reps = 1

        if target <= 0:
            messagebox.showerror("Error", "Target must be > 0.")
            return
        if speed <= 0:
            messagebox.showerror("Error", "Speed must be > 0.")
            return

        mode = PassiveMovementMode(direction=direction, target_deg=target,
                                   active_end_deg=active_end,
                                   speed_deg_s=speed, hold_duration_s=hold,
                                   rest_time_s=rest, use_decel=use_decel,
                                   damper_pwm=damper, total_reps=total_reps)
        mode.set_handedness(h)
        mode.reset()

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        dir_str  = "ext" if direction >= 0 else "flex"
        stem     = f"PM_{hand_str}_{dir_str}_{ts}_{self._trial_str()}"
        self._make_file_paths("passive_movement", stem)
        self._write_udp_header({"experiment":        "passive_movement",
                                 "direction":        "extension" if direction >= 0 else "flexion",
                                 "target_wrist_deg": target,
                                 "active_end_deg":   active_end,
                                 "speed_wrist_deg_s": speed,
                                 "hold_duration_s":  hold,
                                 "rest_time_s":      rest,
                                 "damper_pwm":       damper,
                                 "total_reps":       total_reps})

        self.pm_btn_start.config(state="disabled")
        self.pm_btn_stop.config(state="disabled")
        self.motion_status.set("PASSIVE MOVE — starting in 3…")

        def _launch():
            get_t0 = lambda: self.recorder.t0

            def _on_phase(prev_sp, new_sp):
                prev_m = mode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m  = mode.PHASE_TO_MARKER.get(new_sp, 0)
                # set float marker in CSV
                float_marker = {
                    "go":     1.0,
                    "hold":   2.0,
                    "return": 2.5,
                    "rest":   0.0,
                    "done":   0.0,
                }.get(new_sp, float(new_m))
                self.recorder.set_marker(float_marker)
                if prev_m != new_m:
                    self.udp.send(prev_m, new_m, self._udp_log_path, get_t0)
                total = int(round(mode.params.get("total_reps", 1)))
                self.after(0, lambda s=new_sp: self.pm_phase_var.set(
                    f"phase={s}  |  rep={mode.rep+1}/{total}  target={target}°"))

            def _on_active_reached(direction_str: str):
                if direction_str == "go":
                    # CSV: 1.0 → 1.5
                    self.recorder.set_marker(1.5)
                    # UDP: 1→1 (event marker, same value)
                    self.udp.send(1, 1, self._udp_log_path, get_t0)
                else:  # return
                    # CSV: 2.5 → 3.0
                    self.recorder.set_marker(3.0)
                    # UDP: 3→3 (event marker)
                    self.udp.send(3, 3, self._udp_log_path, get_t0)

            self.recorder.start(self._csv_path, mode)
            self.udp.start_listen(self._udp_log_path, get_t0)
            self.motion_runner.start(mode)
            mode.on_phase_change   = _on_phase
            mode.on_active_reached = _on_active_reached
            self.serial_worker.send("TORQUE_ON")
            wrist_now = self._get_wrist()
            mode.start_go(wrist_now)
            self.motion_status.set(f"PASSIVE MOVE — driving to {target}° at {speed}°/s")
            self.pm_phase_var.set(f"phase=go  |  rep=1/{total_reps}  target={target}°")
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.pm_btn_stop.config(state="normal")
            self._motion_polling = True
            self._pm_ui_loop()

        self.display.countdown(_launch)

    def pm_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._udp_send(int(cur), 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.pm_phase_var.set("—")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.pm_btn_start.config(state="normal")
        self.pm_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

    def _pm_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "passive_movement":
            self.after(interval_ms, self._pm_ui_loop)
            return
        wrist = self._get_wrist()
        if mode.done:
            self.after(200, self.pm_on_stop)
            return
        total    = int(round(mode.params.get("total_reps", 1)))
        hold_dur = float(mode.params.get("hold_duration_s", 2.0))
        rest_dur = float(mode.params.get("rest_time_s",     2.0))
        if mode.sub_phase == "hold":
            remaining = max(0.0, hold_dur - mode.hold_t)
            self.motion_status.set(
                f"PASSIVE MOVE — hold {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "rest":
            remaining = max(0.0, rest_dur - mode.rest_t)
            self.motion_status.set(
                f"PASSIVE MOVE — rest {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        rows = self.recorder.get_rows()
        self.rec_status_var.set(
            f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self._draw_pm_canvas(mode, wrist)
        self.after(interval_ms, self._pm_ui_loop)
    
    # # ══════════════════════════════════════════════════════════════════════════
    # #  Back and Forth handlers
    # # ══════════════════════════════════════════════════════════════════════════
    # def bf_on_start(self):
    #     if not self.serial_worker.connected:
    #         messagebox.showerror("Error", "Connect Serial first.")
    #         return
    #     if not self.lc_worker.connected:
    #         if not messagebox.askyesno("LC not connected",
    #                 "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
    #             return
    #     try:
    #         left   = float(self.bf_left_var.get())
    #         right  = float(self.bf_right_var.get())
    #         speed  = float(self.bf_speed_var.get())
    #         reps   = int(self.bf_reps_var.get())
    #         damper = int(self.bf_damper_var.get())
    #     except ValueError:
    #         messagebox.showerror("Error", "Invalid parameter values.")
    #         return
    #     if left >= right:
    #         messagebox.showerror("Error", "Left boundary must be < right boundary.")
    #         return
    #     if speed <= 0 or reps <= 0:
    #         messagebox.showerror("Error", "Speed and reps must be > 0.")
    #         return
    #     h = 1 if self.handedness_var.get() == "Right" else -1
 
    #     mode = BackAndForthMode(left_deg=left, right_deg=right,
    #                             speed_deg_s=speed, total_reps=reps, damper_pwm=damper)
    #     mode.set_handedness(h)
    #     mode.reset()
 
    #     ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    #     hand_str = "R" if h == 1 else "L"
    #     stem     = f"BF_{hand_str}_{ts}_{self._trial_str()}"
    #     self._make_file_paths("back_and_forth", stem)
    #     self._write_udp_header({"experiment": "back_and_forth",
    #                              "left_deg": left, "right_deg": right,
    #                              "speed_deg_s": speed, "reps": reps,
    #                              "damper_pwm": damper})
    #     self.bf_btn_start.config(state="disabled")
    #     self.bf_btn_stop.config(state="disabled")
    #     self.motion_status.set("BACK AND FORTH — starting in 3…")
 
    #     def _launch():
    #         get_t0 = lambda: self.recorder.t0
    #         self.recorder.start(self._csv_path, mode)
    #         self.udp.start_listen(self._udp_log_path, get_t0)
    #         self.recorder.set_marker(1)
    #         self.udp.send(0, 1, self._udp_log_path, get_t0)
    #         self.motion_runner.start(mode)
    #         self.serial_worker.send("TORQUE_ON")
    #         wrist_now = self._get_wrist()
    #         mode.start_moving(wrist_now)
    #         self.motion_status.set(f"BACK AND FORTH — {left}° ↔ {right}° at {speed}°/s  ({reps} reps)")
    #         self.bf_phase_var.set(f"moving | 0/{reps} reps")
    #         self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
    #         self.bf_btn_stop.config(state="normal")
    #         self._motion_polling = True
    #         self._bf_ui_loop()
 
    #     self.display.countdown(_launch)

    # def bf_on_stop(self):
    #     self._motion_polling = False
    #     self._udp_send(1, 0)
    #     rows = self.recorder.get_rows()
    #     self.recorder.stop()
    #     self._copy_to_subdir()
    #     try:
    #         self.motion_runner.stop(release=False)
    #     except Exception:
    #         pass
    #     self.serial_worker.send("TORQUE_OFF")
    #     self.serial_worker.send("SET_DMP:0")
    #     self.motion_status.set("Idle")
    #     self.bf_phase_var.set("—")
    #     self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
    #     self.udp.stop_listen()
    #     self.bf_btn_start.config(state="normal")
    #     self.bf_btn_stop.config(state="disabled")
    #     self._draw_canvas_idle()
    #     self._trial_increment()

    # def _bf_ui_loop(self, interval_ms=100):
    #     if not self._motion_polling:
    #         return
    #     mode = self.motion_runner.active_mode
    #     if mode is None or mode.name != "back_and_forth":
    #         self.after(interval_ms, self._bf_ui_loop)
    #         return
    #     wrist = self._get_wrist()
    #     total = int(round(mode.params.get("total_reps", 0)))
    #     self.bf_phase_var.set(
    #         f"{mode.sub_phase} | {mode.reps}/{total} reps | wrist={wrist:.1f}°")
    #     if mode.done:
    #         self.after(200, self.bf_on_stop)
    #         return
    #     self._draw_bf_canvas(mode, wrist)
    #     rows = self.recorder.get_rows()
    #     self.rec_status_var.set(
    #         f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)")
    #     self.after(interval_ms, self._bf_ui_loop)

# ══════════════════════════════════════════════════════════════════════════
    #  RAH handlers
    # ══════════════════════════════════════════════════════════════════════════
    def rah_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC data will be empty.\nProceed anyway?"):
                return
        direction = self._get_direction()
        limit     = self._parse_float(self.rah_limit_var, 10.0)
        channel   = self.rah_channel_var.get()
        h         = 1 if self.handedness_var.get() == "Right" else -1

        mode = RAHMode(direction=direction, torque_limit=limit)
        mode.set_handedness(h)
        mode.reset()

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        dir_str  = "ext" if direction >= 0 else "flex"
        stem     = f"RAH_{hand_str}_{dir_str}_{ts}_{self._trial_str()}"
        self._make_file_paths("ramp_and_hold", stem)

        self.rah_btn_start.config(state="disabled")
        self.rah_btn_stop.config(state="disabled")
        self.motion_status.set("RAH — starting in 3…")

        def _launch():
            wrist_now = self._get_wrist()
            self._write_udp_header({"experiment":    "ramp_and_hold",
                                     "direction":    "extension" if direction >= 0 else "flexion",
                                     "hold_angle_deg": f"{wrist_now:.3f}",
                                     "torque_limit": limit,
                                     "lc_channel":   channel})
            get_t0 = lambda: self.recorder.t0
            self.recorder.start(self._csv_path, mode)
            self.udp.start_listen(self._udp_log_path, get_t0)
            self.recorder.set_marker(1)
            self.udp.send(0, 1, self._udp_log_path, get_t0)
            self._rah_t0      = time.time()
            self._rah_history = []
            self.motion_runner.start(mode)
            mode.start(wrist_now)
            self.serial_worker.send("TORQUE_ON")
            self.motion_status.set(
                f"RAH — holding at {wrist_now:.1f}° | channel={channel} | limit={limit}")
            self.rah_phase_var.set(
                f"active | hold={wrist_now:.1f}° | {channel} limit={limit}")
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.rah_btn_stop.config(state="normal")
            self._motion_polling = True
            self._rah_ui_loop()

        self.display.countdown(_launch)

    def rah_on_stop(self):
        self._motion_polling = False
        self._udp_send(1, 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.rah_phase_var.set("—")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.rah_btn_start.config(state="normal")
        self.rah_btn_stop.config(state="disabled")
        self._draw_rah_canvas_idle()
        self._trial_increment()

    def _rah_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "ramp_and_hold":
            self.after(interval_ms, self._rah_ui_loop)
            return
        lc_sample, _ = self.lc_worker.ring.get_latest()
        t_elapsed    = time.time() - self._rah_t0 if self._rah_t0 else 0.0
        self._draw_rah_live(lc_sample, t_elapsed)
        rows = self.recorder.get_rows()
        self.rec_status_var.set(
            f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)  t={t_elapsed:.1f}s")
        self.after(interval_ms, self._rah_ui_loop)

# ══════════════════════════════════════════════════════════════════════════
    #  Active Movement handlers
    # ══════════════════════════════════════════════════════════════════════════
    def am_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Serial first.")
            return
        if not self.lc_worker.connected:
            if not messagebox.askyesno("LC not connected",
                    "Load Cell is not connected — LC columns will be empty.\nProceed anyway?"):
                return
        try:
            direction = int(self._get_direction())
        except ValueError:
            direction = 1
        damper     = int(self.am_damper_var.get())
        return_tol = self._parse_float(self.am_return_tol_var, 5.0)
        rest_time  = self._parse_float(self.am_rest_var,        2.0)
        h          = 1 if self.handedness_var.get() == "Right" else -1
        try:
            total_reps = int(self.am_reps_var.get())
        except ValueError:
            total_reps = 1

        mode = ActiveMovementMode(direction=direction, damper_pwm=damper,
                                  return_tol_deg=return_tol,
                                  rest_time_s=rest_time, total_reps=total_reps)
        mode.set_handedness(h)
        mode.reset()

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        dir_str  = "ext" if direction >= 0 else "flex"
        stem     = f"AM_{hand_str}_{dir_str}_{ts}_{self._trial_str()}"
        self._make_file_paths("active_movement", stem)
        self._write_udp_header({"experiment":    "active_movement",
                                 "direction":    "extension" if direction >= 0 else "flexion",
                                 "damper_pwm":   damper,
                                 "return_tol_deg": return_tol,
                                 "rest_time_s":  rest_time,
                                 "total_reps":   total_reps})

        self.am_btn_start.config(state="disabled")
        self.am_btn_end.config(state="disabled")
        self.am_btn_stop.config(state="disabled")
        self.motion_status.set("ACTIVE MOVE — starting in 3…")

        def _launch():
            get_t0 = lambda: self.recorder.t0

            def _on_phase(prev_sp, new_sp):
                prev_m = ActiveMovementMode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m  = ActiveMovementMode.PHASE_TO_MARKER.get(new_sp, 0)
                self.recorder.set_marker(new_m)
                if prev_m != new_m:
                    self.udp.send(prev_m, new_m, self._udp_log_path, get_t0)
                total = int(round(mode.params.get("total_reps", 1)))
                self.after(0, lambda s=new_sp: (
                    self.am_phase_var.set(
                        f"phase={s}  |  rep={mode.rep+1}/{total}"),
                    self.am_btn_end.config(
                        state="normal" if s == "go" else "disabled"),
                ))
                if new_sp == "done":
                    self.after(200, self.am_on_stop)

            self.recorder.start(self._csv_path, mode)
            self.udp.start_listen(self._udp_log_path, get_t0)
            self.motion_runner.start(mode)
            mode.on_phase_change = _on_phase
            self.serial_worker.send("TORQUE_OFF")
            self.recorder.set_marker(1)
            # self.udp.send(0, 1, self._udp_log_path, get_t0)
            mode.start_go()
            self.motion_status.set(f"ACTIVE MOVE — move in {'extension' if direction >= 0 else 'flexion'} direction")
            self.am_phase_var.set(f"phase=go  |  rep=1/{total_reps}")
            self.rec_status_var.set(f"Recording → {os.path.basename(self._csv_path)}")
            self.am_btn_end.config(state="normal")
            self.am_btn_stop.config(state="normal")
            self._motion_polling = True
            self._am_ui_loop()

        self.display.countdown(_launch)

    def am_on_end(self):
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "active_movement":
            return
        mode.mark_end()

    def am_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._udp_send(int(cur), 0)
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.am_phase_var.set("—")
        self.rec_status_var.set(f"Saved: {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.udp.stop_listen()
        self.am_btn_start.config(state="normal")
        self.am_btn_end.config(state="disabled")
        self.am_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

    def _am_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "active_movement":
            self.after(interval_ms, self._am_ui_loop)
            return
        wrist    = self._get_wrist()
        total    = int(round(mode.params.get("total_reps", 1)))
        rest_dur = float(mode.params.get("rest_time_s", 2.0))
        if mode.sub_phase == "rest":
            remaining = max(0.0, rest_dur - mode.rest_t)
            self.motion_status.set(
                f"ACTIVE MOVE — rest {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "return":
            self.motion_status.set(
                f"ACTIVE MOVE — return to 0°  |  wrist={wrist:.1f}°  |  rep {mode.rep+1}/{total}")
        rows = self.recorder.get_rows()
        self.rec_status_var.set(
            f"Recording → {os.path.basename(self._csv_path)}  ({rows:,} rows)")
        self.after(interval_ms, self._am_ui_loop)

    # ══════════════════════════════════════════════════════════════════════════
    #  Canvas drawing
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_canvas_idle(self):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        c.create_text(W // 2, H // 2, text="Press Start to begin",
                      fill="#334", font=("Segoe UI", 11))
        
    def _draw_rom_canvas(self, wrist, sub_phase, active_rom, passive_rom, direction):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        BOUND = WRIST_LIMIT_DEG; r_flex = -BOUND; span = 2 * BOUND
        cy = H // 2; margin = 40
        h  = 1 if self.handedness_var.get() == "Right" else -1
        phys = direction * h
        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        c.create_line(margin, cy, W - margin, cy, fill="#444", width=3)
        for deg, label in [(-BOUND, f"-{BOUND:.0f}°"), (0, "0°"), (BOUND, f"+{BOUND:.0f}°")]:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#555", width=2)
            c.create_text(x, cy+22, text=label, fill="#555", font=("Segoe UI", 9))
        ext_x  = W - margin if h >= 0 else margin
        flex_x = margin     if h >= 0 else W - margin
        c.create_text(ext_x,  cy-22, text="EXT",  fill="#00e676", font=("Segoe UI", 8, "bold"))
        c.create_text(flex_x, cy-22, text="FLEX", fill="#ff9800", font=("Segoe UI", 8, "bold"))
        gx = _x(phys * BOUND)
        c.create_oval(gx-18, cy-18, gx+18, cy+18, fill="#00e676", outline="#00c853", width=3)
        if active_rom is not None:
            ax = _x(max(-BOUND, min(BOUND, phys * active_rom)))
            c.create_line(ax, cy-45, ax, cy+45, fill="#00bcd4", width=2, dash=(6,4))
            c.create_text(ax, cy-55, text=f"A={active_rom:.1f}°", fill="#00bcd4", font=("Segoe UI", 8))
        if passive_rom is not None:
            px = _x(max(-BOUND, min(BOUND, phys * passive_rom)))
            c.create_line(px, cy-45, px, cy+45, fill="#ff9800", width=2, dash=(6,4))
            c.create_text(px, cy-55, text=f"P={passive_rom:.1f}°", fill="#ff9800", font=("Segoe UI", 8))
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-15, cy-15, rx+15, cy+15, fill="#ff1744", outline="#d50000", width=3)
        colors = {"active": "#00e676", "passive": "#90caf9", "return": "#ffeb3b", "done": "#aaa"}
        col = colors.get(sub_phase, "#aaa")
        c.create_text(W//2, 14, text=sub_phase.upper(), fill=col, font=("Segoe UI", 11, "bold"))
        d_lbl = "EXTEND" if direction >= 0 else "FLEX"
        a_str = f"{active_rom:.1f}°"  if active_rom  is not None else "---"
        p_str = f"{passive_rom:.1f}°" if passive_rom is not None else "---"
        self.rom_info_var.set(
            f"{d_lbl}  |  active(subj)={a_str}  passive(servo)={p_str}  |  wrist={wrist:.1f}°")

    def _draw_real_canvas(self, mode, wrist):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        BOUND = WRIST_LIMIT_DEG; r_flex = -BOUND; span = 2 * BOUND
        cy = H // 2; margin = 40
        h  = 1 if self.handedness_var.get() == "Right" else -1
        direction = self._get_direction()
        phys = direction * h
        ae = abs(self._parse_float(self.aan_active_end_var,  30.0))
        pe = abs(self._parse_float(self.aan_passive_end_var, 50.0))
        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        c.create_line(margin, cy, W - margin, cy, fill="#444", width=3)
        for deg, label in [(-BOUND, f"-{BOUND:.0f}°"), (0, "0°"), (BOUND, f"+{BOUND:.0f}°")]:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#555", width=2)
            c.create_text(x, cy+22, text=label, fill="#555", font=("Segoe UI", 9))
        ext_x  = W - margin if h >= 0 else margin
        flex_x = margin     if h >= 0 else W - margin
        c.create_text(ext_x,  cy-22, text="EXT",  fill="#00e676", font=("Segoe UI", 8, "bold"))
        c.create_text(flex_x, cy-22, text="FLEX", fill="#ff9800", font=("Segoe UI", 8, "bold"))
        DOT_R  = 15
        ax_raw = _x(max(-BOUND, min(BOUND, phys * ae)))
        ax = ax_raw + (DOT_R if phys >= 0 else -DOT_R)
        c.create_line(ax, cy-55, ax, cy+55, fill="#00bcd4", width=2, dash=(6,4))
        c.create_text(ax, cy-65, text=f"A={ae:.1f}°", fill="#00bcd4", font=("Segoe UI", 8))
        px = _x(max(-BOUND, min(BOUND, phys * pe)))
        c.create_line(px, cy-55, px, cy+55, fill="#ff9800", width=2, dash=(6,4))
        c.create_text(px, cy-65, text=f"P={pe:.1f}°", fill="#ff9800", font=("Segoe UI", 8))
        if mode.sub_phase in ("passive", "hold") and mode.drive_pos:
            sx = _x(max(-BOUND, min(BOUND, mode.drive_pos)))
            c.create_oval(sx-12, cy-12, sx+12, cy+12, fill="#ffd700", outline="#f9a825", width=2)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-15, cy-15, rx+15, cy+15, fill="#ff1744", outline="#d50000", width=3)
        colors = {"active": "#00e676", "passive": "#ffd700", "hold": "#90caf9", "return": "#ffeb3b"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 14, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 11, "bold"))
        if mode.sub_phase == "passive" and mode.drive_speed:
            c.create_text(W//2, H - 10, text=f"carry speed: {mode.drive_speed:.1f}°/s",
                          fill="#ffd700", font=("Segoe UI", 8))
        self.rom_info_var.set(f"AAN  |  A-end={ae:.1f}°  P-end={pe:.1f}°  wrist={wrist:.1f}°")

    def _draw_pm_canvas(self, mode, wrist):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        BOUND = WRIST_LIMIT_DEG; r_flex = -BOUND; span = 2 * BOUND
        cy = H // 2; margin = 40
        h  = 1 if self.handedness_var.get() == "Right" else -1
        try:
            direction = int(self.pm_direction_var.get())
        except ValueError:
            direction = 1
        phys   = direction * h
        target = abs(self._parse_float(self.pm_target_var, 40.0))
        speed  = self._parse_float(self.pm_speed_var, 20.0)
        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        c.create_line(margin, cy, W - margin, cy, fill="#444", width=3)
        for deg, label in [(-BOUND, f"-{BOUND:.0f}°"), (0, "0°"), (BOUND, f"+{BOUND:.0f}°")]:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#555", width=2)
            c.create_text(x, cy+22, text=label, fill="#555", font=("Segoe UI", 9))
        ext_x  = W - margin if h >= 0 else margin
        flex_x = margin     if h >= 0 else W - margin
        c.create_text(ext_x,  cy-22, text="EXT",  fill="#00e676", font=("Segoe UI", 8, "bold"))
        c.create_text(flex_x, cy-22, text="FLEX", fill="#ff9800", font=("Segoe UI", 8, "bold"))
        tx = _x(max(-BOUND, min(BOUND, phys * target)))
        c.create_line(tx, cy-55, tx, cy+55, fill="#ffd700", width=2, dash=(6, 4))
        c.create_text(tx, cy-65, text=f"T={target:.1f}°", fill="#ffd700", font=("Segoe UI", 8))
        if mode.sub_phase in ("go", "hold", "return") and mode.drive_pos is not None:
            sx = _x(max(-BOUND, min(BOUND, mode.drive_pos)))
            c.create_oval(sx-12, cy-12, sx+12, cy+12, fill="#ffd700", outline="#f9a825", width=2)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-15, cy-15, rx+15, cy+15, fill="#ff1744", outline="#d50000", width=3)
        colors = {"idle": "#aaa", "go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b", "done": "#aaa"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 14, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 11, "bold"))
        c.create_text(W//2, H - 10, text=f"speed: {speed:.1f} wrist°/s", fill="#888", font=("Segoe UI", 8))
        self.rom_info_var.set(
            f"PASSIVE MOVE  |  target={target:.1f}°  speed={speed:.1f}°/s  wrist={wrist:.1f}°")

    def _draw_am_canvas(self, mode, wrist):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        BOUND = WRIST_LIMIT_DEG; r_flex = -BOUND; span = 2 * BOUND
        cy = H // 2; margin = 40
        h  = 1 if self.handedness_var.get() == "Right" else -1
        try:
            direction = int(self.am_direction_var.get())
        except ValueError:
            direction = 1
        phys   = direction * h
        target = abs(self._parse_float(self.am_target_var, 30.0))
        speed  = self._parse_float(self.am_speed_var, 20.0)
        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        c.create_line(margin, cy, W - margin, cy, fill="#444", width=3)
        for deg, label in [(-BOUND, f"-{BOUND:.0f}°"), (0, "0°"), (BOUND, f"+{BOUND:.0f}°")]:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#555", width=2)
            c.create_text(x, cy+22, text=label, fill="#555", font=("Segoe UI", 9))
        ext_x  = W - margin if h >= 0 else margin
        flex_x = margin     if h >= 0 else W - margin
        c.create_text(ext_x,  cy-22, text="EXT",  fill="#00e676", font=("Segoe UI", 8, "bold"))
        c.create_text(flex_x, cy-22, text="FLEX", fill="#ff9800", font=("Segoe UI", 8, "bold"))
        tx = _x(max(-BOUND, min(BOUND, phys * target)))
        c.create_line(tx, cy-55, tx, cy+55, fill="#888", width=1, dash=(4, 4))
        c.create_text(tx, cy-65, text=f"T={target:.1f}°", fill="#888", font=("Segoe UI", 8))
        gx = _x(max(-BOUND, min(BOUND, mode.green_pos)))
        c.create_oval(gx-16, cy-16, gx+16, cy+16, fill="#00e676", outline="#00c853", width=3)
        rx = _x(max(-BOUND, min(BOUND, wrist)))
        c.create_oval(rx-13, cy-13, rx+13, cy+13, fill="#ff1744", outline="#d50000", width=3)
        colors = {"idle": "#aaa", "guide": "#00e676", "hit": "#ffd700", "return": "#ffeb3b"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 14, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 11, "bold"))
        c.create_text(W//2, H - 10, text=f"guide speed: {speed:.1f} wrist°/s",
                      fill="#888", font=("Segoe UI", 8))
        self.rom_info_var.set(
            f"ACTIVE MOVE  |  target={target:.1f}°  guide={mode.green_pos:.1f}°  wrist={wrist:.1f}°")

    # def _draw_bf_canvas(self, mode, wrist):
    #     c = self.canvas
    #     W, H = int(c["width"]), int(c["height"])
    #     c.delete("all")
    #     left  = float(mode.params.get("left_deg",  -30.0))
    #     right = float(mode.params.get("right_deg",  30.0))
    #     span   = max(abs(right - left) * 1.2, 20.0)
    #     center = (left + right) / 2.0
    #     r_min  = center - span / 2
    #     margin = 40; cy = H // 2
    #     def _x(deg):
    #         return int(margin + (deg - r_min) / span * (W - 2 * margin))
    #     c.create_line(margin, cy, W - margin, cy, fill="#444", width=2)
    #     lx = _x(left)
    #     c.create_line(lx, cy-55, lx, cy+55, fill="#00bcd4", width=2, dash=(6, 4))
    #     c.create_text(lx, cy-65, text=f"L={left:.1f}°", fill="#00bcd4", font=("Segoe UI", 8))
    #     rx = _x(right)
    #     c.create_line(rx, cy-55, rx, cy+55, fill="#ff9800", width=2, dash=(6, 4))
    #     c.create_text(rx, cy-65, text=f"R={right:.1f}°", fill="#ff9800", font=("Segoe UI", 8))
    #     if mode.drive_pos is not None:
    #         sx = _x(max(r_min, min(r_min + span, mode.drive_pos)))
    #         c.create_oval(sx-12, cy-12, sx+12, cy+12, fill="#ffd700", outline="#f9a825", width=2)
    #     wx = _x(max(r_min, min(r_min + span, wrist)))
    #     c.create_oval(wx-14, cy-14, wx+14, cy+14, fill="#ff1744", outline="#d50000", width=3)
    #     total = int(round(mode.params.get("total_reps", 0)))
    #     c.create_text(W//2, 14, text=f"{mode.reps} / {total} reps",
    #                   fill="#ffd700", font=("Segoe UI", 12, "bold"))
    #     self.rom_info_var.set(
    #         f"BACK AND FORTH  |  L={left:.1f}°  R={right:.1f}°  "
    #         f"reps={mode.reps}/{total}  wrist={wrist:.1f}°")

    # RAH canvas
    _RAH_PROFILE = [
        (0, 0), (5, 0), (10, 1), (15, 1),
        (20, 0), (25, 0), (30, 1), (35, 1),
        (40, 0), (45, 0),
    ]
    _RAH_TOTAL_S        = 45.0
    _RAH_HISTORY_MAXLEN = 1000

    def _draw_rah_canvas_idle(self):
        c = self.rah_canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        c.create_text(W // 2, H // 2, text="Press Start to begin",
                      fill="#334", font=("Segoe UI", 10))

    def _lc_rah_value(self, sample, channel: str) -> float:
        if sample is None or len(sample) < 6:
            return 0.0
        if channel == "Fnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3)))
        if channel == "Tnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3, 6)))
        ch_idx = {"Fx": 0, "Fy": 1, "Fz": 2, "Tx": 3, "Ty": 4, "Tz": 5}
        return sample[ch_idx.get(channel, 0)]

    def _draw_rah_live(self, lc_sample, t_elapsed):
        c = self.rah_canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        channel   = self.rah_channel_var.get()
        limit     = max(0.01, self._parse_float(self.rah_limit_var, 10.0))
        direction = self._get_direction()
        h         = 1 if self.handedness_var.get() == "Right" else -1
        phys_dir  = direction * h
        upward    = phys_dir > 0
        ML, MR, MT, MB = 50, 10, 15, 30
        PW, PH = W - ML - MR, H - MT - MB
        WIN_S  = 15.0
        t_left  = t_elapsed - WIN_S / 2
        t_right = t_elapsed + WIN_S / 2
        def _xpx(t_abs):
            return int(ML + (t_abs - t_left) / WIN_S * PW)
        y_zero = MT + PH if upward else MT
        def _ypx(norm_val):
            frac = max(0.0, min(1.4, norm_val))
            return int(y_zero - frac / 1.4 * PH) if upward else int(y_zero + frac / 1.4 * PH)
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill="#0d1117", outline="#1e2a3a")
        c.create_line(ML, y_zero, ML + PW, y_zero, fill="#2a3a4a", width=2)
        unit = "Nm" if channel.startswith("T") else "N"
        for tick in [0.25, 0.5, 0.75, 1.0]:
            yg = _ypx(tick)
            c.create_line(ML, yg, ML + PW, yg, fill="#1a2535", width=1)
            c.create_text(ML - 4, yg, text=f"{tick * limit:.1f}", anchor="e",
                          fill="#445566", font=("Segoe UI", 7))
        c.create_text(12, MT + PH // 2, text=f"{channel}\n{unit}",
                      fill="#445566", font=("Segoe UI", 8), angle=90)
        tick_start = int(t_left // 5) * 5
        t_tick = tick_start
        while t_tick <= t_right + 0.01:
            x = _xpx(t_tick)
            if ML <= x <= ML + PW:
                c.create_line(x, MT + PH, x, MT + PH + 4, fill="#445566")
                c.create_text(x, MT + PH + 14, text=f"{int(t_tick)}s",
                              fill="#445566", font=("Segoe UI", 7))
            t_tick += 5
        CYCLE = self._RAH_TOTAL_S
        def _profile_y(t_abs):
            phase = t_abs % CYCLE
            pts = self._RAH_PROFILE
            for i in range(len(pts) - 1):
                t0, y0 = pts[i]; t1, y1 = pts[i + 1]
                if t0 <= phase <= t1:
                    return y0 if t1 == t0 else y0 + (y1 - y0) * (phase - t0) / (t1 - t0)
            return pts[-1][1]
        prof_coords = []
        t_s = t_left
        while t_s <= t_right + 0.01:
            prof_coords.extend([_xpx(t_s), _ypx(_profile_y(t_s))])
            t_s += 0.25
        if len(prof_coords) >= 4:
            c.create_line(*prof_coords, fill="#1565c0", width=2, dash=(8, 4))
        cycle_n = int(t_left // CYCLE)
        while True:
            t_boundary = (cycle_n + 1) * CYCLE
            if t_boundary > t_right + 0.01:
                break
            if t_left <= t_boundary <= t_right:
                xb = _xpx(t_boundary)
                c.create_line(xb, MT, xb, MT + PH, fill="#1e4a8a", width=1, dash=(4, 6))
                c.create_text(xb + 3, MT + 4, text=f"×{int(t_boundary // CYCLE)}",
                              anchor="nw", fill="#3a6aaa", font=("Segoe UI", 7))
            cycle_n += 1
        y_lim = _ypx(1.0)
        c.create_line(ML, y_lim, ML + PW, y_lim, fill="#ffd700", width=1, dash=(6, 4))
        c.create_text(ML + PW - 2, y_lim - 7, text=f"{limit:.1f}{unit}", anchor="e",
                      fill="#ffd700", font=("Segoe UI", 8, "bold"))
        x_now = _xpx(t_elapsed)
        c.create_line(x_now, MT, x_now, MT + PH, fill="#555", width=1, dash=(4, 4))
        if not hasattr(self, "_rah_history"):
            self._rah_history = []
        if lc_sample and len(lc_sample) >= 6:
            raw_val = self._lc_rah_value(lc_sample, channel)
            if channel in ("Fnorm", "Tnorm"):
                norm_val = abs(raw_val) / limit
            else:
                norm_val = max(0.0, raw_val * phys_dir / limit)
            self._rah_history.append((t_elapsed, norm_val))
            if len(self._rah_history) > self._RAH_HISTORY_MAXLEN:
                self._rah_history.pop(0)
        if len(self._rah_history) >= 2:
            coords = []
            for t_s, nv in self._rah_history:
                x = _xpx(t_s); y = _ypx(nv)
                if ML - 2 <= x <= ML + PW + 2:
                    coords.extend([x, y])
            if len(coords) >= 4:
                last_nv = self._rah_history[-1][1]
                lc_col  = "#ff4444" if last_nv > 1.0 else "#00e676"
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2]-5, coords[-1]-5, coords[-2]+5, coords[-1]+5,
                              fill=lc_col, outline="white", width=1)
        phase_s  = t_elapsed % CYCLE
        cycle_n2 = int(t_elapsed // CYCLE) + 1
        c.create_text(W // 2, 8,
                      text=f"t = {t_elapsed:.1f}s  |  cycle {cycle_n2}  |  phase {phase_s:.1f}s / {int(CYCLE)}s",
                      fill="#aaa", font=("Segoe UI", 9, "bold"))

    # LC Monitor canvas
    _LC_CHANNELS = {
        "Fx":    (0, True,  "N"),  "Fy":    (1, True,  "N"),  "Fz":    (2, True,  "N"),
        "Tx":    (3, True,  "Nm"), "Ty":    (4, True,  "Nm"), "Tz":    (5, True,  "Nm"),
        "Fnorm": (None, False, "N"), "Tnorm": (None, False, "Nm"),
    }

    def _lc_value(self, sample, channel: str) -> float:
        if channel == "Fnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3)))
        if channel == "Tnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3, 6)))
        return sample[self._LC_CHANNELS[channel][0]]

    def _draw_fx_canvas_empty(self):
        c = self.fx_canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        c.create_text(W // 2, H // 2, text="Waiting for LC data…",
                      fill="#334", font=("Segoe UI", 9))

    def _draw_fx_chart(self):
        c = self.fx_canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        ML, MR, MT, MB = 54, 10, 12, 22
        PW, PH = W - ML - MR, H - MT - MB
        try:
            baseline = float(self.fx_max_var.get())
            if baseline <= 0:
                baseline = 10.0
        except ValueError:
            baseline = 10.0
        channel  = self.lc_channel_var.get()
        _, signed, unit = self._LC_CHANNELS.get(channel, (0, False, "N"))
        if signed:
            y_min = -baseline * 1.4; y_max = baseline * 1.4
        else:
            y_min = 0.0; y_max = baseline * 1.4
        y_span = y_max - y_min or 1.0
        def _ypx(v):
            frac = (v - y_min) / y_span
            return int(MT + PH * (1.0 - max(0.0, min(1.0, frac))))
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill="#0d1117", outline="#1e2a3a")
        n_grid = 5 if signed else 4
        for i in range(n_grid + 1):
            val = y_min + (y_max - y_min) * i / n_grid
            y   = _ypx(val)
            c.create_line(ML, y, ML + PW, y, fill="#1a2535", width=1)
            c.create_text(ML - 4, y, text=f"{val:.1f}", anchor="e",
                          fill="#445566", font=("Segoe UI", 7))
        if signed:
            y0 = _ypx(0.0)
            c.create_line(ML, y0, ML + PW, y0, fill="#2a3a4a", width=1)
        c.create_text(10, MT + PH // 2, text=f"{channel} {unit}",
                      fill="#445566", font=("Segoe UI", 8), angle=90)
        WINDOW_S = 15.0
        now      = time.time()
        t_start  = now - WINDOW_S
        def _xpx(t):
            return int(ML + (t - t_start) / WINDOW_S * PW)
        for off in range(0, int(WINDOW_S) + 1, 5):
            x = _xpx(now - off)
            if ML <= x <= ML + PW:
                c.create_line(x, MT + PH, x, MT + PH + 4, fill="#445566")
                c.create_text(x, MT + PH + 12,
                              text=f"-{off}s" if off else "now",
                              fill="#445566", font=("Segoe UI", 7))
        if signed:
            for sign, anchor_offset in [(1, -7), (-1, 9)]:
                by = _ypx(sign * baseline)
                c.create_line(ML, by, ML + PW, by, fill="#ffd700", width=2, dash=(8, 4))
                c.create_text(ML + PW - 2, by + anchor_offset,
                              text=f"{'+' if sign>0 else ''}{sign*baseline:.1f}{unit}",
                              anchor="e", fill="#ffd700", font=("Segoe UI", 8, "bold"))
        else:
            by = _ypx(baseline)
            c.create_line(ML, by, ML + PW, by, fill="#ffd700", width=2, dash=(8, 4))
            c.create_text(ML + PW - 2, by - 7, text=f"{baseline:.1f}{unit}",
                          anchor="e", fill="#ffd700", font=("Segoe UI", 8, "bold"))
        pts = [(t, v) for t, v in self._fx_history if t >= t_start - 0.5]
        if len(pts) >= 2:
            coords = []
            for t, v in pts:
                x = _xpx(t); y = _ypx(v)
                if ML - 2 <= x <= ML + PW + 2:
                    coords.extend([x, y])
            if len(coords) >= 4:
                last_val = pts[-1][1]
                mode = self.motion_runner.active_mode
                is_aan = mode is not None and mode.name == "aan"
                if is_aan:
                    if signed:
                        lc_col = "#ff4444" if abs(last_val) <= baseline else "#00e676"
                    else:
                        lc_col = "#ff4444" if last_val <= baseline else "#00e676"
                else:
                    if signed:
                        lc_col = "#ff4444" if abs(last_val) > baseline else "#00e676"
                    else:
                        lc_col = "#ff4444" if last_val > baseline else "#00e676"
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2]-5, coords[-1]-5, coords[-2]+5, coords[-1]+5,
                              fill=lc_col, outline="white", width=1)
        c.create_line(ML + PW, MT, ML + PW, MT + PH, fill="#334455", width=1)

    def _fx_monitor_loop(self, interval_ms=100):
        current_channel = self.lc_channel_var.get()
        if getattr(self, "_fx_last_channel", None) != current_channel:
            self._fx_history.clear()
            self._fx_last_channel = current_channel
        lc_sample, _ = self.lc_worker.ring.get_latest()
        if lc_sample and len(lc_sample) >= 6:
            val = self._lc_value(lc_sample, current_channel)
            self._fx_history.append((time.time(), val))
            _, signed, unit = self._LC_CHANNELS.get(current_channel, (0, False, "N"))
            self.fx_live_var.set(f"{current_channel} = {val:.3f} {unit}")
            self._draw_fx_chart()
        else:
            if not self._fx_history:
                self._draw_fx_canvas_empty()
        self.after(interval_ms, self._fx_monitor_loop)

    # ══════════════════════════════════════════════════════════════════════════
    #  ROM report
    # ══════════════════════════════════════════════════════════════════════════
    def _write_rom_report(self, mode):
        direction = int(round(mode.params.get("direction", 1)))
        dir_str   = "extension" if direction >= 0 else "flexion"
        a, p      = mode.active_rom, mode.passive_rom
        lines = [
            "", "=" * 44,
            "Wrist Rig — Range of Motion Assessment",
            f"Date/Time  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Handedness : {self.handedness_var.get()}",
            f"Direction  : {dir_str.upper()}",
            "-" * 44,
            f"Active ROM  (subject): {a:.1f} °" if a is not None else "Active ROM  (subject): not recorded",
            f"Passive ROM (servo)  : {p:.1f} °" if p is not None else "Passive ROM (servo)  : not recorded",
            "=" * 44,
        ]
        try:
            with open(self._udp_log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  Close
    # ══════════════════════════════════════════════════════════════════════════
    def on_close(self):
        self._motion_polling = False
        try:
            self.recorder.stop()
        except Exception:
            pass
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        try:
            self.serial_worker.send("SET_DMP:0")
        except Exception:
            pass
        try:
            self.serial_worker.disconnect()
        except Exception:
            pass
        try:
            self.lc_worker.disconnect()
        except Exception:
            pass
        self.udp.close()
        self.destroy()


if __name__ == "__main__":
    use_mock = "--mock" in sys.argv
    app = ExpGUI(use_mock=use_mock)
    app.mainloop()
