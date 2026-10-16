#!/usr/bin/env python3
"""Build a one-glyph Korean subtitle PoC using the Japanese game's own font renderer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import inspect_psx_disc as disc
from build_options_poc import rebuild_mode2_form1_integrity
from build_subtitle_poc import assemble, encode_jump, patch_logical_sector, sha256_file


EXPECTED_BIN_SHA256 = "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
EXPECTED_EXE_SHA256 = "0164189A870BA87495A3C0010ADB85535AE5A798BBD0DD276B88BF24ECC3CEDA"
EXE_PATH = "SLPM_860.20"
MEMORY_MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "subtitle_memory_map.json"
EXE_RAM_BASE = 0x80010000
EXE_HEADER_SIZE = 0x800

V00_GROUP_HOOK_RAM = 0x8003A1A0
V00_GROUP_CONTINUE_RAM = 0x8003A1A8
V00_GROUP_UPDATE_HOOK_RAM = 0x8003A8A8
V00_GROUP_UPDATE_CONTINUE_RAM = 0x8003A8B0
# USA-equivalent lifecycle point: right after the countdown store, before the
# frame-vs-length compare. a0 holds the scene frame, v1 the scene length.
V00_UPDATE_COMPARE_HOOK_RAM = 0x8003A88C
V00_UPDATE_COMPARE_TAKEN_RAM = 0x8003A89C
V00_UPDATE_COMPARE_FALLTHROUGH_RAM = 0x8003A894
RENDER_CALLSITE_RAM = 0x8004322C
RENDER_ORIGINAL_RAM = 0x80070838
FIRST_KILL_GROUP = 3
START_FRAME = 60
END_FRAME = 180

JP_TEXT_RENDERER_RAM = 0x80038B80
JP_LOAD_IMAGE_RAM = 0x80069024
FONT_LOAD_RETURN_HOOK_RAM = 0x80067864
JP_GLYPH_TABLE_RAM = 0x800858AC
A_GLYPH_INDEX = 13
A_TEMPLATE_RAM = JP_GLYPH_TABLE_RAM + A_GLYPH_INDEX * 16

ACTIVE_OFFSET = 3
FRAME_OFFSET = 8

CAVE_START_RAM = 0x800118B4
EVENT_WRAPPER_RAM = CAVE_START_RAM
UPDATE_WRAPPER_RAM = CAVE_START_RAM + 0x100
RENDER_WRAPPER_RAM = CAVE_START_RAM + 0x200
FONT_RESTORE_WRAPPER_RAM = CAVE_START_RAM + 0x300
DATA_RAM = CAVE_START_RAM + 0x400
# Keep the subtitle lifecycle state in the 12-byte padding after the 19-byte
# timed format string (0x80011D64..0x80011D76) and before the existing
# 0x80011D84 init byte. The former 0x800A2E90 address belongs to mutable game
# data. The rejected 0x80011D94 placement overwrote live executable bytes and
# prevented the disc from booting.
STATE_RAM = DATA_RAM + 0xC4
CAVE_END_RAM = 0x80011D85

# The stock A template selects tpage 0x003E, UV (216,0)..(231,15):
# 4bpp page base (896,256), therefore its physical VRAM rectangle is
# x=896+216/4=950, y=256, w=16/4=4 words, h=16.
GLYPH_RECT = (950, 256, 4, 16)
# A private 16-color CLUT at the last legal VRAM row/column group.
CLUT_RECT = (1008, 511, 16, 1)
CLUT_ID = ((CLUT_RECT[1] & 0x1FF) << 6) | ((CLUT_RECT[0] >> 4) & 0x3F)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def split_address(address: int) -> tuple[int, int]:
    return (address >> 16) & 0xFFFF, address & 0xFFFF


def split_address_signed(address: int) -> tuple[int, int]:
    low = address & 0xFFFF
    high = ((address + (0x10000 if low & 0x8000 else 0)) >> 16) & 0xFFFF
    return high, low - 0x10000 if low & 0x8000 else low


def render_glyph(
    font_path: Path, character: str, pixel_size: int = 16
) -> tuple[bytes, Image.Image, dict]:
    if len(character) != 1:
        raise ValueError("the PoC accepts exactly one Unicode character")
    font = ImageFont.truetype(str(font_path), pixel_size)
    probe = Image.new("L", (64, 64), 0)
    probe_draw = ImageDraw.Draw(probe)
    bounds = probe_draw.textbbox((0, 0), character, font=font, stroke_width=1)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    origin_x = (16 - width) // 2 - bounds[0]
    origin_y = (16 - height) // 2 - bounds[1]

    indexed = Image.new("P", (16, 16), 0)
    draw = ImageDraw.Draw(indexed)
    draw.text(
        (origin_x, origin_y),
        character,
        font=font,
        fill=2,
        stroke_width=1,
        stroke_fill=1,
    )
    packed = bytearray()
    pixels = indexed.load()
    for y in range(16):
        for x in range(0, 16, 2):
            packed.append((pixels[x, y] & 0x0F) | ((pixels[x + 1, y] & 0x0F) << 4))
    if len(packed) != 128:
        raise AssertionError("16x16 4bpp glyph must occupy 128 bytes")

    preview = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    rgba = preview.load()
    for y in range(16):
        for x in range(16):
            value = pixels[x, y]
            if value == 1:
                rgba[x, y] = (0, 0, 0, 255)
            elif value == 2:
                rgba[x, y] = (255, 255, 255, 255)
    return bytes(packed), preview, {
        "character": character,
        "font": str(font_path),
        "font_sha256": sha256_file(font_path),
        "font_pixel_size": pixel_size,
        "stroke_pixels": 1,
        "glyph_bounds": list(bounds),
        "draw_origin": [origin_x, origin_y],
    }


def make_payload(
    glyph_data: bytes,
    diagnostic_always: bool = False,
    font_reload_hook: bool = False,
    timed_pair: bool = False,
    scene_frame_direct: bool = False,
    scene_compare_hook: bool = False,
    scene_signature: dict | None = None,
) -> tuple[bytes, dict]:
    state_hi, state_lo = split_address_signed(STATE_RAM)
    glyph_rect_ram = DATA_RAM
    clut_rect_ram = glyph_rect_ram + 8
    glyph_data_ram = clut_rect_ram + 8
    clut_data_ram = glyph_data_ram + len(glyph_data)
    format_ram = clut_data_ram + 32
    init_flag_ram = format_ram + 32

    addresses = {
        "glyph_rect": glyph_rect_ram,
        "clut_rect": clut_rect_ram,
        "glyph_data": glyph_data_ram,
        "clut_data": clut_data_ram,
        "format": format_ram,
        "init_flag": init_flag_ram,
    }
    if not scene_frame_direct:
        addresses["state"] = STATE_RAM
    split = {key: split_address(value) for key, value in addresses.items()}

    if scene_frame_direct:
        # The direct timeline reads only game-owned scene fields. This stub is
        # not hooked; it merely keeps the existing payload layout stable.
        event_source = "jr $ra; nop"
    else:
        event_source = f"""
        lui $t1, {state_hi}
        ori $t0, $zero, {FIRST_KILL_GROUP}
        bne $s2, $t0, clear_group
        nop
        ori $t2, $zero, 1
        sb $t2, {state_lo + ACTIVE_OFFSET}($t1)
        sw $zero, {state_lo + FRAME_OFFSET}($t1)

        lui $a0, {split['glyph_rect'][0]}
        ori $a0, $a0, {split['glyph_rect'][1]}
        lui $a1, {split['glyph_data'][0]}
        ori $a1, $a1, {split['glyph_data'][1]}
        jal 0x{JP_LOAD_IMAGE_RAM:08x}
        nop
        lui $a0, {split['clut_rect'][0]}
        ori $a0, $a0, {split['clut_rect'][1]}
        lui $a1, {split['clut_data'][0]}
        ori $a1, $a1, {split['clut_data'][1]}
        jal 0x{JP_LOAD_IMAGE_RAM:08x}
        nop
        b group_epilogue
        nop

    clear_group:
        sb $zero, {state_lo + ACTIVE_OFFSET}($t1)
        sw $zero, {state_lo + FRAME_OFFSET}($t1)
    group_epilogue:
        lw $ra, 0x4c($sp)
        lw $s2, 0x48($sp)
        j 0x{V00_GROUP_CONTINUE_RAM:08x}
        nop
        """
    event_code = assemble(event_source, EVENT_WRAPPER_RAM)
    if len(event_code) > UPDATE_WRAPPER_RAM - EVENT_WRAPPER_RAM:
        raise ValueError("event wrapper exceeds reserved block")

    if scene_compare_hook:
        # Hooked at 0x8003A88C: a0 = scene frame (1..length), v1 = scene
        # length from [gp+0x2F8]+4, s0 not yet shifted, v0 dead. The wrapper
        # must leave a0/v1/s0 exactly as the original compare expects and
        # reproduce `srl $s0, $s0, 3` plus the beq on both paths.
        if scene_signature is None:
            raise ValueError("scene_compare_hook requires the runtime scene signature")
        signature_word0 = int(scene_signature["word_at_0"], 0)
        signature_word4 = int(scene_signature["word_at_4"], 0)
        signature_word8 = int(scene_signature["word_at_8"], 0)
        scene_length = int(scene_signature["scene_length_via_gp_0x2F8_word_at_4"])
        # Keystone auto-fills every branch/jump delay slot with a nop, so no
        # explicit delay-slot instructions are written here. The single nop
        # after the first lw covers the R3000 load-delay slot; later loads get
        # a natural one-instruction gap before use.
        update_source = f"""
        lw $t5, 0x304($gp)
        nop
        beq $t5, $zero, compare_exit
        lw $t6, 0($t5)
        ori $t7, $zero, {signature_word0}
        bne $t6, $t7, compare_exit
        lw $t6, 4($t5)
        ori $t7, $zero, {signature_word4}
        bne $t6, $t7, compare_exit
        lw $t6, 8($t5)
        ori $t7, $zero, {signature_word8}
        bne $t6, $t7, compare_exit
        ori $t7, $zero, {scene_length}
        bne $v1, $t7, compare_exit
        sltiu $t4, $a0, {START_FRAME}
        bne $t4, $zero, compare_exit
        sltiu $t4, $a0, {END_FRAME}
        beq $t4, $zero, compare_exit

        addiu $sp, $sp, -0x30
        sw $ra, 0x2c($sp)
        sw $a0, 0x28($sp)
        sw $v1, 0x24($sp)
        lui $a0, {split['format'][0]}
        ori $a0, $a0, {split['format'][1]}
        ori $a1, $zero, 304
        ori $a2, $zero, 190
        ori $a3, $zero, 15
        ori $t5, $zero, 15
        sw $t5, 0x10($sp)
        ori $t5, $zero, 0x1000
        sw $t5, 0x14($sp)
        ori $t5, $zero, 0x80
        sw $t5, 0x18($sp)
        sw $t5, 0x1c($sp)
        sw $t5, 0x20($sp)
        jal 0x{JP_TEXT_RENDERER_RAM:08x}
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
        update_code = assemble(update_source, UPDATE_WRAPPER_RAM)
    elif scene_frame_direct:
        update_preamble = """
        lw $t5, 0x304($gp)
        nop
        beq $t5, $zero, update_epilogue
        nop
        lw $t6, 4($t5)
        lui $t7, 0x000c
        ori $t7, $t7, 0x16e9
        bne $t6, $t7, update_epilogue
        nop
        lw $t6, 12($t5)
        lui $t7, 0xacaf
        ori $t7, $t7, 0x17df
        bne $t6, $t7, update_epilogue
        nop
        lw $t2, 0x314($gp)
        """
        clear_active_source = "b update_epilogue; nop"
    else:
        update_preamble = f"""
        lui $t0, {state_hi}
        lbu $t1, {state_lo + ACTIVE_OFFSET}($t0)
        beq $t1, $zero, update_epilogue
        nop
        lw $t2, {state_lo + FRAME_OFFSET}($t0)
        addiu $t3, $t2, 1
        sw $t3, {state_lo + FRAME_OFFSET}($t0)
        """
        clear_active_source = f"sb $zero, {state_lo + ACTIVE_OFFSET}($t0)"

    if not scene_compare_hook:
        update_source = update_preamble + f"""
        sltiu $t4, $t2, {START_FRAME}
        bne $t4, $zero, update_epilogue
        nop
        sltiu $t4, $t2, {END_FRAME}
        beq $t4, $zero, clear_active
        nop

        addiu $sp, $sp, -0x30
        sw $ra, 0x2c($sp)
        lui $a0, {split['format'][0]}
        ori $a0, $a0, {split['format'][1]}
        ori $a1, $zero, 304
        ori $a2, $zero, 190
        ori $a3, $zero, 15
        ori $t5, $zero, 15
        sw $t5, 0x10($sp)
        ori $t5, $zero, 0x1000
        sw $t5, 0x14($sp)
        ori $t5, $zero, 0x80
        sw $t5, 0x18($sp)
        sw $t5, 0x1c($sp)
        jal 0x{JP_TEXT_RENDERER_RAM:08x}
        sw $t5, 0x20($sp)
        lw $ra, 0x2c($sp)
        addiu $sp, $sp, 0x30
        b update_epilogue
        nop

    clear_active:
        {clear_active_source}
    update_epilogue:
        lw $ra, 0x3c($sp)
        lw $s2, 0x38($sp)
        j 0x{V00_GROUP_UPDATE_CONTINUE_RAM:08x}
        nop
    """
        update_code = assemble(update_source, UPDATE_WRAPPER_RAM)
    if UPDATE_WRAPPER_RAM + len(update_code) > RENDER_WRAPPER_RAM:
        raise ValueError(
            f"update wrapper exceeds reserved block: {len(update_code)} bytes > "
            f"{RENDER_WRAPPER_RAM - UPDATE_WRAPPER_RAM}"
        )

    render_code = b""
    if diagnostic_always:
        render_source = f"""
            addiu $sp, $sp, -0x30
            sw $ra, 0x2c($sp)
            sw $a0, 0x28($sp)
            sw $s0, 0x24($sp)

        """
        if not font_reload_hook:
            render_source += f"""
            # v28 diagnostic: re-upload every frame to isolate the atlas
            # lifecycle from renderer/CLUT correctness.
            lui $a0, {split['glyph_rect'][0]}
            ori $a0, $a0, {split['glyph_rect'][1]}
            lui $a1, {split['glyph_data'][0]}
            ori $a1, $a1, {split['glyph_data'][1]}
            jal 0x{JP_LOAD_IMAGE_RAM:08x}
            nop
            lui $a0, {split['clut_rect'][0]}
            ori $a0, $a0, {split['clut_rect'][1]}
            lui $a1, {split['clut_data'][0]}
            ori $a1, $a1, {split['clut_data'][1]}
            jal 0x{JP_LOAD_IMAGE_RAM:08x}
            nop
            """
        render_source += f"""
            lui $a0, {split['format'][0]}
            ori $a0, $a0, {split['format'][1]}
            ori $a1, $zero, 296
            ori $a2, $zero, 180
            ori $a3, $zero, 15
            ori $t5, $zero, 15
            sw $t5, 0x10($sp)
            ori $t5, $zero, 0x1000
            sw $t5, 0x14($sp)
            ori $t5, $zero, 0x80
            sw $t5, 0x18($sp)
            sw $t5, 0x1c($sp)
            jal 0x{JP_TEXT_RENDERER_RAM:08x}
            sw $t5, 0x20($sp)

            lw $a0, 0x28($sp)
            jal 0x{RENDER_ORIGINAL_RAM:08x}
            nop
            lw $s0, 0x24($sp)
            lw $ra, 0x2c($sp)
            addiu $sp, $sp, 0x30
            jr $ra
            nop
        """
        render_code = assemble(render_source, RENDER_WRAPPER_RAM)
        if RENDER_WRAPPER_RAM + len(render_code) > FONT_RESTORE_WRAPPER_RAM:
            raise ValueError("diagnostic render wrapper exceeds reserved block")

    font_restore_code = b""
    if font_reload_hook:
        font_restore_source = f"""
            # s0 is the RECT* argument retained by the game's queued
            # LoadImage wrapper. Restore when the completed upload overlaps
            # either the glyph cell (950,256,4,16) or the private CLUT cell
            # (1008,511,16,1).
            lh $t0, 0($s0)
            lh $t1, 2($s0)
            lh $t2, 4($s0)
            lh $t3, 6($s0)
            slti $t4, $t0, 951
            beq $t4, $zero, check_clut_overlap
            nop
            addu $t4, $t0, $t2
            slti $t4, $t4, 951
            bne $t4, $zero, check_clut_overlap
            nop
            slti $t4, $t1, 257
            beq $t4, $zero, check_clut_overlap
            nop
            addu $t4, $t1, $t3
            slti $t4, $t4, 257
            beq $t4, $zero, do_font_restore
            nop

        check_clut_overlap:
            slti $t4, $t0, 1009
            beq $t4, $zero, font_restore_epilogue
            nop
            addu $t4, $t0, $t2
            slti $t4, $t4, 1009
            bne $t4, $zero, font_restore_epilogue
            nop
            slti $t4, $t1, 512
            beq $t4, $zero, font_restore_epilogue
            nop
            addu $t4, $t1, $t3
            slti $t4, $t4, 512
            bne $t4, $zero, font_restore_epilogue
            nop

        do_font_restore:
            lui $a0, {split['glyph_rect'][0]}
            ori $a0, $a0, {split['glyph_rect'][1]}
            lui $a1, {split['glyph_data'][0]}
            ori $a1, $a1, {split['glyph_data'][1]}
            jal 0x{JP_LOAD_IMAGE_RAM:08x}
            nop
            lui $a0, {split['clut_rect'][0]}
            ori $a0, $a0, {split['clut_rect'][1]}
            lui $a1, {split['clut_data'][0]}
            ori $a1, $a1, {split['clut_data'][1]}
            jal 0x{JP_LOAD_IMAGE_RAM:08x}
            nop

        font_restore_epilogue:
            lw $ra, 0x18($sp)
            lw $s1, 0x14($sp)
            lw $s0, 0x10($sp)
            addiu $sp, $sp, 0x20
            jr $ra
            nop
        """
        font_restore_code = assemble(font_restore_source, FONT_RESTORE_WRAPPER_RAM)
        if FONT_RESTORE_WRAPPER_RAM + len(font_restore_code) > DATA_RAM:
            raise ValueError("font restore wrapper exceeds reserved block")

    rect_data = struct.pack("<hhhh", *GLYPH_RECT) + struct.pack("<hhhh", *CLUT_RECT)
    # Index 0 transparent, index 1 opaque black outline, index 2 opaque white fill.
    clut_data = struct.pack("<16H", 0x0000, 0x8000, 0x7FFF, *([0x0000] * 13))
    format_data = (
        b"#X#Y#w#h#S#R#G#BAB\x00"
        if diagnostic_always or timed_pair
        else b"#X#Y#w#h#S#R#G#BA\x00"
    )
    data = rect_data + glyph_data + clut_data + format_data
    if not scene_frame_direct:
        if len(data) < init_flag_ram - DATA_RAM + 1:
            data += b"\x00" * (init_flag_ram - DATA_RAM + 1 - len(data))
        if len(data) < STATE_RAM - DATA_RAM + 12:
            data += b"\x00" * (STATE_RAM - DATA_RAM + 12 - len(data))
    end_ram = DATA_RAM + len(data)
    if end_ram > CAVE_END_RAM:
        raise ValueError("payload exceeds verified code cave")

    payload = bytearray(end_ram - CAVE_START_RAM)
    payload[0 : len(event_code)] = event_code
    update_offset = UPDATE_WRAPPER_RAM - CAVE_START_RAM
    payload[update_offset : update_offset + len(update_code)] = update_code
    if render_code:
        render_offset = RENDER_WRAPPER_RAM - CAVE_START_RAM
        payload[render_offset : render_offset + len(render_code)] = render_code
    if font_restore_code:
        font_restore_offset = FONT_RESTORE_WRAPPER_RAM - CAVE_START_RAM
        payload[font_restore_offset : font_restore_offset + len(font_restore_code)] = font_restore_code
    data_offset = DATA_RAM - CAVE_START_RAM
    payload[data_offset : data_offset + len(data)] = data
    return bytes(payload), {
        "event_wrapper": {"ram": f"0x{EVENT_WRAPPER_RAM:08X}", "size": len(event_code)},
        "update_wrapper": {"ram": f"0x{UPDATE_WRAPPER_RAM:08X}", "size": len(update_code)},
        "diagnostic_render_wrapper": {
            "ram": f"0x{RENDER_WRAPPER_RAM:08X}",
            "size": len(render_code),
        } if render_code else None,
        "font_restore_wrapper": {
            "ram": f"0x{FONT_RESTORE_WRAPPER_RAM:08X}",
            "size": len(font_restore_code),
        } if font_restore_code else None,
        "data": {key: f"0x{value:08X}" for key, value in addresses.items()},
        "payload_end_ram": f"0x{end_ram:08X}",
        "payload_size": len(payload),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--font",
        type=Path,
        default=Path(r"G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf"),
    )
    parser.add_argument("--character", default="처")
    parser.add_argument(
        "--diagnostic-always",
        action="store_true",
        help="render replaced A plus stock B every frame to isolate renderer/texture failures",
    )
    parser.add_argument(
        "--font-reload-hook",
        action="store_true",
        help="restore the custom glyph only after stock uploads overlapping its VRAM cell",
    )
    parser.add_argument(
        "--timed-pair",
        action="store_true",
        help="draw replaced A plus stock B only during the group-3 timed subtitle gate",
    )
    parser.add_argument(
        "--scene-frame-direct",
        action="store_true",
        help="use JP game-owned scene source/frame fields; no custom persistent state",
    )
    parser.add_argument(
        "--scene-compare-hook",
        action="store_true",
        help="hook the USA-equivalent compare point 0x8003A88C instead of the epilogue",
    )
    parser.add_argument(
        "--base-name",
        default="Bushido Blade (Japan) [KO Official Glyph PoC v26]",
    )
    args = parser.parse_args()

    memory_map = json.loads(MEMORY_MAP_PATH.read_text(encoding="utf-8"))
    payload_policy = memory_map["policy"]["official_glyph_payload"]
    mapped_start = int(payload_policy["start"], 0)
    mapped_end = int(payload_policy["end_exclusive"], 0)
    if (mapped_start, mapped_end) != (CAVE_START_RAM, CAVE_END_RAM):
        raise ValueError("builder payload range disagrees with subtitle_memory_map.json")
    if not args.scene_frame_direct:
        raise ValueError(
            "custom persistent subtitle state is rejected; "
            "use --scene-frame-direct"
        )

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

    glyph_data, preview, glyph_meta = render_glyph(args.font, args.character)
    payload, payload_meta = make_payload(
        glyph_data,
        args.diagnostic_always,
        args.font_reload_hook,
        args.timed_pair,
        args.scene_frame_direct,
        args.scene_compare_hook,
        memory_map["target_scene_signature"],
    )
    patched_exe = bytearray(original_exe)

    expected_group = assemble("lw $ra, 0x4c($sp); lw $s2, 0x48($sp)", V00_GROUP_HOOK_RAM)
    if args.scene_compare_hook:
        update_hook_ram = V00_UPDATE_COMPARE_HOOK_RAM
        # keystone rejects absolute branch targets, so encode directly:
        # beq $a0, $v1, +3 words (0x8003A89C) ; srl $s0, $s0, 3
        expected_update = struct.pack("<II", 0x10830003, 0x001080C2)
    else:
        update_hook_ram = V00_GROUP_UPDATE_HOOK_RAM
        expected_update = assemble(
            "lw $ra, 0x3c($sp); lw $s2, 0x38($sp)", V00_GROUP_UPDATE_HOOK_RAM
        )
    group_offset = EXE_HEADER_SIZE + V00_GROUP_HOOK_RAM - EXE_RAM_BASE
    update_offset = EXE_HEADER_SIZE + update_hook_ram - EXE_RAM_BASE
    if original_exe[group_offset : group_offset + 8] != expected_group:
        raise ValueError("group hook expected-write mismatch")
    if original_exe[update_offset : update_offset + 8] != expected_update:
        raise ValueError("update hook expected-write mismatch")
    group_after = None
    if not args.scene_frame_direct:
        group_after = encode_jump(EVENT_WRAPPER_RAM, False) + b"\0\0\0\0"
        patched_exe[group_offset : group_offset + 8] = group_after
    patched_exe[update_offset : update_offset + 8] = encode_jump(UPDATE_WRAPPER_RAM, False) + b"\0\0\0\0"

    render_expected = encode_jump(RENDER_ORIGINAL_RAM, True)
    render_offset = EXE_HEADER_SIZE + RENDER_CALLSITE_RAM - EXE_RAM_BASE
    render_after = None
    if args.diagnostic_always:
        if original_exe[render_offset : render_offset + 4] != render_expected:
            raise ValueError("render callsite expected-write mismatch")
        render_after = encode_jump(RENDER_WRAPPER_RAM, True)
        patched_exe[render_offset : render_offset + 4] = render_after

    font_hook_expected = assemble(
        "lw $ra, 0x18($sp); lw $s1, 0x14($sp)",
        FONT_LOAD_RETURN_HOOK_RAM,
    )
    font_hook_offset = EXE_HEADER_SIZE + FONT_LOAD_RETURN_HOOK_RAM - EXE_RAM_BASE
    font_hook_after = None
    if args.font_reload_hook:
        if original_exe[font_hook_offset : font_hook_offset + 8] != font_hook_expected:
            raise ValueError("font LoadImage return expected-write mismatch")
        font_hook_after = encode_jump(FONT_RESTORE_WRAPPER_RAM, False) + b"\0\0\0\0"
        patched_exe[font_hook_offset : font_hook_offset + 8] = font_hook_after

    template_offset = EXE_HEADER_SIZE + A_TEMPLATE_RAM - EXE_RAM_BASE
    expected_template = bytes.fromhex("8080802cd8003878e7003e00d80fe70f")
    if original_exe[template_offset : template_offset + 16] != expected_template:
        raise ValueError("A glyph template expected-write mismatch")
    patched_template = bytearray(expected_template)
    struct.pack_into("<H", patched_template, 6, CLUT_ID)
    patched_exe[template_offset : template_offset + 16] = patched_template

    cave_offset = EXE_HEADER_SIZE + CAVE_START_RAM - EXE_RAM_BASE
    cave_end_offset = EXE_HEADER_SIZE + CAVE_END_RAM - EXE_RAM_BASE
    cave_source_hash = sha256_bytes(original_exe[cave_offset:cave_end_offset])
    patched_exe[cave_offset : cave_offset + len(payload)] = payload

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = args.output_dir / f"{args.base_name}.bin"
    output_cue = args.output_dir / f"{args.base_name}.cue"
    preview_path = args.output_dir / "glyph_preview.png"
    report_path = args.output_dir / "build_report.json"
    shutil.copyfile(args.image, output_bin)
    preview.resize((256, 256), Image.Resampling.NEAREST).save(preview_path)

    touched = []
    for index in range(len(patched_exe) // disc.LOGICAL_SECTOR_SIZE):
        chunk = patched_exe[index * disc.LOGICAL_SECTOR_SIZE : (index + 1) * disc.LOGICAL_SECTOR_SIZE]

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
        "gate_id": "BBJ-POC-OFFICIAL-GLYPH-001",
        "status": "static_build_passed_runtime_pending",
        "renderer": {
            "japanese_entry": f"0x{JP_TEXT_RENDERER_RAM:08X}",
            "japanese_glyph_table": f"0x{JP_GLYPH_TABLE_RAM:08X}",
            "reused_slot": "A",
            "slot_index": A_GLYPH_INDEX,
            "format": (
                "#X#Y#w#h#S#R#G#BAB"
                if args.diagnostic_always or args.timed_pair
                else "#X#Y#w#h#S#R#G#BA"
            ),
            "screen_position": [304, 190],
            "glyph_size": [15, 15],
            "advance_12_4": "0x1000",
            "modulation_rgb": "0x808080",
        },
        "glyph": {
            **glyph_meta,
            "vram_rect_words": list(GLYPH_RECT),
            "clut_rect": list(CLUT_RECT),
            "clut_id": f"0x{CLUT_ID:04X}",
            "palette": {"0": "transparent", "1": "opaque black", "2": "opaque white"},
        },
        "timing": {
            "mode": "jp_game_owned_scene_frame",
            "group": None,
            "scene_source_gp_offset": "0x304",
            "scene_frame_gp_offset": "0x314",
            "scene_signature": memory_map["target_scene_signature"],
            "start_frame": START_FRAME,
            "end_frame": END_FRAME,
            "source": "USA DATA7/V00.DAT first-kill subtitle record",
        },
        "injection": payload_meta,
        "expected_writes": {
            "group_hook": {
                "ram": f"0x{V00_GROUP_HOOK_RAM:08X}",
                "before": expected_group.hex(),
                "after": group_after.hex() if group_after is not None else None,
            },
            "update_hook": {
                "ram": f"0x{update_hook_ram:08X}",
                "before": expected_update.hex(),
                "after": (encode_jump(UPDATE_WRAPPER_RAM, False) + b"\0\0\0\0").hex(),
                "mode": "compare_point" if args.scene_compare_hook else "epilogue",
            },
            "A_template": {"ram": f"0x{A_TEMPLATE_RAM:08X}", "before": expected_template.hex(), "after": bytes(patched_template).hex()},
            "diagnostic_render_callsite": {
                "ram": f"0x{RENDER_CALLSITE_RAM:08X}",
                "before": render_expected.hex(),
                "after": render_after.hex(),
            } if render_after is not None else None,
            "font_load_return": {
                "ram": f"0x{FONT_LOAD_RETURN_HOOK_RAM:08X}",
                "before": font_hook_expected.hex(),
                "after": font_hook_after.hex(),
            } if font_hook_after is not None else None,
            "code_cave": {"ram_start": f"0x{CAVE_START_RAM:08X}", "ram_end": f"0x{CAVE_END_RAM:08X}", "source_sha256": cave_source_hash},
        },
        "static_checks": {
            "source_hash": True,
            "expected_writes": True,
            "mode2_form1_integrity": True,
            "disc_size_preserved": output_bin.stat().st_size == args.image.stat().st_size,
            "exe_roundtrip": True,
            "code_cave_fit": int(payload_meta["payload_end_ram"], 16) <= CAVE_END_RAM,
            "memory_map_enforced": True,
            "custom_persistent_state": False,
        },
        "output": {
            "bin": str(output_bin),
            "cue": str(output_cue),
            "bin_sha256": sha256_file(output_bin),
            "preview": str(preview_path),
        },
        "runtime": {
            "status": "pending",
            "required_observation": "target JP scene signature renders one clean Hangul glyph while game-owned scene frame is 60-179; no subtitle in other scenes",
        },
        "sector_changes": len(touched),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"output_cue={output_cue}")
    print(f"report={report_path}")
    print(f"payload={len(payload)}/{CAVE_END_RAM - CAVE_START_RAM}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
