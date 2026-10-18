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

WRIST_LIMIT_DEG = 150

# Smoothing used by the existing subject-facing LC display.
DISPLAY_SMOOTHING_MS = 150.0
