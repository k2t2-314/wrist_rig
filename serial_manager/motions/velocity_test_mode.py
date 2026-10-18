"""Velocity perturbation calibration mode."""

import math

from config import WRIST_LIMIT_DEG
from motions.base import Command, ModeBase


class VelocityTestMode(ModeBase):
    """Run scheduled out-and-back velocity perturbations around the launch angle."""

    name = "velocity_test"
    description = "Velocity test: fixed-interval perturbations at the current wrist angle"

    parameters = [
        {"name": "events", "default": None},
        {"name": "interval_s", "default": 3.0, "min": 0.1, "unit": "s"},
    ]

    REACHED_TOL_DEG = 0.5
    FIRST_EVENT_DELAY_S = 3.0
    FINAL_SETTLE_DELAY_S = 3.0

    def reset(self):
        super().reset()
        self.sub_phase = "idle"
        self._base_pos = 0.0
        self._hold_pos = 0.0
        self._start_t = None
        self._events = self._normalized_events()
        self._results = []
        self._active_index = None
        self._stage = "idle"
        self._target = None
        self._prev_gap = None
        self._stage_deadline = None
        self._event_issue = ""
        self._pending_marker = None
        self._last_event_completed_s = None

    @staticmethod
    def _direction_sign(value) -> float:
        text = str(value).strip().lower()
        return -1.0 if text in ("-", "negative", "minus", "opposite") else 1.0

    def _normalized_events(self):
        normalized = []
        for item in self.params.get("events", None) or []:
            try:
                if isinstance(item, dict):
                    speed = item.get("velocity_deg_s", item.get("speed_deg_s"))
                    angle = item.get("angle_deg")
                    direction = item.get("direction", "+")
                else:
                    speed, angle, direction = item[:3]
                speed = abs(float(speed))
                angle = abs(float(angle))
                if speed <= 0.0 or angle <= 0.0:
                    continue
                normalized.append({
                    "velocity_deg_s": speed,
                    "angle_deg": angle,
                    "direction": "+" if self._direction_sign(direction) > 0.0 else "-",
                })
            except (TypeError, ValueError):
                continue
        return normalized

    def start(self, wrist_deg: float):
        self.reset()
        self._base_pos = float(wrist_deg)
        self._hold_pos = float(wrist_deg)
        interval = self.interval_s
        self._results = [
            {
                "event": index + 1,
                "scheduled_time_s": self.FIRST_EVENT_DELAY_S + index * interval,
                "direction": event["direction"],
                "commanded_velocity_deg_s": event["velocity_deg_s"],
                "commanded_angle_deg": event["angle_deg"],
                "base_wrist_deg": self._base_pos,
                "target_wrist_deg": self._clamped_target(event),
                "actual_start_s": None,
                "start_delay_s": None,
                "completed_time_s": None,
                "measured_peak_velocity_deg_s": 0.0,
                "measured_peak_displacement_deg": 0.0,
                "peak_wrist_deg": self._base_pos,
                "status": "pending",
            }
            for index, event in enumerate(self._events)
        ]
        self.sub_phase = "waiting"

    @property
    def interval_s(self) -> float:
        return max(0.1, float(self.params.get("interval_s", 3.0)))

    def total_duration(self) -> float:
        if self._last_event_completed_s is not None:
            return self._last_event_completed_s + self.FINAL_SETTLE_DELAY_S
        if not self._events:
            return self.FIRST_EVENT_DELAY_S + self.FINAL_SETTLE_DELAY_S
        final_event = self._events[-1]
        final_start_s = (
            self.FIRST_EVENT_DELAY_S + (len(self._events) - 1) * self.interval_s)
        nominal_round_trip_s = (
            2.0 * final_event["angle_deg"] / final_event["velocity_deg_s"])
        return final_start_s + nominal_round_trip_s + self.FINAL_SETTLE_DELAY_S

    def _clamped_target(self, event) -> float:
        direction = self._direction_sign(event["direction"])
        target = self._base_pos + direction * float(event["angle_deg"])
        return max(-WRIST_LIMIT_DEG, min(WRIST_LIMIT_DEG, target))

    def _hold_command(self, velocity=None):
        self._hold_pos = self._base_pos
        return Command(servo=self._base_pos, damper=255, velocity=velocity)

    def _start_event(self, index: int, elapsed: float, wrist: float):
        event = self._events[index]
        result = self._results[index]
        result["actual_start_s"] = elapsed
        result["start_delay_s"] = max(0.0, elapsed - result["scheduled_time_s"])
        result["base_wrist_deg"] = self._base_pos
        result["target_wrist_deg"] = self._clamped_target(event)
        result["peak_wrist_deg"] = wrist
        result["status"] = "active"
        self._active_index = index
        self._stage = "out"
        self._target = result["target_wrist_deg"]
        self._prev_gap = None
        expected_leg_s = event["angle_deg"] / event["velocity_deg_s"]
        self._stage_deadline = elapsed + max(0.75, 3.0 * expected_leg_s + 0.5)
        self._event_issue = ""
        self._pending_marker = 5
        self.sub_phase = f"event {index + 1} out"

    def _update_peaks(self, wrist: float, velocity: float):
        if self._active_index is None:
            return
        result = self._results[self._active_index]
        result["measured_peak_velocity_deg_s"] = max(
            float(result["measured_peak_velocity_deg_s"]), abs(float(velocity)))
        displacement = abs(float(wrist) - self._base_pos)
        if displacement >= float(result["measured_peak_displacement_deg"]):
            result["measured_peak_displacement_deg"] = displacement
            result["peak_wrist_deg"] = float(wrist)

    def _finish_event(self, elapsed: float, status: str = "complete"):
        if self._active_index is None:
            return
        finished_index = self._active_index
        result = self._results[finished_index]
        result["completed_time_s"] = elapsed
        result["status"] = self._event_issue or status
        if finished_index == len(self._results) - 1:
            self._last_event_completed_s = elapsed
        self._active_index = None
        self._stage = "idle"
        self._target = None
        self._prev_gap = None
        self._stage_deadline = None
        self._event_issue = ""
        self.sub_phase = (
            "settling" if self._last_event_completed_s is not None else "waiting")

    def compute(self, state: dict) -> Command:
        if self.sub_phase == "idle":
            return Command(servo=None, damper=0)

        now = float(state.get("t", 0.0))
        if self._start_t is None:
            self._start_t = now
        elapsed = max(0.0, now - self._start_t)
        wrist_value = state.get("wrist_deg", self._hold_pos)
        wrist = self._hold_pos if wrist_value is None else float(wrist_value)
        velocity = float(state.get("wrist_velocity_deg_s", 0.0) or 0.0)
        self._hold_pos = wrist

        if self._active_index is not None:
            event = self._events[self._active_index]
            result = self._results[self._active_index]
            self._update_peaks(wrist, velocity)
            target = float(self._target if self._stage == "out" else self._base_pos)
            gap = target - wrist
            crossed = self._prev_gap is not None and gap * self._prev_gap <= 0.0
            reached = abs(gap) <= self.REACHED_TOL_DEG or crossed
            timed_out = self._stage_deadline is not None and elapsed >= self._stage_deadline

            if reached or timed_out:
                if self._stage == "out":
                    if timed_out and not reached:
                        self._event_issue = "out_timeout"
                    self._stage = "return"
                    self.sub_phase = f"event {self._active_index + 1} return"
                    self._prev_gap = None
                    expected_leg_s = event["angle_deg"] / event["velocity_deg_s"]
                    self._stage_deadline = elapsed + max(0.75, 3.0 * expected_leg_s + 0.5)
                    target = self._base_pos
                    gap = target - wrist
                    if abs(gap) <= self.REACHED_TOL_DEG:
                        self._finish_event(elapsed)
                        return self._hold_command(velocity=0.0)
                else:
                    status = "return_timeout" if timed_out and not reached else "complete"
                    self._finish_event(elapsed, status=status)
                    return self._hold_command(velocity=0.0)

            self._prev_gap = gap
            marker = self._pending_marker
            self._pending_marker = None
            speed = float(result["commanded_velocity_deg_s"])
            return Command(
                servo=None,
                damper=255,
                velocity=math.copysign(speed, gap),
                marker_pulse=marker,
            )

        for index, result in enumerate(self._results):
            if result["status"] == "pending" and elapsed >= float(result["scheduled_time_s"]):
                self._start_event(index, elapsed, wrist)
                event = self._events[index]
                gap = float(self._target) - wrist
                self._prev_gap = gap
                marker = self._pending_marker
                self._pending_marker = None
                return Command(
                    servo=None,
                    damper=255,
                    velocity=math.copysign(float(event["velocity_deg_s"]), gap),
                    marker_pulse=marker,
                )

        all_finished = all(result["status"] not in ("pending", "active") for result in self._results)
        if (
            all_finished
            and self._last_event_completed_s is not None
            and elapsed >= self._last_event_completed_s + self.FINAL_SETTLE_DELAY_S
        ):
            self._done = True
            self.sub_phase = "done"
            return self._hold_command(velocity=0.0)
        return self._hold_command()

    def results_snapshot(self):
        return [dict(result) for result in self._results]

    @property
    def active_event_number(self) -> int:
        return 0 if self._active_index is None else self._active_index + 1

    def current_guide_speed(self) -> float:
        if self._active_index is None:
            return 0.0
        return float(self._results[self._active_index]["commanded_velocity_deg_s"])

    @property
    def hold_pos(self) -> float:
        return self._hold_pos

    @property
    def phase(self) -> str:
        return f"{self.sub_phase} | event={self.active_event_number}/{len(self._events)}"
