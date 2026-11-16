"""
motions/__init__.py — re-exports all motion modes
"""

from motions.base                   import ModeBase, Command
from motions.rest_mode              import RestMode
from motions.passive_movement_mode  import PassiveMovementMode
from motions.follow_mode            import FollowMode
from motions.rah_mode               import RAHMode
from motions.rah_practice_mode      import RAHPracticeMode
from motions.velocity_test_mode     import VelocityTestMode
from motions.direction_reversal_mode import DirectionReversalMode
from motions.motion_runner          import MotionRunner

MODES = {
    "rest":              RestMode,
    "passive_movement":  PassiveMovementMode,
    "follow":            FollowMode,
    "ramp_and_hold":     RAHMode,
    "rah_practice":      RAHPracticeMode,
    "velocity_test":     VelocityTestMode,
    "direction_reversal": DirectionReversalMode,
}
