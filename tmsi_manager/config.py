"""
tmsi_manager/config.py — configuration constants
"""

import sys
import os

# ── SDK path ──────────────────────────────────────────────────────────────────
# Bundled SDK is beside tmsi_manager: ../tmsi-python-interface-main
TMSI_SDK_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tmsi-python-interface-main")
)
if TMSI_SDK_PATH not in sys.path:
    sys.path.insert(0, TMSI_SDK_PATH)

# ── Device interface ──────────────────────────────────────────────────────────
# DR-DS interface: "docked" (USB dock), "wifi", "optical"
# DS-PC interface: "usb", "network"
DR_INTERFACE = "docked"
DS_INTERFACE = "usb"

# Maximum number of SAGA devices to discover
MAX_DEVICES = 2

# ── Recording ─────────────────────────────────────────────────────────────────
# Output directory for .poly5 files
RECORDING_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "recordings"))

# File format: "poly5" or "xdf"
FILE_FORMAT = "poly5"
