#!/usr/bin/env python3
"""Build the integrated Bushido Blade Korean baked-graphics disc image."""

from __future__ import annotations

import argparse
import json
import shutil
import struct
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

import build_ending_patch as ending
import build_options_poc as shared
import build_title_poc as title
import inspect_psx_disc as disc
import scan_psx_tim as timlib


EXPECTED_BIN_SHA256 = (
    "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
)
LOADING_PATH = "SLPM_860.20"
LOADING_OFFSET = 0x7657C
OPTIONS_PATH = "DATA3/O04.DAT"
OPTIONS_OFFSET = 0x3CCC
TITLE_PATH = "DATA3/O12.DAT"
TITLE_OFFSET = 0x1CC
ENDING_PATH = "DATA3/O15.DAT"


def image_rgba(path: Path) -> Image.Image:
    image = Image.open(path)
    image.load()
    return image.convert("RGBA")


def map_to_original_palette(
    image: Image.Image,
    palette: list[int],
    original_indices: list[int],
) -> tuple[list[int], Image.Image, dict]:
    palette_rgba = [timlib.psx_color(value) for value in palette]
    counts = Counter(original_indices)
    transparent_candidates = [
        index for index, color in enumerate(palette_rgba) if color[3] == 0
    ]
    transparent_index = (
        max(transparent_candidates, key=lambda index: counts[index])
        if transparent_candidates
        else None
    )
    opaque_candidates = [
        index for index, color in enumerate(palette_rgba) if color[3] != 0
    ]
    if not opaque_candidates:
        raise ValueError("target palette has no visible entries")

    indices = []
    for r, g, b, a in image.get_flattened_data():
        if a < 128 and transparent_index is not None:
            indices.append(transparent_index)
            continue
        indices.append(
            min(
                opaque_candidates,
                key=lambda index: (
                    (r - palette_rgba[index][0]) ** 2
                    + (g - palette_rgba[index][1]) ** 2
                    + (b - palette_rgba[index][2]) ** 2
                ),
            )
        )

    preview = Image.new("RGBA", image.size)
    preview.putdata([palette_rgba[index] for index in indices])
    return indices, preview, {
        "method": "nearest original PS1 CLUT entry, alpha below 128 maps to transparent",
        "transparent_index": transparent_index,
        "used_indices": sorted(set(indices)),
    }


def validate_tim_roundtrip(data: bytes, parsed: dict, label: str) -> list[int]:
    if parsed["bpp"] != 4:
        raise ValueError(f"{label}: only 4bpp TIM is supported")
    indices = shared.decode_4bpp_indices(data, parsed)
    meta = parsed["image"]
    payload = data[
        meta["payload_offset"] : meta["payload_offset"] + meta["payload_size"]
    ]
    if shared.encode_4bpp_indices(indices) != payload:
        raise ValueError(f"{label}: original 4bpp roundtrip failed")
    return indices


def patch_png_payload(
    original_file: bytes,
    patched_file: bytearray,
    offset: int,
    png_path: Path,
    preview_path: Path,
    expected_size: tuple[int, int] | None = None,
) -> tuple[dict, tuple[int, int]]:
    parsed = timlib.parse_tim(original_file, offset)
    if parsed is None:
        raise ValueError(f"TIM did not validate at 0x{offset:08X}")
    size = (parsed["pixel_width"], parsed["pixel_height"])
    if expected_size is not None and size != expected_size:
        raise ValueError(
            f"{png_path.name}: TIM size {size} differs from expected {expected_size}"
        )
    edited = image_rgba(png_path)
    if edited.size != size:
        raise ValueError(f"{png_path.name}: expected {size}, got {edited.size}")

    original_indices = validate_tim_roundtrip(
        original_file, parsed, f"{png_path.name}@0x{offset:08X}"
    )
    palette = shared.palette_values(original_file, parsed)
    indices, preview, mapping = map_to_original_palette(
        edited, palette, original_indices
    )
    payload = shared.encode_4bpp_indices(indices)
    meta = parsed["image"]
    start = meta["payload_offset"]
    end = start + meta["payload_size"]
    if len(payload) != meta["payload_size"]:
        raise ValueError(f"{png_path.name}: encoded payload size changed")
    patched_file[start:end] = payload
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path)
    return (
        {
            "tim_offset": offset,
            "tim_offset_hex": f"0x{offset:08X}",
            "edited_png": str(png_path),
            "edited_png_sha256": shared.sha256_file(png_path),
            "pixel_width": size[0],
            "pixel_height": size[1],
            "tim_total_size": parsed["total_size"],
            "image_payload_range": {"start": start, "end": end},
            "pixel_indices_changed": sum(
                before != after
                for before, after in zip(original_indices, indices)
            ),
            "mapping": mapping,
            "preview": str(preview_path),
        },
        (start, end),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--ui-catalog", required=True, type=Path)
    parser.add_argument("--title-png", required=True, type=Path)
    parser.add_argument("--loading-png", required=True, type=Path)
    parser.add_argument("--ending-dir", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    original_hash = shared.sha256_file(args.image)
    if original_hash != EXPECTED_BIN_SHA256:
        raise ValueError(
            f"unsupported BIN SHA-256: {original_hash}, expected {EXPECTED_BIN_SHA256}"
        )
    if shared.sha256_file(args.font) != shared.EXPECTED_FONT_SHA256:
        raise ValueError("options-header font SHA-256 mismatch")

    project_root = Path.cwd().resolve()
    ui_catalog = json.loads(args.ui_catalog.read_text(encoding="utf-8"))
    inspection = disc.inspect(args.image)
    entries = {
        item["path"]: item
        for item in inspection["entries"]
        if not item["is_dir"]
    }
    target_paths = {
        item["source_path"] for item in ui_catalog["items"]
    } | {LOADING_PATH, OPTIONS_PATH, TITLE_PATH, ENDING_PATH}
    missing_paths = sorted(target_paths - entries.keys())
    if missing_paths:
        raise ValueError(f"disc files not found: {missing_paths}")

    original_files: dict[str, bytes] = {}
    patched_files: dict[str, bytearray] = {}
    with args.image.open("rb") as source:
        for path in sorted(target_paths):
            entry = entries[path]
            if entry["sector_forms"] != ["form1"]:
                raise ValueError(f"{path} is not entirely Mode 2 Form 1")
            data, forms = disc.read_extent(source, entry["lba"], entry["size"])
            if forms != {"form1"}:
                raise ValueError(f"{path}: unexpected sector forms {forms}")
            original_files[path] = data
            patched_files[path] = bytearray(data)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    preview_root = args.output_dir / "previews"
    target_reports: list[dict] = []
    allowed_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)

    # User-edited remaining UI batch. The unchanged question-mark placeholder is
    # explicitly preserved and reported instead of being rewritten.
    for item in ui_catalog["items"]:
        source_png = project_root / item["source_png"]
        edited_png = project_root / item["edited_png"]
        if not source_png.is_file() or not edited_png.is_file():
            raise ValueError(f"UI batch file missing for {item['item_id']}")
        path = item["source_path"]
        offset = item["offset"]
        parsed = timlib.parse_tim(original_files[path], offset)
        if parsed is None:
            raise ValueError(f"{item['item_id']}: original TIM did not validate")
        expected = (
            item["bpp"],
            item["width"],
            item["height"],
            item["tim_total_size"],
        )
        actual = (
            parsed["bpp"],
            parsed["pixel_width"],
            parsed["pixel_height"],
            parsed["total_size"],
        )
        if actual != expected:
            raise ValueError(
                f"{item['item_id']}: TIM properties {actual} != {expected}"
            )
        decoded = timlib.decode_tim(original_files[path], parsed).convert("RGBA")
        if decoded.tobytes() != image_rgba(source_png).tobytes():
            raise ValueError(
                f"{item['item_id']}: source PNG does not match original TIM pixels"
            )
        changed = shared.sha256_file(source_png) != shared.sha256_file(edited_png)
        if not changed:
            target_reports.append(
                {
                    "owner": item["item_id"],
                    "disc_path": path,
                    "tim_offset": offset,
                    "tim_offset_hex": item["offset_hex"],
                    "status": "unchanged_preserved",
                    "edited_png": str(edited_png),
                }
            )
            continue
        report, allowed = patch_png_payload(
            original_files[path],
            patched_files[path],
            offset,
            edited_png,
            preview_root / item["group"] / edited_png.name,
            (item["width"], item["height"]),
        )
        report.update(
            {
                "owner": item["item_id"],
                "disc_path": path,
                "status": "patched",
            }
        )
        target_reports.append(report)
        allowed_ranges[path].append(allowed)

    # Verified Korean options header, rebuilt deterministically from the pinned font.
    options_original = original_files[OPTIONS_PATH]
    options_target = timlib.parse_tim(options_original, OPTIONS_OFFSET)
    if options_target is None:
        raise ValueError("options header TIM did not validate")
    options_original_indices = validate_tim_roundtrip(
        options_original, options_target, "options header"
    )
    options_palette = shared.palette_values(options_original, options_target)
    options_indices, options_render = shared.render_hangul_indices(
        options_target["pixel_width"],
        options_target["pixel_height"],
        options_palette,
        options_original_indices,
        args.font,
        16,
    )
    options_meta = options_target["image"]
    options_start = options_meta["payload_offset"]
    options_end = options_start + options_meta["payload_size"]
    patched_files[OPTIONS_PATH][options_start:options_end] = (
        shared.encode_4bpp_indices(options_indices)
    )
    options_preview = preview_root / "completed" / "options_header.png"
    options_preview.parent.mkdir(parents=True, exist_ok=True)
    options_pixels = [
        timlib.psx_color(options_palette[index]) for index in options_indices
    ]
    options_image = Image.new(
        "RGBA",
        (options_target["pixel_width"], options_target["pixel_height"]),
    )
    options_image.putdata(options_pixels)
    options_image.save(options_preview)
    allowed_ranges[OPTIONS_PATH].append((options_start, options_end))
    target_reports.append(
        {
            "owner": "BBJ-POC-GFX-001",
            "disc_path": OPTIONS_PATH,
            "tim_offset": OPTIONS_OFFSET,
            "tim_offset_hex": f"0x{OPTIONS_OFFSET:08X}",
            "status": "patched",
            "image_payload_range": {"start": options_start, "end": options_end},
            "render": options_render,
            "preview": str(options_preview),
        }
    )

    # Verified title path. This asset owns both its CLUT and image payload.
    title_original = original_files[TITLE_PATH]
    title_target = timlib.parse_tim(title_original, TITLE_OFFSET)
    if title_target is None:
        raise ValueError("title TIM did not validate")
    if (
        title_target["bpp"],
        title_target["pixel_width"],
        title_target["pixel_height"],
        title_target["total_size"],
    ) != title.TARGET_PROPERTIES:
        raise ValueError("title TIM properties changed")
    title_image = image_rgba(args.title_png)
    title_indices, title_palette, title_preview_image = title.quantize_for_psx(
        title_image
    )
    title_clut = title_target["clut"]
    title_meta = title_target["image"]
    if title_clut is None or title_clut["payload_size"] != 32:
        raise ValueError("title CLUT properties changed")
    title_clut_start = title_clut["payload_offset"]
    title_clut_end = title_clut_start + title_clut["payload_size"]
    title_start = title_meta["payload_offset"]
    title_end = title_start + title_meta["payload_size"]
    patched_files[TITLE_PATH][title_clut_start:title_clut_end] = b"".join(
        struct.pack("<H", value) for value in title_palette
    )
    patched_files[TITLE_PATH][title_start:title_end] = (
        shared.encode_4bpp_indices(title_indices)
    )
    title_preview = preview_root / "completed" / "title.png"
    title_preview_image.save(title_preview)
    allowed_ranges[TITLE_PATH].extend(
        [(title_clut_start, title_clut_end), (title_start, title_end)]
    )
    target_reports.append(
        {
            "owner": "BBJ-POC-GFX-002",
            "disc_path": TITLE_PATH,
            "tim_offset": TITLE_OFFSET,
            "tim_offset_hex": f"0x{TITLE_OFFSET:08X}",
            "status": "patched",
            "edited_png": str(args.title_png),
            "clut_payload_range": {
                "start": title_clut_start,
                "end": title_clut_end,
            },
            "image_payload_range": {"start": title_start, "end": title_end},
            "preview": str(title_preview),
        }
    )

    # Runtime-linked loading logo.
    loading_report, loading_allowed = patch_png_payload(
        original_files[LOADING_PATH],
        patched_files[LOADING_PATH],
        LOADING_OFFSET,
        args.loading_png,
        preview_root / "completed" / "loading_logo.png",
        (136, 32),
    )
    loading_report.update(
        {
            "owner": "BBJ-BUILD-GFX-LOAD-001",
            "disc_path": LOADING_PATH,
            "status": "patched",
        }
    )
    target_reports.append(loading_report)
    allowed_ranges[LOADING_PATH].append(loading_allowed)

    # Six verified ending assets.
    for filename, offset, width, height, total_size in ending.TARGETS:
        report, allowed = patch_png_payload(
            original_files[ENDING_PATH],
            patched_files[ENDING_PATH],
            offset,
            args.ending_dir / filename,
            preview_root / "completed" / "ending" / filename,
            (width, height),
        )
        if report["tim_total_size"] != total_size:
            raise ValueError(f"{filename}: ending TIM total size changed")
        report.update(
            {
                "owner": f"BBJ-END-{offset:08X}",
                "disc_path": ENDING_PATH,
                "status": "patched",
            }
        )
        target_reports.append(report)
        allowed_ranges[ENDING_PATH].append(allowed)

    # Validate file-level Expected Writes before touching the output track.
    file_reports = []
    changes_by_file_sector: dict[str, dict[int, list[int]]] = {}
    for path in sorted(target_paths):
        original_file = original_files[path]
        patched_file = patched_files[path]
        changed_offsets = [
            offset
            for offset, (before, after) in enumerate(
                zip(original_file, patched_file)
            )
            if before != after
        ]
        if not changed_offsets:
            continue
        if any(
            not any(start <= offset < end for start, end in allowed_ranges[path])
            for offset in changed_offsets
        ):
            raise ValueError(f"{path}: diff escaped declared TIM payloads")
        by_sector: dict[int, list[int]] = defaultdict(list)
        for offset in changed_offsets:
            by_sector[offset // disc.LOGICAL_SECTOR_SIZE].append(offset)
        changes_by_file_sector[path] = dict(by_sector)
        patched_output = args.output_dir / "patched_files" / path
        patched_output.parent.mkdir(parents=True, exist_ok=True)
        patched_output.write_bytes(patched_file)
        file_reports.append(
            {
                "disc_path": path,
                "original_sha256": shared.sha256_bytes(original_file),
                "patched_sha256": shared.sha256_bytes(bytes(patched_file)),
                "size": len(patched_file),
                "changed_byte_count": len(changed_offsets),
                "changed_ranges": [
                    {"start": start, "end": end}
                    for start, end in shared.contiguous_ranges(changed_offsets)
                ],
                "allowed_ranges": [
                    {"start": start, "end": end}
                    for start, end in allowed_ranges[path]
                ],
                "patched_file": str(patched_output),
            }
        )

    base_name = "Bushido Blade (Japan) [Korean Graphics]"
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    shutil.copyfile(args.image, output_bin)

    touched_sectors = []
    touched_lbas: set[int] = set()
    with args.image.open("rb") as original_track, output_bin.open("r+b") as output_track:
        for path in sorted(changes_by_file_sector):
            entry = entries[path]
            patched_file = patched_files[path]
            for relative_sector, offsets in sorted(
                changes_by_file_sector[path].items()
            ):
                lba = entry["lba"] + relative_sector
                if lba in touched_lbas:
                    raise ValueError(f"overlapping file writes at LBA {lba}")
                touched_lbas.add(lba)
                raw_offset = lba * disc.RAW_SECTOR_SIZE
                original_track.seek(raw_offset)
                original_sector = original_track.read(disc.RAW_SECTOR_SIZE)
                if (
                    shared.rebuild_mode2_form1_integrity(original_sector)
                    != original_sector
                ):
                    raise ValueError(f"original EDC/ECC mismatch at LBA {lba}")
                patched_sector = bytearray(original_sector)
                allowed_data_local = set()
                for file_offset in offsets:
                    in_sector = file_offset % disc.LOGICAL_SECTOR_SIZE
                    local = disc.MODE2_FORM1_DATA_OFFSET + in_sector
                    patched_sector[local] = patched_file[file_offset]
                    allowed_data_local.add(local)
                rebuilt = shared.rebuild_mode2_form1_integrity(
                    bytes(patched_sector)
                )
                if shared.rebuild_mode2_form1_integrity(rebuilt) != rebuilt:
                    raise ValueError(f"patched EDC/ECC failed at LBA {lba}")
                changed_local = [
                    offset
                    for offset, (before, after) in enumerate(
                        zip(original_sector, rebuilt)
                    )
                    if before != after
                ]
                if any(
                    offset not in allowed_data_local
                    and not 0x818 <= offset < 0x930
                    for offset in changed_local
                ):
                    raise ValueError(
                        f"{path}: raw diff escaped expected ranges at LBA {lba}"
                    )
                output_track.seek(raw_offset)
                output_track.write(rebuilt)
                touched_sectors.append(
                    {
                        "disc_path": path,
                        "lba": lba,
                        "raw_offset": raw_offset,
                        "original_sha256": shared.sha256_bytes(original_sector),
                        "patched_sha256": shared.sha256_bytes(rebuilt),
                        "changed_local_ranges": [
                            {"start": start, "end": end}
                            for start, end in shared.contiguous_ranges(
                                changed_local
                            )
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

    # Reparse every patched file and every target TIM from the final track.
    with output_bin.open("rb") as patched_track:
        for file_report in file_reports:
            path = file_report["disc_path"]
            entry = entries[path]
            reparsed_file, forms = disc.read_extent(
                patched_track, entry["lba"], entry["size"]
            )
            if forms != {"form1"}:
                raise ValueError(f"{path}: final sector forms changed")
            if reparsed_file != bytes(patched_files[path]):
                raise ValueError(f"{path}: final disc reparse mismatch")
    for target_report in target_reports:
        if target_report["status"] != "patched":
            continue
        path = target_report["disc_path"]
        parsed = timlib.parse_tim(
            bytes(patched_files[path]), target_report["tim_offset"]
        )
        if parsed is None:
            raise ValueError(
                f"{path}@{target_report['tim_offset_hex']}: final TIM invalid"
            )
    if output_bin.stat().st_size != args.image.stat().st_size:
        raise ValueError("output BIN size changed")

    report = {
        "gate_id": "BBJ-BUILD-GFX-FULL-001",
        "status": "static_build_passed_runtime_pending",
        "baseline": {
            "image": str(args.image),
            "sha256": original_hash,
            "size": args.image.stat().st_size,
        },
        "inputs": {
            "ui_catalog": str(args.ui_catalog),
            "ui_catalog_sha256": shared.sha256_file(args.ui_catalog),
            "title_png": str(args.title_png),
            "title_png_sha256": shared.sha256_file(args.title_png),
            "loading_png": str(args.loading_png),
            "loading_png_sha256": shared.sha256_file(args.loading_png),
            "ending_dir": str(args.ending_dir),
            "font": str(args.font),
            "font_sha256": shared.sha256_file(args.font),
        },
        "population": {
            "ui_items_total": len(ui_catalog["items"]),
            "ui_items_patched": sum(
                item["owner"].startswith("BBJ-UI-")
                and item["status"] == "patched"
                for item in target_reports
            ),
            "ui_items_unchanged_preserved": sum(
                item["owner"].startswith("BBJ-UI-")
                and item["status"] == "unchanged_preserved"
                for item in target_reports
            ),
            "total_tim_targets": len(target_reports),
        },
        "targets": target_reports,
        "expected_write": {
            "owner": "BBJ-BUILD-GFX-FULL-001",
            "files": file_reports,
            "touched_sector_count": len(touched_sectors),
            "touched_sectors": touched_sectors,
        },
        "output": {
            "bin": str(output_bin),
            "bin_sha256": shared.sha256_file(output_bin),
            "bin_size": output_bin.stat().st_size,
            "cue": str(output_cue),
            "preview_root": str(preview_root),
        },
        "static_checks": {
            "supported_input_hash": True,
            "source_pngs_match_original_tim_pixels": True,
            "all_png_dimensions_match": True,
            "original_cluts_preserved_except_title": True,
            "tim_headers_and_sizes_preserved": True,
            "file_sizes_preserved": True,
            "disc_size_preserved": True,
            "diffs_within_expected_writes": True,
            "mode2_form1_integrity": True,
            "disc_reparse": True,
            "final_tim_reparse": True,
        },
        "runtime": {
            "status": "pending",
            "required_observation": [
                "boot and title/menu re-entry",
                "all main-menu selections",
                "character, weapon, and arena selections",
                "options and memory-card messages",
                "versus/stage settings",
                "character profile labels",
                "loading logo and ending",
            ],
        },
    }
    report_path = args.output_dir / "build_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"output_bin={output_bin}")
    print(f"output_cue={output_cue}")
    print(f"report={report_path}")
    print(f"ui_patched={report['population']['ui_items_patched']}")
    print(
        "ui_unchanged_preserved="
        f"{report['population']['ui_items_unchanged_preserved']}"
    )
    print(f"total_tim_targets={report['population']['total_tim_targets']}")
    print(f"touched_sectors={len(touched_sectors)}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
