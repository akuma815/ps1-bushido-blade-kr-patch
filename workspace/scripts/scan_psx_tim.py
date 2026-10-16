#!/usr/bin/env python3
"""Validate and optionally decode standard TIM images from every disc file."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

from PIL import Image


MAGIC = b"\x10\x00\x00\x00"


def load_inspector(path: Path):
    spec = importlib.util.spec_from_file_location("inspect_psx_disc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inspector: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_block(data: bytes, offset: int) -> tuple[dict, int] | None:
    if offset + 12 > len(data):
        return None
    size = struct.unpack_from("<I", data, offset)[0]
    if size < 12 or offset + size > len(data):
        return None
    x, y, width_words, height = struct.unpack_from("<4H", data, offset + 4)
    payload_size = size - 12
    if width_words == 0 or height == 0 or payload_size != width_words * height * 2:
        return None
    return (
        {
            "size": size,
            "x": x,
            "y": y,
            "width_words": width_words,
            "height": height,
            "payload_offset": offset + 12,
            "payload_size": payload_size,
        },
        offset + size,
    )


def parse_tim(data: bytes, offset: int) -> dict | None:
    if data[offset : offset + 4] != MAGIC or offset + 8 > len(data):
        return None
    flags = struct.unpack_from("<I", data, offset + 4)[0]
    if flags & ~0x0F:
        return None
    bpp_code = flags & 0x07
    if bpp_code > 3:
        return None
    has_clut = bool(flags & 0x08)
    cursor = offset + 8
    clut = None
    if has_clut:
        parsed = parse_block(data, cursor)
        if parsed is None:
            return None
        clut, cursor = parsed
    parsed = parse_block(data, cursor)
    if parsed is None:
        return None
    image, end = parsed
    row_bytes = image["width_words"] * 2
    if bpp_code == 0:
        pixel_width = image["width_words"] * 4
        minimum_colors = 16
    elif bpp_code == 1:
        pixel_width = image["width_words"] * 2
        minimum_colors = 256
    elif bpp_code == 2:
        pixel_width = image["width_words"]
        minimum_colors = 0
    else:
        if row_bytes % 3:
            return None
        pixel_width = row_bytes // 3
        minimum_colors = 0
    if pixel_width > 1024 or image["height"] > 512:
        return None
    if bpp_code in (0, 1):
        if clut is None or clut["width_words"] * clut["height"] < minimum_colors:
            return None
    return {
        "offset": offset,
        "flags": flags,
        "bpp": (4, 8, 16, 24)[bpp_code],
        "has_clut": has_clut,
        "clut": clut,
        "image": image,
        "pixel_width": pixel_width,
        "pixel_height": image["height"],
        "total_size": end - offset,
    }


def psx_color(value: int) -> tuple[int, int, int, int]:
    r = (value & 0x1F) * 255 // 31
    g = ((value >> 5) & 0x1F) * 255 // 31
    b = ((value >> 10) & 0x1F) * 255 // 31
    alpha = 0 if value == 0 else 255
    return r, g, b, alpha


def decode_tim(data: bytes, tim: dict) -> Image.Image:
    image_meta = tim["image"]
    payload = data[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    width = tim["pixel_width"]
    height = tim["pixel_height"]
    pixels: list[tuple[int, int, int, int]] = []
    palette = []
    if tim["clut"]:
        clut = tim["clut"]
        clut_data = data[
            clut["payload_offset"] : clut["payload_offset"] + clut["payload_size"]
        ]
        palette = [
            psx_color(struct.unpack_from("<H", clut_data, index)[0])
            for index in range(0, len(clut_data), 2)
        ]
    if tim["bpp"] == 4:
        for value in payload:
            pixels.append(palette[value & 0x0F])
            pixels.append(palette[(value >> 4) & 0x0F])
    elif tim["bpp"] == 8:
        pixels = [palette[value] for value in payload]
    elif tim["bpp"] == 16:
        pixels = [
            psx_color(struct.unpack_from("<H", payload, index)[0])
            for index in range(0, len(payload), 2)
        ]
    else:
        for row in range(height):
            row_start = row * image_meta["width_words"] * 2
            row_data = payload[row_start : row_start + image_meta["width_words"] * 2]
            for index in range(0, len(row_data), 3):
                b, g, r = row_data[index : index + 3]
                pixels.append((r, g, b, 255))
    result = Image.new("RGBA", (width, height))
    result.putdata(pixels[: width * height])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--inspector", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--extract-dir", type=Path)
    args = parser.parse_args()

    inspector = load_inspector(args.inspector)
    disc = inspector.inspect(args.image)
    records = []
    with args.image.open("rb") as track:
        for entry in disc["entries"]:
            if entry["is_dir"]:
                continue
            data, sector_forms = inspector.read_extent(
                track, entry["lba"], entry["size"]
            )
            cursor = 0
            index = 0
            while True:
                offset = data.find(MAGIC, cursor)
                if offset < 0:
                    break
                tim = parse_tim(data, offset)
                cursor = offset + 1
                if tim is None:
                    continue
                record = {
                    "path": entry["path"],
                    "file_lba": entry["lba"],
                    "file_size": entry["size"],
                    "sector_forms": sorted(sector_forms),
                    **tim,
                }
                if args.extract_dir:
                    path_tag = hashlib.sha1(entry["path"].encode("ascii")).hexdigest()[:8]
                    png_name = f"{path_tag}_{index:03d}_{offset:08x}.png"
                    png_path = args.extract_dir / png_name
                    png_path.parent.mkdir(parents=True, exist_ok=True)
                    decode_tim(data, tim).save(png_path)
                    record["png"] = png_path.as_posix()
                records.append(record)
                index += 1

    report = {
        "image": disc["image"],
        "files_scanned": sum(1 for entry in disc["entries"] if not entry["is_dir"]),
        "valid_tim_count": len(records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"files_scanned={report['files_scanned']}")
    print(f"valid_tim_count={report['valid_tim_count']}")
    print(args.output)


if __name__ == "__main__":
    main()
