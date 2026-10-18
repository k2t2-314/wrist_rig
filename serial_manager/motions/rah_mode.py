"""
motions/rah_mode.py
"""

import math

from motions.base import ModeBase, Command
from config import WRIST_LIMIT_DEG


class RAHMode(ModeBase):
    """LC ramp-and-hold with an absolute time/%MVC target schedule.

    The motor holds the starting wrist angle. Scheduled velocity perturbations
    move away from that angle and return to it using encoder feedback.
    """

    name        = "ramp_and_hold"
    description = "RAH_LC: hold wrist angle and follow a force target schedule"

    parameters = [{'name': 'direction', 'default': 1, 'min': -1, 'max': 1, 'unit': '+1=extension / -1=flexion'}, {'name': 'mvc_value', 'default': 10.0, 'min': 0.1, 'max': 500.0, 'unit': 'N or Nm'}, {'name': 'total_reps', 'default': 3, 'min': 1, 'max': 50, 'unit': 'trials'}, {'name': 'profile_points', 'default': None, 'unit': 'optional absolute (time_s, %MVC) points'}, {'name': 'perturbation_events', 'default': None, 'unit': 'optional absolute perturbation events'}, {'name': 'hold_step_enabled', 'default': 0, 'min': 0, 'max': 1, 'unit': '0=off, 1=on'}]

    PERTURB_REACHED_TOL_DEG = 0.5

    def reset(self):
        super().reset()
        self.sub_phase = "idle"
        self._base_hold_pos = 0.0
        self._hold_pos = 0.0
        self._start_t = None
        self._perturb_done_events = set()
        self._perturb_active = False
        self._perturb_stage = "idle"
        self._perturb_rep = None
        self._perturb_event_key = None
        self._perturb_relative_direction = 1.0
        self._perturb_step_deg = None
        self._perturb_speed_deg_s = None
        self._perturb_target = None
        self._perturb_prev_gap = None
        self._rep = 0

    def start(self, wrist_deg: float):
        self._base_hold_pos = wrist_deg
        self._hold_pos = wrist_deg
        self.sub_phase = "rest"
        self._start_t = None
        self._perturb_done_events = set()
        self._perturb_active = False
        self._perturb_stage = "idle"
        self._perturb_rep = None
        self._perturb_event_key = None
        self._perturb_relative_direction = 1.0
        self._perturb_step_deg = None
        self._perturb_speed_deg_s = None
        self._perturb_target = None
        self._perturb_prev_gap = None
        self._rep = 0


    def _custom_profile_points(self) -> list[tuple[float, float]]:
        raw = self.params.get("profile_points", None)
        if not isinstance(raw, (list, tuple)):
            return []
        points = []
        for item in raw:
            try:
                time_s, pct = item[:2]
                points.append((float(time_s), float(pct)))
            except (TypeError, ValueError):
                continue
        return sorted(points, key=lambda item: item[0])

    def has_custom_profile(self) -> bool:
        return len(self._custom_profile_points()) >= 2

    def _custom_profile_pct(self, elapsed_s: float) -> float:
        points = self._custom_profile_points()
        if not points:
            return 0.0
        elapsed = max(0.0, float(elapsed_s))
        if elapsed <= points[0][0]:
            return max(0.0, points[0][1])
        for (t0, value0), (t1, value1) in zip(points, points[1:]):
            if elapsed <= t1:
                if t1 <= t0:
                    return max(0.0, value1)
                fraction = (elapsed - t0) / (t1 - t0)
                return max(0.0, value0 + fraction * (value1 - value0))
        return max(0.0, points[-1][1])

    def custom_rep_count(self) -> int:
        points = self._custom_profile_points()
        if not points:
            return max(1, int(round(float(self.params.get("total_reps", 3)))))
        count = 1 if points[0][1] > 0.0 else 0
        count += sum(1 for current, following in zip(points, points[1:])
                     if current[1] <= 0.0 < following[1])
        return max(1, count)

    def _custom_rep_at(self, elapsed_s: float) -> int:
        points = self._custom_profile_points()
        if not points:
            return 1
        count = 1 if points[0][1] > 0.0 else 0
        elapsed = max(0.0, float(elapsed_s))
        for current, following in zip(points, points[1:]):
            if current[0] > elapsed:
                break
            if current[1] <= 0.0 < following[1]:
                count += 1
        return max(1, min(self.custom_rep_count(), count or 1))

    def target_pct_at(self, elapsed_s: float | None=None, rep: int | None=None) -> float:
        return self._custom_profile_pct(0.0 if elapsed_s is None else elapsed_s)

    def target_pct_values(self) -> list[float]:
        return [max(0.0, value) for (_time_s, value) in self._custom_profile_points()]

    def target_value(self, rep: int | None = None, elapsed_s: float | None = None) -> float:
        mvc = float(self.params.get("mvc_value", 10.0))
        pct = self.target_pct_at(elapsed_s=elapsed_s, rep=rep)
        return mvc * pct / 100.0


    def total_duration(self) -> float:
        return max(0.0, self._custom_profile_points()[-1][0])

    def profile_value(self, elapsed_s: float) -> float:
        mvc = float(self.params.get('mvc_value', 10.0))
        return mvc * self._custom_profile_pct(elapsed_s) / 100.0

    def profile_fraction(self, elapsed_s: float) -> float:
        maximum = max((value for (_time_s, value) in self._custom_profile_points()), default=0.0)
        return self._custom_profile_pct(elapsed_s) / maximum if maximum > 0.0 else 0.0

    def phase_at(self, elapsed_s: float) -> tuple[str, int, float]:
        points = self._custom_profile_points()
        elapsed = max(0.0, float(elapsed_s))
        rep = self._custom_rep_at(elapsed)
        if elapsed >= points[-1][0]:
            return ('done', self.custom_rep_count(), 0.0)
        for ((t0, value0), (t1, value1)) in zip(points, points[1:]):
            if elapsed < t1:
                delta = value1 - value0
                if delta > 1e-09:
                    phase = 'rise'
                elif delta < -1e-09:
                    phase = 'release'
                elif max(value0, value1) > 1e-09:
                    phase = 'hold'
                else:
                    phase = 'rest'
                return (phase, rep, max(0.0, elapsed - t0))
        return ('done', self.custom_rep_count(), 0.0)


    def _step_direction_value(self, value) -> float:
        text = str(value).strip().lower()
        if text in ("-", "opposite", "opp", "o", "reverse", "against"):
            return -1.0
        return 1.0


    def _custom_perturbation_events(self) -> list[tuple[float, float, float, float]]:
        raw = self.params.get("perturbation_events", None)
        if not isinstance(raw, (list, tuple)):
            return []
        events = []
        for item in raw:
            try:
                time_s, direction, speed, angle = item[:4]
                events.append((
                    float(time_s),
                    self._step_direction_value(direction),
                    abs(float(angle)),
                    abs(float(speed)),
                ))
            except (TypeError, ValueError):
                continue
        return sorted(events, key=lambda item: item[0])

    def _target_step_pos(self, relative_direction: float, step_deg: float) -> float:
        step_deg = abs(float(step_deg))
        rah_direction = 1.0 if float(self.params.get("direction", 1)) >= 0.0 else -1.0
        target = self._base_hold_pos + rah_direction * relative_direction * step_deg
        return max(-WRIST_LIMIT_DEG, min(WRIST_LIMIT_DEG, target))

    def _clear_perturbation(self, mark_done: bool = False):
        if mark_done and self._perturb_event_key is not None:
            self._perturb_done_events.add(self._perturb_event_key)
        self._perturb_active = False
        self._perturb_stage = "idle"
        self._perturb_rep = None
        self._perturb_event_key = None
        self._perturb_relative_direction = 1.0
        self._perturb_step_deg = None
        self._perturb_speed_deg_s = None
        self._perturb_target = None
        self._perturb_prev_gap = None

    def _hold_command(self, velocity_override=None) -> Command:
        self._hold_pos = self._base_hold_pos
        return Command(servo=self._base_hold_pos, damper=255, velocity=velocity_override)

    def compute(self, state: dict) -> Command:
        if self.sub_phase == 'idle':
            return Command(servo=None, damper=0)
        now = float(state.get('t', 0.0))
        if self._start_t is None:
            self._start_t = now
        elapsed = max(0.0, now - self._start_t)
        (phase, rep, phase_t) = self.phase_at(elapsed)
        self.sub_phase = phase
        self._rep = max(0, rep - 1)
        step_enabled = bool(int(round(float(self.params.get('hold_step_enabled', 0) or 0))))
        reps = max(1, int(round(float(self.params.get('total_reps', 3)))))
        hold = max(0.0, float(self.params.get('hold_time_s', 10.0)))
        custom_events = self._custom_perturbation_events()
        schedule = {}
        perturb_marker = None
        if phase == 'done':
            self._done = True
            self._clear_perturbation(mark_done=True)
            self._hold_pos = self._base_hold_pos
            return Command(servo=self._hold_pos, damper=255, velocity=0.0)
        if step_enabled and (not self._perturb_active):
            scheduled_events = custom_events
            event_clock = elapsed
            for (event_index, (event_t, relative_direction, step_deg, speed_deg_s)) in enumerate(scheduled_events):
                event_key = ('custom', event_index)
                if event_key in self._perturb_done_events or event_clock < event_t:
                    continue
                self._perturb_active = True
                self._perturb_stage = 'out'
                self._perturb_rep = rep
                self._perturb_event_key = event_key
                self._perturb_relative_direction = relative_direction
                self._perturb_step_deg = step_deg
                self._perturb_speed_deg_s = speed_deg_s
                self._perturb_target = self._target_step_pos(relative_direction=relative_direction, step_deg=step_deg)
                self._perturb_prev_gap = None
                perturb_marker = 5
                break
        if not self._perturb_active:
            return self._hold_command()
        target_hold_pos = float(self._perturb_target if self._perturb_target is not None else self._base_hold_pos)
        wrist_value = state.get('wrist_deg', self._hold_pos)
        wrist = self._hold_pos if wrist_value is None else float(wrist_value)
        self._hold_pos = wrist
        default_speed = abs(float(self.params.get('hold_step_speed_deg_s', 100.0) or 100.0))
        speed = max(0.001, abs(float(self._perturb_speed_deg_s if self._perturb_speed_deg_s is not None else default_speed)))
        for _ in range(2):
            target = target_hold_pos if self._perturb_stage == 'out' else self._base_hold_pos
            gap = target - wrist
            crossed = self._perturb_prev_gap is not None and gap * self._perturb_prev_gap <= 0.0
            reached = abs(gap) <= self.PERTURB_REACHED_TOL_DEG or crossed
            if reached:
                if self._perturb_stage == 'out':
                    self._perturb_stage = 'return'
                    self._perturb_prev_gap = None
                    continue
                self._hold_pos = self._base_hold_pos
                self._clear_perturbation(mark_done=True)
                return Command(servo=self._hold_pos, damper=255, velocity=0.0, marker_pulse=perturb_marker)
            self._perturb_prev_gap = gap
            return Command(servo=None, damper=255, velocity=math.copysign(speed, gap), marker_pulse=perturb_marker)
        self._hold_pos = self._base_hold_pos
        self._clear_perturbation(mark_done=True)
        return Command(servo=self._hold_pos, damper=255, velocity=0.0, marker_pulse=perturb_marker)

    @property
    def hold_pos(self) -> float:
        return self._hold_pos

    @property
    def phase(self) -> str:
        total = self.custom_rep_count()
        return f'{self.sub_phase} | trial={self._rep + 1}/{total} | hold={self._hold_pos:.1f} deg | perturb=speed'

    @property
    def rep(self) -> int:
        return self._rep

