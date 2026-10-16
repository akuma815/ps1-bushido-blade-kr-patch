#!/usr/bin/env python3
"""Build palette or pixel probes for the suspected loading-screen logo TIM."""

from __future__ import annotations

import argparse
import json
import shutil
import struct
from pathlib import Path

from PIL import Image

import build_options_poc as shared
import inspect_psx_disc as disc
import scan_psx_tim as timlib


EXPECTED_BIN_SHA256 = (
    "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
)
EXPECTED_O01_SHA256 = (
    "DC39D39BF3CA258D9CF388962A63C4C06BC9C2B0E183B854B23131F014C6C0F3"
)
TARGET_PATH = "DATA3/O01.DAT"
TARGET_TIM_OFFSET = 0x7CB18
TARGET_PROPERTIES = (4, 552, 144, 39_808)

# Deliberately unnatural colors. The pixel indices remain byte-identical, so a
# changed loading logo proves that this exact TIM is consumed at runtime.
PROBE_PALETTE = [
    0xFC1F, 0x83FF, 0xFFE0, 0x801F,
    0x83E0, 0xFC00, 0xFFFF, 0x8210,
    0xFD5F, 0x97FF, 0xFFE7, 0xA11F,
    0x83EA, 0xE400, 0xE739, 0x8000,
]


def render_preview(data: bytes, parsed: dict, palette: list[int]) -> Image.Image:
    indices = shared.decode_4bpp_indices(data, parsed)
    pixels = [timlib.psx_color(palette[index]) for index in indices]
    image = Image.new(
        "RGBA", (parsed["pixel_width"], parsed["pixel_height"])
    )
    image.putdata(pixels)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--probe-mode",
        choices=("palette", "pixels"),
        default="palette",
        help="palette changes only the TIM CLUT; pixels inverts every 4bpp index",
    )
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
    if entry["sha256"].upper() != EXPECTED_O01_SHA256:
        raise ValueError("DATA3/O01.DAT source hash mismatch")
    if entry["sector_forms"] != ["form1"]:
        raise ValueError("DATA3/O01.DAT is not entirely Form 1")

    with args.image.open("rb") as source:
        original_dat, _ = disc.read_extent(source, entry["lba"], entry["size"])
    target = timlib.parse_tim(original_dat, TARGET_TIM_OFFSET)
    if target is None:
        raise ValueError("loading-logo candidate TIM did not validate")
    properties = (
        target["bpp"],
        target["pixel_width"],
        target["pixel_height"],
        target["total_size"],
    )
    if properties != TARGET_PROPERTIES:
        raise ValueError(f"loading-logo TIM properties changed: {properties}")

    clut = target["clut"]
    if clut is None or clut["payload_size"] != 32:
        raise ValueError("loading-logo TIM does not have one 16-entry CLUT")
    image_meta = target["image"]
    original_indices = shared.decode_4bpp_indices(original_dat, target)
    original_image_payload = original_dat[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    if shared.encode_4bpp_indices(original_indices) != original_image_payload:
        raise ValueError("loading-logo TIM failed byte-identical 4bpp roundtrip")

    original_palette = shared.palette_values(original_dat, target)
    patched_dat = bytearray(original_dat)
    if args.probe_mode == "palette":
        probe_indices = original_indices
        probe_palette = PROBE_PALETTE
        allowed_range = (
            clut["payload_offset"],
            clut["payload_offset"] + clut["payload_size"],
        )
        patched_dat[allowed_range[0] : allowed_range[1]] = b"".join(
            struct.pack("<H", value) for value in probe_palette
        )
    else:
        probe_indices = [15 - index for index in original_indices]
        probe_palette = original_palette
        allowed_range = (
            image_meta["payload_offset"],
            image_meta["payload_offset"] + image_meta["payload_size"],
        )
        patched_dat[allowed_range[0] : allowed_range[1]] = (
            shared.encode_4bpp_indices(probe_indices)
        )
    changed_dat_offsets = [
        offset
        for offset, (before, after) in enumerate(zip(original_dat, patched_dat))
        if before != after
    ]
    if not changed_dat_offsets or any(
        not allowed_range[0] <= offset < allowed_range[1]
        for offset in changed_dat_offsets
    ):
        raise ValueError(f"DAT diff escaped the target TIM {args.probe_mode} payload")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    label = "Palette" if args.probe_mode == "palette" else "Pixel"
    base_name = f"Bushido Blade (Japan) [Loading Logo {label} Probe]"
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    output_dat = args.output_dir / "O01.probe.DAT"
    preview_png = args.output_dir / "loading_logo_probe_preview.png"
    report_path = args.output_dir / "build_report.json"

    output_dat.write_bytes(patched_dat)
    render_preview(bytes(patched_dat), target, probe_palette).save(preview_png)
    shutil.copyfile(args.image, output_bin)

    changes_by_sector: dict[int, list[int]] = {}
    for file_offset in changed_dat_offsets:
        changes_by_sector.setdefault(
            file_offset // disc.LOGICAL_SECTOR_SIZE, []
        ).append(file_offset)

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
    reparsed = timlib.parse_tim(reparsed_dat, TARGET_TIM_OFFSET)
    if forms != {"form1"} or reparsed_dat != bytes(patched_dat):
        raise ValueError("patched O01.DAT did not survive disc reinsertion")
    if reparsed is None:
        raise ValueError("patched loading-logo TIM no longer validates")
    if shared.decode_4bpp_indices(reparsed_dat, reparsed) != probe_indices:
        raise ValueError("probe pixel indices differ after disc reinsertion")
    if shared.palette_values(reparsed_dat, reparsed) != probe_palette:
        raise ValueError("probe CLUT differs after disc reinsertion")
    if output_bin.stat().st_size != args.image.stat().st_size:
        raise ValueError("patched BIN size changed")

    report = {
        "gate_id": "BBJ-POC-GFX-003",
        "experiment_id": (
            "BBJ-POC-GFX-003-CLUT"
            if args.probe_mode == "palette"
            else "BBJ-POC-GFX-003-PIXELS"
        ),
        "status": "static_build_passed_runtime_pending",
        "purpose": (
            "Test the internal CLUT reachability of DATA3/O01.DAT @ 0x7CB18."
            if args.probe_mode == "palette"
            else "Test the pixel-index reachability of DATA3/O01.DAT @ 0x7CB18."
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
            "file_original_sha256": EXPECTED_O01_SHA256,
            "tim_offset": TARGET_TIM_OFFSET,
            "tim_total_size": target["total_size"],
            "format": "TIM 4bpp indexed",
            "pixel_width": target["pixel_width"],
            "pixel_height": target["pixel_height"],
        },
        "probe": {
            "mode": args.probe_mode,
            "method": (
                "palette-only diagnostic replacement"
                if args.probe_mode == "palette"
                else "invert every 4bpp pixel index while preserving the CLUT"
            ),
            "pixel_indices_preserved": args.probe_mode == "palette",
            "clut_preserved": args.probe_mode == "pixels",
            "original_palette_psx_bgr555": [
                f"0x{value:04X}" for value in original_palette
            ],
            "output_palette_psx_bgr555": [
                f"0x{value:04X}" for value in probe_palette
            ],
        },
        "expected_write": {
            "owner": "BBJ-POC-GFX-003",
            "dat_allowed_ranges": [
                {"start": allowed_range[0], "end": allowed_range[1]}
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
        },
        "static_checks": {
            "source_hash": True,
            "tim_roundtrip": True,
            "pixel_indices_match_probe": True,
            "clut_matches_probe": True,
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
            "required_screen": "게임 시작 직후 로딩 화면",
            "required_observation": (
                "우하단 로고가 진단색으로 바뀌는지 확인"
                if args.probe_mode == "palette"
                else "우하단 로고의 명암과 배경이 반전되는지 확인"
            ),
            "pass": (
                "로딩 로고만 진단색으로 바뀌고 타이틀/메뉴 진입은 정상"
                if args.probe_mode == "palette"
                else "로딩 로고의 명암과 배경이 반전되고 타이틀/메뉴 진입은 정상"
            ),
            "fail": "로딩 로고가 원래 화면 그대로 유지",
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
    print(f"changed_dat_bytes={len(changed_dat_offsets)}")
    print(f"touched_sectors={len(touched_sector_records)}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
