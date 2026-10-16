#!/usr/bin/env python3
"""Insert translated PNGs back into their TIM images, in place.

The bad-ending poem banners in DATA3/O15.DAT are 4bpp TIMs with a 16-entry
CLUT.  Edited PNGs come back anti-aliased (~186 colours), so each pixel is
re-quantised to the ORIGINAL CLUT -- keeping the palette means the patch is a
straight pixel-payload overwrite with no size change and no CLUT rewrite.

Transparency: CLUT index 0 is the transparent entry in these images, so fully
transparent pixels map to 0 and everything else picks the nearest visible
entry (index 0 excluded, or a dark outline would silently vanish).
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
from build_subtitle_poc import patch_logical_sector
from build_subtitle_v37 import make_raw_sector

ROOT = Path(__file__).resolve().parents[2]
SCAN = ROOT / "inputs" / "analysis" / "usa_tim_scan.json"
PREVIEW = ROOT / "inputs" / "usa_ending"


def clut_rgb(value: int) -> tuple[int, int, int]:
    return ((value & 31) * 8, ((value >> 5) & 31) * 8, ((value >> 10) & 31) * 8)


def quantise(png_path: Path, palette: list[int], width: int, height: int) -> bytes:
    from PIL import Image

    image = Image.open(png_path).convert("RGBA")
    if image.size != (width, height):
        raise SystemExit(
            f"{png_path.name}: size {image.size} != TIM {width}x{height}")
    rgb = [clut_rgb(v) for v in palette]
    # index 0 is transparent here; never pick it for an opaque pixel
    candidates = [(i, rgb[i]) for i in range(1, len(rgb))]
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
                if a < 128:
                    index = 0
                else:
                    index = min(
                        candidates,
                        key=lambda c: (c[1][0] - r) ** 2
                        + (c[1][1] - g) ** 2
                        + (c[1][2] - b) ** 2,
                    )[0]
                cache[key] = index
            row.append(index)
        for x in range(0, width, 2):
            packed.append((row[x] & 0xF) | ((row[x + 1] & 0xF) << 4))
    return bytes(packed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, nargs="?",
                        default=ROOT / "original" / "Bushido Blade (USA).bin")
    parser.add_argument("--in-place", type=Path,
                        help="patch this already-built image instead of a copy")
    parser.add_argument("--file", default="DATA3/O15.DAT")
    parser.add_argument("--prefix", default="O15")
    args = parser.parse_args()

    target = args.in_place or args.image
    if not target.exists():
        raise SystemExit(f"missing image: {target}")

    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    records = [r for r in scan["records"] if r["path"] == args.file]
    info = disc.inspect(target)
    entry = next(e for e in info["entries"] if e["path"] == args.file)
    with target.open("rb") as handle:
        data, forms = disc.read_extent(handle, entry["lba"], entry["size"])
    assert forms == {"form1"}
    data = bytearray(data)

    patched = []
    for record in records:
        image = record["image"]
        bpp = record["bpp"]
        if bpp != 4:
            continue
        width = image["width_words"] * 4
        height = image["height"]
        name = f"{args.prefix}_{record['offset']:06X}_{width}x{height}.png"
        png = PREVIEW / name
        if not png.exists():
            continue
        clut = record["clut"]
        raw = data[clut["payload_offset"]:clut["payload_offset"] + clut["payload_size"]]
        palette = list(struct.unpack(f"<{len(raw) // 2}H", raw))
        pixels = quantise(png, palette, width, height)
        if len(pixels) != image["payload_size"]:
            raise SystemExit(
                f"{name}: packed {len(pixels)} != {image['payload_size']}")
        start = image["payload_offset"]
        if bytes(data[start:start + len(pixels)]) == pixels:
            continue
        data[start:start + len(pixels)] = pixels
        patched.append({"name": name, "offset": hex(record["offset"]),
                        "size": f"{width}x{height}"})

    if not patched:
        print("nothing to patch (no changed PNGs found)")
        return

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
            out.write(make_raw_sector(entry["lba"] + index, sub, chunk,
                                      rebuild_mode2_form1_integrity))

    for item in patched:
        print(f"  patched {item['name']} @{item['offset']}")
    print(f"{len(patched)} image(s) written into {args.file} of {target.name}")


if __name__ == "__main__":
    main()
