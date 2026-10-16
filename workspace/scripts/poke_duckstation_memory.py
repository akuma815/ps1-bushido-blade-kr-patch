#!/usr/bin/env python3
"""Write a value into PS1 RAM through DuckStation's GDB server.

Investigation aid. Example — unlock every ending-gallery entry:
    python poke_duckstation_memory.py --address 0x800A30B8 --value 0xFFFFFFFF
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import read_memory  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument(
        "--address", required=True, type=lambda value: int(value, 0)
    )
    parser.add_argument("--value", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--size", type=int, default=4, choices=(1, 2, 4))
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="write this many consecutive units (same value)",
    )
    args = parser.parse_args()

    fmt = {1: "<B", 2: "<H", 4: "<I"}[args.size]
    payload = struct.pack(fmt, args.value) * args.repeat

    client = GDBRemote(args.host, args.port, 10.0)
    resumed = False
    try:
        client.request("qSupported")
        client.request("?")
        before = read_memory(client, args.address, len(payload))
        reply = client.request(
            f"M{args.address:x},{len(payload):x}:{payload.hex()}"
        )
        if reply != b"OK":
            raise RuntimeError(f"memory write rejected: {reply!r}")
        after = read_memory(client, args.address, len(payload))
        print(f"address=0x{args.address:08X} size={args.size} repeat={args.repeat}")
        print(f"before={before.hex().upper()}")
        print(f"after ={after.hex().upper()}")
        print("verified" if after == payload else "WRITE DID NOT STICK")
        client.continue_without_waiting()
        resumed = True
    finally:
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()


if __name__ == "__main__":
    main()
