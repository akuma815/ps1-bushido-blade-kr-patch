#!/usr/bin/env python3
"""Trace stock LoadImage calls while temporarily disabling the v28 frame upload."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import time
from pathlib import Path

from capture_duckstation_ram import GDBRemote
from live_probe_subtitle_render import write_memory
from wait_duckstation_vab_write import REGISTER_NAMES, read_memory


LOAD_IMAGE_RAM = 0x80069024
RENDER_CALLSITE_RAM = 0x8004322C
RENDER_ORIGINAL_RAM = 0x80070838


def encode_jump(address: int, link: bool = True) -> bytes:
    opcode = 3 if link else 2
    return struct.pack("<I", (opcode << 26) | ((address >> 2) & 0x03FFFFFF))


def registers(client: GDBRemote) -> list[int]:
    raw = bytes.fromhex(client.request("g").decode("ascii"))
    return [struct.unpack_from("<I", raw, index * 4)[0] for index in range(len(REGISTER_NAMES))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--max-hits", type=int, default=64)
    parser.add_argument(
        "--filter-font-cell",
        action="store_true",
        help="record only uploads overlapping the stock A glyph cell at VRAM 950,256",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/analysis/font_loadimage_trace.json"),
    )
    args = parser.parse_args()

    client = GDBRemote("127.0.0.1", 2345, max(10.0, args.seconds + 5.0))
    saved_render_hook = b""
    breakpoint = False
    resumed = False
    hits = []
    total_loadimage_hits = 0
    started = time.time()
    try:
        client.request("qSupported")
        client.request("?")
        saved_render_hook = read_memory(client, RENDER_CALLSITE_RAM, 4)
        write_memory(client, RENDER_CALLSITE_RAM, encode_jump(RENDER_ORIGINAL_RAM, True))
        reply = client.request(f"Z0,{LOAD_IMAGE_RAM:x}")
        if reply != b"OK":
            raise RuntimeError(f"failed to add LoadImage breakpoint: {reply!r}")
        breakpoint = True
        client.continue_without_waiting()
        resumed = True
        client.socket.settimeout(0.25)

        while time.time() - started < args.seconds and len(hits) < args.max_hits:
            try:
                stop = client.receive_packet()
            except socket.timeout:
                continue
            resumed = False
            values = registers(client)
            pc = values[REGISTER_NAMES.index("pc")]
            if pc == LOAD_IMAGE_RAM:
                total_loadimage_hits += 1
                rect_pointer = values[4]
                data_pointer = values[5]
                rect_raw = read_memory(client, rect_pointer, 8)
                rect = struct.unpack("<hhhh", rect_raw)
                overlaps_font_cell = (
                    rect[0] <= 950 < rect[0] + rect[2]
                    and rect[1] <= 256 < rect[1] + rect[3]
                )
                if args.filter_font_cell and not overlaps_font_cell:
                    client.continue_without_waiting()
                    resumed = True
                    continue
                stack_pointer = values[29]
                caller_ra = struct.unpack(
                    "<I", read_memory(client, stack_pointer + 0x20, 4)
                )[0]
                hits.append(
                    {
                        "index": len(hits),
                        "seconds": round(time.time() - started, 6),
                        "pc": f"0x{pc:08X}",
                        "ra": f"0x{values[31]:08X}",
                        "caller_ra": f"0x{caller_ra:08X}",
                        "rect_pointer": f"0x{rect_pointer:08X}",
                        "data_pointer": f"0x{data_pointer:08X}",
                        "rect": {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3]},
                        "data_prefix": read_memory(client, data_pointer, 16).hex().upper(),
                        "overlaps_font_cell": overlaps_font_cell,
                        "stop": stop.decode("ascii", errors="replace"),
                    }
                )
            client.continue_without_waiting()
            resumed = True

        if resumed:
            client.socket.settimeout(5.0)
            client.interrupt()
            resumed = False
        if breakpoint:
            reply = client.request(f"z0,{LOAD_IMAGE_RAM:x}")
            if reply != b"OK":
                raise RuntimeError(f"failed to remove LoadImage breakpoint: {reply!r}")
            breakpoint = False
        write_memory(client, RENDER_CALLSITE_RAM, saved_render_hook)
        client.continue_without_waiting()
        resumed = True
    finally:
        if not resumed:
            if breakpoint:
                try:
                    client.request(f"z0,{LOAD_IMAGE_RAM:x}")
                except Exception:
                    pass
            if saved_render_hook:
                try:
                    write_memory(client, RENDER_CALLSITE_RAM, saved_render_hook)
                except Exception:
                    pass
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()

    report = {
        "trace_seconds": round(time.time() - started, 3),
        "hit_count": len(hits),
        "total_loadimage_hits": total_loadimage_hits,
        "saved_render_hook": saved_render_hook.hex().upper(),
        "font_cell": {"x": 950, "y": 256, "w": 4, "h": 16},
        "hits": hits,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"hit_count={len(hits)}")
    for hit in hits:
        rect = hit["rect"]
        print(f"{hit['caller_ra']} via={hit['ra']} rect={rect['x']},{rect['y']},{rect['w']},{rect['h']} data={hit['data_pointer']}")
    print(f"total_loadimage_hits={total_loadimage_hits}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
