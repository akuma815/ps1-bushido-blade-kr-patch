#!/usr/bin/env python3
"""Trace AddPrim calls immediately after the first story voice starts.

The tracer arms on the game's VAB Key On helper.  When tone 0 is played from
the currently loaded story VAB, it swaps to an AddPrim breakpoint and records
the real ordering-table and primitive pointers for a small number of frames.
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


KEY_ON = 0x80070B9C
ADD_PRIM = 0x8006A368
STORY_VAB_HANDLE = 0x8013EDD8
FRAME_COUNTER = 0x800A2F3C


def register_values(client: GDBRemote) -> list[int]:
    raw = bytes.fromhex(client.request("g").decode("ascii"))
    return [
        struct.unpack_from("<I", raw, index * 4)[0]
        for index in range(len(REGISTER_NAMES))
    ]


def read_u32(client: GDBRemote, address: int) -> int:
    return struct.unpack("<I", read_memory(client, address, 4))[0]


def safe_read_hex(client: GDBRemote, address: int, size: int) -> str | None:
    try:
        return read_memory(client, address, size).hex().upper()
    except Exception:
        return None


def add_breakpoint(client: GDBRemote, address: int) -> None:
    reply = client.request(f"Z0,{address:x}")
    if reply != b"OK":
        raise RuntimeError(f"failed to add breakpoint {address:#x}: {reply!r}")


def remove_breakpoint(client: GDBRemote, address: int) -> None:
    reply = client.request(f"z0,{address:x}")
    if reply != b"OK":
        raise RuntimeError(f"failed to remove breakpoint {address:#x}: {reply!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--arm-timeout", type=float, default=900.0)
    parser.add_argument("--max-addprim-hits", type=int, default=512)
    parser.add_argument("--target-frames", type=int, default=4)
    parser.add_argument("--state-address", type=lambda value: int(value, 0))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, args.arm_timeout)
    installed: set[int] = set()
    resumed = False
    armed_at = time.time()
    matched_event: dict | None = None
    key_on_hits = 0
    addprim_hits: list[dict] = []
    frame_values: list[int] = []
    stop_reason = "unknown"

    try:
        client.request("qSupported")
        client.request("?")
        add_breakpoint(client, KEY_ON)
        installed.add(KEY_ON)
        client.continue_without_waiting()
        resumed = True
        client.socket.settimeout(0.5)

        while True:
            if matched_event is None and time.time() - armed_at >= args.arm_timeout:
                stop_reason = "arm_timeout"
                break
            if len(addprim_hits) >= args.max_addprim_hits:
                stop_reason = "max_addprim_hits"
                break
            if len(frame_values) >= args.target_frames:
                stop_reason = "target_frames"
                break

            try:
                stop_reply = client.receive_packet()
            except socket.timeout:
                continue
            resumed = False
            values = register_values(client)
            pc = values[REGISTER_NAMES.index("pc")]

            if pc == KEY_ON:
                key_on_hits += 1
                story_handle = read_u32(client, STORY_VAB_HANDLE)
                if values[5] == story_handle and values[7] == 0:
                    matched_event = {
                        "seconds_from_arm": round(time.time() - armed_at, 6),
                        "stop_reply": stop_reply.decode("ascii", errors="replace"),
                        "pc": f"0x{pc:08X}",
                        "a0": f"0x{values[4]:08X}",
                        "a1": f"0x{values[5]:08X}",
                        "a2": f"0x{values[6]:08X}",
                        "a3": f"0x{values[7]:08X}",
                        "ra": f"0x{values[31]:08X}",
                        "story_vab_handle": f"0x{story_handle:08X}",
                        "key_on_hit_index": key_on_hits,
                    }
                    remove_breakpoint(client, KEY_ON)
                    installed.remove(KEY_ON)
                    add_breakpoint(client, ADD_PRIM)
                    installed.add(ADD_PRIM)

            elif pc == ADD_PRIM and matched_event is not None:
                frame = read_u32(client, FRAME_COUNTER)
                if not frame_values or frame_values[-1] != frame:
                    frame_values.append(frame)
                ot_pointer = values[4]
                primitive_pointer = values[5]
                addprim_hits.append(
                    {
                        "index": len(addprim_hits),
                        "seconds_from_event": round(
                            time.time() - armed_at - matched_event["seconds_from_arm"], 6
                        ),
                        "frame_counter": f"0x{frame:08X}",
                        "frame_ordinal": len(frame_values) - 1,
                        "a0_ot": f"0x{ot_pointer:08X}",
                        "a1_primitive": f"0x{primitive_pointer:08X}",
                        "ra": f"0x{values[31]:08X}",
                        "ot_entry_before": safe_read_hex(client, ot_pointer, 4),
                        "primitive_before": safe_read_hex(client, primitive_pointer, 16),
                        "subtitle_state": (
                            safe_read_hex(client, args.state_address, 8)
                            if args.state_address is not None
                            else None
                        ),
                    }
                )

            client.continue_without_waiting()
            resumed = True

        if resumed:
            client.socket.settimeout(5.0)
            client.interrupt()
            resumed = False
        for address in list(installed):
            remove_breakpoint(client, address)
            installed.remove(address)
    finally:
        for address in list(installed):
            try:
                remove_breakpoint(client, address)
            except Exception:
                pass
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()

    report = {
        "key_on_address": f"0x{KEY_ON:08X}",
        "addprim_address": f"0x{ADD_PRIM:08X}",
        "story_vab_handle_address": f"0x{STORY_VAB_HANDLE:08X}",
        "frame_counter_address": f"0x{FRAME_COUNTER:08X}",
        "subtitle_state_address": (
            f"0x{args.state_address:08X}" if args.state_address is not None else None
        ),
        "stop_reason": stop_reason,
        "key_on_hit_count": key_on_hits,
        "matched_event": matched_event,
        "unique_frame_values": [f"0x{value:08X}" for value in frame_values],
        "addprim_hit_count": len(addprim_hits),
        "addprim_hits": addprim_hits,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"stop_reason={stop_reason}")
    print(f"key_on_hit_count={key_on_hits}")
    print(f"matched_event={matched_event is not None}")
    print(f"unique_frames={len(frame_values)}")
    print(f"addprim_hit_count={len(addprim_hits)}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
