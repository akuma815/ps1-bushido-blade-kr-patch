#!/usr/bin/env python3
"""Recover USA subtitle SPRT packets from a 2 MiB PS1 RAM dump.

The stock text renderer copies one 16-byte template entry to a 28-byte
primitive, then overwrites RGB, x/y, and w/h.  AddPrim later replaces the
first packet word with the ordering-table link, so bytes 4..15 are the stable
template fingerprint left in a stopped RAM image.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


RAM_BASE = 0x80000000
RAM_SIZE = 0x200000
TEMPLATE_BASE = 0x80083774
TEMPLATE_COUNT = 96
PACKET_SIZE = 28


def cell_index(value: int) -> int:
    if 0x30 <= value <= 0x39:
        return value - 0x2D
    if 0x41 <= value <= 0x5A:
        return value - 0x34
    if 0x61 <= value <= 0x7A:
        return value - 0x3A
    return 0xFF


def ram_slice(ram: bytes, address: int, size: int) -> bytes:
    offset = address - RAM_BASE
    if offset < 0 or offset + size > len(ram):
        raise ValueError(f"RAM range outside dump: 0x{address:08X}+0x{size:X}")
    return ram[offset:offset + size]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ram", type=Path)
    parser.add_argument("--text", default="ACEGICKMOQCSUCWYB")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--x-min", type=int, default=0)
    parser.add_argument("--x-max", type=int, default=640)
    parser.add_argument("--y-min", type=int, default=0)
    parser.add_argument("--y-max", type=int, default=512)
    args = parser.parse_args()

    ram = args.ram.read_bytes()
    if len(ram) != RAM_SIZE:
        raise ValueError(f"expected {RAM_SIZE} RAM bytes, got {len(ram)}")

    templates = ram_slice(
        ram, TEMPLATE_BASE, TEMPLATE_COUNT * 16
    )
    fingerprints: dict[bytes, list[int]] = {}
    for index in range(TEMPLATE_COUNT):
        entry = templates[index * 16:(index + 1) * 16]
        fingerprints.setdefault(entry[4:16], []).append(index)

    wanted_indices = [cell_index(ord(char)) for char in args.text]
    wanted_set = set(wanted_indices)
    hits = []
    for offset in range(0, len(ram) - PACKET_SIZE + 1, 4):
        packet = ram[offset:offset + PACKET_SIZE]
        indices = fingerprints.get(packet[4:16])
        if not indices or not wanted_set.intersection(indices):
            continue
        x, y, width, height = struct.unpack_from("<hhhh", packet, 0x14)
        if not (args.x_min <= x <= args.x_max and args.y_min <= y <= args.y_max):
            continue
        if not (0 <= width <= 64 and 0 <= height <= 64):
            continue
        hits.append(
            {
                "address": f"0x{RAM_BASE + offset:08X}",
                "indices": indices,
                "candidate_chars": [
                    chr(index + 0x34) if 13 <= index <= 37 else None
                    for index in indices
                ],
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "packet_hex": packet.hex().upper(),
            }
        )

    by_line: dict[int, list[dict]] = {}
    for hit in hits:
        if hit["width"] == 16 and hit["height"] == 15:
            by_line.setdefault(hit["y"], []).append(hit)
    lines = []
    for y, line_hits in sorted(by_line.items()):
        ordered = sorted(line_hits, key=lambda item: (item["x"], item["address"]))
        # A complete target line occupies roughly 17*16 pixels.  Retain broad
        # groups here; duplicate buffers/frames are useful evidence.
        if len(ordered) < 8:
            continue
        lines.append({"y": y, "hits": ordered})

    report = {
        "ram": str(args.ram),
        "template_base": f"0x{TEMPLATE_BASE:08X}",
        "text": args.text,
        "expected_indices": wanted_indices,
        "hit_count": len(hits),
        "hits": hits,
        "candidate_lines": lines,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"output={args.output}")
    print(f"hit_count={len(hits)}")
    print(f"candidate_line_count={len(lines)}")
    for line in lines:
        summary = " ".join(
            f"{item['candidate_chars'][0] or '?'}@{item['x']}"
            for item in line["hits"]
        )
        print(f"y={line['y']}: {summary}")


if __name__ == "__main__":
    main()
