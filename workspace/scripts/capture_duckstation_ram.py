#!/usr/bin/env python3
"""Capture PS1 RAM from DuckStation's GDB remote server and scan signatures."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from pathlib import Path


class GDBRemote:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.settimeout(timeout)

    def close(self) -> None:
        self.socket.close()

    @staticmethod
    def frame(payload: bytes) -> bytes:
        checksum = sum(payload) & 0xFF
        return b"$" + payload + b"#" + f"{checksum:02x}".encode("ascii")

    def receive_packet(self) -> bytes:
        while True:
            marker = self.socket.recv(1)
            if not marker:
                raise ConnectionError("GDB server closed the connection")
            if marker == b"$":
                break
        payload = bytearray()
        while True:
            value = self.socket.recv(1)
            if value == b"#":
                break
            payload.extend(value)
        received_checksum = int(self.socket.recv(2), 16)
        calculated_checksum = sum(payload) & 0xFF
        if received_checksum != calculated_checksum:
            self.socket.sendall(b"-")
            raise ValueError(
                f"GDB checksum mismatch: received={received_checksum:02x}, "
                f"calculated={calculated_checksum:02x}"
            )
        self.socket.sendall(b"+")
        return bytes(payload)

    def request(self, payload: str) -> bytes:
        self.socket.sendall(self.frame(payload.encode("ascii")))
        acknowledgement = self.socket.recv(1)
        if acknowledgement != b"+":
            raise ValueError(f"unexpected GDB acknowledgement: {acknowledgement!r}")
        return self.receive_packet()

    def interrupt(self) -> bytes:
        self.socket.sendall(b"\x03")
        return self.receive_packet()

    def continue_without_waiting(self) -> None:
        self.socket.sendall(self.frame(b"c"))
        # DuckStation acknowledges the continue packet before it resumes.  If
        # the client closes with that ACK still unread, this build can remain
        # paused at the BIOS/title screen.
        acknowledgement = self.socket.recv(1)
        if acknowledgement != b"+":
            raise ValueError(
                f"unexpected GDB continue acknowledgement: {acknowledgement!r}"
            )
        time.sleep(0.05)


def all_offsets(data: bytes, pattern: bytes):
    start = 0
    while True:
        offset = data.find(pattern, start)
        if offset < 0:
            return
        yield offset
        start = offset + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--ram-base", type=lambda value: int(value, 0), default=0x80000000)
    parser.add_argument("--ram-size", type=lambda value: int(value, 0), default=0x200000)
    parser.add_argument("--chunk-size", type=lambda value: int(value, 0), default=0x4000)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--signature", action="append", default=[])
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, args.timeout)
    started_at = time.time()
    try:
        supported = client.request("qSupported")
        # DuckStation pauses the emulated system as soon as a GDB client
        # connects. Query the stop reason; sending Ctrl-C here receives no
        # reply when the system was already paused on connection.
        stop_reply = client.request("?")
        chunks = []
        for offset in range(0, args.ram_size, args.chunk_size):
            size = min(args.chunk_size, args.ram_size - offset)
            reply = client.request(f"m{args.ram_base + offset:x},{size:x}")
            if reply.startswith(b"E"):
                raise RuntimeError(
                    f"GDB memory read failed at {args.ram_base + offset:#x}: "
                    f"{reply.decode('ascii', errors='replace')}"
                )
            chunk = bytes.fromhex(reply.decode("ascii"))
            if len(chunk) != size:
                raise ValueError(
                    f"short GDB memory read at {args.ram_base + offset:#x}: "
                    f"expected={size}, actual={len(chunk)}"
                )
            chunks.append(chunk)
        ram = b"".join(chunks)
    finally:
        client.continue_without_waiting()
        client.close()

    signatures = []
    for text in (args.signature or ["pBAV"]):
        pattern = text.encode("ascii", errors="strict")
        offsets = list(all_offsets(ram, pattern))
        signatures.append(
            {
                "text": text,
                "hex": pattern.hex(),
                "count": len(offsets),
                "ram_addresses": [f"0x{args.ram_base + offset:08X}" for offset in offsets],
                "ram_offsets": [f"0x{offset:X}" for offset in offsets],
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(ram)
    report = {
        "host": args.host,
        "port": args.port,
        "qSupported": supported.decode("ascii", errors="replace"),
        "stop_reply": stop_reply.decode("ascii", errors="replace"),
        "ram_base": f"0x{args.ram_base:08X}",
        "ram_size": len(ram),
        "ram_sha256": hashlib.sha256(ram).hexdigest(),
        "capture_seconds": round(time.time() - started_at, 3),
        "signatures": signatures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"ram_size={len(ram)}")
    print(f"ram_sha256={report['ram_sha256']}")
    for signature in signatures:
        print(f"{signature['text']}={signature['count']}")
        for address in signature["ram_addresses"]:
            print(f"  {address}")
    print(f"output={args.output}")
    print(f"report={args.report}")


if __name__ == "__main__":
    main()
