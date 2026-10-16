#!/usr/bin/env python3
"""Create labeled contact sheets from PNGs listed in a TIM manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def checkerboard(width: int, height: int, cell: int = 8) -> Image.Image:
    image = Image.new("RGB", (width, height), "#d8d8d8")
    draw = ImageDraw.Draw(image)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill="#f0f0f0")
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--path-regex", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--per-sheet", type=int, default=60)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pattern = re.compile(args.path_regex, re.IGNORECASE)
    records = [
        record
        for record in manifest["records"]
        if pattern.search(record["path"]) and record.get("png")
    ]
    records.sort(key=lambda item: (item["path"], item["offset"]))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    columns = 5
    cell_width = 240
    cell_height = 190
    image_box = (220, 145)
    font = ImageFont.load_default()
    outputs = []
    for page, start in enumerate(range(0, len(records), args.per_sheet), 1):
        page_records = records[start : start + args.per_sheet]
        rows = (len(page_records) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
        draw = ImageDraw.Draw(sheet)
        for index, record in enumerate(page_records):
            column = index % columns
            row = index // columns
            left = column * cell_width
            top = row * cell_height
            tile = checkerboard(*image_box)
            source = Image.open(record["png"]).convert("RGBA")
            scale = min(
                image_box[0] / max(1, source.width),
                image_box[1] / max(1, source.height),
            )
            scale = max(1.0, scale)
            resized = source.resize(
                (
                    max(1, int(source.width * scale)),
                    max(1, int(source.height * scale)),
                ),
                Image.Resampling.NEAREST,
            )
            if resized.width > image_box[0] or resized.height > image_box[1]:
                resized.thumbnail(image_box, Image.Resampling.NEAREST)
            x = (image_box[0] - resized.width) // 2
            y = (image_box[1] - resized.height) // 2
            tile.paste(resized, (x, y), resized)
            sheet.paste(tile, (left + 10, top + 5))
            label = (
                f"{record['path']} @ {record['offset']:08X}\n"
                f"{record['pixel_width']}x{record['pixel_height']} {record['bpp']}bpp"
            )
            draw.multiline_text((left + 10, top + 152), label, fill="black", font=font)
            source.close()
        output = args.output_dir / f"tim_contact_{page:03d}.png"
        sheet.save(output)
        outputs.append(output.as_posix())
    print(f"records={len(records)}")
    print(f"sheets={len(outputs)}")
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
