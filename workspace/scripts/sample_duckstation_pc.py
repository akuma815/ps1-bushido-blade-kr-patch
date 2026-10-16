#!/usr/bin/env python3
"""Sample the current PS1 PC without installing breakpoints."""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import REGISTER_NAMES  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    for index in range(args.samples):
        client = GDBRemote(args.host, args.port, 5.0)
        resumed = False
        try:
            client.request("qSupported")
            stop = client.request("?").decode("ascii", errors="replace")
            raw = bytes.fromhex(client.request("g").decode("ascii"))
            values = [
                struct.unpack_from("<I", raw, i * 4)[0]
                for i in range(len(REGISTER_NAMES))
            ]
            pc = values[REGISTER_NAMES.index("pc")]
            ra = values[REGISTER_NAMES.index("r31")]
            sp = values[REGISTER_NAMES.index("r29")]
            print(
                f"sample={index} stop={stop} pc=0x{pc:08X} "
                f"ra=0x{ra:08X} sp=0x{sp:08X}"
            )
            client.continue_without_waiting()
            resumed = True
        finally:
            if not resumed:
                try:
                    client.continue_without_waiting()
                except Exception:
                    pass
            client.close()
        if index + 1 < args.samples:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
