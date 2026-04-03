"""
motions/back_and_forth_mode.py
"""

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class BackAndForthMode(ModeBase):
    """
    Back and Forth experiment.

    Servo sweeps between left_deg and right_deg at speed_deg_s.
    Each boundary arrival counts as one rep.
    After total_reps the mode sets done=True.

    Torque ON throughout. Subject is passive.
    """

    name        = "back_and_forth"
    description = "Back and Forth: servo sweeps between two boundaries N times"

    parameters = [
        {"name": "left_deg",    "default": -30.0, "min": -WRIST_LIMIT_DEG, "max":   0.0, "unit": "wrist deg"},
        {"name": "right_deg",   "default":  30.0, "min":   0.0, "max": WRIST_LIMIT_DEG, "unit": "wrist deg"},
        {"name": "speed_deg_s", "default":  20.0, "min":   1.0, "max":  80.0, "unit": "wrist deg/s"},
        {"name": "total_reps",  "default":  10,   "min":   1,   "max":  200,  "unit": "boundary touches"},
        {"name": "damper_pwm",  "default":   0,   "min":   0,   "max":  255,  "unit": "PWM"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase  = "idle"
        self._drive_pos = None
        self._dir       = -1    # -1 = heading left first
        self._reps      = 0

    def start_moving(self, wrist_deg: float):
        self._drive_pos = wrist_deg
        self._dir       = -1
        self._reps      = 0
        self.sub_phase  = "moving"

    def compute(self, state: dict) -> Command:
        dt     = state["dt"]
        damper = int(max(0, min(255, round(self.params["damper_pwm"]))))

        if self.sub_phase == "idle":
            return Command(servo=None, damper=damper)

        left  = float(self.params["left_deg"])
        right = float(self.params["right_deg"])
        speed = abs(self.params["speed_deg_s"])
        total = int(round(self.params["total_reps"]))

        self._drive_pos += self._dir * speed * dt

        if self._dir < 0 and self._drive_pos <= left:
            self._drive_pos = left
            self._reps     += 1
            self._dir       = 1
        elif self._dir > 0 and self._drive_pos >= right:
            self._drive_pos = right
            self._reps     += 1
            self._dir       = -1

        if self._reps >= total:
            self.sub_phase = "done"
            self._done     = True

        return Command(servo=self._drive_pos, damper=damper)

    @property
    def drive_pos(self) -> float:
        return self._drive_pos or 0.0

    @property
    def reps(self) -> int:
        return self._reps

    @property
    def phase(self) -> str:
        total = int(round(self.params.get("total_reps", 0)))
        return f"{self.sub_phase} | {self._reps}/{total} reps"
