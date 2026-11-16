from pathlib import Path

# Serial (Exo)
SERIAL_COM_PORT = "COM3"
SERIAL_BAUD     = 1000000     # must match DEBUG_BAUD_RATE in high_freq_onboard/high_freq_onboard.ino
SERIAL_TIMEOUT  = 0.1

# Serial (Trigger Arduino)
TRIGGER_COM_PORT = "COM5"
TRIGGER_BAUD     = 1000000
TRIGGER_TIMEOUT  = 0.1

# Load Cell (Nano17)
LC_COM_PORT      = "COM4"
LC_BAUD          = 115200
LC_SER_TIMEOUT   = 0.002
LC_INTER_BYTE_TIMEOUT = 0.001
# Effective Nano17 calibration used by this rig:
#   Internal values were Counts per N = 80 and Counts per N-mm = 16.
#   Fz weight calibration supports a 4x factor, so force is 320 counts/N.
#   Torque is scaled by the same 4x factor and recorded/displayed as Nmm.
LC_FORCE_COUNTS_PER_N = 320.0
LC_TORQUE_COUNTS_PER_NMM = 64.0
LC_SCALE_FACTORS = [
    1.0,
    LC_FORCE_COUNTS_PER_N, LC_FORCE_COUNTS_PER_N, LC_FORCE_COUNTS_PER_N,
    LC_TORQUE_COUNTS_PER_NMM, LC_TORQUE_COUNTS_PER_NMM, LC_TORQUE_COUNTS_PER_NMM,
]

# Mechanics
GEAR_RATIO = 1.0
WRIST_ENCODER_SIGN = -1
WRIST_COMMAND_SIGN = -1
WRIST_VELOCITY_COMMAND_SIGN = -1

# VICON Nexus UDP capture control
VICON_ENABLED       = True
VICON_HOST          = "172.26.116.93"
VICON_PORT          = 30
VICON_BIND_HOST     = ""
VICON_DATABASE_PATH = "D/Database/Kriti/Serhii/2026_09_23"

# Data
DATA_ROOT = Path(__file__).parent / "recordings"

# MVC from EMG (tab MVC_EMG): trigger codes of the EMG-only maximal-effort trial (1-5 and 15 are taken by
# the other tabs), defaults of the tab, and where the TMSi recorder saves its Poly5 files.
MVC_EMG_CODES = {"rest": 6, "max": 7, "recovery": 8}
MVC_EMG_DEFAULTS = {
    "efforts": 3, "rest_s": 5.0, "max_s": 5.0, "recovery_s": 60.0,     # maximal-effort trial
    "levels_N": "1, 5, 20, 30", "hold_s": 5.0, "cal_rest_s": 10.0, "ramp_s": 0.5,   # square-wave calibration
    "tag": "FLX", "n_best": 0, "force": "Fx", "spread_limit_pct": 25.0,  # analysis; n_best 0 = all good channels, N = best N
}
TMSI_RECORDINGS_DIR = str((Path(__file__).parent.parent / "tmsi_manager" / "recordings").resolve())
MVC_EMG_SETTINGS_PATH = str(Path(__file__).parent / "mvc_emg_settings.json")

WRIST_LIMIT_DEG = 150

# Smoothing used by the existing subject-facing LC display.
DISPLAY_SMOOTHING_MS = 150.0

# RAH bean game on the subject display: beans sit at equal distance along the target path.
# RAH_COIN_INTERVAL_S is that distance expressed as the time between beans on a flat segment
# (rest or hold); ramps get proportionally more beans so the spacing along the line stays the
# same. The score stays one point per bean, so the total per run changes with this value.
RAH_COIN_INTERVAL_S = 0.4
# THE COLLECT ZONE: a bean is collected when the force at the bean is within this percentage of
# the run's highest target above or below the line (6.0 at a 20 % MVC target = +/-1.2 % MVC;
# at MVC 50 N that is +/-0.60 N). The character is sized at every window size so that its
# pickup part touches a bean exactly at that distance, so what is seen on screen, this number
# and the Start status / rah_runs.txt line are always the same, on any monitor and for every
# participant. THIS is the number to change to make pickup stricter or easier.
RAH_PICKUP_TOLERANCE_PCT_TARGET = 6.0
# Which part of the character's round body collects: the body shrunk about its centre to this
# fraction (0.7 = the outer 30 % does not count; 1.0 = any touch). It only changes the look of
# the pickup (a smaller fraction = a larger character for the same zone), not the zone itself.
RAH_PICKUP_BODY_FRACTION = 0.7
# The coffee robot wakes up as beans are collected, rep by rep: eyes closed when a rep starts,
# then one step (slightly open, half open and smiling, wide awake) each time this fraction of
# THAT REP's beans has been collected. A rep = the rest, ramp up, hold and ramp down of one
# cycle of the profile (the rest between two cycles starts the next rep, so the robot falls
# asleep again there); the extra rest at the end of the run keeps the last face. The face
# never changes the collect zone (one pickup body for all faces).
RAH_ROBOT_WAKE_FRACTIONS = (0.25, 0.50, 0.75)
# A complete run that collects at least this fraction of its beans earns one sticker (the robot
# with its cup) next to the player's name on the leaderboard (0.9 = 90 %: 84 of 93 beans).
RAH_STICKER_FRACTION = 0.9
# After every complete RAH run the operator GUI asks whether the run's beans are added to the
# player's leaderboard total (Yes = running total and, for a sticker run, a sticker; No = the
# board stays as it is). The recording and the run log (rah_runs.txt next to rah_scores.txt,
# one line per run) are written either way. False = add every complete run without asking.
RAH_LEADERBOARD_CONFIRM = True

# Practice before a RAH run (never recorded): the top of the subject display = this % MVC.
# The RAH tab has an entry with this default.
RAH_PRACTICE_TOP_PCT = 30.0

# Look of the RAH game on the subject display at start-up (the RAH tab can switch it):
#   "coffee" - THE GAME: light grey background, coffee robot that wakes up, coffee beans drawn
#              in code; after a sticker run the robot with its cup appears on the leaderboard
#   "alien"  - kept as an option: the green alien collects coffee beans in front of the window
#              on the night sky
# Scores, markers, triggers and timing are identical in every theme.
RAH_THEME = "coffee"
