#!/usr/bin/env python3
"""Create labeled contact sheets for arbitrary PNG files."""

from __future__ import annotations

import argparse
from datetime import datetime
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
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--since", help="local ISO datetime lower bound")
    parser.add_argument("--name-prefix")
    parser.add_argument("--per-sheet", type=int, default=40)
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since).timestamp() if args.since else None
    records = []
    for path in args.input_dir.glob("*.png"):
        if since is not None and path.stat().st_mtime < since:
            continue
        if args.name_prefix and not path.name.startswith(args.name_prefix):
            continue
        with Image.open(path) as image:
            records.append(
                {
                    "path": path,
                    "width": image.width,
                    "height": image.height,
                    "mtime": path.stat().st_mtime,
                }
            )
    records.sort(key=lambda item: (item["mtime"], item["path"].name))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    columns = 4
    cell_width = 300
    cell_height = 235
    image_box = (280, 180)
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
            with Image.open(record["path"]) as opened:
                source = opened.convert("RGBA")
            scale = min(
                image_box[0] / max(1, source.width),
                image_box[1] / max(1, source.height),
            )
            scale = min(4.0, max(1.0, scale))
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
                f"{record['path'].name}\n"
                f"{record['width']}x{record['height']}"
            )
            draw.multiline_text((left + 10, top + 190), label, fill="black", font=font)
        output = args.output_dir / f"png_contact_{page:03d}.png"
        sheet.save(output)
        outputs.append(output)
    print(f"records={len(records)}")
    print(f"sheets={len(outputs)}")
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
