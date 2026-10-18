"""
run.py — entry point for tmsi_manager

Usage:
    python run.py          # real hardware (up to 2 SAGA devices)
    python run.py --mock   # mock workers, no hardware required

Launches the operator GUI, including the enlarged layout and optional
single-channel live monitoring panel.
"""

import argparse
import os
import sys

# Ensure tmsi_manager root is on sys.path when run directly
sys.path.insert(0, os.path.dirname(__file__))

import config  # inserts TMSI_SDK_PATH


def main():
    parser = argparse.ArgumentParser(description="TMSi Manager")
    parser.add_argument("--mock", action="store_true",
                        help="Use mock workers (no SAGA hardware required)")
    args = parser.parse_args()

    if args.mock:
        from workers.mock_saga_worker import MockSagaWorker
        worker1 = MockSagaWorker(label="SAGA #1")
        worker2 = MockSagaWorker(label="SAGA #2")
        print("[run] Mock mode - no hardware required.")
    else:
        from workers.saga_worker import SagaWorker
        worker1 = SagaWorker(label="SAGA #1")
        worker2 = SagaWorker(label="SAGA #2")

    from gui.app import TMSiManagerGUI
    app = TMSiManagerGUI(worker1, worker2, default_geometry="1240x760")
    app.mainloop()


if __name__ == "__main__":
    main()
