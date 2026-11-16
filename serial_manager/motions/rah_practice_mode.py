"""
motions/rah_practice_mode.py
"""

from motions.base import ModeBase, Command


class RAHPracticeMode(ModeBase):
    """Unrecorded warm-up before RAH_LC.

    The motor holds the starting wrist angle exactly as RAHMode does, but there is no
    force schedule, no perturbation and no end: the mode runs until the GUI stops it
    (Stop practice, or Start of the recorded run). The subject display shows the robot
    scaled from 0 (baseline) to ``top_pct`` % MVC at the top of the screen. The GUI
    attaches no recorder, marker, trigger, Vicon capture, log or score to this mode.
    """

    name        = "rah_practice"
    description = "RAH practice: hold wrist angle, display-only scaling check (not recorded)"

    parameters = [
        {'name': 'direction', 'default': 1, 'min': -1, 'max': 1, 'unit': '+1=extension / -1=flexion'},
        {'name': 'mvc_value', 'default': 10.0, 'min': 0.1, 'max': 500.0, 'unit': 'N or Nmm'},
        {'name': 'top_pct', 'default': 30.0, 'min': 1.0, 'max': 100.0, 'unit': '% MVC at the top of the screen'},
    ]

    def reset(self):
        super().reset()
        self.sub_phase = "idle"
        self._hold_pos = 0.0
        self._start_t = None

    def start(self, wrist_deg: float):
        self._hold_pos = float(wrist_deg)
        self.sub_phase = "practice"
        self._start_t = None

    def compute(self, state: dict) -> Command:
        if self.sub_phase == "idle":
            return Command(servo=None, damper=0)
        now = float(state.get("t", 0.0))
        if self._start_t is None:
            self._start_t = now
        return Command(servo=self._hold_pos, damper=255)

    def top_fraction(self) -> float:
        return max(0.01, float(self.params.get("top_pct", 30.0)) / 100.0)

    @property
    def hold_pos(self) -> float:
        return self._hold_pos

    @property
    def phase(self) -> str:
        return f"{self.sub_phase} | hold={self._hold_pos:.1f} deg | not recorded"

    @property
    def rep(self) -> int:
        return 0
