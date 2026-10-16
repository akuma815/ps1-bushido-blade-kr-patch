#!/usr/bin/env python3
"""Record every Japanese V00 group-selector context through DuckStation GDB."""

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


# This is the Japanese epilogue corresponding to the USA subtitle-group call
# at 0x8003C578.  The older 0x8003AB74 probe is a one-shot story initializer
# and cannot distinguish individual subtitle records.
SELECTOR_HOOK = 0x8003A1A0
SUBTITLE_STATE = 0x800A2E90


def register_values(client: GDBRemote) -> list[int]:
    raw = bytes.fromhex(client.request("g").decode("ascii"))
    return [
        struct.unpack_from("<I", raw, index * 4)[0]
        for index in range(len(REGISTER_NAMES))
    ]


def write_report(output: Path, started: float, hits: list[dict], status: str) -> None:
    report = {
        "selector_hook": f"0x{SELECTOR_HOOK:08X}",
        "subtitle_state": f"0x{SUBTITLE_STATE:08X}",
        "status": status,
        "elapsed_seconds": round(time.time() - started, 3),
        "hit_count": len(hits),
        "unique_s2": sorted({hit["s2"] for hit in hits}),
        "hits": hits,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--max-hits", type=int, default=128)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    args = parser.parse_args()

    started = time.time()
    hits: list[dict] = []
    client = GDBRemote(args.host, args.port, args.timeout)
    breakpoint_installed = False
    resumed = False
    status = "running"
    try:
        client.request("qSupported")
        client.request("?")
        reply = client.request(f"Z0,{SELECTOR_HOOK:x}")
        if reply != b"OK":
            raise RuntimeError(f"failed to add breakpoint: {reply!r}")
        breakpoint_installed = True
        write_report(args.output, started, hits, status)
        client.continue_without_waiting()
        resumed = True
        client.socket.settimeout(0.5)

        while True:
            if args.stop_file.exists():
                status = "stopped_by_sentinel"
                break
            if time.time() - started >= args.timeout:
                status = "timeout"
                break
            if len(hits) >= args.max_hits:
                status = "max_hits"
                break
            try:
                stop_reply = client.receive_packet()
            except socket.timeout:
                continue
            resumed = False
            values = register_values(client)
            pc = values[REGISTER_NAMES.index("pc")]
            if pc == SELECTOR_HOOK:
                state = read_memory(client, SUBTITLE_STATE, 12)
                hits.append(
                    {
                        "index": len(hits),
                        "seconds": round(time.time() - started, 6),
                        "stop_reply": stop_reply.decode("ascii", errors="replace"),
                        "pc": f"0x{pc:08X}",
                        "a0": f"0x{values[4]:08X}",
                        "a1": f"0x{values[5]:08X}",
                        "a2": f"0x{values[6]:08X}",
                        "a3": f"0x{values[7]:08X}",
                        "s0": f"0x{values[16]:08X}",
                        "s1": f"0x{values[17]:08X}",
                        "s2": values[18],
                        "gp": f"0x{values[28]:08X}",
                        "sp": f"0x{values[29]:08X}",
                        "ra": f"0x{values[31]:08X}",
                        "state_hex": state.hex().upper(),
                    }
                )
                write_report(args.output, started, hits, status)
            client.continue_without_waiting()
            resumed = True

        if resumed:
            client.socket.settimeout(5.0)
            client.interrupt()
            resumed = False
        if breakpoint_installed:
            reply = client.request(f"z0,{SELECTOR_HOOK:x}")
            if reply != b"OK":
                raise RuntimeError(f"failed to remove breakpoint: {reply!r}")
            breakpoint_installed = False
    finally:
        if breakpoint_installed:
            try:
                client.request(f"z0,{SELECTOR_HOOK:x}")
            except Exception:
                pass
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()
        write_report(args.output, started, hits, status)

    print(f"status={status}")
    print(f"hit_count={len(hits)}")
    print(f"unique_s2={sorted({hit['s2'] for hit in hits})}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
