"""
motions/__init__.py — re-exports all motion modes
"""

from motions.base                   import ModeBase, Command
from motions.rest_mode              import RestMode
from motions.calibration_mode       import CalibrationMode
from motions.rom_mode               import ROMMode
from motions.aan_mode               import AANMode
from motions.passive_movement_mode  import PassiveMovementMode
from motions.active_movement_mode   import ActiveMovementMode
from motions.follow_mode            import FollowMode
# from motions.back_and_forth_mode    import BackAndForthMode
from motions.rah_mode               import RAHMode
from motions.motion_runner          import MotionRunner

MODES = {
    "rest":              RestMode,
    "calibration":       CalibrationMode,
    "rom_assessment":    ROMMode,
    "aan":               AANMode,
    "passive_movement":  PassiveMovementMode,
    "active_movement":   ActiveMovementMode,
    "follow":            FollowMode,
    # "back_and_forth":    BackAndForthMode,
    "ramp_and_hold":     RAHMode,
}
