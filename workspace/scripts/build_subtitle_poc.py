#!/usr/bin/env python3
"""Build a one-line runtime subtitle PoC for Bushido Blade (Japan)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

from keystone import KS_ARCH_MIPS, KS_MODE_LITTLE_ENDIAN, KS_MODE_MIPS32, Ks
from PIL import Image, ImageDraw, ImageFont

import inspect_psx_disc as disc
from build_options_poc import rebuild_mode2_form1_integrity


EXPECTED_BIN_SHA256 = "7D89B9254C5001AD7FC4F0C2FC3CDA9600417D075292C34D3B60AA3B600DBA96"
EXPECTED_EXE_SHA256 = "0164189A870BA87495A3C0010ADB85535AE5A798BBD0DD276B88BF24ECC3CEDA"
EXE_PATH = "SLPM_860.20"
EXE_RAM_BASE = 0x80010000
EXE_HEADER_SIZE = 0x800
APPEND_RAM = 0x800118B4
APPEND_FILE_OFFSET = EXE_HEADER_SIZE + APPEND_RAM - EXE_RAM_BASE
CAVE_END_RAM = 0x800120D0

VOICE_HOOK_RAM = 0x8003E798
VOICE_CONTINUE_RAM = 0x8003E7A0
KEY_ON_HOOK_RAM = 0x80070B9C
KEY_ON_CONTINUE_RAM = 0x80070BA4
STORY_VAB_HANDLE_RAM = 0x8013EDD8
# USA SCUS_941.80 calls its subtitle event selector at 0x800390B4 while
# initializing a V00 story event.  The normalized Japanese equivalent has no
# call and jumps directly onward here.  Hook this regional gap, not the SPU
# voice path, so the trigger follows the same scene/event lifecycle.
V00_EVENT_HOOK_RAM = 0x8003AB74
V00_EVENT_CONTINUE_RAM = 0x8003AB98
V00_FIRST_KILL_GROUP = 3
V00_UPDATE_HOOK_RAM = 0x8003B348
V00_UPDATE_ORIGINAL_RAM = 0x8003BAB0
V00_UPDATE_CONTINUE_RAM = 0x8003B350
# USA 0x800386B8 selects a subtitle group with a0=0,a1=s2.  Japanese deletes
# that call and starts its epilogue at 0x8003A1A0.  USA 0x80038DA4 performs
# the matching per-frame subtitle update; Japanese deletes it at the function
# epilogue beginning 0x8003A8A8.
V00_GROUP_HOOK_RAM = 0x8003A1A0
V00_GROUP_CONTINUE_RAM = 0x8003A1A8
V00_GROUP_UPDATE_HOOK_RAM = 0x8003A8A8
V00_GROUP_UPDATE_CONTINUE_RAM = 0x8003A8B0
RENDER_HOOK_RAM = 0x8004322C
RENDER_ORIGINAL_RAM = 0x80070838
RENDER_GLOBAL_CONTINUE_RAM = 0x80070840
ADD_PRIM_RAM = 0x8006A368
DRAW_OTAG_RAM = 0x80067B54
CURRENT_OT_POINTER = 0x800A3010
FRAME_PARITY = 0x800A2F3C
# State lives beside long-lived frame globals.  v12 proved that the apparent
# padding before the VAB array is active audio data and must not be used.
TIMER_RAM = 0x800A2E90
EVENT_SEEN_RAM = TIMER_RAM + 2
EVENT_MATCH_RAM = TIMER_RAM + 3
LAST_RENDER_FRAME_RAM = TIMER_RAM + 4
SCENE_FRAME_RAM = TIMER_RAM + 8
TARGET_SOUND_ID = 0x7C
DISPLAY_FRAMES = 100

EVENT_WRAPPER_RAM = APPEND_RAM
SCENE_UPDATE_WRAPPER_RAM = APPEND_RAM + 0x80
RENDER_WRAPPER_RAM = APPEND_RAM + 0x100
MULTI_RENDER_WRAPPER_RAM = RENDER_WRAPPER_RAM + 0x38
RECT_TABLE_RAM = APPEND_RAM + 0x400
PRIMITIVES_RAM = 0x80122000
PRIMITIVES_BYTES_LIMIT = 1952
DEFAULT_SUBTITLE_FONT = Path(
    r"G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def encode_jump(address: int, link: bool) -> bytes:
    opcode = 3 if link else 2
    return struct.pack("<I", (opcode << 26) | ((address >> 2) & 0x03FFFFFF))


def assemble(source: str, address: int) -> bytes:
    assembler = Ks(KS_ARCH_MIPS, KS_MODE_MIPS32 | KS_MODE_LITTLE_ENDIAN)
    encoded, _ = assembler.asm(source, address)
    return bytes(encoded)


def split_address(address: int) -> tuple[int, int]:
    return (address >> 16) & 0xFFFF, address & 0xFFFF


def split_address_signed_offset(address: int) -> tuple[int, int]:
    """Split an address for LUI plus a sign-extended load/store offset."""
    low = address & 0xFFFF
    high = ((address + (0x10000 if low & 0x8000 else 0)) >> 16) & 0xFFFF
    return high, low - 0x10000 if low & 0x8000 else low


def render_runs(
    text: str,
    font_path: Path,
    font_size: int,
    merge_gap: int = 0,
) -> tuple[list[tuple[int, int, int, int]], Image.Image, dict]:
    font = ImageFont.truetype(str(font_path), font_size)
    scratch = Image.new("L", (512, 64), 0)
    draw = ImageDraw.Draw(scratch)
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    width = bounds[2] - bounds[0] + 4
    height = bounds[3] - bounds[1] + 4
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.text((2 - bounds[0], 2 - bounds[1]), text, font=font, fill=255, stroke_width=1, stroke_fill=255)
    binary = mask.point(lambda value: 255 if value >= 96 else 0)

    scanline_runs: list[tuple[int, int, int]] = []
    pixels = binary.load()
    for y in range(binary.height):
        row_runs: list[tuple[int, int]] = []
        x = 0
        while x < binary.width:
            while x < binary.width and pixels[x, y] == 0:
                x += 1
            start = x
            while x < binary.width and pixels[x, y] != 0:
                x += 1
            if x > start:
                row_runs.append((start, x))
        merged_row: list[list[int]] = []
        for start, end in row_runs:
            if merged_row and start - merged_row[-1][1] <= merge_gap:
                merged_row[-1][1] = end
            else:
                merged_row.append([start, end])
        scanline_runs.extend((start, y, end - start) for start, end in merged_row)

    if merge_gap:
        binary = Image.new("L", binary.size, 0)
        merged_draw = ImageDraw.Draw(binary)
        for x, y, run_width in scanline_runs:
            merged_draw.rectangle((x, y, x + run_width - 1, y), fill=255)

    # Merge identical horizontal spans on adjacent rows. This keeps two full
    # primitive sets inside the runtime-verified executable cave.
    rectangles: list[tuple[int, int, int, int]] = []
    active: dict[tuple[int, int], tuple[int, int]] = {}
    for y in range(binary.height):
        row = {(x, run_width) for x, run_y, run_width in scanline_runs if run_y == y}
        for key, (start_y, last_y) in list(active.items()):
            if key not in row:
                rectangles.append((key[0], start_y, key[1], last_y - start_y + 1))
                del active[key]
        for key in row:
            if key in active:
                active[key] = (active[key][0], y)
            else:
                active[key] = (y, y)
    for key, (start_y, last_y) in active.items():
        rectangles.append((key[0], start_y, key[1], last_y - start_y + 1))
    return rectangles, binary, {
        "text": text,
        "width": width,
        "height": height,
        "scanline_run_count": len(scanline_runs),
        "rectangle_count": len(rectangles),
        "merge_gap": merge_gap,
    }


def tile_packet(x: int, y: int, width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    if not all(-32768 <= value <= 32767 for value in (x, y, width, height)):
        raise ValueError("tile coordinate outside signed 16-bit range")
    packet = bytearray(16)
    packet[3] = 3
    packet[4:8] = bytes((*rgb, 0x60))
    struct.pack_into("<hhhh", packet, 8, x, y, width, height)
    return bytes(packet)


def build_primitive_sets(
    runs: list[tuple[int, int, int, int]],
    image_meta: dict,
    include_background: bool = True,
) -> tuple[bytes, dict]:
    origin_x = (640 - image_meta["width"]) // 2
    # y=202 crossed the smaller draw area used by one alternating battle OT.
    origin_y = 174
    table = bytearray()
    for x, y, width, height in runs:
        if not (-128 <= x <= 127 and -128 <= y <= 127):
            raise ValueError("subtitle rectangle offset does not fit signed byte")
        if not (0 < width <= 255 and 0 < height <= 255):
            raise ValueError("subtitle rectangle size does not fit byte")
        table.extend(struct.pack("<bbBB", x, y, width, height))
    packet_count = len(runs) + (1 if include_background else 0)
    set_size = packet_count * 16
    if set_size * 2 > PRIMITIVES_BYTES_LIMIT:
        raise ValueError(
            f"double-buffered primitive data exceeds verified zero region: "
            f"{set_size * 2}/{PRIMITIVES_BYTES_LIMIT} bytes"
        )
    return bytes(table), {
        "screen_origin": {"x": origin_x, "y": origin_y},
        "image_width": image_meta["width"],
        "image_height": image_meta["height"],
        "packet_size": 16,
        "packets_per_set": packet_count,
        "rectangle_count": len(runs),
        "background_packet": include_background,
        "set_size": set_size,
        "double_buffered": True,
        "runtime_packet_ram": f"0x{PRIMITIVES_RAM:08X}",
        "runtime_packet_bytes": set_size * 2,
        "runtime_packet_bytes_limit": PRIMITIVES_BYTES_LIMIT,
        "runtime_region_zero_in_title_and_target_story_captures": True,
    }


def build_payload(
    primitive_meta: dict,
    primitive_data: bytes,
    target_sound_id: int,
    event_hook: str,
    tone_index: int,
    render_hook: str,
    display_frames: int,
    timeline_start_frame: int | None = None,
    timeline_end_frame: int | None = None,
    secondary_meta: dict | None = None,
    secondary_data: bytes | None = None,
    secondary_group: int | None = None,
    secondary_start_frame: int | None = None,
    secondary_end_frame: int | None = None,
    multi_dynamic_render: bool = True,
    force_shifted_render: bool = False,
    hide_background: bool = False,
) -> tuple[bytearray, dict]:
    render_wrapper_ram = (
        MULTI_RENDER_WRAPPER_RAM
        if event_hook == "v00-multi" or force_shifted_render
        else RENDER_WRAPPER_RAM
    )
    timer_hi, timer_lo = split_address_signed_offset(TIMER_RAM)
    last_frame_hi, last_frame_lo = split_address_signed_offset(
        LAST_RENDER_FRAME_RAM
    )
    scene_frame_hi, scene_frame_lo = split_address_signed_offset(SCENE_FRAME_RAM)
    ot_hi, ot_lo = split_address(CURRENT_OT_POINTER)
    parity_hi, parity_lo = split_address(FRAME_PARITY)
    prim_hi, prim_lo = split_address(PRIMITIVES_RAM)
    table_hi, table_lo = split_address(RECT_TABLE_RAM)
    secondary_table_ram = RECT_TABLE_RAM + len(primitive_data)
    secondary_table_hi, secondary_table_lo = split_address(secondary_table_ram)
    set_size = primitive_meta["set_size"]
    if event_hook == "v00-multi":
        if secondary_meta is None or secondary_data is None:
            raise ValueError("V00 multi hook requires a secondary subtitle asset")
        if secondary_group is None or secondary_start_frame is None:
            raise ValueError("V00 multi hook requires a secondary group and start frame")
        set_size = max(set_size, secondary_meta["set_size"])
        if set_size * 2 > PRIMITIVES_BYTES_LIMIT:
            raise ValueError("multi-subtitle primitive sets exceed verified runtime region")
    packet_count = primitive_meta["packets_per_set"]
    rectangle_count = primitive_meta["rectangle_count"]
    head_packet_index = packet_count - 1
    primitive_head0 = (PRIMITIVES_RAM + head_packet_index * 16) & 0x00FFFFFF
    primitive_head1 = (PRIMITIVES_RAM + set_size + head_packet_index * 16) & 0x00FFFFFF
    head0_hi, head0_lo = split_address(primitive_head0)
    head1_hi, head1_lo = split_address(primitive_head1)
    secondary_rectangle_count = (
        secondary_meta["rectangle_count"] if secondary_meta is not None else 0
    )
    secondary_head_packet_index = (
        secondary_meta["packets_per_set"] - 1 if secondary_meta is not None else 0
    )
    secondary_head0 = (PRIMITIVES_RAM + secondary_head_packet_index * 16) & 0x00FFFFFF
    secondary_head1 = (
        PRIMITIVES_RAM + set_size + secondary_head_packet_index * 16
    ) & 0x00FFFFFF
    secondary_head0_hi, secondary_head0_lo = split_address(secondary_head0)
    secondary_head1_hi, secondary_head1_lo = split_address(secondary_head1)
    origin_x = primitive_meta["screen_origin"]["x"]
    origin_y = primitive_meta["screen_origin"]["y"]
    background_width = 0 if hide_background else primitive_meta["image_width"] + 16
    background_height = 0 if hide_background else primitive_meta["image_height"] + 8
    if event_hook in ("v00-timeline", "v00-multi"):
        if timeline_start_frame is None or timeline_end_frame is None:
            raise ValueError("V00 timeline hook requires start and end frames")
        if not 0 <= timeline_start_frame < timeline_end_frame <= 0xFFFF:
            raise ValueError("invalid V00 subtitle timeline range")
    if event_hook == "v00-multi":
        if not 0 <= secondary_group <= 0xFF:
            raise ValueError("secondary V00 group must fit one byte")
        if not 0 <= secondary_start_frame <= 0xFFFF:
            raise ValueError("invalid secondary V00 start frame")
        if secondary_end_frame is not None and not (
            secondary_start_frame < secondary_end_frame <= 0xFFFF
        ):
            raise ValueError("invalid secondary V00 end frame")

    if event_hook == "common-request":
        event_source = f"""
            ori $t0, $zero, {target_sound_id}
            bne $a0, $t0, continue_original
            nop
            lui $t1, {timer_hi}
            ori $t2, $zero, {display_frames}
            sh $t2, {timer_lo}($t1)
            lui $t1, {last_frame_hi}
            addiu $t2, $zero, -1
            sw $t2, {last_frame_lo}($t1)
        continue_original:
            sll $v0, $a3, 1
            addu $v0, $v0, $a3
            j 0x{VOICE_CONTINUE_RAM:08x}
            nop
        """
    elif event_hook == "key-on":
        story_handle_hi, story_handle_lo = split_address_signed_offset(
            STORY_VAB_HANDLE_RAM
        )
        event_source = f"""
            lui $t1, {timer_hi}
            lbu $t2, {timer_lo + 2}($t1)
            addiu $t2, $t2, 1
            sb $t2, {timer_lo + 2}($t1)
            lui $t0, {story_handle_hi}
            lw $t0, {story_handle_lo}($t0)
            bne $a1, $t0, continue_original
            nop
            ori $t0, $zero, {tone_index}
            bne $a3, $t0, continue_original
            nop
            ori $t2, $zero, 1
            sb $t2, {timer_lo + 3}($t1)
            lui $t1, {timer_hi}
            ori $t2, $zero, {display_frames}
            sh $t2, {timer_lo}($t1)
            lui $t1, {last_frame_hi}
            addiu $t2, $zero, -1
            sw $t2, {last_frame_lo}($t1)
        continue_original:
            addiu $sp, $sp, -0x40
            sw $s0, 0x18($sp)
            j 0x{KEY_ON_CONTINUE_RAM:08x}
            nop
        """
    elif event_hook == "v00-scene":
        event_source = f"""
            lui $t1, {timer_hi}
            sltiu $t3, $a0, 8
            beq $t3, $zero, skip_seen_mask
            nop
            ori $t3, $zero, 1
            sllv $t3, $t3, $a0
            lbu $t4, {timer_lo + 2}($t1)
            or $t3, $t3, $t4
            sb $t3, {timer_lo + 2}($t1)
        skip_seen_mask:
            ori $t0, $zero, {target_sound_id}
            bne $a0, $t0, clear_match
            nop
            ori $t2, $zero, 1
            sb $t2, {timer_lo + 3}($t1)
            sh $zero, {timer_lo}($t1)
            lui $t1, {scene_frame_hi}
            sw $zero, {scene_frame_lo}($t1)
            lui $t1, {last_frame_hi}
            addiu $t2, $zero, -1
            sw $t2, {last_frame_lo}($t1)
            b continue_original
            nop
        clear_match:
            # A later scene selector must not cancel an already armed or
            # active subtitle. The next target selector overwrites the state.
            nop
        continue_original:
            j 0x{V00_EVENT_CONTINUE_RAM:08x}
            nop
        """
    elif event_hook == "v00-group":
        event_source = f"""
            lui $t1, {timer_hi}
            ori $t0, $zero, {target_sound_id}
            bne $s2, $t0, group_continue
            nop
            ori $t2, $zero, 1
            sb $t2, {timer_lo + 3}($t1)
            sh $zero, {timer_lo}($t1)
            lui $t1, {scene_frame_hi}
            sw $zero, {scene_frame_lo}($t1)
            lui $t1, {last_frame_hi}
            addiu $t2, $zero, -1
            sw $t2, {last_frame_lo}($t1)
        group_continue:
            lw $ra, 0x4c($sp)
            lw $s2, 0x48($sp)
            j 0x{V00_GROUP_CONTINUE_RAM:08x}
            nop
        """
    elif event_hook == "v00-timeline":
        event_source = f"""
            lui $t1, {timer_hi}
            ori $t0, $zero, {target_sound_id}
            beq $s2, $t0, timeline_arm
            nop
            sb $zero, {timer_lo + 3}($t1)
            sh $zero, {timer_lo}($t1)
            lui $t1, {scene_frame_hi}
            sw $zero, {scene_frame_lo}($t1)
            b timeline_group_continue
            nop
        timeline_arm:
            ori $t2, $zero, 1
            sb $t2, {timer_lo + 3}($t1)
            sh $zero, {timer_lo}($t1)
            lui $t1, {scene_frame_hi}
            sw $zero, {scene_frame_lo}($t1)
            lui $t1, {last_frame_hi}
            addiu $t2, $zero, -1
            sw $t2, {last_frame_lo}($t1)
        timeline_group_continue:
            lw $ra, 0x4c($sp)
            lw $s2, 0x48($sp)
            j 0x{V00_GROUP_CONTINUE_RAM:08x}
            nop
        """
    elif event_hook == "v00-multi":
        event_source = f"""
            lui $t1, {timer_hi}
            ori $t0, $zero, {target_sound_id}
            beq $s2, $t0, multi_timeline_arm
            ori $t0, $zero, {secondary_group}
            beq $s2, $t0, multi_timeline_arm
            nop
            sb $zero, {timer_lo + 2}($t1)
            sb $zero, {timer_lo + 3}($t1)
            lui $t1, {scene_frame_hi}
            sw $zero, {scene_frame_lo}($t1)
            b multi_group_continue
            nop
        multi_timeline_arm:
            sb $s2, {timer_lo + 2}($t1)
            ori $t2, $zero, 1
            sb $t2, {timer_lo + 3}($t1)
            lui $t1, {scene_frame_hi}
            sw $zero, {scene_frame_lo}($t1)
            lui $t1, {last_frame_hi}
            addiu $t2, $zero, -1
            sw $t2, {last_frame_lo}($t1)
        multi_group_continue:
            lw $ra, 0x4c($sp)
            lw $s2, 0x48($sp)
            j 0x{V00_GROUP_CONTINUE_RAM:08x}
            nop
        """
    else:
        raise ValueError(f"unsupported event hook: {event_hook}")
    event_code = assemble(event_source, EVENT_WRAPPER_RAM)
    if len(event_code) > SCENE_UPDATE_WRAPPER_RAM - EVENT_WRAPPER_RAM:
        raise ValueError("event wrapper exceeds reserved area")
    scene_update_code = b""
    if event_hook == "v00-scene":
        scene_update_source = f"""
            addiu $sp, $sp, -0x18
            sw $ra, 0x10($sp)
            jal 0x{V00_UPDATE_ORIGINAL_RAM:08x}
            nop
            lui $t0, {timer_hi}
            lbu $t1, {timer_lo + 3}($t0)
            nop
            ori $t3, $zero, 1
            bne $t1, $t3, scene_update_return
            nop
            lui $t0, {scene_frame_hi}
            lw $t2, {scene_frame_lo}($t0)
            nop
            addiu $t2, $t2, 1
            sw $t2, {scene_frame_lo}($t0)
            ori $t3, $zero, 60
            bne $t2, $t3, scene_update_return
            nop
            lui $t0, {timer_hi}
            ori $t2, $zero, {display_frames}
            sh $t2, {timer_lo}($t0)
            ori $t2, $zero, 2
            sb $t2, {timer_lo + 3}($t0)
        scene_update_return:
            lw $ra, 0x10($sp)
            addiu $sp, $sp, 0x18
            jr $ra
            nop
        """
        scene_update_code = assemble(scene_update_source, SCENE_UPDATE_WRAPPER_RAM)
        if len(scene_update_code) > render_wrapper_ram - SCENE_UPDATE_WRAPPER_RAM:
            raise ValueError("scene update wrapper exceeds reserved area")
    elif event_hook == "v00-group":
        scene_update_source = f"""
            lui $t0, {timer_hi}
            lbu $t1, {timer_lo + 3}($t0)
            nop
            ori $t3, $zero, 1
            bne $t1, $t3, group_update_return
            nop
            lui $t0, {scene_frame_hi}
            lw $t2, {scene_frame_lo}($t0)
            nop
            addiu $t2, $t2, 1
            sw $t2, {scene_frame_lo}($t0)
            ori $t3, $zero, 60
            bne $t2, $t3, group_update_return
            nop
            lui $t0, {timer_hi}
            ori $t2, $zero, {display_frames}
            sh $t2, {timer_lo}($t0)
            ori $t2, $zero, 2
            sb $t2, {timer_lo + 3}($t0)
        group_update_return:
            lw $ra, 0x3c($sp)
            lw $s2, 0x38($sp)
            j 0x{V00_GROUP_UPDATE_CONTINUE_RAM:08x}
            nop
        """
        scene_update_code = assemble(scene_update_source, SCENE_UPDATE_WRAPPER_RAM)
        if len(scene_update_code) > render_wrapper_ram - SCENE_UPDATE_WRAPPER_RAM:
            raise ValueError("group update wrapper exceeds reserved area")
    elif event_hook == "v00-timeline":
        scene_update_source = f"""
            lui $t0, {timer_hi}
            lbu $t1, {timer_lo + 3}($t0)
            nop
            beq $t1, $zero, timeline_update_return
            lui $t4, {scene_frame_hi}
            lw $t2, {scene_frame_lo}($t4)
            nop
            # The USA routine draws inside its update function and advances
            # the descriptor afterward.  Our GPU packets are submitted later
            # at DrawOTag, so advance first and expose the state for that same
            # completed frame: visible while new_frame is [start, end).
            addiu $t2, $t2, 1
            sw $t2, {scene_frame_lo}($t4)
            ori $t3, $zero, {timeline_start_frame}
            sltu $t5, $t2, $t3
            bne $t5, $zero, timeline_update_return
            ori $t3, $zero, {timeline_end_frame}
            sltu $t5, $t2, $t3
            beq $t5, $zero, timeline_hide
            ori $t3, $zero, 2
            sb $t3, {timer_lo + 3}($t0)
            b timeline_update_return
        timeline_hide:
            sb $zero, {timer_lo + 3}($t0)
        timeline_update_return:
            lw $ra, 0x3c($sp)
            lw $s2, 0x38($sp)
            j 0x{V00_GROUP_UPDATE_CONTINUE_RAM:08x}
        """
        scene_update_code = assemble(scene_update_source, SCENE_UPDATE_WRAPPER_RAM)
        if len(scene_update_code) > render_wrapper_ram - SCENE_UPDATE_WRAPPER_RAM:
            raise ValueError("timeline update wrapper exceeds reserved area")
    elif event_hook == "v00-multi":
        secondary_end_logic = (
            f"""
            ori $t3, $zero, {secondary_end_frame}
            sltu $t5, $t2, $t3
            beq $t5, $zero, multi_timeline_hide
            nop
            """
            if secondary_end_frame is not None
            else ""
        )
        scene_update_source = f"""
            lui $t0, {timer_hi}
            lbu $t1, {timer_lo + 3}($t0)
            nop
            beq $t1, $zero, multi_update_return
            lui $t4, {scene_frame_hi}
            lw $t2, {scene_frame_lo}($t4)
            nop
            addiu $t2, $t2, 1
            sw $t2, {scene_frame_lo}($t4)
            lbu $t6, {timer_lo + 2}($t0)
            ori $t7, $zero, {target_sound_id}
            beq $t6, $t7, multi_primary_timing
            ori $t7, $zero, {secondary_group}
            bne $t6, $t7, multi_timeline_hide
            nop
            ori $t3, $zero, {secondary_start_frame}
            sltu $t5, $t2, $t3
            bne $t5, $zero, multi_update_return
            nop
            {secondary_end_logic}
            b multi_timeline_show
            nop
        multi_primary_timing:
            ori $t3, $zero, {timeline_start_frame}
            sltu $t5, $t2, $t3
            bne $t5, $zero, multi_update_return
            ori $t3, $zero, {timeline_end_frame}
            sltu $t5, $t2, $t3
            beq $t5, $zero, multi_timeline_hide
            nop
        multi_timeline_show:
            ori $t3, $zero, 2
            sb $t3, {timer_lo + 3}($t0)
            b multi_update_return
            nop
        multi_timeline_hide:
            sb $zero, {timer_lo + 3}($t0)
        multi_update_return:
            lw $ra, 0x3c($sp)
            lw $s2, 0x38($sp)
            j 0x{V00_GROUP_UPDATE_CONTINUE_RAM:08x}
            nop
        """
        scene_update_code = assemble(scene_update_source, SCENE_UPDATE_WRAPPER_RAM)
        if len(scene_update_code) > render_wrapper_ram - SCENE_UPDATE_WRAPPER_RAM:
            raise ValueError(
                "multi timeline update wrapper exceeds reserved area: "
                f"{len(scene_update_code)}/{render_wrapper_ram - SCENE_UPDATE_WRAPPER_RAM}"
            )
    state_start = TIMER_RAM
    state_end = SCENE_FRAME_RAM + 4
    wrapper_ranges = (
        (EVENT_WRAPPER_RAM, EVENT_WRAPPER_RAM + len(event_code)),
        (SCENE_UPDATE_WRAPPER_RAM, SCENE_UPDATE_WRAPPER_RAM + len(scene_update_code)),
        (render_wrapper_ram, RECT_TABLE_RAM),
    )
    if any(state_start < end and state_end > start for start, end in wrapper_ranges):
        raise ValueError("persistent subtitle state overlaps a wrapper")

    if (
        render_hook == "drawotag-terminal"
        and event_hook == "v00-multi"
        and multi_dynamic_render
    ):
        secondary_origin_x = secondary_meta["screen_origin"]["x"]
        secondary_origin_y = secondary_meta["screen_origin"]["y"]
        render_source = f"""
            addiu $sp, $sp, -0x18
            sw $a0, 0x14($sp)
            lui $t3, {timer_hi}
            lbu $t4, {timer_lo + 1}($t3)
            ori $t7, $zero, 0x00a5
            beq $t4, $t7, multi_state_initialized
            lbu $a1, {timer_lo}($t3)
            sb $zero, {timer_lo}($t3)
            b multi_continue_original_frame
            sb $t7, {timer_lo + 1}($t3)
        multi_state_initialized:
            lbu $t2, {timer_lo + 3}($t3)
            bne $a1, $zero, multi_find_ot_terminal
            ori $t4, $zero, 2
            bne $t2, $t4, multi_continue_original_frame
            nop
        multi_find_ot_terminal:
            move $t5, $a0
            lui $a2, 0x00ff
            ori $a2, $a2, 0xffff
        multi_sanitize_ot_loop:
            lw $t4, 0($t5)
            nop
            and $t6, $t4, $a2
            beq $a1, $zero, multi_check_ot_terminal
            nop
            lui $t7, {head0_hi}
            ori $t7, $t7, {head0_lo}
            beq $t6, $t7, multi_detach_old_subtitle
            nop
            lui $t7, {head1_hi}
            ori $t7, $t7, {head1_lo}
            beq $t6, $t7, multi_detach_old_subtitle
            nop
            lui $t7, {secondary_head0_hi}
            ori $t7, $t7, {secondary_head0_lo}
            beq $t6, $t7, multi_detach_old_subtitle
            nop
            lui $t7, {secondary_head1_hi}
            ori $t7, $t7, {secondary_head1_lo}
            beq $t6, $t7, multi_detach_old_subtitle
            nop
        multi_check_ot_terminal:
            beq $t6, $a2, multi_ot_terminal_ready
            nop
            lui $t7, 0x8000
            or $t5, $t6, $t7
            b multi_sanitize_ot_loop
            nop
        multi_detach_old_subtitle:
            lui $t7, 0xff00
            and $t4, $t4, $t7
            or $t4, $t4, $a2
            sw $t4, 0($t5)
        multi_ot_terminal_ready:
            sb $zero, {timer_lo}($t3)
            sw $t5, 0x10($sp)
            lbu $t2, {timer_lo + 3}($t3)
            ori $t4, $zero, 2
            bne $t2, $t4, multi_continue_original_frame
            lbu $t2, {timer_lo + 2}($t3)
            ori $t4, $zero, {secondary_group}
            beq $t2, $t4, multi_select_secondary
            ori $t4, $zero, {target_sound_id}
            bne $t2, $t4, multi_continue_original_frame
            nop
            lui $t9, {table_hi}
            ori $t9, $t9, {table_lo}
            ori $a2, $zero, {rectangle_count}
            ori $t0, $zero, {origin_x}
            ori $t1, $zero, {origin_y}
            ori $t3, $zero, {primitive_meta['image_width'] + 16}
            sw $t3, 0x0c($sp)
            ori $t3, $zero, {primitive_meta['image_height'] + 8}
            sw $t3, 0x08($sp)
            b multi_asset_selected
            nop
        multi_select_secondary:
            lui $t9, {secondary_table_hi}
            ori $t9, $t9, {secondary_table_lo}
            ori $a2, $zero, {secondary_rectangle_count}
            ori $t0, $zero, {secondary_origin_x}
            ori $t1, $zero, {secondary_origin_y}
            ori $t3, $zero, {secondary_meta['image_width'] + 16}
            sw $t3, 0x0c($sp)
            ori $t3, $zero, {secondary_meta['image_height'] + 8}
            sw $t3, 0x08($sp)
        multi_asset_selected:
            lui $t2, {parity_hi}
            lw $t2, {parity_lo}($t2)
            nop
            andi $t2, $t2, 1
            ori $t3, $zero, {set_size}
            multu $t2, $t3
            mflo $t2
            lui $t8, {prim_hi}
            ori $t8, $t8, {prim_lo}
            addu $t8, $t8, $t2
        multi_terminal_render_loop:
            lui $t4, 0x0300
            sw $t4, 0($t8)
            lui $t4, 0x60ff
            ori $t4, $t4, 0xffff
            sw $t4, 4($t8)
            lb $t4, 0($t9)
            lb $t5, 1($t9)
            lbu $t6, 2($t9)
            lbu $t7, 3($t9)
            addu $t4, $t4, $t0
            addu $t5, $t5, $t1
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            lw $a0, 0x10($sp)
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            lui $v1, 0xff00
            lw $v0, 0($t8)
            lw $t4, 0($a0)
            and $v0, $v0, $v1
            and $t4, $t4, $a3
            or $v0, $v0, $t4
            sw $v0, 0($t8)
            lw $v0, 0($a0)
            and $t4, $t8, $a3
            and $v0, $v0, $v1
            or $v0, $v0, $t4
            sw $v0, 0($a0)
            addiu $t8, $t8, 16
            addiu $t9, $t9, 4
            addiu $a2, $a2, -1
            bne $a2, $zero, multi_terminal_render_loop
            nop
            lui $t4, 0x0300
            sw $t4, 0($t8)
            lui $t4, 0x6000
            sw $t4, 4($t8)
            addiu $t4, $t0, -8
            addiu $t5, $t1, -4
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            lw $t6, 0x0c($sp)
            lw $t7, 0x08($sp)
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            lw $a0, 0x10($sp)
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            lui $v1, 0xff00
            lw $v0, 0($t8)
            lw $t4, 0($a0)
            and $v0, $v0, $v1
            and $t4, $t4, $a3
            or $v0, $v0, $t4
            sw $v0, 0($t8)
            lw $v0, 0($a0)
            and $t4, $t8, $a3
            and $v0, $v0, $v1
            or $v0, $v0, $t4
            sw $v0, 0($a0)
            lui $t3, {timer_hi}
            ori $t2, $zero, 1
            sb $t2, {timer_lo}($t3)
        multi_continue_original_frame:
            lw $a0, 0x14($sp)
            addiu $sp, $sp, 0x18
            lui $v0, 0x8009
            lbu $v0, -0x5b4e($v0)
            j 0x{DRAW_OTAG_RAM + 8:08x}
            nop
        """
    elif render_hook == "drawotag-terminal":
        if event_hook in ("v00-timeline", "v00-multi"):
            lifetime_source = f"""
            lui $t0, {timer_hi}
            lbu $t2, {timer_lo + 3}($t0)
            nop
            ori $t3, $zero, 2
            bne $t2, $t3, continue_original_frame
            nop
            lui $t2, {parity_hi}
            lw $t2, {parity_lo}($t2)
            nop
            andi $t2, $t2, 1
            """
        else:
            lifetime_source = f"""
            lui $t0, {timer_hi}
            lbu $t2, {timer_lo + 3}($t0)
            nop
            ori $t3, $zero, 2
            bne $t2, $t3, continue_original_frame
            nop
            lhu $t1, {timer_lo}($t0)
            nop
            beq $t1, $zero, continue_original_frame
            nop
            lui $t2, {parity_hi}
            lw $t2, {parity_lo}($t2)
            nop
            andi $t2, $t2, 1
            # This hook is reached once per submitted display frame.  The
            # former parity-change guard could remain constant on this game's
            # post-fight path, leaving the timer at 179 forever.
            addiu $t1, $t1, -1
            sh $t1, {timer_lo}($t0)
            bne $t1, $zero, subtitle_lifetime_active
            nop
            sb $zero, {timer_lo + 3}($t0)
        subtitle_lifetime_active:
            """
        if hide_background:
            background_packet_source = f"""
            lui $t0, {timer_hi}
            ori $t1, $zero, 1
            sb $t1, {timer_lo}($t0)
            b continue_original_frame
            nop
            """
        else:
            background_packet_source = f"""
            lui $t4, 0x0300
            sw $t4, 0($t8)
            lui $t4, 0x6000
            sw $t4, 4($t8)
            ori $t4, $zero, {origin_x - 8}
            ori $t5, $zero, {origin_y - 4}
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            ori $t6, $zero, {background_width}
            ori $t7, $zero, {background_height}
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            lw $a0, 0x08($sp)
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            lui $v1, 0xff00
            lw $v0, 0($t8)
            lw $t4, 0($a0)
            and $v0, $v0, $v1
            and $t4, $t4, $a3
            or $v0, $v0, $t4
            sw $v0, 0($t8)
            lw $v0, 0($a0)
            and $t4, $t8, $a3
            and $v0, $v0, $v1
            or $v0, $v0, $t4
            sw $v0, 0($a0)
            lui $t0, {timer_hi}
            ori $t1, $zero, 1
            sb $t1, {timer_lo}($t0)
            """
        render_source = f"""
            addiu $sp, $sp, -0x10
            sw $a0, 0x0c($sp)
            # The primitive head can coincidentally match a legitimate boot OT
            # link.  Initialize once, then inspect head addresses only after
            # this wrapper has actually attached a subtitle chain.
            lui $t0, {timer_hi}
            lbu $t1, {timer_lo + 1}($t0)
            ori $t2, $zero, 0x00a5
            beq $t1, $t2, subtitle_attach_state_ready
            nop
            sb $zero, {timer_lo}($t0)
            sb $t2, {timer_lo + 1}($t0)
            b continue_original_frame
            nop
        subtitle_attach_state_ready:
            lbu $t1, {timer_lo}($t0)
            move $t5, $a0
            lui $a2, 0x00ff
            ori $a2, $a2, 0xffff
        sanitize_ot_loop:
            lw $t4, 0($t5)
            nop
            and $t6, $t4, $a2
            beq $t1, $zero, sanitize_check_terminal
            nop
            lui $t7, {head0_hi}
            ori $t7, $t7, {head0_lo}
            beq $t6, $t7, detach_old_subtitle
            nop
            lui $t7, {head1_hi}
            ori $t7, $t7, {head1_lo}
            beq $t6, $t7, detach_old_subtitle
            nop
        sanitize_check_terminal:
            beq $t6, $a2, ot_terminal_ready
            nop
            lui $t7, 0x8000
            or $t5, $t6, $t7
            b sanitize_ot_loop
            nop
        detach_old_subtitle:
            lui $t7, 0xff00
            and $t4, $t4, $t7
            or $t4, $t4, $a2
            sw $t4, 0($t5)
        ot_terminal_ready:
            lui $t0, {timer_hi}
            sb $zero, {timer_lo}($t0)
            sw $t5, 0x08($sp)
            {lifetime_source}
            ori $t3, $zero, {set_size}
            multu $t2, $t3
            mflo $t2
            lui $t8, {prim_hi}
            ori $t8, $t8, {prim_lo}
            addu $t8, $t8, $t2
            lui $t9, {table_hi}
            ori $t9, $t9, {table_lo}
            ori $a2, $zero, {rectangle_count}
        terminal_render_loop:
            lui $t4, 0x0300
            sw $t4, 0($t8)
            lui $t4, 0x60ff
            ori $t4, $t4, 0xffff
            sw $t4, 4($t8)
            lb $t4, 0($t9)
            lb $t5, 1($t9)
            lbu $t6, 2($t9)
            lbu $t7, 3($t9)
            addiu $t4, $t4, {origin_x}
            addiu $t5, $t5, {origin_y}
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            lw $a0, 0x08($sp)
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            lui $v1, 0xff00
            lw $v0, 0($t8)
            lw $t4, 0($a0)
            and $v0, $v0, $v1
            and $t4, $t4, $a3
            or $v0, $v0, $t4
            sw $v0, 0($t8)
            lw $v0, 0($a0)
            and $t4, $t8, $a3
            and $v0, $v0, $v1
            or $v0, $v0, $t4
            sw $v0, 0($a0)
            addiu $t8, $t8, 16
            addiu $t9, $t9, 4
            addiu $a2, $a2, -1
            bne $a2, $zero, terminal_render_loop
            nop
            {background_packet_source}
        continue_original_frame:
            lw $a0, 0x0c($sp)
            addiu $sp, $sp, 0x10
            lui $v0, 0x8009
            lbu $v0, -0x5b4e($v0)
            j 0x{DRAW_OTAG_RAM + 8:08x}
            nop
        """
    elif render_hook == "drawotag":
        render_source = f"""
            addiu $sp, $sp, -0x28
            sw $ra, 0x24($sp)
            sw $s0, 0x20($sp)
            sw $s1, 0x1c($sp)
            move $s0, $a0

            # Submit the game's ordering table first.
            lui $v0, 0x8009
            lbu $v0, -0x5b4e($v0)
            nop
            jal 0x{DRAW_OTAG_RAM + 8:08x}
            move $a0, $s0

            lui $t0, {timer_hi}
            lhu $t1, {timer_lo}($t0)
            nop
            beq $t1, $zero, drawotag_wrapper_return
            nop

            # Decrement once per display parity, but redraw after every game
            # OT so a later foreground/UI OT cannot cover the subtitle.
            lui $t2, {parity_hi}
            lw $t2, {parity_lo}($t2)
            nop
            andi $t2, $t2, 1
            lui $t3, {last_frame_hi}
            lw $t4, {last_frame_lo}($t3)
            nop
            beq $t2, $t4, lifetime_done
            nop
            sw $t2, {last_frame_lo}($t3)
            addiu $t1, $t1, -1
            sh $t1, {timer_lo}($t0)
        lifetime_done:
            ori $t3, $zero, {set_size}
            multu $t2, $t3
            mflo $t2
            lui $s1, {prim_hi}
            ori $s1, $s1, {prim_lo}
            addu $s1, $s1, $t2
            move $t8, $s1
            lui $t9, {table_hi}
            ori $t9, $t9, {table_lo}
            ori $a2, $zero, {rectangle_count}

        drawotag_text_loop:
            lui $t4, 0x60ff
            ori $t4, $t4, 0xffff
            sw $t4, 4($t8)
            lb $t4, 0($t9)
            lb $t5, 1($t9)
            lbu $t6, 2($t9)
            lbu $t7, 3($t9)
            addiu $t4, $t4, {origin_x}
            addiu $t5, $t5, {origin_y}
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            addiu $a2, $a2, -1
            beq $a2, $zero, drawotag_text_terminal
            nop
            addiu $v0, $t8, 16
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            and $v0, $v0, $a3
            lui $v1, 0x0300
            or $v1, $v1, $v0
            sw $v1, 0($t8)
            addiu $t8, $t8, 16
            addiu $t9, $t9, 4
            b drawotag_text_loop
            nop

        drawotag_text_terminal:
            lui $v0, 0x03ff
            ori $v0, $v0, 0xffff
            sw $v0, 0($t8)
            addiu $t8, $t8, 16

            # Background is the standalone OT head and links to the text.
            lui $t4, 0x0300
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            and $v0, $s1, $a3
            or $t4, $t4, $v0
            sw $t4, 0($t8)
            lui $t4, 0x6000
            sw $t4, 4($t8)
            ori $t4, $zero, {origin_x - 8}
            ori $t5, $zero, {origin_y - 4}
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            ori $t6, $zero, {background_width}
            ori $t7, $zero, {background_height}
            sh $t6, 12($t8)
            sh $t7, 14($t8)

            # Submit the subtitle as an independent OT after the current game
            # OT. Repeating this leaves it above the final foreground/UI OT.
            lui $v0, 0x8009
            lbu $v0, -0x5b4e($v0)
            nop
            jal 0x{DRAW_OTAG_RAM + 8:08x}
            move $a0, $t8

        drawotag_wrapper_return:
            lw $s1, 0x1c($sp)
            lw $s0, 0x20($sp)
            lw $ra, 0x24($sp)
            addiu $sp, $sp, 0x28
            jr $ra
            nop
        """
    elif render_hook == "addprim":
        render_source = f"""
            addiu $sp, $sp, -0x18
            sw $a0, 0x10($sp)
            sw $a1, 0x14($sp)
            lui $t0, {timer_hi}
            lhu $t1, {timer_lo}($t0)
            beq $t1, $zero, continue_original_frame
            nop
            lui $t2, {parity_hi}
            lw $t2, {parity_lo}($t2)
            lui $t3, {last_frame_hi}
            lw $t4, {last_frame_lo}($t3)
            beq $t2, $t4, continue_original_frame
            nop
            sw $t2, {last_frame_lo}($t3)
            addiu $t1, $t1, -1
            sh $t1, {timer_lo}($t0)
            andi $t2, $t2, 1
            ori $t3, $zero, {set_size}
            multu $t2, $t3
            mflo $t2
            lui $t8, {prim_hi}
            ori $t8, $t8, {prim_lo}
            addu $t8, $t8, $t2
            lui $t9, {table_hi}
            ori $t9, $t9, {table_lo}
            ori $a2, $zero, {rectangle_count}
        addprim_render_loop:
            lui $t4, 0x0300
            sw $t4, 0($t8)
            lui $t4, 0x60ff
            ori $t4, $t4, 0xffff
            sw $t4, 4($t8)
            lb $t4, 0($t9)
            lb $t5, 1($t9)
            lbu $t6, 2($t9)
            lbu $t7, 3($t9)
            addiu $t4, $t4, {origin_x}
            addiu $t5, $t5, {origin_y}
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            lw $a0, 0x10($sp)
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            lui $v1, 0xff00
            lw $v0, 0($t8)
            lw $t4, 0($a0)
            and $v0, $v0, $v1
            and $t4, $t4, $a3
            or $v0, $v0, $t4
            sw $v0, 0($t8)
            lw $v0, 0($a0)
            and $t4, $t8, $a3
            and $v0, $v0, $v1
            or $v0, $v0, $t4
            sw $v0, 0($a0)
            addiu $t8, $t8, 16
            addiu $t9, $t9, 4
            addiu $a2, $a2, -1
            bne $a2, $zero, addprim_render_loop
            nop
            lui $t4, 0x0300
            sw $t4, 0($t8)
            lui $t4, 0x6000
            sw $t4, 4($t8)
            ori $t4, $zero, {origin_x - 8}
            ori $t5, $zero, {origin_y - 4}
            sh $t4, 8($t8)
            sh $t5, 10($t8)
            ori $t6, $zero, {primitive_meta['image_width'] + 16}
            ori $t7, $zero, {primitive_meta['image_height'] + 8}
            sh $t6, 12($t8)
            sh $t7, 14($t8)
            lw $a0, 0x10($sp)
            lui $a3, 0x00ff
            ori $a3, $a3, 0xffff
            lui $v1, 0xff00
            lw $v0, 0($t8)
            lw $t4, 0($a0)
            and $v0, $v0, $v1
            and $t4, $t4, $a3
            or $v0, $v0, $t4
            sw $v0, 0($t8)
            lw $v0, 0($a0)
            and $t4, $t8, $a3
            and $v0, $v0, $v1
            or $v0, $v0, $t4
            sw $v0, 0($a0)
        continue_original_frame:
            lw $a0, 0x10($sp)
            lw $a1, 0x14($sp)
            addiu $sp, $sp, 0x18
            lui $a2, 0x00ff
            ori $a2, $a2, 0xffff
            j 0x{ADD_PRIM_RAM + 8:08x}
            nop
        """
    elif render_hook == "callsite":
        render_tail = f"""
            ori $a0, $zero, 1
            jal 0x{RENDER_ORIGINAL_RAM:08x}
            nop
            lw $ra, 0x20($sp)
            lw $s3, 0x1c($sp)
            lw $s2, 0x18($sp)
            lw $s1, 0x14($sp)
            lw $s0, 0x10($sp)
            addiu $sp, $sp, 0x28
            jr $ra
            nop
        """
    elif render_hook == "global":
        render_tail = f"""
            lw $ra, 0x20($sp)
            lw $s3, 0x1c($sp)
            lw $s2, 0x18($sp)
            lw $s1, 0x14($sp)
            lw $s0, 0x10($sp)
            lw $a0, 0x24($sp)
            addiu $sp, $sp, 0x28
            lui $v0, 0x8009
            lw $v0, 0x0ea4($v0)
            j 0x{RENDER_GLOBAL_CONTINUE_RAM:08x}
            nop
        """
    elif render_hook != "addprim":
        raise ValueError(f"unsupported render hook: {render_hook}")

    if render_hook not in ("addprim", "drawotag", "drawotag-terminal"):
        render_source = f"""
        addiu $sp, $sp, -0x28
        sw $ra, 0x20($sp)
        sw $a0, 0x24($sp)
        sw $s0, 0x10($sp)
        sw $s1, 0x14($sp)
        sw $s2, 0x18($sp)
        sw $s3, 0x1c($sp)
        lui $t0, {timer_hi}
        lhu $t1, {timer_lo}($t0)
        beq $t1, $zero, call_original_frame
        nop
        addiu $t1, $t1, -1
        sh $t1, {timer_lo}($t0)
        lui $t2, {parity_hi}
        lw $t2, {parity_lo}($t2)
        andi $t2, $t2, 1
        ori $t3, $zero, {set_size}
        multu $t2, $t3
        mflo $t2
        lui $s0, {prim_hi}
        ori $s0, $s0, {prim_lo}
        addu $s0, $s0, $t2
        lui $s2, {table_hi}
        ori $s2, $s2, {table_lo}
        ori $s1, $zero, {rectangle_count}
    render_loop:
        lui $t4, 0x0300
        sw $t4, 0($s0)
        lui $t4, 0x60ff
        ori $t4, $t4, 0xffff
        sw $t4, 4($s0)
        lb $t4, 0($s2)
        lb $t5, 1($s2)
        lbu $t6, 2($s2)
        lbu $t7, 3($s2)
        addiu $t4, $t4, {origin_x}
        addiu $t5, $t5, {origin_y}
        sh $t4, 8($s0)
        sh $t5, 10($s0)
        sh $t6, 12($s0)
        sh $t7, 14($s0)
        lui $a0, {ot_hi}
        lw $a0, {ot_lo}($a0)
        move $a1, $s0
        jal 0x{ADD_PRIM_RAM:08x}
        nop
        addiu $s0, $s0, 16
        addiu $s2, $s2, 4
        addiu $s1, $s1, -1
        bne $s1, $zero, render_loop
        nop
        lui $t4, 0x0300
        sw $t4, 0($s0)
        lui $t4, 0x6000
        sw $t4, 4($s0)
        ori $t4, $zero, {origin_x - 8}
        ori $t5, $zero, {origin_y - 4}
        sh $t4, 8($s0)
        sh $t5, 10($s0)
        ori $t6, $zero, {primitive_meta['image_width'] + 16}
        ori $t7, $zero, {primitive_meta['image_height'] + 8}
        sh $t6, 12($s0)
        sh $t7, 14($s0)
        lui $a0, {ot_hi}
        lw $a0, {ot_lo}($a0)
        move $a1, $s0
        jal 0x{ADD_PRIM_RAM:08x}
        nop
    call_original_frame:
        {render_tail}
        """
    render_code = assemble(render_source, render_wrapper_ram)
    if render_wrapper_ram + len(render_code) > RECT_TABLE_RAM:
        raise ValueError(
            "render wrapper exceeds reserved area: "
            f"{len(render_code)}/{RECT_TABLE_RAM - render_wrapper_ram}"
        )

    # Persistent state is external to the executable payload and is zero in
    # every verified baseline capture.  Only code and rectangle data extend
    # the diagnostic-string cave.
    table_data = primitive_data + (secondary_data or b"")
    end_ram = RECT_TABLE_RAM + len(table_data)
    if end_ram > CAVE_END_RAM:
        raise ValueError(
            f"subtitle payload exceeds verified code cave: end=0x{end_ram:08X}, "
            f"limit=0x{CAVE_END_RAM:08X}"
        )
    payload_size = end_ram - APPEND_RAM
    payload = bytearray(payload_size)
    payload[EVENT_WRAPPER_RAM - APPEND_RAM : EVENT_WRAPPER_RAM - APPEND_RAM + len(event_code)] = event_code
    payload[
        SCENE_UPDATE_WRAPPER_RAM - APPEND_RAM :
        SCENE_UPDATE_WRAPPER_RAM - APPEND_RAM + len(scene_update_code)
    ] = scene_update_code
    payload[
        render_wrapper_ram - APPEND_RAM : render_wrapper_ram - APPEND_RAM + len(render_code)
    ] = render_code
    payload[RECT_TABLE_RAM - APPEND_RAM : RECT_TABLE_RAM - APPEND_RAM + len(table_data)] = table_data
    return payload, {
        "event_wrapper": {"ram": EVENT_WRAPPER_RAM, "size": len(event_code)},
        "scene_update_wrapper": {
            "ram": SCENE_UPDATE_WRAPPER_RAM,
            "size": len(scene_update_code),
        },
        "render_wrapper": {"ram": render_wrapper_ram, "size": len(render_code)},
        "timer_ram": TIMER_RAM,
        "event_seen_ram": EVENT_SEEN_RAM,
        "event_match_ram": EVENT_MATCH_RAM,
        "scene_frame_ram": SCENE_FRAME_RAM,
        "rectangle_table_ram": RECT_TABLE_RAM,
        "secondary_rectangle_table_ram": secondary_table_ram
        if event_hook == "v00-multi"
        else None,
        "runtime_primitives_ram": PRIMITIVES_RAM,
        "payload_end_ram": end_ram,
        "payload_size": payload_size,
    }


def locate_root_record(track_path: Path, wanted_name: str) -> tuple[int, int, bytes]:
    with track_path.open("rb") as track:
        pvd, form = disc.read_sector(track, 16)
        if form != "form1" or pvd[0] != 1 or pvd[1:6] != b"CD001":
            raise ValueError("valid Form 1 ISO PVD not found")
        root = disc.parse_record(pvd[156 : 156 + pvd[156]])
        data, forms = disc.read_extent(track, root["lba"], root["size"])
        if forms != {"form1"}:
            raise ValueError("root directory is not Form 1")
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset = ((offset // disc.LOGICAL_SECTOR_SIZE) + 1) * disc.LOGICAL_SECTOR_SIZE
            continue
        record = data[offset : offset + length]
        parsed = disc.parse_record(record)
        if parsed["name"].upper() == wanted_name.upper():
            return root["lba"] + offset // disc.LOGICAL_SECTOR_SIZE, offset % disc.LOGICAL_SECTOR_SIZE, record
        offset += length
    raise ValueError(f"root directory record not found: {wanted_name}")


def patch_logical_sector(output_bin: Path, lba: int, mutator, *, allow_form2_conversion: bool = False) -> dict:
    raw_offset = lba * disc.RAW_SECTOR_SIZE
    with output_bin.open("r+b") as track:
        track.seek(raw_offset)
        original = track.read(disc.RAW_SECTOR_SIZE)
        patched = bytearray(original)
        was_form2 = bool(patched[18] & 0x20)
        if was_form2:
            if not allow_form2_conversion:
                raise ValueError(f"unexpected Form 2 sector at LBA {lba}")
            # The sectors between SLPM and the next ISO extent are unallocated.
            # Convert only the sectors claimed by the expanded file to Form 1.
            patched[18] &= ~0x20
            patched[22] &= ~0x20
        elif rebuild_mode2_form1_integrity(original) != original:
            raise ValueError(f"source sector EDC/ECC mismatch at LBA {lba}")
        mutator(patched)
        rebuilt = rebuild_mode2_form1_integrity(bytes(patched))
        track.seek(raw_offset)
        track.write(rebuilt)
    return {"lba": lba, "converted_from_form2": was_form2, "original_sha256": sha256_bytes(original), "patched_sha256": sha256_bytes(rebuilt)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_SUBTITLE_FONT,
        help=f"subtitle font (default: {DEFAULT_SUBTITLE_FONT})",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--text", default="자막 테스트")
    parser.add_argument("--font-size", type=int, default=20)
    parser.add_argument(
        "--merge-gap",
        type=int,
        default=0,
        help="merge same-row white runs separated by this many pixels",
    )
    parser.add_argument(
        "--hide-background",
        action="store_true",
        help="retain the safe chain-head packet but render it at zero size",
    )
    parser.add_argument("--sound-id", type=lambda value: int(value, 0), default=TARGET_SOUND_ID)
    parser.add_argument(
        "--event-hook",
        choices=(
            "common-request",
            "key-on",
            "v00-scene",
            "v00-group",
            "v00-timeline",
            "v00-multi",
        ),
        default="common-request",
    )
    parser.add_argument(
        "--timing-report",
        type=Path,
        default=Path("work/analysis/usa_subtitle_comparison.json"),
    )
    parser.add_argument("--timing-path", default="DATA7/V00.DAT")
    parser.add_argument("--timing-text", default="For the first time in my life,")
    parser.add_argument("--secondary-text")
    parser.add_argument("--secondary-sound-id", type=lambda value: int(value, 0))
    parser.add_argument("--secondary-timing-text")
    parser.add_argument(
        "--multi-single-render-diagnostic",
        action="store_true",
        help="keep multi-group timing but use the proven primary v15 renderer",
    )
    parser.add_argument(
        "--shift-render-wrapper-diagnostic",
        action="store_true",
        help="move the proven single renderer to the multi-render address",
    )
    parser.add_argument("--tone-index", type=int, default=0)
    parser.add_argument(
        "--render-hook",
        choices=("callsite", "global", "addprim", "drawotag", "drawotag-terminal"),
        default="callsite",
    )
    parser.add_argument("--display-frames", type=int, default=DISPLAY_FRAMES)
    parser.add_argument("--base-name", default="Bushido Blade (Japan) [KO Subtitle PoC]")
    args = parser.parse_args()
    if not 0 <= args.sound_id <= 0xFFFF:
        raise ValueError("sound ID must fit 16 bits")
    if not 1 <= args.display_frames <= 0xFFFF:
        raise ValueError("display frame count must fit 16 bits")
    if not 0 <= args.tone_index <= 0xFF:
        raise ValueError("tone index must fit 8 bits")
    if not 0 <= args.merge_gap <= 3:
        raise ValueError("merge gap must be between 0 and 3 pixels")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    timing_record = None
    if args.event_hook in ("v00-timeline", "v00-multi"):
        timing_report = json.loads(args.timing_report.read_text(encoding="utf-8"))
        candidates = timing_report["official_subtitle_records"].get(args.timing_path, [])
        matches = [record for record in candidates if record["text"] == args.timing_text]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one USA timing record, found {len(matches)}: "
                f"{args.timing_path} / {args.timing_text!r}"
            )
        timing_record = matches[0]
        if timing_record.get("end_frame") is None:
            raise ValueError("selected USA timing record has no explicit zero-line end token")
    secondary_timing_record = None
    if args.event_hook == "v00-multi":
        if not args.secondary_text or args.secondary_sound_id is None or not args.secondary_timing_text:
            raise ValueError(
                "V00 multi hook requires secondary text, group, and timing text"
            )
        if not 0 <= args.secondary_sound_id <= 0xFF:
            raise ValueError("secondary V00 group must fit one byte")
        candidates = timing_report["official_subtitle_records"].get(args.timing_path, [])
        matches = [
            record for record in candidates if record["text"] == args.secondary_timing_text
        ]
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one secondary USA timing record, found {len(matches)}: "
                f"{args.timing_path} / {args.secondary_timing_text!r}"
            )
        secondary_timing_record = matches[0]

    if sha256_file(args.image) != EXPECTED_BIN_SHA256:
        raise ValueError("unsupported source BIN hash")
    inspection = disc.inspect(args.image)
    entry = next(item for item in inspection["entries"] if item["path"] == EXE_PATH)
    if entry["sha256"].upper() != EXPECTED_EXE_SHA256 or entry["sector_forms"] != ["form1"]:
        raise ValueError("unsupported SLPM executable baseline")
    with args.image.open("rb") as source:
        original_exe, _ = disc.read_extent(source, entry["lba"], entry["size"])

    if args.event_hook == "common-request":
        hook_ram = VOICE_HOOK_RAM
        continue_ram = VOICE_CONTINUE_RAM
        expected_voice_entry = assemble(
            "sll $v0, $a3, 1; addu $v0, $v0, $a3",
            hook_ram,
        )
        replayed_instructions = ["sll v0,a3,1", "addu v0,v0,a3"]
    elif args.event_hook == "key-on":
        hook_ram = KEY_ON_HOOK_RAM
        continue_ram = KEY_ON_CONTINUE_RAM
        expected_voice_entry = assemble(
            "addiu $sp, $sp, -0x40; sw $s0, 0x18($sp)",
            hook_ram,
        )
        replayed_instructions = ["addiu sp,sp,-0x40", "sw s0,0x18(sp)"]
    elif args.event_hook == "v00-scene":
        hook_ram = V00_EVENT_HOOK_RAM
        continue_ram = V00_EVENT_CONTINUE_RAM
        # Keystone inserts a MIPS branch-delay NOP for `j`; use the explicit
        # eight-byte encoding so expected-write validation covers exactly the
        # two instructions replaced at the regional gap.
        expected_voice_entry = encode_jump(V00_EVENT_CONTINUE_RAM, link=False) + b"\x00\x00\x00\x00"
        replayed_instructions = [f"j 0x{V00_EVENT_CONTINUE_RAM:08X}", "nop"]
    else:
        hook_ram = V00_GROUP_HOOK_RAM
        continue_ram = V00_GROUP_CONTINUE_RAM
        expected_voice_entry = assemble(
            "lw $ra, 0x4c($sp); lw $s2, 0x48($sp)",
            hook_ram,
        )
        replayed_instructions = ["lw ra,0x4c(sp)", "lw s2,0x48(sp)"]
    voice_offset = EXE_HEADER_SIZE + hook_ram - EXE_RAM_BASE
    if original_exe[voice_offset : voice_offset + 8] != expected_voice_entry:
        raise ValueError("common voice entry expected-write mismatch")
    voice_after = encode_jump(EVENT_WRAPPER_RAM, link=False) + b"\x00\x00\x00\x00"
    if args.event_hook == "v00-scene":
        # The USA call uses `move a0,s1` in its JAL delay slot.  The Japanese
        # equivalent renamed that live event register to s2, and deleting the
        # regional call also deleted the argument setup.  Restore it in the
        # replacement jump's delay slot so the selector receives the event ID.
        voice_after = encode_jump(EVENT_WRAPPER_RAM, link=False) + assemble(
            "move $a0, $s2", hook_ram + 4
        )
    scene_update_offset = None
    expected_scene_update_entry = None
    scene_update_after = None
    scene_update_hook_ram = None
    scene_update_continue_ram = None
    scene_update_replayed = []
    scene_update_original_call = None
    if args.event_hook == "v00-scene":
        scene_update_hook_ram = V00_UPDATE_HOOK_RAM
        scene_update_continue_ram = V00_UPDATE_CONTINUE_RAM
        scene_update_original_call = V00_UPDATE_ORIGINAL_RAM
        scene_update_offset = EXE_HEADER_SIZE + scene_update_hook_ram - EXE_RAM_BASE
        expected_scene_update_entry = (
            encode_jump(V00_UPDATE_ORIGINAL_RAM, link=True) + b"\x00\x00\x00\x00"
        )
        scene_update_after = (
            encode_jump(SCENE_UPDATE_WRAPPER_RAM, link=True) + b"\x00\x00\x00\x00"
        )
        if original_exe[scene_update_offset : scene_update_offset + 8] != expected_scene_update_entry:
            raise ValueError("V00 scene update expected-write mismatch")
    elif args.event_hook in ("v00-group", "v00-timeline", "v00-multi"):
        scene_update_hook_ram = V00_GROUP_UPDATE_HOOK_RAM
        scene_update_continue_ram = V00_GROUP_UPDATE_CONTINUE_RAM
        scene_update_replayed = ["lw ra,0x3c(sp)", "lw s2,0x38(sp)"]
        scene_update_offset = EXE_HEADER_SIZE + scene_update_hook_ram - EXE_RAM_BASE
        expected_scene_update_entry = assemble(
            "lw $ra, 0x3c($sp); lw $s2, 0x38($sp)",
            scene_update_hook_ram,
        )
        scene_update_after = (
            encode_jump(SCENE_UPDATE_WRAPPER_RAM, link=False) + b"\x00\x00\x00\x00"
        )
        if original_exe[scene_update_offset : scene_update_offset + 8] != expected_scene_update_entry:
            raise ValueError("V00 group update expected-write mismatch")
    render_wrapper_target = (
        MULTI_RENDER_WRAPPER_RAM
        if args.event_hook == "v00-multi" or args.shift_render_wrapper_diagnostic
        else RENDER_WRAPPER_RAM
    )
    if args.render_hook == "callsite":
        render_hook_ram = RENDER_HOOK_RAM
        expected_render_entry = encode_jump(RENDER_ORIGINAL_RAM, link=True)
        render_after = encode_jump(render_wrapper_target, link=True)
        render_write_size = 4
        render_replayed = []
    elif args.render_hook == "global":
        render_hook_ram = RENDER_ORIGINAL_RAM
        expected_render_entry = assemble(
            "lui $v0, 0x8009; lw $v0, 0x0ea4($v0)",
            render_hook_ram,
        )
        render_after = encode_jump(render_wrapper_target, link=False) + b"\x00\x00\x00\x00"
        render_write_size = 8
        render_replayed = ["lui v0,0x8009", "lw v0,0x0ea4(v0)"]
    elif args.render_hook == "addprim":
        render_hook_ram = ADD_PRIM_RAM
        expected_render_entry = assemble(
            "lui $a2, 0x00ff; ori $a2, $a2, 0xffff",
            render_hook_ram,
        )
        render_after = encode_jump(render_wrapper_target, link=False) + b"\x00\x00\x00\x00"
        render_write_size = 8
        render_replayed = ["lui a2,0x00ff", "ori a2,a2,0xffff"]
    else:
        render_hook_ram = DRAW_OTAG_RAM
        expected_render_entry = assemble(
            "lui $v0, 0x8009; lbu $v0, -0x5b4e($v0)",
            render_hook_ram,
        )
        render_after = encode_jump(render_wrapper_target, link=False) + b"\x00\x00\x00\x00"
        render_write_size = 8
        render_replayed = ["lui v0,0x8009", "lbu v0,-0x5b4e(v0)"]
    render_offset = EXE_HEADER_SIZE + render_hook_ram - EXE_RAM_BASE
    if original_exe[render_offset : render_offset + render_write_size] != expected_render_entry:
        raise ValueError("frame submission expected-write mismatch")

    runs, preview, image_meta = render_runs(
        args.text, args.font, args.font_size, args.merge_gap
    )
    primitive_data, primitive_meta = build_primitive_sets(runs, image_meta)
    secondary_runs = None
    secondary_preview = None
    secondary_image_meta = None
    secondary_primitive_data = None
    secondary_primitive_meta = None
    if args.event_hook == "v00-multi":
        secondary_runs, secondary_preview, secondary_image_meta = render_runs(
            args.secondary_text, args.font, args.font_size, args.merge_gap
        )
        secondary_primitive_data, secondary_primitive_meta = build_primitive_sets(
            secondary_runs, secondary_image_meta
        )
    payload, payload_meta = build_payload(
        primitive_meta,
        primitive_data,
        args.sound_id,
        args.event_hook,
        args.tone_index,
        args.render_hook,
        args.display_frames,
        timing_record["start_frame"] if timing_record is not None else None,
        timing_record["end_frame"] if timing_record is not None else None,
        secondary_primitive_meta,
        secondary_primitive_data,
        args.secondary_sound_id,
        secondary_timing_record["start_frame"]
        if secondary_timing_record is not None
        else None,
        secondary_timing_record["end_frame"]
        if secondary_timing_record is not None
        else None,
        not args.multi_single_render_diagnostic,
        args.shift_render_wrapper_diagnostic,
        args.hide_background,
    )

    patched_exe = bytearray(original_exe)
    patched_exe[voice_offset : voice_offset + 8] = voice_after
    if scene_update_offset is not None and scene_update_after is not None:
        patched_exe[scene_update_offset : scene_update_offset + 8] = scene_update_after
    patched_exe[render_offset : render_offset + render_write_size] = render_after
    cave_offset = APPEND_FILE_OFFSET
    cave_end_offset = EXE_HEADER_SIZE + CAVE_END_RAM - EXE_RAM_BASE
    injection_source_sha256 = sha256_bytes(original_exe[cave_offset:cave_end_offset])
    patched_exe[cave_offset : cave_offset + len(payload)] = payload

    base_name = args.base_name
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    report_path = args.output_dir / "build_report.json"
    preview_path = args.output_dir / "subtitle_preview.png"
    preview.resize((preview.width * 4, preview.height * 4), Image.Resampling.NEAREST).save(preview_path)
    secondary_preview_path = None
    if secondary_preview is not None:
        secondary_preview_path = args.output_dir / "subtitle_preview_secondary.png"
        secondary_preview.resize(
            (secondary_preview.width * 4, secondary_preview.height * 4),
            Image.Resampling.NEAREST,
        ).save(secondary_preview_path)
    shutil.copyfile(args.image, output_bin)

    touched = []
    sector_count = len(patched_exe) // disc.LOGICAL_SECTOR_SIZE
    for index in range(sector_count):
        chunk = patched_exe[index * disc.LOGICAL_SECTOR_SIZE : (index + 1) * disc.LOGICAL_SECTOR_SIZE]
        lba = entry["lba"] + index
        def replace_data(raw: bytearray, chunk=chunk):
            raw[disc.MODE2_FORM1_DATA_OFFSET : disc.MODE2_FORM1_DATA_OFFSET + disc.LOGICAL_SECTOR_SIZE] = chunk
        touched.append(
            patch_logical_sector(
                output_bin,
                lba,
                replace_data,
                allow_form2_conversion=index * disc.LOGICAL_SECTOR_SIZE >= len(original_exe),
            )
        )

    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
        newline="\n",
    )

    reparsed = disc.inspect(output_bin)
    patched_entry = next(item for item in reparsed["entries"] if item["path"] == EXE_PATH)
    if patched_entry["size"] != len(original_exe):
        raise ValueError("patched ISO directory changed the SLPM size")
    with output_bin.open("rb") as track:
        disc_exe, forms = disc.read_extent(track, patched_entry["lba"], patched_entry["size"])
    if forms != {"form1"} or disc_exe != bytes(patched_exe):
        raise ValueError("expanded SLPM failed disc roundtrip")

    report = {
        "gate_id": "BBJ-POC-SUB-001",
        "status": "static_build_passed_runtime_pending",
        "baseline": {"bin_sha256": EXPECTED_BIN_SHA256, "exe_sha256": EXPECTED_EXE_SHA256},
        "event": {
            "voice_hook_ram": f"0x{hook_ram:08X}",
            "voice_continue_ram": f"0x{continue_ram:08X}",
            "hook_scope": args.event_hook,
            "sound_id": f"0x{args.sound_id:02X}" if args.event_hook == "common-request" else None,
            "usa_subtitle_group": args.sound_id
            if args.event_hook in ("v00-scene", "v00-group", "v00-timeline", "v00-multi")
            else None,
            "usa_timing": {
                "report": str(args.timing_report),
                "path": args.timing_path,
                "text": args.timing_text,
                "start_frame": timing_record["start_frame"],
                "end_frame": timing_record["end_frame"],
                "duration_frames": timing_record["duration_frames"],
                "end_mode": timing_record["end_mode"],
            } if timing_record is not None else None,
            "secondary_usa_timing": {
                "report": str(args.timing_report),
                "path": args.timing_path,
                "group": args.secondary_sound_id,
                "text": args.secondary_timing_text,
                "start_frame": secondary_timing_record["start_frame"],
                "end_frame": secondary_timing_record["end_frame"],
                "duration_frames": secondary_timing_record["duration_frames"],
                "end_mode": secondary_timing_record["end_mode"],
                "mapping_status": "candidate",
            }
            if secondary_timing_record is not None
            else None,
            "tone_index": args.tone_index,
            "story_vab_handle_ram": f"0x{STORY_VAB_HANDLE_RAM:08X}" if args.event_hook == "key-on" else None,
            "display_frames": args.display_frames if timing_record is None else None,
        },
        "render": {
            **image_meta,
            **primitive_meta,
            "font": str(args.font),
            "font_sha256": sha256_file(args.font),
            "hook_scope": args.render_hook,
            "hook_ram": f"0x{render_hook_ram:08X}",
            "secondary": {
                **secondary_image_meta,
                **secondary_primitive_meta,
                "text": args.secondary_text,
            }
            if secondary_primitive_meta is not None
            else None,
        },
        "injection": payload_meta,
        "diagnostic_string_region": {
            "ram_start": f"0x{APPEND_RAM:08X}",
            "ram_end": f"0x{CAVE_END_RAM:08X}",
            "size": CAVE_END_RAM - APPEND_RAM,
            "source_sha256": injection_source_sha256,
            "purpose_before_patch": "PsyQ GPU/CD/SPU diagnostic strings",
            "bytes_match_source_in_title_capture": True,
            "bytes_match_source_in_tatsumi_voice_capture": True,
            "conditional_scope": "one-line PoC only; diagnostic text loss is accepted, normal-path calls still require runtime verification",
        },
        "expected_writes": {
            "voice_hook": {
                "ram": f"0x{hook_ram:08X}",
                "before": expected_voice_entry.hex(),
                "after": voice_after.hex(),
                "overwritten_instructions_replayed_by_wrapper": replayed_instructions
            },
            "scene_update_hook": {
                "ram": f"0x{scene_update_hook_ram:08X}",
                "before": expected_scene_update_entry.hex(),
                "after": scene_update_after.hex(),
                "original_call_replayed_by_wrapper": (
                    f"0x{scene_update_original_call:08X}" if scene_update_original_call is not None else None
                ),
                "overwritten_instructions_replayed_by_wrapper": scene_update_replayed,
                "continue_ram": f"0x{scene_update_continue_ram:08X}",
            } if expected_scene_update_entry is not None and scene_update_after is not None else None,
            "render_hook": {
                "ram": f"0x{render_hook_ram:08X}",
                "before": expected_render_entry.hex(),
                "after": render_after.hex(),
                "overwritten_instructions_replayed_by_wrapper": render_replayed
            },
            "payload": {"file_offset": cave_offset, "size": len(payload)},
        },
        "static_checks": {
            "source_hash": True,
            "expected_write": True,
            "mode2_form1_integrity": True,
            "disc_size_preserved": output_bin.stat().st_size == args.image.stat().st_size,
            "same_exe_size": len(patched_exe) == len(original_exe),
            "code_cave_fit": payload_meta["payload_end_ram"] <= CAVE_END_RAM,
            "exe_roundtrip": True,
        },
        "output": {
            "bin": str(output_bin),
            "cue": str(output_cue),
            "bin_sha256": sha256_file(output_bin),
            "preview": str(preview_path),
            "secondary_preview": str(secondary_preview_path)
            if secondary_preview_path is not None
            else None,
        },
        "runtime": {
            "status": "pending",
            "required_observation": (
                (
                    f"USA 공식 타임라인에 따라 scene frame "
                    f"{timing_record['start_frame']}부터 {timing_record['end_frame'] - 1}까지만 "
                    f"화면 하단에 '{args.text}'가 표시되고 frame {timing_record['end_frame']}에서 사라짐"
                )
                if timing_record is not None
                else (
                    f"{args.event_hook} 조건 재생 시에만 화면 하단에 "
                    f"'{args.text}'가 나타나고 약 {args.display_frames}프레임 뒤 사라짐"
                )
            ),
        },
        "sector_changes": {"exe_sector_count": len(touched)},
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"output_cue={output_cue}")
    print(f"report={report_path}")
    print(f"subtitle_rectangles={len(runs)} packets_per_frame={primitive_meta['packets_per_set']}")
    print(f"code_cave_used={len(payload)}/{CAVE_END_RAM - APPEND_RAM}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise
