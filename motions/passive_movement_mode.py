"""
motions/passive_movement_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class PassiveMovementMode(ModeBase):
    """
    Passive Movement — fully automatic, n reps.
    Set total_reps=1 for single trial.

    Phases per rep:
        go      — servo ramps to target
                  sub-marker 1.0 → 1.5 when wrist passes active_end_deg
        hold    — holds x s at target (marker 2)
        return  — servo brings back to 0 (damper off)
                  sub-marker 2.5 → 3.0 when wrist passes active_end_deg
        rest    — holds at 0 for y s

    UDP marker sequence per rep (integers only):
        0→1  go starts
        1→1  reached active_end on the way out (event marker)
        1→2  hold starts
        2→3  return starts
        3→3  passed active_end on the way back (event marker)
        3→0  rest starts

    CSV marker (float, finer resolution):
        1.0  go, before active_end
        1.5  go, past active_end
        2.0  hold
        2.5  return, before active_end
        3.0  return, past active_end
        0.0  rest / done
    """

    name        = "passive_movement"
    description = "Passive Movement: servo drives to target and back, n reps"

    PHASE_TO_MARKER = {
        "idle":   0,
        "go":     1,
        "hold":   2,
        "return": 3,
        "rest":   0,
        "done":   0,
    }

    parameters = [
        {"name": "direction",       "default":  1,    "min": -1,   "max":  1,             "unit": "+1=extension / -1=flexion"},
        {"name": "target_deg",      "default": 40.0,  "min":  1.0, "max": WRIST_LIMIT_DEG,"unit": "wrist deg"},
        {"name": "active_end_deg",  "default": 20.0,  "min":  1.0, "max": WRIST_LIMIT_DEG,"unit": "wrist deg (sub-marker threshold)"},
        {"name": "speed_deg_s",     "default": 20.0,  "min":  1.0, "max": 80.0,           "unit": "wrist deg/s"},
        {"name": "hold_duration_s", "default":  2.0,  "min":  0.5, "max": 30.0,           "unit": "s"},
        {"name": "rest_time_s",     "default":  2.0,  "min":  0.5, "max": 30.0,           "unit": "s"},
        {"name": "use_decel",       "default":  0,    "min":  0,   "max":  1,             "unit": "0=constant, 1=cos decel"},
        {"name": "damper_pwm",      "default":  0,    "min":  0,   "max": 255,            "unit": "PWM"},
        {"name": "total_reps",      "default":  1,    "min":  1,   "max": 50,             "unit": "reps"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase         = "idle"
        self._drive_pos        = None
        self._hold_t           = 0.0
        self._rest_t           = 0.0
        self._rep              = 0
        self._go_reached       = False   # passed active_end on the way out
        self._return_reached   = False   # passed active_end on the way back
        self.on_phase_change   = None
        self.on_active_reached = None    # callback(direction: str) "go" or "return"

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def _notify_reached(self, direction: str):
        if self.on_active_reached:
            self.on_active_reached(direction)

    def start_go(self, wrist_deg: float):
        self._drive_pos      = wrist_deg
        self._hold_t         = 0.0
        self._go_reached     = False
        self._return_reached = False
        self._notify("idle", "go")

    def compute(self, state: dict) -> Command:
        wrist        = state["wrist_deg"]
        dt           = state["dt"]
        direction    = 1 if int(round(self.params["direction"])) >= 0 else -1
        physical_dir = direction * self.handedness
        target_wrist = abs(self.params["target_deg"])
        active_end   = abs(self.params.get("active_end_deg", 20.0))
        speed_wrist  = abs(self.params["speed_deg_s"])
        hold_dur     = float(self.params["hold_duration_s"])
        rest_dur     = float(self.params.get("rest_time_s", 2.0))
        use_decel    = int(round(self.params.get("use_decel", 0)))
        damper       = int(max(0, min(255, round(self.params["damper_pwm"]))))
        total_reps   = int(round(self.params.get("total_reps", 1)))

        wrist_signed = wrist * physical_dir   # positive = toward target

        def _scaled_speed(pos):
            if not use_decel or target_wrist <= 0:
                return speed_wrist
            progress = min(1.0, abs(pos) / target_wrist)
            return max(0.5, speed_wrist * math.cos(progress * math.pi / 2))

        if self.sub_phase == "idle":
            return Command(servo=None, damper=damper)

        # ── go ────────────────────────────────────────────────────────────────
        if self.sub_phase == "go":
            target_signed = physical_dir * target_wrist
            eff_speed = _scaled_speed(self._drive_pos)
            gap  = target_signed - self._drive_pos
            step = math.copysign(min(abs(gap), eff_speed * dt), gap)
            self._drive_pos += step

            # sub-marker: passed active_end on the way out
            if not self._go_reached and wrist_signed >= active_end:
                self._go_reached = True
                self._notify_reached("go")

            if abs(self._drive_pos - target_signed) < 0.3:
                self._drive_pos = target_signed
                self._hold_t    = 0.0
                self._notify("go", "hold")
            return Command(servo=self._drive_pos, damper=damper)

        # ── hold ──────────────────────────────────────────────────────────────
        if self.sub_phase == "hold":
            self._hold_t += dt
            target_signed = physical_dir * target_wrist
            if self._hold_t >= hold_dur:
                self._hold_t         = 0.0
                self._drive_pos      = physical_dir * target_wrist
                self._return_reached = False
                self._notify("hold", "return")
            return Command(servo=target_signed, damper=damper)

        # ── return ────────────────────────────────────────────────────────────
        if self.sub_phase == "return":
            gap  = 0.0 - self._drive_pos
            step = math.copysign(min(abs(gap), speed_wrist * dt), gap)
            self._drive_pos += step

            # sub-marker: passed active_end on the way back
            if not self._return_reached and wrist_signed <= active_end:
                self._return_reached = True
                self._notify_reached("return")

            if abs(self._drive_pos) < 0.3:
                self._drive_pos = 0.0
                self._rest_t    = 0.0
                self._notify("return", "rest")
            return Command(servo=self._drive_pos, damper=0)

        # ── rest ──────────────────────────────────────────────────────────────
        if self.sub_phase == "rest":
            self._rest_t += dt
            if self._rest_t >= rest_dur:
                self._rep      += 1
                self._rest_t    = 0.0
                self._drive_pos = 0.0
                if self._rep >= total_reps:
                    self._notify("rest", "done")
                    self._done = True
                else:
                    self._go_reached     = False
                    self._return_reached = False
                    self._notify("rest", "go")
            return Command(servo=0.0, damper=damper)

        if self.sub_phase == "done":
            return Command(servo=None, damper=damper)

        return Command(servo=None, damper=damper)

    @property
    def phase(self) -> str:
        total = int(round(self.params.get("total_reps", 1)))
        tgt   = self.params.get("target_deg", "?")
        return f"{self.sub_phase} | rep={self._rep+1}/{total} | target={tgt}°"

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
        return self._drive_pos or 0.0

    @property
    def go_reached(self) -> bool:
        return self._go_reached

    @property
    def return_reached(self) -> bool:
        return self._return_reached
