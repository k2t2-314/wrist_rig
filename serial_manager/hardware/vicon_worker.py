import random
import socket
from typing import Optional
from xml.sax.saxutils import escape


def _xml_value(value) -> str:
    return escape(str(value), {'"': "&quot;"})


def build_vicon_udp_payload(
    *,
    action: str,
    name: str,
    database_path: str,
    notes: Optional[str] = None,
    description: Optional[str] = None,
    timecode: Optional[str] = None,
    delay_ms: int = 0,
    packet_id: Optional[int] = None,
) -> tuple[str, int]:
    if action not in ("start", "stop"):
        raise ValueError(f"Invalid VICON action: {action}")

    packet_id = packet_id or random.randint(1, 2**31 - 1)
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>']

    if action == "start":
        lines += [
            "<CaptureStart>",
            f'  <Name VALUE="{_xml_value(name)}"/>',
        ]
        if notes:
            lines.append(f'  <Notes VALUE="{_xml_value(notes)}"/>')
        if description:
            lines.append(f'  <Description VALUE="{_xml_value(description)}"/>')
        if timecode:
            lines.append(f'  <TimeCode VALUE="{_xml_value(timecode)}"/>')
        lines += [
            f'  <DatabasePath VALUE="{_xml_value(database_path)}"/>',
            f'  <Delay VALUE="{int(delay_ms)}"/>',
            f'  <PacketID VALUE="{packet_id}"/>',
            "</CaptureStart>",
        ]
    else:
        lines += [
            "<CaptureStop>",
            f'  <Name VALUE="{_xml_value(name)}"/>',
            f'  <DatabasePath VALUE="{_xml_value(database_path)}"/>',
        ]
        if timecode:
            lines.append(f'  <TimeCode VALUE="{_xml_value(timecode)}"/>')
        lines += [
            f'  <PacketID VALUE="{packet_id}"/>',
            "</CaptureStop>",
        ]

    return "\n".join(lines) + "\0", packet_id


def send_vicon_udp(
    *,
    action: str,
    name: str,
    database_path: str,
    notes: Optional[str] = None,
    description: Optional[str] = None,
    timecode: Optional[str] = None,
    delay_ms: int = 0,
    host: str = "192.168.10.1",
    port: int = 30,
    bind_host: Optional[str] = None,
    packet_id: Optional[int] = None,
) -> int:
    """
    Send a VICON Nexus CaptureStart/CaptureStop UDP XML packet.

    Returns the PacketID used for the message.
    """
    payload, packet_id = build_vicon_udp_payload(
        action=action,
        name=name,
        database_path=database_path,
        notes=notes,
        description=description,
        timecode=timecode,
        delay_ms=delay_ms,
        packet_id=packet_id,
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if bind_host:
            sock.bind((bind_host, 0))
        sock.sendto(payload.encode("utf-8"), (host, int(port)))
    finally:
        sock.close()

    return packet_id
