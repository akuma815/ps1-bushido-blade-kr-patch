#!/usr/bin/env python3
"""Find the frame counter that actually advances during gallery playback.

Takes two RAM snapshots a few seconds apart and reports every 32-bit word that
grew by roughly (elapsed * 30) -- a 30 Hz frame counter -- while staying in the
range a per-ending counter would occupy.

Uses plain memory reads, so the game keeps running at full speed.  Breakpoint
tracing stalls CD/MDEC streaming badly enough that the counter never advances
at all (HANDOFF 41), which is why this has to be snapshot-based.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import read_memory  # noqa: E402


def snapshot(client: GDBRemote, start: int, size: int) -> bytes:
    return read_memory(client, start, size)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--start", type=lambda v: int(v, 0), default=0x80080000)
    parser.add_argument("--size", type=lambda v: int(v, 0), default=0xC0000)
    parser.add_argument("--gap", type=float, default=5.0)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--max-value", type=int, default=4000)
    parser.add_argument("--tolerance", type=float, default=0.45)
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, 30.0)
    try:
        client.request("qSupported")
        client.request("?")
        t0 = time.time()
        first = snapshot(client, args.start, args.size)
        mid = time.time()
        time.sleep(max(0.0, args.gap - (mid - t0)))
        t1 = time.time()
        second = snapshot(client, args.start, args.size)
        elapsed = (t1 + time.time()) / 2 - (t0 + mid) / 2
    finally:
        try:
            client.continue_without_waiting()
        except Exception:
            pass
        client.close()

    expected = elapsed * args.fps
    lo = expected * (1 - args.tolerance)
    hi = expected * (1 + args.tolerance)
    print(f"elapsed {elapsed:.2f}s -> expecting a rise of ~{expected:.0f} "
          f"(accepting {lo:.0f}..{hi:.0f})")

    found = []
    for offset in range(0, min(len(first), len(second)) - 3, 4):
        a = struct.unpack_from("<I", first, offset)[0]
        if a > args.max_value:
            continue
        b = struct.unpack_from("<I", second, offset)[0]
        if b > args.max_value:
            continue
        delta = b - a
        if lo <= delta <= hi:
            found.append((args.start + offset, a, b, delta))

    print(f"candidates: {len(found)}")
    for addr, a, b, delta in found[:40]:
        print(f"  0x{addr:08X}  {a:6d} -> {b:6d}  (+{delta})")


if __name__ == "__main__":
    main()
