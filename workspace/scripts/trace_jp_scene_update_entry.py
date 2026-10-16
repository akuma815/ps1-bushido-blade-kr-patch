#!/usr/bin/env python3
"""Trace the JP scene-update compare entry 0x8003A88C with one breakpoint.

Captures per hit: frame (a0), last-frame operand (v1), countdown-1 (v0),
s0/ra/sp, the compare pointer gp+0x2F8 and scene source gp+0x304 with their
first 16 bytes, so signature stability across frames 0/60/179 can be judged.

The breakpoint is resumed immediately after each capture (handoff rule 7).
Exits automatically a few hits after the target-signature scene reaches its
final frame (a0 == v1), or on timeout/max-hits.
"""

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

BREAK_ADDRESS = 0x8003A88C
TARGET_WORD4 = 0x000C16E9
TARGET_WORD12 = 0xACAF17DF
RAM_START = 0x80000000
RAM_END = 0x80200000


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
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--max-hits", type=int, default=6000)
    parser.add_argument("--grace-hits", type=int, default=150)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    pc_index = REGISTER_NAMES.index("pc")
    started = time.time()
    hits: list[dict] = []
    status = "running"
    target_seen = False
    grace_remaining: int | None = None
    installed = False
    running = False
    client = GDBRemote(args.host, args.port, 10.0)
    try:
        client.request("qSupported")
        client.request("?")
        reply = client.request(f"Z0,{BREAK_ADDRESS:x}")
        if reply != b"OK":
            raise RuntimeError(f"breakpoint failed: {reply!r}")
        installed = True
        client.continue_without_waiting()
        running = True
        client.socket.settimeout(0.25)

        while time.time() - started < args.timeout and len(hits) < args.max_hits:
            try:
                client.receive_packet()
            except socket.timeout:
                continue
            running = False
            values = register_values(client)
            pc = values[pc_index]
            if pc == BREAK_ADDRESS:
                gp = values[28]
                record = {
                    "index": len(hits),
                    "seconds": round(time.time() - started, 3),
                    "frame_a0": values[4],
                    "last_v1": values[3],
                    "countdown_minus1_v0": values[2],
                    "s0": f"0x{values[16]:08X}",
                    "ra": f"0x{values[31]:08X}",
                    "sp": f"0x{values[29]:08X}",
                    "gp": f"0x{gp:08X}",
                }
                pointer_block = read_memory(client, gp + 0x2F8, 16)
                cmp_pointer = struct.unpack_from("<I", pointer_block, 0)[0]
                source_pointer = struct.unpack_from("<I", pointer_block, 12)[0]
                record["cmp_ptr"] = f"0x{cmp_pointer:08X}"
                record["src_ptr"] = f"0x{source_pointer:08X}"
                signature_match = False
                for name, pointer in (("cmp", cmp_pointer), ("src", source_pointer)):
                    if RAM_START <= pointer < RAM_END - 16:
                        data = read_memory(client, pointer, 16)
                        record[f"{name}_bytes"] = data.hex().upper()
                        word4 = struct.unpack_from("<I", data, 4)[0]
                        word12 = struct.unpack_from("<I", data, 12)[0]
                        if word4 == TARGET_WORD4 and word12 == TARGET_WORD12:
                            signature_match = True
                record["target"] = signature_match
                hits.append(record)
                if signature_match:
                    target_seen = True
                    if record["frame_a0"] == record["last_v1"] and grace_remaining is None:
                        grace_remaining = args.grace_hits
                if grace_remaining is not None:
                    grace_remaining -= 1
                    if grace_remaining <= 0:
                        status = "target_complete"
                        break
            client.continue_without_waiting()
            running = True

        if status == "running":
            status = "max_hits" if len(hits) >= args.max_hits else "timeout"
        if running:
            client.socket.settimeout(10.0)
            client.interrupt()
            running = False
        reply = client.request(f"z0,{BREAK_ADDRESS:x}")
        if reply != b"OK":
            raise RuntimeError(f"breakpoint removal failed: {reply!r}")
        installed = False
    finally:
        if installed:
            try:
                client.request(f"z0,{BREAK_ADDRESS:x}")
            except Exception:
                pass
        if not running:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "status": status,
                    "break_address": f"0x{BREAK_ADDRESS:08X}",
                    "target_seen": target_seen,
                    "elapsed_seconds": round(time.time() - started, 3),
                    "hit_count": len(hits),
                    "target_hit_count": sum(hit["target"] for hit in hits),
                    "hits": hits,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"status={status}")
    print(f"hit_count={len(hits)}")
    print(f"target_seen={target_seen}")
    print(f"target_hit_count={sum(hit['target'] for hit in hits)}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
