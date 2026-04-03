"""
motions/rom_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class ROMMode(ModeBase):
    """
    Range of Motion assessment — fully automatic, n reps.
    Set total_reps=1 for single trial.

    Phases: active → passive → hold → servo_back → rest → (next rep)

    Marker sequence per rep:
        0→1  start / next rep (active)
        1→2  active end marked (manual)
        2→3  passive end marked (manual)
        3→4  hold complete, servo returning
        4→0  returned to 0, rest begins
    """

    name        = "rom_assessment"
    description = "ROM: active then passive, n reps (set reps=1 for single)"

    PHASE_TO_MARKER = {
        "active":     1,
        "passive":    2,
        "hold":       3,
        "servo_back": 4,
        "rest":       0,
        "done":       0,
    }

    PASSIVE_SPEED = 20.0
    BOUNDARY      = WRIST_LIMIT_DEG

    parameters = [
        {"name": "direction",           "default": 1,    "min": -1,  "max": 1,             "unit": "+1=extension / -1=flexion"},
        {"name": "damper_pwm",          "default": 0,    "min": 0,   "max": 255,           "unit": "PWM"},
        {"name": "passive_speed_deg_s", "default": 20.0, "min": 1.0, "max": 80.0,          "unit": "wrist deg/s"},
        {"name": "hold_time_s",         "default": 3.0,  "min": 0.5, "max": 30.0,          "unit": "s"},
        {"name": "rest_time_s",         "default": 3.0,  "min": 0.5, "max": 30.0,          "unit": "s"},
        {"name": "total_reps",          "default": 1,    "min": 1,   "max": 50,            "unit": "reps"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase     = "active"
        self.active_rom    = None
        self.passive_rom   = None
        self.txt_written   = False
        self._passive_hold = False
        self._passive_pos  = None
        self._drive_pos    = None
        self._hold_t       = 0.0
        self._rest_t       = 0.0
        self._rep          = 0
        self.on_phase_change = None

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def mark_active_end(self, wrist_deg: float):
        if self.sub_phase != "active":
            return
        self.active_rom = abs(wrist_deg)
        self._drive_pos = wrist_deg
        self._notify("active", "passive")

    def mark_passive_end(self, wrist_deg: float):
        if self.sub_phase != "passive":
            return
        self.passive_rom   = abs(wrist_deg)
        self._passive_hold = True
        self._passive_pos  = wrist_deg
        self._hold_t       = 0.0
        self._notify("passive", "hold")

    def compute(self, state: dict) -> Command:
        wrist        = state["wrist_deg"]
        dt           = state["dt"]
        direction    = 1 if int(round(self.params["direction"])) >= 0 else -1
        physical_dir = direction * self.handedness
        damper       = int(max(0, min(255, round(self.params["damper_pwm"]))))
        speed        = float(self.params.get("passive_speed_deg_s", self.PASSIVE_SPEED))

        if self.sub_phase == "active":
            return Command(servo=None, damper=damper)

        if self.sub_phase == "passive":
            if self._passive_hold:
                hold_pos = self._passive_pos if self._passive_pos is not None \
                           else physical_dir * self.BOUNDARY
                return Command(servo=hold_pos, damper=damper)
            if self._drive_pos is None:
                self._drive_pos = wrist
            target = physical_dir * self.BOUNDARY
            gap    = target - self._drive_pos
            # slow down beyond 90° for safety
            eff_speed = speed * 0.3 if abs(self._drive_pos) > 90.0 else speed
            step   = math.copysign(min(abs(gap), eff_speed * dt), gap) if gap != 0 else 0.0
            self._drive_pos += step
            if abs(self._drive_pos - target) < 0.3:
                self._drive_pos = target
            return Command(servo=self._drive_pos, damper=0)

        if self.sub_phase == "hold":
            target = self._passive_pos if self._passive_pos is not None \
                     else physical_dir * self.BOUNDARY
            self._hold_t += dt
            hold_dur = float(self.params.get("hold_time_s", 3.0))
            if self._hold_t >= hold_dur:
                self._hold_t    = 0.0
                self._drive_pos = self._passive_pos or wrist
                self._notify("hold", "servo_back")
            return Command(servo=target, damper=damper)

        if self.sub_phase == "servo_back":
            gap  = 0.0 - self._drive_pos
            step = math.copysign(min(abs(gap), speed * dt), gap) if gap != 0 else 0.0
            self._drive_pos += step
            if abs(self._drive_pos) < 0.3:
                self._drive_pos = 0.0
                self._start_rest()
            return Command(servo=self._drive_pos, damper=0)

        if self.sub_phase == "rest":
            self._rest_t += dt
            rest_dur = float(self.params.get("rest_time_s", 3.0))
            if self._rest_t >= rest_dur:
                self._rest_t       = 0.0
                self._hold_t       = 0.0
                self._drive_pos    = None
                self._passive_hold = False
                self._passive_pos  = None
                self.active_rom    = None
                self.passive_rom   = None
                self.txt_written   = False
                self._rep         += 1
                if self._rep >= int(round(self.params["total_reps"])):
                    self._notify("rest", "done")
                    self._done = True
                else:
                    self._notify("rest", "active")
            return Command(servo=0.0, damper=damper)

        if self.sub_phase == "done":
            return Command(servo=None, damper=damper)

        return Command(servo=None, damper=0)

    def _start_rest(self):
        self._rest_t = 0.0
        prev = self.sub_phase
        self._notify(prev, "rest")

    @property
    def phase(self) -> str:
        d       = int(round(self.params.get("direction", 1)))
        dir_str = "EXTEND" if d >= 0 else "FLEX"
        a = f"{self.active_rom:.1f}°"  if self.active_rom  is not None else "---"
        p = f"{self.passive_rom:.1f}°" if self.passive_rom is not None else "---"
        total = int(round(self.params.get("total_reps", 1)))
        return f"{self.sub_phase} | rep={self._rep+1}/{total} | {dir_str} | A={a} P={p}"

    @property
    def rep(self) -> int:
        return self._rep

    @property
    def hold_t(self) -> float:
        return self._hold_t

    @property
    def rest_t(self) -> float:
        return self._rest_t

    @property
    def green_pos(self) -> float:
        d = int(round(self.params.get("direction", 1)))
        return float(self.BOUNDARY if d >= 0 else -self.BOUNDARY)

    @property
    def rec_active_rom(self) -> str:
        return f"{self.active_rom:.3f}" if self.active_rom is not None else ""

    @property
    def rec_passive_rom(self) -> str:
        return f"{self.passive_rom:.3f}" if self.passive_rom is not None else ""
