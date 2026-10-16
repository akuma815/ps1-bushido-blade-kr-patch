#!/usr/bin/env python3
"""Extract Bushido Blade's title-logo TIM as editable and reference assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

from PIL import Image

from scan_psx_tim import decode_tim, parse_tim, psx_color


EXPECTED_DAT_SHA256 = "10FB76FA2D09F99E48CF3B12B26521C6FE9618F15E0EFD2DF72F4CB79025E83B"
TIM_OFFSET = 0x1CC
EXPECTED_TIM_SIZE = 30_784
EXPECTED_WIDTH = 480
EXPECTED_HEIGHT = 128


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("o12_dat", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    data = args.o12_dat.read_bytes()
    dat_hash = sha256(data)
    if dat_hash != EXPECTED_DAT_SHA256:
        raise SystemExit(
            f"unsupported O12.DAT SHA-256: {dat_hash}; "
            f"expected {EXPECTED_DAT_SHA256}"
        )

    tim = parse_tim(data, TIM_OFFSET)
    if tim is None:
        raise SystemExit(f"valid TIM not found at 0x{TIM_OFFSET:X}")
    actual_shape = (tim["total_size"], tim["pixel_width"], tim["pixel_height"])
    expected_shape = (EXPECTED_TIM_SIZE, EXPECTED_WIDTH, EXPECTED_HEIGHT)
    if actual_shape != expected_shape or tim["bpp"] != 4:
        raise SystemExit(
            f"unexpected title TIM: size/width/height={actual_shape}, "
            f"bpp={tim['bpp']}"
        )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    tim_data = data[TIM_OFFSET : TIM_OFFSET + tim["total_size"]]

    raw_path = output_dir / "title_original.tim"
    rgba_path = output_dir / "title_original.png"
    indexed_path = output_dir / "title_original_indexed.png"
    edit_path = output_dir / "title_ko_edit.png"
    palette_path = output_dir / "title_palette.json"
    manifest_path = output_dir / "title_asset_manifest.json"

    raw_path.write_bytes(tim_data)
    rgba = decode_tim(data, tim)
    rgba.save(rgba_path)

    image_meta = tim["image"]
    payload = data[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    indices = bytearray()
    for value in payload:
        indices.append(value & 0x0F)
        indices.append((value >> 4) & 0x0F)
    indexed = Image.frombytes("P", (EXPECTED_WIDTH, EXPECTED_HEIGHT), bytes(indices))

    clut = tim["clut"]
    if clut is None:
        raise SystemExit("title TIM has no CLUT")
    clut_data = data[
        clut["payload_offset"] : clut["payload_offset"] + clut["payload_size"]
    ]
    colors = []
    png_palette = []
    transparent_indices = []
    for index in range(16):
        raw = struct.unpack_from("<H", clut_data, index * 2)[0]
        rgba_color = psx_color(raw)
        if rgba_color[3] == 0:
            transparent_indices.append(index)
        colors.append(
            {
                "index": index,
                "psx_bgr555": f"0x{raw:04X}",
                "rgba": list(rgba_color),
                "pixel_count": indices.count(index),
            }
        )
        png_palette.extend(rgba_color[:3])
    png_palette.extend([0] * (768 - len(png_palette)))
    indexed.putpalette(png_palette)
    if len(transparent_indices) > 1:
        raise SystemExit(
            f"indexed PNG cannot represent multiple transparent indices: "
            f"{transparent_indices}"
        )
    if transparent_indices:
        indexed.save(indexed_path, transparency=transparent_indices[0])
    else:
        indexed.save(indexed_path)

    # This is the user's editable handoff. Never overwrite existing manual work.
    if not edit_path.exists():
        shutil.copyfile(rgba_path, edit_path)

    palette_path.write_text(
        json.dumps({"palette": colors}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "asset_id": "BBJ-TITLE-LOGO-001",
        "source": {
            "path": "DATA3/O12.DAT",
            "sha256": dat_hash,
            "tim_offset": TIM_OFFSET,
            "tim_offset_hex": f"0x{TIM_OFFSET:08X}",
            "tim_size": tim["total_size"],
        },
        "format": {
            "container": "PlayStation TIM",
            "bpp": tim["bpp"],
            "width": tim["pixel_width"],
            "height": tim["pixel_height"],
            "palette_entries": 16,
            "transparent_palette_indices": transparent_indices,
            "background_palette_index": 15,
            "background_rgba": [255, 255, 255, 255],
        },
        "outputs": {
            "raw_tim": raw_path.name,
            "rgba_png": rgba_path.name,
            "indexed_png": indexed_path.name,
            "editable_png": edit_path.name,
            "palette": palette_path.name,
        },
        "hashes": {
            "raw_tim_sha256": sha256(raw_path.read_bytes()),
            "rgba_png_sha256": sha256(rgba_path.read_bytes()),
            "indexed_png_sha256": sha256(indexed_path.read_bytes()),
        },
        "editable_png_matches_original": edit_path.stat().st_size == rgba_path.stat().st_size
        and sha256(edit_path.read_bytes()) == sha256(rgba_path.read_bytes()),
        "note": "title_ko_edit.png is created only when missing and is never overwritten.",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"title_tim={raw_path}")
    print(f"title_rgba={rgba_path}")
    print(f"title_indexed={indexed_path}")
    print(f"title_edit={edit_path}")
    print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
