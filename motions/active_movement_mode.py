"""
motions/active_movement_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class ActiveMovementMode(ModeBase):
    """
    Active Movement — user moves freely, operator marks end, n reps.
    Set total_reps=1 for single trial.

    Phases per rep:
        go      — user moves toward target direction (torque off)
        return  — user returns to 0 (auto-detected by wrist <= return_tol)
        rest    — rest x s at 0 (torque off)

    Marker sequence per rep:
        0→1  go starts
        1→2  Mark End (manual)
        2→3  user back at 0 (auto)
        3→0  rest starts
        0→1  next rep
    """

    name        = "active_movement"
    description = "Active Movement: user moves freely, operator marks end, n reps"

    PHASE_TO_MARKER = {
        "idle":   0,
        "go":     1,
        "return": 2,
        "rest":   0,
        "done":   0,
    }

    parameters = [
        {"name": "direction",     "default":  1,   "min": -1,  "max":  1,   "unit": "+1=extension / -1=flexion"},
        {"name": "damper_pwm",    "default":  0,   "min":  0,  "max": 255,  "unit": "PWM"},
        {"name": "return_tol_deg","default":  5.0, "min":  1.0,"max": 20.0, "unit": "deg"},
        {"name": "rest_time_s",   "default":  2.0, "min":  0.5,"max": 30.0, "unit": "s"},
        {"name": "total_reps",    "default":  1,   "min":  1,  "max": 50,   "unit": "reps"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase       = "idle"
        self._rest_t         = 0.0
        self._rep            = 0
        self.on_phase_change = None

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def start_go(self):
        self._notify("idle", "go")

    def mark_end(self):
        if self.sub_phase == "go":
            self._notify("go", "return")

    def compute(self, state: dict) -> Command:
        wrist      = state["wrist_deg"]
        dt         = state["dt"]
        direction  = 1 if int(round(self.params["direction"])) >= 0 else -1
        phys_dir   = direction * self.handedness
        damper     = int(max(0, min(255, round(self.params["damper_pwm"]))))
        tol        = float(self.params.get("return_tol_deg", 5.0))
        rest_dur   = float(self.params.get("rest_time_s",    2.0))
        total_reps = int(round(self.params.get("total_reps", 1)))

        if self.sub_phase == "idle":
            return Command(servo=None, damper=damper)

        if self.sub_phase == "go":
            return Command(servo=None, damper=damper)

        if self.sub_phase == "return":
            # auto-detect return to 0
            if abs(wrist) <= tol:
                self._rest_t = 0.0
                self._notify("return", "rest")
            return Command(servo=None, damper=damper)

        if self.sub_phase == "rest":
            self._rest_t += dt
            if self._rest_t >= rest_dur:
                self._rep    += 1
                self._rest_t  = 0.0
                if self._rep >= total_reps:
                    self._notify("rest", "done")
                    self._done = True
                else:
                    self._notify("rest", "go")
            return Command(servo=None, damper=0)

        if self.sub_phase == "done":
            return Command(servo=None, damper=0)

        return Command(servo=None, damper=0)

    @property
    def phase(self) -> str:
        total = int(round(self.params.get("total_reps", 1)))
        d     = int(round(self.params.get("direction", 1)))
        dir_str = "EXTEND" if d >= 0 else "FLEX"
        return f"{self.sub_phase} | rep={self._rep+1}/{total} | {dir_str}"

    @property
    def rep(self) -> int:
        return self._rep

    @property
    def rest_t(self) -> float:
        return self._rest_t
