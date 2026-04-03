from dataclasses import dataclass


@dataclass
class Command:
    """
    Actuator command returned by a motion mode each control tick.

    servo  : desired wrist angle in degrees relative to GUI zero.
             None means do not send a new servo command (torque-off behaviour).
    damper : desired PWM [0, 255]. None means leave unchanged.
    """
    servo:  float | None = None
    damper: int   | None = None


class ModeBase:
    """Base class for all motion modes executed by MotionRunner."""

    name        = "base"
    description = "base motion mode"
    parameters  = []

    def __init__(self, **kwargs):
        self.params = {}
        for spec in getattr(self, "parameters", []):
            self.params[spec["name"]] = spec.get("default")
        self.params.update(kwargs)
        self.handedness = 1    # +1 right hand, -1 left hand
        self._done      = False

    def set_handedness(self, h: int):
        self.handedness = 1 if h >= 0 else -1

    def set_param(self, name: str, value):
        self.params[name] = value

    def reset(self):
        self._done = False

    def compute(self, state: dict) -> Command:
        """
        Called every 100 Hz tick by MotionRunner.

        Args:
            state: dict with keys:
                t           — elapsed seconds since runner start
                dt          — nominal loop period (0.01 s)
                wrist_deg   — encoder wrist angle, zeroed
                imu_deg     — IMU yaw angle, zeroed
                current_mA  — motor current
                qw/qx/qy/qz — raw IMU quaternion
                t_ms        — firmware timestamp in ms

        Returns:
            Command with servo and/or damper values.
        """
        return Command()

    @property
    def done(self) -> bool:
        return self._done