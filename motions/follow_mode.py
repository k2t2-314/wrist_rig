"""
motions/follow_mode.py
"""

from motions.base import ModeBase, Command


class FollowMode(ModeBase):
    """
    Follow mode - participant moves freely while tracking a timed guide ball.

    Per repetition:
        go      : guide ball moves from 0 to target
        hold    : guide ball stays at target
        return  : guide ball moves from target back to 0
        rest    : guide ball stays at 0 before next rep

    Marker sequence per rep:
        0->1  guide starts moving away from zero
        1->2  guide reaches end target
        2->3  guide starts returning
        3->0  guide reaches zero
    """

    name = "follow"
    description = "Follow: participant tracks a moving guide ball"

    PHASE_TO_MARKER = {
        "idle": 0,
        "go": 1,
        "hold": 2,
        "return": 3,
        "rest": 0,
        "done": 0,
    }

    parameters = [
        {"name": "direction", "default": 1, "min": -1, "max": 1, "unit": "+1=extension / -1=flexion"},
        {"name": "target_deg", "default": 30.0, "min": 1.0, "max": 150.0, "unit": "wrist deg"},
        {"name": "speed_deg_s", "default": 20.0, "min": 1.0, "max": 150.0, "unit": "wrist deg/s"},
        {"name": "hold_time_s", "default": 2.0, "min": 0.0, "max": 30.0, "unit": "s"},
        {"name": "rest_time_s", "default": 2.0, "min": 0.0, "max": 30.0, "unit": "s"},
        {"name": "damper_pwm", "default": 0, "min": 0, "max": 255, "unit": "PWM"},
        {"name": "total_reps", "default": 1, "min": 1, "max": 50, "unit": "reps"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase = "idle"
        self.green_pos = 0.0
        self._rep = 0
        self._hold_t = 0.0
        self._rest_t = 0.0
        self.on_phase_change = None

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def start_go(self):
        self.green_pos = 0.0
        self._notify("idle", "go")

    def compute(self, state: dict) -> Command:
        dt = float(state["dt"])
        direction = 1 if int(round(self.params.get("direction", 1))) >= 0 else -1
        phys_dir = direction * self.handedness
        target = abs(float(self.params.get("target_deg", 30.0)))
        speed = max(0.001, float(self.params.get("speed_deg_s", 20.0)))
        hold_dur = max(0.0, float(self.params.get("hold_time_s", 2.0)))
        rest_dur = max(0.0, float(self.params.get("rest_time_s", 2.0)))
        damper = int(max(0, min(255, round(self.params.get("damper_pwm", 0)))))
        total_reps = int(round(self.params.get("total_reps", 1)))

        target_pos = phys_dir * target
        step = speed * dt

        if self.sub_phase == "idle":
            self.green_pos = 0.0
            return Command(servo=None, damper=damper)

        if self.sub_phase == "go":
            if target_pos >= self.green_pos:
                self.green_pos = min(target_pos, self.green_pos + step)
            else:
                self.green_pos = max(target_pos, self.green_pos - step)
            if abs(self.green_pos - target_pos) <= 1e-9:
                self.green_pos = target_pos
                self._hold_t = 0.0
                self._notify("go", "hold")
            return Command(servo=None, damper=damper)

        if self.sub_phase == "hold":
            self.green_pos = target_pos
            self._hold_t += dt
            if self._hold_t >= hold_dur:
                self._notify("hold", "return")
            return Command(servo=None, damper=damper)

        if self.sub_phase == "return":
            if self.green_pos >= 0.0:
                self.green_pos = max(0.0, self.green_pos - step)
            else:
                self.green_pos = min(0.0, self.green_pos + step)
            if abs(self.green_pos) <= 1e-9:
                self.green_pos = 0.0
                self._rest_t = 0.0
                self._notify("return", "rest")
            return Command(servo=None, damper=damper)

        if self.sub_phase == "rest":
            self.green_pos = 0.0
            self._rest_t += dt
            if self._rest_t >= rest_dur:
                self._rep += 1
                self._rest_t = 0.0
                if self._rep >= total_reps:
                    self._notify("rest", "done")
                    self._done = True
                else:
                    self._notify("rest", "go")
            return Command(servo=None, damper=damper)

        if self.sub_phase == "done":
            self.green_pos = 0.0
            return Command(servo=None, damper=0)

        return Command(servo=None, damper=damper)

    @property
    def phase(self) -> str:
        total = int(round(self.params.get("total_reps", 1)))
        direction = int(round(self.params.get("direction", 1)))
        dir_str = "EXTEND" if direction >= 0 else "FLEX"
        return f"{self.sub_phase} | rep={self._rep+1}/{total} | {dir_str}"

    @property
    def rep(self) -> int:
        return self._rep

    @property
    def hold_t(self) -> float:
        return self._hold_t

    @property
    def rest_t(self) -> float:
        return self._rest_t
