# run.py
import argparse
import ctypes
import sys

import config

# Timing: the 100 Hz motion loop and the 100 ms GUI ticks sleep in small steps. On Windows the
# default timer resolution is ~15.6 ms (time.sleep(0.01) measured 13.9 ms mean, 26 ms max on
# this PC), so ask for 1 ms for the lifetime of this process (10.4 ms mean, 11 ms max).
# Do not lower sys.setswitchinterval: a 1 ms GIL switch interval was measured to slow the
# motion loop to ~18 ms per tick (the default 5 ms gives ~11 ms with the GUI drawing).
if sys.platform == "win32":
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

def parse_args():
    parser = argparse.ArgumentParser(description='Wrist rig experiment GUI')
    parser.add_argument('--mock', action='store_true', help='Use synthetic hardware workers.')
    parser.add_argument(
        '--gear-ratio',
        type=float,
        default=config.GEAR_RATIO,
        help=f'Wrist/motor gear ratio used for angle and velocity conversion. Default: {config.GEAR_RATIO:g}.',
    )
    args = parser.parse_args()
    if args.gear_ratio <= 0:
        parser.error('--gear-ratio must be > 0')
    return args


args = parse_args()
config.GEAR_RATIO = float(args.gear_ratio)

from gui.app import ExpGUI

app = ExpGUI(use_mock=args.mock)
app.mainloop()

