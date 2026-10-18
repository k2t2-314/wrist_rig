# run.py
import argparse

import config

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

