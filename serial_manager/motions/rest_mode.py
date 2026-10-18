"""
motions/rest_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class RestMode(ModeBase):
    """
    Rest baseline acquisition.

    Servo ramps to the target angle at DRIVE_SPEED, then holds with torque on.
    """

    name        = "rest"
    description = "Rest baseline: ramp to and hold a fixed wrist angle"

    parameters = [
        {"name": "rest_angle_deg", "default": 0.0, "min": -WRIST_LIMIT_DEG, "max": WRIST_LIMIT_DEG, "unit": "deg"},
        {"name": "damper_pwm",     "default": 0,   "min": 0,     "max": 255,  "unit": "PWM"},
    ]

    DRIVE_SPEED = 50.0   # wrist deg/s

    def reset(self):
        super().reset()
        self.sub_phase  = "moving"
        self._drive_pos = None

    def compute(self, state: dict) -> Command:
        wrist  = float(state.get("wrist_deg", 0.0))
        dt     = float(state.get("dt", 0.01))
        target = float(self.params.get("rest_angle_deg", 0.0))
        damper = int(max(0, min(255, round(self.params.get("damper_pwm", 0)))))

        if self.sub_phase == "moving":
            if self._drive_pos is None:
                self._drive_pos = wrist
            gap  = target - self._drive_pos
            step = math.copysign(min(abs(gap), self.DRIVE_SPEED * dt), gap) \
                   if gap != 0 else 0
            self._drive_pos += step
            if abs(self._drive_pos - target) < 0.2:
                self._drive_pos = target
                self.sub_phase  = "hold"
            return Command(servo=self._drive_pos, damper=damper)

        # hold
        return Command(servo=target, damper=damper)

    @property
    def phase(self) -> str:
        angle = float(self.params.get("rest_angle_deg", 0.0))
        return f"{self.sub_phase} | hold={angle:.1f}°"
