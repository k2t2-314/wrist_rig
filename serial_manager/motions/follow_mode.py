"""Voluntary movement mode with configurable signed start and end points."""

from motions.base import Command, ModeBase


class FollowMode(ModeBase):
    """
    Initial positioning:
        positioning -> servo moves the wrist to start_deg

    Per repetition:
        go      -> guide moves from start_deg to target_deg
        hold    -> guide stays at target_deg
        return  -> guide moves back to start_deg
        rest    -> guide stays at start_deg
    """

    name = "follow"
    description = "Voluntary Movement: participant tracks a guide between signed start/end points"

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
        {"name": "start_deg", "default": 0.0, "min": -150.0, "max": 150.0, "unit": "signed wrist deg"},
        {"name": "target_deg", "default": 30.0, "min": -150.0, "max": 150.0, "unit": "signed wrist deg"},
        {"name": "speed_deg_s", "default": 20.0, "min": 1.0, "max": 150.0, "unit": "wrist deg/s"},
        {"name": "hold_time_s", "default": 2.0, "min": 0.0, "max": 30.0, "unit": "s"},
        {"name": "rest_time_s", "default": 2.0, "min": 0.0, "max": 30.0, "unit": "s"},
        {"name": "damper_pwm", "default": 0, "min": 0, "max": 255, "unit": "PWM"},
        {"name": "total_reps", "default": 1, "min": 1, "max": 50, "unit": "reps"},
    ]

    def reset(self):
        super().reset()
        self.sub_phase = 'idle'
        self._rep = 0
        self._segment_idx = 0
        self.green_pos = self.current_start()
        self._drive_pos = 0.0
        self._hold_t = 0.0
        self._rest_t = 0.0
        self._guide_phase = None
        self._guide_t0 = 0.0
        self._timer_phase = None
        self._timer_t0 = 0.0
        self._last_marker_speed = None
        self.on_phase_change = None


    def _path_segments(self):
        segments = self.params.get("path_segments") or []
        out = []
        for item in segments:
            try:
                kind = str(item.get("kind", "go")).strip().lower()
                start = float(item.get("start"))
                end = float(item.get("end"))
                speed = item.get("speed", None)
                speed = None if speed is None else max(0.001, float(speed))
            except Exception:
                continue
            if kind in ("back", "return"):
                kind = "back"
            elif kind == "go":
                kind = "go"
            else:
                continue
            segment = {"kind": kind, "start": start, "end": end}
            if speed is not None:
                segment["speed"] = speed
            out.append(segment)
        return out

    def _current_path_segment(self):
        segments = self._path_segments()
        if not segments:
            return None
        idx = min(max(self._segment_idx, 0), len(segments) - 1)
        return segments[idx]

    def _sync_rep_from_segment(self):
        if self._path_segments():
            self._rep = max(0, int(self._segment_idx) // 2)


    def _segment_motion_phase(self) -> str:
        seg = self._current_path_segment()
        if seg and seg.get("kind") == "back":
            return "return"
        return "go"

    def current_start(self) -> float:
        seg = self._current_path_segment()
        if seg:
            return float(seg['start'])
        return float(self.params.get('start_deg', 0.0))

    def current_target(self) -> float:
        seg = self._current_path_segment()
        if seg:
            return float(seg['end'])
        return float(self.params.get('target_deg', 30.0))

    def current_speed(self) -> float:
        seg = self._current_path_segment()
        if seg and 'speed' in seg:
            return max(0.001, float(seg['speed']))
        return max(0.001, float(self.params.get('speed_deg_s', 20.0)))

    def current_guide_speed(self) -> float:
        if self.sub_phase not in ("go", "return"):
            return 0.0
        return self.current_speed()

    def _speed_switch_marker_pulse(self):
        if self.sub_phase not in ("go", "return"):
            return None
        speed = self.current_guide_speed()
        if self._last_marker_speed is None:
            self._last_marker_speed = speed
            return None
        if abs(speed - self._last_marker_speed) > 1e-9:
            self._last_marker_speed = speed
            return 5
        return None

    def _command(self, servo=None, damper=None):
        return Command(servo=servo, damper=damper, marker_pulse=self._speed_switch_marker_pulse())

    def _notify(self, prev: str, new: str):
        self.sub_phase = new
        if self.on_phase_change:
            self.on_phase_change(prev, new)

    def _guide_elapsed(self, now: float) -> float:
        if self._guide_phase != self.sub_phase:
            self._guide_phase = self.sub_phase
            self._guide_t0 = now
        return max(0.0, now - self._guide_t0)

    def _timer_elapsed(self, now: float) -> float:
        if self._timer_phase != self.sub_phase:
            self._timer_phase = self.sub_phase
            self._timer_t0 = now
        return max(0.0, now - self._timer_t0)

    def start_positioning(self, wrist_deg: float):
        self._drive_pos = float(wrist_deg)
        start = self.current_start()
        self.green_pos = start
        first_phase = self._segment_motion_phase()
        if abs(self._drive_pos - start) < 0.3:
            self._drive_pos = start
            self._notify("idle", first_phase)
        else:
            self._notify("idle", "positioning")


    def compute(self, state: dict) -> Command:
        now = float(state.get('t', 0.0))
        dt = float(state['dt'])
        custom_segments = self._path_segments()
        start = self.current_start()
        target = self.current_target()
        speed = self.current_speed()
        hold_dur = max(0.0, float(self.params.get('hold_time_s', 2.0)))
        rest_dur = max(0.0, float(self.params.get('rest_time_s', 2.0)))
        damper = int(max(0, min(255, round(self.params.get('damper_pwm', 0)))))
        total_segments = len(custom_segments)
        total_reps = int(round(self.params.get('total_reps', 1)))
        step = speed * dt

        def move_toward(current: float, destination: float) -> float:
            if destination >= current:
                return min(destination, current + step)
            return max(destination, current - step)

        def guide_pos_by_elapsed(origin: float, destination: float, first_speed: float) -> float:
            distance = destination - origin
            abs_distance = abs(distance)
            if abs_distance <= 1e-09:
                return destination
            elapsed = self._guide_elapsed(now)
            first_speed = max(0.001, float(first_speed))
            travel = first_speed * elapsed
            travel = min(abs_distance, travel)
            direction = 1.0 if distance >= 0.0 else -1.0
            return origin + direction * travel
        if self.sub_phase == 'idle':
            self.green_pos = start
            return self._command(servo=None, damper=damper)
        if self.sub_phase == 'positioning':
            self._drive_pos = move_toward(self._drive_pos, start)
            self.green_pos = start
            if abs(self._drive_pos - start) < 0.3:
                self._drive_pos = start
                self._notify('positioning', self._segment_motion_phase())
                return self._command(servo=None, damper=damper)
            return self._command(servo=self._drive_pos, damper=0)
        if self.sub_phase == 'go':
            self.green_pos = guide_pos_by_elapsed(start, target, speed)
            reached_target = abs(self.green_pos - target) <= 1e-09
            if reached_target:
                self.green_pos = target
                self._hold_t = 0.0
                self._notify('go', 'hold')
            return self._command(servo=None, damper=damper)
        if self.sub_phase == 'hold':
            self.green_pos = target
            self._hold_t = self._timer_elapsed(now)
            if self._hold_t >= hold_dur:
                if custom_segments:
                    self._segment_idx += 1
                    self._sync_rep_from_segment()
                    if self._segment_idx >= total_segments:
                        self._notify('hold', 'done')
                        self._done = True
                    else:
                        self._notify('hold', self._segment_motion_phase())
                else:
                    self._notify('hold', 'return')
            return self._command(servo=None, damper=damper)
        if self.sub_phase == 'return':
            if custom_segments:
                self.green_pos = guide_pos_by_elapsed(start, target, speed)
                destination = target
                reached_destination = abs(self.green_pos - destination) <= 1e-09
            else:
                self.green_pos = guide_pos_by_elapsed(target, start, speed)
                destination = start
                reached_destination = abs(self.green_pos - destination) <= 1e-09
            if reached_destination:
                self.green_pos = destination
                self._rest_t = 0.0
                self._notify('return', 'rest')
            return self._command(servo=None, damper=damper)
        if self.sub_phase == 'rest':
            self.green_pos = target if custom_segments else start
            self._rest_t = self._timer_elapsed(now)
            if self._rest_t >= rest_dur:
                if custom_segments:
                    self._segment_idx += 1
                    self._sync_rep_from_segment()
                    if self._segment_idx >= total_segments:
                        self._notify('rest', 'done')
                        self._done = True
                    else:
                        self._notify('rest', self._segment_motion_phase())
                else:
                    self._rep += 1
                    if self._rep >= total_reps:
                        self._notify('rest', 'done')
                        self._done = True
                    else:
                        self._notify('rest', self._segment_motion_phase())
            return self._command(servo=None, damper=damper)
        self.green_pos = start
        return self._command(servo=None, damper=0)

    @property
    def phase(self) -> str:
        segments = self._path_segments()
        if segments:
            total = max(1, int(round(float(self.params.get('total_reps', max(1, len(segments) // 2))))))
            rep = min(total, max(1, self._rep + 1))
        else:
            total = int(round(self.params.get('total_reps', 1)))
            rep = self._rep + 1
            if getattr(self, 'done', False):
                rep = min(rep, total)
        start = self.current_start()
        target = self.current_target()
        speed = self.current_speed()
        speed_text = f'{speed:.1f} deg/s'
        return f'{self.sub_phase} | rep={rep}/{total} | {start:+.1f} deg -> {target:+.1f} deg | {speed_text}'

    @property
    def rep(self) -> int:
        return self._rep

    @property
    def hold_t(self) -> float:
        return self._hold_t

    @property
    def rest_t(self) -> float:
        return self._rest_t
