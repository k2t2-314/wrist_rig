"""
motions/rah_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class RAHMode(ModeBase):
    """
    Ramp and Hold (RAH) experiment.

    Servo holds at current wrist angle (set at start).
    Subject pushes isometrically against physical lock.

    Marker sequence:
        0→1  start
        1→0  stop
    """

    name        = "ramp_and_hold"
    description = "Ramp and Hold: servo holds at current angle, isometric force"

    RAMP_PROFILE = [
        (0, 0), (5, 0), (10, 1), (15, 1),
        (20, 0), (25, 0), (30, 1), (35, 1),
        (40, 0), (45, 0),
    ]

    parameters = [
        {"name": "direction",    "default":  1,    "min": -1,    "max":  1,    "unit": "+1=extension / -1=flexion"},
        {"name": "torque_limit", "default": 10.0,  "min":  0.1,  "max": 200.0, "unit": "N or Nm (y=1 reference)"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase  = "idle"
        self._hold_pos  = 0.0

    def start(self, wrist_deg: float):
        self._hold_pos = wrist_deg
        self.sub_phase = "active"

    def compute(self, state: dict) -> Command:
        if self.sub_phase == "idle":
            return Command(servo=None, damper=0)
        return Command(servo=self._hold_pos, damper=0)

    @property
    def hold_pos(self) -> float:
        return self._hold_pos

    @property
    def phase(self) -> str:
        return f"{self.sub_phase} | hold={self._hold_pos:.1f}°"
