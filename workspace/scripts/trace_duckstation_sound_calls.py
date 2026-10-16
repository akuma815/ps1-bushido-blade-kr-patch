#!/usr/bin/env python3
"""Record a bounded sequence of DuckStation sound-call breakpoints."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import REGISTER_NAMES, read_memory  # noqa: E402


DEFAULT_ADDRESSES = [
    0x8003E650,
    0x8003E728,
    0x8003E798,
    0x8003E874,
    0x8003E90C,
    0x8005CCB4,
]


def register_values(client: GDBRemote) -> list[int]:
    raw = bytes.fromhex(client.request("g").decode("ascii"))
    return [
        struct.unpack_from("<I", raw, index * 4)[0]
        for index in range(len(REGISTER_NAMES))
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--address", action="append", type=lambda value: int(value, 0))
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--max-hits", type=int, default=500)
    parser.add_argument("--arm-timeout", type=float, default=600.0)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    addresses = args.address or DEFAULT_ADDRESSES

    client = GDBRemote(args.host, args.port, args.arm_timeout)
    breakpoints: list[int] = []
    resumed = False
    hits: list[dict] = []
    first_hit_at: float | None = None
    armed_at = time.time()
    stop_reason = "unknown"
    try:
        client.request("qSupported")
        client.request("?")
        for address in addresses:
            reply = client.request(f"Z0,{address:x}")
            if reply != b"OK":
                raise RuntimeError(f"failed to add breakpoint {address:#x}: {reply!r}")
            breakpoints.append(address)
        client.continue_without_waiting()
        resumed = True
        client.socket.settimeout(0.5)

        while True:
            now = time.time()
            if args.stop_file is not None and args.stop_file.exists():
                stop_reason = "stop_file"
                break
            if first_hit_at is None:
                if now - armed_at >= args.arm_timeout:
                    stop_reason = "arm_timeout"
                    break
            elif now - first_hit_at >= args.duration:
                stop_reason = "duration"
                break
            if len(hits) >= args.max_hits:
                stop_reason = "max_hits"
                break
            try:
                stop_reply = client.receive_packet()
            except socket.timeout:
                continue
            resumed = False
            values = register_values(client)
            pc = values[REGISTER_NAMES.index("pc")]
            if pc in addresses:
                captured_at = time.time()
                if first_hit_at is None:
                    first_hit_at = captured_at
                story_handle = struct.unpack(
                    "<I", read_memory(client, 0x8013EDD8, 4)
                )[0]
                hits.append(
                    {
                        "index": len(hits),
                        "seconds_from_first": round(captured_at - first_hit_at, 6),
                        "stop_reply": stop_reply.decode("ascii", errors="replace"),
                        "pc": f"0x{pc:08X}",
                        "a0": f"0x{values[4]:08X}",
                        "a1": f"0x{values[5]:08X}",
                        "a2": f"0x{values[6]:08X}",
                        "a3": f"0x{values[7]:08X}",
                        "s0": f"0x{values[16]:08X}",
                        "s1": f"0x{values[17]:08X}",
                        "s2": f"0x{values[18]:08X}",
                        "s3": f"0x{values[19]:08X}",
                        "ra": f"0x{values[31]:08X}",
                        "story_vab_handle": f"0x{story_handle:08X}",
                        "a1_matches_story_vab_handle": values[5] == story_handle,
                    }
                )
            client.continue_without_waiting()
            resumed = True

        if resumed:
            client.socket.settimeout(5.0)
            client.interrupt()
            resumed = False
        for address in breakpoints:
            reply = client.request(f"z0,{address:x}")
            if reply != b"OK":
                raise RuntimeError(f"failed to remove breakpoint {address:#x}: {reply!r}")
        breakpoints.clear()
    finally:
        for address in breakpoints:
            try:
                client.request(f"z0,{address:x}")
            except Exception:
                pass
        if not resumed:
            client.continue_without_waiting()
        client.close()

    report = {
        "addresses": [f"0x{address:08X}" for address in addresses],
        "armed_seconds": round(time.time() - armed_at, 3),
        "capture_duration_seconds": args.duration,
        "stop_reason": stop_reason,
        "hit_count": len(hits),
        "hits": hits,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"stop_reason={stop_reason}")
    print(f"hit_count={len(hits)}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
