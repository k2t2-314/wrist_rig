import argparse
import ipaddress
import json
import math
import socket
import time


OUTPUT_LAYOUT = ("tx", "ty", "tz", "fx", "fy", "fz")
LC_LAYOUT = ("fx", "fy", "fz", "tx", "ty", "tz")
OUTPUT_INDICES = (3, 4, 5, 0, 1, 2)
N_CHANNELS = len(OUTPUT_LAYOUT)
DEFAULT_HOST = "10.0.0.6"
DEFAULT_PORT = 10020
DEFAULT_SAMPLE_RATE = 100.0


def validate_row(values):
    if isinstance(values, (str, bytes, bytearray)) or len(values) != N_CHANNELS:
        raise ValueError("A UDP sample must contain exactly six channels")
    row = [float(value) for value in values]
    if not all(math.isfinite(value) for value in row):
        raise ValueError("A UDP sample must contain finite numbers")
    return row


def output_row(lc_sample):
    """Reorder scaled LC [fx, fy, fz, tx, ty, tz] into receiver order."""
    lc_sample = validate_row(lc_sample)
    return [lc_sample[i] for i in OUTPUT_INDICES]


def encode_row(row):
    """Encode one receiver-order sample as UTF-8 JSON bytes."""
    return json.dumps(validate_row(row), separators=(",", ":"), allow_nan=False).encode("utf-8")


def validate_target(host, port, rate):
    host = str(ipaddress.IPv4Address(host.strip()))
    port = int(port)
    rate = float(rate)
    if not 1 <= port <= 65535:
        raise ValueError("UDP port must be between 1 and 65535")
    if not math.isfinite(rate) or not 0 < rate <= 600:
        raise ValueError("Send rate must be greater than 0 and at most 600 Hz")
    return host, port, rate


class UDPSender:
    """Send fresh samples at up to the target rate; call from one thread.

    send_row()/send_lc_sample() return True when a datagram is sent, False
    when rate limiting skips a sample. No stale sample is repeated or queued.
    sent counts successful local sendto calls, without receiver confirmation.
    Serial acquisition/tare and physical unit conversion belong to the caller.
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, rate=DEFAULT_SAMPLE_RATE):
        host, port, rate = validate_target(host, port, rate)
        self.target = (host, port)
        self.rate = rate
        self.interval = 1.0 / rate
        self.sent = 0
        self.next_send = time.perf_counter()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_row(self, row, *, now=None):
        """Send [tx, ty, tz, fx, fy, fz]; now uses perf_counter seconds."""
        if self._socket is None:
            raise RuntimeError("UDP sender is closed")
        row = validate_row(row)
        now = time.perf_counter() if now is None else now
        if now < self.next_send:
            return False
        self._socket.sendto(encode_row(row), self.target)
        self.sent += 1
        self.next_send += self.interval
        if self.next_send <= now:
            self.next_send = now + self.interval
        return True

    def send_lc_sample(self, lc_sample, *, now=None):
        """Send a scaled LCWorker sample in its original force-first order."""
        return self.send_row(output_row(lc_sample), now=now)

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def demo_row(t):
    """Six synthetic channels for protocol testing, in receiver order."""
    freqs = (0.5, 1.0, 2.0, 3.0, 4.0, 8.0)
    amps = (0.30, 0.2642857143, 0.2285714286, 0.1928571429, 0.1571428571, 0.1214285714)
    return [0.15 + amp * math.sin(2.0 * math.pi * freq * t + i * math.pi / 8.0)
            for i, (amp, freq) in enumerate(zip(amps, freqs))]


def main():
    parser = argparse.ArgumentParser(description="Send six-channel UTF-8 JSON UDP samples without hardware.")
    parser.add_argument("--target-ip", default=DEFAULT_HOST)
    parser.add_argument("--target-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--duration", type=float, default=10.0, help="Seconds to run; 0 runs until Ctrl+C.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Generate six synthetic sine-wave channels.")
    mode.add_argument("--values", nargs=6, type=float, metavar=("TX", "TY", "TZ", "FX", "FY", "FZ"),
                      help="Repeated sample in receiver order; torque Nmm, force N.")
    args = parser.parse_args()
    try:
        host, port, rate = validate_target(args.target_ip, args.target_port, args.sample_rate)
        if not math.isfinite(args.duration) or args.duration < 0:
            raise ValueError("Duration must be finite and at least zero")
        values = validate_row(args.values) if args.values is not None else None
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Sending JSON {OUTPUT_LAYOUT} to {host}:{port}, target {rate:g} Hz")
    print("Torque: Nmm; force: N. Press Ctrl+C to stop.")
    start = time.perf_counter()
    with UDPSender(host, port, rate) as sender:
        try:
            while True:
                now = time.perf_counter()
                elapsed = now - start
                if args.duration > 0 and elapsed >= args.duration:
                    break
                row = demo_row(elapsed) if args.demo else values
                sender.send_row(row, now=now)
                wait = sender.next_send - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
        except KeyboardInterrupt:
            pass
        elapsed = max(time.perf_counter() - start, 1e-9)
        print(f"Stopped. Packets sent: {sender.sent}, effective rate: {sender.sent / elapsed:.2f} Hz")


if __name__ == "__main__":
    main()
