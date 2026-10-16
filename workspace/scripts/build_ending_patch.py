#!/usr/bin/env python3
"""Insert all six edited ending TIM PNGs into DATA3/O15.DAT."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

import build_options_poc as shared
import inspect_psx_disc as disc
import scan_psx_tim as timlib


EXPECTED_INPUT_HASHES = {
    "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96":
        "clean_japanese",
    "3BE610886611C0F63A36121B93DB236ED39D09158A7421804BC23AB83A36DA39":
        "ko_loading_logo",
}
EXPECTED_O15_SHA256 = (
    "486D464AB79F2FBB1E783DB7A00ACC8496AB5B3E979D52F037BE9F735FD27E90"
)
TARGET_PATH = "DATA3/O15.DAT"
TARGETS = (
    ("O15_00000148.png", 0x0148, 32, 216, 3520),
    ("O15_00000F08.png", 0x0F08, 32, 216, 3520),
    ("O15_00001CC8.png", 0x1CC8, 64, 180, 5824),
    ("O15_00003388.png", 0x3388, 64, 216, 6976),
    ("O15_00004EC8.png", 0x4EC8, 64, 204, 6592),
    ("O15_00006888.png", 0x6888, 24, 40, 544),
)


def map_to_original_palette(
    image: Image.Image, palette: list[int]
) -> tuple[list[int], Image.Image]:
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getextrema() != (255, 255):
        raise ValueError("ending PNG must be fully opaque")
    palette_rgba = [timlib.psx_color(value) for value in palette]
    indices = []
    for r, g, b, _ in rgba.get_flattened_data():
        indices.append(
            min(
                range(16),
                key=lambda index: (
                    (r - palette_rgba[index][0]) ** 2
                    + (g - palette_rgba[index][1]) ** 2
                    + (b - palette_rgba[index][2]) ** 2
                ),
            )
        )
    preview = Image.new("RGBA", rgba.size)
    preview.putdata([palette_rgba[index] for index in indices])
    return indices, preview


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--edited-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    input_hash = shared.sha256_file(args.image)
    baseline_kind = EXPECTED_INPUT_HASHES.get(input_hash)
    if baseline_kind is None:
        raise ValueError(
            f"unsupported input BIN SHA-256: {input_hash}; expected the clean "
            "Japanese image or the verified Korean loading-logo build"
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
    if entry["sha256"].upper() != EXPECTED_O15_SHA256:
        raise ValueError(f"{TARGET_PATH} source hash mismatch")
    if entry["sector_forms"] != ["form1"]:
        raise ValueError(f"{TARGET_PATH} is not entirely Form 1")

    with args.image.open("rb") as source:
        original_file, _ = disc.read_extent(source, entry["lba"], entry["size"])
    patched_file = bytearray(original_file)
    target_reports = []
    allowed_ranges = []

    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = args.output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    for filename, offset, width, height, total_size in TARGETS:
        edited_path = args.edited_dir / filename
        if not edited_path.is_file():
            raise ValueError(f"edited ending PNG is missing: {edited_path}")
        edited = Image.open(edited_path)
        edited.load()
        if edited.size != (width, height):
            raise ValueError(
                f"{filename} must be {width}x{height}, got "
                f"{edited.size[0]}x{edited.size[1]}"
            )

        target = timlib.parse_tim(original_file, offset)
        if target is None:
            raise ValueError(f"ending TIM did not validate at 0x{offset:X}")
        properties = (
            target["bpp"],
            target["pixel_width"],
            target["pixel_height"],
            target["total_size"],
        )
        expected = (4, width, height, total_size)
        if properties != expected:
            raise ValueError(
                f"ending TIM properties changed at 0x{offset:X}: "
                f"{properties}, expected {expected}"
            )

        image_meta = target["image"]
        original_indices = shared.decode_4bpp_indices(original_file, target)
        original_payload = original_file[
            image_meta["payload_offset"] :
            image_meta["payload_offset"] + image_meta["payload_size"]
        ]
        if shared.encode_4bpp_indices(original_indices) != original_payload:
            raise ValueError(
                f"ending TIM failed 4bpp roundtrip at 0x{offset:X}"
            )
        palette = shared.palette_values(original_file, target)
        indices, preview = map_to_original_palette(edited, palette)
        encoded = shared.encode_4bpp_indices(indices)
        if len(encoded) != image_meta["payload_size"]:
            raise ValueError(f"encoded payload size changed for {filename}")

        start = image_meta["payload_offset"]
        end = start + image_meta["payload_size"]
        patched_file[start:end] = encoded
        allowed_ranges.append((start, end))
        preview_path = preview_dir / filename
        preview.save(preview_path)
        target_reports.append(
            {
                "filename": filename,
                "edited_png": str(edited_path),
                "edited_png_sha256": shared.sha256_file(edited_path),
                "tim_offset": offset,
                "tim_offset_hex": f"0x{offset:08X}",
                "pixel_width": width,
                "pixel_height": height,
                "tim_total_size": total_size,
                "image_payload_range": {"start": start, "end": end},
                "pixel_indices_changed": sum(
                    before != after
                    for before, after in zip(original_indices, indices)
                ),
                "preview": str(preview_path),
            }
        )

    changed_file_offsets = [
        offset
        for offset, (before, after) in enumerate(zip(original_file, patched_file))
        if before != after
    ]
    if not changed_file_offsets:
        raise ValueError("all edited ending PNGs encode identically to the originals")
    if any(
        not any(start <= offset < end for start, end in allowed_ranges)
        for offset in changed_file_offsets
    ):
        raise ValueError("O15 diff escaped the six TIM image payloads")

    base_name = (
        "Bushido Blade (Japan) [KO Loading+Ending]"
        if baseline_kind == "ko_loading_logo"
        else "Bushido Blade (Japan) [KO Ending]"
    )
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    output_file = args.output_dir / "O15.patched.DAT"
    report_path = args.output_dir / "build_report.json"
    output_file.write_bytes(patched_file)
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
    if forms != {"form1"} or reparsed_file != bytes(patched_file):
        raise ValueError(f"patched {TARGET_PATH} did not survive disc reinsertion")
    for target_report in target_reports:
        reparsed = timlib.parse_tim(reparsed_file, target_report["tim_offset"])
        if reparsed is None:
            raise ValueError(
                "patched ending TIM no longer validates at "
                f"{target_report['tim_offset_hex']}"
            )
    if output_bin.stat().st_size != args.image.stat().st_size:
        raise ValueError("patched BIN size changed")

    report = {
        "gate_id": "BBJ-BUILD-GFX-END-001",
        "status": "static_build_passed_runtime_pending",
        "baseline": {
            "kind": baseline_kind,
            "image": str(args.image),
            "sha256": input_hash,
            "size": args.image.stat().st_size,
        },
        "target": {
            "disc_path": TARGET_PATH,
            "file_lba": entry["lba"],
            "file_size": entry["size"],
            "file_original_sha256": EXPECTED_O15_SHA256,
            "tim_count": len(TARGETS),
            "targets": target_reports,
        },
        "expected_write": {
            "owner": "BBJ-BUILD-GFX-END-001",
            "changed_file_byte_count": len(changed_file_offsets),
            "changed_file_ranges": [
                {"start": start, "end": end}
                for start, end in shared.contiguous_ranges(changed_file_offsets)
            ],
            "touched_sectors": touched_sectors,
        },
        "output": {
            "bin": str(output_bin),
            "bin_sha256": shared.sha256_file(output_bin),
            "bin_size": output_bin.stat().st_size,
            "cue": str(output_cue),
            "patched_file": str(output_file),
            "patched_file_sha256": shared.sha256_bytes(bytes(patched_file)),
        },
        "static_checks": {
            "supported_input_hash": True,
            "o15_source_hash": True,
            "all_six_pngs_present": True,
            "dimensions_match": True,
            "all_pngs_opaque": True,
            "original_cluts_preserved": True,
            "tim_headers_preserved": True,
            "tim_sizes_preserved": True,
            "file_size_preserved": len(patched_file) == len(original_file),
            "disc_size_preserved": output_bin.stat().st_size
            == args.image.stat().st_size,
            "raw_diff_within_expected_writes": True,
            "mode2_form1_integrity": True,
            "disc_reparse": True,
        },
        "runtime": {
            "status": "pending",
            "required_screen": "스토리 모드 완료 후 세로 엔딩 문구와 서명",
            "pass": "한글 엔딩 문구가 정상 표시되고 진행·복귀가 정상",
            "fail": "일본어가 남거나 글자가 깨지고 진행이 멈춤",
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"output_bin={output_bin}")
    print(f"output_cue={output_cue}")
    print(f"report={report_path}")
    print(f"changed_file_bytes={len(changed_file_offsets)}")
    print(f"touched_sectors={len(touched_sectors)}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
