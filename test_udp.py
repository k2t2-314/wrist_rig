"""
test_udp.py — UDP loopback tester for wrist_rig

Simulates the EEG/recording system that the GUI sends markers to.

- Listens on UDP_SEND_PORT (10020) for markers from the GUI
- Prints received markers with timestamp
- Echoes each marker back to UDP_LISTEN_PORT (10022) so the GUI
  can log round-trip times

Usage:
    python test_udp.py

Run this before starting the GUI. Keep it open during the experiment.
"""

import socket
import threading
import time
from datetime import datetime

LISTEN_PORT  = 10020   # port the GUI sends TO  → we listen here
REPLY_PORT   = 10022   # port the GUI listens ON → we reply here
REPLY_HOST   = "127.0.0.1"
BUFFER_SIZE  = 256


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", LISTEN_PORT))
    sock.settimeout(1.0)

    print(f"[test_udp] Listening on port {LISTEN_PORT}")
    print(f"[test_udp] Will echo back to {REPLY_HOST}:{REPLY_PORT}")
    print(f"[test_udp] Press Ctrl+C to stop\n")

    reply_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        while True:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                msg = data.decode("utf-8", errors="ignore").strip()
                ts  = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                print(f"[{ts}] RECV from {addr[0]}:{addr[1]}  →  {msg}")

                # echo back to GUI listen port
                reply_sock.sendto(data, (REPLY_HOST, REPLY_PORT))
                print(f"[{ts}] ECHO back to {REPLY_HOST}:{REPLY_PORT}  →  {msg}")

            except socket.timeout:
                continue

    except KeyboardInterrupt:
        print("\n[test_udp] Stopped.")
    finally:
        sock.close()
        reply_sock.close()


if __name__ == "__main__":
    main()
