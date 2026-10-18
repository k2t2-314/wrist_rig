"""
Direction reversal mode.

The default task holds the current wrist angle with the damper fully on while
the subject display alternates rest periods and operator-selected direction
cues.  Optional dynamics mode releases the servo during extension/flexion cues
and then uses velocity mode to return to the launch angle.
"""

import math

from config import WRIST_LIMIT_DEG
from motions.base import ModeBase, Command


class DirectionReversalMode(ModeBase):
    name = "direction_reversal"
    description = "fixed-angle direction reversal cue task"
    parameters = [
        {"name": "hold_angle_deg", "default": 0.0},
        {"name": "rest_time_s", "default": 5.0},
        {"name": "instruction_time_s", "default": 5.0},
        {"name": "rise_time_s", "default": 5.0},
        {"name": "hold_time_s", "default": 5.0},
        {"name": "down_time_s", "default": 5.0},
        {"name": "target_force_value", "default": 10.0},
        {"name": "feedback_channel", "default": "Fx"},
        {"name": "total_reps", "default": 10},
        {"name": "instructions", "default": []},
        {"name": "damper_pwm", "default": 255},
        {"name": "dynamics_enabled", "default": False},
        {"name": "dynamics_angle_deg", "default": 10.0},
        {"name": "dynamics_return_speed_deg_s", "default": 20.0},
    ]

    MARKER_REST = 1
    MARKER_EXTENSION = 2
    MARKER_FLEXION = 3
    MARKER_REST_INSTRUCTION = 4
    INSTRUCTION_MARKERS = {
        "extension": MARKER_EXTENSION,
        "flexion": MARKER_FLEXION,
        "rest": MARKER_REST_INSTRUCTION,
    }
    REACHED_TOL_DEG = 0.5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_phase_change = None
        self.reset()

    def reset(self):
        super().reset()
        self.sub_phase = "idle"
        self._rep = 0
        self._phase_t = 0.0
        self._elapsed = 0.0
        self._last_elapsed = None
        self._current_instruction = "rest"
        self._prev_return_gap = None
        self.hold_pos = float(self.params.get("hold_angle_deg", 0.0) or 0.0)

    def start(self, wrist_deg: float):
        self.hold_pos = float(wrist_deg)
        self.params["hold_angle_deg"] = self.hold_pos
        self._done = False
        self.sub_phase = "rest"
        self._rep = 0
        self._phase_t = 0.0
        self._elapsed = 0.0
        self._last_elapsed = None
        self._prev_return_gap = None
        self._current_instruction = self._instruction_for_rep(1)

    @property
    def rep(self) -> int:
        return self._rep

    @property
    def phase(self) -> str:
        return self.sub_phase


    def total_reps(self) -> int:
        try:
            return max(1, int(round(float(self.params.get("total_reps", 10) or 10))))
        except Exception:
            return 10

    def rest_time(self) -> float:
        try:
            return max(0.0, float(self.params.get("rest_time_s", 5.0) or 0.0))
        except Exception:
            return 5.0

    def rise_time(self) -> float:
        try:
            return max(0.0, float(self.params.get("rise_time_s", 5.0) or 0.0))
        except Exception:
            return 5.0

    def hold_time(self) -> float:
        try:
            return max(0.0, float(self.params.get("hold_time_s", 5.0) or 0.0))
        except Exception:
            return 5.0

    def down_time(self) -> float:
        try:
            return max(0.0, float(self.params.get("down_time_s", 5.0) or 0.0))
        except Exception:
            return 5.0

    def instruction_time(self) -> float:
        if not self.dynamics_enabled():
            return self.rise_time() + self.hold_time() + self.down_time()
        try:
            return max(0.0, float(self.params.get("instruction_time_s", 5.0) or 0.0))
        except Exception:
            return 5.0

    def dynamics_enabled(self) -> bool:
        return bool(self.params.get("dynamics_enabled", False))

    def dynamics_angle(self) -> float:
        try:
            return max(0.0, float(self.params.get("dynamics_angle_deg", 10.0) or 0.0))
        except Exception:
            return 10.0

    def dynamics_return_speed(self) -> float:
        try:
            return max(1.0, abs(float(self.params.get("dynamics_return_speed_deg_s", 20.0) or 20.0)))
        except Exception:
            return 20.0

    def total_duration(self) -> float:
        return self.total_reps() * (self.rest_time() + self.instruction_time()) + self.rest_time()

    def target_force_value(self) -> float:
        try:
            return max(0.01, abs(float(self.params.get("target_force_value", 10.0) or 10.0)))
        except Exception:
            return 10.0

    def cycle_duration(self) -> float:
        return self.rest_time() + self.instruction_time()

    def instruction_profile_fraction_at(self, instruction_t: float) -> float:
        rise = self.rise_time()
        hold = self.hold_time()
        down = self.down_time()
        t = max(0.0, float(instruction_t))
        if rise > 1e-9 and t < rise:
            return t / rise
        t -= rise
        if t < hold:
            return 1.0
        t -= hold
        if down > 1e-9 and t < down:
            return 1.0 - t / down
        return 0.0

    def profile_fraction(self, elapsed_s: float) -> float:
        phase, _rep, phase_t = self._phase_at(max(0.0, float(elapsed_s)))
        if phase != "instruction":
            return 0.0
        return self.instruction_profile_fraction_at(phase_t)

    def profile_sign(self, elapsed_s: float | None = None, rep: int | None = None) -> int:
        if rep is None:
            if elapsed_s is None:
                rep = self._rep + 1
            else:
                _phase, rep, _phase_t = self._phase_at(max(0.0, float(elapsed_s)))
        cue = self._instruction_for_rep(max(1, int(rep)))
        if cue == "extension":
            return 1
        if cue == "flexion":
            return -1
        return 0

    def profile_value(self, elapsed_s: float) -> float:
        return self.profile_sign(elapsed_s=elapsed_s) * self.target_force_value() * self.profile_fraction(elapsed_s)

    def phase_remaining(self) -> float:
        if self.sub_phase in ("rest", "final_rest"):
            return max(0.0, self.rest_time() - self._phase_t)
        if self.sub_phase == "instruction":
            return max(0.0, self.instruction_time() - self._phase_t)
        return 0.0

    def marker_code(self) -> int:
        if self.sub_phase in ("rest", "final_rest", "return"):
            return self.MARKER_REST
        if self.sub_phase == "instruction":
            return self.INSTRUCTION_MARKERS.get(self._current_instruction, self.MARKER_REST_INSTRUCTION)
        return 0 if self.sub_phase == "done" else self.MARKER_REST

    def target_angle_for_current_instruction(self) -> float:
        if self._current_instruction == "extension":
            return min(WRIST_LIMIT_DEG, self.hold_pos + self.dynamics_angle())
        if self._current_instruction == "flexion":
            return max(-WRIST_LIMIT_DEG, self.hold_pos - self.dynamics_angle())
        return self.hold_pos

    def _instruction_for_rep(self, rep_number: int) -> str:
        instructions = self.params.get("instructions", []) or []
        if rep_number - 1 < len(instructions):
            raw = str(instructions[rep_number - 1]).strip().lower()
        else:
            raw = "extension" if rep_number % 2 == 1 else "flexion"
        if raw in ("ext", "extension"):
            return "extension"
        if raw in ("flx", "flex", "flexion"):
            return "flexion"
        return "rest"

    def _phase_at(self, elapsed: float):
        rest_t = self.rest_time()
        cue_t = self.instruction_time()
        reps = self.total_reps()
        cycle = rest_t + cue_t
        main_duration = reps * cycle
        total_duration = main_duration + rest_t

        if elapsed >= total_duration:
            return "done", reps, 0.0
        if elapsed >= main_duration:
            return "final_rest", reps, elapsed - main_duration
        if cycle <= 1e-9:
            return "done", reps, 0.0

        rep_index = int(elapsed // cycle)
        phase_t = elapsed - rep_index * cycle
        if phase_t < rest_t:
            return "rest", rep_index + 1, phase_t
        return "instruction", rep_index + 1, phase_t - rest_t

    def _set_phase(self, new_phase: str):
        old = self.sub_phase
        if old == new_phase:
            return
        self.sub_phase = new_phase
        self._phase_t = 0.0
        self._prev_return_gap = None
        if new_phase == "instruction":
            self._current_instruction = self._instruction_for_rep(self._rep + 1)
        callback = self.on_phase_change
        if callback is not None:
            try:
                callback(old, new_phase)
            except Exception:
                pass

    def _compute_standard(self, elapsed: float) -> Command:
        phase, rep_number, phase_t = self._phase_at(elapsed)

        self._elapsed = elapsed
        self.sub_phase = phase
        self._rep = max(0, min(self.total_reps() - 1, rep_number - 1))
        self._phase_t = max(0.0, phase_t)
        self._current_instruction = self._instruction_for_rep(rep_number)

        if phase == "done":
            self._done = True

        damper = self._damper()
        return Command(servo=self.hold_pos, damper=damper)

    def _damper(self) -> int:
        damper = int(round(float(self.params.get("damper_pwm", 255) or 255)))
        return max(0, min(255, damper))

    def _advance_dynamic_phase(self, dt: float):
        self._phase_t += max(0.0, dt)
        if self.sub_phase == "rest" and self._phase_t >= self.rest_time():
            self._set_phase("instruction")
        elif self.sub_phase == "instruction" and self._phase_t >= self.instruction_time():
            if self._current_instruction in ("extension", "flexion"):
                self._set_phase("return")
            else:
                self._advance_rep_after_return()
        elif self.sub_phase == "final_rest" and self._phase_t >= self.rest_time():
            self._set_phase("done")
            self._done = True

    def _advance_rep_after_return(self):
        reps = self.total_reps()
        if self._rep + 1 >= reps:
            self._set_phase("final_rest")
        else:
            self._rep += 1
            self._current_instruction = self._instruction_for_rep(self._rep + 1)
            self._set_phase("rest")

    def _return_command(self, wrist: float, damper: int) -> Command:
        gap = self.hold_pos - wrist
        crossed = self._prev_return_gap is not None and gap * self._prev_return_gap <= 0.0
        reached = abs(gap) <= self.REACHED_TOL_DEG or crossed
        if reached:
            self._prev_return_gap = None
            self._advance_rep_after_return()
            return Command(servo=self.hold_pos, damper=damper, velocity=0.0)

        self._prev_return_gap = gap
        velocity = math.copysign(self.dynamics_return_speed(), gap)
        return Command(servo=None, damper=damper, velocity=velocity)

    def _compute_dynamic(self, state: dict, elapsed: float) -> Command:
        if self._last_elapsed is None:
            dt = 0.0
        else:
            dt = max(0.0, elapsed - self._last_elapsed)
        self._last_elapsed = elapsed
        self._elapsed = elapsed

        try:
            wrist = float(state.get("wrist_deg", self.hold_pos) or self.hold_pos)
        except Exception:
            wrist = self.hold_pos
        damper = self._damper()

        self._advance_dynamic_phase(dt)

        if self.sub_phase == "done":
            self._done = True
            return Command(servo=self.hold_pos, damper=damper, velocity=0.0)
        if self.sub_phase == "return":
            return self._return_command(wrist, 0)
        if self.sub_phase == "instruction" and self._current_instruction in ("extension", "flexion"):
            return Command(servo=None, damper=0)
        return Command(servo=self.hold_pos, damper=damper)

    def compute(self, state: dict) -> Command:
        if self.sub_phase == "idle":
            return Command(servo=None, damper=0)

        try:
            elapsed = max(0.0, float(state.get("t", 0.0) or 0.0))
        except Exception:
            elapsed = 0.0

        if self.dynamics_enabled():
            return self._compute_dynamic(state, elapsed)
        return self._compute_standard(elapsed)
