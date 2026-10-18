"""
Publish two hardware-free TMSi-like LSL streams for testing the RAH GUI.

Streams:
    SAGA1
    SAGA2

Channel layout:
    0       CREF
    1..64   R1C1 .. R8C8 (row-major, matching the RAH GUI)
    65      TRIGGERS (active-low hardware idle value: 15)

Examples:
    python mock_lsl_sagas.py
    python mock_lsl_sagas.py --rate 100
    python mock_lsl_sagas.py --frequency 20 --amplitude 250
"""

from __future__ import annotations

import argparse
import math
import time

try:
    from pylsl import StreamInfo, StreamOutlet, local_clock
except ImportError as exc:
    raise SystemExit(
        "pylsl is required. Run this script in the same Python environment "
        "used by tmsi_manager/RAH."
    ) from exc


GRID_NAMES = [
    f"R{row}C{col}"
    for row in range(1, 9)
    for col in range(1, 9)
]
CHANNELS = [
    ("CREF", "uV", "-"),
    *((name, "uV", "EEG") for name in GRID_NAMES),
    ("TRIGGERS", "bits", "STATUS"),
]


def create_outlet(stream_name: str, sample_rate: float, source_id: str) -> StreamOutlet:
    """Create one LSL outlet with TMSi-compatible channel metadata."""
    info = StreamInfo(
        stream_name,
        "EEG",
        len(CHANNELS),
        sample_rate,
        "float32",
        source_id,
    )

    channels_node = info.desc().append_child("channels")
    for index, (label, unit, channel_type) in enumerate(CHANNELS):
        channel = channels_node.append_child("channel")
        channel.append_child_value("label", label)
        channel.append_child_value("index", str(index))
        channel.append_child_value("unit", unit)
        channel.append_child_value("type", channel_type)

    info.desc().append_child_value("manufacturer", "TMSi")
    synchronization = info.desc().append_child("synchronization")
    synchronization.append_child_value("offset_mean", "0.0335")
    synchronization.append_child_value("offset_std", "0.0008")

    return StreamOutlet(info, chunk_size=0, max_buffered=360)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish mock SAGA1 and SAGA2 LSL streams."
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=1000.0,
        help="Sampling frequency in Hz (default: 1000).",
    )
    parser.add_argument(
        "--frequency",
        type=float,
        default=10.0,
        help="Sine-wave frequency in Hz (default: 10).",
    )
    parser.add_argument(
        "--amplitude",
        type=float,
        default=100.0,
        help="Sine-wave amplitude in uV (default: 100).",
    )
    parser.add_argument(
        "--chunk-ms",
        type=float,
        default=20.0,
        help="Chunk duration in milliseconds (default: 20).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rate <= 0:
        raise SystemExit("--rate must be greater than zero.")
    if args.frequency < 0:
        raise SystemExit("--frequency cannot be negative.")
    if args.chunk_ms <= 0:
        raise SystemExit("--chunk-ms must be greater than zero.")

    sample_rate = float(args.rate)
    chunk_samples = max(1, round(sample_rate * args.chunk_ms / 1000.0))
    chunk_duration = chunk_samples / sample_rate

    outlet1 = create_outlet("SAGA1", sample_rate, "mock-tmsi-saga1")
    outlet2 = create_outlet("SAGA2", sample_rate, "mock-tmsi-saga2")

    print(
        f"Publishing SAGA1 and SAGA2: {len(CHANNELS)} channels, "
        f"{sample_rate:g} Hz, {chunk_samples} samples/chunk."
    )
    print("Channels: CREF, R1C1..R8C8, TRIGGERS. Press Ctrl+C to stop.")

    sample_index = 0
    next_push_time = time.perf_counter()

    try:
        while True:
            chunk = []
            for offset in range(chunk_samples):
                t = (sample_index + offset) / sample_rate
                value = float(
                    args.amplitude * math.sin(2.0 * math.pi * args.frequency * t)
                )
                # CREF and all 64 grid channels share the same sine wave.
                # TRIGGERS stays at 15, matching the SAGA active-low idle state.
                chunk.append([value] * 65 + [15.0])

            timestamp = local_clock()
            outlet1.push_chunk(chunk, timestamp)
            outlet2.push_chunk(chunk, timestamp)
            sample_index += chunk_samples

            next_push_time += chunk_duration
            sleep_time = next_push_time - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                # If the process falls behind, resume from the current clock
                # instead of sending an increasingly large burst.
                next_push_time = time.perf_counter()
    except KeyboardInterrupt:
        print("\nMock SAGA LSL streams stopped.")


if __name__ == "__main__":
    main()
