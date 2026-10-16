#!/usr/bin/env python3
"""Build a title-logo TIM PoC from the user-edited PNG and the clean JP BIN."""

from __future__ import annotations

import argparse
import json
import shutil
import struct
from collections import Counter
from pathlib import Path

from PIL import Image

import build_options_poc as shared
import inspect_psx_disc as disc
import scan_psx_tim as timlib


EXPECTED_BIN_SHA256 = (
    "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
)
EXPECTED_O12_SHA256 = (
    "10FB76FA2D09F99E48CF3B12B26521C6FE9618F15E0EFD2DF72F4CB79025E83B"
)
TARGET_PATH = "DATA3/O12.DAT"
TARGET_TIM_OFFSET = 0x1CC
TARGET_PROPERTIES = (4, 480, 128, 30_784)


def rgb_to_psx(r: int, g: int, b: int) -> int:
    """Convert opaque RGB to BGR555 while preserving the original STP-bit policy."""
    r5 = (r * 31 + 127) // 255
    g5 = (g * 31 + 127) // 255
    b5 = (b * 31 + 127) // 255
    return 0x8000 | r5 | (g5 << 5) | (b5 << 10)


def quantize_for_psx(source: Image.Image) -> tuple[list[int], list[int], Image.Image]:
    if source.size != (480, 128):
        raise ValueError(f"edited title must be 480x128, got {source.size}")
    rgba = source.convert("RGBA")
    if rgba.getchannel("A").getextrema() != (255, 255):
        raise ValueError("edited title must be fully opaque")

    rgb = rgba.convert("RGB")
    quantized = rgb.quantize(
        colors=16,
        method=Image.Quantize.MAXCOVERAGE,
        dither=Image.Dither.NONE,
    )
    indices = list(quantized.get_flattened_data())
    if not indices or max(indices) >= 16:
        raise ValueError("quantizer produced an index outside the 4bpp palette")

    raw_palette = quantized.getpalette()
    if raw_palette is None:
        raise ValueError("quantizer did not return a palette")
    psx_palette = []
    for index in range(16):
        r, g, b = raw_palette[index * 3 : index * 3 + 3]
        psx_palette.append(rgb_to_psx(r, g, b))

    preview_pixels = [timlib.psx_color(psx_palette[index]) for index in indices]
    preview = Image.new("RGBA", rgb.size)
    preview.putdata(preview_pixels)
    return indices, psx_palette, preview


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--edited-png", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    original_hash = shared.sha256_file(args.image)
    if original_hash != EXPECTED_BIN_SHA256:
        raise ValueError(
            f"unsupported BIN SHA-256: {original_hash}, expected {EXPECTED_BIN_SHA256}"
        )
    edited_hash = shared.sha256_file(args.edited_png)
    edited = Image.open(args.edited_png)
    edited.load()
    source_color_count = len(edited.convert("RGBA").getcolors(maxcolors=1_000_000) or [])
    indices, palette, preview = quantize_for_psx(edited)
    encoded_payload = shared.encode_4bpp_indices(indices)

    inspection = disc.inspect(args.image)
    entry = next(
        (
            item
            for item in inspection["entries"]
            if not item["is_dir"] and item["path"] == TARGET_PATH
        ),
        None,
    )
    if entry is None:
        raise ValueError(f"disc file not found: {TARGET_PATH}")
    if entry["sha256"].upper() != EXPECTED_O12_SHA256:
        raise ValueError("DATA3/O12.DAT source hash mismatch")
    if entry["sector_forms"] != ["form1"]:
        raise ValueError("DATA3/O12.DAT is not entirely Form 1")

    with args.image.open("rb") as source:
        original_dat, _ = disc.read_extent(source, entry["lba"], entry["size"])
    target = timlib.parse_tim(original_dat, TARGET_TIM_OFFSET)
    if target is None:
        raise ValueError("title TIM did not validate")
    properties = (
        target["bpp"],
        target["pixel_width"],
        target["pixel_height"],
        target["total_size"],
    )
    if properties != TARGET_PROPERTIES:
        raise ValueError(f"title TIM properties changed: {properties}")

    original_indices = shared.decode_4bpp_indices(original_dat, target)
    image_meta = target["image"]
    original_payload = original_dat[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    if shared.encode_4bpp_indices(original_indices) != original_payload:
        raise ValueError("original title TIM failed byte-identical 4bpp roundtrip")
    if len(encoded_payload) != image_meta["payload_size"]:
        raise ValueError("edited title payload size changed")

    clut = target["clut"]
    if clut is None or clut["payload_size"] != 32:
        raise ValueError("title TIM does not have the expected 16-entry CLUT")
    encoded_clut = b"".join(struct.pack("<H", value) for value in palette)

    patched_dat = bytearray(original_dat)
    patched_dat[
        clut["payload_offset"] : clut["payload_offset"] + clut["payload_size"]
    ] = encoded_clut
    patched_dat[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ] = encoded_payload
    changed_dat_offsets = [
        offset
        for offset, (before, after) in enumerate(zip(original_dat, patched_dat))
        if before != after
    ]
    allowed_ranges = [
        (clut["payload_offset"], clut["payload_offset"] + clut["payload_size"]),
        (
            image_meta["payload_offset"],
            image_meta["payload_offset"] + image_meta["payload_size"],
        ),
    ]
    if not changed_dat_offsets or any(
        not any(start <= offset < end for start, end in allowed_ranges)
        for offset in changed_dat_offsets
    ):
        raise ValueError("DAT diff escaped the title TIM CLUT/image payload")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    base_name = "Bushido Blade (Japan) [KO Title PoC]"
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    output_dat = args.output_dir / "O12.patched.DAT"
    preview_png = args.output_dir / "title_ko_psx_preview.png"
    preview_scaled_png = args.output_dir / "title_ko_psx_preview_2x.png"
    report_path = args.output_dir / "build_report.json"

    output_dat.write_bytes(patched_dat)
    preview.save(preview_png)
    preview.resize(
        (preview.width * 2, preview.height * 2), Image.Resampling.NEAREST
    ).save(preview_scaled_png)
    shutil.copyfile(args.image, output_bin)

    changes_by_sector: dict[int, list[int]] = {}
    for file_offset in changed_dat_offsets:
        changes_by_sector.setdefault(file_offset // disc.LOGICAL_SECTOR_SIZE, []).append(
            file_offset
        )

    touched_sector_records = []
    with args.image.open("rb") as original_track, output_bin.open("r+b") as output_track:
        for relative_sector in sorted(changes_by_sector):
            lba = entry["lba"] + relative_sector
            raw_offset = lba * disc.RAW_SECTOR_SIZE
            original_track.seek(raw_offset)
            original_sector = original_track.read(disc.RAW_SECTOR_SIZE)
            if shared.rebuild_mode2_form1_integrity(original_sector) != original_sector:
                raise ValueError(f"original EDC/ECC mismatch at LBA {lba}")

            patched_sector = bytearray(original_sector)
            allowed_data_local = set()
            for file_offset in changes_by_sector[relative_sector]:
                in_sector = file_offset % disc.LOGICAL_SECTOR_SIZE
                local = disc.MODE2_FORM1_DATA_OFFSET + in_sector
                patched_sector[local] = patched_dat[file_offset]
                allowed_data_local.add(local)
            rebuilt = shared.rebuild_mode2_form1_integrity(bytes(patched_sector))
            if shared.rebuild_mode2_form1_integrity(rebuilt) != rebuilt:
                raise ValueError(f"patched EDC/ECC failed self-check at LBA {lba}")

            changed_raw_local = [
                offset
                for offset, (before, after) in enumerate(zip(original_sector, rebuilt))
                if before != after
            ]
            if any(
                offset not in allowed_data_local and not 0x818 <= offset < 0x930
                for offset in changed_raw_local
            ):
                raise ValueError(f"raw diff escaped expected ranges at LBA {lba}")
            output_track.seek(raw_offset)
            output_track.write(rebuilt)
            touched_sector_records.append(
                {
                    "lba": lba,
                    "raw_offset": raw_offset,
                    "original_sha256": shared.sha256_bytes(original_sector),
                    "patched_sha256": shared.sha256_bytes(rebuilt),
                    "changed_local_ranges": [
                        {"start": start, "end": end}
                        for start, end in shared.contiguous_ranges(changed_raw_local)
                    ],
                }
            )

    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n",
        encoding="ascii",
        newline="\n",
    )

    with output_bin.open("rb") as patched_track:
        reparsed_dat, forms = disc.read_extent(
            patched_track, entry["lba"], entry["size"]
        )
    if forms != {"form1"} or reparsed_dat != bytes(patched_dat):
        raise ValueError("patched O12.DAT did not survive disc reinsertion")
    reparsed = timlib.parse_tim(reparsed_dat, TARGET_TIM_OFFSET)
    if reparsed is None:
        raise ValueError("patched title TIM no longer validates")
    if shared.decode_4bpp_indices(reparsed_dat, reparsed) != indices:
        raise ValueError("patched title indices differ after disc reinsertion")
    if shared.palette_values(reparsed_dat, reparsed) != palette:
        raise ValueError("patched title CLUT differs after disc reinsertion")
    if output_bin.stat().st_size != args.image.stat().st_size:
        raise ValueError("patched BIN size changed")

    report = {
        "gate_id": "BBJ-POC-GFX-002",
        "status": "static_build_passed_runtime_pending",
        "baseline": {
            "image": str(args.image),
            "sha256": original_hash,
            "size": args.image.stat().st_size,
        },
        "input": {
            "edited_png": str(args.edited_png),
            "sha256": edited_hash,
            "size": list(edited.size),
            "mode": edited.mode,
            "fully_opaque": True,
            "source_color_count": source_color_count,
        },
        "target": {
            "disc_path": TARGET_PATH,
            "file_lba": entry["lba"],
            "file_size": entry["size"],
            "file_original_sha256": EXPECTED_O12_SHA256,
            "tim_offset": TARGET_TIM_OFFSET,
            "tim_total_size": target["total_size"],
            "format": "TIM 4bpp indexed",
            "pixel_width": target["pixel_width"],
            "pixel_height": target["pixel_height"],
        },
        "conversion": {
            "method": "Pillow MAXCOVERAGE, no dithering, PSX BGR555",
            "output_palette_entries": 16,
            "used_palette_indices": sorted(Counter(indices)),
            "palette_psx_bgr555": [f"0x{value:04X}" for value in palette],
            "stp_bit_policy": "bit 15 set on every entry, matching original CLUT",
        },
        "expected_write": {
            "owner": "BBJ-POC-GFX-002",
            "dat_allowed_ranges": [
                {"start": start, "end": end} for start, end in allowed_ranges
            ],
            "dat_changed_byte_count": len(changed_dat_offsets),
            "dat_changed_ranges": [
                {"start": start, "end": end}
                for start, end in shared.contiguous_ranges(changed_dat_offsets)
            ],
            "touched_sectors": touched_sector_records,
        },
        "output": {
            "bin": str(output_bin),
            "bin_sha256": shared.sha256_file(output_bin),
            "bin_size": output_bin.stat().st_size,
            "cue": str(output_cue),
            "patched_dat": str(output_dat),
            "patched_dat_sha256": shared.sha256_bytes(bytes(patched_dat)),
            "preview": str(preview_png),
            "preview_2x": str(preview_scaled_png),
        },
        "static_checks": {
            "source_hash": True,
            "edited_png_dimensions": True,
            "edited_png_opaque": True,
            "tim_roundtrip": True,
            "tim_headers_preserved": True,
            "tim_size_preserved": True,
            "dat_size_preserved": len(patched_dat) == len(original_dat),
            "disc_size_preserved": output_bin.stat().st_size
            == args.image.stat().st_size,
            "raw_diff_within_expected_writes": True,
            "mode2_form1_integrity": True,
            "disc_reparse": True,
        },
        "runtime": {
            "status": "pending",
            "required_screen": "타이틀/메인 메뉴 화면",
            "required_observation": "한글 타이틀 로고, 영문명, TM이 정상 표시되고 메뉴 진입이 정상",
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"output_bin={output_bin}")
    print(f"output_cue={output_cue}")
    print(f"preview={preview_scaled_png}")
    print(f"report={report_path}")
    print(f"changed_dat_bytes={len(changed_dat_offsets)}")
    print(f"touched_sectors={len(touched_sector_records)}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
