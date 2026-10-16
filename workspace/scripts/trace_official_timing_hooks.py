#!/usr/bin/env python3
"""Trace the v31 event/update hook chain through DuckStation GDB."""

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


HOOKS = {
    0x8003A1A0: "selector_patch",
    0x800118B4: "event_wrapper",
    0x8003A1A8: "selector_continue",
    0x8003A8A8: "update_patch",
    0x800119B4: "update_wrapper",
    0x8003A8B0: "update_continue",
}
LEGACY_STATE = 0x800A2E90
CAVE_STATE = 0x80011DA0


def registers(client: GDBRemote) -> list[int]:
    raw = bytes.fromhex(client.request("g").decode("ascii"))
    return [
        struct.unpack_from("<I", raw, index * 4)[0]
        for index in range(len(REGISTER_NAMES))
    ]


def write_report(path: Path, started: float, hits: list[dict], status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "elapsed_seconds": round(time.time() - started, 3),
                "hit_count": len(hits),
                "counts": {
                    name: sum(hit["name"] == name for hit in hits)
                    for name in HOOKS.values()
                },
                "hits": hits,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-hits", type=int, default=120)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    started = time.time()
    hits: list[dict] = []
    installed: list[int] = []
    client = GDBRemote(args.host, args.port, max(args.timeout, 5.0))
    running = False
    status = "running"
    try:
        client.request("qSupported")
        client.request("?")
        for address in HOOKS:
            reply = client.request(f"Z0,{address:x}")
            if reply != b"OK":
                raise RuntimeError(f"breakpoint failed at 0x{address:08X}: {reply!r}")
            installed.append(address)
        client.continue_without_waiting()
        running = True
        client.socket.settimeout(0.25)
        while time.time() - started < args.timeout and len(hits) < args.max_hits:
            try:
                stop = client.receive_packet()
            except socket.timeout:
                continue
            running = False
            values = registers(client)
            pc = values[REGISTER_NAMES.index("pc")]
            if pc in HOOKS:
                hits.append(
                    {
                        "index": len(hits),
                        "seconds": round(time.time() - started, 6),
                        "name": HOOKS[pc],
                        "pc": f"0x{pc:08X}",
                        "stop": stop.decode("ascii", errors="replace"),
                        "s2": values[18],
                        "legacy_state": read_memory(client, LEGACY_STATE, 12).hex().upper(),
                        "cave_state": read_memory(client, CAVE_STATE, 12).hex().upper(),
                    }
                )
            client.continue_without_waiting()
            running = True
        status = "max_hits" if len(hits) >= args.max_hits else "timeout"
        if running:
            client.socket.settimeout(5.0)
            client.interrupt()
            running = False
        for address in reversed(installed):
            reply = client.request(f"z0,{address:x}")
            if reply != b"OK":
                raise RuntimeError(f"breakpoint removal failed at 0x{address:08X}: {reply!r}")
        installed.clear()
    finally:
        for address in reversed(installed):
            try:
                client.request(f"z0,{address:x}")
            except Exception:
                pass
        if not running:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()
        write_report(args.output, started, hits, status)

    print(f"status={status}")
    print(f"hit_count={len(hits)}")
    print(json.dumps({name: sum(hit['name'] == name for hit in hits) for name in HOOKS.values()}))


if __name__ == "__main__":
    main()
