"""
gui/app.py - Serial Experiment GUI
====================================
Entry point:
    python run.py            # real hardware
    python run.py --mock     # synthetic data, no hardware needed
"""

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
import csv
import math as _math
import os
import random
import re
import shutil
import threading
import time
import zipfile
from datetime import datetime
import xml.etree.ElementTree as ET

import tkinter as tk
from tkinter import ttk, messagebox

PRIMARY_CSV_RECORDING_ENABLED = False
RAH_DISPLAY_HOLD_LOWPASS_HZ = 1.0
RAH_DISPLAY_OTHER_LOWPASS_HZ = 1.0
GUI_LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "GUI_logs"))
GUI_LOG_TEMPLATE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "run_sheet_template.xlsx"))

from config import (
    GEAR_RATIO, WRIST_COMMAND_SIGN, WRIST_ENCODER_SIGN, WRIST_VELOCITY_COMMAND_SIGN, DATA_ROOT,
    SERIAL_COM_PORT, LC_COM_PORT, TRIGGER_COM_PORT,
    VICON_ENABLED, VICON_HOST, VICON_PORT, VICON_BIND_HOST, VICON_DATABASE_PATH,
)
from motions import (
    RestMode, PassiveMovementMode, FollowMode,
    RAHMode, VelocityTestMode, DirectionReversalMode, MotionRunner,
)
from recording.recorder import BaseCsvRecorder
from recording.raw_sync_recorder import RawSyncRecorder
from recording.trigger_timing_recorder import TriggerTimingRecorder
from hardware.vicon_worker import send_vicon_udp

from gui.display import DisplayWindow


MANUAL_VELOCITY_DIRECTION_SIGN = WRIST_VELOCITY_COMMAND_SIGN


class ExpGUI(tk.Tk):

    def __init__(self, use_mock: bool=False):
        super().__init__()
        self.title('Wrist Rig - Experiment')
        self.geometry('1280x900')
        self.minsize(1180, 760)
        self.resizable(True, True)
        self.use_mock = use_mock
        if use_mock:
            from hardware.mock_workers import MockSerialWorker, MockLCWorker
            from hardware.trigger_worker import TriggerWorker
            self.serial_worker = MockSerialWorker()
            self.lc_worker = MockLCWorker()
            self.trigger_worker = TriggerWorker()
        else:
            from hardware.serial_worker import SerialWorker
            from hardware.lc_worker import LCWorker
            from hardware.trigger_worker import TriggerWorker
            self.serial_worker = SerialWorker()
            self.lc_worker = LCWorker()
            self.trigger_worker = TriggerWorker()
        self.wrist_zero = 0.0
        self.imu_zero = 0.0
        self.motion_runner = MotionRunner(self.serial_worker, get_wrist_zero=lambda : self.wrist_zero, get_imu_zero=lambda : self.imu_zero, on_marker_pulse=self._on_motion_marker_pulse, reserve_marker_event_id=lambda : self.trigger_worker.reserve_event_id())
        self.recorder = BaseCsvRecorder(self.serial_worker, self.lc_worker, get_wrist_zero=lambda : self.wrist_zero, get_imu_zero=lambda : self.imu_zero)
        self.trigger_timing_recorder = TriggerTimingRecorder()
        self.raw_sync_recorder = RawSyncRecorder(get_wrist_zero=lambda : self.wrist_zero)
        for worker in (self.serial_worker, self.lc_worker):
            try:
                worker.set_raw_recorder(self.raw_sync_recorder)
            except Exception:
                pass
        self.handedness_var = tk.StringVar(value='Right')
        self.port_var = tk.StringVar(value=SERIAL_COM_PORT)
        self.lc_port_var = tk.StringVar(value=LC_COM_PORT)
        self.trigger_port_var = tk.StringVar(value=TRIGGER_COM_PORT)
        self.trial_var = tk.StringVar(value='1')
        self.serial_status = tk.StringVar(value='Serial: Disconnected')
        self.lc_status_var = tk.StringVar(value='LC: Disconnected')
        self.trigger_status_var = tk.StringVar(value='Trigger: Disconnected')
        self.angle_var = tk.StringVar(value='--- deg')
        self.imu_var = tk.StringVar(value='--- deg')
        self.current_var = tk.StringVar(value='--- mA')
        self.manual_vel_status_var = tk.StringVar(value='-')
        self.motion_status = tk.StringVar(value='Idle')
        self.rec_status_var = tk.StringVar(value='')
        self.file_prefix_var = tk.StringVar(value='')
        self.free_record_name_var = tk.StringVar(value='')
        self._vicon_auto_base = ''
        self.vicon_capture_name_var = tk.StringVar(value='01_REST_R')
        self._vicon_auto_base = '01_REST_R'
        self.vicon_database_path_var = tk.StringVar(value=VICON_DATABASE_PATH)
        self.vicon_bind_host_var = tk.StringVar(value=VICON_BIND_HOST)
        self.fx_max_var = tk.StringVar(value='10.0')
        self.fx_live_var = tk.StringVar(value='--- ')
        self.lc_channel_var = tk.StringVar(value='Fnorm')
        self._fx_history = collections.deque(maxlen=300)
        self.rah_display_lowpass_var = tk.BooleanVar(value=True)
        self.rest_duration_var = tk.StringVar(value='30.0')
        self.rest_phase_var = tk.StringVar(value='-')
        self._rest_running = False
        self._rest_stop_after_id = None
        self._rest_phase_after_id = None
        self._rest_duration_s = 0.0
        self._rest_t0 = None
        self.mvc_duration_var = tk.StringVar(value='6.0')
        self.mvc_lc_channel_var = tk.StringVar(value='Fnorm')
        self.mvc_lc_direction_var = tk.StringVar(value='extension')
        self.mvc_phase_var = tk.StringVar(value='-')
        self.mvc_result_var = tk.StringVar(value='MVC: --')
        self.mvc_result_detail_var = tk.StringVar(value='Window: --')
        self._mvc_running = False
        self._mvc_stop_after_id = None
        self._mvc_duration_s = 0.0
        self._mvc_rest_angle = 0.0
        self._mvc_lc_channel = 'Fnorm'
        self.motion_info_var = tk.StringVar(value='-')
        self.phase_detail_var = tk.StringVar(value='-')
        self.pm_start_var = tk.StringVar(value='0.0')
        self.pm_target_var = tk.StringVar(value='40.0')
        self.pm_speed_var = tk.StringVar(value='20.0')
        self.pm_hold_var = tk.StringVar(value='2.0')
        self.pm_phase_var = tk.StringVar(value='-')
        self.pm_reps_var = tk.StringVar(value='1')
        self.pm_rest_var = tk.StringVar(value='2.0')
        self.follow_start_var = tk.StringVar(value='0.0')
        self.follow_target_var = tk.StringVar(value='30.0')
        self.follow_speed_var = tk.StringVar(value='20.0')
        self.follow_change_mode_var = tk.StringVar(value='change all')
        self.follow_velocity_plan_var = tk.StringVar(value='')
        self.follow_velocity_random_min_var = tk.StringVar(value='5')
        self.follow_velocity_random_max_var = tk.StringVar(value='60')
        self.follow_position_plan_var = tk.StringVar(value='')
        self.follow_position_random_min_var = tk.StringVar(value='-40')
        self.follow_position_random_max_var = tk.StringVar(value='40')
        self.follow_position_random_direction_var = tk.StringVar(value='positive')
        self.follow_path_var = tk.StringVar(value='go:(0,40),back:(40,0)')
        self.follow_path_rep_vars = []
        self._follow_rep_refresh_after_id = None
        self.follow_hold_var = tk.StringVar(value='2.0')
        self.follow_rest_var = tk.StringVar(value='2.0')
        self.follow_reps_var = tk.StringVar(value='1')
        self.follow_damper_var = tk.IntVar(value=0)
        self.follow_phase_var = tk.StringVar(value='-')
        self.follow_start_buttons = []
        self.follow_stop_buttons = []
        self.rah_target_pct_var = tk.StringVar(value='10.0')
        self.rah_limit_var = tk.StringVar(value='10.0')
        self.rah_channel_var = tk.StringVar(value='Fx')
        self.rah_direction_var = tk.StringVar(value='extension')
        self.rah_profile_schedule_var = tk.StringVar(value='(0,0),(5,0),(10,10),(20,10),(25,0),(30,0)')
        self.rah_perturbation_schedule_var = tk.StringVar(value='')
        self.rah_phase_var = tk.StringVar(value='-')
        self._rah_t0 = None
        self.dr_rest_time_var = tk.StringVar(value='5.0')
        self.dr_instruction_time_var = tk.StringVar(value='5.0')
        self.dr_rise_time_var = tk.StringVar(value='5.0')
        self.dr_hold_time_var = tk.StringVar(value='5.0')
        self.dr_down_time_var = tk.StringVar(value='5.0')
        self.dr_target_force_var = tk.StringVar(value='10.0')
        self.dr_lc_channel_var = tk.StringVar(value='Fx')
        self.dr_reps_var = tk.StringVar(value='10')
        self.dr_dynamics_enabled_var = tk.BooleanVar(value=False)
        self.dr_dynamics_angle_var = tk.StringVar(value='10.0')
        self.dr_instruction_vars = []
        self.dr_phase_var = tk.StringVar(value='-')
        self._dr_t0 = None
        self._rah_generated_config = None
        self.velocity_test_count_var = tk.StringVar(value='6')
        self.velocity_test_interval_var = tk.StringVar(value='3.0')
        self.velocity_test_duration_var = tk.StringVar(value='21.000 s')
        self.velocity_test_phase_var = tk.StringVar(value='-')
        self.velocity_test_rows = []
        self._velocity_test_mode_ref = None
        self._velocity_test_t0 = None
        self._csv_path = ''
        self._log_path = ''
        self._rah_perturbation_table_path = ''
        self._velocity_test_results_path = ''
        self._motion_polling = False
        self._manual_vel_after_id = None
        self._manual_vel_target = None
        self._manual_vel_direction = 0
        self._manual_vel_tol_deg = 0.5
        self._manual_pos_after_id = None
        self._manual_pos_target = None
        self._manual_pos_direction = 0
        self._manual_pos_prev_gap = None
        self._manual_pos_tol_deg = 0.2
        self._manual_pos_speed_deg_s = 30.0
        self._vicon_capture_active = False
        self._vicon_active_name = None
        self._vicon_active_path = None
        self._free_recording = False
        self._free_record_path = ''
        self._free_record_after_id = None
        self._trigger_async_lock = threading.Lock()
        self._pending_trigger_acks = 0
        self._trigger_timing_stop_requested = False
        self._copy_to_subdir_after_trigger = False
        self._recording_health_after_id = None
        self._recording_health_logged = False
        self._rah_display_lowpass_active = bool(self.rah_display_lowpass_var.get())
        self._rah_display_lowpass_locked = False
        self._gui_session_log_path = ''
        self._gui_session_log_file = None
        self._gui_session_log_lock = threading.Lock()
        self._mvc_lc_gui_log_run_name = ''
        self._mvc_lc_gui_log_duration_s = 0.0
        self._start_gui_session_log()
        self._build_ui()
        self._install_vicon_default_traces()
        self._serial_poll()
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.display = DisplayWindow(self)

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  UI construction
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def _make_scrollable_tab(self, title: str):
        outer = ttk.Frame(self.notebook)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_inner_width(event):
            canvas.itemconfigure(inner_id, width=max(inner.winfo_reqwidth(), event.width))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")

        inner.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _sync_inner_width)
        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        self.notebook.add(outer, text=title)
        return inner

    def _build_ui(self):
        pad = {'padx': 8, 'pady': 5}
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self)
        right = ttk.Frame(self)
        left.grid(row=0, column=0, sticky='nsew', padx=(8, 4), pady=8)
        right.grid(row=0, column=1, sticky='new', padx=(4, 8), pady=8)
        top_row = ttk.Frame(left)
        top_row.pack(anchor='w', padx=8, pady=(5, 2))
        trial_frm = ttk.LabelFrame(top_row, text='Trial')
        trial_frm.grid(row=0, column=0, sticky='nw', padx=(0, 4))
        tr_row = ttk.Frame(trial_frm)
        tr_row.pack(padx=6, pady=5)
        ttk.Label(tr_row, text='No:').pack(side='left')
        ttk.Entry(tr_row, textvariable=self.trial_var, width=5).pack(side='left', padx=4)
        ttk.Button(tr_row, text='+1', command=self._trial_increment, width=4).pack(side='left')
        prefix_row = ttk.Frame(trial_frm)
        prefix_row.pack(fill='x', padx=6, pady=(0, 5))
        ttk.Label(prefix_row, text='File Prefix:').pack(side='left')
        ttk.Entry(prefix_row, textvariable=self.file_prefix_var, width=24).pack(side='left', padx=(4, 0))
        hand_frm = ttk.LabelFrame(top_row, text='Handedness')
        hand_frm.grid(row=0, column=1, sticky='nw', padx=(4, 0))
        ttk.Radiobutton(hand_frm, text='Right  (+wrist = ext)', variable=self.handedness_var, value='Right').pack(side='left', padx=8, pady=4)
        ttk.Radiobutton(hand_frm, text='Left  (+wrist = flex)', variable=self.handedness_var, value='Left').pack(side='left', padx=8, pady=4)
        conn_row = ttk.Frame(left)
        conn_row.pack(anchor='w', padx=8, pady=(2, 2))
        ser_frm = ttk.LabelFrame(conn_row, text='Serial (Exo)')
        ser_frm.grid(row=0, column=0, sticky='nw', padx=(0, 4))
        ttk.Label(ser_frm, text='Port:').grid(row=0, column=0, sticky='w', padx=5, pady=3)
        ttk.Entry(ser_frm, textvariable=self.port_var, width=7).grid(row=0, column=1, sticky='w', padx=3)
        self.btn_connect = ttk.Button(ser_frm, text='Connect', command=self.on_serial_connect, width=8)
        self.btn_connect.grid(row=0, column=2, padx=3)
        self.btn_disconnect = ttk.Button(ser_frm, text='Disconnect', command=self.on_serial_disconnect, state='disabled', width=9)
        self.btn_disconnect.grid(row=0, column=3, padx=3)
        ttk.Label(ser_frm, textvariable=self.serial_status, font=('Segoe UI', 8), wraplength=260, justify='left').grid(row=1, column=0, columnspan=4, sticky='w', padx=5, pady=(0, 3))
        lc_frm = ttk.LabelFrame(conn_row, text='Load Cell (Nano17)')
        lc_frm.grid(row=0, column=1, sticky='nw', padx=(4, 0))
        ttk.Label(lc_frm, text='Port:').grid(row=0, column=0, sticky='w', padx=5, pady=3)
        ttk.Entry(lc_frm, textvariable=self.lc_port_var, width=7).grid(row=0, column=1, sticky='w', padx=3)
        self.btn_lc_connect = ttk.Button(lc_frm, text='Connect', command=self.on_lc_connect, width=8)
        self.btn_lc_connect.grid(row=0, column=2, padx=3)
        self.btn_lc_disconnect = ttk.Button(lc_frm, text='Disconnect', command=self.on_lc_disconnect, state='disabled', width=9)
        self.btn_lc_disconnect.grid(row=0, column=3, padx=3)
        self.btn_lc_zero = ttk.Button(lc_frm, text='Zero LC', command=self.on_lc_zero, state='disabled', width=8)
        self.btn_lc_zero.grid(row=0, column=4, padx=3)
        ttk.Label(lc_frm, textvariable=self.lc_status_var, foreground='gray', font=('Segoe UI', 8), wraplength=300, justify='left').grid(row=1, column=0, columnspan=5, sticky='w', padx=5, pady=(0, 3))
        trig_frm = ttk.LabelFrame(conn_row, text='Trigger Arduino')
        trig_frm.grid(row=1, column=0, sticky='nw', padx=(0, 4), pady=(4, 0))
        ttk.Label(trig_frm, text='Port:').grid(row=0, column=0, sticky='w', padx=5, pady=3)
        ttk.Entry(trig_frm, textvariable=self.trigger_port_var, width=7).grid(row=0, column=1, sticky='w', padx=3)
        self.btn_trigger_connect = ttk.Button(trig_frm, text='Connect', command=self.on_trigger_connect, width=8)
        self.btn_trigger_connect.grid(row=0, column=2, padx=3)
        self.btn_trigger_disconnect = ttk.Button(trig_frm, text='Disconnect', command=self.on_trigger_disconnect, state='disabled', width=9)
        self.btn_trigger_disconnect.grid(row=0, column=3, padx=3)
        ttk.Label(trig_frm, textvariable=self.trigger_status_var, foreground='gray', font=('Segoe UI', 8), wraplength=260, justify='left').grid(row=1, column=0, columnspan=4, sticky='w', padx=5, pady=(0, 3))
        frm = ttk.LabelFrame(left, text='Manual Controls')
        frm.pack(anchor='w', **pad)
        manual_common = ttk.Frame(frm)
        manual_common.grid(row=0, column=0, sticky='nw', padx=6, pady=4)
        ttk.Label(manual_common, text='Damper (0-255):').grid(row=0, column=0, sticky='w', pady=4)
        self.manual_damper_entry = ttk.Entry(manual_common, width=6)
        self.manual_damper_entry.insert(0, '0')
        self.manual_damper_entry.grid(row=0, column=1, sticky='w', padx=(8, 4), pady=4)
        ttk.Button(manual_common, text='Set', command=self.on_set_damper, width=6).grid(row=0, column=2, sticky='w', pady=4)
        sync_row = ttk.Frame(manual_common)
        sync_row.grid(row=1, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Button(sync_row, text='Zero Wrist', command=self.on_zero, width=12).pack(side='left')
        ttk.Button(sync_row, text='Sync IMU', command=self.on_sync_imu, width=12).pack(side='left', padx=(6, 0))
        ttk.Button(sync_row, text='Sync Encoder', command=self.on_sync_encoder, width=13).pack(side='left', padx=(6, 0))
        ttk.Separator(frm, orient='vertical').grid(row=0, column=1, sticky='ns', padx=(10, 10), pady=4)
        self.manual_mode_tabs = ttk.Notebook(frm)
        self.manual_mode_tabs.grid(row=0, column=2, sticky='nsew', padx=(0, 6), pady=4)
        pos_tab = ttk.Frame(self.manual_mode_tabs)
        cur_tab = ttk.Frame(self.manual_mode_tabs)
        vel_tab = ttk.Frame(self.manual_mode_tabs)
        self.manual_mode_tabs.add(pos_tab, text='POS')
        self.manual_mode_tabs.add(cur_tab, text='CUR')
        self.manual_mode_tabs.add(vel_tab, text='VEL')
        self.manual_mode_tabs.bind('<<NotebookTabChanged>>', self._on_manual_mode_tab_changed)
        ttk.Label(pos_tab, text='Servo Angle:').grid(row=0, column=0, sticky='w', padx=6, pady=6)
        self.servo_entry = ttk.Entry(pos_tab, width=8)
        self.servo_entry.insert(0, '0')
        self.servo_entry.grid(row=0, column=1, sticky='w', padx=4, pady=6)
        ttk.Button(pos_tab, text='Set', command=self.on_set_servo, width=6).grid(row=0, column=2, sticky='w', padx=4, pady=6)
        ttk.Button(pos_tab, text='Passive', command=lambda : self._manual_send_serial('TORQUE_OFF'), width=10).grid(row=1, column=0, sticky='w', padx=6, pady=(0, 6))
        ttk.Button(pos_tab, text='Active', command=lambda : self._manual_send_serial('TORQUE_ON'), width=10).grid(row=1, column=1, sticky='w', padx=4, pady=(0, 6))
        ttk.Label(cur_tab, text='Servo Torque (Nmm):').grid(row=0, column=0, sticky='w', padx=6, pady=6)
        self.servo_torque_entry = ttk.Entry(cur_tab, width=8)
        self.servo_torque_entry.insert(0, '0')
        self.servo_torque_entry.grid(row=0, column=1, sticky='w', padx=4, pady=6)
        ttk.Button(cur_tab, text='Set', command=self.on_set_torque, width=6).grid(row=0, column=2, sticky='w', padx=4, pady=6)
        ttk.Button(cur_tab, text='Zero Torque', command=self.on_zero_torque, width=12).grid(row=1, column=0, columnspan=3, sticky='w', padx=6, pady=(0, 6))
        ttk.Label(vel_tab, text='Target Angle (deg):').grid(row=0, column=0, sticky='w', padx=6, pady=6)
        self.vel_target_entry = ttk.Entry(vel_tab, width=8)
        self.vel_target_entry.insert(0, '0')
        self.vel_target_entry.grid(row=0, column=1, sticky='w', padx=4, pady=6)
        ttk.Label(vel_tab, text='Speed (deg/s):').grid(row=0, column=2, sticky='w', padx=(14, 4), pady=6)
        self.vel_speed_entry = ttk.Entry(vel_tab, width=8)
        self.vel_speed_entry.insert(0, '10')
        self.vel_speed_entry.grid(row=0, column=3, sticky='w', padx=4, pady=6)
        self.vel_move_btn = ttk.Button(vel_tab, text='Move', command=self.on_start_velocity_move, width=8)
        self.vel_move_btn.grid(row=0, column=4, sticky='w', padx=4, pady=6)
        self.vel_stop_btn = ttk.Button(vel_tab, text='Stop', command=self.on_stop_velocity_move, width=8, state='disabled')
        self.vel_stop_btn.grid(row=0, column=5, sticky='w', padx=4, pady=6)
        ttk.Label(vel_tab, textvariable=self.manual_vel_status_var, font=('Segoe UI', 8), foreground='gray').grid(row=1, column=0, columnspan=6, sticky='w', padx=6, pady=(0, 6))
        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill='both', expand=True, padx=8, pady=(4, 8))
        tab_rest = self._make_scrollable_tab('  Rest  ')
        tab_mvc_lc = self._make_scrollable_tab('  MVC_LC  ')
        tab_rah = self._make_scrollable_tab('  RAH_LC  ')
        tab_velocity_test = self._make_scrollable_tab('  Test Velocity  ')
        tab_dr = self._make_scrollable_tab('  Direction reversal  ')
        tab_pm = self._make_scrollable_tab('  Passive Movement  ')
        tab_follow = self._make_scrollable_tab('  Voluntary Movement  ')
        self._build_rest_tab(tab_rest)
        self._build_mvc_lc_tab(tab_mvc_lc)
        self._build_rah_tab(tab_rah)
        self._build_velocity_test_tab(tab_velocity_test)
        self._build_direction_reversal_tab(tab_dr)
        self._build_pm_tab(tab_pm)
        self._build_follow_tab(tab_follow)
        frm = ttk.LabelFrame(right, text='Live Monitoring')
        frm.pack(fill='x', **pad)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)
        ttk.Label(frm, text='Wrist Angle:').grid(row=0, column=0, sticky='w', padx=6, pady=3)
        ttk.Label(frm, textvariable=self.angle_var, font=('Segoe UI', 13, 'bold')).grid(row=0, column=1, sticky='w')
        ttk.Label(frm, text='IMU:').grid(row=0, column=2, sticky='w', padx=(20, 6))
        ttk.Label(frm, textvariable=self.imu_var, font=('Segoe UI', 9), foreground='#1e90ff').grid(row=0, column=3, sticky='w')
        ttk.Label(frm, text='Current:').grid(row=1, column=0, sticky='w', padx=6, pady=3)
        ttk.Label(frm, textvariable=self.current_var, font=('Segoe UI', 11, 'bold')).grid(row=1, column=1, sticky='w')
        frm = ttk.LabelFrame(right, text='Recording')
        frm.pack(fill='x', **pad)
        ttk.Label(frm, textvariable=self.rec_status_var).pack(anchor='w', padx=8, pady=6)
        vicon_name_row = ttk.Frame(frm)
        vicon_name_row.pack(fill='x', padx=8, pady=(0, 6))
        ttk.Label(vicon_name_row, text='VICON Name:').pack(side='left')
        ttk.Entry(vicon_name_row, textvariable=self.vicon_capture_name_var).pack(side='left', fill='x', expand=True, padx=(6, 0))
        vicon_row = ttk.Frame(frm)
        vicon_row.pack(fill='x', padx=8, pady=(0, 6))
        ttk.Label(vicon_row, text='VICON Path:').pack(side='left')
        ttk.Entry(vicon_row, textvariable=self.vicon_database_path_var).pack(side='left', fill='x', expand=True, padx=(6, 0))
        free_rec_row = ttk.Frame(frm)
        free_rec_row.pack(fill='x', padx=8, pady=(0, 6))
        ttk.Label(free_rec_row, text='CSV Record Name:').pack(side='left')
        ttk.Entry(free_rec_row, textvariable=self.free_record_name_var).pack(side='left', fill='x', expand=True, padx=(6, 6))
        self.free_record_btn_start = ttk.Button(free_rec_row, text='Start CSV', command=self.free_record_on_start, width=10)
        self.free_record_btn_start.pack(side='left', padx=(0, 4))
        self.free_record_btn_stop = ttk.Button(free_rec_row, text='Stop', command=self.free_record_on_stop, state='disabled', width=8)
        self.free_record_btn_stop.pack(side='left')
        self.fx_frm = ttk.LabelFrame(right, text='LC Monitor (real-time)')
        self.fx_frm.pack(fill='x', **pad)
        self.lc_monitor_controls = ttk.Frame(self.fx_frm)
        self.lc_monitor_controls.pack(fill='x', padx=6, pady=(6, 2))
        ttk.Label(self.lc_monitor_controls, text='Channel:').pack(side='left')
        ttk.Combobox(self.lc_monitor_controls, textvariable=self.lc_channel_var, values=['Fx', 'Fy', 'Fz', 'Tx', 'Ty', 'Tz', 'Fnorm', 'Tnorm'], width=7, state='readonly').pack(side='left', padx=(4, 16))
        ttk.Label(self.lc_monitor_controls, text='Baseline (N or Nmm):').pack(side='left')
        ttk.Entry(self.lc_monitor_controls, textvariable=self.fx_max_var, width=8).pack(side='left', padx=6)
        ttk.Label(self.lc_monitor_controls, text='- dashed line', foreground='gray', font=('Segoe UI', 8)).pack(side='left')
        self.fx_canvas = tk.Canvas(self.fx_frm, width=460, height=150, bg='#0d1117', highlightthickness=1, highlightbackground='#333')
        self.fx_canvas.pack(padx=6, pady=(2, 4))
        ttk.Label(self.fx_frm, textvariable=self.fx_live_var, font=('Segoe UI', 10, 'bold'), foreground='#ff6b6b').pack(anchor='w', padx=8, pady=(0, 6))
        self.motion_frm = ttk.Frame(right)
        self.canvas = tk.Canvas(self.motion_frm, width=1, height=1, bg='#1a1a2e', highlightthickness=0)
        self.rah_frm = ttk.LabelFrame(right, text='RAH_LC Monitor')
        self.rah_monitor_controls = ttk.Frame(self.rah_frm)
        self.rah_monitor_controls.pack(fill='x', padx=6, pady=(6, 0))
        self.rah_display_lowpass_check = ttk.Checkbutton(self.rah_monitor_controls, text=f'Display low-pass ({RAH_DISPLAY_HOLD_LOWPASS_HZ:g} Hz)', variable=self.rah_display_lowpass_var)
        self.rah_display_lowpass_check.pack(side='left')
        self.rah_canvas = tk.Canvas(self.rah_frm, width=460, height=340, bg='#0d1117', highlightthickness=1, highlightbackground='#333')
        self.rah_canvas.pack(fill='both', expand=True, padx=6, pady=6)
        self.rah_canvas.bind('<Configure>', self._on_rah_canvas_configure)
        self._draw_canvas_idle()
        self._draw_fx_canvas_empty()
        self._fx_monitor_loop()
        self._draw_rah_canvas_idle()
        self.notebook.bind('<<NotebookTabChanged>>', self._on_tab_changed)

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â


    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Tab builders
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â


    def _build_rest_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Rest Control")
        frm.pack(anchor="w", **pad)
        ttk.Label(frm, text="Duration (s):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.rest_duration_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        r1 = ttk.Frame(frm); r1.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 6))
        self.rest_btn_start = ttk.Button(r1, text="Start", command=self.rest_on_start, width=12)
        self.rest_btn_start.pack(side="left", padx=(0, 6))
        self.rest_btn_stop = ttk.Button(r1, text="Stop", command=self.rest_on_stop,
                                        state="disabled", width=12)
        self.rest_btn_stop.pack(side="left")
        ttk.Label(frm, textvariable=self.rest_phase_var,
                  font=("Segoe UI", 9, "bold"), foreground="#ffd700").grid(
                      row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 6))

    def _build_mvc_lc_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="MVC_LC Control")
        frm.pack(anchor="w", **pad)
        ttk.Label(frm, text="Duration (s):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.mvc_duration_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="LC Channel:").grid(row=0, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Combobox(frm, textvariable=self.mvc_lc_channel_var,
                     values=["Fx","Fy","Fz","Tx","Ty","Tz","Fnorm","Tnorm"],
                     width=7, state="readonly").grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Direction:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        dir_row = ttk.Frame(frm)
        dir_row.grid(row=1, column=1, sticky="w", padx=4, pady=5)
        ttk.Label(frm, text="Analysis: mean over full recording").grid(row=1, column=2, columnspan=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Radiobutton(dir_row, text="Ext", variable=self.mvc_lc_direction_var,
                        value="extension").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(dir_row, text="Flex", variable=self.mvc_lc_direction_var,
                        value="flexion").pack(side="left")
        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(anchor="w", **pad)
        r1 = ttk.Frame(frm2); r1.pack(fill="x", padx=6, pady=(6, 2))
        self.mvc_btn_start = ttk.Button(r1, text="Start", command=self.mvc_lc_on_start, width=12)
        self.mvc_btn_start.pack(side="left", padx=(0, 6))
        self.mvc_btn_stop = ttk.Button(r1, text="Stop", command=self.mvc_lc_on_stop,
                                       state="disabled", width=12)
        self.mvc_btn_stop.pack(side="left")
        ttk.Label(frm2, textvariable=self.mvc_phase_var,
                  font=("Segoe UI", 9, "bold"), foreground="#ffd700").pack(
                      anchor="w", padx=10, pady=(4, 8))

        result_frm = ttk.LabelFrame(parent, text="Computed MVC")
        result_frm.pack(anchor="w", **pad)
        ttk.Label(result_frm, textvariable=self.mvc_result_var,
                  font=("Segoe UI", 11, "bold")).pack(
                      anchor="w", padx=10, pady=(6, 2))
        ttk.Label(result_frm, textvariable=self.mvc_result_detail_var,
                  font=("Segoe UI", 8), foreground="gray").pack(
                      anchor="w", padx=10, pady=(0, 8))


    def _build_rah_tab(self, parent):
        pad = {'padx': 6, 'pady': 4}
        frm = ttk.LabelFrame(parent, text='Parameters')
        frm.pack(fill='x', **pad)
        ttk.Label(frm, text='MVC value (N or Nmm):').grid(row=0, column=0, sticky='w', padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.rah_limit_var, width=8).grid(row=0, column=1, sticky='w', padx=4)
        ttk.Label(frm, text='LC Channel:').grid(row=0, column=2, sticky='w', padx=(16, 6), pady=5)
        ttk.Combobox(frm, textvariable=self.rah_channel_var, values=['Fx', 'Fy', 'Fz', 'Tx', 'Ty', 'Tz', 'Fnorm', 'Tnorm'], width=7, state='readonly').grid(row=0, column=3, sticky='w', padx=4)
        ttk.Label(frm, text='Direction:').grid(row=1, column=0, sticky='w', padx=6, pady=5)
        dir_row = ttk.Frame(frm)
        dir_row.grid(row=1, column=1, columnspan=3, sticky='w', padx=4, pady=5)
        ttk.Radiobutton(dir_row, text='Extension', variable=self.rah_direction_var, value='extension').pack(side='left', padx=(0, 10))
        ttk.Radiobutton(dir_row, text='Flexion', variable=self.rah_direction_var, value='flexion').pack(side='left')
        ttk.Label(frm, text='Diagram (time, % MVC):').grid(row=2, column=0, sticky='w', padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.rah_profile_schedule_var, width=72).grid(row=2, column=1, columnspan=3, sticky='ew', padx=4, pady=5)
        ttk.Label(frm, text='Perturbation (time, direction, velocity, angle):').grid(row=3, column=0, sticky='w', padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.rah_perturbation_schedule_var, width=72).grid(row=3, column=1, columnspan=3, sticky='ew', padx=4, pady=5)
        ttk.Button(frm, text='Generate Diagram', command=lambda : self._generate_rah_diagram(), width=18).grid(row=4, column=1, sticky='w', padx=4, pady=(4, 8))
        frm.columnconfigure(3, weight=1)
        frm2 = ttk.LabelFrame(parent, text='Experiment Control')
        frm2.pack(anchor='w', **pad)
        r1 = ttk.Frame(frm2)
        r1.pack(fill='x', padx=6, pady=(6, 2))
        self.rah_btn_start = ttk.Button(r1, text='  Start', command=self.rah_on_start, width=14, state='disabled')
        self.rah_btn_start.pack(side='left', padx=(0, 6))
        self.rah_btn_stop = ttk.Button(r1, text='  Stop', command=self.rah_on_stop, state='disabled', width=14)
        self.rah_btn_stop.pack(side='left')
        ttk.Label(frm2, textvariable=self.rah_phase_var, font=('Segoe UI', 9, 'bold'), foreground='#ffd700').pack(anchor='w', padx=10, pady=(4, 8))
        preview_frame = ttk.LabelFrame(parent, text='Schedule Preview')
        preview_frame.pack(fill='both', expand=True, **pad)
        self.rah_schedule_canvas = tk.Canvas(preview_frame, width=760, height=280, bg='#0d1117', highlightthickness=1, highlightbackground='#333')
        self.rah_schedule_canvas.pack(fill='both', expand=True, padx=6, pady=6)
        self.rah_schedule_canvas.bind('<Configure>', lambda _event: self._on_rah_schedule_canvas_configure())
        for var in (self.rah_profile_schedule_var, self.rah_perturbation_schedule_var, self.rah_direction_var, self.rah_limit_var, self.rah_channel_var):
            var.trace_add('write', lambda *_args: self._invalidate_rah_diagram())

    def _build_velocity_test_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        params = ttk.LabelFrame(parent, text="Parameters")
        params.pack(fill="x", **pad)
        ttk.Label(params, text="Perturbations (N):").grid(
            row=0, column=0, sticky="w", padx=6, pady=6)
        count_entry = ttk.Entry(params, textvariable=self.velocity_test_count_var, width=7)
        count_entry.grid(row=0, column=1, sticky="w", padx=4, pady=6)
        ttk.Label(params, text="Fixed interval (s):").grid(
            row=0, column=2, sticky="w", padx=(18, 6), pady=6)
        interval_entry = ttk.Entry(
            params, textvariable=self.velocity_test_interval_var, width=8)
        interval_entry.grid(row=0, column=3, sticky="w", padx=4, pady=6)
        ttk.Label(params, text="Duration (auto):").grid(
            row=0, column=4, sticky="w", padx=(18, 6), pady=6)
        ttk.Label(params, textvariable=self.velocity_test_duration_var,
                  font=("Segoe UI", 9, "bold")).grid(
                      row=0, column=5, sticky="w", padx=4, pady=6)
        ttk.Button(params, text="Build Table", command=self._rebuild_velocity_test_rows,
                   width=12).grid(row=0, column=6, sticky="w", padx=(18, 6), pady=6)
        count_entry.bind("<Return>", lambda _event: self._rebuild_velocity_test_rows())
        count_entry.bind("<FocusOut>", lambda _event: self._rebuild_velocity_test_rows())
        interval_entry.bind("<Return>", lambda _event: self._update_velocity_test_duration())
        interval_entry.bind("<FocusOut>", lambda _event: self._update_velocity_test_duration())
        self.velocity_test_count_var.trace_add(
            "write", lambda *_args: self._update_velocity_test_duration())
        self.velocity_test_interval_var.trace_add(
            "write", lambda *_args: self._update_velocity_test_duration())

        table = ttk.LabelFrame(parent, text="Perturbation Table")
        table.pack(fill="x", **pad)
        self.velocity_test_table_frame = ttk.Frame(table)
        self.velocity_test_table_frame.pack(fill="x", padx=6, pady=6)
        self._rebuild_velocity_test_rows()

        controls = ttk.LabelFrame(parent, text="Experiment Control")
        controls.pack(fill="x", **pad)
        row = ttk.Frame(controls)
        row.pack(fill="x", padx=6, pady=(6, 2))
        self.velocity_test_btn_start = ttk.Button(
            row, text="Start", command=self.velocity_test_on_start, width=14)
        self.velocity_test_btn_start.pack(side="left", padx=(0, 6))
        self.velocity_test_btn_stop = ttk.Button(
            row, text="Stop", command=self.velocity_test_on_stop,
            state="disabled", width=14)
        self.velocity_test_btn_stop.pack(side="left")
        ttk.Label(controls, textvariable=self.velocity_test_phase_var,
                  font=("Segoe UI", 9, "bold"), foreground="#ffd700").pack(
                      anchor="w", padx=10, pady=(4, 8))

    def _velocity_test_count(self) -> int:
        try:
            return max(1, min(100, int(self.velocity_test_count_var.get())))
        except (TypeError, ValueError):
            return max(1, len(getattr(self, "velocity_test_rows", [])) or 1)

    def _update_velocity_test_duration(self):
        try:
            count = self._velocity_test_count()
            interval = float(self.velocity_test_interval_var.get())
            if not _math.isfinite(interval) or interval <= 0.0:
                raise ValueError
            nominal_round_trip = 0.0
            rows = getattr(self, "velocity_test_rows", [])
            if len(rows) >= count:
                speed = abs(float(rows[count - 1]["velocity"].get()))
                angle = abs(float(rows[count - 1]["angle"].get()))
                if not all(_math.isfinite(value) for value in (speed, angle)) or speed <= 0.0:
                    raise ValueError
                nominal_round_trip = 2.0 * angle / speed
            total = (
                VelocityTestMode.FIRST_EVENT_DELAY_S
                + (count - 1) * interval
                + nominal_round_trip
                + VelocityTestMode.FINAL_SETTLE_DELAY_S
            )
            self.velocity_test_duration_var.set(f"{total:.3f} s")
        except (TypeError, ValueError):
            self.velocity_test_duration_var.set("-")

    def _rebuild_velocity_test_rows(self):
        frame = getattr(self, "velocity_test_table_frame", None)
        if frame is None:
            return
        old_values = []
        for row in getattr(self, "velocity_test_rows", []):
            old_values.append((
                row["velocity"].get(), row["angle"].get(), row["direction"].get(),
                row["velocity_max"].get(), row["angle_max"].get(),
            ))
        count = self._velocity_test_count()
        if self.velocity_test_count_var.get() != str(count):
            self.velocity_test_count_var.set(str(count))
        for child in frame.winfo_children():
            child.destroy()
        self.velocity_test_rows = []
        headings = (
            "#", "Velocity given (deg/s)", "Velocity max (deg/s)",
            "Angle given (deg)", "Angle max (deg)", "Direction",
        )
        widths = (4, 21, 20, 18, 17, 10)
        for column, (heading, width) in enumerate(zip(headings, widths)):
            ttk.Label(frame, text=heading, width=width).grid(
                row=0, column=column, sticky="w", padx=(0, 6), pady=(0, 4))
        for index in range(count):
            if index < len(old_values):
                velocity, angle, direction, velocity_max, angle_max = old_values[index]
            else:
                velocity = str(200 + 20 * (index // 2))
                angle = str(7 + index)
                direction = "+" if index % 2 == 0 else "-"
                velocity_max = angle_max = "--"
            velocity_var = tk.StringVar(value=velocity)
            velocity_max_var = tk.StringVar(value=velocity_max or "--")
            angle_var = tk.StringVar(value=angle)
            angle_max_var = tk.StringVar(value=angle_max or "--")
            direction_var = tk.StringVar(value=direction if direction in ("+", "-") else "+")
            velocity_var.trace_add(
                "write", lambda *_args: self._update_velocity_test_duration())
            angle_var.trace_add(
                "write", lambda *_args: self._update_velocity_test_duration())
            grid_row = index + 1
            ttk.Label(frame, text=str(index + 1), width=4).grid(
                row=grid_row, column=0, sticky="w", padx=(0, 6), pady=2)
            ttk.Entry(frame, textvariable=velocity_var, width=18).grid(
                row=grid_row, column=1, sticky="w", padx=(0, 6), pady=2)
            ttk.Label(frame, textvariable=velocity_max_var, width=18).grid(
                row=grid_row, column=2, sticky="w", padx=(0, 6), pady=2)
            ttk.Entry(frame, textvariable=angle_var, width=15).grid(
                row=grid_row, column=3, sticky="w", padx=(0, 6), pady=2)
            ttk.Label(frame, textvariable=angle_max_var, width=15).grid(
                row=grid_row, column=4, sticky="w", padx=(0, 6), pady=2)
            ttk.Combobox(frame, textvariable=direction_var, values=("+", "-"),
                         width=7, state="readonly").grid(
                             row=grid_row, column=5, sticky="w", padx=(0, 6), pady=2)
            self.velocity_test_rows.append({
                "velocity": velocity_var,
                "velocity_max": velocity_max_var,
                "angle": angle_var,
                "angle_max": angle_max_var,
                "direction": direction_var,
            })
        self._update_velocity_test_duration()

    def _velocity_test_inputs(self):
        self._rebuild_velocity_test_rows()
        try:
            interval = float(self.velocity_test_interval_var.get())
        except ValueError:
            return None, None, "Fixed interval must be a number."
        if not _math.isfinite(interval) or interval <= 0.0:
            return None, None, "Fixed interval must be > 0 s."
        events = []
        for index, row in enumerate(self.velocity_test_rows, start=1):
            try:
                speed = abs(float(row["velocity"].get()))
                angle = abs(float(row["angle"].get()))
            except ValueError:
                return None, None, f"Row {index}: velocity and angle must be numbers."
            if not all(_math.isfinite(value) for value in (speed, angle)):
                return None, None, f"Row {index}: velocity and angle must be finite."
            if speed <= 0.0:
                return None, None, f"Row {index}: velocity must be > 0 deg/s."
            if angle <= 0.0 or angle > WRIST_LIMIT_DEG:
                return None, None, (
                    f"Row {index}: angle must be > 0 and <= {WRIST_LIMIT_DEG:g} deg.")
            round_trip_s = 2.0 * angle / speed
            if round_trip_s >= interval:
                return None, None, (
                    f"Row {index}: nominal round trip is {round_trip_s:.3f}s; "
                    f"fixed interval must be greater than this.")
            direction = row["direction"].get().strip()
            if direction not in ("+", "-"):
                return None, None, f"Row {index}: direction must be + or -."
            events.append({
                "velocity_deg_s": speed,
                "angle_deg": angle,
                "direction": direction,
            })
        return events, interval, ""

    @staticmethod
    def _velocity_test_schedule_strings(events):
        velocities = "(" + ",".join(f"{event['velocity_deg_s']:g}" for event in events) + ")"
        positions = "(" + ",".join(
            f"{event['angle_deg']:g}{event['direction']}" for event in events) + ")"
        return velocities, positions

    @staticmethod
    def _rah_tuple_groups(raw: str):
        text = str(raw or "").strip()
        if not text:
            return []
        matches = list(re.finditer(r"\(([^()]*)\)", text))
        if not matches:
            return None
        remainder = re.sub(r"\([^()]*\)", "", text)
        if remainder.strip(" \t\r\n,;[]"):
            return None
        return [match.group(1).strip() for match in matches]

    def _parse_rah_profile_schedule(self, raw: str):
        groups = self._rah_tuple_groups(raw)
        if not groups:
            return None, "Diagram must contain (time, %MVC) pairs."
        points = []
        for index, group in enumerate(groups, start=1):
            parts = [part.strip() for part in group.replace("，", ",").split(",")]
            if len(parts) != 2:
                return None, f"Diagram point {index} must be (time, %MVC)."
            try:
                time_s = float(parts[0])
                value_pct = float(parts[1])
            except ValueError:
                return None, f"Diagram point {index} contains a non-numeric value."
            if not _math.isfinite(time_s) or not _math.isfinite(value_pct):
                return None, f"Diagram point {index} must contain finite values."
            if time_s < 0.0:
                return None, f"Diagram point {index}: time must be >= 0."
            if value_pct < 0.0 or value_pct > 100.0:
                return None, f"Diagram point {index}: %MVC must be between 0 and 100."
            points.append((time_s, value_pct))
        if len(points) < 2:
            return None, "Diagram needs at least two points."
        if abs(points[0][0]) > 1e-9:
            return None, "The first diagram point must start at time 0."
        for previous, current in zip(points, points[1:]):
            if current[0] <= previous[0]:
                return None, "Diagram times must be strictly increasing."
        if points[-1][0] <= 0.0:
            return None, "Diagram duration must be greater than 0 seconds."
        return points, ""

    def _parse_rah_perturbation_schedule(self, raw: str, total_duration: float):
        if not str(raw or "").strip():
            return [], ""
        groups = self._rah_tuple_groups(raw)
        if not groups:
            return None, "Perturbation must contain (time, direction, velocity, angle) groups."
        events = []
        for index, group in enumerate(groups, start=1):
            parts = [part.strip() for part in group.replace("，", ",").split(",")]
            if len(parts) != 4:
                return None, (
                    f"Perturbation {index} must be (time, same/opposite, velocity, angle).")
            try:
                time_s = float(parts[0])
                speed = abs(float(parts[2]))
                angle = abs(float(parts[3]))
            except ValueError:
                return None, f"Perturbation {index} contains a non-numeric time, velocity, or angle."
            if not all(_math.isfinite(value) for value in (time_s, speed, angle)):
                return None, f"Perturbation {index} must contain finite values."
            direction_text = parts[1].strip().lower()
            if direction_text in ("same", "+", "s"):
                direction = "+"
            elif direction_text in ("opposite", "-", "opp", "o"):
                direction = "-"
            else:
                return None, f"Perturbation {index}: direction must be same/+ or opposite/-."
            if time_s <= 0.0 or time_s >= total_duration:
                return None, (
                    f"Perturbation {index}: time must be > 0 and < {total_duration:g}s.")
            if speed <= 0.0:
                return None, f"Perturbation {index}: velocity must be > 0 deg/s."
            if angle <= 0.0 or angle > WRIST_LIMIT_DEG:
                return None, (
                    f"Perturbation {index}: angle must be > 0 and <= {WRIST_LIMIT_DEG:g} deg.")
            events.append((time_s, direction, speed, angle))
        events.sort(key=lambda event: event[0])
        for current, following in zip(events, events[1:]):
            nominal_end = current[0] + 2.0 * current[3] / current[2]
            if nominal_end > following[0] + 1e-9:
                return None, (
                    f"Perturbation at {current[0]:g}s nominally returns at {nominal_end:g}s, "
                    f"after the next event at {following[0]:g}s.")
        if events:
            final_end = events[-1][0] + 2.0 * events[-1][3] / events[-1][2]
            if final_end > total_duration + 1e-9:
                return None, (
                    f"Final perturbation nominally returns at {final_end:g}s, after diagram end "
                    f"at {total_duration:g}s.")
        return events, ""

    def _rah_profile_var(self):
        return self.rah_profile_schedule_var

    def _rah_perturbation_var(self):
        return self.rah_perturbation_schedule_var

    def _rah_schedule_config(self):
        (points, error) = self._parse_rah_profile_schedule(self._rah_profile_var().get())
        if error:
            return (None, error)
        (events, error) = self._parse_rah_perturbation_schedule(self._rah_perturbation_var().get(), points[-1][0])
        if error:
            return (None, error)
        mvc_value = self._parse_float(self.rah_limit_var, 10.0)
        if not _math.isfinite(mvc_value) or mvc_value <= 0.0:
            return (None, 'MVC value must be > 0.')
        direction_var = self.rah_direction_var
        channel = self.rah_channel_var.get()
        config = {'points': points, 'events': events, 'direction_choice': direction_var.get(), 'mvc_value': mvc_value, 'channel': channel}
        config['signature'] = (tuple(points), tuple(events), config['direction_choice'], mvc_value, channel)
        return (config, '')

    def _format_rah_profile_schedule(self, points) -> str:
        return ",".join(
            f"({self._compact_gui_log_number(time_s)},{self._compact_gui_log_number(value_pct)})"
            for time_s, value_pct in points)

    def _format_rah_perturbation_schedule(self, events) -> str:
        return ",".join(
            "(" + ",".join((
                self._compact_gui_log_number(time_s),
                "same" if direction == "+" else "opposite",
                self._compact_gui_log_number(speed),
                self._compact_gui_log_number(angle),
            )) + ")"
            for time_s, direction, speed, angle in events)

    def _invalidate_rah_diagram(self):
        self._rah_generated_config = None
        button = getattr(self, 'rah_btn_start', None)
        if button is not None:
            button.config(state='disabled')
        if not getattr(self, '_motion_polling', False):
            self.rah_phase_var.set('Generate diagram before starting')
        self._draw_rah_schedule_preview_idle()

    def _rah_schedule_preview_canvas(self):
        name = 'rah_schedule_canvas'
        return getattr(self, name, None)

    def _draw_rah_schedule_preview_idle(self):
        c = self._rah_schedule_preview_canvas()
        if c is None:
            return
        (W, H) = self._rah_canvas_size(c)
        c.delete('all')
        c.create_text(W // 2, H // 2, text='Generate diagram to preview', fill='#334', font=('Segoe UI', 10))

    def _on_rah_schedule_canvas_configure(self):
        config = getattr(self, '_rah_generated_config', None)
        if config is None:
            self._draw_rah_schedule_preview_idle()
        else:
            self._draw_rah_schedule_preview(config)

    def _rah_diagram_ready(self) -> bool:
        (config, error) = self._rah_schedule_config()
        generated = getattr(self, '_rah_generated_config', None)
        return not error and generated is not None and (generated.get('signature') == config.get('signature'))

    def _generate_rah_diagram(self):
        (config, error) = self._rah_schedule_config()
        if error:
            messagebox.showerror('Invalid RAH schedule', error)
            return
        self._rah_profile_var().set(self._format_rah_profile_schedule(config['points']))
        self._rah_perturbation_var().set(self._format_rah_perturbation_schedule(config['events']))
        (config, error) = self._rah_schedule_config()
        if error:
            messagebox.showerror('Invalid RAH schedule', error)
            return
        self._rah_generated_config = config
        button = self.rah_btn_start
        button.config(state='normal')
        phase_var = self.rah_phase_var
        phase_var.set(f"Diagram ready | {config['points'][-1][0]:g}s | {len(config['points'])} points | {len(config['events'])} perturbations")
        self._draw_rah_schedule_preview(config)

    def _draw_rah_schedule_preview(self, config):
        c = self._rah_schedule_preview_canvas()
        if c is None:
            return
        (W, H) = self._rah_canvas_size(c)
        c.delete('all')
        points = config['points']
        events = config['events']
        total = max(0.001, float(points[-1][0]))
        max_pct = max(10.0, max((value for (_time_s, value) in points)))
        y_max = min(100.0, max_pct * 1.2)
        (ML, MR, MT, MB) = (54, 18, 30, 42)
        (PW, PH) = (max(10, W - ML - MR), max(10, H - MT - MB))

        def x_px(time_s):
            return ML + float(time_s) / total * PW

        def y_px(value_pct):
            return MT + PH - max(0.0, min(y_max, float(value_pct))) / y_max * PH

        def profile_pct_at(time_s):
            for ((time0, value0), (time1, value1)) in zip(points, points[1:]):
                if time_s <= time1:
                    fraction = (time_s - time0) / (time1 - time0)
                    return value0 + fraction * (value1 - value0)
            return points[-1][1]
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill='#0d1117', outline='#334155')
        for index in range(5):
            value = y_max * index / 4.0
            y = y_px(value)
            c.create_line(ML, y, ML + PW, y, fill='#1f2937')
            c.create_text(ML - 6, y, text=f'{value:g}', anchor='e', fill='#718096', font=('Segoe UI', 8))
        for index in range(6):
            time_s = total * index / 5.0
            x = x_px(time_s)
            c.create_line(x, MT + PH, x, MT + PH + 4, fill='#718096')
            c.create_text(x, MT + PH + 15, text=f'{time_s:g}s', fill='#718096', font=('Segoe UI', 8))
        c.create_text(16, MT + PH // 2, text='% MVC', angle=90, fill='#94a3b8', font=('Segoe UI', 9, 'bold'))
        coordinates = []
        for (time_s, value_pct) in points:
            coordinates.extend((x_px(time_s), y_px(value_pct)))
        c.create_line(*coordinates, fill='#38bdf8', width=3)
        for (time_s, value_pct) in points:
            (x, y) = (x_px(time_s), y_px(value_pct))
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill='#e2e8f0', outline='#38bdf8')
        for (index, (time_s, direction, speed, angle)) in enumerate(events):
            x = x_px(time_s)
            y = y_px(profile_pct_at(time_s))
            color = '#22c55e' if direction == '+' else '#f59e0b'
            c.create_line(x, MT, x, MT + PH, fill=color, width=2, dash=(5, 4))
            c.create_polygon(x, MT + 2, x - 5, MT + 11, x + 5, MT + 11, fill=color, outline=color)
            c.create_polygon(x, y - 6, x + 6, y, x, y + 6, x - 6, y, fill=color, outline='#f8fafc', width=1)
            label_y = MT + 15 + index % 2 * 18
            label = 'same' if direction == '+' else 'opposite'
            c.create_text(x + 4, label_y, text=label, anchor='nw', fill=color, font=('Segoe UI', 7, 'bold'))
        source_name = 'RAH_LC'
        c.create_text(W // 2, 12, text=f'{source_name} schedule | {total:g}s', fill='#cbd5e1', font=('Segoe UI', 10, 'bold'))


    def _write_rah_perturbation_table(self, *, experiment_name: str, display_name: str,
                                      direction: int, direction_choice: str,
                                      hold_step_active: bool, profile_schedule: str = "",
                                      perturbation_schedule: str = "", custom_events=()):
        path = getattr(self, "_rah_perturbation_table_path", "")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"experiment: {experiment_name}\n")
                f.write(f"display_name: {display_name}\n")
                f.write(f"start_time_local: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}\n")
                f.write(f"trial: {self._trial_str()}\n")
                f.write(f"handedness: {self.handedness_var.get()}\n")
                f.write(f"rah_direction: {self._direction_choice_to_label(direction_choice)}\n")
                if profile_schedule:
                    f.write(f"profile_schedule_time_pct_mvc: {profile_schedule}\n")
                if perturbation_schedule:
                    f.write(f"perturbation_schedule_time_direction_velocity_angle: {perturbation_schedule}\n")
                f.write(f"perturbation_enabled: {int(bool(hold_step_active))}\n\n")
                headers = ("event_order", "absolute_time_s", "relative_dir", "speed_deg_s", "angle_deg", "signed_angle_deg")
                f.write("\t".join(headers) + "\n")
                if not custom_events:
                    f.write("none\n")
                for event_order, (time_s, rel_dir, speed, angle) in enumerate(custom_events, start=1):
                    rel = 1.0 if rel_dir != "-" else -1.0
                    signed_angle = float(direction) * rel * float(angle)
                    f.write(f"{event_order}\t{float(time_s):.6f}\t{rel_dir}\t{float(speed):.6f}\t{float(angle):.6f}\t{signed_angle:.6f}\n")
        except Exception as exc:
            self.rec_status_var.set(f"RAH perturbation table write error: {exc}")


    def _dr_rep_count_for_inputs(self) -> int:
        try:
            reps = int(self.dr_reps_var.get())
        except (TypeError, ValueError):
            reps = 10
        return max(1, min(100, reps))

    def _ensure_dr_instruction_vars(self, count: int):
        while len(self.dr_instruction_vars) < count:
            idx = len(self.dr_instruction_vars)
            default = "extension" if idx % 2 == 0 else "flexion"
            self.dr_instruction_vars.append(tk.StringVar(value=default))
        del self.dr_instruction_vars[count:]

    def _refresh_dr_instruction_inputs(self):
        frame = getattr(self, "dr_instruction_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        count = self._dr_rep_count_for_inputs()
        self._ensure_dr_instruction_vars(count)
        ttk.Label(frame, text="Instruction per rep:").pack(side="left", padx=(0, 8))
        grid = ttk.Frame(frame)
        grid.pack(side="left", fill="x", expand=True)
        for idx, var in enumerate(self.dr_instruction_vars, start=1):
            cell = ttk.Frame(grid)
            cell.grid(row=(idx - 1) // 5, column=(idx - 1) % 5, sticky="w", padx=(0, 12), pady=2)
            ttk.Label(cell, text=f"{idx}:").pack(side="left")
            ttk.Combobox(
                cell,
                textvariable=var,
                values=("extension", "flexion", "rest"),
                state="readonly",
                width=10,
            ).pack(side="left")

    def _dr_instruction_sequence(self, total_reps: int) -> list[str]:
        self._ensure_dr_instruction_vars(total_reps)
        values = []
        for idx, var in enumerate(self.dr_instruction_vars[:total_reps], start=1):
            value = str(var.get()).strip().lower()
            if value not in ("extension", "flexion", "rest"):
                messagebox.showerror("Error", f"Direction reversal instruction for rep {idx} is invalid.")
                return []
            values.append(value)
        return values

    def _build_direction_reversal_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Parameters")
        frm.pack(anchor="w", **pad)

        ttk.Label(frm, text="Rest time (s):").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.dr_rest_time_var, width=8).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Dynamic instruction time (s):").grid(row=0, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.dr_instruction_time_var, width=8).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Label(frm, text="Reps:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        reps_entry = ttk.Entry(frm, textvariable=self.dr_reps_var, width=8)
        reps_entry.grid(row=1, column=1, sticky="w", padx=4)
        reps_entry.bind("<KeyRelease>", lambda _e: self._refresh_dr_instruction_inputs())
        reps_entry.bind("<FocusOut>", lambda _e: self._refresh_dr_instruction_inputs())

        ttk.Label(frm, text="Damper PWM:").grid(row=1, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Label(frm, text="255").grid(row=1, column=3, sticky="w", padx=4)

        ttk.Checkbutton(frm, text="Dynamics mode",
                        variable=self.dr_dynamics_enabled_var,
                        command=self._on_tab_changed).grid(
                            row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(frm, text="Angle n (deg):").grid(row=2, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.dr_dynamics_angle_var, width=8).grid(row=2, column=3, sticky="w", padx=4)

        ttk.Label(frm, text="RAH ramp/hold/down (s):").grid(row=3, column=0, sticky="w", padx=6, pady=5)
        rah_time_row = ttk.Frame(frm)
        rah_time_row.grid(row=3, column=1, sticky="w", padx=4, pady=5)
        ttk.Entry(rah_time_row, textvariable=self.dr_rise_time_var, width=5).pack(side="left")
        ttk.Label(rah_time_row, text="/").pack(side="left", padx=2)
        ttk.Entry(rah_time_row, textvariable=self.dr_hold_time_var, width=5).pack(side="left")
        ttk.Label(rah_time_row, text="/").pack(side="left", padx=2)
        ttk.Entry(rah_time_row, textvariable=self.dr_down_time_var, width=5).pack(side="left")
        ttk.Label(frm, text="Target force:").grid(row=3, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.dr_target_force_var, width=8).grid(row=3, column=3, sticky="w", padx=4)

        ttk.Label(frm, text="Feedback LC Channel:").grid(row=4, column=0, sticky="w", padx=6, pady=5)
        ttk.Combobox(frm, textvariable=self.dr_lc_channel_var,
                     values=["Fx","Fy","Fz","Tx","Ty","Tz","Fnorm","Tnorm"],
                     width=7, state="readonly").grid(row=4, column=1, sticky="w", padx=4)

        self.dr_instruction_frame = ttk.Frame(frm)
        self.dr_instruction_frame.grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=(8, 6))
        self._refresh_dr_instruction_inputs()

        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(anchor="w", **pad)
        r1 = ttk.Frame(frm2)
        r1.pack(fill="x", padx=6, pady=(6, 2))
        self.dr_btn_start = ttk.Button(r1, text="  Start", command=self.direction_reversal_on_start, width=14)
        self.dr_btn_start.pack(side="left", padx=(0, 6))
        self.dr_btn_stop = ttk.Button(
            r1, text="  Stop", command=self.direction_reversal_on_stop,
            state="disabled", width=14)
        self.dr_btn_stop.pack(side="left")
        ttk.Label(frm2, textvariable=self.dr_phase_var,
                  font=("Segoe UI", 9, "bold"), foreground="#ffd700").pack(
                      anchor="w", padx=10, pady=(4, 8))

    def _build_pm_tab(self, parent):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Movement Parameters")
        frm.pack(anchor="w", **pad)
        # ttk.Label(frm, text="Direction:").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        # dir_row = ttk.Frame(frm)
        # dir_row.grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
        # ttk.Radiobutton(dir_row, text="Extension (+1)", variable=self.pm_direction_var, value="1").pack(side="left", padx=(0, 10))
        # ttk.Radiobutton(dir_row, text="Flexion (-1)",   variable=self.pm_direction_var, value="-1").pack(side="left")
        ttk.Label(frm, text="Start point (wrist deg):").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.pm_start_var, width=8).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="End point (wrist deg):").grid(row=1, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.pm_target_var, width=8).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Speed (wrist deg/s):").grid(row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.pm_speed_var, width=8).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Hold duration (s):").grid(row=2, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.pm_hold_var, width=8).grid(row=2, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Rest time (s):").grid(row=3, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.pm_rest_var, width=8).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Reps (1 = single):").grid(row=3, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.pm_reps_var, width=8).grid(row=3, column=3, sticky="w", padx=4)
        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(anchor="w", **pad)
        r1 = ttk.Frame(frm2); r1.pack(fill="x", padx=6, pady=(6, 2))
        self.pm_btn_start = ttk.Button(r1, text="  Start", command=self.pm_on_start, width=14)
        self.pm_btn_start.pack(side="left", padx=(0, 6))
        self.pm_btn_stop = ttk.Button(r1, text="  Stop", command=self.pm_on_stop,
                                      state="disabled", width=14)
        self.pm_btn_stop.pack(side="left")
        ttk.Label(frm2, textvariable=self.pm_phase_var,
                  font=("Segoe UI", 8), foreground="gray").pack(anchor="w", padx=10, pady=(2, 6))
    def _build_follow_tab(self, parent, variant="standard"):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.LabelFrame(parent, text="Voluntary Movement Path")
        frm.pack(anchor="w", **pad)
        ttk.Label(frm, text="Change mode:").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        self.follow_change_mode_combo = ttk.Combobox(
            frm,
            textvariable=self.follow_change_mode_var,
            values=("change nothing", "change velocity", "change end points", "change all"),
            state="readonly",
            width=18,
        )
        self.follow_change_mode_combo.grid(row=0, column=1, sticky="w", padx=4)
        self.follow_change_mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_follow_rep_inputs())
        ttk.Label(frm, text="Reps:").grid(row=0, column=2, sticky="w", padx=(16, 6), pady=5)
        reps_entry = ttk.Entry(frm, textvariable=self.follow_reps_var, width=8)
        reps_entry.grid(row=0, column=3, sticky="w", padx=4)
        reps_entry.bind("<KeyRelease>", lambda _e: self._schedule_follow_rep_inputs_refresh())
        reps_entry.bind("<FocusOut>", lambda _e: self._refresh_follow_rep_inputs())
        self.follow_start_label = ttk.Label(frm, text="Start (deg):")
        self.follow_start_entry = ttk.Entry(frm, textvariable=self.follow_start_var, width=8)
        self.follow_end_label = ttk.Label(frm, text="End (deg):")
        self.follow_end_entry = ttk.Entry(frm, textvariable=self.follow_target_var, width=8)
        self.follow_velocity_label = ttk.Label(frm, text="Velocity (deg/s):")
        self.follow_velocity_entry = ttk.Entry(frm, textvariable=self.follow_speed_var, width=8)
        self.follow_start_label.grid(row=1, column=0, sticky="w", padx=6, pady=5)
        self.follow_start_entry.grid(row=1, column=1, sticky="w", padx=4)
        self.follow_end_label.grid(row=1, column=2, sticky="w", padx=(16, 6), pady=5)
        self.follow_end_entry.grid(row=1, column=3, sticky="w", padx=4)
        self.follow_velocity_label.grid(row=1, column=4, sticky="w", padx=(16, 6), pady=5)
        self.follow_velocity_entry.grid(row=1, column=5, sticky="w", padx=4)
        ttk.Label(frm, text="Hold time at end (s):").grid(row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(frm, textvariable=self.follow_hold_var, width=8).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(frm, text="Rest time after return (s):").grid(row=2, column=2, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.follow_rest_var, width=8).grid(row=2, column=3, sticky="w", padx=4)
        ttk.Label(frm, text="Damper PWM:").grid(row=2, column=4, sticky="w", padx=(16, 6), pady=5)
        ttk.Entry(frm, textvariable=self.follow_damper_var, width=8).grid(row=2, column=5, sticky="w", padx=4)
        self.follow_plan_frame = ttk.Frame(frm)
        self.follow_plan_frame.grid(row=3, column=0, columnspan=6, sticky="w", padx=6, pady=(4, 0))
        self.follow_velocity_plan_row = ttk.Frame(self.follow_plan_frame)
        ttk.Label(self.follow_velocity_plan_row, text="Velocity string:").pack(side="left", padx=(0, 4))
        ttk.Entry(self.follow_velocity_plan_row, textvariable=self.follow_velocity_plan_var, width=38).pack(side="left", padx=(0, 4))
        ttk.Button(self.follow_velocity_plan_row, text="Apply", command=self._apply_follow_velocity_string, width=8).pack(side="left", padx=(0, 4))
        ttk.Label(self.follow_velocity_plan_row, text="Min:").pack(side="left", padx=(8, 2))
        ttk.Entry(self.follow_velocity_plan_row, textvariable=self.follow_velocity_random_min_var, width=6).pack(side="left", padx=(0, 4))
        ttk.Label(self.follow_velocity_plan_row, text="Max:").pack(side="left", padx=(0, 2))
        ttk.Entry(self.follow_velocity_plan_row, textvariable=self.follow_velocity_random_max_var, width=6).pack(side="left", padx=(0, 4))
        ttk.Button(self.follow_velocity_plan_row, text="Random", command=self._randomize_follow_velocity_string, width=8).pack(side="left")
        self.follow_position_plan_row = ttk.Frame(self.follow_plan_frame)
        ttk.Label(self.follow_position_plan_row, text="Position string:").pack(side="left", padx=(0, 4))
        ttk.Entry(self.follow_position_plan_row, textvariable=self.follow_position_plan_var, width=38).pack(side="left", padx=(0, 4))
        ttk.Button(self.follow_position_plan_row, text="Apply", command=self._apply_follow_position_string, width=8).pack(side="left", padx=(0, 4))
        ttk.Label(self.follow_position_plan_row, text="Min:").pack(side="left", padx=(8, 2))
        ttk.Entry(self.follow_position_plan_row, textvariable=self.follow_position_random_min_var, width=6).pack(side="left", padx=(0, 4))
        ttk.Label(self.follow_position_plan_row, text="Max:").pack(side="left", padx=(0, 2))
        ttk.Entry(self.follow_position_plan_row, textvariable=self.follow_position_random_max_var, width=6).pack(side="left", padx=(0, 4))
        ttk.Combobox(
            self.follow_position_plan_row,
            textvariable=self.follow_position_random_direction_var,
            values=("positive", "negative"),
            state="readonly",
            width=8,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(self.follow_position_plan_row, text="Random", command=self._randomize_follow_position_string, width=8).pack(side="left")
        self.follow_random_all_row = ttk.Frame(self.follow_plan_frame)
        ttk.Button(self.follow_random_all_row, text="Random all", command=self._randomize_follow_all_strings, width=12).pack(side="left")
        self.follow_path_rep_frame = ttk.Frame(frm)
        self.follow_path_rep_frame.grid(row=4, column=0, columnspan=6, sticky="w", padx=6, pady=(6, 2))
        self._refresh_follow_rep_inputs()

        frm2 = ttk.LabelFrame(parent, text="Experiment Control")
        frm2.pack(anchor="w", **pad)
        r1 = ttk.Frame(frm2)
        r1.pack(fill="x", padx=6, pady=(6, 2))
        btn_start = ttk.Button(r1, text="  Start", command=self.follow_on_start, width=14)
        btn_start.pack(side="left", padx=(0, 6))
        btn_stop = ttk.Button(r1, text="  Stop", command=self.follow_on_stop,
                              state="disabled", width=14)
        btn_stop.pack(side="left")
        self.follow_start_buttons.append(btn_start)
        self.follow_stop_buttons.append(btn_stop)
        self.follow_btn_start = btn_start
        self.follow_btn_stop = btn_stop
        ttk.Label(frm2, textvariable=self.follow_phase_var,
                  font=("Segoe UI", 8), foreground="gray").pack(anchor="w", padx=10, pady=(2, 6))


    def _set_follow_button_states(self, start_state=None, stop_state=None):
        for btn in getattr(self, "follow_start_buttons", []):
            if start_state is not None:
                btn.config(state=start_state)
        for btn in getattr(self, "follow_stop_buttons", []):
            if stop_state is not None:
                btn.config(state=stop_state)

    def _parse_follow_path_expr(self, expr: str):
        pattern = re.compile(
            r"(go|back)\s*[:：]\s*[\(（]\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*[,，]\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*[\)）]",
            re.IGNORECASE,
        )
        segments = []
        consumed = []
        for match in pattern.finditer(expr or ""):
            consumed.append(match.span())
            segments.append({
                "kind": match.group(1).lower(),
                "start": float(match.group(2)),
                "end": float(match.group(3)),
            })
        if not segments:
            raise ValueError("Enter at least one segment, for example go:(0,40),back:(40,20).")
        leftovers = list(expr or "")
        for a, b in consumed:
            for i in range(a, b):
                leftovers[i] = " "
        rest = "".join(leftovers)
        rest = re.sub(r"[\s,，;；]+", "", rest)
        if rest:
            raise ValueError(f"Could not parse: {rest[:40]}")
        return segments


    def _schedule_follow_rep_inputs_refresh(self):
        after_id = getattr(self, "_follow_rep_refresh_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        self._follow_rep_refresh_after_id = self.after(120, self._refresh_follow_rep_inputs)

    def _follow_rep_count_for_inputs(self):
        try:
            reps = int(self.follow_reps_var.get())
        except (TypeError, ValueError):
            reps = 1
        return max(1, reps)

    def _follow_change_mode(self) -> str:
        mode = self.follow_change_mode_var.get().strip().lower()
        if mode not in ("change nothing", "change velocity", "change end points", "change all"):
            mode = "change all"
            self.follow_change_mode_var.set(mode)
        return mode

    def _follow_global_param_visibility(self, mode: str):
        show_points = mode in ("change nothing", "change velocity")
        show_velocity = mode in ("change nothing", "change end points")
        for widget in (getattr(self, "follow_start_label", None), getattr(self, "follow_start_entry", None),
                       getattr(self, "follow_end_label", None), getattr(self, "follow_end_entry", None)):
            if widget is None:
                continue
            if show_points:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (getattr(self, "follow_velocity_label", None), getattr(self, "follow_velocity_entry", None)):
            if widget is None:
                continue
            if show_velocity:
                widget.grid()
            else:
                widget.grid_remove()
        velocity_row = getattr(self, "follow_velocity_plan_row", None)
        position_row = getattr(self, "follow_position_plan_row", None)
        random_all_row = getattr(self, "follow_random_all_row", None)
        if velocity_row is not None:
            velocity_row.pack_forget()
        if position_row is not None:
            position_row.pack_forget()
        if random_all_row is not None:
            random_all_row.pack_forget()
        if velocity_row is not None and mode in ("change velocity", "change all"):
            velocity_row.pack(anchor="w", fill="x", pady=(0, 3))
        if position_row is not None and mode in ("change end points", "change all"):
            position_row.pack(anchor="w", fill="x", pady=(0, 3))
        if random_all_row is not None and mode == "change all":
            random_all_row.pack(anchor="w", fill="x", pady=(0, 3))

    def _parse_follow_tuple_string(self, text: str, arity: int, label: str):
        text = (text or "").replace("（", "(").replace("）", ")")
        num = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
        pattern = re.compile(r"\(\s*(" + num + r"(?:\s*[,，]\s*" + num + r"){" + str(arity - 1) + r"})\s*\)")
        values = []
        leftovers = list(text or "")
        for match in pattern.finditer(text or ""):
            parts = re.split(r"\s*[,，]\s*", match.group(1).strip())
            if len(parts) != arity:
                raise ValueError(f"{label} tuple must contain {arity} values.")
            values.append(tuple(float(part) for part in parts))
            for idx in range(match.start(), match.end()):
                leftovers[idx] = " "
        rest = re.sub(r"[\s,，;；]+", "", "".join(leftovers))
        if rest:
            raise ValueError(f"Could not parse {label} string near: {rest[:40]}")
        if not values:
            raise ValueError(f"Enter at least one {label} tuple.")
        return values

    def _set_follow_reps_from_tuple_count(self, count: int):
        self.follow_reps_var.set(str(max(1, int(count))))
        self._ensure_follow_path_rep_vars(max(1, int(count)))

    def _set_follow_velocity_pairs(self, pairs, refresh: bool = True):
        self._set_follow_reps_from_tuple_count(len(pairs))
        for row_vars, (go_speed, return_speed) in zip(self.follow_path_rep_vars, pairs):
            row_vars["go_speed"].set(f"{go_speed:g}")
            row_vars["return_speed"].set(f"{return_speed:g}")
        self.follow_velocity_plan_var.set(",".join(f"({go:g},{back:g})" for go, back in pairs))
        if refresh:
            self._refresh_follow_rep_inputs()

    def _set_follow_position_triples(self, triples, refresh: bool = True):
        self._set_follow_reps_from_tuple_count(len(triples))
        for row_vars, (go_from, end, return_to) in zip(self.follow_path_rep_vars, triples):
            row_vars["go_from"].set(f"{go_from:g}")
            row_vars["end"].set(f"{end:g}")
            row_vars["return_to"].set(f"{return_to:g}")
        self.follow_position_plan_var.set(",".join(f"({start:g},{end:g},{ret:g})" for start, end, ret in triples))
        if refresh:
            self._refresh_follow_rep_inputs()


    def _format_follow_velocity_string_from_segments(self, path_segments) -> str:
        parts = []
        for idx in range(0, len(path_segments), 2):
            go = path_segments[idx]
            back = path_segments[idx + 1] if idx + 1 < len(path_segments) else None
            if back is None:
                continue
            parts.append(f"({float(go['speed']):g},{float(back['speed']):g})")
        return ",".join(parts)

    def _format_follow_position_string_from_segments(self, path_segments) -> str:
        parts = []
        for idx in range(0, len(path_segments), 2):
            go = path_segments[idx]
            back = path_segments[idx + 1] if idx + 1 < len(path_segments) else None
            if back is None:
                continue
            parts.append(f"({float(go['start']):g},{float(go['end']):g},{float(back['end']):g})")
        return ",".join(parts)

    def _sync_follow_string_inputs_for_mode(self, mode: str) -> bool:
        velocity_text = self.follow_velocity_plan_var.get().strip()
        position_text = self.follow_position_plan_var.get().strip()
        pairs = None
        triples = None
        try:
            if mode in ("change velocity", "change all") and velocity_text:
                pairs = self._parse_follow_tuple_string(velocity_text, 2, "velocity")
                if any(go <= 0 or back <= 0 for go, back in pairs):
                    raise ValueError("Velocity values must be > 0.")
            if mode in ("change end points", "change all") and position_text:
                triples = self._parse_follow_tuple_string(position_text, 3, "position")
            if mode == "change all" and pairs is not None and triples is not None and len(pairs) != len(triples):
                raise ValueError("In change all mode, velocity and position strings must have the same number of tuples.")
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return False
        if pairs is not None:
            self._set_follow_velocity_pairs(pairs, refresh=False)
        if triples is not None:
            self._set_follow_position_triples(triples, refresh=False)
        if pairs is not None or triples is not None:
            self._refresh_follow_rep_inputs()
        return True

    def _follow_planned_duration(self, path_segments, hold: float, rest: float, wrist_now: float, start: float, speed: float) -> float:
        positioning_time = abs(float(wrist_now) - float(start)) / max(float(speed), 0.001)
        motion_time = 0.0
        for segment in path_segments:
            motion_time += abs(float(segment["end"]) - float(segment["start"])) / max(float(segment["speed"]), 0.001)
        reps = max(1, len(path_segments) // 2)
        return positioning_time + motion_time + reps * (max(0.0, hold) + max(0.0, rest))

    def _follow_gui_log_details(self, mode: str, total_reps: int, start: float, target: float,
                                speed: float, path_segments):
        velocity_string = self._format_follow_velocity_string_from_segments(path_segments)
        position_string = self._format_follow_position_string_from_segments(path_segments)
        details = {"mode": mode, "reps": total_reps}
        if mode == "change nothing":
            details.update({
                "start_deg": f"{start:g}",
                "end_deg": f"{target:g}",
                "vel_deg_s": f"{speed:g}",
            })
        elif mode == "change velocity":
            details.update({
                "start_deg": f"{start:g}",
                "end_deg": f"{target:g}",
                "velocity": velocity_string,
            })
        elif mode == "change end points":
            details.update({
                "vel_deg_s": f"{speed:g}",
                "position": position_string,
            })
        else:
            details.update({
                "velocity": velocity_string,
                "position": position_string,
            })
        return details

    def _apply_follow_velocity_string(self):
        try:
            pairs = self._parse_follow_tuple_string(self.follow_velocity_plan_var.get(), 2, "velocity")
            if any(go <= 0 or back <= 0 for go, back in pairs):
                raise ValueError("Velocity values must be > 0.")
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self._set_follow_velocity_pairs(pairs)

    def _apply_follow_position_string(self):
        try:
            triples = self._parse_follow_tuple_string(self.follow_position_plan_var.get(), 3, "position")
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self._set_follow_position_triples(triples)

    def _randomize_follow_velocity_string(self):
        count = self._follow_rep_count_for_inputs()
        try:
            min_speed = float(self.follow_velocity_random_min_var.get())
            max_speed = float(self.follow_velocity_random_max_var.get())
        except ValueError:
            messagebox.showerror("Error", "Velocity random min/max must be numbers.")
            return False
        lo = int(_math.ceil(min(min_speed, max_speed) / 5.0) * 5)
        hi = int(_math.floor(max(min_speed, max_speed) / 5.0) * 5)
        if lo <= 0 or hi < lo:
            messagebox.showerror("Error", "Velocity random range must include at least one positive multiple of 5.")
            return False
        candidates = list(range(lo, hi + 1, 5))
        pairs = [(random.choice(candidates), random.choice(candidates)) for _ in range(count)]
        self.follow_velocity_plan_var.set(",".join(f"({go:g},{back:g})" for go, back in pairs))
        self._apply_follow_velocity_string()
        return True

    def _randomize_follow_position_string(self):
        count = self._follow_rep_count_for_inputs()
        try:
            min_pos = int(round(float(self.follow_position_random_min_var.get())))
            max_pos = int(round(float(self.follow_position_random_max_var.get())))
        except ValueError:
            messagebox.showerror("Error", "Position random min/max must be numbers.")
            return False
        lo = max(int(-WRIST_LIMIT_DEG), min(min_pos, max_pos))
        hi = min(int(WRIST_LIMIT_DEG), max(min_pos, max_pos))
        if hi - lo < 1:
            messagebox.showerror("Error", "Position random range must contain at least two integer positions.")
            return False
        direction = self.follow_position_random_direction_var.get().strip().lower()
        positive = direction != "negative"
        triples = []
        start = random.randint(lo, hi - 1) if positive else random.randint(lo + 1, hi)
        for _ in range(count):
            if positive:
                if start >= hi:
                    start = random.randint(lo, hi - 1)
                end = random.randint(start + 1, hi)
                return_to = random.randint(lo, end - 1)
            else:
                if start <= lo:
                    start = random.randint(lo + 1, hi)
                end = random.randint(lo, start - 1)
                return_to = random.randint(end + 1, hi)
            triples.append((start, end, return_to))
            start = return_to
        self.follow_position_plan_var.set(",".join(f"({start:g},{end:g},{ret:g})" for start, end, ret in triples))
        self._apply_follow_position_string()
        return True

    def _randomize_follow_all_strings(self):
        if not self._randomize_follow_velocity_string():
            return
        self._randomize_follow_position_string()


    def _initial_follow_path_rows(self):
        try:
            segments = self._parse_follow_path_expr(self.follow_path_var.get())
        except ValueError:
            segments = []
        rows = []
        for idx in range(0, len(segments) - 1, 2):
            go = segments[idx]
            back = segments[idx + 1]
            if go.get("kind") == "go" and back.get("kind") == "back":
                rows.append((
                    go["start"],
                    go["end"],
                    back["end"],
                    go.get("speed", self.follow_speed_var.get()),
                    back.get("speed", self.follow_speed_var.get()),
                ))
        return rows

    def _ensure_follow_path_rep_vars(self, count: int):
        parsed_rows = self._initial_follow_path_rows()
        while len(self.follow_path_rep_vars) < count:
            idx = len(self.follow_path_rep_vars)
            if idx < len(parsed_rows):
                values = parsed_rows[idx]
            elif idx == 0:
                speed = self.follow_speed_var.get()
                values = (
                    self._parse_float(self.follow_start_var, 0.0),
                    self._parse_float(self.follow_target_var, 30.0),
                    self._parse_float(self.follow_start_var, 0.0),
                    speed,
                    speed,
                )
            else:
                prev = self.follow_path_rep_vars[-1]
                try:
                    prev_end = float(prev["return_to"].get())
                except ValueError:
                    prev_end = 0.0
                try:
                    go_to = float(self.follow_target_var.get())
                except ValueError:
                    go_to = prev_end + 30.0
                speed = self.follow_speed_var.get()
                values = (prev_end, go_to, prev_end, speed, speed)
            keys = ("go_from", "end", "return_to", "go_speed", "return_speed")
            self.follow_path_rep_vars.append({
                key: tk.StringVar(master=self, value=f"{float(value):g}")
                for key, value in zip(keys, values)
            })
        del self.follow_path_rep_vars[count:]

    def _draw_follow_path_rep_inputs(self, mode: str):
        frame = getattr(self, "follow_path_rep_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        if mode == "change nothing":
            return
        if mode == "change velocity":
            columns = (("go_speed", "go speed"), ("return_speed", "return speed"))
        elif mode == "change end points":
            columns = (("go_from", "go from"), ("end", "end"), ("return_to", "return to"))
        else:
            columns = (
                ("go_from", "go from"),
                ("end", "end"),
                ("return_to", "return to"),
                ("go_speed", "go speed"),
                ("return_speed", "return speed"),
            )
        headers = ("Rep",) + tuple(label for _key, label in columns)
        for col, label in enumerate(headers):
            ttk.Label(frame, text=label, foreground="gray").grid(
                row=0, column=col, sticky="w", padx=(0, 8), pady=(0, 2))
        for idx, row_vars in enumerate(self.follow_path_rep_vars, start=1):
            ttk.Label(frame, text=f"{idx}:").grid(row=idx, column=0, sticky="w", padx=(0, 8), pady=2)
            for col, (key, _label) in enumerate(columns, start=1):
                ttk.Entry(frame, textvariable=row_vars[key], width=8).grid(
                    row=idx, column=col, sticky="w", padx=(0, 8), pady=2)

    def _follow_path_segments_from_rows(self, count: int, mode: str | None = None):
        mode = mode or self._follow_change_mode()
        self._ensure_follow_path_rep_vars(count)
        segments = []
        labels = []
        global_start = self._parse_float(self.follow_start_var, 0.0)
        global_end = self._parse_float(self.follow_target_var, 30.0)
        global_speed = self._parse_float(self.follow_speed_var, 20.0)
        for idx, row_vars in enumerate(self.follow_path_rep_vars[:count], start=1):
            try:
                if mode in ("change nothing", "change velocity"):
                    go_from = global_start
                    end = global_end
                    return_to = global_start
                else:
                    go_from = float(row_vars["go_from"].get().strip())
                    end = float(row_vars["end"].get().strip())
                    return_to = float(row_vars["return_to"].get().strip())
                if mode in ("change nothing", "change end points"):
                    go_speed = global_speed
                    return_speed = global_speed
                else:
                    go_speed = float(row_vars["go_speed"].get().strip())
                    return_speed = float(row_vars["return_speed"].get().strip())
            except ValueError:
                raise ValueError(f"Rep {idx} has non-numeric path values.")
            if go_speed <= 0 or return_speed <= 0:
                raise ValueError(f"Rep {idx} speeds must be > 0.")
            segments.extend([
                {"kind": "go", "start": go_from, "end": end, "speed": go_speed},
                {"kind": "back", "start": end, "end": return_to, "speed": return_speed},
            ])
            labels.extend([f"go{idx}", f"back{idx}"])
        for idx, segment in enumerate(segments):
            if abs(segment["end"] - segment["start"]) < 1e-9:
                raise ValueError(f"{labels[idx]} start and end must be different.")
            if idx > 0:
                prev_end = segments[idx - 1]["end"]
                cur_start = segment["start"]
                if abs(prev_end - cur_start) > 1e-9:
                    raise ValueError(
                        f"Path is not continuous: {labels[idx - 1]} ends at {prev_end:g}, "
                        f"but {labels[idx]} starts at {cur_start:g}."
                    )
        expr = ",".join(f"{s['kind']}:({s['start']:g},{s['end']:g})" for s in segments)
        self.follow_path_var.set(expr)
        return segments

    def _refresh_follow_rep_inputs(self):
        self._follow_rep_refresh_after_id = None
        count = self._follow_rep_count_for_inputs()
        mode = self._follow_change_mode()
        self._follow_global_param_visibility(mode)
        if hasattr(self, "follow_path_rep_frame") and mode != "change nothing":
            self._ensure_follow_path_rep_vars(count)
        self._draw_follow_path_rep_inputs(mode)


    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Shared helpers
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def _emit_marker(self, prev: int, new: int, pulse_csv: bool = False, event_id=None):
        trigger_code = 15 if int(new) == 0 else int(new)
        if event_id is None:
            try:
                event_id = self.trigger_worker.reserve_event_id()
            except Exception:
                event_id = None
        with self._trigger_async_lock:
            self._pending_trigger_acks += 1

        def _on_complete(timing):
            self._finish_marker_timing(
                prev=prev,
                new=new,
                pulse_csv=pulse_csv,
                trigger_code=trigger_code,
                event_id=event_id,
                timing=timing,
            )

        try:
            if hasattr(self.trigger_worker, "send_marker_code_with_vicon_async"):
                queued = self.trigger_worker.send_marker_code_with_vicon_async(
                    trigger_code,
                    event_id=event_id,
                    on_complete=_on_complete,
                )
            else:
                timing = self.trigger_worker.send_marker_code_with_vicon(
                    trigger_code,
                    event_id=event_id,
                )
                _on_complete(timing)
                queued = {"event_id": event_id, "queued": False}
            if queued is None:
                _on_complete(None)
                return None
            return queued
        except Exception as exc:
            print(f"[TriggerTiming] queue error ({exc})", flush=True)
            _on_complete(None)
            return None

    def _finish_marker_timing(self, *, prev, new, pulse_csv, trigger_code, event_id, timing):
        if timing is None:
            timing = {"event_id": event_id} if event_id is not None else {}
        else:
            timing = dict(timing)
            if event_id is not None:
                timing.setdefault("event_id", event_id)
        try:
            self.trigger_timing_recorder.record(
                prev_marker=prev,
                new_marker=new,
                trigger_code=trigger_code,
                timing=timing,
                experiment_t0_s=getattr(self, "_trigger_timing_t0", None),
                lc_worker=self.lc_worker,
            )
        except Exception as exc:
            print(f"[TriggerTiming] record error ({exc})", flush=True)
        try:
            if getattr(self.recorder, "recording", False):
                edge_ns = (timing or {}).get("estimated_edge_ns")
                if edge_ns is not None:
                    if pulse_csv:
                        self.recorder.align_marker_pulse(new, edge_ns)
                    else:
                        self.recorder.align_marker_transition(prev, new, edge_ns)
        except Exception as exc:
            print(f"[TriggerTiming] marker align error ({exc})", flush=True)
        self._mark_trigger_ack_done()

    def _mark_trigger_ack_done(self):
        stop_timing = False
        copy_after = False
        with self._trigger_async_lock:
            if self._pending_trigger_acks > 0:
                self._pending_trigger_acks -= 1
            if self._pending_trigger_acks == 0:
                stop_timing = self._trigger_timing_stop_requested
                self._trigger_timing_stop_requested = False
                copy_after = self._copy_to_subdir_after_trigger
                self._copy_to_subdir_after_trigger = False
        if stop_timing:
            try:
                self.trigger_timing_recorder.stop()
            except Exception:
                pass
            self._append_recording_health_log_once()
        if copy_after:
            self._append_recording_health_log_once()
            self._copy_to_subdir()

    def _pending_trigger_ack_count(self) -> int:
        with self._trigger_async_lock:
            return int(self._pending_trigger_acks)

    def _append_recording_health_log_once(self):
        if self._recording_health_logged:
            return
        log_path = getattr(self, "_log_path", "")
        if not log_path:
            return
        try:
            raw_stats = self.raw_sync_recorder.stats()
        except Exception:
            raw_stats = {}
        try:
            trig_stats = self.trigger_timing_recorder.stats()
        except Exception:
            trig_stats = {}
        try:
            raw_errors = int(raw_stats.get("write_errors", 0) or 0)
            raw_drops = int(raw_stats.get("dropped_rows", 0) or 0)
            trig_errors = int(trig_stats.get("write_errors", 0) or 0)
            status = "warning" if (raw_errors or raw_drops or trig_errors) else "ok"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 44 + "\n")
                f.write("Recording Health\n")
                f.write(f"data_integrity:             {status}\n")
                f.write(f"raw_rows_written:           {raw_stats.get('rows_written', 0)}\n")
                f.write(f"raw_dropped_rows:           {raw_drops}\n")
                f.write(f"raw_write_errors:           {raw_errors}\n")
                f.write(f"raw_backlog_at_stop:        {raw_stats.get('backlog', 0)}\n")
                f.write(f"raw_last_error:             {raw_stats.get('last_error', '')}\n")
                f.write(f"trigger_rows_written:       {trig_stats.get('rows_written', 0)}\n")
                f.write(f"trigger_write_errors:       {trig_errors}\n")
                f.write(f"trigger_last_error:         {trig_stats.get('last_error', '')}\n")
                f.write("=" * 44 + "\n")
            self._recording_health_logged = True
        except Exception as exc:
            print(f"[RecordingHealth] log error ({exc})", flush=True)

    def _on_motion_marker_pulse(self, marker: int, event_id=None):
        try:
            marker = int(marker)
        except Exception:
            return
        self._emit_marker(
            int(getattr(self.recorder, "current_marker", 0) or 0),
            marker,
            pulse_csv=(marker == 5),
            event_id=event_id,
        )

    _RAH_PHASE_TO_MARKER = {
        "rest": 1,
        "rise": 2,
        "hold": 3,
        "release": 4,
        "final_rest": 1,
    }

    def _update_rah_phase_marker(self, mode):
        """Keep RAH CSV marker and external trigger aligned with the current phase."""
        marker = self._RAH_PHASE_TO_MARKER.get(getattr(mode, "sub_phase", ""))
        if marker is None:
            return
        prev = int(getattr(self.recorder, "current_marker", 0) or 0)
        if prev == marker:
            return
        self.recorder.set_marker(marker)
        self._emit_marker(prev, marker)


    def _trial_increment(self):
        try:
            self.trial_var.set(str(int(self.trial_var.get()) + 1))
        except ValueError:
            self.trial_var.set("1")
        self._refresh_vicon_default_for_current_tab()

    def _trial_str(self) -> str:
        try:
            n = int(self.trial_var.get())
        except ValueError:
            n = 1
        return f"trial_{n:02d}"

    def _trial_number_str(self) -> str:
        try:
            n = int(self.trial_var.get())
        except ValueError:
            n = 1
        return f"{n:02d}"

    def _custom_file_prefix(self) -> str:
        try:
            return self._safe_csv_stem(self.file_prefix_var.get())
        except Exception:
            return ""

    def _trial_prefixed_stem(self, stem: str) -> str:
        custom = self._custom_file_prefix()
        if custom:
            match = re.search(r"_(\d{8}_\d{6})$", str(stem))
            if match:
                return f"{custom}_{match.group(1)}"
            return custom
        return f"{self._trial_number_str()}_{stem}"

    def _compact_gui_log_number(self, value, decimals: int = 3) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
        text = f"{number:.{decimals}f}"
        return text.rstrip("0").rstrip(".")

    def _gui_log_duration_text(self, duration_s) -> str:
        try:
            return f"{float(duration_s):.3f}"
        except (TypeError, ValueError):
            return str(duration_s)

    def _gui_log_details_text(self, details) -> str:
        if details is None:
            return ""
        if isinstance(details, dict):
            return "; ".join(f"{key}={value}" for key, value in details.items())
        if isinstance(details, (list, tuple)):
            return "; ".join(str(item) for item in details)
        return str(details)

    def _xlsx_tag(self, name: str) -> str:
        return "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}" + name

    def _xlsx_col_name(self, index: int) -> str:
        name = ""
        while index > 0:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name

    def _xlsx_cell_col_index(self, ref: str) -> int:
        letters = "".join(ch for ch in str(ref) if ch.isalpha()).upper()
        value = 0
        for ch in letters:
            value = value * 26 + ord(ch) - 64
        return value

    def _xlsx_cell_ref(self, row: int, col: int) -> str:
        return f"{self._xlsx_col_name(col)}{row}"

    def _xlsx_inline_cell(self, row: int, col: int, value, style_id: str):
        c = ET.Element(self._xlsx_tag("c"), {"r": self._xlsx_cell_ref(row, col), "s": str(style_id)})
        text = "" if value is None else str(value)
        if text:
            c.set("t", "inlineStr")
            is_el = ET.SubElement(c, self._xlsx_tag("is"))
            t_el = ET.SubElement(is_el, self._xlsx_tag("t"))
            if text.strip() != text:
                t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t_el.text = text
        return c

    def _xlsx_set_dimension(self, root, ref: str):
        dimension = root.find(self._xlsx_tag("dimension"))
        if dimension is None:
            dimension = ET.Element(self._xlsx_tag("dimension"), {"ref": ref})
            root.insert(0, dimension)
        else:
            dimension.set("ref", ref)

    def _xlsx_write_replaced_sheet(self, source_path: str, dest_path: str, sheet_xml: bytes):
        tmp_path = dest_path + ".tmp"
        with zipfile.ZipFile(source_path, "r") as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "xl/worksheets/sheet1.xml":
                    zout.writestr(item, sheet_xml)
                else:
                    zout.writestr(item, zin.read(item.filename))
        os.replace(tmp_path, dest_path)

    def _create_gui_session_log_xlsx(self, path: str):
        if not os.path.exists(GUI_LOG_TEMPLATE_PATH):
            raise FileNotFoundError(f"missing template: {GUI_LOG_TEMPLATE_PATH}")
        ET.register_namespace("", "http://schemas.openxmlformats.org/spreadsheetml/2006/main")
        with zipfile.ZipFile(GUI_LOG_TEMPLATE_PATH, "r") as zf:
            root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        sheet_data = root.find(self._xlsx_tag("sheetData"))
        if sheet_data is None:
            raise ValueError("template sheet has no sheetData")
        cols = root.find(self._xlsx_tag("cols"))
        if cols is not None:
            schedule_widths = {}
            for col_el in list(cols):
                try:
                    min_col = int(col_el.get("min", "0"))
                    max_col = int(col_el.get("max", "0"))
                except ValueError:
                    continue
                if min_col == max_col and min_col in (8, 9, 10):
                    schedule_widths[min_col] = col_el.get("width", "40")
                    cols.remove(col_el)
            diagram_width = schedule_widths.get(8, "40")
            shifted_widths = {
                8: diagram_width,
                9: diagram_width,
                10: schedule_widths.get(9, "53"),
                11: schedule_widths.get(10, "55"),
                12: diagram_width,
            }
            for col_index, width in shifted_widths.items():
                ET.SubElement(cols, self._xlsx_tag("col"), {
                    "min": str(col_index),
                    "max": str(col_index),
                    "width": str(width),
                    "customWidth": "1",
                })
        for row in list(sheet_data):
            if row.tag != self._xlsx_tag("row"):
                continue
            if row.get("r") != "1":
                sheet_data.remove(row)
                continue
            row.set("r", "1")
            row.set("spans", "1:12")
            for cell in list(row):
                ref = cell.get("r", "")
                if self._xlsx_cell_col_index(ref) >= 8:
                    row.remove(cell)
            row.append(self._xlsx_inline_cell(1, 8, "Diagram schedule", "1"))
            row.append(self._xlsx_inline_cell(1, 9, "Perturbation schedule", "1"))
            row.append(self._xlsx_inline_cell(1, 10, "Velocity schedule", "1"))
            row.append(self._xlsx_inline_cell(1, 11, "Position schedule", "1"))
            row.append(self._xlsx_inline_cell(1, 12, "Reversal schedule", "1"))
        self._xlsx_set_dimension(root, "A1:L1")
        sheet_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        self._xlsx_write_replaced_sheet(GUI_LOG_TEMPLATE_PATH, path, sheet_xml)

    def _append_xlsx_row(self, path: str, values):
        ET.register_namespace("", "http://schemas.openxmlformats.org/spreadsheetml/2006/main")
        with zipfile.ZipFile(path, "r") as zf:
            root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        sheet_data = root.find(self._xlsx_tag("sheetData"))
        if sheet_data is None:
            raise ValueError("session sheet has no sheetData")
        max_row = 1
        for row in sheet_data.findall(self._xlsx_tag("row")):
            try:
                max_row = max(max_row, int(row.get("r", "1")))
            except ValueError:
                pass
        row_index = max_row + 1
        row_el = ET.Element(self._xlsx_tag("row"), {"r": str(row_index), "spans": "1:12"})
        row_values = list(values[:12]) + [""] * max(0, 12 - len(values))
        for col, value in enumerate(row_values[:12], start=1):
            style_id = "2" if col == 10 else "1"
            row_el.append(self._xlsx_inline_cell(row_index, col, value, style_id))
        sheet_data.append(row_el)
        self._xlsx_set_dimension(root, f"A1:L{row_index}")
        sheet_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        self._xlsx_write_replaced_sheet(path, path, sheet_xml)

    def _gui_session_xlsx_values(self, run_name: str, experiment_type: str, duration_s, details=None):
        info = details if isinstance(details, dict) else {}
        type_name = str(experiment_type)
        display_type = {'Rest': 'Baseline', 'MVC_LC': 'MVC', 'RAH_LC': 'Ramp-and-Hold', 'Test Velocity': 'Test Velocity', 'Passive Movement': 'Passive movement', 'Voluntary Movement': 'Active movement', 'Direction reversal': 'Direction reversal'}.get(type_name, type_name)
        percent_mvc = ''
        wrist_angle = ''
        reps = ''
        velocity = ''
        perturbation_angle = ''
        diagram_schedule = ''
        perturbation_schedule = ''
        velocity_schedule = ''
        position_schedule = ''
        reversal_schedule = ''
        if type_name == 'Rest':
            wrist_angle = info.get('rest_angle_deg', '')
            reps = f'{self._gui_log_duration_text(duration_s)} secs'
        elif type_name in ('MVC_LC',):
            percent_mvc = info.get('mvc_value', '')
            wrist_angle = info.get('rest_angle_deg', '')
        elif type_name in ('RAH_LC',):
            percent_mvc = info.get('mvc_pct', '')
            diagram_schedule = info.get('diagram_schedule', '')
            wrist_angle = info.get('wrist_angle_deg', '')
            hold = info.get('hold_time_s', '')
            rest = info.get('rest_time_s', '')
            rise = info.get('rise_time_s', '')
            parts = [f"{info.get('reps', '')} reps"]
            timing = []
            if rest != '':
                timing.append(f'{rest}s rest')
            if rise != '':
                timing.append(f'{rise}s ramp up')
            if hold != '':
                timing.append(f'{hold}s hold')
            if timing:
                parts.append('(' + ', '.join(timing) + ')')
            reps = ' '.join((part for part in parts if part.strip()))
            velocity = info.get('perturbation_speed_deg_s', '')
            perturbation_angle = info.get('perturbation_angle_deg', '')
            perturbation_schedule = info.get('perturbation_abs_times', '')
        elif type_name == 'Passive Movement':
            start = info.get('start_deg', '')
            end = info.get('end_deg', '')
            wrist_angle = f'{start}->{end}' if start != '' or end != '' else ''
            reps = f"{info.get('reps', '')} reps"
            velocity = info.get('vel_deg_s', '')
        elif type_name == 'Voluntary Movement':
            reps = f"{info.get('reps', '')} reps"
            velocity = 'variable' if info.get('velocity') else info.get('vel_deg_s', '')
            velocity_schedule = info.get('velocity', '')
            position_schedule = info.get('position', '')
        elif type_name == 'Test Velocity':
            reps = f"{info.get('reps', '')} perturbations"
            velocity = 'variable'
            velocity_schedule = info.get('velocity', '')
            position_schedule = info.get('position', '')
        elif type_name == 'Direction reversal':
            if info.get('mode') == 'dynamics':
                display_type = 'Direction reversal, dynamics'
            reps = f"{info.get('reps', '')} reps"
            reversal_schedule = info.get('sequence', '')
        elif details is not None:
            position_schedule = self._gui_log_details_text(details)
        return [str(run_name), display_type, percent_mvc, wrist_angle, reps, velocity, perturbation_angle, diagram_schedule, perturbation_schedule, velocity_schedule, position_schedule, reversal_schedule]

    def _start_gui_session_log(self):
        try:
            os.makedirs(GUI_LOG_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(GUI_LOG_DIR, f"gui_session_{ts}.xlsx")
            self._create_gui_session_log_xlsx(path)
            self._gui_session_log_path = path
            self._gui_session_log_file = None
            self._gui_session_log_writer = None
        except Exception as exc:
            self._gui_session_log_path = ""
            self._gui_session_log_file = None
            self._gui_session_log_writer = None
            print(f"[GUI session log] disabled: {exc}", flush=True)

    def _append_gui_session_log(self, run_name: str, experiment_type: str, duration_s, details=None):
        path = getattr(self, "_gui_session_log_path", "")
        if not path:
            return
        row = self._gui_session_xlsx_values(run_name, experiment_type, duration_s, details)
        try:
            with self._gui_session_log_lock:
                self._append_xlsx_row(path, row)
        except Exception as exc:
            print(f"[GUI session log] write failed: {exc}", flush=True)

    def _close_gui_session_log(self):
        f = getattr(self, "_gui_session_log_file", None)
        if f is None:
            return
        try:
            with self._gui_session_log_lock:
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except Exception:
                    pass
                f.close()
        finally:
            self._gui_session_log_file = None
            self._gui_session_log_writer = None

    def _current_vicon_base_for_tab(self) -> str:
        try:
            tab_text = self.notebook.tab(self.notebook.select(), 'text').strip()
        except Exception:
            tab_text = 'Rest'
        hand_str = 'R' if self.handedness_var.get() == 'Right' else 'L'
        if tab_text == 'Rest':
            return self._trial_prefixed_stem(f'REST_{hand_str}')
        if tab_text == 'MVC_LC':
            dir_str = self._direction_choice_to_code(self.mvc_lc_direction_var.get())
            return self._trial_prefixed_stem(f'MVC_LC_{hand_str}_{dir_str}')
        if tab_text == 'RAH_LC':
            dir_str = self._direction_choice_to_code(self.rah_direction_var.get())
            return self._trial_prefixed_stem(f'RAH_{hand_str}_{dir_str}')
        if tab_text == 'Test Velocity':
            return self._trial_prefixed_stem(f'TEST_VELOCITY_{hand_str}')
        if tab_text == 'Direction reversal':
            return self._trial_prefixed_stem(f'DIRECTION_REVERSAL_{hand_str}')
        if tab_text == 'Passive Movement':
            start = self._parse_float(self.pm_start_var, 0.0)
            target = self._parse_float(self.pm_target_var, 40.0)
            range_str = f'{start:+g}to{target:+g}deg'.replace('+', 'p').replace('-', 'm')
            return self._trial_prefixed_stem(f'PM_{hand_str}_{range_str}')
        if tab_text == 'Voluntary Movement':
            start = self._parse_float(self.follow_start_var, 0.0)
            if self._follow_change_mode() in ('change end points', 'change all'):
                try:
                    self._ensure_follow_path_rep_vars(self._follow_rep_count_for_inputs())
                    start = float(self.follow_path_rep_vars[0]['go_from'].get())
                except Exception:
                    pass
            range_str = f'{start:+g}toPATHdeg'.replace('+', 'p').replace('-', 'm')
            return self._trial_prefixed_stem(f'VOLUNTARY_{hand_str}_{range_str}')
        return self._trial_prefixed_stem(f'REST_{hand_str}')

    def _refresh_vicon_default_for_current_tab(self):
        self._set_vicon_capture_default(self._current_vicon_base_for_tab())

    def _install_vicon_default_traces(self):

        def _refresh(*_):
            try:
                self._refresh_vicon_default_for_current_tab()
            except Exception:
                pass
        self._vicon_default_trace_vars = [self.trial_var, self.file_prefix_var, self.handedness_var, self.mvc_lc_direction_var, self.rah_direction_var, self.dr_reps_var, self.pm_start_var, self.pm_target_var, self.follow_start_var, self.follow_target_var, self.follow_speed_var, self.follow_change_mode_var, self.follow_path_var, self.follow_reps_var]
        for var in self._vicon_default_trace_vars:
            try:
                var.trace_add('write', _refresh)
            except Exception:
                pass

    def _set_vicon_capture_default(self, base: str):
        base = self._safe_csv_stem(base)
        if not base:
            return
        current = self.vicon_capture_name_var.get().strip()
        old_base = getattr(self, "_vicon_auto_base", "")
        if not current:
            suffix = ""
        elif old_base and current == old_base:
            suffix = ""
        elif old_base and current.startswith(old_base + "_"):
            suffix = current[len(old_base):]
        elif old_base and current.startswith(old_base + "-"):
            suffix = current[len(old_base):]
        else:
            return
        self.vicon_capture_name_var.set(base + suffix)
        self._vicon_auto_base = base

    def _make_file_paths(self, subdir: str, stem: str):
        root = str(DATA_ROOT)
        sub  = os.path.join(root, subdir)
        os.makedirs(root, exist_ok=True)
        os.makedirs(sub,  exist_ok=True)
        self._csv_path         = os.path.join(root, f"{stem}.csv")
        self._log_path         = os.path.join(root, f"{stem}_log.txt")
        self._rah_perturbation_table_path = os.path.join(root, f"{stem}_rah_perturbation_table.txt")
        self._velocity_test_results_path = os.path.join(root, f"{stem}_velocity_test_results.csv")
        self._trigger_timing_path = os.path.join(root, f"{stem}_trigger_timing.csv")
        self._lc_raw_path      = os.path.join(root, f"{stem}_lc_raw.csv")
        self._openrb_raw_path  = os.path.join(root, f"{stem}_openrb_raw.csv")
        self._openrb_command_timing_path = ""
        self._csv_path_sub     = os.path.join(sub,  f"{stem}.csv")
        self._log_path_sub     = os.path.join(sub,  f"{stem}_log.txt")
        self._rah_perturbation_table_path_sub = os.path.join(sub, f"{stem}_rah_perturbation_table.txt")
        self._velocity_test_results_path_sub = os.path.join(sub, f"{stem}_velocity_test_results.csv")
        self._trigger_timing_path_sub = os.path.join(sub, f"{stem}_trigger_timing.csv")
        self._lc_raw_path_sub     = os.path.join(sub, f"{stem}_lc_raw.csv")
        self._openrb_raw_path_sub = os.path.join(sub, f"{stem}_openrb_raw.csv")
        self._openrb_command_timing_path_sub = ""

    def _copy_to_subdir(self):
        if self._pending_trigger_ack_count() > 0:
            with self._trigger_async_lock:
                self._copy_to_subdir_after_trigger = True
            return
        copy_pairs = [
            (self._log_path,     getattr(self, "_log_path_sub",     None)),
            (getattr(self, "_rah_perturbation_table_path", None),
             getattr(self, "_rah_perturbation_table_path_sub", None)),
            (getattr(self, "_velocity_test_results_path", None),
             getattr(self, "_velocity_test_results_path_sub", None)),
            (getattr(self, "_trigger_timing_path", None),
             getattr(self, "_trigger_timing_path_sub", None)),
            (getattr(self, "_lc_raw_path", None),
             getattr(self, "_lc_raw_path_sub", None)),
            (getattr(self, "_openrb_raw_path", None),
             getattr(self, "_openrb_raw_path_sub", None)),
        ]
        if PRIMARY_CSV_RECORDING_ENABLED:
            copy_pairs.insert(0, (self._csv_path, getattr(self, "_csv_path_sub", None)))
        for src, dst in copy_pairs:
            if src and dst and os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass

    def _start_primary_csv(self, mode, extra_headers=None, extra_row_provider=None):
        if not PRIMARY_CSV_RECORDING_ENABLED:
            try:
                self.recorder.set_marker(0)
            except Exception:
                pass
            return False
        self.recorder.start(
            self._csv_path,
            mode,
            extra_headers=extra_headers,
            extra_row_provider=extra_row_provider,
        )
        return True

    def _primary_csv_rows(self) -> int:
        if not PRIMARY_CSV_RECORDING_ENABLED:
            return 0
        return self.recorder.get_rows()

    def _stop_primary_csv(self) -> int:
        rows = self._primary_csv_rows()
        if PRIMARY_CSV_RECORDING_ENABLED:
            self.recorder.stop()
        return rows

    def _session_record_status(self, prefix: str, rows: int | None = None, elapsed_s: float | None = None) -> str:
        label = str(prefix or "").strip()
        if label.lower().startswith("recording"):
            return "Recording"
        if label.lower().startswith("saved"):
            return "Saved"
        return label


    def _start_recording_health_poll(self):
        self._stop_recording_health_poll()
        self._recording_health_ui_loop()

    def _stop_recording_health_poll(self):
        if self._recording_health_after_id is not None:
            try:
                self.after_cancel(self._recording_health_after_id)
            except Exception:
                pass
            self._recording_health_after_id = None

    def _recording_health_ui_loop(self, interval_ms: int = 1000):
        if not (
            getattr(self.raw_sync_recorder, "active", False)
            or getattr(self.trigger_timing_recorder, "active", False)
            or self._pending_trigger_ack_count() > 0
        ):
            self._recording_health_after_id = None
            return
        try:
            elapsed = None
            if getattr(self, "_trigger_timing_t0", None) is not None:
                elapsed = max(0.0, time.perf_counter() - float(self._trigger_timing_t0))
            self.rec_status_var.set(self._session_record_status("Recording", self._primary_csv_rows(), elapsed))
        except Exception:
            pass
        self._recording_health_after_id = self.after(interval_ms, self._recording_health_ui_loop, interval_ms)

    def _safe_csv_stem(self, text: str) -> str:
        stem = (text or "").strip()
        if stem.lower().endswith(".csv"):
            stem = stem[:-4].strip()
        for ch in '<>:"/\\|?*':
            stem = stem.replace(ch, "_")
        stem = stem.strip(" ._")
        return stem

    def _free_record_path_for_name(self, name: str) -> str:
        root = str(DATA_ROOT)
        os.makedirs(root, exist_ok=True)
        stem = self._safe_csv_stem(name)
        if not stem:
            raise ValueError("CSV record name is empty.")
        path = os.path.join(root, f"{stem}.csv")
        if os.path.exists(path):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(root, f"{stem}_{ts}.csv")
        return path

    def _experiment_busy_for_free_record(self) -> bool:
        return self.recorder.recording or bool(getattr(self, '_motion_polling', False)) or bool(getattr(self, '_rest_running', False)) or bool(getattr(self, '_mvc_running', False)) or False

    def free_record_on_start(self):
        if self._experiment_busy_for_free_record():
            messagebox.showwarning(
                "CSV Record",
                "Another recording/experiment is already running. Stop it before starting free CSV record.")
            return
        try:
            path = self._free_record_path_for_name(self.free_record_name_var.get())
        except Exception as exc:
            messagebox.showerror("CSV Record", str(exc))
            return
        try:
            self.recorder.start(path, mode=None)
        except Exception as exc:
            messagebox.showerror("CSV Record", f"Cannot start CSV record: {exc}")
            return
        self._free_recording = True
        self._free_record_path = path
        self.rec_status_var.set("Recording")
        self.motion_status.set("Free CSV record running")
        self.free_record_btn_start.config(state="disabled")
        self.free_record_btn_stop.config(state="normal")
        self._free_record_loop()

    def free_record_on_stop(self):
        if not getattr(self, "_free_recording", False):
            return
        if self._free_record_after_id is not None:
            try:
                self.after_cancel(self._free_record_after_id)
            except Exception:
                pass
            self._free_record_after_id = None
        rows = self.recorder.get_rows()
        self.recorder.stop()
        self._free_recording = False
        path = self._free_record_path
        self._free_record_path = ""
        self.rec_status_var.set("Saved")
        self.motion_status.set("Idle")
        self.free_record_btn_start.config(state="normal")
        self.free_record_btn_stop.config(state="disabled")

    def _free_record_loop(self, interval_ms: int = 250):
        if not getattr(self, "_free_recording", False):
            return
        self.rec_status_var.set("Recording")
        self._free_record_after_id = self.after(interval_ms, self._free_record_loop, interval_ms)

    def _write_log_header(self, params: dict):
        try:
            with open(self._log_path, "w", encoding="utf-8") as f:
                f.write(f"experiment:       {params.pop('experiment', '?')}\n")
                f.write(f"start_time_local: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}\n")
                f.write(f"trial:            {self._trial_str()}\n")
                f.write(f"handedness:       {self.handedness_var.get()}\n")
                f.write("firmware_COM3:    high_freq_onboard/high_freq_onboard.ino\n")
                f.write("firmware_COM5:    trigger_timed/trigger_timed.ino\n")
                for k, v in params.items():
                    f.write(f"{k+':':<28}{v}\n")

        except Exception:
            pass

    def _parse_float(self, var, default: float) -> float:
        try:
            return float(var.get())
        except ValueError:
            return default


    @staticmethod
    def _direction_choice_to_sign(choice: str) -> int:
        return -1 if str(choice).strip().lower().startswith("flex") else 1

    @staticmethod
    def _direction_choice_to_code(choice: str) -> str:
        choice = str(choice).strip().lower()
        if choice.startswith("flex"):
            return "flex"
        if choice.startswith("ext"):
            return "ext"
        if choice.startswith("rad"):
            return "radial"
        if choice.startswith("uln"):
            return "ulnar"
        return choice or "unknown"

    @staticmethod
    def _direction_choice_to_label(choice: str) -> str:
        code = ExpGUI._direction_choice_to_code(choice)
        return {
            "ext": "extension",
            "flex": "flexion",
            "radial": "radial",
            "ulnar": "ulnar",
        }.get(code, code)

    def _get_wrist(self) -> float:
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 7:
            return WRIST_ENCODER_SIGN * (sample[6] - self.wrist_zero) / GEAR_RATIO
        return 0.0

    @staticmethod
    def _quat_to_imu(qw, qx, qy, qz) -> float:
        sinz = 2.0 * (qw * qz + qx * qy)
        cosz = 1.0 - 2.0 * (qy * qy + qz * qz)
        return -_math.degrees(_math.atan2(sinz, cosz))

    def _on_tab_changed(self, event=None):
        try:
            tab_text = self.notebook.tab(self.notebook.select(), 'text').strip()
        except Exception:
            return
        if tab_text == 'Voluntary Movement':
            self._refresh_follow_rep_inputs()
        pad = {'padx': 6, 'pady': 4}
        is_rah = tab_text in ('RAH_LC',)
        is_dr_force = tab_text == 'Direction reversal' and (not bool(self.dr_dynamics_enabled_var.get()))
        if is_rah or is_dr_force:
            self.fx_frm.pack_forget()
            self.motion_frm.pack_forget()
            monitor_title = f'{tab_text} Monitor' if is_rah else 'Direction Reversal Force Monitor'
            self.rah_frm.configure(text=monitor_title)
            if not self.rah_frm.winfo_ismapped():
                self.rah_frm.pack(fill='both', expand=True, **pad)
            if is_rah and (not getattr(self, '_motion_polling', False)):
                self._draw_rah_canvas_idle()
        else:
            self.rah_frm.pack_forget()
            if not self.fx_frm.winfo_ismapped():
                self.fx_frm.pack(fill='x', **pad)
            self.motion_frm.pack_forget()
            self._set_top_monitor_mode('lc')
        self._refresh_vicon_default_for_current_tab()

    def _current_tab_text(self) -> str:
        try:
            return self.notebook.tab(self.notebook.select(), "text").strip()
        except Exception:
            return ""

    def _set_top_monitor_mode(self, mode: str):
        current = getattr(self, "_top_monitor_mode", "")
        if current == mode:
            return
        self._top_monitor_mode = mode
        self.fx_frm.configure(text="LC Monitor (real-time)")
        if not self.lc_monitor_controls.winfo_ismapped():
            self.lc_monitor_controls.pack(fill="x", padx=6, pady=(6, 2), before=self.fx_canvas)
        self._draw_fx_canvas_empty()

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Hardware controls
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
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
        self.serial_status.set("Serial: Connecting...")
        self.serial_worker.connect(port, on_success=_ok, on_error=_err)

    def on_serial_disconnect(self):
        self._cancel_manual_velocity_poll()
        self._cancel_manual_position_poll()
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
                self.btn_lc_zero.config(state="normal"),
            ))
        def _err(e):
            self.after(0, lambda: self.lc_status_var.set(f"LC: Error ({e})"))
        self.lc_status_var.set("LC: Connecting + taring...")
        self.lc_worker.connect(port=port, on_success=_ok, on_error=_err)

    def on_lc_disconnect(self):
        self.lc_worker.disconnect()
        self.lc_status_var.set("LC: Disconnected")
        self.btn_lc_connect.config(state="normal")
        self.btn_lc_disconnect.config(state="disabled")
        self.btn_lc_zero.config(state="disabled")

    def on_lc_zero(self):
        if not self.lc_worker.connected:
            messagebox.showerror("Error", "Connect Load Cell first.")
            return

        ok, msg = self.lc_worker.retare(window_s=0.5, min_samples=20)
        if ok:
            self.lc_status_var.set("LC: Connected (soft-zeroed)")
        else:
            self.lc_status_var.set(f"LC: Zero failed ({msg})")

    def on_trigger_connect(self):
        port = self.trigger_port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Enter trigger Arduino COM port.")
            return
        if self.trigger_worker.connected or getattr(self.trigger_worker, "connecting", False):
            return

        def _ok():
            ready = getattr(self.trigger_worker, "firmware_ready", "")
            self.after(0, lambda: (
                self.trigger_status_var.set(f"Trigger: Verified ({port}, {ready})"),
                self.btn_trigger_connect.config(state="disabled"),
                self.btn_trigger_disconnect.config(state="normal"),
            ))

        def _err(e):
            self.after(0, lambda: (
                self.trigger_status_var.set(f"Trigger: Error ({e})"),
                self.btn_trigger_connect.config(state="normal"),
                self.btn_trigger_disconnect.config(state="disabled"),
            ))

        self.trigger_status_var.set("Trigger: Connecting...")
        self.btn_trigger_connect.config(state="disabled")
        self.btn_trigger_disconnect.config(state="disabled")
        self.trigger_worker.connect(port, on_success=_ok, on_error=_err)

        def _timeout_check():
            if getattr(self.trigger_worker, "connecting", False):
                self.trigger_worker.disconnect()
                self.trigger_status_var.set("Trigger: Error (connect timeout)")
                self.btn_trigger_connect.config(state="normal")
                self.btn_trigger_disconnect.config(state="disabled")

        self.after(5000, _timeout_check)

    def on_trigger_disconnect(self):
        self.trigger_worker.disconnect()
        self.trigger_status_var.set("Trigger: Disconnected")
        self.btn_trigger_connect.config(state="normal")
        self.btn_trigger_disconnect.config(state="disabled")

    def _trigger_ready(self):
        if getattr(self, "use_mock", False):
            return True
        return bool(
            getattr(self.trigger_worker, "connected", False)
            and getattr(self.trigger_worker, "verified", False)
        )

    def _require_trigger_connection(self):
        if self._trigger_ready():
            return True
        messagebox.showerror(
            "Error",
            "Connect verified Trigger Arduino first (READY,2 + PING/PONG).",
        )
        return False

    def _require_experiment_connections(self):
        if not self._require_trigger_connection():
            return False
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Exo Serial first.")
            return False
        if not self.lc_worker.connected:
            messagebox.showerror("Error", "Connect Load Cell first.")
            return False
        return True


    def _optional_hardware_log_fields(self):
        return {
            "exo_connected": bool(getattr(self.serial_worker, "connected", False)),
            "lc_connected": bool(getattr(self.lc_worker, "connected", False)),
            "trigger_connected": bool(getattr(self.trigger_worker, "connected", False)),
            "trigger_verified": bool(getattr(self.trigger_worker, "verified", False)),
            "trigger_firmware_ready": getattr(self.trigger_worker, "firmware_ready", ""),
        }

    def _require_manual_serial(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Exo Serial first.")
            return False
        return True

    def _manual_send_serial(self, command: str):
        if not self._require_manual_serial():
            return
        self.serial_worker.send(command)

    def _on_manual_mode_tab_changed(self, event=None):
        # Treat the POS/CUR tabs as explicit manual mode selectors, but do not
        # let casual tab clicks interfere with a running experiment.
        try:
            if not self.serial_worker.connected:
                return
            if self.motion_runner.is_running:
                return
            tab_text = self.manual_mode_tabs.tab(self.manual_mode_tabs.select(), "text").strip().upper()
        except Exception:
            return

        if tab_text == "POS":
            self._cancel_manual_velocity_poll()
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self._set_manual_velocity_buttons(False)
        elif tab_text == "CUR":
            self._cancel_manual_velocity_poll()
            self._cancel_manual_position_poll()
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_CUR")
            self._set_manual_velocity_buttons(False)
        elif tab_text == "VEL":
            self._cancel_manual_velocity_poll()
            self._cancel_manual_position_poll()
            self.serial_worker.send("MODE_VEL")
            self._set_manual_velocity_buttons(False)
            self.manual_vel_status_var.set("Velocity mode ready")


    def _append_log_line(self, line: str):
        try:
            if self._log_path:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line.rstrip() + "\n")
        except Exception:
            pass

    def _vicon_capture_name(self) -> str:
        """Return the exact operator-entered Vicon capture name."""
        return self.vicon_capture_name_var.get().strip()

    def _send_vicon_capture(self, action: str):
        if not VICON_ENABLED:
            return
        if action == "stop" and not self._vicon_capture_active:
            return
        database_path = self.vicon_database_path_var.get().strip()
        if not database_path:
            print(f"[VICON] {action}: skipped (database path is empty)", flush=True)
            self._append_log_line(f"vicon_{action}: skipped (VICON_DATABASE_PATH is empty)")
            return

        if action == "stop":
            name = self._vicon_active_name or self._vicon_capture_name()
            database_path = self._vicon_active_path or database_path
        else:
            name = self._vicon_capture_name()
            if not name:
                print("[VICON] start: skipped (VICON Name is empty)", flush=True)
                self._append_log_line("vicon_start: skipped (VICON Name is empty)")
                return
        bind_host = self.vicon_bind_host_var.get().strip() or None
        try:
            packet_id = send_vicon_udp(
                action=action,
                name=name,
                database_path=database_path,
                description="Captured automatically by RAH GUI" if action == "start" else None,
                host=VICON_HOST,
                port=VICON_PORT,
                bind_host=bind_host,
            )
            self._append_log_line(
                f"vicon_{action}: sent name={name} database_path={database_path} "
                f"bind_host={bind_host or 'auto'} packet_id={packet_id}"
            )
            print(
                f"[VICON] Capture{action.capitalize()} sent: "
                f"name={name}, database_path={database_path}, "
                f"host={VICON_HOST}:{VICON_PORT}, bind={bind_host or 'auto'}, "
                f"packet_id={packet_id}",
                flush=True,
            )
            if action == "start":
                self._vicon_capture_active = True
                self._vicon_active_name = name
                self._vicon_active_path = database_path
            else:
                self._vicon_capture_active = False
                self._vicon_active_name = None
                self._vicon_active_path = None
        except Exception as exc:
            print(f"[VICON] {action}: error ({exc})", flush=True)
            self._append_log_line(f"vicon_{action}: error ({exc})")
            if action == "start":
                self._vicon_capture_active = False
                self._vicon_active_name = None
                self._vicon_active_path = None

    def _start_session_recording(self):
        self._recording_health_logged = False
        try:
            if getattr(self.recorder, "recording", False):
                self._trigger_timing_t0 = float(self.recorder.t0)
            else:
                self._trigger_timing_t0 = time.perf_counter()
        except Exception:
            self._trigger_timing_t0 = time.perf_counter()
        timing_path = getattr(self, "_trigger_timing_path", "")
        try:
            self.raw_sync_recorder.start(
                lc_path=getattr(self, "_lc_raw_path", ""),
                openrb_path=getattr(self, "_openrb_raw_path", ""),
                experiment_t0_s=self._trigger_timing_t0,
            )
        except Exception as exc:
            print(f"[RawSync] start error ({exc})", flush=True)
            try:
                self.rec_status_var.set(f"RawSync start error: {exc}")
            except Exception:
                pass
        if timing_path:
            try:
                self.trigger_timing_recorder.start(timing_path)
            except Exception as exc:
                print(f"[TriggerTiming] start error ({exc})", flush=True)
                try:
                    self.rec_status_var.set(f"Trigger timing start error: {exc}")
                except Exception:
                    pass
        self._start_recording_health_poll()
        self._send_vicon_capture("start")

    def _stop_session_recording(self):
        self._send_vicon_capture("stop")
        self._stop_recording_health_poll()
        try:
            self.raw_sync_recorder.stop()
        except Exception:
            pass
        with self._trigger_async_lock:
            if self._pending_trigger_acks > 0:
                self._trigger_timing_stop_requested = True
                return
        try:
            self.trigger_timing_recorder.stop()
        except Exception:
            pass
        self._append_recording_health_log_once()

    def on_zero(self):
        if not self._require_manual_serial():
            return
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 8:
            self.wrist_zero = sample[6]
            self.imu_zero   = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4])

    def on_sync_imu(self):
        if not self._require_manual_serial():
            return
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 7:
            encoder_wrist = WRIST_ENCODER_SIGN * (sample[6] - self.wrist_zero) / GEAR_RATIO
            imu_raw = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4])
            self.imu_zero = imu_raw - encoder_wrist

    def on_sync_encoder(self):
        if not self._require_manual_serial():
            return
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 7:
            imu_angle = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4]) - self.imu_zero
            self.wrist_zero = sample[6] - (imu_angle * GEAR_RATIO / WRIST_ENCODER_SIGN)


    def _set_manual_velocity_buttons(self, running: bool):
        move_btn = getattr(self, "vel_move_btn", None)
        stop_btn = getattr(self, "vel_stop_btn", None)
        if move_btn is not None:
            move_btn.config(state="disabled" if running else "normal")
        if stop_btn is not None:
            stop_btn.config(state="normal" if running else "disabled")

    def _cancel_manual_velocity_poll(self):
        after_id = getattr(self, "_manual_vel_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
            self._manual_vel_after_id = None

    def _cancel_manual_position_poll(self):
        was_running = getattr(self, "_manual_pos_target", None) is not None
        after_id = getattr(self, "_manual_pos_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        if was_running and getattr(self.serial_worker, "connected", False):
            try:
                self.serial_worker.send("ZERO_VEL")
            except Exception:
                pass
        self._manual_pos_after_id = None
        self._manual_pos_target = None
        self._manual_pos_direction = 0
        self._manual_pos_prev_gap = None

    def _manual_target_to_motor_deg(self, wrist_deg: float) -> float:
        return float(wrist_deg) * WRIST_COMMAND_SIGN * GEAR_RATIO + self.wrist_zero

    def _send_manual_position_target(self, wrist_deg: float):
        motor = self._manual_target_to_motor_deg(wrist_deg)
        self.serial_worker.send(f"SET_ANG:{motor:.2f}")

    def _send_manual_position_velocity(self, direction: int):
        command_speed = (
            MANUAL_VELOCITY_DIRECTION_SIGN
            * int(direction)
            * self._manual_pos_speed_deg_s
            * GEAR_RATIO
        )
        self.serial_worker.send(f"SET_VEL_DPS:{command_speed:.3f}")

    def on_start_velocity_move(self):
        if not self._require_manual_serial():
            return
        if self.motion_runner.is_running:
            messagebox.showerror("Error", "Stop the running experiment before using manual velocity move.")
            return
        self._cancel_manual_position_poll()
        try:
            target = float(self.vel_target_entry.get().strip())
            speed = abs(float(self.vel_speed_entry.get().strip()))
        except ValueError:
            messagebox.showerror("Error", "Target angle and speed must be numbers.")
            return
        if abs(target) > WRIST_LIMIT_DEG:
            messagebox.showerror("Error", f"Target angle must be between {-WRIST_LIMIT_DEG:g} and {WRIST_LIMIT_DEG:g} deg.")
            return
        if speed <= 0.0:
            messagebox.showerror("Error", "Speed must be > 0 deg/s.")
            return

        wrist = self._get_wrist()
        gap = target - wrist
        if abs(gap) <= self._manual_vel_tol_deg:
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self.serial_worker.send(f"SET_ANG:{self._manual_target_to_motor_deg(target):.1f}")
            self.manual_vel_status_var.set(f"Already at target {target:+.1f} deg")
            return

        direction = 1 if gap > 0 else -1
        self._manual_vel_target = target
        self._manual_vel_direction = direction
        self._cancel_manual_velocity_poll()
        self.serial_worker.send("MODE_VEL")
        command_speed = MANUAL_VELOCITY_DIRECTION_SIGN * direction * speed * GEAR_RATIO
        self.serial_worker.send(f"SET_VEL_DPS:{command_speed:.3f}")
        self._set_manual_velocity_buttons(True)
        self.manual_vel_status_var.set(
            f"Moving {wrist:+.1f} -> {target:+.1f} deg at {speed:.1f} deg/s")
        self._manual_velocity_poll()

    def on_stop_velocity_move(self):
        if not self._require_manual_serial():
            return
        self._cancel_manual_velocity_poll()
        self.serial_worker.send("ZERO_VEL")
        self.serial_worker.send("MODE_EXT")
        self._manual_vel_target = None
        self._manual_vel_direction = 0
        self._set_manual_velocity_buttons(False)
        self.manual_vel_status_var.set(f"Stopped at {self._get_wrist():+.1f} deg")

    def _manual_velocity_poll(self, interval_ms: int = 20):
        target = self._manual_vel_target
        direction = self._manual_vel_direction
        if target is None or direction == 0:
            self._set_manual_velocity_buttons(False)
            return
        wrist = self._get_wrist()
        remaining = (target - wrist) * direction
        if remaining <= self._manual_vel_tol_deg:
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self.serial_worker.send(f"SET_ANG:{self._manual_target_to_motor_deg(target):.1f}")
            self._manual_vel_target = None
            self._manual_vel_direction = 0
            self._manual_vel_after_id = None
            self._set_manual_velocity_buttons(False)
            self.manual_vel_status_var.set(f"Reached {target:+.1f} deg")
            return
        self.manual_vel_status_var.set(
            f"Moving to {target:+.1f} deg | wrist={wrist:+.1f} deg | remaining={remaining:.1f} deg")
        self._manual_vel_after_id = self.after(interval_ms, self._manual_velocity_poll, interval_ms)

    def on_set_servo(self):
        if not self._require_manual_serial():
            return
        try:
            gear_ang = float(self.servo_entry.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Angle must be a number.")
            return
        if not (-90 <= gear_ang <= 90):
            messagebox.showerror("Error", "Angle out of range (-90 to 90).")
            return
        self._cancel_manual_velocity_poll()
        self._cancel_manual_position_poll()
        wrist = self._get_wrist()
        gap = gear_ang - wrist
        if abs(gap) <= self._manual_pos_tol_deg:
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self._send_manual_position_target(gear_ang)
            self.motion_status.set(
                f"Manual POS - already at {gear_ang:+.1f} deg | wrist={wrist:+.2f} deg")
            return

        direction = 1 if gap > 0 else -1
        self._manual_pos_target = gear_ang
        self._manual_pos_direction = direction
        self._manual_pos_prev_gap = gap
        self.serial_worker.send("MODE_VEL")
        self._send_manual_position_velocity(direction)
        self.motion_status.set(
            f"Manual POS - moving {wrist:+.2f} -> {gear_ang:+.1f} deg at {self._manual_pos_speed_deg_s:.1f} deg/s")
        self._manual_position_poll()

    def _manual_position_poll(self, interval_ms: int = 20):
        target = getattr(self, "_manual_pos_target", None)
        direction = int(getattr(self, "_manual_pos_direction", 0) or 0)
        if target is None:
            self._manual_pos_after_id = None
            return
        if direction == 0:
            self._cancel_manual_position_poll()
            return
        if not self.serial_worker.connected:
            self._cancel_manual_position_poll()
            return

        sample, _ = self.serial_worker.ring.get_latest()
        if not sample or len(sample) < 7:
            self.motion_status.set(f"Manual POS - waiting for wrist feedback to reach {target:+.1f} deg")
            self._manual_pos_after_id = self.after(interval_ms, self._manual_position_poll, interval_ms)
            return

        wrist = WRIST_ENCODER_SIGN * (sample[6] - self.wrist_zero) / GEAR_RATIO
        gap = target - wrist
        prev_gap = getattr(self, "_manual_pos_prev_gap", None)
        crossed = prev_gap is not None and gap * float(prev_gap) <= 0.0
        reached = abs(gap) <= self._manual_pos_tol_deg or crossed or gap * direction <= 0.0
        if reached:
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self._send_manual_position_target(target)
            self._manual_pos_target = None
            self._manual_pos_direction = 0
            self._manual_pos_prev_gap = None
            self._manual_pos_after_id = None
            self.motion_status.set(
                f"Manual POS - reached {target:+.1f} deg | wrist={wrist:+.2f} deg")
            return

        self._manual_pos_prev_gap = gap
        self.motion_status.set(
            f"Manual POS - target={target:+.1f} deg | wrist={wrist:+.2f} deg | gap={gap:+.2f} deg")
        self._manual_pos_after_id = self.after(interval_ms, self._manual_position_poll, interval_ms)

    def on_set_damper(self):
        if not self._require_manual_serial():
            return
        try:
            val = int(float(self.manual_damper_entry.get().strip()))
        except ValueError:
            messagebox.showerror("Error", "Damper must be an integer.")
            return
        if not (0 <= val <= 255):
            messagebox.showerror("Error", "Damper out of range (0-255).")
            return

        # If a mode is active, update its live damper parameter so the
        # running control logic can pick up the new value.
        mode = self.motion_runner.active_mode
        if mode is not None and hasattr(mode, "params") and isinstance(mode.params, dict):
            if "damper_pwm" in mode.params:
                mode.params["damper_pwm"] = int(val)

        self.serial_worker.send(f"SET_DMP:{val}")
        self._last_manual_dmp_sent = val

    def on_set_torque(self):
        if not self._require_manual_serial():
            return
        try:
            torque_nmm = float(self.servo_torque_entry.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Servo torque must be a number.")
            return
        max_torque_nmm = 0.500 * 1.150 * 1000.0
        if not (-max_torque_nmm <= torque_nmm <= max_torque_nmm):
            messagebox.showerror(
                "Error",
                f"Servo torque out of range ({-max_torque_nmm:.1f} to {max_torque_nmm:.1f} Nmm)."
            )
            return
        torque_nm = torque_nmm / 1000.0
        self.serial_worker.send("MODE_CUR")
        self.serial_worker.send(f"SET_TAU:{torque_nm:.5f}")

    def on_zero_torque(self):
        if not self._require_manual_serial():
            return
        try:
            self.servo_torque_entry.delete(0, tk.END)
            self.servo_torque_entry.insert(0, "0")
        except Exception:
            pass
        self.serial_worker.send("MODE_CUR")
        self.serial_worker.send("ZERO_CUR")

    def _serial_poll(self, interval_ms=100):
        sample, _ = self.serial_worker.ring.get_latest()
        if sample and len(sample) >= 8:
            wrist = WRIST_ENCODER_SIGN * (sample[6] - self.wrist_zero) / GEAR_RATIO
            imu   = self._quat_to_imu(sample[1], sample[2], sample[3], sample[4]) - self.imu_zero
            self.angle_var.set(f"{wrist:.2f} deg")
            self.imu_var.set(f"{imu:.2f} deg")
            self.current_var.set(f"{sample[5]:.2f} mA")
        self.after(interval_ms, self._serial_poll)

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Rest handlers
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def rest_on_start(self):
        if not self._require_trigger_connection():
            return
        duration = self._parse_float(self.rest_duration_var, 30.0)
        if duration <= 0:
            messagebox.showerror('Error', 'Duration must be > 0.')
            return
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        hand_str = 'R' if self.handedness_var.get() == 'Right' else 'L'
        vicon_base = self._trial_prefixed_stem(f'REST_{hand_str}')
        stem = self._trial_prefixed_stem(f'REST_{hand_str}_{ts}')
        self._make_file_paths('rest', stem)
        self._set_vicon_capture_default(vicon_base)
        self.rest_btn_start.config(state='disabled')
        self.rest_btn_stop.config(state='disabled')
        self.rest_phase_var.set('starting in 3...')
        self.motion_status.set('REST - starting in 3...')
        self._rest_duration_s = duration

        def _launch():
            rest_angle = self._get_wrist()
            mode = RestMode(rest_angle_deg=rest_angle, damper_pwm=0)
            mode.reset()
            self._rest_running = True
            self._rest_t0 = time.perf_counter()
            self._write_log_header({'experiment': 'rest', 'rest_angle_deg': f'{rest_angle:.3f}', 'duration_s': f'{duration:.3f}', 'csv_recording': 'disabled', 'servo_control': 'hold_current_angle_if_exo_connected', 'damper_pwm': '0', **self._optional_hardware_log_fields()})
            self._append_gui_session_log(vicon_base, 'Rest', duration, {'rest_angle_deg': f'{rest_angle:.3f}'})
            self.motion_runner.start(mode)
            self.serial_worker.send('TORQUE_ON')
            self._start_session_recording()
            self._emit_marker(0, 1)
            self.motion_status.set(f'REST - servo holding {rest_angle:.1f} deg for {duration:.1f}s')
            self.phase_detail_var.set(mode.phase)
            self.rec_status_var.set('Recording')
            self.rest_btn_stop.config(state='normal')
            self._rest_update_phase()
            self._rest_stop_after_id = self.after(int(duration * 1000), self.rest_on_stop)
        self.display.countdown(_launch)

    def rest_on_stop(self):
        if not self._rest_running:
            return
        self._rest_running = False
        if self._rest_stop_after_id is not None:
            try:
                self.after_cancel(self._rest_stop_after_id)
            except Exception:
                pass
            self._rest_stop_after_id = None
        if self._rest_phase_after_id is not None:
            try:
                self.after_cancel(self._rest_phase_after_id)
            except Exception:
                pass
            self._rest_phase_after_id = None
        self._emit_marker(1, 0)
        self._stop_session_recording()
        self._append_rest_summary()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("ZERO_VEL")
        self.serial_worker.send("MODE_EXT")
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.phase_detail_var.set("-")
        self.rest_phase_var.set("-")
        self.rec_status_var.set("Saved")
        self.rest_btn_start.config(state="normal")
        self.rest_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

    def _rest_update_phase(self):
        if not getattr(self, "_rest_running", False):
            return
        duration = max(0.0, float(getattr(self, "_rest_duration_s", 0.0)))
        t0 = getattr(self, "_rest_t0", None) or time.perf_counter()
        elapsed = max(0.0, time.perf_counter() - t0)
        remaining = max(0.0, duration - elapsed)
        self.rest_phase_var.set(
            f"rest timer | elapsed {elapsed:.1f}s / {duration:.1f}s | remaining {remaining:.1f}s")
        self.motion_status.set(f"REST - remaining {remaining:.1f}s")
        self._rest_phase_after_id = self.after(250, self._rest_update_phase)

    def _append_rest_summary(self):
        try:
            duration = float(getattr(self, "_rest_duration_s", 0.0))
            t0 = getattr(self, "_rest_t0", None)
            elapsed = max(0.0, time.perf_counter() - t0) if t0 is not None else duration
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 44 + "\n")
                f.write("REST Summary\n")
                f.write("role:                       timer_only\n")
                f.write(f"planned_duration_s:         {duration:.3f}\n")
                f.write(f"actual_elapsed_s:           {elapsed:.3f}\n")
                f.write("csv_recording:              disabled\n")
                f.write("servo_control:              hold_current_angle\n")
                f.write("damper_pwm:                 0\n")
                f.write("=" * 44 + "\n")
        except Exception:
            pass

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  MVC_LC handlers
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def mvc_lc_on_start(self):
        if not self._require_experiment_connections():
            return
        duration = self._parse_float(self.mvc_duration_var, 6.0)
        if not _math.isfinite(duration) or duration <= 0.0:
            messagebox.showerror("Error", "MVC_LC duration must be a finite number greater than 0 s.")
            return

        channel = self.mvc_lc_channel_var.get().strip() or "Fnorm"
        if channel not in self._LC_CHANNELS:
            messagebox.showerror("Error", "Select a valid LC channel.")
            return

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if self.handedness_var.get() == "Right" else "L"
        direction_choice = self.mvc_lc_direction_var.get()
        dir_str = self._direction_choice_to_code(direction_choice)
        vicon_base = self._trial_prefixed_stem(f"MVC_LC_{hand_str}_{dir_str}")
        stem     = self._trial_prefixed_stem(f"MVC_LC_{hand_str}_{dir_str}_{ts}")
        self._make_file_paths("mvc_lc", stem)
        self._set_vicon_capture_default(vicon_base)
        self.mvc_btn_start.config(state="disabled")
        self.mvc_btn_stop.config(state="disabled")
        self.mvc_phase_var.set("starting in 3...")
        self.mvc_result_var.set("MVC: pending")
        self.mvc_result_detail_var.set(
            f"Channel: {channel} | analysis window: 0.000-{duration:.3f} s")
        self.motion_status.set("MVC_LC - starting in 3...")
        self._mvc_lc_gui_log_run_name = vicon_base
        self._mvc_lc_gui_log_duration_s = duration

        def _launch():
            rest_angle = self._get_wrist()
            mode = RestMode(rest_angle_deg=rest_angle, damper_pwm=255)
            mode.reset()

            self._mvc_running       = True
            self._mvc_duration_s    = duration
            self._mvc_rest_angle    = rest_angle
            self._mvc_lc_channel    = channel
            self._write_log_header({"experiment": "mvc_lc",
                                     "direction": self._direction_choice_to_label(direction_choice),
                                     "rest_angle_deg": f"{rest_angle:.3f}",
                                     "duration_s": f"{duration:.3f}",
                                     "cue_schedule": "use your maximum force for the full duration",
                                     "lc_channel": channel,
                                     "analysis_window_s": f"0.000-{duration:.3f}",
                                     "analysis_rule": "mean of all valid LC samples from 0 s to the configured duration",
                                     "analysis_mean": "pending"})
            self.motion_runner.start(mode)
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:255")
            self._start_primary_csv(mode)
            self._start_session_recording()
            self.recorder.set_marker(1)
            self._emit_marker(0, 1)
            self.mvc_phase_var.set("Use your max force")
            self.motion_status.set("MVC_LC - Use your max force")
            self.rec_status_var.set(self._session_record_status("Recording"))
            self.mvc_btn_stop.config(state="normal")
            self._mvc_stop_after_id = self.after(int(duration * 1000), self.mvc_lc_on_stop)

        self.display.countdown(_launch)

    def mvc_lc_on_stop(self):
        if not self._mvc_running:
            return
        self._mvc_running = False
        if self._mvc_stop_after_id is not None:
            try:
                self.after_cancel(self._mvc_stop_after_id)
            except Exception:
                pass
            self._mvc_stop_after_id = None

        self._emit_marker(1, 0)
        rows = self._stop_primary_csv()
        self._stop_session_recording()
        mvc_mean, sample_count, unit, analysis_start, analysis_end = self._compute_mvc_lc_mean(
            self._csv_path, self._mvc_lc_channel, self._mvc_duration_s)
        self._append_mvc_lc_summary(
            mvc_mean, sample_count, unit, analysis_start, analysis_end)
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        if mvc_mean is None:
            self.mvc_phase_var.set("saved | MVC mean unavailable")
            self.mvc_result_var.set("MVC: unavailable")
            self.mvc_result_detail_var.set(
                f"Channel: {self._mvc_lc_channel} | analysis window 0-{self._mvc_duration_s:g}s | not enough valid data")
            self.rec_status_var.set(self._session_record_status("Saved", rows))
        else:
            self.mvc_phase_var.set(
                f"saved | {self._mvc_lc_channel} mean = {mvc_mean:.6f} {unit}")
            self.mvc_result_var.set(
                f"MVC ({self._mvc_lc_channel}): {mvc_mean:.6f} {unit}")
            detail_parts = []
            if analysis_start is not None and analysis_end is not None:
                detail_parts.append(f"analysis {analysis_start:.3f}-{analysis_end:.3f}s")
            detail_parts.append(f"samples {sample_count}")
            self.mvc_result_detail_var.set(" | ".join(detail_parts))
            self.rec_status_var.set(self._session_record_status("Saved", rows))
        mvc_value_text = "NA" if mvc_mean is None else f"{mvc_mean:.3f} {unit}".strip()
        self._append_gui_session_log(
            self._mvc_lc_gui_log_run_name or self._current_vicon_base_for_tab(),
            "MVC_LC",
            self._mvc_lc_gui_log_duration_s or self._mvc_duration_s,
            {
                "lc_channel": self._mvc_lc_channel,
                "mvc_value": mvc_value_text,
                "rest_angle_deg": f"{getattr(self, '_mvc_rest_angle', 0.0):.3f}",
            },
        )
        self.motion_status.set("Idle")
        self.mvc_btn_start.config(state="normal")
        self.mvc_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()


    def _compute_mvc_lc_mean(self, csv_path: str, channel: str, duration_s: float):
        """Average valid LC samples within the experiment-relative 0-duration window."""
        analysis_start_s = 0.0
        analysis_end_s = float(duration_s)
        _, _, unit = self._LC_CHANNELS.get(channel, (None, False, ""))
        samples = []
        source_path = csv_path
        if not source_path or not os.path.exists(source_path):
            source_path = getattr(self, "_lc_raw_path", "")
        try:
            with open(source_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = set(reader.fieldnames or [])
                rows = list(reader)
        except Exception:
            return None, 0, unit, analysis_start_s, analysis_end_s
        if not rows:
            return None, 0, unit, analysis_start_s, analysis_end_s
        if "t_rel" in fieldnames:
            time_col = "t_rel"
            time_scale = 1.0
            col_map = {
                "Fx": "lc_fx", "Fy": "lc_fy", "Fz": "lc_fz",
                "Tx": "lc_tx", "Ty": "lc_ty", "Tz": "lc_tz",
            }
        else:
            time_col = "host_rx_rel_ns" if "host_rx_rel_ns" in fieldnames else "ati_sample_est_rel_ns"
            time_scale = 1_000_000_000.0
            col_map = {
                "Fx": "fx_N", "Fy": "fy_N", "Fz": "fz_N",
                "Tx": "tx_Nmm", "Ty": "ty_Nmm", "Tz": "tz_Nmm",
            }
        for row in rows:
            try:
                t = float(row.get(time_col, "")) / time_scale
            except ValueError:
                continue
            try:
                if channel == "Fnorm":
                    vals = [float(row[col_map[k]]) for k in ("Fx", "Fy", "Fz")]
                    val = _math.sqrt(sum(v * v for v in vals))
                elif channel == "Tnorm":
                    vals = [float(row[col_map[k]]) for k in ("Tx", "Ty", "Tz")]
                    val = _math.sqrt(sum(v * v for v in vals))
                else:
                    val = float(row[col_map[channel]])
            except (KeyError, ValueError):
                continue
            if _math.isfinite(t) and _math.isfinite(val):
                samples.append((t, val))
        if not samples:
            return None, 0, unit, analysis_start_s, analysis_end_s
        values = [
            val for t, val in samples
            if analysis_start_s <= t <= analysis_end_s
        ]
        if not values:
            return None, 0, unit, analysis_start_s, analysis_end_s
        return sum(values) / len(values), len(values), unit, analysis_start_s, analysis_end_s

    def _append_mvc_lc_summary(self, mean_value, mean_count: int, unit: str,
                               analysis_start=None, analysis_end=None):
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 44 + "\n")
                f.write("MVC_LC Summary\n")
                f.write(f"analysis_channel:           {self._mvc_lc_channel}\n")
                if analysis_start is not None and analysis_end is not None:
                    f.write(f"analysis_window_s:          {analysis_start:.6f}-{analysis_end:.6f}\n")
                else:
                    f.write("analysis_window_s:          NA\n")
                f.write("analysis_rule:              mean of all valid LC samples from 0 s to the configured duration\n")
                f.write(f"analysis_samples:           {mean_count}\n")
                if mean_value is None:
                    f.write("analysis_mean:              NA\n")
                    f.write("final_mvc_value:            NA\n")
                    f.write("offset:                     NA\n")
                else:
                    f.write(f"analysis_mean:              {mean_value:.6f} {unit}\n")
                    f.write(f"final_mvc_value:            {mean_value:.6f} {unit}\n")
                    f.write("offset:                     NA\n")
                f.write("=" * 44 + "\n")
        except Exception:
            pass

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â


# ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
# ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
# ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    _DR_MARKER_NAMES = {
        0: "stop",
        1: "rest",
        2: "extension_instruction",
        3: "flexion_instruction",
        4: "rest_instruction",
    }

    def _update_direction_reversal_marker(self, mode):
        try:
            marker = int(mode.marker_code())
        except Exception:
            marker = 1
        prev = int(getattr(self.recorder, "current_marker", 0) or 0)
        if prev == marker:
            return
        self.recorder.set_marker(marker)
        self._emit_marker(prev, marker)

    def direction_reversal_on_start(self):
        if not self._require_experiment_connections():
            return
        rest_time = self._parse_float(self.dr_rest_time_var, 5.0)
        dynamic_instruction_time = self._parse_float(self.dr_instruction_time_var, 5.0)
        rise_time = self._parse_float(self.dr_rise_time_var, 5.0)
        hold_time = self._parse_float(self.dr_hold_time_var, 5.0)
        down_time = self._parse_float(self.dr_down_time_var, 5.0)
        target_force = abs(self._parse_float(self.dr_target_force_var, 10.0))
        feedback_channel = self.dr_lc_channel_var.get().strip() or "Fx"
        try:
            total_reps = int(self.dr_reps_var.get())
        except ValueError:
            total_reps = 10
        damper_pwm = 255
        dynamics_enabled = bool(self.dr_dynamics_enabled_var.get())
        dynamics_angle = self._parse_float(self.dr_dynamics_angle_var, 10.0)
        dynamics_return_speed = 20.0

        if rest_time < 0.0 or (dynamics_enabled and dynamic_instruction_time <= 0.0):
            messagebox.showerror("Error", "Rest time must be >= 0 and dynamic instruction time must be > 0.")
            return
        if not dynamics_enabled:
            if rise_time < 0.0 or hold_time < 0.0 or down_time < 0.0 or (rise_time + hold_time + down_time) <= 0.0:
                messagebox.showerror("Error", "RAH ramp/hold/down times must be >= 0 and sum to > 0.")
                return
            if target_force <= 0.0:
                messagebox.showerror("Error", "Target force must be > 0.")
                return
            if feedback_channel not in self._LC_CHANNELS:
                messagebox.showerror("Error", "Feedback LC channel is invalid.")
                return
        instruction_time = dynamic_instruction_time if dynamics_enabled else (rise_time + hold_time + down_time)
        if total_reps <= 0:
            messagebox.showerror("Error", "Reps must be > 0.")
            return
        if dynamics_enabled and dynamics_angle <= 0.0:
            messagebox.showerror("Error", "Dynamics angle must be > 0.")
            return

        instructions = self._dr_instruction_sequence(total_reps)
        if not instructions:
            return
        instruction_sequence_log = "(" + ",".join(
            "e" if str(value).strip().lower().startswith("ext") else "f"
            for value in instructions
        ) + ")"
        h = 1 if self.handedness_var.get() == "Right" else -1

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        vicon_base = self._trial_prefixed_stem(f"DIRECTION_REVERSAL_{hand_str}")
        stem = self._trial_prefixed_stem(f"DIRECTION_REVERSAL_{hand_str}_{ts}")
        self._make_file_paths("direction_reversal", stem)
        self._set_vicon_capture_default(vicon_base)

        total_duration = total_reps * (rest_time + instruction_time) + rest_time
        self._write_log_header({
            "experiment": "direction_reversal",
            "hold_angle_deg": "current wrist angle at launch",
            "rest_time_s": f"{rest_time:.6f}",
            "instruction_time_s": f"{instruction_time:.6f}",
            "rah_ramp_up_time_s": f"{rise_time:.6f}",
            "rah_hold_time_s": f"{hold_time:.6f}",
            "rah_ramp_down_time_s": f"{down_time:.6f}",
            "target_force_value": f"{target_force:.6f}",
            "feedback_lc_channel": feedback_channel,
            "final_rest_time_s": f"{rest_time:.6f}",
            "total_reps": total_reps,
            "instructions": ",".join(f"{idx}:{value}" for idx, value in enumerate(instructions, start=1)),
            "damper_pwm": damper_pwm,
            "dynamics_mode": dynamics_enabled,
            "dynamics_angle_deg": f"{dynamics_angle:.6f}" if dynamics_enabled else "off",
            "dynamics_return_speed_deg_s": f"{dynamics_return_speed:.6f}" if dynamics_enabled else "off",
            "display_lowpass_enabled": int(bool(self.rah_display_lowpass_var.get())) if not dynamics_enabled else "n/a",
            "display_lowpass_hold_cutoff_hz": f"{RAH_DISPLAY_HOLD_LOWPASS_HZ:.6f}" if not dynamics_enabled else "n/a",
            "display_lowpass_other_cutoff_hz": f"{RAH_DISPLAY_OTHER_LOWPASS_HZ:.6f}" if not dynamics_enabled else "n/a",
            "total_duration_s": f"{total_duration:.6f}" if not dynamics_enabled else f"{total_duration:.6f}+auto_return",
            "markers": ", ".join(f"{code}={name}" for code, name in sorted(self._DR_MARKER_NAMES.items())),
        })
        if not dynamics_enabled:
            self._lock_rah_display_lowpass()

        self.dr_btn_start.config(state="disabled")
        self.dr_btn_stop.config(state="disabled")
        self.motion_status.set("DIRECTION REVERSAL - starting in 3...")
        self.dr_phase_var.set("starting...")

        def _launch():
            wrist_now = self._get_wrist()
            if dynamics_enabled:
                targets = []
                if any(value == "extension" for value in instructions):
                    targets.append(wrist_now + dynamics_angle)
                if any(value == "flexion" for value in instructions):
                    targets.append(wrist_now - dynamics_angle)
                if any(abs(value) > WRIST_LIMIT_DEG for value in targets):
                    self.dr_btn_start.config(state="normal")
                    self.dr_btn_stop.config(state="disabled")
                    self.motion_status.set("Idle")
                    self.dr_phase_var.set("-")
                    messagebox.showerror(
                        "Error",
                        f"Dynamics target would exceed wrist limit ({-WRIST_LIMIT_DEG:.0f} to +{WRIST_LIMIT_DEG:.0f} deg).",
                    )
                    return
            mode = DirectionReversalMode(
                hold_angle_deg=wrist_now,
                rest_time_s=rest_time,
                instruction_time_s=instruction_time,
                rise_time_s=rise_time,
                hold_time_s=hold_time,
                down_time_s=down_time,
                target_force_value=target_force,
                feedback_channel=feedback_channel,
                total_reps=total_reps,
                instructions=instructions,
                damper_pwm=damper_pwm,
                dynamics_enabled=dynamics_enabled,
                dynamics_angle_deg=dynamics_angle,
                dynamics_return_speed_deg_s=dynamics_return_speed,
            )
            mode.set_handedness(h)
            mode.reset()

            def _on_phase(_prev, new_phase):
                if not dynamics_enabled:
                    return
                self.after(0, lambda s=new_phase: self._direction_reversal_on_phase_ui(s))

            mode.on_phase_change = _on_phase
            mode.start(wrist_now)
            gui_log_details = {
                "reps": total_reps,
                "mode": "dynamics" if dynamics_enabled else "isometric",
                "sequence": instruction_sequence_log,
                "hold_angle_deg": f"{wrist_now:.3f}",
            }
            if dynamics_enabled:
                gui_log_details["target_angle_deg"] = f"{dynamics_angle:.3f}"
            else:
                gui_log_details["target_force"] = f"{target_force:.3f}"
                gui_log_details["lc_channel"] = feedback_channel
            self._append_gui_session_log(
                vicon_base,
                "Direction reversal",
                total_duration,
                gui_log_details,
            )
            self._start_primary_csv(mode)
            self._start_session_recording()
            self.recorder.set_marker(1)
            self._emit_marker(0, 1)
            self._dr_t0 = time.time()
            self._dr_mode_ref = mode
            self._dr_force_history = []
            if not dynamics_enabled:
                self._rah_history = []
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send(f"SET_DMP:{damper_pwm}")
            self.motion_runner.start(mode, reset=False)
            self.motion_status.set(
                f"DIRECTION REVERSAL - holding {wrist_now:+.1f} deg | damper={damper_pwm}")
            self.dr_phase_var.set(f"rep 1/{total_reps} | rest | remaining {rest_time:.1f}s")
            self.rec_status_var.set(self._session_record_status("Recording"))
            self.dr_btn_stop.config(state="normal")
            self._motion_polling = True
            self._direction_reversal_ui_loop()

        self.display.countdown(_launch)

    def _direction_reversal_on_phase_ui(self, phase: str):
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "direction_reversal":
            return
        damper = int(round(mode.params.get("damper_pwm", 255)))
        cue = getattr(mode, "current_instruction", "rest")
        if phase == "instruction" and cue in ("extension", "flexion"):
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self.serial_worker.send("TORQUE_OFF")
            self.serial_worker.send("SET_DMP:0")
            target = mode.target_angle_for_current_instruction() if hasattr(mode, "target_angle_for_current_instruction") else 0.0
            self.motion_status.set(
                f"DIRECTION REVERSAL dynamics - {cue}, move toward {target:+.1f} deg")
            return
        self.serial_worker.send("TORQUE_ON")
        self.serial_worker.send("SET_DMP:0" if phase == "return" else f"SET_DMP:{damper}")
        if phase == "return":
            self.motion_status.set(
                f"DIRECTION REVERSAL dynamics - returning to {mode.hold_pos:+.1f} deg")

    def direction_reversal_on_stop(self, auto_complete: bool = False):
        self._motion_polling = False
        self._unlock_rah_display_lowpass()
        prev_marker = int(getattr(self.recorder, "current_marker", 0) or 0)
        if prev_marker != 0:
            self._emit_marker(prev_marker, 0)
        self.recorder.set_marker(0)
        rows = self._stop_primary_csv()
        self._stop_session_recording()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.dr_phase_var.set("-")
        self.rec_status_var.set(self._session_record_status("Saved", rows))
        self.dr_btn_start.config(state="normal")
        self.dr_btn_stop.config(state="disabled")
        self._draw_canvas_idle()
        self._draw_rah_canvas_idle()
        self._dr_mode_ref = None
        self._dr_t0 = None
        self._trial_increment()

    def _direction_reversal_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "direction_reversal":
            self.after(interval_ms, self._direction_reversal_ui_loop)
            return
        if mode.done:
            self.after(100, lambda: self.direction_reversal_on_stop(auto_complete=True))
            return

        self._update_direction_reversal_marker(mode)
        rows = self._primary_csv_rows()
        elapsed = time.time() - self._dr_t0 if self._dr_t0 else 0.0
        if not mode.dynamics_enabled():
            lc_sample, _host_ts = self.lc_worker.ring.get_latest()
            self._draw_rah_live(lc_sample, elapsed)
        total_reps = mode.total_reps() if hasattr(mode, "total_reps") else int(self.dr_reps_var.get())
        remaining = mode.phase_remaining() if hasattr(mode, "phase_remaining") else 0.0
        instruction = getattr(mode, "current_instruction", "rest")
        phase = getattr(mode, "sub_phase", "rest")
        label = instruction if phase == "instruction" else phase
        if phase == "instruction" and hasattr(mode, "target_angle_for_current_instruction") and mode.dynamics_enabled():
            target = mode.target_angle_for_current_instruction()
            label = f"{instruction} to {target:+.1f} deg"
        self.dr_phase_var.set(
            f"rep {getattr(mode, 'rep', 0) + 1}/{total_reps} | {label} | remaining {remaining:.1f}s")
        self.rec_status_var.set(self._session_record_status("Recording", rows, elapsed))
        self.after(interval_ms, self._direction_reversal_ui_loop)

    def pm_on_start(self):
        if not self._require_experiment_connections():
            return
        start      = self._parse_float(self.pm_start_var,       0.0)
        target     = self._parse_float(self.pm_target_var,     40.0)
        speed      = self._parse_float(self.pm_speed_var,      20.0)
        hold       = self._parse_float(self.pm_hold_var,        2.0)
        rest       = self._parse_float(self.pm_rest_var,        2.0)
        h          = 1 if self.handedness_var.get() == "Right" else -1
        try:
            total_reps = int(self.pm_reps_var.get())
        except ValueError:
            total_reps = 1

        if abs(start) > WRIST_LIMIT_DEG or abs(target) > WRIST_LIMIT_DEG:
            messagebox.showerror("Error", f"Start/end points must be between {-WRIST_LIMIT_DEG:.0f} and +{WRIST_LIMIT_DEG:.0f} deg.")
            return
        if abs(target - start) < 1e-9:
            messagebox.showerror("Error", "Start point and end point must be different.")
            return
        if speed <= 0:
            messagebox.showerror("Error", "Speed must be > 0.")
            return

        mode = PassiveMovementMode(start_deg=start, target_deg=target,
                                   speed_deg_s=speed, hold_duration_s=hold,
                                   rest_time_s=rest, total_reps=total_reps)
        mode.set_handedness(h)
        mode.reset()

        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if h == 1 else "L"
        range_str = f"{start:+g}to{target:+g}deg".replace("+", "p").replace("-", "m")
        vicon_base = self._trial_prefixed_stem(f"PM_{hand_str}_{range_str}")
        stem     = self._trial_prefixed_stem(f"PM_{hand_str}_{range_str}_{ts}")
        self._make_file_paths("passive_movement", stem)
        self._set_vicon_capture_default(vicon_base)
        self._write_log_header({"experiment":        "passive_movement",
                                 "start_wrist_deg":  start,
                                 "end_wrist_deg":    target,
                                 "speed_wrist_deg_s": speed,
                                 "hold_duration_s":  hold,
                                 "rest_time_s":      rest,
                                 "damper_pwm":       0,
                                 "total_reps":       total_reps})

        self.pm_btn_start.config(state="disabled")
        self.pm_btn_stop.config(state="disabled")
        self.motion_status.set("PASSIVE MOVE - starting in 3...")

        def _launch():
            def _on_phase(prev_sp, new_sp):
                prev_m = mode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m  = mode.PHASE_TO_MARKER.get(new_sp, 0)
                # set float marker in CSV
                float_marker = {
                    "go":     1.0,
                    "hold":   2.0,
                    "return": 3.0,
                    "rest":   0.0,
                    "done":   0.0,
                }.get(new_sp, float(new_m))
                self.recorder.set_marker(float_marker)
                if prev_m != new_m or (prev_sp == "positioning" and new_sp == "go"):
                    self._emit_marker(prev_m, new_m)
                total = int(round(mode.params.get("total_reps", 1)))
                self.after(0, lambda s=new_sp: self.pm_phase_var.set(
                    f"phase={s}  |  rep={mode.rep+1}/{total}  {start:+g} -> {target:+g} deg"))

            self._start_primary_csv(mode)
            self._start_session_recording()
            self.motion_runner.start(mode)
            mode.on_phase_change   = _on_phase
            self.serial_worker.send("TORQUE_ON")
            wrist_now = self._get_wrist()
            positioning_time = abs(wrist_now - start) / max(speed, 0.001)
            move_time = abs(target - start) / max(speed, 0.001)
            planned_duration = positioning_time + total_reps * (2.0 * move_time + hold + rest)
            self._append_gui_session_log(
                vicon_base,
                "Passive Movement",
                planned_duration,
                {
                    "start_deg": self._compact_gui_log_number(start),
                    "end_deg": self._compact_gui_log_number(target),
                    "reps": total_reps,
                    "vel_deg_s": self._compact_gui_log_number(speed),
                },
            )
            mode.start_positioning(wrist_now)
            phase = mode.sub_phase
            self.motion_status.set(
                f"PASSIVE MOVE - positioning to start {start:+g} deg at {speed:g} deg/s"
                if phase == "positioning"
                else f"PASSIVE MOVE - driving from {start:+g} to {target:+g} deg at {speed:g} deg/s")
            self.pm_phase_var.set(f"phase={phase}  |  rep=1/{total_reps}  {start:+g} -> {target:+g} deg")
            self.rec_status_var.set(self._session_record_status("Recording"))
            self.pm_btn_stop.config(state="normal")
            self._motion_polling = True
            self._pm_ui_loop()

        self.display.countdown(_launch)

    def pm_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._emit_marker(int(cur), 0)
        rows = self._stop_primary_csv()
        self._stop_session_recording()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.pm_phase_var.set("-")
        self.rec_status_var.set(self._session_record_status("Saved", rows))
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
                f"PASSIVE MOVE - hold {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "rest":
            remaining = max(0.0, rest_dur - mode.rest_t)
            self.motion_status.set(
                f"PASSIVE MOVE - rest {remaining:.1f}s  |  rep {mode.rep+1}/{total}")
        elif mode.sub_phase == "positioning":
            start = mode.current_start() if hasattr(mode, "current_start") else float(mode.params.get("start_deg", 0.0))
            self.motion_status.set(
                f"PASSIVE MOVE - positioning to start {start:+.1f} deg  |  wrist={wrist:+.1f} deg")
        rows = self._primary_csv_rows()
        self.rec_status_var.set(self._session_record_status("Recording", rows))
        self._draw_pm_canvas(mode, wrist)
        self.after(interval_ms, self._pm_ui_loop)
    
    # # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def _update_velocity_test_result_cells(self, results):
        for index, result in enumerate(results):
            if index >= len(self.velocity_test_rows):
                break
            peak_velocity = result.get("measured_peak_velocity_deg_s")
            peak_angle = result.get("measured_peak_displacement_deg")
            self.velocity_test_rows[index]["velocity_max"].set(
                "--" if peak_velocity is None else f"{float(peak_velocity):.3f}")
            self.velocity_test_rows[index]["angle_max"].set(
                "--" if peak_angle is None else f"{float(peak_angle):.3f}")

    def _write_velocity_test_results(self, results):
        path = getattr(self, "_velocity_test_results_path", "")
        if not path:
            return
        headers = [
            "event", "scheduled_time_s", "actual_start_s", "start_delay_s",
            "direction", "commanded_velocity_deg_s", "measured_peak_velocity_deg_s",
            "commanded_angle_deg", "measured_peak_displacement_deg",
            "base_wrist_deg", "target_wrist_deg", "peak_wrist_deg",
            "completed_time_s", "status",
        ]

        def value(result, key):
            item = result.get(key)
            if item is None:
                return ""
            if isinstance(item, float):
                return f"{item:.6f}"
            return item

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for result in results:
                row = dict(result)
                if row.get("status") in ("pending", "active"):
                    row["status"] = "aborted"
                writer.writerow([value(row, key) for key in headers])

    def velocity_test_on_start(self):
        if not self.serial_worker.connected:
            messagebox.showerror("Error", "Connect Exo Serial first.")
            return
        events, interval, error = self._velocity_test_inputs()
        if error:
            messagebox.showerror("Invalid velocity test", error)
            return
        final_event = events[-1]
        total_duration = (
            VelocityTestMode.FIRST_EVENT_DELAY_S
            + (len(events) - 1) * interval
            + 2.0 * final_event["angle_deg"] / final_event["velocity_deg_s"]
            + VelocityTestMode.FINAL_SETTLE_DELAY_S
        )
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hand_str = "R" if self.handedness_var.get() == "Right" else "L"
        vicon_base = self._trial_prefixed_stem(f"TEST_VELOCITY_{hand_str}")
        stem = self._trial_prefixed_stem(f"TEST_VELOCITY_{hand_str}_{ts}")
        self._make_file_paths("test_velocity", stem)
        self._set_vicon_capture_default(vicon_base)
        velocity_schedule, position_schedule = self._velocity_test_schedule_strings(events)
        event_schedule = ",".join(
            f"({VelocityTestMode.FIRST_EVENT_DELAY_S + index * interval:g},"
            f"{event['velocity_deg_s']:g},"
            f"{event['angle_deg']:g},{event['direction']})"
            for index, event in enumerate(events))
        self.velocity_test_btn_start.config(state="disabled")
        self.velocity_test_btn_stop.config(state="disabled")
        self.velocity_test_phase_var.set("starting in 3...")
        for row in self.velocity_test_rows:
            row["velocity_max"].set("--")
            row["angle_max"].set("--")

        def _launch():
            if not self.serial_worker.connected:
                messagebox.showerror("Error", "Exo Serial disconnected before launch.")
                self.velocity_test_btn_start.config(state="normal")
                self.velocity_test_phase_var.set("-")
                return
            wrist_now = self._get_wrist()
            for index, event in enumerate(events, start=1):
                target = wrist_now + (1.0 if event["direction"] == "+" else -1.0) * event["angle_deg"]
                if target < -WRIST_LIMIT_DEG or target > WRIST_LIMIT_DEG:
                    messagebox.showerror(
                        "Invalid velocity test",
                        f"Row {index}: current angle {wrist_now:.3f} deg plus the requested "
                        f"perturbation would exceed the wrist limit.")
                    self.velocity_test_btn_start.config(state="normal")
                    self.velocity_test_phase_var.set("-")
                    return
            mode = VelocityTestMode(events=events, interval_s=interval)
            mode.start(wrist_now)
            self._write_log_header({
                "experiment": "test_velocity",
                "base_wrist_deg": f"{wrist_now:.6f}",
                "perturbation_count": len(events),
                "first_perturbation_delay_s": f"{VelocityTestMode.FIRST_EVENT_DELAY_S:.6f}",
                "fixed_interval_s": f"{interval:.6f}",
                "final_settle_delay_s": f"{VelocityTestMode.FINAL_SETTLE_DELAY_S:.6f}",
                "total_duration_s": f"{total_duration:.6f}",
                "event_schedule_time_velocity_angle_direction": event_schedule,
            })
            self._append_gui_session_log(
                vicon_base,
                "Test Velocity",
                total_duration,
                {
                    "reps": len(events),
                    "velocity": velocity_schedule,
                    "position": position_schedule,
                },
            )
            self._start_primary_csv(mode)
            self._start_session_recording()
            self.recorder.set_marker(1)
            if self._trigger_ready():
                self._emit_marker(0, 1)
            self._velocity_test_mode_ref = mode
            self._velocity_test_t0 = time.time()
            self.serial_worker.send("ZERO_VEL")
            self.serial_worker.send("MODE_EXT")
            self.serial_worker.send("TORQUE_ON")
            self.serial_worker.send("SET_DMP:255")
            self.motion_runner.start(mode, reset=False)
            self.velocity_test_btn_stop.config(state="normal")
            self.velocity_test_phase_var.set(
                f"waiting | 0/{len(events)} | t=0.0/{total_duration:.1f}s")
            self.motion_status.set(
                f"TEST VELOCITY - holding {wrist_now:.3f} deg | {len(events)} perturbations")
            self.rec_status_var.set(self._session_record_status("Recording"))
            self._motion_polling = True
            self._velocity_test_ui_loop()

        self.display.countdown(_launch)

    def velocity_test_on_stop(self, auto_complete: bool = False):
        mode = getattr(self, "_velocity_test_mode_ref", None)
        self._motion_polling = False
        prev_marker = int(getattr(self.recorder, "current_marker", 0) or 0)
        if prev_marker != 0 and self._trigger_ready():
            self._emit_marker(prev_marker, 0)
        self.recorder.set_marker(0)
        self.serial_worker.send("ZERO_VEL")
        self.serial_worker.send("MODE_EXT")
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        rows = self._stop_primary_csv()
        self._stop_session_recording()
        results = mode.results_snapshot() if mode is not None else []
        self._update_velocity_test_result_cells(results)
        try:
            self._write_velocity_test_results(results)
        except Exception as exc:
            messagebox.showerror("Velocity test result error", str(exc))
        self._copy_to_subdir()
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.velocity_test_btn_start.config(state="normal")
        self.velocity_test_btn_stop.config(state="disabled")
        completed = sum(1 for result in results if result.get("status") == "complete")
        suffix = "complete" if auto_complete else "stopped"
        self.velocity_test_phase_var.set(f"{suffix} | {completed}/{len(results)} complete")
        self.motion_status.set("Idle")
        self.rec_status_var.set(self._session_record_status("Saved", rows))
        self._velocity_test_mode_ref = None
        self._velocity_test_t0 = None
        if mode is not None:
            self._trial_increment()

    def _velocity_test_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "velocity_test":
            self.after(interval_ms, self._velocity_test_ui_loop)
            return
        results = mode.results_snapshot()
        self._update_velocity_test_result_cells(results)
        elapsed = time.time() - self._velocity_test_t0 if self._velocity_test_t0 else 0.0
        complete_count = sum(
            1 for result in results if result.get("status") not in ("pending", "active"))
        self.velocity_test_phase_var.set(
            f"{mode.sub_phase} | {complete_count}/{len(results)} | "
            f"t={elapsed:.1f}/{mode.total_duration():.1f}s")
        self.rec_status_var.set(
            self._session_record_status("Recording", self._primary_csv_rows(), elapsed))
        if mode.done:
            self._motion_polling = False
            self.after(100, lambda: self.velocity_test_on_stop(auto_complete=True))
            return
        self.after(interval_ms, self._velocity_test_ui_loop)


    def rah_on_start(self):
        if not self._require_experiment_connections():
            return
        (config, error) = self._rah_schedule_config()
        if error:
            messagebox.showerror('Invalid RAH schedule', error)
            return
        generated = self._rah_generated_config
        if generated is None or generated.get('signature') != config.get('signature'):
            messagebox.showerror('Generate diagram first', 'The RAH settings changed. Generate the diagram again before starting.')
            return
        direction_choice = config['direction_choice']
        direction = self._direction_choice_to_sign(direction_choice)
        mvc_value = config['mvc_value']
        channel = config['channel']
        profile_points = list(config['points'])
        perturbation_events = list(config['events'])
        profile_schedule_log = self._format_rah_profile_schedule(profile_points)
        perturbation_schedule_log = self._format_rah_perturbation_schedule(perturbation_events)
        target_pct = max((value for (_time_s, value) in profile_points))
        total_duration = profile_points[-1][0]
        hold_step_active = bool(perturbation_events)
        hold_step_count = len(perturbation_events)
        perturbation_speeds = [event[2] for event in perturbation_events]
        perturbation_angles = [event[3] for event in perturbation_events]
        hold_step_speed_log = self._compact_gui_log_number(perturbation_speeds[0]) if perturbation_speeds and all((abs(value - perturbation_speeds[0]) <= 1e-09 for value in perturbation_speeds)) else 'mixed' if perturbation_speeds else ''
        hold_step_angle_log = self._compact_gui_log_number(perturbation_angles[0]) if perturbation_angles and all((abs(value - perturbation_angles[0]) <= 1e-09 for value in perturbation_angles)) else 'mixed' if perturbation_angles else ''
        h = 1 if self.handedness_var.get() == 'Right' else -1
        self._lock_rah_display_lowpass()
        mode = RAHMode(direction=direction, mvc_value=mvc_value, profile_points=profile_points, perturbation_events=perturbation_events, hold_step_enabled=1 if hold_step_active else 0)
        mode.set_handedness(h)
        mode.reset()
        total_reps = mode.custom_rep_count()
        mode.params['total_reps'] = total_reps
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        hand_str = 'R' if h == 1 else 'L'
        dir_str = self._direction_choice_to_code(direction_choice)
        experiment_name = 'ramp_and_hold'
        display_name = 'RAH_LC'
        vicon_base = self._trial_prefixed_stem(f"{'RAH'}_{hand_str}_{dir_str}")
        stem = self._trial_prefixed_stem(f"{'RAH'}_{hand_str}_{dir_str}_{ts}")
        self._make_file_paths('ramp_and_hold', stem)
        self._set_vicon_capture_default(vicon_base)
        self._write_log_header({'experiment': experiment_name, 'direction': self._direction_choice_to_label(direction_choice), 'mvc_value': f'{mvc_value:.6f}', 'lc_channel': channel, 'profile_schedule_time_pct_mvc': profile_schedule_log, 'max_target_pct_mvc': f'{target_pct:.6f}', 'total_reps': total_reps, 'perturbation_enabled': int(hold_step_active), 'perturbation_schedule_time_direction_velocity_angle': perturbation_schedule_log, 'perturbation_count': hold_step_count, 'display_lowpass_enabled': int(self._rah_display_lowpass_active), 'display_lowpass_hold_cutoff_hz': f'{RAH_DISPLAY_HOLD_LOWPASS_HZ:.6f}', 'display_lowpass_other_cutoff_hz': f'{RAH_DISPLAY_OTHER_LOWPASS_HZ:.6f}', 'total_duration_s': f'{total_duration:.6f}'})
        self._write_rah_perturbation_table(experiment_name=experiment_name, display_name=display_name, direction=direction, direction_choice=direction_choice, hold_step_active=hold_step_active, profile_schedule=profile_schedule_log, perturbation_schedule=perturbation_schedule_log, custom_events=perturbation_events)
        start_btn = self.rah_btn_start
        stop_btn = self.rah_btn_stop
        phase_var = self.rah_phase_var
        start_btn.config(state='disabled')
        stop_btn.config(state='disabled')
        self.motion_status.set(f'{display_name} - starting in 3...')

        def _launch():
            wrist_now = self._get_wrist()
            gui_details = {'mvc_value': f'{mvc_value:.3f}', 'mvc_pct': f'{target_pct:.3f}', 'diagram_schedule': profile_schedule_log, 'lc_channel': channel, 'reps': total_reps, 'wrist_angle_deg': f'{wrist_now:.3f}'}
            if hold_step_active:
                gui_details.update({'perturbation_count': hold_step_count, 'perturbation_angle_deg': hold_step_angle_log, 'perturbation_speed_deg_s': hold_step_speed_log, 'perturbation_abs_times': perturbation_schedule_log})
            extra_headers = None
            extra_provider = None
            self._append_gui_session_log(vicon_base, display_name, total_duration, gui_details)
            self._start_primary_csv(mode, extra_headers=extra_headers, extra_row_provider=extra_provider)
            self._start_session_recording()
            self.recorder.set_marker(1)
            self._emit_marker(0, 1)
            self._rah_t0 = time.time()
            self._rah_start_wrist_deg = wrist_now
            self._rah_history = []
            self._rah_mode_ref = mode
            self.serial_worker.send('ZERO_VEL')
            self.serial_worker.send('MODE_EXT')
            self.serial_worker.send('TORQUE_ON')
            self.serial_worker.send('SET_DMP:255')
            mode.start(wrist_now)
            self.motion_runner.start(mode, reset=False)
            self.motion_status.set(f'{display_name} - custom schedule | peak={target_pct:.1f}% MVC | duration={total_duration:g}s')
            phase_var.set(f'trial 1/{total_reps} | t=0/{total_duration:g}s')
            self.rec_status_var.set(self._session_record_status('Recording'))
            stop_btn.config(state='normal')
            self._motion_polling = True
            self._rah_ui_loop()
        self.display.countdown(_launch)


    def rah_on_stop(self, auto_complete: bool = False):
        self._motion_polling = False
        prev_marker = int(getattr(self.recorder, "current_marker", 0) or 0)
        if prev_marker != 0:
            self._emit_marker(prev_marker, 0)
        self.recorder.set_marker(0)
        rows = self._stop_primary_csv()
        self._rah_finish_stop(rows)

    def _rah_finish_stop(self, rows: int):
        self._unlock_rah_display_lowpass()
        self._stop_session_recording()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send('TORQUE_OFF')
        self.serial_worker.send('SET_DMP:0')
        self.motion_status.set('Idle')
        self.rah_phase_var.set('-')
        self.rec_status_var.set(self._session_record_status('Saved', rows))
        self.rah_btn_start.config(state='normal' if self._rah_diagram_ready() else 'disabled')
        self.rah_btn_stop.config(state='disabled')
        self._draw_rah_canvas_idle()
        self._rah_start_wrist_deg = None
        self._trial_increment()


    def _rah_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != 'ramp_and_hold':
            self.after(interval_ms, self._rah_ui_loop)
            return
        if mode.done:
            prev_marker = int(getattr(self.recorder, 'current_marker', 0) or 0)
            if prev_marker not in (0, 4):
                self.recorder.set_marker(4)
                self._emit_marker(prev_marker, 4)
            self.after(100, lambda : self.rah_on_stop(auto_complete=True))
            return
        self._update_rah_phase_marker(mode)
        (lc_sample, _) = self.lc_worker.ring.get_latest()
        t_elapsed = time.time() - self._rah_t0 if self._rah_t0 else 0.0
        self._draw_rah_live(lc_sample, t_elapsed)
        rows = self._primary_csv_rows()
        total_reps = int(round(float(mode.params.get('total_reps', 3))))
        total_duration = mode.total_duration() if hasattr(mode, 'total_duration') else 0.0
        phase_var = self.rah_phase_var
        phase_var.set(f"trial {getattr(mode, 'rep', 0) + 1}/{total_reps} | {mode.sub_phase} | t={t_elapsed:.1f}/{total_duration:.1f}s")
        self.rec_status_var.set(self._session_record_status('Recording', rows, t_elapsed))
        self.after(interval_ms, self._rah_ui_loop)

# ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Active Movement handlers
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def _follow_display_rep_info(self, mode):
        try:
            segments = mode.params.get("path_segments") or []
        except Exception:
            segments = []
        if segments:
            total = max(1, int(round(float(mode.params.get("total_reps", max(1, len(segments) // 2))))))
            rep = min(total, max(1, int(getattr(mode, "rep", 0)) + 1))
            return rep, total
        total = int(round(mode.params.get("total_reps", 1)))
        rep = int(getattr(mode, "rep", 0)) + 1
        if getattr(mode, "done", False):
            rep = min(rep, total)
        return rep, total

    def follow_on_start(self):
        if not self._require_experiment_connections():
            return
        hold = self._parse_float(self.follow_hold_var, 2.0)
        rest = self._parse_float(self.follow_rest_var, 2.0)
        damper = max(0, min(255, int(round(self._parse_float(self.follow_damper_var, 0.0)))))
        self.follow_damper_var.set(damper)
        h = 1 if self.handedness_var.get() == 'Right' else -1
        change_mode = self._follow_change_mode()
        if not self._sync_follow_string_inputs_for_mode(change_mode):
            return
        try:
            total_reps = int(self.follow_reps_var.get())
        except ValueError:
            total_reps = 1
        total_reps = max(1, total_reps)
        self._refresh_follow_rep_inputs()
        try:
            path_segments = self._follow_path_segments_from_rows(total_reps, change_mode)
        except ValueError as exc:
            messagebox.showerror('Error', str(exc) or 'Enter numeric path and speed values.')
            return
        start = float(path_segments[0]['start'])
        target = float(path_segments[0]['end'])
        speed = float(path_segments[0].get('speed', self._parse_float(self.follow_speed_var, 20.0)))
        axis_points = [v for segment in path_segments for v in (float(segment['start']), float(segment['end']))]
        speeds_to_check = [float(segment['speed']) for segment in path_segments]
        if any((abs(v) > WRIST_LIMIT_DEG for v in axis_points)):
            messagebox.showerror('Error', f'Start/end points must be between {-WRIST_LIMIT_DEG:.0f} and +{WRIST_LIMIT_DEG:.0f} deg.')
            return
        if any((abs(segment['end'] - segment['start']) < 1e-09 for segment in path_segments)):
            messagebox.showerror('Error', 'Every path segment must have different start and end points.')
            return
        if any((v <= 0 for v in speeds_to_check)):
            messagebox.showerror('Error', 'Every guide speed must be > 0.')
            return
        view_min = max(-WRIST_LIMIT_DEG, min(axis_points) - 10.0)
        view_max = min(WRIST_LIMIT_DEG, max(axis_points) + 10.0)
        if view_max <= view_min:
            (view_min, view_max) = (-WRIST_LIMIT_DEG, WRIST_LIMIT_DEG)
        mode = FollowMode(start_deg=start, target_deg=target, speed_deg_s=speed, path_segments=path_segments, hold_time_s=hold, rest_time_s=rest, damper_pwm=damper, total_reps=total_reps, view_min_deg=view_min, view_max_deg=view_max)
        mode.set_handedness(h)
        mode.reset()
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        hand_str = 'R' if h == 1 else 'L'
        range_str = f'{start:+g}toPATHdeg'.replace('+', 'p').replace('-', 'm')
        vicon_base = self._trial_prefixed_stem(f'VOLUNTARY_{hand_str}_{range_str}')
        stem = self._trial_prefixed_stem(f'VOLUNTARY_{hand_str}_{range_str}_{ts}')
        self._make_file_paths('voluntary_movement', stem)
        self._set_vicon_capture_default(vicon_base)
        segment_plan = ','.join((f"{s['kind']}:{s['start']:g}->{s['end']:g}@{s['speed']:g}" for s in path_segments))
        velocity_plan = self._format_follow_velocity_string_from_segments(path_segments)
        position_plan = self._format_follow_position_string_from_segments(path_segments)
        if change_mode in ('change velocity', 'change all'):
            self.follow_velocity_plan_var.set(velocity_plan)
        if change_mode in ('change end points', 'change all'):
            self.follow_position_plan_var.set(position_plan)
        self._write_log_header({'experiment': 'voluntary_movement', 'start_wrist_deg': start, 'end_wrist_deg': target, 'speed_deg_s': speed, 'per_rep_mode': change_mode, 'custom_path': self.follow_path_var.get().strip(), 'path_segments': segment_plan, 'velocity_string': velocity_plan if change_mode in ('change velocity', 'change all') else '', 'position_string': position_plan if change_mode in ('change end points', 'change all') else '', 'per_rep_end_points_deg': position_plan if change_mode in ('change end points', 'change all') else '', 'per_rep_speeds_deg_s': velocity_plan if change_mode in ('change velocity', 'change all') else ','.join((f'{v:g}' for v in speeds_to_check)), 'guide_speed_switch_marker': '5 on guide speed changes', 'guide_speed_switch_plan': segment_plan, 'end_point_switch_plan': segment_plan, 'hold_time_s': hold, 'rest_time_s': rest, 'damper_pwm': damper, 'total_reps': total_reps})
        self._set_follow_button_states(start_state='disabled', stop_state='disabled')
        self.motion_status.set('VOLUNTARY MOVE - starting in 3...')

        def _launch():

            def _on_phase(prev_sp, new_sp):
                prev_m = FollowMode.PHASE_TO_MARKER.get(prev_sp, 0)
                new_m = FollowMode.PHASE_TO_MARKER.get(new_sp, 0)
                self.recorder.set_marker(new_m)
                if prev_m != new_m or (prev_sp == 'positioning' and new_sp == 'go'):
                    self._emit_marker(prev_m, new_m)
                if new_sp in ('go', 'return') and prev_sp in ('idle', 'positioning'):
                    self.serial_worker.send('TORQUE_OFF')
                    self.serial_worker.send(f'SET_DMP:{damper}')
                (rep, total) = self._follow_display_rep_info(mode)
                self.after(0, lambda s=new_sp, r=rep, n=total: self.follow_phase_var.set(f'phase={s}  |  rep={r}/{n}'))
                if new_sp == 'done':
                    self.after(200, self.follow_on_stop)
            self._start_primary_csv(mode)
            self._start_session_recording()
            self.motion_runner.start(mode)
            mode.on_phase_change = _on_phase
            self.serial_worker.send('TORQUE_ON')
            self.serial_worker.send('SET_DMP:0')
            wrist_now = self._get_wrist()
            planned_duration = self._follow_planned_duration(path_segments, hold, rest, wrist_now, start, speed)
            self._append_gui_session_log(vicon_base, 'Voluntary Movement', planned_duration, self._follow_gui_log_details(change_mode, total_reps, start, target, speed, path_segments))
            mode.start_positioning(wrist_now)
            phase = mode.sub_phase
            first_target = mode.current_target() if hasattr(mode, 'current_target') else target
            first_speed = mode.current_speed() if hasattr(mode, 'current_speed') else speed
            self.motion_status.set(f'VOLUNTARY MOVE - positioning to start {start:+.1f} deg at {first_speed:.1f} deg/s' if phase == 'positioning' else f'VOLUNTARY MOVE - track the yellow ball from {start:+.1f} to {first_target:+.1f} deg')
            self.follow_phase_var.set(f'phase={phase}  |  rep=1/{total_reps}')
            self.rec_status_var.set(self._session_record_status('Recording'))
            self._set_follow_button_states(stop_state='normal')
            self._motion_polling = True
            self._follow_ui_loop()
        self.display.countdown(_launch)

    def follow_on_stop(self):
        self._motion_polling = False
        cur = self.recorder.current_marker
        if cur != 0:
            self._emit_marker(int(cur), 0)
        rows = self._stop_primary_csv()
        self._stop_session_recording()
        self._copy_to_subdir()
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        self.serial_worker.send("TORQUE_OFF")
        self.serial_worker.send("SET_DMP:0")
        self.motion_status.set("Idle")
        self.follow_phase_var.set("-")
        self.rec_status_var.set(self._session_record_status("Saved", rows))
        self._set_follow_button_states(start_state="normal", stop_state="disabled")
        self._draw_canvas_idle()
        self._trial_increment()

    def _follow_ui_loop(self, interval_ms=100):
        if not self._motion_polling:
            return
        mode = self.motion_runner.active_mode
        if mode is None or mode.name != "follow":
            self.after(interval_ms, self._follow_ui_loop)
            return

        wrist = self._get_wrist()
        rep, total = self._follow_display_rep_info(mode)
        hold_dur = float(mode.params.get("hold_time_s", 2.0))
        rest_dur = float(mode.params.get("rest_time_s", 2.0))
        target = mode.current_target() if hasattr(mode, "current_target") else float(mode.params.get("target_deg", 30.0))
        speed = mode.current_speed() if hasattr(mode, "current_speed") else float(mode.params.get("speed_deg_s", 20.0))

        if mode.sub_phase == "go":
            self.motion_status.set(
                f"VOLUNTARY MOVE - move with the yellow ball to {target:+.1f} deg at {speed:.1f} deg/s  |  rep {rep}/{total}")
        elif mode.sub_phase == "hold":
            remaining = max(0.0, hold_dur - mode.hold_t)
            self.motion_status.set(
                f"VOLUNTARY MOVE - hold at end {remaining:.1f}s  |  rep {rep}/{total}")
        elif mode.sub_phase == "return":
            self.motion_status.set(
                f"VOLUNTARY MOVE - return with the yellow ball at {speed:.1f} deg/s  |  rep {rep}/{total}")
        elif mode.sub_phase == "rest":
            remaining = max(0.0, rest_dur - mode.rest_t)
            self.motion_status.set(
                f"VOLUNTARY MOVE - rest {remaining:.1f}s  |  rep {rep}/{total}")
        elif mode.sub_phase == "positioning":
            start = mode.current_start() if hasattr(mode, "current_start") else float(mode.params.get("start_deg", 0.0))
            self.motion_status.set(
                f"VOLUNTARY MOVE - positioning to start {start:+.1f} deg  |  wrist={wrist:+.1f} deg")

        rows = self._primary_csv_rows()
        self.rec_status_var.set(self._session_record_status("Recording", rows))
        self._draw_follow_canvas(mode, wrist)
        self.after(interval_ms, self._follow_ui_loop)

    def _draw_canvas_idle(self):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        c.create_text(W // 2, H // 2, text="Press Start to begin",
                      fill="#334", font=("Segoe UI", 11))
        
    def _draw_pm_canvas(self, mode, wrist):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        start  = self._parse_float(self.pm_start_var, 0.0)
        target = self._parse_float(self.pm_target_var, 40.0)
        speed  = self._parse_float(self.pm_speed_var, 20.0)
        view_min = max(-WRIST_LIMIT_DEG, min(start, target) - 10.0)
        view_max = min(WRIST_LIMIT_DEG, max(start, target) + 10.0)
        if view_max <= view_min:
            view_min, view_max = -WRIST_LIMIT_DEG, WRIST_LIMIT_DEG
        r_flex = view_min
        span = max(1.0, view_max - view_min)
        cy = H // 2; margin = 40
        h  = 1 if self.handedness_var.get() == "Right" else -1
        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        def _clamp(deg):
            return max(view_min, min(view_max, deg))
        c.create_line(margin, cy, W - margin, cy, fill="#444", width=3)
        ticks = [(view_min, f"{view_min:+.0f} deg")]
        if view_min < 0 < view_max:
            ticks.append((0.0, "0 deg"))
        ticks.append((view_max, f"{view_max:+.0f} deg"))
        for deg, label in ticks:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#555", width=2)
            c.create_text(x, cy+22, text=label, fill="#555", font=("Segoe UI", 9))
        ext_x  = W - margin if h >= 0 else margin
        flex_x = margin     if h >= 0 else W - margin
        c.create_text(ext_x,  cy-22, text="EXT",  fill="#00e676", font=("Segoe UI", 8, "bold"))
        c.create_text(flex_x, cy-22, text="FLEX", fill="#ff9800", font=("Segoe UI", 8, "bold"))
        sx0 = _x(_clamp(start))
        c.create_line(sx0, cy-48, sx0, cy+48, fill="#40c4ff", width=2, dash=(4, 4))
        c.create_text(sx0, cy+62, text=f"S={start:+.1f} deg", fill="#40c4ff", font=("Segoe UI", 8))
        tx = _x(_clamp(target))
        c.create_line(tx, cy-55, tx, cy+55, fill="#ffd700", width=2, dash=(6, 4))
        c.create_text(tx, cy-65, text=f"E={target:+.1f} deg", fill="#ffd700", font=("Segoe UI", 8))
        if mode.sub_phase in ("positioning", "go", "hold", "return") and mode.drive_pos is not None:
            sx = _x(_clamp(mode.drive_pos))
            c.create_oval(sx-12, cy-12, sx+12, cy+12, fill="#ffd700", outline="#f9a825", width=2)
        rx = _x(_clamp(wrist))
        c.create_oval(rx-15, cy-15, rx+15, cy+15, fill="#ff1744", outline="#d50000", width=3)
        colors = {"idle": "#aaa", "positioning": "#40c4ff", "go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b", "done": "#aaa"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 14, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 11, "bold"))
        c.create_text(W//2, H - 10, text=f"speed: {speed:.1f} wrist deg/s", fill="#888", font=("Segoe UI", 8))
        self.motion_info_var.set(
            f"PASSIVE MOVE  |  {start:+.1f} -> {target:+.1f} deg  speed={speed:.1f} deg/s  wrist={wrist:.1f} deg")

    def _draw_follow_canvas(self, mode, wrist):
        c = self.canvas
        W, H = int(c["width"]), int(c["height"])
        c.delete("all")
        start = mode.current_start() if hasattr(mode, "current_start") else float(mode.params.get("start_deg", self._parse_float(self.follow_start_var, 0.0)))
        target = mode.current_target() if hasattr(mode, "current_target") else float(mode.params.get("target_deg", self._parse_float(self.follow_target_var, 30.0)))
        speed = mode.current_speed() if hasattr(mode, "current_speed") else float(mode.params.get("speed_deg_s", self._parse_float(self.follow_speed_var, 20.0)))
        all_targets = [start, target, float(mode.params.get("target_deg", target))]
        try:
            all_targets.extend(float(v) for v in mode.params.get("per_rep_targets", []) or [])
        except Exception:
            pass
        try:
            for segment in mode.params.get("path_segments", []) or []:
                all_targets.extend([float(segment["start"]), float(segment["end"])])
        except Exception:
            pass
        view_min = mode.params.get("view_min_deg")
        view_max = mode.params.get("view_max_deg")
        if view_min is None or view_max is None:
            view_min = max(-WRIST_LIMIT_DEG, min(all_targets) - 10.0)
            view_max = min(WRIST_LIMIT_DEG, max(all_targets) + 10.0)
        else:
            view_min = float(view_min)
            view_max = float(view_max)
        if view_max <= view_min:
            view_min, view_max = -WRIST_LIMIT_DEG, WRIST_LIMIT_DEG
        r_flex = view_min
        span = max(1.0, view_max - view_min)
        cy = H // 2
        margin = 40
        h = 1 if self.handedness_var.get() == "Right" else -1

        def _x(deg):
            return margin + (deg - r_flex) / span * (W - 2 * margin)
        def _clamp(deg):
            return max(view_min, min(view_max, deg))

        c.create_line(margin, cy, W - margin, cy, fill="#444", width=3)
        ticks = [(view_min, f"{view_min:+.0f} deg")]
        if view_min < 0 < view_max:
            ticks.append((0.0, "0 deg"))
        ticks.append((view_max, f"{view_max:+.0f} deg"))
        for deg, label in ticks:
            x = _x(deg)
            c.create_line(x, cy-12, x, cy+12, fill="#555", width=2)
            c.create_text(x, cy+22, text=label, fill="#555", font=("Segoe UI", 9))
        ext_x = W - margin if h >= 0 else margin
        flex_x = margin if h >= 0 else W - margin
        c.create_text(ext_x, cy-22, text="EXT", fill="#00e676", font=("Segoe UI", 8, "bold"))
        c.create_text(flex_x, cy-22, text="FLEX", fill="#ff9800", font=("Segoe UI", 8, "bold"))
        direction = 1 if target >= start else -1
        arrow_len = min(90, max(36, (W - 2 * margin) * 0.18))
        arrow_cx = W // 2
        arrow_y = cy - 42
        arrow_x0 = arrow_cx - direction * arrow_len * 0.5
        arrow_x1 = arrow_cx + direction * arrow_len * 0.5
        arrow_fill = "#ffd700" if mode.sub_phase in ("go", "return") else "#777777"
        c.create_line(
            arrow_x0, arrow_y, arrow_x1, arrow_y,
            fill=arrow_fill, width=4, arrow=tk.LAST, arrowshape=(14, 18, 7),
        )
        gx = _x(_clamp(mode.green_pos))
        c.create_oval(gx-16, cy-16, gx+16, cy+16, fill="#ffd700", outline="#f9a825", width=3)
        rx = _x(_clamp(wrist))
        c.create_oval(rx-13, cy-13, rx+13, cy+13, fill="#ff1744", outline="#d50000", width=3)
        colors = {"positioning": "#40c4ff", "go": "#00e676", "hold": "#90caf9", "return": "#ffeb3b", "rest": "#888888", "done": "#888888"}
        col = colors.get(mode.sub_phase, "#aaa")
        c.create_text(W//2, 14, text=mode.sub_phase.upper(), fill=col, font=("Segoe UI", 11, "bold"))
        c.create_text(W//2, H - 10, text=f"guide speed: {speed:.1f} wrist deg/s",
                      fill="#888", font=("Segoe UI", 8))
        self.motion_info_var.set(
            f"VOLUNTARY MOVE  |  {start:+.1f} -> {target:+.1f} deg  guide={mode.green_pos:.1f} deg  wrist={wrist:.1f} deg")

    # RAH canvas
    _RAH_HISTORY_MAXLEN = 1000

    def _on_rah_canvas_configure(self, _event=None):
        if not getattr(self, '_motion_polling', False) and self._current_tab_text() in ('RAH_LC',):
            self._draw_rah_canvas_idle()

    def _rah_canvas_size(self, canvas=None):
        c = self.rah_canvas if canvas is None else canvas
        try:
            W = int(c.winfo_width())
            H = int(c.winfo_height())
        except Exception:
            W, H = 0, 0
        if W <= 1:
            W = int(c["width"])
        if H <= 1:
            H = int(c["height"])
        return max(1, W), max(1, H)

    def _draw_rah_canvas_idle(self):
        c = self.rah_canvas
        W, H = self._rah_canvas_size()
        c.delete("all")
        c.create_text(W // 2, H // 2, text="Press Start to begin",
                      fill="#334", font=("Segoe UI", 10))

    def _lock_rah_display_lowpass(self):
        self._rah_display_lowpass_active = bool(self.rah_display_lowpass_var.get())
        self._rah_display_lowpass_locked = True
        try:
            self.rah_display_lowpass_check.config(state="disabled")
        except Exception:
            pass

    def _unlock_rah_display_lowpass(self):
        self._rah_display_lowpass_locked = False
        try:
            self.rah_display_lowpass_check.config(state="normal")
        except Exception:
            pass

    def _rah_display_lowpass_enabled(self) -> bool:
        if bool(getattr(self, "_rah_display_lowpass_locked", False)):
            return bool(getattr(self, "_rah_display_lowpass_active", True))
        return bool(self.rah_display_lowpass_var.get())

    def _rah_display_lowpass_cutoff_hz(self, t_s, mode=None) -> float:
        mode = self.motion_runner.active_mode if mode is None else mode
        phase = ""
        phase_t = 0.0
        try:
            if mode is not None and hasattr(mode, "phase_at"):
                phase_info = mode.phase_at(max(0.0, float(t_s)))
                phase = str(phase_info[0])
                phase_t = float(phase_info[2]) if len(phase_info) >= 3 else 0.0
            elif mode is not None and hasattr(mode, "_phase_at"):
                phase_info = mode._phase_at(max(0.0, float(t_s)))
                phase = str(phase_info[0])
                phase_t = float(phase_info[2]) if len(phase_info) >= 3 else 0.0
        except Exception:
            phase = str(getattr(mode, "sub_phase", "") if mode is not None else "")
        try:
            if (
                phase == "instruction"
                and mode is not None
                and getattr(mode, "name", "") == "direction_reversal"
                and hasattr(mode, "rise_time")
                and hasattr(mode, "hold_time")
            ):
                rise = float(mode.rise_time())
                hold = float(mode.hold_time())
                if rise <= phase_t < rise + hold:
                    phase = "hold"
        except Exception:
            pass
        return RAH_DISPLAY_HOLD_LOWPASS_HZ if phase == "hold" else RAH_DISPLAY_OTHER_LOWPASS_HZ

    def _smooth_history_for_display(self, history):
        if not self._rah_display_lowpass_enabled():
            return history
        if len(history) < 3:
            return history
        mode = self.motion_runner.active_mode
        smoothed = []
        prev_t = float(history[0][0])
        y = float(history[0][1])
        smoothed.append((history[0][0], y))
        for t_s, value in history[1:]:
            t_now = float(t_s)
            dt_s = max(0.0, t_now - prev_t)
            cutoff_hz = max(0.0, float(self._rah_display_lowpass_cutoff_hz(t_now, mode)))
            tau_s = 1.0 / (2.0 * _math.pi * cutoff_hz) if cutoff_hz > 0.0 else 0.0
            alpha = 1.0 if dt_s <= 0.0 or tau_s <= 0.0 else 1.0 - _math.exp(-dt_s / tau_s)
            y += alpha * (float(value) - y)
            smoothed.append((t_s, y))
            prev_t = t_now
        return smoothed

    def _lc_rah_value(self, sample, channel: str) -> float:
        if sample is None or len(sample) < 6:
            return 0.0
        if channel == "Fnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3)))
        if channel == "Tnorm":
            return _math.sqrt(sum(sample[i]**2 for i in range(3, 6)))
        ch_idx = {"Fx": 0, "Fy": 1, "Fz": 2, "Tx": 3, "Ty": 4, "Tz": 5}
        return sample[ch_idx.get(channel, 0)]

    def _rah_lc_direction_sign(self, channel: str, direction: int) -> int:
        direction_sign = 1 if int(direction) >= 0 else -1
        hand_sign = 1 if self.handedness_var.get() == "Right" else -1
        if channel == "Fx":
            return hand_sign * direction_sign
        if channel in ("Tx", "Ty", "Tz"):
            return -hand_sign * direction_sign
        return direction_sign

    def _draw_rah_live(self, lc_sample, t_elapsed):
        c = self.rah_canvas
        (W, H) = self._rah_canvas_size()
        c.delete('all')
        mode = self.motion_runner.active_mode
        is_dr_force = mode is not None and getattr(mode, 'name', '') == 'direction_reversal' and hasattr(mode, 'dynamics_enabled') and (not mode.dynamics_enabled())
        if is_dr_force:
            channel = str(mode.params.get('feedback_channel', 'Fx') or 'Fx')
            mvc_value = max(0.01, float(mode.target_force_value()))
            target_value = float(mode.profile_value(t_elapsed)) if hasattr(mode, 'profile_value') else 0.0
            target_pct = target_value / mvc_value * 100.0
            target_norm = target_value / mvc_value
            direction = int(mode.profile_sign(elapsed_s=t_elapsed)) if hasattr(mode, 'profile_sign') else 1
        else:
            channel = self.rah_channel_var.get()
            mvc_value = max(0.01, self._parse_float(self.rah_limit_var, 10.0))
            custom_profile = bool(mode is not None and hasattr(mode, 'has_custom_profile') and mode.has_custom_profile())
            target_pct = self._parse_float(self.rah_target_pct_var, 10.0)
            if mode is not None and hasattr(mode, 'target_pct_at'):
                target_pct = float(mode.target_pct_at(t_elapsed))
            target_pct = max(0.0 if custom_profile else 0.01, target_pct)
            target_value = mvc_value * target_pct / 100.0
            target_norm = max(0.0 if custom_profile else 0.01, target_value / mvc_value)
            direction = int(round(mode.params.get('direction', 1))) if mode is not None else 1
        desired_sign = self._rah_lc_direction_sign(channel, 1 if is_dr_force else direction)
        (ML, MR, MT, MB) = (50, 10, 15, 30)
        (PW, PH) = (W - ML - MR, H - MT - MB)
        WIN_S = 15.0
        now_frac = 1.0 / 3.0
        t_left = t_elapsed - WIN_S * now_frac
        t_right = t_left + WIN_S
        if not hasattr(self, '_rah_history'):
            self._rah_history = []
        if lc_sample and len(lc_sample) >= 6:
            raw_val = self._lc_rah_value(lc_sample, channel)
            if is_dr_force and channel in ('Fnorm', 'Tnorm'):
                norm_val = abs(raw_val) * direction / mvc_value
            elif channel in ('Fnorm', 'Tnorm'):
                norm_val = abs(raw_val) / mvc_value
            elif is_dr_force:
                norm_val = raw_val * desired_sign / mvc_value
            else:
                norm_val = max(0.0, raw_val * desired_sign / mvc_value)
            self._rah_history.append((t_elapsed, norm_val))
            if len(self._rah_history) > self._RAH_HISTORY_MAXLEN:
                self._rah_history.pop(0)
        visible_history = [(t_s, nv) for (t_s, nv) in self._rah_history if t_left <= t_s <= t_right]
        if is_dr_force:
            (y_axis_min, y_axis_max) = (-1.0, 1.0)
        else:
            max_target_norm = target_norm
            if mode is not None and hasattr(mode, 'target_pct_values'):
                max_target_norm = max(max_target_norm, max(mode.target_pct_values()) / 100.0)
            (y_axis_min, y_axis_max) = (0.0, max(0.01, max_target_norm * 1.25))
        y_axis_span = y_axis_max - y_axis_min

        def _xpx(t_abs):
            return int(ML + (t_abs - t_left) / WIN_S * PW)

        def _ypx(norm_val):
            value = max(y_axis_min, min(y_axis_max, norm_val))
            return int(MT + (y_axis_max - value) / y_axis_span * PH)
        y_zero = _ypx(0.0)
        c.create_rectangle(ML, MT, ML + PW, MT + PH, fill='#0d1117', outline='#1e2a3a')
        c.create_line(ML, y_zero, ML + PW, y_zero, fill='#2a3a4a', width=2)
        unit = '%MVC' if is_dr_force else 'Nmm' if channel.startswith('T') else 'N'
        y_ticks = [-1.0, -0.5, 0.0, 0.5, 1.0] if is_dr_force else [y_axis_max * i / 4.0 for i in range(1, 5)]
        for tick in y_ticks:
            yg = _ypx(tick)
            c.create_line(ML, yg, ML + PW, yg, fill='#1a2535', width=1)
            if is_dr_force:
                tick_label = '0%' if abs(tick) < 1e-09 else f'{tick * 100.0:+.0f}%'
            else:
                tick_label = f'{tick * mvc_value:.1f}'
            c.create_text(ML - 4, yg, text=tick_label, anchor='e', fill='#445566', font=('Segoe UI', 7))
        c.create_text(12, MT + PH // 2, text=f'{channel}\n{unit}', fill='#445566', font=('Segoe UI', 8), angle=90)
        tick_start = int(t_left // 5) * 5
        t_tick = tick_start
        while t_tick <= t_right + 0.01:
            x = _xpx(t_tick)
            if ML <= x <= ML + PW:
                c.create_line(x, MT + PH, x, MT + PH + 4, fill='#445566')
                c.create_text(x, MT + PH + 14, text=f'{int(t_tick)}s', fill='#445566', font=('Segoe UI', 7))
            t_tick += 5
        custom_profile = bool(mode is not None and hasattr(mode, 'has_custom_profile') and mode.has_custom_profile())
        CYCLE = mode.cycle_duration() if mode is not None and hasattr(mode, 'cycle_duration') else 20.0
        total_reps = int(round(float(mode.params.get('total_reps', 3)))) if mode is not None else 3
        profile_duration = mode.total_duration() if (custom_profile or is_dr_force) and hasattr(mode, 'total_duration') else CYCLE * total_reps

        def _profile_y(t_abs):
            if t_abs < 0 or t_abs > profile_duration:
                return 0.0
            if mode is not None and hasattr(mode, 'profile_value'):
                profile_norm = float(mode.profile_value(t_abs)) / mvc_value
                return profile_norm if is_dr_force else max(0.0, profile_norm)
            frac = mode.profile_fraction(t_abs) if mode is not None and hasattr(mode, 'profile_fraction') else 0.0
            return frac * target_norm
        prof_coords = []
        t_s = t_left
        while t_s <= t_right + 0.01:
            prof_coords.extend([_xpx(t_s), _ypx(_profile_y(t_s))])
            t_s += 0.25
        if len(prof_coords) >= 4:
            c.create_line(*prof_coords, fill='#1565c0', width=2, dash=(8, 4))
        if not custom_profile:
            cycle_n = int(t_left // CYCLE)
            while True:
                t_boundary = (cycle_n + 1) * CYCLE
                if t_boundary > t_right + 0.01:
                    break
                if t_left <= t_boundary <= t_right:
                    xb = _xpx(t_boundary)
                    c.create_line(xb, MT, xb, MT + PH, fill='#1e4a8a', width=1, dash=(4, 6))
                    c.create_text(xb + 3, MT + 4, text=f'x{int(t_boundary // CYCLE)}', anchor='nw', fill='#3a6aaa', font=('Segoe UI', 7))
                cycle_n += 1
        if not is_dr_force:
            y_lim = _ypx(target_norm)
            c.create_line(ML, y_lim, ML + PW, y_lim, fill='#ffd700', width=1, dash=(6, 4))
            target_label = f'{target_value:.1f}{unit} ({target_pct:.0f}% MVC)'
            c.create_text(ML + PW - 2, y_lim - 7, text=target_label, anchor='e', fill='#ffd700', font=('Segoe UI', 8, 'bold'))
        x_now = _xpx(t_elapsed)
        c.create_line(x_now, MT, x_now, MT + PH, fill='#555', width=1, dash=(4, 4))
        draw_history = self._smooth_history_for_display(visible_history)
        if len(draw_history) >= 2:
            coords = []
            for (t_s, nv) in draw_history:
                x = _xpx(t_s)
                y = _ypx(nv)
                if ML - 2 <= x <= ML + PW + 2:
                    coords.extend([x, y])
            if len(coords) >= 4:
                last_nv = draw_history[-1][1]
                if is_dr_force:
                    lc_col = '#ff4444' if abs(last_nv) > 1.0 else '#00e676'
                else:
                    lc_col = '#ff4444' if last_nv > target_norm else '#00e676'
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2] - 5, coords[-1] - 5, coords[-2] + 5, coords[-1] + 5, fill=lc_col, outline='white', width=1)
        if custom_profile or is_dr_force:
            title = f't = {t_elapsed:.1f}s / {profile_duration:.1f}s  |  {mode.sub_phase}'
        else:
            phase_s = t_elapsed % CYCLE
            cycle_n2 = int(t_elapsed // CYCLE) + 1
            title = f't = {t_elapsed:.1f}s  |  trial {min(cycle_n2, total_reps)}/{total_reps}  |  phase {phase_s:.1f}s / {CYCLE:.1f}s'
        c.create_text(W // 2, 8, text=title, fill='#aaa', font=('Segoe UI', 9, 'bold'))

    # LC Monitor canvas
    _LC_CHANNELS = {
        "Fx":    (0, True,  "N"),  "Fy":    (1, True,  "N"),  "Fz":    (2, True,  "N"),
        "Tx":    (3, True,  "Nmm"), "Ty":    (4, True,  "Nmm"), "Tz":    (5, True,  "Nmm"),
        "Fnorm": (None, False, "N"), "Tnorm": (None, False, "Nmm"),
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
        c.create_text(W // 2, H // 2, text="Waiting for LC data...",
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
                if signed:
                    lc_col = "#ff4444" if abs(last_val) > baseline else "#00e676"
                else:
                    lc_col = "#ff4444" if last_val > baseline else "#00e676"
                c.create_line(*coords, fill=lc_col, width=2, smooth=True, splinesteps=12)
                c.create_oval(coords[-2]-5, coords[-1]-5, coords[-2]+5, coords[-1]+5,
                              fill=lc_col, outline="white", width=1)
        c.create_line(ML + PW, MT, ML + PW, MT + PH, fill="#334455", width=1)

    def _fx_monitor_loop(self, interval_ms=100):
        if self._current_tab_text() not in ('RAH_LC',):
            self._set_top_monitor_mode('lc')
        current_channel = self.lc_channel_var.get()
        if getattr(self, '_fx_last_channel', None) != current_channel:
            self._fx_history.clear()
            self._fx_last_channel = current_channel
        (lc_sample, _) = self.lc_worker.ring.get_latest()
        if lc_sample and len(lc_sample) >= 6:
            val = self._lc_value(lc_sample, current_channel)
            self._fx_history.append((time.time(), val))
            (_, signed, unit) = self._LC_CHANNELS.get(current_channel, (0, False, 'N'))
            self.fx_live_var.set(f'{current_channel} = {val:.3f} {unit}')
            self._draw_fx_chart()
        elif not self._fx_history:
            self._draw_fx_canvas_empty()
        self.after(interval_ms, self._fx_monitor_loop)

    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Close
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    #  Close
    # ÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚ÂÃƒÂ¢Ã¢â‚¬Â¢Ã‚Â
    def on_close(self):
        self._motion_polling = False
        self._cancel_manual_velocity_poll()
        self._cancel_manual_position_poll()
        try:
            self.recorder.stop()
            self._stop_session_recording()
        except Exception:
            pass
        try:
            self.motion_runner.stop(release=False)
        except Exception:
            pass
        try:
            self.serial_worker.send('SET_DMP:0')
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
        try:
            self.trigger_worker.disconnect()
        except Exception:
            pass
        try:
            self._close_gui_session_log()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = ExpGUI(use_mock="--mock" in sys.argv)
    app.mainloop()
