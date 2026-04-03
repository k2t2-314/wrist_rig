"""
motions/calibration_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class CalibrationMode(ModeBase):
    """
    Calibration pose setup (single mode only).

    Phases:
        moving  — servo ramps to start_angle_deg
        hold    — servo holds after arrival (operator presses Return)
        return  — torque off, subject returns manually (operator presses Final Pos)
        done    — complete

    Marker sequence:
        0→1  start (moving)
        1→2  arrived at hold
        2→3  return (torque off)
        3→0  final pos + stop
    """

    name        = "calibration"
    description = "Calibration: move to start angle, hold, then release"

    PHASE_TO_MARKER = {
        "moving": 1,
        "hold":   2,
        "return": 3,
        "done":   0,
    }

    parameters = [
        {"name": "start_angle_deg", "default": 0.0, "min": -WRIST_LIMIT_DEG, "max": WRIST_LIMIT_DEG, "unit": "deg"},
        {"name": "damper_pwm",      "default": 0,   "min": 0,   "max": 255,  "unit": "PWM"},
        {"name": "tol_deg",         "default": 2.0, "min": 0.1, "max": 10.0, "unit": "deg"},
        {"name": "settle_time_s",   "default": 0.3, "min": 0.0, "max": 3.0,  "unit": "s"},
    ]

    DRIVE_SPEED = 50.0

    def reset(self):
        super().reset()
        self.sub_phase       = "moving"
        self._within_since   = None
        self._drive_pos      = None
        self.on_phase_change = None

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def mark_return(self):
        if self.sub_phase == "hold":
            self._notify("hold", "return")

    def mark_final(self):
        if self.sub_phase == "return":
            self._notify("return", "done")
            self._done = True

    def compute(self, state: dict) -> Command:
        wrist  = float(state.get("wrist_deg", 0.0))
        t      = float(state.get("t", 0.0))
        dt     = float(state.get("dt", 0.01))
        target = float(self.params.get("start_angle_deg", 0.0))
        damper = int(max(0, min(255, round(self.params.get("damper_pwm", 0)))))
        tol    = float(self.params.get("tol_deg", 2.0))
        settle = float(self.params.get("settle_time_s", 0.3))

        if self.sub_phase == "moving":
            if self._drive_pos is None:
                self._drive_pos = wrist
            gap  = target - self._drive_pos
            step = math.copysign(min(abs(gap), self.DRIVE_SPEED * dt), gap) \
                   if gap != 0 else 0
            self._drive_pos += step
            if abs(wrist - target) <= tol:
                if self._within_since is None:
                    self._within_since = t
                elif (t - self._within_since) >= settle:
                    self._within_since = None
                    self._notify("moving", "hold")
            else:
                self._within_since = None
            return Command(servo=self._drive_pos, damper=damper)

        if self.sub_phase == "hold":
            return Command(servo=target, damper=damper)

        if self.sub_phase == "return":
            return Command(servo=None, damper=damper)

        if self.sub_phase == "done":
            return Command(servo=None, damper=damper)

        return Command(servo=None, damper=damper)

    @property
    def phase(self) -> str:
        target = float(self.params.get("start_angle_deg", 0.0))
        return f"{self.sub_phase} | start={target:.1f}°"

    @property
    def rec_phase(self) -> str:
        return self.sub_phase
