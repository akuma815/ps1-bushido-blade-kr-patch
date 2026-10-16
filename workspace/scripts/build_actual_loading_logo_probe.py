#!/usr/bin/env python3
"""Build a pixel-inversion probe for the runtime-linked loading-logo TIM."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

import build_options_poc as shared
import inspect_psx_disc as disc
import scan_psx_tim as timlib


EXPECTED_BIN_SHA256 = (
    "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
)
EXPECTED_FILE_SHA256 = (
    "0164189A870BA87495A3C0010ADB85535AE5A798BBD0DD276B88BF24ECC3CEDA"
)
TARGET_PATH = "SLPM_860.20"
TARGET_TIM_OFFSET = 0x7657C
TARGET_PROPERTIES = (4, 136, 32, 2240)


def render_preview(data: bytes, parsed: dict) -> Image.Image:
    indices = shared.decode_4bpp_indices(data, parsed)
    palette = shared.palette_values(data, parsed)
    pixels = [timlib.psx_color(palette[index]) for index in indices]
    image = Image.new(
        "RGBA", (parsed["pixel_width"], parsed["pixel_height"])
    )
    image.putdata(pixels)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--edited-png",
        type=Path,
        help=(
            "136x32 opaque PNG to insert. When omitted, build the diagnostic "
            "pixel-inversion probe."
        ),
    )
    parser.add_argument(
        "--right-guard-columns",
        type=int,
        default=0,
        help=(
            "diagnostic only: force the rightmost N pixel columns to the "
            "brightest original palette entry"
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    original_hash = shared.sha256_file(args.image)
    if original_hash != EXPECTED_BIN_SHA256:
        raise ValueError(
            f"unsupported BIN SHA-256: {original_hash}, expected {EXPECTED_BIN_SHA256}"
        )

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
    if entry["sha256"].upper() != EXPECTED_FILE_SHA256:
        raise ValueError(f"{TARGET_PATH} source hash mismatch")
    if entry["sector_forms"] != ["form1"]:
        raise ValueError(f"{TARGET_PATH} is not entirely Form 1")

    with args.image.open("rb") as source:
        original_file, _ = disc.read_extent(source, entry["lba"], entry["size"])
    target = timlib.parse_tim(original_file, TARGET_TIM_OFFSET)
    if target is None:
        raise ValueError("runtime-linked loading-logo TIM did not validate")
    properties = (
        target["bpp"],
        target["pixel_width"],
        target["pixel_height"],
        target["total_size"],
    )
    if properties != TARGET_PROPERTIES:
        raise ValueError(f"loading-logo TIM properties changed: {properties}")

    original_indices = shared.decode_4bpp_indices(original_file, target)
    image_meta = target["image"]
    original_payload = original_file[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    if shared.encode_4bpp_indices(original_indices) != original_payload:
        raise ValueError("loading-logo TIM failed byte-identical 4bpp roundtrip")

    original_palette = shared.palette_values(original_file, target)
    if args.edited_png is None:
        probe_indices = [15 - index for index in original_indices]
        input_png_hash = None
        method = "invert every 4bpp pixel index while preserving the CLUT"
        build_kind = "probe"
    else:
        edited = Image.open(args.edited_png)
        edited.load()
        if edited.size != (target["pixel_width"], target["pixel_height"]):
            raise ValueError(
                "edited loading logo must be 136x32, "
                f"got {edited.size[0]}x{edited.size[1]}"
            )
        rgba = edited.convert("RGBA")
        if rgba.getchannel("A").getextrema() != (255, 255):
            raise ValueError("edited loading logo must be fully opaque")
        palette_rgba = [timlib.psx_color(value) for value in original_palette]
        probe_indices = []
        for r, g, b, _ in rgba.get_flattened_data():
            probe_indices.append(
                min(
                    range(16),
                    key=lambda index: (
                        (r - palette_rgba[index][0]) ** 2
                        + (g - palette_rgba[index][1]) ** 2
                        + (b - palette_rgba[index][2]) ** 2
                    ),
                )
            )
        input_png_hash = shared.sha256_file(args.edited_png)
        method = "map the edited PNG to the original 16-entry grayscale CLUT"
        build_kind = "edited_png"
        if not 0 <= args.right_guard_columns < target["pixel_width"]:
            raise ValueError("--right-guard-columns must be between 0 and 135")
        if args.right_guard_columns:
            palette_rgba = [timlib.psx_color(value) for value in original_palette]
            background_index = max(
                range(16),
                key=lambda index: sum(palette_rgba[index][:3]),
            )
            guard_start = target["pixel_width"] - args.right_guard_columns
            for y in range(target["pixel_height"]):
                row_start = y * target["pixel_width"]
                for x in range(guard_start, target["pixel_width"]):
                    probe_indices[row_start + x] = background_index
            method += (
                f"; force rightmost {args.right_guard_columns} columns "
                "to the brightest palette entry"
            )
            build_kind = "right_guard_probe"
    probe_payload = shared.encode_4bpp_indices(probe_indices)
    allowed_range = (
        image_meta["payload_offset"],
        image_meta["payload_offset"] + image_meta["payload_size"],
    )
    patched_file = bytearray(original_file)
    patched_file[allowed_range[0] : allowed_range[1]] = probe_payload
    changed_file_offsets = [
        offset
        for offset, (before, after) in enumerate(zip(original_file, patched_file))
        if before != after
    ]
    if not changed_file_offsets or any(
        not allowed_range[0] <= offset < allowed_range[1]
        for offset in changed_file_offsets
    ):
        if not changed_file_offsets:
            raise ValueError(
                "edited PNG encodes identically to the original loading logo; "
                "edit workspace/graphics/loading/edited/SLPM_86020_0007657C.png "
                "before building"
            )
        raise ValueError("file diff escaped the target TIM image payload")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    base_name = (
        "Bushido Blade (Japan) [Actual Loading Logo Pixel Probe]"
        if build_kind == "probe"
        else (
            "Bushido Blade (Japan) [KO Loading Logo Right Guard Probe]"
            if build_kind == "right_guard_probe"
            else "Bushido Blade (Japan) [KO Loading Logo]"
        )
    )
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    output_file = args.output_dir / (
        "SLPM_860.20.probe"
        if build_kind == "probe"
        else "SLPM_860.20.patched"
    )
    preview_png = args.output_dir / (
        "actual_loading_logo_probe_preview.png"
        if build_kind == "probe"
        else "loading_logo_ko_psx_preview.png"
    )
    report_path = args.output_dir / "build_report.json"

    output_file.write_bytes(patched_file)
    render_preview(bytes(patched_file), target).save(preview_png)
    shutil.copyfile(args.image, output_bin)

    changes_by_sector: dict[int, list[int]] = {}
    for file_offset in changed_file_offsets:
        changes_by_sector.setdefault(
            file_offset // disc.LOGICAL_SECTOR_SIZE, []
        ).append(file_offset)

    touched_sectors = []
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
                patched_sector[local] = patched_file[file_offset]
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
            touched_sectors.append(
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
        reparsed_file, forms = disc.read_extent(
            patched_track, entry["lba"], entry["size"]
        )
    reparsed = timlib.parse_tim(reparsed_file, TARGET_TIM_OFFSET)
    if forms != {"form1"} or reparsed_file != bytes(patched_file):
        raise ValueError(f"patched {TARGET_PATH} did not survive disc reinsertion")
    if reparsed is None:
        raise ValueError("patched loading-logo TIM no longer validates")
    if shared.decode_4bpp_indices(reparsed_file, reparsed) != probe_indices:
        raise ValueError("probe indices differ after disc reinsertion")
    if output_bin.stat().st_size != args.image.stat().st_size:
        raise ValueError("patched BIN size changed")

    report = {
        "gate_id": (
            "BBJ-POC-GFX-004"
            if build_kind == "probe"
            else (
                "BBJ-DIAG-GFX-LOAD-EDGE-001"
                if build_kind == "right_guard_probe"
                else "BBJ-BUILD-GFX-LOAD-001"
            )
        ),
        "experiment_id": (
            "BBJ-POC-GFX-004-PIXELS"
            if build_kind == "probe"
            else (
                "BBJ-DIAG-GFX-LOAD-EDGE-001-RIGHT-GUARD"
                if build_kind == "right_guard_probe"
                else "BBJ-BUILD-GFX-LOAD-001-PNG"
            )
        ),
        "status": "static_build_passed_runtime_pending",
        "purpose": (
            "Confirm runtime reachability of the DuckStation-linked loading-logo "
            "TIM in SLPM_860.20 by inverting every 4bpp pixel index."
            if build_kind == "probe"
            else "Insert the edited Korean loading logo into SLPM_860.20."
        ),
        "baseline": {
            "image": str(args.image),
            "sha256": original_hash,
            "size": args.image.stat().st_size,
        },
        "target": {
            "disc_path": TARGET_PATH,
            "file_lba": entry["lba"],
            "file_size": entry["size"],
            "file_original_sha256": EXPECTED_FILE_SHA256,
            "tim_offset": TARGET_TIM_OFFSET,
            "tim_total_size": target["total_size"],
            "format": "TIM 4bpp indexed",
            "pixel_width": target["pixel_width"],
            "pixel_height": target["pixel_height"],
            "runtime_dump_evidence": (
                "work/graphics/duckstation_loading_dump/"
                "runtime_loading_logo_136x32.png"
            ),
        },
        "probe": {
            "build_kind": build_kind,
            "method": method,
            "clut_preserved": True,
            "edited_png": (
                None if args.edited_png is None else str(args.edited_png)
            ),
            "edited_png_sha256": input_png_hash,
            "right_guard_columns": args.right_guard_columns,
            "changed_file_byte_count": len(changed_file_offsets),
            "changed_file_ranges": [
                {"start": start, "end": end}
                for start, end in shared.contiguous_ranges(changed_file_offsets)
            ],
        },
        "touched_sectors": touched_sectors,
        "output": {
            "bin": str(output_bin),
            "bin_sha256": shared.sha256_file(output_bin),
            "bin_size": output_bin.stat().st_size,
            "cue": str(output_cue),
            "patched_file": str(output_file),
            "patched_file_sha256": shared.sha256_bytes(bytes(patched_file)),
            "preview": str(preview_png),
        },
        "static_checks": {
            "source_hash": True,
            "tim_roundtrip": True,
            "pixel_indices_match_probe": True,
            "tim_size_preserved": True,
            "file_size_preserved": len(patched_file) == len(original_file),
            "disc_size_preserved": output_bin.stat().st_size
            == args.image.stat().st_size,
            "raw_diff_within_expected_writes": True,
            "mode2_form1_integrity": True,
            "disc_reparse": True,
        },
        "runtime": {
            "status": "pending",
            "required_screen": "게임 기동 직후 로딩 화면",
            "required_observation": (
                "우측 아래 일본어 로고의 명암과 배경이 반전되는지 확인"
                if build_kind == "probe"
                else "우측 아래 로딩 로고가 편집 PNG와 같은 한글로 표시되는지 확인"
            ),
            "pass": (
                "로딩 로고가 반전되고 타이틀 메뉴 진입이 정상"
                if build_kind == "probe"
                else "한글 로딩 로고가 표시되고 타이틀 메뉴 진입이 정상"
            ),
            "fail": (
                "로딩 로고가 원본과 동일"
                if build_kind == "probe"
                else "로고가 원본 일본어로 남거나 깨짐"
            ),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"output_bin={output_bin}")
    print(f"output_cue={output_cue}")
    print(f"preview={preview_png}")
    print(f"report={report_path}")
    print(f"changed_file_bytes={len(changed_file_offsets)}")
    print(f"touched_sectors={len(touched_sectors)}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
