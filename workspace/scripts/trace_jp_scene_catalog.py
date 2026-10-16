#!/usr/bin/env python3
"""Catalog JP dialogue scenes via one breakpoint at 0x8003A88C.

Purpose: signature-to-voice mapping research. Each distinct scene playback
gets one catalog entry with rich context captured on its first frame:
  - gp+0x2F8..0x330 field block (cmp ptr, source ptr, frame, countdown,
    stream pointers)
  - first 32 bytes behind cmp/source/stream pointers
Subsequent frames of the same scene cost only one register fetch plus one
16-byte read, and the scene entry tracks the observed frame range. Source
bytes are re-read at frame 60 and every 60 frames to catch mutation.

The report JSON is flushed incrementally so closing the emulator or killing
the script never loses data. Single breakpoint, resumed immediately per hit
(handoff rule 7).
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
RAM_START = 0x80000000
RAM_END = 0x80200000
FIELD_BLOCK_START = 0x2F8
FIELD_BLOCK_SIZE = 0x38  # gp+0x2F8 .. gp+0x330 exclusive
POINTER_FIELDS = {
    "cmp": 0x2F8,
    "source": 0x304,
    "stream": 0x31C,
    "stream2": 0x320,
}


def register_values(client: GDBRemote) -> list[int]:
    raw = bytes.fromhex(client.request("g").decode("ascii"))
    return [
        struct.unpack_from("<I", raw, index * 4)[0]
        for index in range(len(REGISTER_NAMES))
    ]


def write_report(path: Path, status: str, started: float, hit_count: int, scenes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status": status,
                "break_address": f"0x{BREAK_ADDRESS:08X}",
                "elapsed_seconds": round(time.time() - started, 3),
                "hit_count": hit_count,
                "scene_count": len(scenes),
                "scenes": scenes,
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
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--max-hits", type=int, default=20000)
    parser.add_argument("--flush-every", type=int, default=60)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    pc_index = REGISTER_NAMES.index("pc")
    started = time.time()
    scenes: list[dict] = []
    hit_count = 0
    status = "running"
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

        while time.time() - started < args.timeout and hit_count < args.max_hits:
            try:
                client.receive_packet()
            except socket.timeout:
                continue
            running = False
            values = register_values(client)
            if values[pc_index] == BREAK_ADDRESS:
                hit_count += 1
                gp = values[28]
                frame = values[4]
                length = values[3]
                pointer_block = read_memory(client, gp + 0x2F8, 16)
                source_pointer = struct.unpack_from("<I", pointer_block, 12)[0]
                current = scenes[-1] if scenes else None
                is_new = (
                    current is None
                    or current["source_ptr_value"] != source_pointer
                    or frame <= current["last_frame"]
                )
                if is_new:
                    field_block = read_memory(client, gp + FIELD_BLOCK_START, FIELD_BLOCK_SIZE)
                    transient_head = read_memory(client, 0x8014C000, 16)
                    entry = {
                        "index": len(scenes),
                        "start_seconds": round(time.time() - started, 3),
                        "transient_head": transient_head.hex().upper(),
                        "transient_word3": f"0x{struct.unpack_from('<I', transient_head, 12)[0]:X}",
                        "first_frame": frame,
                        "last_frame": frame,
                        "length_v1": length,
                        "source_ptr_value": source_pointer,
                        "gp_field_block_0x2F8": field_block.hex().upper(),
                        "pointers": {},
                        "source_byte_changes": [],
                    }
                    for name, offset in POINTER_FIELDS.items():
                        pointer = struct.unpack_from("<I", field_block, offset - FIELD_BLOCK_START)[0]
                        record = {"value": f"0x{pointer:08X}", "bytes": None}
                        if RAM_START <= pointer < RAM_END - 32:
                            record["bytes"] = read_memory(client, pointer, 32).hex().upper()
                        entry["pointers"][name] = record
                    scenes.append(entry)
                    write_report(args.output, status, started, hit_count, scenes)
                else:
                    current["last_frame"] = frame
                    current["length_v1"] = length
                    if frame % 60 == 0 and RAM_START <= source_pointer < RAM_END - 32:
                        latest = read_memory(client, source_pointer, 32).hex().upper()
                        if latest != current["pointers"]["source"]["bytes"]:
                            current["source_byte_changes"].append(
                                {"frame": frame, "bytes": latest}
                            )
                if hit_count % args.flush_every == 0:
                    write_report(args.output, status, started, hit_count, scenes)
            client.continue_without_waiting()
            running = True

        status = "max_hits" if hit_count >= args.max_hits else "timeout"
        if running:
            client.socket.settimeout(10.0)
            client.interrupt()
            running = False
        reply = client.request(f"z0,{BREAK_ADDRESS:x}")
        if reply != b"OK":
            raise RuntimeError(f"breakpoint removal failed: {reply!r}")
        installed = False
    except (ConnectionError, OSError):
        status = "disconnected"
        installed = False
        running = True
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
        try:
            client.close()
        except Exception:
            pass
        write_report(args.output, status, started, hit_count, scenes)

    print(f"status={status}")
    print(f"hit_count={hit_count}")
    print(f"scene_count={len(scenes)}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
