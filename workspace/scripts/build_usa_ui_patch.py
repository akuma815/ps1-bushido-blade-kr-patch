#!/usr/bin/env python3
"""Insert the edited USA title / loading logos back into their TIMs, in place.

Same contract as build_usa_tim_patch.py: the edited PNGs come back
anti-aliased, so every pixel is re-quantised to the TIM's ORIGINAL 16-entry
CLUT and only the pixel payload is overwritten -- no size change, no CLUT
rewrite, so nothing downstream of the TIM moves.

The CLUT is read from the TARGET image rather than the pristine disc, because
the title lives in DATA3/O12.DAT while the loading logo lives inside
SCUS_941.80, and the subtitle builder relocates and grows that executable.
Reading the target keeps this correct whichever build it is pointed at.

A CLUT entry of 0x0000 is PlayStation transparency; it is never chosen for an
opaque pixel (otherwise a dark outline would silently vanish), and is used for
pixels the PNG marks transparent.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc
from build_options_poc import rebuild_mode2_form1_integrity
from build_subtitle_v37 import make_raw_sector

ROOT = Path(__file__).resolve().parents[2]
SCAN = ROOT / "inputs" / "analysis" / "usa_tim_scan.json"
ASSET_DIR = ROOT / "workspace" / "graphics" / "usa"

ASSETS = (
    ("title", "DATA3/O12.DAT", 0x0001CC),
    ("loading", "SCUS_941.80", 0x074A84),
)


def clut_rgb(value: int) -> tuple[int, int, int]:
    return ((value & 31) * 8, ((value >> 5) & 31) * 8, ((value >> 10) & 31) * 8)


def quantise(png_path: Path, palette: list[int], width: int, height: int) -> bytes:
    from PIL import Image

    image = Image.open(png_path).convert("RGBA")
    if image.size != (width, height):
        raise SystemExit(f"{png_path.name}: size {image.size} != TIM {width}x{height}")

    transparent = [i for i, v in enumerate(palette) if v == 0]
    opaque = [(i, clut_rgb(v)) for i, v in enumerate(palette) if v != 0]
    if not opaque:
        raise SystemExit(f"{png_path.name}: CLUT has no opaque entry")

    cache: dict[tuple[int, int, int, int], int] = {}
    pixels = image.load()
    packed = bytearray()
    for y in range(height):
        row = []
        for x in range(width):
            key = pixels[x, y]
            index = cache.get(key)
            if index is None:
                r, g, b, a = key
                if a < 128 and transparent:
                    index = transparent[0]
                else:
                    index = min(
                        opaque,
                        key=lambda c: (c[1][0] - r) ** 2
                        + (c[1][1] - g) ** 2
                        + (c[1][2] - b) ** 2,
                    )[0]
                cache[key] = index
            row.append(index)
        for x in range(0, width, 2):
            packed.append((row[x] & 0xF) | ((row[x + 1] & 0xF) << 4))
    return bytes(packed)


def write_extent(target: Path, entry: dict, data: bytes) -> None:
    sectors = (entry["size"] + 2047) // 2048
    with target.open("rb") as src:
        src.seek(entry["lba"] * disc.RAW_SECTOR_SIZE + 16)
        first_sub = src.read(8)
        src.seek((entry["lba"] + sectors - 1) * disc.RAW_SECTOR_SIZE + 16)
        last_sub = src.read(8)
    with target.open("r+b") as out:
        out.seek(entry["lba"] * disc.RAW_SECTOR_SIZE)
        for index in range(sectors):
            chunk = bytes(data[index * 2048:(index + 1) * 2048]).ljust(2048, b"\x00")
            sub = last_sub if index == sectors - 1 else first_sub
            out.write(make_raw_sector(entry["lba"] + index, sub,
                                      chunk, rebuild_mode2_form1_integrity))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-place", type=Path, required=True,
                        help="built USA image to patch")
    parser.add_argument("--asset-dir", type=Path, default=ASSET_DIR)
    parser.add_argument("--only", action="append", default=[],
                        choices=[a[0] for a in ASSETS],
                        help="patch just these assets (default: all)")
    args = parser.parse_args()

    target = args.in_place
    if not target.exists():
        raise SystemExit(f"missing image: {target}")
    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    info = disc.inspect(target)
    wanted = args.only or [a[0] for a in ASSETS]
    patched = []

    for name, path, offset in ASSETS:
        if name not in wanted:
            continue
        png = args.asset_dir / f"{name}_ko_edit.png"
        if not png.exists():
            print(f"  skip {name}: {png.name} not found")
            continue
        record = next((r for r in scan["records"]
                       if r["path"] == path and r["offset"] == offset), None)
        if record is None:
            raise SystemExit(f"{name}: no TIM at {path} +0x{offset:X}")

        entry = next(e for e in info["entries"] if e["path"] == path)
        with target.open("rb") as handle:
            data, forms = disc.read_extent(handle, entry["lba"], entry["size"])
        assert forms == {"form1"}, forms
        data = bytearray(data)

        clut = record["clut"]
        raw = data[clut["payload_offset"]:clut["payload_offset"] + clut["payload_size"]]
        palette = list(struct.unpack(f"<{len(raw) // 2}H", raw))
        width, height = record["pixel_width"], record["pixel_height"]
        pixels = quantise(png, palette, width, height)
        if len(pixels) != record["image"]["payload_size"]:
            raise SystemExit(
                f"{name}: packed {len(pixels)} != {record['image']['payload_size']}")

        start = record["image"]["payload_offset"]
        if bytes(data[start:start + len(pixels)]) == pixels:
            print(f"  {name}: already identical, skipped")
            continue
        data[start:start + len(pixels)] = pixels
        write_extent(target, entry, bytes(data))
        info = disc.inspect(target)          # extents are stable, but re-read
        patched.append(f"{name} ({width}x{height}) -> {path} +0x{offset:06X}")

    for line in patched:
        print(f"  patched {line}")
    print(f"{len(patched)} asset(s) written into {target.name}")


if __name__ == "__main__":
    main()
