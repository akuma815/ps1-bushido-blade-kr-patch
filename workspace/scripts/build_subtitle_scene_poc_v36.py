#!/usr/bin/env python3
"""v36: render the first-kill subtitle line with the proven runtime scene key.

Differences from v35 (build_official_glyph_poc.py):
- Gate uses the proven runtime key instead of the non-unique record header:
    file  : [0x8014C00C] == 0x3F8       (V00.DAT word3, unique across V0X)
    group : [gp+0x2F8]   == 0x8014C06C  (group-3 compare entry)
    frame : 60 <= a0 < 180
- Renders the full first line "처음으로…" as four 16x16 glyphs stacked in
  VRAM column (950,256,4,64) = stock cells A/C/E/G, plus stock '.' glyphs
  for the ellipsis. Text string: "ACEG...".
- Glyphs + CLUT re-uploaded every gated frame (v28 proved this clean), so no
  font-reload hook and no other-screen contamination outside the scene.
- Template CLUT fields are patched at RUNTIME around the renderer call only
  (patch -> render -> restore within the same frame). A static CLUT patch
  broke the CONTINUE screen's C/E letters (user screenshot, 2026-08-01):
  the stock 39-glyph font is live UI on real screens.

Keystone auto-fills branch delay slots with nops: never write delay-slot
instructions in wrapper sources (HANDOFF_2026-08-01.md section 15).
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
from pathlib import Path

import inspect_psx_disc as disc
from build_official_glyph_poc import (
    A_GLYPH_INDEX,
    CAVE_END_RAM,
    CAVE_START_RAM,
    CLUT_ID,
    CLUT_RECT,
    EXE_HEADER_SIZE,
    EXE_PATH,
    EXE_RAM_BASE,
    EXPECTED_BIN_SHA256,
    EXPECTED_EXE_SHA256,
    JP_GLYPH_TABLE_RAM,
    JP_LOAD_IMAGE_RAM,
    JP_TEXT_RENDERER_RAM,
    MEMORY_MAP_PATH,
    V00_UPDATE_COMPARE_FALLTHROUGH_RAM,
    V00_UPDATE_COMPARE_HOOK_RAM,
    V00_UPDATE_COMPARE_TAKEN_RAM,
    render_glyph,
    sha256_bytes,
    split_address,
)
from build_subtitle_poc import assemble, encode_jump, patch_logical_sector, sha256_file

TRANSIENT_BASE = 0x8014C000
V00_WORD3 = 0x3F8
GROUP3_CMP_PTR = 0x8014C06C
START_FRAME = 60
END_FRAME = 180

LINE_CHARACTERS = "처음으로"
LINE_TEXT = "ACEG..."
# stock cells A/C/E/G form one contiguous VRAM column
GLYPH_STRIP_RECT = (950, 256, 4, 16 * len(LINE_CHARACTERS))
TEMPLATE_INDICES = [13, 15, 17, 19]  # A, C, E, G
STOCK_TEMPLATE_CLUT = 0x7838

TEXT_X = 104
TEXT_Y = 190
# The font's ink height (with stroke) can exceed the 16px cell regardless of
# nominal size (user report: 'ㅓ' top clipped at both 16px and 15px), so keep
# 16px and squeeze oversized ink to fit instead of clipping it.
FONT_PIXEL_SIZE = 16


def render_glyph_fit(
    font_path: Path, character: str, pixel_size: int = FONT_PIXEL_SIZE
) -> tuple[bytes, "Image.Image", dict]:
    """Render one glyph like render_glyph, but shrink oversized ink into the
    16x16 cell (index-preserving NEAREST) instead of letting it clip."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), pixel_size)
    canvas = Image.new("P", (48, 48), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 16), character, font=font, fill=2, stroke_width=1, stroke_fill=1)
    raw_indices = Image.frombytes("L", canvas.size, canvas.tobytes())
    ink_box = raw_indices.point(lambda v: 255 if v else 0).getbbox()
    if ink_box is None:
        raise ValueError(f"empty glyph for {character!r}")
    ink = canvas.crop(ink_box)
    squeezed = ink.size
    if ink.width > 16 or ink.height > 16:
        squeezed = (min(ink.width, 16), min(ink.height, 16))
        ink = ink.resize(squeezed, Image.Resampling.NEAREST)
    cell = Image.new("P", (16, 16), 0)
    cell.paste(ink, ((16 - ink.width) // 2, (16 - ink.height) // 2))

    pixels = cell.load()
    packed = bytearray()
    for y in range(16):
        for x in range(0, 16, 2):
            packed.append((pixels[x, y] & 0x0F) | ((pixels[x + 1, y] & 0x0F) << 4))
    preview = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    rgba = preview.load()
    for y in range(16):
        for x in range(16):
            value = pixels[x, y]
            if value == 1:
                rgba[x, y] = (0, 0, 0, 255)
            elif value == 2:
                rgba[x, y] = (255, 255, 255, 255)
    meta = {
        "character": character,
        "font": str(font_path),
        "font_pixel_size": pixel_size,
        "stroke_pixels": 1,
        "ink_size": list(ink.size),
        "squeezed_to": list(squeezed),
    }
    return bytes(packed), preview, meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--font",
        type=Path,
        default=Path(r"G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf"),
    )
    parser.add_argument(
        "--base-name",
        default="Bushido Blade (Japan) [KO Scene Key PoC v36]",
    )
    args = parser.parse_args()

    memory_map = json.loads(MEMORY_MAP_PATH.read_text(encoding="utf-8"))
    payload_policy = memory_map["policy"]["official_glyph_payload"]
    if (int(payload_policy["start"], 0), int(payload_policy["end_exclusive"], 0)) != (
        CAVE_START_RAM,
        CAVE_END_RAM,
    ):
        raise ValueError("builder payload range disagrees with subtitle_memory_map.json")

    if sha256_file(args.image) != EXPECTED_BIN_SHA256:
        raise ValueError("unsupported source BIN hash")
    inspection = disc.inspect(args.image)
    entry = next(item for item in inspection["entries"] if item["path"] == EXE_PATH)
    if entry["sha256"].upper() != EXPECTED_EXE_SHA256:
        raise ValueError("unsupported Japanese executable baseline")
    with args.image.open("rb") as source:
        original_exe, forms = disc.read_extent(source, entry["lba"], entry["size"])
    if forms != {"form1"}:
        raise ValueError("Japanese executable is not Form 1")

    # --- payload data -------------------------------------------------------
    glyph_block = bytearray()
    glyph_meta = []
    previews = []
    for character in LINE_CHARACTERS:
        data, preview, meta = render_glyph_fit(args.font, character, FONT_PIXEL_SIZE)
        glyph_block += data
        glyph_meta.append(meta)
        previews.append(preview)
    if len(glyph_block) != 128 * len(LINE_CHARACTERS):
        raise AssertionError("glyph block size mismatch")

    clut_data = struct.pack("<16H", 0x0000, 0x8000, 0x7FFF, *([0x0000] * 13))
    format_data = ("#X#Y#w#h#S#R#G#B" + LINE_TEXT).encode("ascii") + b"\x00"

    wrapper_ram = CAVE_START_RAM
    data_ram = CAVE_START_RAM + 0x200
    glyph_rect_ram = data_ram
    clut_rect_ram = glyph_rect_ram + 8
    glyph_data_ram = clut_rect_ram + 8
    clut_data_ram = glyph_data_ram + len(glyph_block)
    format_ram = clut_data_ram + len(clut_data)
    split = {
        name: split_address(value)
        for name, value in {
            "glyph_rect": glyph_rect_ram,
            "clut_rect": clut_rect_ram,
            "glyph_data": glyph_data_ram,
            "clut_data": clut_data_ram,
            "format": format_ram,
        }.items()
    }

    template_clut_addresses = [
        JP_GLYPH_TABLE_RAM + index * 16 + 6 for index in TEMPLATE_INDICES
    ]
    template_hi = template_clut_addresses[0] >> 16
    if any(address >> 16 != template_hi for address in template_clut_addresses):
        raise ValueError("template CLUT addresses span multiple 64K pages")
    if any(address & 0x8000 for address in template_clut_addresses):
        raise ValueError("template CLUT low halves need signed handling")
    patch_lines = "\n".join(
        f"    sh $t5, 0x{address & 0xFFFF:X}($t3)"
        for address in template_clut_addresses
    )

    wrapper_source = f"""
    lui $t5, 0x8014
    ori $t5, $t5, 0xC000
    lw $t6, 0xC($t5)
    ori $t7, $zero, {V00_WORD3}
    bne $t6, $t7, compare_exit
    lw $t6, 0x2F8($gp)
    addiu $t7, $t5, 0x6C
    bne $t6, $t7, compare_exit
    sltiu $t4, $a0, {START_FRAME}
    bne $t4, $zero, compare_exit
    sltiu $t4, $a0, {END_FRAME}
    beq $t4, $zero, compare_exit

    addiu $sp, $sp, -0x30
    sw $ra, 0x2c($sp)
    sw $a0, 0x28($sp)
    sw $v1, 0x24($sp)
    lui $a0, {split['glyph_rect'][0]}
    ori $a0, $a0, {split['glyph_rect'][1]}
    lui $a1, {split['glyph_data'][0]}
    ori $a1, $a1, {split['glyph_data'][1]}
    jal 0x{JP_LOAD_IMAGE_RAM:08x}
    lui $a0, {split['clut_rect'][0]}
    ori $a0, $a0, {split['clut_rect'][1]}
    lui $a1, {split['clut_data'][0]}
    ori $a1, $a1, {split['clut_data'][1]}
    jal 0x{JP_LOAD_IMAGE_RAM:08x}
    lui $a0, {split['format'][0]}
    ori $a0, $a0, {split['format'][1]}
    ori $a1, $zero, {TEXT_X}
    ori $a2, $zero, {TEXT_Y}
    ori $a3, $zero, 15
    ori $t5, $zero, 15
    sw $t5, 0x10($sp)
    ori $t5, $zero, 0x1000
    sw $t5, 0x14($sp)
    ori $t5, $zero, 0x80
    sw $t5, 0x18($sp)
    sw $t5, 0x1c($sp)
    sw $t5, 0x20($sp)
    lui $t3, 0x{template_hi & 0xFFFF:X}
    ori $t5, $zero, 0x{CLUT_ID:X}
{patch_lines}
    jal 0x{JP_TEXT_RENDERER_RAM:08x}
    lui $t3, 0x{template_hi & 0xFFFF:X}
    ori $t5, $zero, 0x{STOCK_TEMPLATE_CLUT:X}
{patch_lines}
    lw $v1, 0x24($sp)
    lw $a0, 0x28($sp)
    lw $ra, 0x2c($sp)
    addiu $sp, $sp, 0x30

compare_exit:
    srl $s0, $s0, 3
    beq $a0, $v1, compare_taken
    j 0x{V00_UPDATE_COMPARE_FALLTHROUGH_RAM:08x}
compare_taken:
    j 0x{V00_UPDATE_COMPARE_TAKEN_RAM:08x}
    """
    wrapper_code = assemble(wrapper_source, wrapper_ram)
    if wrapper_ram + len(wrapper_code) > data_ram:
        raise ValueError(f"wrapper exceeds reserved block: {len(wrapper_code)}")

    data = (
        struct.pack("<hhhh", *GLYPH_STRIP_RECT)
        + struct.pack("<hhhh", *CLUT_RECT)
        + bytes(glyph_block)
        + clut_data
        + format_data
    )
    end_ram = data_ram + len(data)
    if end_ram > CAVE_END_RAM:
        raise ValueError(f"payload exceeds verified code cave: end=0x{end_ram:08X}")

    payload = bytearray(end_ram - CAVE_START_RAM)
    payload[: len(wrapper_code)] = wrapper_code
    payload[data_ram - CAVE_START_RAM :] = data

    # --- patch executable ---------------------------------------------------
    patched_exe = bytearray(original_exe)

    expected_update = struct.pack("<II", 0x10830003, 0x001080C2)
    update_offset = EXE_HEADER_SIZE + V00_UPDATE_COMPARE_HOOK_RAM - EXE_RAM_BASE
    if original_exe[update_offset : update_offset + 8] != expected_update:
        raise ValueError("compare hook expected-write mismatch")
    patched_exe[update_offset : update_offset + 8] = (
        encode_jump(wrapper_ram, False) + b"\0\0\0\0"
    )

    # Templates stay stock on disc; the wrapper patches and restores their
    # CLUT fields around the renderer call at runtime. Verify the stock value
    # the restore path writes back.
    template_checks = []
    for index in TEMPLATE_INDICES:
        template_ram = JP_GLYPH_TABLE_RAM + index * 16
        template_offset = EXE_HEADER_SIZE + template_ram - EXE_RAM_BASE
        before = bytes(original_exe[template_offset : template_offset + 16])
        stock_clut = struct.unpack_from("<H", before, 6)[0]
        if stock_clut != STOCK_TEMPLATE_CLUT:
            raise ValueError(
                f"template {index} stock CLUT mismatch: 0x{stock_clut:04X}"
            )
        template_checks.append(
            {
                "index": index,
                "clut_field_ram": f"0x{template_ram + 6:08X}",
                "stock_clut": f"0x{stock_clut:04X}",
                "runtime_patch": f"0x{CLUT_ID:04X} around renderer call only",
            }
        )

    cave_offset = EXE_HEADER_SIZE + CAVE_START_RAM - EXE_RAM_BASE
    cave_end_offset = EXE_HEADER_SIZE + CAVE_END_RAM - EXE_RAM_BASE
    cave_source_hash = sha256_bytes(bytes(original_exe[cave_offset:cave_end_offset]))
    patched_exe[cave_offset : cave_offset + len(payload)] = payload

    # --- write disc ---------------------------------------------------------
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = args.output_dir / f"{args.base_name}.bin"
    output_cue = args.output_dir / f"{args.base_name}.cue"
    report_path = args.output_dir / "build_report.json"
    shutil.copyfile(args.image, output_bin)

    from PIL import Image

    sheet = Image.new("RGBA", (16 * len(previews), 16), (0, 0, 0, 255))
    for i, preview in enumerate(previews):
        sheet.paste(preview, (16 * i, 0), preview)
    sheet.resize((64 * len(previews), 64), Image.Resampling.NEAREST).save(
        args.output_dir / "glyph_preview.png"
    )

    touched = []
    for index in range(len(patched_exe) // disc.LOGICAL_SECTOR_SIZE):
        chunk = patched_exe[
            index * disc.LOGICAL_SECTOR_SIZE : (index + 1) * disc.LOGICAL_SECTOR_SIZE
        ]

        def replace_data(raw: bytearray, chunk=chunk):
            start = disc.MODE2_FORM1_DATA_OFFSET
            raw[start : start + disc.LOGICAL_SECTOR_SIZE] = chunk

        touched.append(patch_logical_sector(output_bin, entry["lba"] + index, replace_data))

    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
        newline="\n",
    )
    reparsed = disc.inspect(output_bin)
    patched_entry = next(item for item in reparsed["entries"] if item["path"] == EXE_PATH)
    with output_bin.open("rb") as track:
        disc_exe, disc_forms = disc.read_extent(track, patched_entry["lba"], patched_entry["size"])
    if disc_forms != {"form1"} or disc_exe != bytes(patched_exe):
        raise ValueError("patched executable failed disc roundtrip")

    report = {
        "gate_id": "BBJ-POC-SCENE-KEY-001",
        "status": "static_build_passed_runtime_pending",
        "runtime_key": {
            "file_check": f"[0x8014C00C] == 0x{V00_WORD3:X} (V00.DAT)",
            "group_check": f"[gp+0x2F8] == 0x{GROUP3_CMP_PTR:08X} (group 3)",
            "frame_window": [START_FRAME, END_FRAME],
        },
        "text": {
            "korean": LINE_CHARACTERS + "…",
            "encoded": LINE_TEXT,
            "screen_position": [TEXT_X, TEXT_Y],
            "glyph_strip_rect": list(GLYPH_STRIP_RECT),
            "clut_rect": list(CLUT_RECT),
            "clut_id": f"0x{CLUT_ID:04X}",
        },
        "glyphs": glyph_meta,
        "wrapper": {
            "ram": f"0x{wrapper_ram:08X}",
            "size": len(wrapper_code),
            "hook": f"0x{V00_UPDATE_COMPARE_HOOK_RAM:08X}",
        },
        "payload_end_ram": f"0x{end_ram:08X}",
        "expected_writes": {
            "update_hook": {
                "ram": f"0x{V00_UPDATE_COMPARE_HOOK_RAM:08X}",
                "before": expected_update.hex(),
                "after": (encode_jump(wrapper_ram, False) + b"\0\0\0\0").hex(),
            },
            "templates_runtime_only": template_checks,
            "code_cave": {
                "ram_start": f"0x{CAVE_START_RAM:08X}",
                "ram_end": f"0x{CAVE_END_RAM:08X}",
                "source_sha256": cave_source_hash,
            },
        },
        "output": {
            "bin": str(output_bin),
            "cue": str(output_cue),
            "bin_sha256": sha256_file(output_bin),
        },
        "runtime": {
            "status": "pending",
            "required_observation": (
                "V00 group-3 scene shows '처음으로....' at frames 60-179 and "
                "nothing in any other scene or screen"
            ),
        },
        "sector_changes": len(touched),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"output_cue={output_cue}")
    print(f"report={report_path}")
    print(f"wrapper={len(wrapper_code)} bytes, payload_end=0x{end_ram:08X}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
