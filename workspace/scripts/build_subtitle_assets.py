#!/usr/bin/env python3
"""Build deterministic Korean subtitle render assets from voice_catalog.csv.

The single-subtitle runtime hook is proven, but the full disc asset loader is
not.  Every record remains sector aligned and keyed by the full VAB context so
the pack can be consumed by that loader without changing the translation CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import struct
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PACK_MAGIC = b"BBSUB001"
RECORD_MAGIC = b"SUBR"
VERSION = 1
SECTOR_SIZE = 2048
HEADER_SIZE = SECTOR_SIZE
RECORD_HEADER_SIZE = 64
CANVAS_WIDTH = 640
CANVAS_HEIGHT = 28
FONT_SIZE = 20
STROKE_WIDTH = 1
FPS = 60


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def render_subtitle(text: str, font: ImageFont.FreeTypeFont) -> tuple[Image.Image, bytes, dict]:
    measure = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    measure_draw = ImageDraw.Draw(measure)
    bounds = measure_draw.textbbox((0, 0), text, font=font, stroke_width=STROKE_WIDTH)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    if text_width > CANVAS_WIDTH - 16 or text_height > CANVAS_HEIGHT:
        raise ValueError(f"subtitle does not fit {CANVAS_WIDTH}x{CANVAS_HEIGHT}: {text!r}")

    image = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = (CANVAS_WIDTH - text_width) // 2 - bounds[0]
    y = (CANVAS_HEIGHT - text_height) // 2 - bounds[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=STROKE_WIDTH,
        stroke_fill=(0, 0, 0, 255),
    )

    # 4bpp indices: 0 transparent, 1 black outline, 2 white glyph.
    indices = bytearray(CANVAS_WIDTH * CANVAS_HEIGHT)
    pixels = image.load()
    for py in range(CANVAS_HEIGHT):
        row = py * CANVAS_WIDTH
        for px in range(CANVAS_WIDTH):
            red, green, blue, alpha = pixels[px, py]
            if alpha < 96:
                index = 0
            elif red + green + blue >= 384:
                index = 2
            else:
                index = 1
            indices[row + px] = index

    packed = bytearray(len(indices) // 2)
    for index in range(0, len(indices), 2):
        packed[index // 2] = indices[index] | (indices[index + 1] << 4)
    return image, bytes(packed), {
        "text_width_px": text_width,
        "text_height_px": text_height,
        "origin_x": x,
        "origin_y": y,
    }


def make_record(index: int, row: dict[str, str], pixels: bytes, record_size: int) -> bytes:
    subtitle_hash = hashlib.sha256(row["subtitle_id"].encode("utf-8")).digest()[:16]
    duration_ms = int(row["estimated_duration_ms"])
    duration_frames = max(1, math.ceil(duration_ms * FPS / 1000))
    header = bytearray(RECORD_HEADER_SIZE)
    struct.pack_into(
        "<4sHHHHBBBBHIII16s",
        header,
        0,
        RECORD_MAGIC,
        index,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
        duration_frames,
        int(row["program_index"]),
        int(row["tone_index"]),
        int(row["vag_index"]),
        0,
        int(row["event_number"]),
        int(row["bank_lba"]),
        len(pixels),
        0,
        subtitle_hash,
    )
    record = header + pixels
    if len(record) > record_size:
        raise ValueError("subtitle record exceeds its fixed sector allocation")
    record.extend(b"\x00" * (record_size - len(record)))
    return bytes(record)


def build_contact_sheets(items: list[dict], output_dir: Path) -> list[str]:
    by_speaker: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_speaker[item["speaker_code"]].append(item)
    paths: list[str] = []
    for speaker, speaker_items in sorted(by_speaker.items()):
        sheet = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT * len(speaker_items)), (48, 48, 48, 255))
        for row_index, item in enumerate(speaker_items):
            image = Image.open(output_dir / item["preview_png"]).convert("RGBA")
            sheet.alpha_composite(image, (0, row_index * CANVAS_HEIGHT))
        relative = f"contact_sheets/{speaker}.png"
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(destination)
        paths.append(relative)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    raw_catalog = args.catalog.read_bytes()
    if not raw_catalog.startswith(b"\xef\xbb\xbf"):
        raise ValueError("voice catalog must retain its UTF-8 BOM")
    with args.catalog.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 106:
        raise ValueError(f"expected 106 catalog rows, found {len(rows)}")
    if len({row["subtitle_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate subtitle_id in voice catalog")
    if any(not row["korean_translation"].strip() for row in rows):
        raise ValueError("blank korean_translation in voice catalog")

    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    previews_dir = args.output_dir / "previews"
    previews_dir.mkdir(parents=True)

    font = ImageFont.truetype(str(args.font), FONT_SIZE)
    pixel_bytes = CANVAS_WIDTH * CANVAS_HEIGHT // 2
    record_size = math.ceil((RECORD_HEADER_SIZE + pixel_bytes) / SECTOR_SIZE) * SECTOR_SIZE
    palette_bgr555 = [0x0000, 0x8000, 0xFFFF] + [0x0000] * 13
    global_header = bytearray(HEADER_SIZE)
    struct.pack_into(
        "<8sHHHHHHII32s",
        global_header,
        0,
        PACK_MAGIC,
        VERSION,
        len(rows),
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
        FONT_SIZE,
        STROKE_WIDTH,
        record_size,
        pixel_bytes,
        struct.pack("<16H", *palette_bgr555),
    )

    manifest_rows: list[dict] = []
    records: list[bytes] = []
    for index, row in enumerate(rows):
        image, pixels, render_meta = render_subtitle(row["korean_translation"], font)
        preview_relative = f"previews/{index + 1:03d}.png"
        image.save(args.output_dir / preview_relative)
        records.append(make_record(index, row, pixels, record_size))
        manifest_rows.append(
            {
                "asset_index": index,
                "record_sector": 1 + index * (record_size // SECTOR_SIZE),
                "record_byte_offset": HEADER_SIZE + index * record_size,
                "record_size": record_size,
                "subtitle_id": row["subtitle_id"],
                "bank_path": row["bank_path"],
                "bank_lba": int(row["bank_lba"]),
                "event_number": int(row["event_number"]),
                "speaker_code": row["speaker_code"],
                "program_index": int(row["program_index"]),
                "tone_index": int(row["tone_index"]),
                "vag_index": int(row["vag_index"]),
                "estimated_duration_ms": int(row["estimated_duration_ms"]),
                "display_frames_60hz": max(1, math.ceil(int(row["estimated_duration_ms"]) * FPS / 1000)),
                "korean_translation": row["korean_translation"],
                "preview_png": preview_relative,
                **render_meta,
            }
        )

    pack_path = args.output_dir / "subtitle_pack.bin"
    with pack_path.open("wb") as output:
        output.write(global_header)
        for record in records:
            output.write(record)

    contact_sheets = build_contact_sheets(manifest_rows, args.output_dir)
    manifest = {
        "format": "BBSUB001",
        "version": VERSION,
        "source_catalog": str(args.catalog),
        "source_catalog_sha256": hashlib.sha256(raw_catalog).hexdigest().upper(),
        "font": str(args.font),
        "font_sha256": sha256_file(args.font),
        "render": {
            "canvas_width": CANVAS_WIDTH,
            "canvas_height": CANVAS_HEIGHT,
            "font_size": FONT_SIZE,
            "stroke_width": STROKE_WIDTH,
            "pixel_format": "4bpp indexed; low nibble is left pixel",
            "palette_bgr555": [f"0x{value:04X}" for value in palette_bgr555],
            "palette_semantics": {"0": "transparent", "1": "black outline", "2": "white glyph"},
        },
        "pack": {
            "path": pack_path.name,
            "sha256": sha256_file(pack_path),
            "header_size": HEADER_SIZE,
            "record_size": record_size,
            "record_sectors": record_size // SECTOR_SIZE,
            "record_count": len(records),
            "total_size": pack_path.stat().st_size,
        },
        "runtime_key": ["bank_lba", "program_index", "tone_index", "vag_index"],
        "runtime_status": "asset_ready_trigger_hook_pending",
        "contact_sheets": contact_sheets,
        "speaker_counts": dict(sorted(Counter(row["speaker_code"] for row in rows).items())),
        "entries": manifest_rows,
    }
    manifest_path = args.output_dir / "subtitle_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with (args.output_dir / "subtitle_mapping.csv").open("w", encoding="utf-8-sig", newline="") as output:
        fields = [
            "asset_index", "record_sector", "subtitle_id", "bank_path", "bank_lba",
            "program_index", "tone_index", "vag_index", "display_frames_60hz",
            "korean_translation", "preview_png",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"entries={len(manifest_rows)}")
    print(f"record_size={record_size} bytes ({record_size // SECTOR_SIZE} sectors)")
    print(f"pack_size={pack_path.stat().st_size}")
    print(f"pack_sha256={manifest['pack']['sha256']}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
