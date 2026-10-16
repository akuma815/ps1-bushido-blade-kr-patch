#!/usr/bin/env python3
"""Extract the USA title and loading logos as editable PNGs.

Mirrors the Japanese asset convention (docs/title-image.md): each asset is
written twice -- `<name>_original.png` is always refreshed and is the
reference, `<name>_ko_edit.png` is the file to paint on and is never
overwritten once it exists.

Both assets are 4bpp TIMs with a 16-entry CLUT, so the re-insertion step can
be a straight pixel-payload overwrite as long as edits stay inside those 16
colours.  A CLUT entry of 0x0000 is PlayStation transparency; everything else
is drawn opaque here (the STP bit only matters for semi-transparent blending,
which these assets do not use).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc

ROOT = Path(__file__).resolve().parents[2]
SCAN = ROOT / "work" / "analysis" / "usa_tim_scan.json"
OUT = ROOT / "workspace" / "graphics" / "usa"

# (asset name, containing file, TIM offset) -- the title sits at the same
# offset as the Japanese one (DATA3/O12.DAT @0x1CC, 480x128); the loading logo
# is the single TIM inside the executable, the USA twin of SLPM_86020 @0x7657C.
ASSETS = (
    ("title", "DATA3/O12.DAT", 0x0001CC),
    ("loading", "SCUS_941.80", 0x074A84),
)


def relative(path: Path) -> str:
    """Repo-relative when possible; an --output-dir outside it stays absolute."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def clut_rgba(value: int) -> tuple[int, int, int, int]:
    if value == 0:
        return (0, 0, 0, 0)
    return ((value & 31) * 8, ((value >> 5) & 31) * 8, ((value >> 10) & 31) * 8, 255)


def decode(data: bytes, record: dict):
    from PIL import Image

    if record["bpp"] != 4:
        raise SystemExit(f"{record['path']}: only 4bpp is handled here")
    clut = record["clut"]
    raw = data[clut["payload_offset"]:clut["payload_offset"] + clut["payload_size"]]
    palette = [clut_rgba(v) for v in struct.unpack(f"<{len(raw) // 2}H", raw)]

    image = record["image"]
    width = record["pixel_width"]
    height = record["pixel_height"]
    start = image["payload_offset"]
    out = Image.new("RGBA", (width, height))
    pixels = out.load()
    for y in range(height):
        row = data[start + y * (width // 2):start + (y + 1) * (width // 2)]
        for x in range(0, width, 2):
            byte = row[x // 2]
            pixels[x, y] = palette[byte & 0xF]
            pixels[x + 1, y] = palette[byte >> 4]
    return out, palette


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, nargs="?",
                        default=ROOT / "original" / "Bushido Blade (USA).bin")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()

    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    info = disc.inspect(args.image)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for name, path, offset in ASSETS:
        record = next((r for r in scan["records"]
                       if r["path"] == path and r["offset"] == offset), None)
        if record is None:
            raise SystemExit(f"{name}: no TIM at {path} +0x{offset:X}")
        entry = next(e for e in info["entries"] if e["path"] == path)
        with args.image.open("rb") as handle:
            data, forms = disc.read_extent(handle, entry["lba"], entry["size"])
        assert forms == {"form1"}, forms

        picture, palette = decode(data, record)
        original = args.output_dir / f"{name}_original.png"
        editable = args.output_dir / f"{name}_ko_edit.png"
        picture.save(original)
        if not editable.exists():
            picture.save(editable)

        manifest.append({
            "asset": name,
            "path": path,
            "offset": f"0x{offset:06X}",
            "size": f"{record['pixel_width']}x{record['pixel_height']}",
            "bpp": record["bpp"],
            "tim_total_size": record["total_size"],
            "pixel_payload_size": record["image"]["payload_size"],
            "clut_entries": len(palette),
            "transparent_indices": [i for i, c in enumerate(palette) if c[3] == 0],
            "palette_rgb": [f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}" for c in palette],
            "original_png": relative(original),
            "edit_png": relative(editable),
        })
        print(f"{name:8s} {path} +0x{offset:06X}  "
              f"{record['pixel_width']}x{record['pixel_height']}  -> {editable.name}")

    report = args.output_dir / "assets.json"
    report.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"manifest: {report}")


if __name__ == "__main__":
    main()
