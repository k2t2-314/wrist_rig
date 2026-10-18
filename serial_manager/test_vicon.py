import argparse
import random
import time
from datetime import datetime

from config import VICON_BIND_HOST, VICON_DATABASE_PATH, VICON_HOST, VICON_PORT
from hardware.vicon_worker import build_vicon_udp_payload, send_vicon_udp


def main():
    parser = argparse.ArgumentParser(description="Send standalone VICON CaptureStart/CaptureStop UDP packets.")
    parser.add_argument("--action", choices=("start", "stop", "both"), default="both")
    parser.add_argument("--name", default=f"VICON_TEST_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--database-path", default=VICON_DATABASE_PATH)
    parser.add_argument("--host", default=VICON_HOST)
    parser.add_argument("--port", type=int, default=VICON_PORT)
    parser.add_argument("--bind-host", default=VICON_BIND_HOST)
    parser.add_argument("--duration", type=float, default=3.0, help="Seconds between start and stop for --action both.")
    parser.add_argument("--show-xml", action="store_true", help="Print the XML payload before sending.")
    args = parser.parse_args()

    bind_host = args.bind_host.strip() or None
    database_path = args.database_path.strip()
    if not database_path:
        raise SystemExit("VICON database path is empty. Pass --database-path or set VICON_DATABASE_PATH in config.py.")

    def send(action: str):
        packet_id = random.randint(1, 2**31 - 1)
        if args.show_xml:
            payload, _ = build_vicon_udp_payload(
                action=action,
                name=args.name,
                database_path=database_path,
                description="Standalone VICON UDP test" if action == "start" else None,
                packet_id=packet_id,
            )
            print(f"\n[VICON-TEST] Capture{action.capitalize()} XML payload:")
            print(payload[:-1])
            print("\\0")
        packet_id = send_vicon_udp(
            action=action,
            name=args.name,
            database_path=database_path,
            description="Standalone VICON UDP test" if action == "start" else None,
            host=args.host,
            port=args.port,
            bind_host=bind_host,
            packet_id=packet_id,
        )
        print(
            f"[VICON-TEST] Capture{action.capitalize()} sent: "
            f"name={args.name}, database_path={database_path}, "
            f"host={args.host}:{args.port}, bind={bind_host or 'auto'}, "
            f"packet_id={packet_id}",
            flush=True,
        )

    if args.action in ("start", "both"):
        send("start")
    if args.action == "both":
        time.sleep(max(0.0, args.duration))
    if args.action in ("stop", "both"):
        send("stop")


if __name__ == "__main__":
    main()
