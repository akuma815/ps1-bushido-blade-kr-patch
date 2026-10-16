#!/usr/bin/env python3
"""Build the Bushido Blade Japanese options-header Hangul visibility PoC."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import inspect_psx_disc as disc
import scan_psx_tim as timlib


EXPECTED_BIN_SHA256 = (
    "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
)
EXPECTED_O04_SHA256 = (
    "DDED78FBE71C06711EA2F35E8F29D134DF18A21A1CF3FE6BCD0F0C1F62B5EE91"
)
EXPECTED_FONT_SHA256 = (
    "7B433B4A007C36DFB535FDEA11DE3E4F4C8B641AB591ED05D3BC0A4BBD75EB5F"
)
TARGET_PATH = "DATA3/O04.DAT"
TARGET_TIM_OFFSET = 0x3CCC
TARGET_TEXT = "옵션"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_edc_lut() -> list[int]:
    table = []
    for value in range(256):
        edc = value
        for _ in range(8):
            edc = (edc >> 1) ^ (0xD8018001 if edc & 1 else 0)
        table.append(edc)
    return table


def build_ecc_luts() -> tuple[list[int], list[int]]:
    forward = [0] * 256
    backward = [0] * 256
    for value in range(256):
        shifted = value << 1
        if value & 0x80:
            shifted ^= 0x11D
        shifted &= 0xFF
        forward[value] = shifted
        backward[value ^ shifted] = value
    return forward, backward


EDC_LUT = build_edc_lut()
ECC_F_LUT, ECC_B_LUT = build_ecc_luts()


def compute_edc(source: bytes) -> int:
    edc = 0
    for value in source:
        edc = (edc >> 8) ^ EDC_LUT[(edc ^ value) & 0xFF]
    return edc


def compute_ecc(
    source: bytes,
    major_count: int,
    minor_count: int,
    major_mult: int,
    minor_inc: int,
) -> bytes:
    size = major_count * minor_count
    if len(source) < size:
        raise ValueError("ECC source is shorter than the requested matrix")
    output = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            value = source[index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= value
            ecc_b ^= value
            ecc_a = ECC_F_LUT[ecc_a]
        ecc_a = ECC_B_LUT[ECC_F_LUT[ecc_a] ^ ecc_b]
        output[major] = ecc_a
        output[major + major_count] = ecc_a ^ ecc_b
    return bytes(output)


def rebuild_mode2_form1_integrity(raw_sector: bytes) -> bytes:
    if len(raw_sector) != disc.RAW_SECTOR_SIZE:
        raise ValueError("raw sector size mismatch")
    if raw_sector[:12] != b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00":
        raise ValueError("raw sector sync mismatch")
    if raw_sector[15] != 2:
        raise ValueError("target sector is not Mode 2")
    if raw_sector[16:20] != raw_sector[20:24]:
        raise ValueError("Mode 2 duplicated subheaders disagree")
    if raw_sector[18] & 0x20:
        raise ValueError("target sector is Form 2, expected Form 1")

    result = bytearray(raw_sector)
    result[0x818:0x81C] = struct.pack(
        "<I", compute_edc(bytes(result[0x10:0x818]))
    )
    saved_header = bytes(result[0x0C:0x10])
    result[0x0C:0x10] = b"\x00" * 4
    result[0x81C:0x8C8] = compute_ecc(
        bytes(result[0x0C:0x81C]), 86, 24, 2, 86
    )
    result[0x8C8:0x930] = compute_ecc(
        bytes(result[0x0C:0x8C8]), 52, 43, 86, 88
    )
    result[0x0C:0x10] = saved_header
    return bytes(result)


def palette_values(data: bytes, tim: dict) -> list[int]:
    clut = tim["clut"]
    if clut is None:
        raise ValueError("4bpp target has no CLUT")
    raw = data[
        clut["payload_offset"] : clut["payload_offset"] + clut["payload_size"]
    ]
    return [struct.unpack_from("<H", raw, offset)[0] for offset in range(0, 32, 2)]


def decode_4bpp_indices(data: bytes, tim: dict) -> list[int]:
    image = tim["image"]
    payload = data[
        image["payload_offset"] : image["payload_offset"] + image["payload_size"]
    ]
    indices = []
    for value in payload:
        indices.extend((value & 0x0F, (value >> 4) & 0x0F))
    expected = tim["pixel_width"] * tim["pixel_height"]
    if len(indices) != expected:
        raise ValueError("decoded 4bpp pixel count mismatch")
    return indices


def encode_4bpp_indices(indices: list[int]) -> bytes:
    if len(indices) % 2:
        raise ValueError("4bpp index count must be even")
    output = bytearray()
    for offset in range(0, len(indices), 2):
        low = indices[offset]
        high = indices[offset + 1]
        if not 0 <= low < 16 or not 0 <= high < 16:
            raise ValueError("palette index outside 4bpp range")
        output.append(low | (high << 4))
    return bytes(output)


def color_luminance(value: int) -> int:
    r = value & 0x1F
    g = (value >> 5) & 0x1F
    b = (value >> 10) & 0x1F
    return r * 299 + g * 587 + b * 114


def render_hangul_indices(
    width: int,
    height: int,
    palette: list[int],
    original_indices: list[int],
    font_path: Path,
    font_size: int,
) -> tuple[list[int], dict]:
    counts = Counter(original_indices)
    transparent_candidates = [
        index for index, value in enumerate(palette) if value & 0x7FFF == 0
    ]
    if not transparent_candidates:
        raise ValueError("target palette has no transparent black entry")
    transparent = max(transparent_candidates, key=lambda index: counts[index])
    used = [
        index
        for index, count in counts.items()
        if count and index != transparent and palette[index] & 0x7FFF
    ]
    if len(used) < 2:
        raise ValueError("target text does not expose enough palette roles")
    used.sort(key=lambda index: color_luminance(palette[index]))
    outline_index = used[0]
    fill_index = used[-1]
    middle_index = used[len(used) // 2]

    font = ImageFont.truetype(str(font_path), font_size)
    fill_mask = Image.new("L", (width, height), 0)
    outline_mask = Image.new("L", (width, height), 0)
    fill_draw = ImageDraw.Draw(fill_mask)
    outline_draw = ImageDraw.Draw(outline_mask)
    bounds = outline_draw.textbbox((0, 0), TARGET_TEXT, font=font, stroke_width=1)
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    x = (width - text_width) // 2 - bounds[0]
    y = (height - text_height) // 2 - bounds[1]
    outline_draw.text(
        (x, y),
        TARGET_TEXT,
        font=font,
        fill=255,
        stroke_width=1,
        stroke_fill=255,
    )
    fill_draw.text((x, y), TARGET_TEXT, font=font, fill=255)

    result = [transparent] * (width * height)
    outline_values = list(outline_mask.get_flattened_data())
    fill_values = list(fill_mask.get_flattened_data())
    for index, coverage in enumerate(outline_values):
        if coverage >= 96:
            result[index] = outline_index
    for index, coverage in enumerate(fill_values):
        if coverage >= 192:
            result[index] = fill_index
        elif coverage >= 64:
            result[index] = middle_index

    nontransparent = [
        index for index, value in enumerate(result) if value != transparent
    ]
    if not nontransparent:
        raise ValueError("Hangul rendering produced no visible pixels")
    xs = [index % width for index in nontransparent]
    ys = [index // width for index in nontransparent]
    return result, {
        "text": TARGET_TEXT,
        "font_size": font_size,
        "text_bounds": {
            "x": min(xs),
            "y": min(ys),
            "w": max(xs) - min(xs) + 1,
            "h": max(ys) - min(ys) + 1,
        },
        "palette_roles": {
            "transparent": transparent,
            "outline": outline_index,
            "middle": middle_index,
            "fill": fill_index,
        },
        "original_palette_indices_used": sorted(used),
        "output_palette_indices_used": sorted(set(result)),
    }


def indices_to_image(indices: list[int], palette: list[int], width: int, height: int):
    image = Image.new("RGBA", (width, height))
    image.putdata([timlib.psx_color(palette[index]) for index in indices])
    return image


def contiguous_ranges(values: list[int]) -> list[tuple[int, int]]:
    if not values:
        return []
    ranges = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            ranges.append((start, previous + 1))
            start = value
        previous = value
    ranges.append((start, previous + 1))
    return ranges


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--font-size", type=int, default=16)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    original_hash = sha256_file(args.image)
    if original_hash != EXPECTED_BIN_SHA256:
        raise ValueError(
            f"unsupported BIN SHA-256: {original_hash}, expected {EXPECTED_BIN_SHA256}"
        )
    font_hash = sha256_file(args.font)
    if font_hash != EXPECTED_FONT_SHA256:
        raise ValueError(
            f"font SHA-256 mismatch: {font_hash}, expected {EXPECTED_FONT_SHA256}"
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
    if entry["sha256"].upper() != EXPECTED_O04_SHA256:
        raise ValueError("DATA3/O04.DAT source hash mismatch")
    if entry["sector_forms"] != ["form1"]:
        raise ValueError("DATA3/O04.DAT is not entirely Form 1")

    with args.image.open("rb") as source:
        original_dat, _ = disc.read_extent(source, entry["lba"], entry["size"])
    target = timlib.parse_tim(original_dat, TARGET_TIM_OFFSET)
    if target is None:
        raise ValueError("target TIM did not validate")
    if (
        target["bpp"],
        target["pixel_width"],
        target["pixel_height"],
        target["total_size"],
    ) != (4, 232, 16, 1920):
        raise ValueError("target TIM properties differ from the fixed Japanese baseline")

    original_indices = decode_4bpp_indices(original_dat, target)
    image_meta = target["image"]
    original_payload = original_dat[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    roundtrip_payload = encode_4bpp_indices(original_indices)
    if roundtrip_payload != original_payload:
        raise ValueError("4bpp decode/encode roundtrip is not byte-identical")

    palette = palette_values(original_dat, target)
    patched_indices, render_report = render_hangul_indices(
        target["pixel_width"],
        target["pixel_height"],
        palette,
        original_indices,
        args.font,
        args.font_size,
    )
    patched_payload = encode_4bpp_indices(patched_indices)
    if len(patched_payload) != len(original_payload):
        raise ValueError("patched TIM payload size changed")

    patched_dat = bytearray(original_dat)
    patched_dat[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ] = patched_payload
    changed_dat_offsets = [
        offset
        for offset, (before, after) in enumerate(zip(original_dat, patched_dat))
        if before != after
    ]
    allowed_start = image_meta["payload_offset"]
    allowed_end = allowed_start + image_meta["payload_size"]
    if not changed_dat_offsets or any(
        offset < allowed_start or offset >= allowed_end
        for offset in changed_dat_offsets
    ):
        raise ValueError("DAT diff escaped the target TIM image payload")

    base_name = "Bushido Blade (Japan) [KO Options PoC]"
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    output_dat = args.output_dir / "O04.patched.DAT"
    preview_png = args.output_dir / "options_ko_preview.png"
    preview_scaled_png = args.output_dir / "options_ko_preview_4x.png"
    report_path = args.output_dir / "build_report.json"

    output_dat.write_bytes(patched_dat)
    preview = indices_to_image(
        patched_indices,
        palette,
        target["pixel_width"],
        target["pixel_height"],
    )
    preview.save(preview_png)
    preview.resize(
        (preview.width * 4, preview.height * 4), Image.Resampling.NEAREST
    ).save(preview_scaled_png)

    shutil.copyfile(args.image, output_bin)
    target_file_start = image_meta["payload_offset"]
    target_file_end = target_file_start + image_meta["payload_size"]
    touched_sector_records = []
    with args.image.open("rb") as original_track, output_bin.open("r+b") as output_track:
        cursor = target_file_start
        while cursor < target_file_end:
            relative_sector = cursor // disc.LOGICAL_SECTOR_SIZE
            in_sector = cursor % disc.LOGICAL_SECTOR_SIZE
            take = min(
                target_file_end - cursor,
                disc.LOGICAL_SECTOR_SIZE - in_sector,
            )
            lba = entry["lba"] + relative_sector
            raw_offset = lba * disc.RAW_SECTOR_SIZE
            original_track.seek(raw_offset)
            original_sector = original_track.read(disc.RAW_SECTOR_SIZE)
            if rebuild_mode2_form1_integrity(original_sector) != original_sector:
                raise ValueError(f"original EDC/ECC mismatch at LBA {lba}")

            patched_sector = bytearray(original_sector)
            source_start = cursor
            source_end = cursor + take
            patched_sector[
                disc.MODE2_FORM1_DATA_OFFSET
                + in_sector : disc.MODE2_FORM1_DATA_OFFSET
                + in_sector
                + take
            ] = patched_dat[source_start:source_end]
            rebuilt_sector = rebuild_mode2_form1_integrity(bytes(patched_sector))
            if rebuild_mode2_form1_integrity(rebuilt_sector) != rebuilt_sector:
                raise ValueError(f"patched EDC/ECC failed self-check at LBA {lba}")
            output_track.seek(raw_offset)
            output_track.write(rebuilt_sector)
            changed_raw_local = [
                offset
                for offset, (before, after) in enumerate(
                    zip(original_sector, rebuilt_sector)
                )
                if before != after
            ]
            allowed_local = [
                (
                    disc.MODE2_FORM1_DATA_OFFSET + in_sector,
                    disc.MODE2_FORM1_DATA_OFFSET + in_sector + take,
                ),
                (0x818, 0x930),
            ]
            if any(
                not any(start <= offset < end for start, end in allowed_local)
                for offset in changed_raw_local
            ):
                raise ValueError(f"raw diff escaped expected ranges at LBA {lba}")
            touched_sector_records.append(
                {
                    "lba": lba,
                    "raw_offset": raw_offset,
                    "original_sha256": sha256_bytes(original_sector),
                    "patched_sha256": sha256_bytes(rebuilt_sector),
                    "original_integrity_verified": True,
                    "patched_integrity_verified": True,
                    "allowed_local_ranges": [
                        {"start": start, "end": end} for start, end in allowed_local
                    ],
                    "changed_local_ranges": [
                        {"start": start, "end": end}
                        for start, end in contiguous_ranges(changed_raw_local)
                    ],
                }
            )
            cursor += take

    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n'
        "  TRACK 01 MODE2/2352\n"
        "    INDEX 01 00:00:00\n",
        encoding="ascii",
        newline="\n",
    )

    with output_bin.open("rb") as patched_track:
        patched_file_data, patched_forms = disc.read_extent(
            patched_track, entry["lba"], entry["size"]
        )
    if patched_forms != {"form1"}:
        raise ValueError("patched O04 extent form changed")
    if bytes(patched_file_data) != bytes(patched_dat):
        raise ValueError("patched disc does not contain the expected O04.DAT")
    reparsed = timlib.parse_tim(patched_file_data, TARGET_TIM_OFFSET)
    if reparsed is None or (
        reparsed["bpp"],
        reparsed["pixel_width"],
        reparsed["pixel_height"],
        reparsed["total_size"],
    ) != (4, 232, 16, 1920):
        raise ValueError("patched target TIM no longer validates")
    if decode_4bpp_indices(patched_file_data, reparsed) != patched_indices:
        raise ValueError("patched TIM pixels differ after disc reinsertion")
    if output_bin.stat().st_size != args.image.stat().st_size:
        raise ValueError("patched BIN size changed")

    report = {
        "gate_id": "BBJ-POC-GFX-001",
        "status": "static_build_passed_runtime_pending",
        "baseline": {
            "image": str(args.image),
            "sha256": original_hash,
            "size": args.image.stat().st_size,
        },
        "target": {
            "disc_path": TARGET_PATH,
            "file_lba": entry["lba"],
            "file_size": entry["size"],
            "file_original_sha256": EXPECTED_O04_SHA256,
            "tim_offset": TARGET_TIM_OFFSET,
            "tim_total_size": target["total_size"],
            "image_payload_offset": image_meta["payload_offset"],
            "image_payload_size": image_meta["payload_size"],
            "format": "TIM 4bpp indexed",
            "pixel_width": target["pixel_width"],
            "pixel_height": target["pixel_height"],
            "source_text": "オプション",
            "translated_text": TARGET_TEXT,
        },
        "font": {
            "path": str(args.font),
            "sha256": font_hash,
        },
        "roundtrip": {
            "original_decode_encode_byte_identical": True,
            "tim_header_and_clut_unchanged": (
                patched_dat[TARGET_TIM_OFFSET : image_meta["payload_offset"]]
                == original_dat[TARGET_TIM_OFFSET : image_meta["payload_offset"]]
            ),
        },
        "render": render_report,
        "expected_write": {
            "owner": "BBJ-POC-GFX-001",
            "dat_allowed_start": allowed_start,
            "dat_allowed_end": allowed_end,
            "dat_changed_byte_count": len(changed_dat_offsets),
            "dat_changed_ranges": [
                {"start": start, "end": end}
                for start, end in contiguous_ranges(changed_dat_offsets)
            ],
            "touched_sectors": touched_sector_records,
        },
        "output": {
            "bin": str(output_bin),
            "bin_sha256": sha256_file(output_bin),
            "bin_size": output_bin.stat().st_size,
            "cue": str(output_cue),
            "patched_dat": str(output_dat),
            "patched_dat_sha256": sha256_bytes(bytes(patched_dat)),
            "preview": str(preview_png),
            "preview_4x": str(preview_scaled_png),
        },
        "static_checks": {
            "source_hash": True,
            "font_hash": True,
            "tim_roundtrip": True,
            "palette_preserved": True,
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
            "required_screen": "옵션 화면",
            "required_observation": "옵션 헤더가 한글 옵션으로 표시되고 주변 UI와 진입·이탈이 정상",
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
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
