from pathlib import Path

# Serial (Exo)
SERIAL_COM_PORT = "COM7"
SERIAL_BAUD     = 115200      # must match DEBUG_BAUD_RATE in firmware/config.h
SERIAL_TIMEOUT  = 0.1

# Serial (Trigger Arduino)
TRIGGER_COM_PORT = "COM10"
TRIGGER_BAUD     = 115200
TRIGGER_TIMEOUT  = 0.1

# Load Cell (Nano17)
LC_COM_PORT      = "COM3"
LC_BAUD          = 115200
LC_SER_TIMEOUT   = 0.02
LC_SCALE_FACTORS = [1.0, 320.0, 320.0, 320.0, 64.0, 64.0, 64.0]

# Mechanics
GEAR_RATIO = 6.25

# UDP
UDP_HOST        = "127.0.0.1"
UDP_SEND_PORT   = 10020
UDP_LISTEN_PORT = 10022

# OBS
OBS_HOST        = "127.0.0.1"
OBS_PORT        = 4455
OBS_PASSWORD    = "nRlORO3FDYLIsYaj"
OBS_TIMEOUT     = 3.0

# GoPro
GOPRO_BASE_URL  = "http://172.27.106.51:8080"
GOPRO_TIMEOUT   = 8.0

# Data
DATA_ROOT = Path(__file__).parent / "data"

WRIST_LIMIT_DEG = 150
