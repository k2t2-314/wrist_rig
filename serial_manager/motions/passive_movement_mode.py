"""Passive movement mode with velocity-mode travel and position-mode endpoint lock."""

import math

from config import WRIST_LIMIT_DEG
from motions.base import Command, ModeBase


class PassiveMovementMode(ModeBase):
    """
    Initial positioning:
        positioning -> velocity drive from the current wrist angle to start_deg

    Per repetition:
        go      -> velocity drive from start_deg to target_deg
        hold    -> position lock at target_deg
        return  -> velocity drive back to start_deg
        rest    -> position lock at start_deg before the next repetition

    Marker sequence:
        idle -> positioning: 1
        positioning -> go:  1 again
        go -> hold:          2
        hold -> return:      3
        return -> rest:      0 (sent to hardware as code 15)
    """

    name = "passive_movement"
    description = "Passive Movement: velocity drives between signed start/end points"

    REACHED_TOL_DEG = 0.5

    PHASE_TO_MARKER = {
        "idle": 0,
        "positioning": 1,
        "go": 1,
        "hold": 2,
        "return": 3,
        "rest": 0,
        "done": 0,
    }

    parameters = [
        {"name": "start_deg", "default": 0.0, "min": -WRIST_LIMIT_DEG, "max": WRIST_LIMIT_DEG, "unit": "signed wrist deg"},
        {"name": "target_deg", "default": 40.0, "min": -WRIST_LIMIT_DEG, "max": WRIST_LIMIT_DEG, "unit": "signed wrist deg"},
        {"name": "speed_deg_s", "default": 20.0, "min": 1.0, "max": 80.0, "unit": "wrist deg/s"},
        {"name": "hold_duration_s", "default": 2.0, "min": 0.5, "max": 30.0, "unit": "s"},
        {"name": "rest_time_s", "default": 2.0, "min": 0.5, "max": 30.0, "unit": "s"},
        {"name": "total_reps", "default": 1, "min": 1, "max": 50, "unit": "reps"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase = "idle"
        self._drive_pos = None
        self._segment_start = 0.0
        self._hold_t = 0.0
        self._rest_t = 0.0
        self._timer_phase = None
        self._timer_t0 = 0.0
        self._rep = 0
        self._prev_gap = None
        self.on_phase_change = None

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        self._prev_gap = None
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def _timer_elapsed(self, now: float) -> float:
        if self._timer_phase != self.sub_phase:
            self._timer_phase = self.sub_phase
            self._timer_t0 = now
        return max(0.0, now - self._timer_t0)

    def start_positioning(self, wrist_deg: float):
        self._drive_pos = float(wrist_deg)
        self._segment_start = float(wrist_deg)
        self._hold_t = 0.0
        self._rest_t = 0.0
        self._prev_gap = None
        start = float(self.params.get("start_deg", 0.0))
        if abs(self._drive_pos - start) <= self.REACHED_TOL_DEG:
            self._drive_pos = start
            self._segment_start = start
            self._notify("idle", "go")
        else:
            self._notify("idle", "positioning")


    def _advance_display_pos(self, target: float, speed: float, dt: float):
        current = self._drive_pos
        if current is None:
            current = target
        gap = target - current
        if abs(gap) <= 1e-9:
            self._drive_pos = target
        else:
            step = min(abs(gap), speed * max(0.0, dt))
            self._drive_pos = current + math.copysign(step, gap)

    def _velocity_to_target(self, wrist: float, target: float, speed: float, dt: float, prev_phase: str, next_phase: str) -> Command:
        gap = target - wrist
        crossed = self._prev_gap is not None and gap * self._prev_gap <= 0.0
        reached = abs(gap) <= self.REACHED_TOL_DEG or crossed
        if reached:
            self._drive_pos = target
            self._prev_gap = None
            self._notify(prev_phase, next_phase)
            return Command(servo=target, damper=0, velocity=0.0)

        self._prev_gap = gap
        self._advance_display_pos(target, speed, dt)
        return Command(servo=None, damper=0, velocity=math.copysign(speed, gap))

    def compute(self, state: dict) -> Command:
        now = float(state.get("t", 0.0))
        dt = float(state["dt"])
        wrist = float(state.get("wrist_deg", 0.0))
        start_wrist = float(self.params.get("start_deg", 0.0))
        target_wrist = float(self.params.get("target_deg", 40.0))
        speed_wrist = abs(float(self.params.get("speed_deg_s", 20.0)))
        hold_dur = float(self.params.get("hold_duration_s", 2.0))
        rest_dur = float(self.params.get("rest_time_s", 2.0))
        total_reps = int(round(self.params.get("total_reps", 1)))

        if self.sub_phase == "idle":
            return Command(servo=None, damper=0)

        if self.sub_phase == "positioning":
            return self._velocity_to_target(wrist, start_wrist, speed_wrist, dt, "positioning", "go")

        if self.sub_phase == "go":
            return self._velocity_to_target(wrist, target_wrist, speed_wrist, dt, "go", "hold")

        if self.sub_phase == "hold":
            self._hold_t = self._timer_elapsed(now)
            if self._hold_t >= hold_dur:
                self._drive_pos = target_wrist
                self._notify("hold", "return")
            return Command(servo=target_wrist, damper=0)

        if self.sub_phase == "return":
            return self._velocity_to_target(wrist, start_wrist, speed_wrist, dt, "return", "rest")

        if self.sub_phase == "rest":
            self._rest_t = self._timer_elapsed(now)
            if self._rest_t >= rest_dur:
                self._rep += 1
                self._drive_pos = start_wrist
                self._segment_start = start_wrist
                if self._rep >= total_reps:
                    self._notify("rest", "done")
                    self._done = True
                else:
                    self._notify("rest", "go")
            return Command(servo=start_wrist, damper=0)

        return Command(servo=None, damper=0, velocity=0.0)

    @property
    def phase(self) -> str:
        total = int(round(self.params.get("total_reps", 1)))
        start = float(self.params.get("start_deg", 0.0))
        target = float(self.params.get("target_deg", 0.0))
        return f"{self.sub_phase} | rep={self._rep + 1}/{total} | {start:+.1f} deg -> {target:+.1f} deg"

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
    def drive_pos(self) -> float:
        return self._drive_pos if self._drive_pos is not None else 0.0
