"""
motions/aan_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class AANMode(ModeBase):
    """
    AAN experiment — fully automatic, n reps.
    Set total_reps=1 for single trial.

    Phases per rep:
        active      — user moves to active_end (timeout_s), or times out
        pre_passive — pause y s (0=skip), servo on if timed out
        passive     — servo drives to passive_end
        hold        — hold z s at passive_end
        return      — damper MAX (if enabled), torque off, user has a s to return
        servo_back  — servo on, damper off, brings user back to 0
        rest        — rest b s at 0, torque on

    Marker sequence per rep:
        0→1  active starts
        1→2  passive starts (servo moving to passive_end)
        2→3  hold starts (at passive_end)
        3→4  return starts (torque off, damper on)
        4→5  servo_back starts (servo on, damper off)
        5→0  rest starts
    """

    name        = "aan"
    description = "AAN: assist-as-needed, n reps (set reps=1 for single)"

    BOUNDARY         = WRIST_LIMIT_DEG
    ACTIVE_TIMEOUT_S = 5.0

    PHASE_TO_MARKER = {
        "active":      1,
        "pre_passive": 1,
        "passive":     2,
        "hold":        3,
        "return":      4,
        "servo_back":  5,
        "rest":        0,
        "done":        0,
    }

    parameters = [
        {"name": "direction",           "default": 1,    "min": -1,  "max": 1,               "unit": "+1=extension / -1=flexion"},
        {"name": "active_end",          "default": 30.0, "min": 1.0, "max": WRIST_LIMIT_DEG, "unit": "deg"},
        {"name": "passive_end",         "default": 50.0, "min": 1.0, "max": WRIST_LIMIT_DEG, "unit": "deg"},
        {"name": "speed_deg_s",         "default": 20.0, "min": 1.0, "max": 80.0,            "unit": "wrist deg/s"},
        {"name": "active_timeout_s",    "default":  5.0, "min": 1.0, "max": 30.0,            "unit": "s"},
        {"name": "pre_passive_pause_s", "default":  0.0, "min": 0.0, "max": 10.0,            "unit": "s (0=skip)"},
        {"name": "damper_pwm",          "default": 0,    "min": 0,   "max": 255,             "unit": "PWM (active/passive)"},
        {"name": "hold_time_s",         "default": 3.0,  "min": 0.5, "max": 30.0,            "unit": "s"},
        {"name": "return_time_s",       "default": 3.0,  "min": 0.0, "max": 30.0,            "unit": "s (user return window, 0=skip)"},
        {"name": "rest_time_s",         "default": 3.0,  "min": 0.5, "max": 30.0,            "unit": "s"},
        {"name": "total_reps",          "default": 1,    "min": 1,   "max": 50,              "unit": "reps"},
        {"name": "damper_on_return",    "default": 1,    "min": 0,   "max": 1,               "unit": "0=off, 1=on"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase       = "active"
        self._drive_pos      = None
        self._active_t       = 0.0
        self._pause_t        = 0.0
        self._hold_t         = 0.0
        self._return_t       = 0.0
        self._rest_t         = 0.0
        self._timed_out      = False
        self._rep            = 0
        self.on_phase_change = None

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def compute(self, state: dict) -> Command:
        wrist        = state["wrist_deg"]
        dt           = state["dt"]
        direction    = 1 if int(round(self.params["direction"])) >= 0 else -1
        physical_dir = direction * self.handedness
        damper       = int(max(0, min(255, round(self.params["damper_pwm"]))))
        active_end   = abs(self.params["active_end"])
        passive_end  = abs(self.params["passive_end"])
        speed        = abs(self.params["speed_deg_s"])
        timeout      = float(self.params.get("active_timeout_s",    self.ACTIVE_TIMEOUT_S))
        pause_dur    = float(self.params.get("pre_passive_pause_s", 0.0))
        hold_dur     = float(self.params.get("hold_time_s",   3.0))
        return_dur   = float(self.params.get("return_time_s", 3.0))
        rest_dur     = float(self.params.get("rest_time_s",   3.0))
        damper_ret   = int(round(self.params.get("damper_on_return", 1)))

        # ── active ────────────────────────────────────────────────────────────
        if self.sub_phase == "active":
            self._active_t += dt
            timed_out = self._active_t >= timeout
            reached   = wrist * physical_dir >= active_end
            if reached or timed_out:
                self._drive_pos = wrist
                self._active_t  = 0.0
                self._timed_out = timed_out and not reached
                self._pause_t   = 0.0
                self._notify("active", "pre_passive")
            return Command(servo=None, damper=damper)

        if self.sub_phase == "pre_passive":
            self._pause_t += dt
            if self._pause_t >= pause_dur:
                self._notify("pre_passive", "passive")
            return Command(servo=self._drive_pos, damper=damper)  # 始终 hold

        # ── passive: servo drives to passive_end ──────────────────────────────
        if self.sub_phase == "passive":
            target_signed = physical_dir * passive_end
            gap  = target_signed - self._drive_pos
            step = math.copysign(min(abs(gap), speed * dt), gap)
            self._drive_pos += step
            if abs(self._drive_pos - target_signed) < 0.5:
                self._drive_pos = target_signed
                self._hold_t    = 0.0
                self._notify("passive", "hold")
            return Command(servo=self._drive_pos, damper=0)

        # ── hold: hold z s at passive_end ─────────────────────────────────────
        if self.sub_phase == "hold":
            target_signed = physical_dir * passive_end
            self._hold_t += dt
            if self._hold_t >= hold_dur:
                self._hold_t   = 0.0
                self._return_t = 0.0
                if return_dur <= 0.0:
                    self._drive_pos = wrist
                    self._notify("hold", "servo_back")
                else:
                    self._notify("hold", "return")
            return Command(servo=target_signed, damper=damper)

        # ── return: torque off, damper on, user has a s to return ─────────────
        if self.sub_phase == "return":
            self._return_t += dt
            if self._return_t >= return_dur:
                self._return_t  = 0.0
                self._drive_pos = wrist
                self._notify("return", "servo_back")
            ret_dmp = 255 if damper_ret else 0
            return Command(servo=None, damper=ret_dmp)

        # ── servo_back: servo on, damper off, bring back to 0 ────────────────
        if self.sub_phase == "servo_back":
            gap  = 0.0 - self._drive_pos
            step = math.copysign(min(abs(gap), speed * dt), gap) if gap != 0 else 0.0
            self._drive_pos += step
            if abs(self._drive_pos) < 0.3:
                self._drive_pos = 0.0
                self._rest_t    = 0.0
                self._rep      += 1
                if self._rep >= int(round(self.params["total_reps"])):
                    self._notify("servo_back", "done")
                    self._done = True
                else:
                    self._notify("servo_back", "rest")
            return Command(servo=self._drive_pos, damper=0)

        # ── rest: torque on, hold at 0, rest b s ─────────────────────────────
        if self.sub_phase == "rest":
            self._rest_t += dt
            if self._rest_t >= rest_dur:
                self._rest_t    = 0.0
                self._hold_t    = 0.0
                self._drive_pos = None
                self._active_t  = 0.0
                self._pause_t   = 0.0
                self._timed_out = False
                self._notify("rest", "active")
            return Command(servo=0.0, damper=damper)

        # ── done ──────────────────────────────────────────────────────────────
        if self.sub_phase == "done":
            return Command(servo=None, damper=damper)

        return Command(servo=None, damper=0)

    @property
    def phase(self) -> str:
        d       = int(round(self.params.get("direction", 1)))
        dir_str = "EXTEND" if d >= 0 else "FLEX"
        ae  = self.params.get("active_end",  "?")
        pe  = self.params.get("passive_end", "?")
        total = int(round(self.params.get("total_reps", 1)))
        return f"{self.sub_phase} | rep={self._rep+1}/{total} | {dir_str} | A={ae}° P={pe}°"

    @property
    def rep(self) -> int:
        return self._rep

    @property
    def hold_t(self) -> float:
        return self._hold_t

    @property
    def return_t(self) -> float:
        return self._return_t

    @property
    def rest_t(self) -> float:
        return self._rest_t

    @property
    def active_t(self) -> float:
        return self._active_t

    @property
    def pause_t(self) -> float:
        return self._pause_t

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    @property
    def drive_speed(self) -> float:
        return float(self.params.get("speed_deg_s", 0.0))

    @property
    def drive_pos(self) -> float:
        return self._drive_pos or 0.0
