#!/usr/bin/env python3
"""Read Japanese V00 scene-owned fields and source signatures once."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import read_memory  # noqa: E402


GP = 0x800A2A84
FIELDS = {
    "source": 0x304,
    "frame": 0x314,
    "countdown": 0x318,
    "stream": 0x31C,
    "stream2": 0x320,
    "word": 0x324,
    "word2": 0x328,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, 5.0)
    resumed = False
    try:
        client.request("qSupported")
        print(f"stop={client.request('?').decode('ascii', errors='replace')}")
        values: dict[str, int] = {}
        for name, offset in FIELDS.items():
            raw = read_memory(client, GP + offset, 4)
            value = struct.unpack("<I", raw)[0]
            values[name] = value
            print(f"{name}=0x{value:08X}")
        for name in ("source", "stream", "stream2"):
            pointer = values[name]
            if 0x80010000 <= pointer < 0x80200000:
                data = read_memory(client, pointer, 32)
                print(f"{name}_bytes={data.hex().upper()}")
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
