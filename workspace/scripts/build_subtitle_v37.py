#!/usr/bin/env python3
"""v37: full Korean subtitle engine for DATA7/V00.DAT via file extension.

Pipeline (docs/HANDOFF_2026-08-01.md section 18, docs/v0x-scene-mapping.md):
- Build a Korean subtitle section (glyph strips + display schedule) per V0X
  file from jp_subtitle_scene_schedule.json (frames) and
  workspace/text/source/v0x_record_korean.json (record-level Korean).
- Append the section to the file WITHOUT touching its header/section table
  (the JP EXE reads header fields at hardcoded byte offsets).
- Relocate the enlarged file to brand-new sectors appended at the image end
  and patch its DATA7 directory record (game resolves files by name).
- EXE cave holds a generic interpreter hooked at 0x8003A88C: it detects a
  patched file via a (header word3 -> section offset) table, derives the
  group from [gp+0x2F8], uploads the group's glyph strips + CLUT each frame,
  and issues one official-renderer call per visible line. Template CLUT
  fields are patched around the renderer calls only (CONTINUE regression).

Cell budget per group: 13 + 13 column cells (letters) + 9 digit cells = 35
16x16 glyphs; '.', '!', '?' render through untouched stock templates and
'…' becomes '...'. One blank cell is shared by all spaces.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
from pathlib import Path

import inspect_psx_disc as disc
from build_official_glyph_poc import (
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
    V00_UPDATE_COMPARE_FALLTHROUGH_RAM,
    V00_UPDATE_COMPARE_HOOK_RAM,
    V00_UPDATE_COMPARE_TAKEN_RAM,
    sha256_bytes,
    split_address,
)
from build_subtitle_poc import assemble, encode_jump, patch_logical_sector, sha256_file
from build_subtitle_scene_poc_v36 import STOCK_TEMPLATE_CLUT, render_glyph_fit

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = ROOT / "inputs" / "analysis" / "jp_subtitle_scene_schedule.json"
KOREAN_PATH = ROOT / "workspace" / "text" / "source" / "v0x_record_korean.json"

TRANSIENT_BASE = 0x8014C000
SECTION_MAGIC = 0x4B303756  # 'V70K' little-endian

COL1_CHARS = "ACEGIKMOQSUWY"
# 'Z' is excluded: its stock cell sits at (954,464) V=208, breaking the
# contiguous V=0..191 column the strip upload assumes (v38 first run: the
# 26th glyph of a group rendered garbage - user report 2026-08-02).
COL2_CHARS = "BDFHJLNPRTVX"
DIG_CHARS = "012345678"
STOCK_CHARS = {".", "!", "?"}
GLYPH_BYTES = 128
ADVANCE_PX = 16

RECT_COL1 = (950, 256, 4, 208)
RECT_COL2 = (954, 256, 4, 192)
RECT_DIG = (920, 400, 12, 48)
# digit stock cells: index i -> block column i%3 (8 bytes), row band i//3

STOCK_PUNCT_CLUT_RANGE = (3, 12)  # template indices restored to 0x7678
STOCK_LETTER_CLUT_RANGE = (13, 38)  # restored to 0x7838
DIGIT_STOCK_CLUT = 0x7678
# The stock font is proportional (u16 width table at 0x80085B1C, e.g. I=7):
# borrowed cells advance by the stock character's width, so the wrapper
# forces entries 3..38 to 16 around the renderer and restores the originals
# (kept in the cave) afterwards. Entry 0 ('.') stays 6 for ellipsis dots.
WIDTH_TABLE_RAM = 0x80085B1C
WIDTH_PATCH_FIRST = 3
WIDTH_PATCH_COUNT = 36
DOT_ADVANCE_PX = 6

FORMAT_PREFIX = "#X#Y#w#h#S#R#G#B"


def normalize_line(line: str) -> str:
    return (
        line.replace("...", "…")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", "'")
        .replace("\u201d", "'")
        .replace("\u3000", " ")
    )


class GroupLayout:
    def __init__(self, font: Path):
        self.font = font
        self.assignments: dict[str, str] = {}  # source char -> encoded char
        self.pool = list(COL1_CHARS) + list(COL2_CHARS) + list(DIG_CHARS)
        self.blank_char: str | None = None

    def assign(self, source_char: str) -> str:
        if source_char in self.assignments:
            return self.assignments[source_char]
        if not self.pool:
            raise ValueError(
                f"group exceeds the "
                f"{len(COL1_CHARS) + len(COL2_CHARS) + len(DIG_CHARS)}-cell "
                f"glyph budget"
            )
        encoded = self.pool.pop(0)
        self.assignments[source_char] = encoded
        return encoded

    def encode_line(self, line: str) -> str:
        encoded = []
        for char in normalize_line(line):
            if char == " ":
                if self.blank_char is None:
                    self.blank_char = self.assign("\x00blank")
                encoded.append(self.blank_char)
            elif char == "…":
                encoded.append("...")
            elif char in STOCK_CHARS:
                encoded.append(char)
            else:
                encoded.append(self.assign(char))
        return "".join(encoded)

    def build_pixel_blocks(self) -> tuple[bytes, bytes, bytes, list[dict]]:
        col1 = bytearray(len(COL1_CHARS) * GLYPH_BYTES)
        col2 = bytearray(len(COL2_CHARS) * GLYPH_BYTES)
        dig = bytearray(24 * 48)
        self.used = {"col1": False, "col2": False, "dig": False}
        meta = []
        for source, encoded in self.assignments.items():
            if source == "\x00blank":
                continue  # stays zero
            pixels, _preview, glyph_meta = render_glyph_fit(self.font, source)
            meta.append({"char": source, "cell": encoded, **glyph_meta})
            if encoded in COL1_CHARS:
                index = COL1_CHARS.index(encoded)
                col1[index * GLYPH_BYTES : (index + 1) * GLYPH_BYTES] = pixels
                self.used["col1"] = True
            elif encoded in COL2_CHARS:
                index = COL2_CHARS.index(encoded)
                col2[index * GLYPH_BYTES : (index + 1) * GLYPH_BYTES] = pixels
                self.used["col2"] = True
            else:
                self.used["dig"] = True
                index = DIG_CHARS.index(encoded)
                row_band = index // 3
                byte_x = 8 * (index % 3)
                for row in range(16):
                    offset = (row_band * 16 + row) * 24 + byte_x
                    dig[offset : offset + 8] = pixels[row * 8 : (row + 1) * 8]
        return bytes(col1), bytes(col2), bytes(dig), meta


def build_section(
    file_path: str, schedule: dict, korean: dict, font: Path
) -> tuple[bytes, dict]:
    file_entry = next(f for f in schedule["files"] if f["path"] == file_path)
    korean_groups = korean.get(file_path, {})
    group_blobs: list[bytes | None] = []
    section_meta = {"groups": []}
    for group in file_entry["groups"]:
        group_number = group["group"]
        tokens = [t for t in group.get("schedule", [])]
        text_tokens = [t for t in tokens if t["lines_en"] or t.get("lines_ko")]
        korean_records = korean_groups.get(str(group_number))
        if not korean_records:
            group_blobs.append(None)
            continue
        if len(korean_records) != len(text_tokens):
            raise ValueError(
                f"{file_path} group {group_number}: "
                f"{len(korean_records)} korean records vs {len(text_tokens)} tokens"
            )
        layout = GroupLayout(font)
        items = []  # (f0, f1, x, y, encoded_string)
        length = group["length_frames"]
        for index, token in enumerate(text_tokens):
            token_position = tokens.index(token)
            f0 = token["frame"]
            if token_position + 1 < len(tokens):
                f1 = tokens[token_position + 1]["frame"]
            else:
                f1 = length
            lines = korean_records[index]
            line_count = len(lines)
            for line_index, line in enumerate(lines):
                encoded = layout.encode_line(line)
                width = sum(
                    DOT_ADVANCE_PX if char == "." else ADVANCE_PX
                    for char in encoded
                )
                x = max(0, (320 - width) // 2)
                y = 190 - 9 * (line_count - 1) + 18 * line_index
                items.append((f0, f1, x, y, encoded))
        col1, col2, dig, glyph_meta = layout.build_pixel_blocks()
        clut = struct.pack("<16H", 0x0000, 0x8000, 0x7FFF, *([0x0000] * 13))

        strings = bytearray()
        string_offsets = []
        for _f0, _f1, _x, _y, encoded in items:
            string_offsets.append(len(strings))
            strings += (FORMAT_PREFIX + encoded).encode("ascii") + b"\x00"
        while len(strings) % 4:
            strings += b"\x00"

        header_size = 20 + 12 * len(items)
        col1_off = header_size + len(strings)
        col2_off = col1_off + len(col1)
        dig_off = col2_off + len(col2)
        clut_off = dig_off + len(dig)
        blob = bytearray()
        blob += struct.pack(
            "<5I",
            col1_off if layout.used["col1"] else 0,
            col2_off if layout.used["col2"] else 0,
            dig_off if layout.used["dig"] else 0,
            clut_off,
            len(items),
        )
        for (f0, f1, x, y, _encoded), string_offset in zip(items, string_offsets):
            blob += struct.pack("<4HI", f0, f1, x, y, header_size + string_offset)
        blob += strings + col1 + col2 + dig + clut
        group_blobs.append(bytes(blob))
        section_meta["groups"].append(
            {
                "group": group_number,
                "items": [
                    {"f0": f0, "f1": f1, "x": x, "y": y, "text": encoded}
                    for f0, f1, x, y, encoded in items
                ],
                "glyphs": glyph_meta,
                "blob_size": len(blob),
            }
        )

    # assemble section: magic + 6 offsets (relative to section start, blob
    # offsets converted below; group item string/pixel offsets are relative
    # to the GROUP BLOB start, and the interpreter adds gblob, so store them
    # as-is)
    header = bytearray(struct.pack("<I", SECTION_MAGIC)) + bytearray(24)
    cursor = len(header)
    body = bytearray()
    for index, blob in enumerate(group_blobs):
        if blob is None:
            continue
        struct.pack_into("<I", header, 4 + index * 4, cursor)
        body += blob
        cursor += len(blob)
        while cursor % 4:
            body += b"\x00"
            cursor += 1
    section = bytes(header) + bytes(body)
    section_meta["size"] = len(section)
    return section, section_meta


def assert_no_load_delay_hazard(code: bytes, address: int) -> None:
    """Reject code where an instruction reads a register loaded by the
    immediately preceding load. Keystone fills branch delay slots but never
    load delays, and the R3000 has no interlock (v37 first run bug)."""
    from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs

    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    loads = {"lw", "lh", "lhu", "lb", "lbu", "lwl", "lwr"}
    previous = None
    for instruction in md.disasm(code, address):
        if previous is not None:
            destination, previous_instruction = previous
            reads = {
                operand.reg
                for operand in instruction.operands
                if operand.type == 1  # MIPS_OP_REG
            } | {
                operand.mem.base
                for operand in instruction.operands
                if operand.type == 3 and operand.mem.base  # MIPS_OP_MEM
            }
            # the write-back operand of a store is a read, of a load is not
            if instruction.mnemonic in loads and instruction.operands:
                reads.discard(instruction.operands[0].reg)
            if destination in reads:
                raise ValueError(
                    f"load-delay hazard at 0x{instruction.address:08X}: "
                    f"'{previous_instruction}' then "
                    f"'{instruction.mnemonic} {instruction.op_str}'"
                )
        if instruction.mnemonic in loads and instruction.operands:
            previous = (
                instruction.operands[0].reg,
                f"{instruction.mnemonic} {instruction.op_str}",
            )
        else:
            previous = None


def bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def msf_word(lba: int) -> int:
    """Little-endian u32 as stored in the EXE load table: BCD m, s, f, 0.
    The loader reads V0X files from this table (start MSF + size), NOT from
    the ISO directory — proven by v37 first run: directory-only relocation
    left the game reading the original sectors (docs section 18 update)."""
    minutes, remainder = divmod(lba + 150, 75 * 60)
    seconds, frames = divmod(remainder, 75)
    return bcd(minutes) | (bcd(seconds) << 8) | (bcd(frames) << 16)


def make_raw_sector(lba: int, subheader: bytes, data: bytes, rebuild) -> bytes:
    if len(data) != 2048 or len(subheader) != 8:
        raise ValueError("sector payload/subheader size mismatch")
    minutes, remainder = divmod(lba + 150, 75 * 60)
    seconds, frames = divmod(remainder, 75)
    raw = bytearray(2352)
    raw[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
    raw[12] = bcd(minutes)
    raw[13] = bcd(seconds)
    raw[14] = bcd(frames)
    raw[15] = 2
    raw[16:24] = subheader
    raw[24 : 24 + 2048] = data
    return rebuild(bytes(raw))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--font",
        type=Path,
        default=Path(r"G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf"),
    )
    parser.add_argument("--files", nargs="*", default=["DATA7/V00.DAT"])
    parser.add_argument(
        "--base-name", default="Bushido Blade (Japan) [KO Subtitle v37]"
    )
    parser.add_argument(
        "--allow-exe-sha", action="append", default=[], metavar="SHA256",
        help="accept this SLPM_860.20 in addition to the pristine one.  The "
             "graphics build patches the loading logo, which lives inside the "
             "executable, so chaining needs this; the cave source hash and the "
             "expected-write checks still guard every region we actually edit.")
    parser.add_argument(
        "--allow-source-sha", action="append", default=[], metavar="SHA256",
        help="accept this source BIN in addition to the pristine Japanese "
             "original -- used to chain on top of the graphics build.  The "
             "caller has to state the hash, so a wrong input still fails, and "
             "the executable baseline below is enforced either way.")
    args = parser.parse_args()

    from build_options_poc import rebuild_mode2_form1_integrity

    accepted = {EXPECTED_BIN_SHA256.upper()}
    accepted.update(s.strip().upper() for s in args.allow_source_sha)
    source_sha = sha256_file(args.image).upper()
    if source_sha not in accepted:
        raise ValueError(f"unsupported source BIN hash: {source_sha}")
    schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    korean = json.loads(KOREAN_PATH.read_text(encoding="utf-8"))

    inspection = disc.inspect(args.image)
    exe_entry = next(e for e in inspection["entries"] if e["path"] == EXE_PATH)
    accepted_exe = {EXPECTED_EXE_SHA256.upper()}
    accepted_exe.update(s.strip().upper() for s in args.allow_exe_sha)
    if exe_entry["sha256"].upper() not in accepted_exe:
        raise ValueError(
            f"unsupported Japanese executable baseline: {exe_entry['sha256'].upper()}")
    with args.image.open("rb") as source:
        original_exe, exe_forms = disc.read_extent(
            source, exe_entry["lba"], exe_entry["size"]
        )
    if exe_forms != {"form1"}:
        raise ValueError("Japanese executable is not Form 1")

    image_sectors = args.image.stat().st_size // disc.RAW_SECTOR_SIZE

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = args.output_dir / f"{args.base_name}.bin"
    output_cue = args.output_dir / f"{args.base_name}.cue"
    report_path = args.output_dir / "build_report.json"
    shutil.copyfile(args.image, output_bin)

    relocations = []
    cave_table = []  # (word3, section_offset)
    next_lba = image_sectors
    with args.image.open("rb") as source:
        for file_path in args.files:
            entry = next(e for e in inspection["entries"] if e["path"] == file_path)
            original, forms = disc.read_extent(source, entry["lba"], entry["size"])
            if forms != {"form1"}:
                raise ValueError(f"{file_path} is not Form 1")
            word3 = struct.unpack_from("<I", original, 0xC)[0]
            section_offset = (len(original) + 3) & ~3
            section, section_meta = build_section(
                file_path, schedule, korean, args.font
            )
            new_file = (
                bytes(original)
                + b"\x00" * (section_offset - len(original))
                + section
            )
            # clone subheaders from the original extent
            source.seek(entry["lba"] * disc.RAW_SECTOR_SIZE + 16)
            first_subheader = source.read(8)
            last_lba = entry["lba"] + math.ceil(entry["size"] / 2048) - 1
            source.seek(last_lba * disc.RAW_SECTOR_SIZE + 16)
            last_subheader = source.read(8)

            sector_count = math.ceil(len(new_file) / 2048)
            new_lba = next_lba
            with output_bin.open("ab") as target:
                for index in range(sector_count):
                    chunk = new_file[index * 2048 : (index + 1) * 2048]
                    chunk += b"\x00" * (2048 - len(chunk))
                    subheader = (
                        last_subheader if index == sector_count - 1 else first_subheader
                    )
                    target.write(
                        make_raw_sector(
                            new_lba + index,
                            subheader,
                            chunk,
                            rebuild_mode2_form1_integrity,
                        )
                    )
            next_lba = new_lba + sector_count

            # patch the directory record (parent directory of the file)
            parent_path = file_path.rsplit("/", 1)[0]
            parent = next(
                e for e in inspection["entries"] if e["path"] == parent_path
            )
            directory_data, dir_forms = disc.read_extent(
                source, parent["lba"], parent["size"]
            )
            if dir_forms != {"form1"}:
                raise ValueError(f"{parent_path} directory is not Form 1")
            wanted = file_path.rsplit("/", 1)[1].upper()
            record_offset = None
            offset = 0
            while offset < len(directory_data):
                length = directory_data[offset]
                if length == 0:
                    offset = ((offset // 2048) + 1) * 2048
                    continue
                parsed = disc.parse_record(directory_data[offset : offset + length])
                if parsed["name"].upper() == wanted:
                    record_offset = offset
                    break
                offset += length
            if record_offset is None:
                raise ValueError(f"directory record not found: {file_path}")
            record_sector = parent["lba"] + record_offset // 2048
            in_sector = record_offset % 2048

            def patch_record(raw: bytearray, in_sector=in_sector, new_lba=new_lba, size=len(new_file)):
                base = disc.MODE2_FORM1_DATA_OFFSET + in_sector
                struct.pack_into("<I", raw, base + 2, new_lba)
                struct.pack_into(">I", raw, base + 6, new_lba)
                struct.pack_into("<I", raw, base + 10, size)
                struct.pack_into(">I", raw, base + 14, size)

            patch_logical_sector(output_bin, record_sector, patch_record)
            relocations.append(
                {
                    "path": file_path,
                    "old_lba": entry["lba"],
                    "old_size": entry["size"],
                    "new_lba": new_lba,
                    "new_size": len(new_file),
                    "section_offset": section_offset,
                    "section": section_meta,
                }
            )
            cave_table.append((word3, section_offset))

    # ------------------------------------------------------------------ EXE
    wrapper_ram = CAVE_START_RAM
    data_ram = CAVE_START_RAM + 0x340
    rect_col1_ram = data_ram
    rect_col2_ram = data_ram + 8
    rect_dig_ram = data_ram + 16
    rect_clut_ram = data_ram + 24
    table_ram = data_ram + 32
    table_data = b""
    for word3, section_offset in cave_table:
        table_data += struct.pack("<II", word3, section_offset)
    table_data += struct.pack("<II", 0, 0)
    width_offset = (
        EXE_HEADER_SIZE
        + WIDTH_TABLE_RAM
        - EXE_RAM_BASE
        + WIDTH_PATCH_FIRST * 2
    )
    width_backup = bytes(
        original_exe[width_offset : width_offset + WIDTH_PATCH_COUNT * 2]
    )
    width_backup_ram = table_ram + len(table_data)
    data = (
        struct.pack("<hhhh", *RECT_COL1)
        + struct.pack("<hhhh", *RECT_COL2)
        + struct.pack("<hhhh", *RECT_DIG)
        + struct.pack("<hhhh", *CLUT_RECT)
        + table_data
        + width_backup
    )
    end_ram = data_ram + len(data)
    if end_ram > CAVE_END_RAM:
        raise ValueError("cave data exceeds verified range")

    split = {
        name: split_address(value)
        for name, value in {
            "rect_col1": rect_col1_ram,
            "rect_col2": rect_col2_ram,
            "rect_dig": rect_dig_ram,
            "rect_clut": rect_clut_ram,
            "table": table_ram,
            "width_backup": width_backup_ram,
        }.items()
    }
    width_patch_ram = WIDTH_TABLE_RAM + WIDTH_PATCH_FIRST * 2
    template_patch_start = JP_GLYPH_TABLE_RAM + 3 * 16 + 6
    # The nop after each lw covers the R3000 load-delay slot: keystone fills
    # branch delay slots but never load delays, and omitting it made
    # LoadImage receive a stale $t0 (v37 first run: no glyphs, CD stall).
    # A zero offset means "block unused" - skip it so stock cells survive.
    upload_blocks = "\n".join(
        f"""
    lui $a0, {split[rect][0]}
    ori $a0, $a0, {split[rect][1]}
    lw $t0, {slot}($s5)
    nop
    beq $t0, $zero, skip_upload_{slot}
    addu $a1, $s6, $t0
    jal 0x{JP_LOAD_IMAGE_RAM:08x}
skip_upload_{slot}:
"""
        for slot, rect in (
            (0, "rect_col1"),
            (4, "rect_col2"),
            (8, "rect_dig"),
            (12, "rect_clut"),
        )
    )

    wrapper_source = f"""
    lui $t5, 0x8014
    ori $t5, $t5, 0xC000
    lw $t6, 0xC($t5)
    lui $t7, {split['table'][0]}
    ori $t7, $t7, {split['table'][1]}
table_loop:
    lw $t8, 0($t7)
    lw $t9, 4($t7)
    beq $t8, $zero, compare_exit
    beq $t8, $t6, table_found
    addiu $t7, $t7, 8
    j table_loop
table_found:
    addu $t9, $t5, $t9

    lw $t8, 0x2F8($gp)
    lw $t7, 4($t5)
    subu $t8, $t8, $t5
    subu $t8, $t8, $t7
    srl $t8, $t8, 3
    sltiu $t7, $t8, 6
    beq $t7, $zero, compare_exit
    sll $t8, $t8, 2
    addu $t8, $t9, $t8
    lw $t8, 4($t8)
    nop
    beq $t8, $zero, compare_exit
    addiu $sp, $sp, -0x40
    sw $ra, 0x3c($sp)
    sw $a0, 0x38($sp)
    sw $v1, 0x34($sp)
    sw $s5, 0x30($sp)
    sw $s6, 0x2c($sp)
    sw $s7, 0x28($sp)
    addu $s5, $t9, $t8
    move $s6, $s5
    move $s7, $a0
{upload_blocks}
    lui $t3, 0x{template_patch_start >> 16:X}
    ori $t3, $t3, 0x{template_patch_start & 0xFFFF:X}
    ori $t5, $zero, 0x{CLUT_ID:X}
    ori $t6, $zero, 36
patch_loop:
    sh $t5, 0($t3)
    addiu $t3, $t3, 16
    addiu $t6, $t6, -1
    bne $t6, $zero, patch_loop
    lui $t3, 0x{width_patch_ram >> 16:X}
    ori $t3, $t3, 0x{width_patch_ram & 0xFFFF:X}
    ori $t5, $zero, 16
    ori $t6, $zero, {WIDTH_PATCH_COUNT}
width_patch:
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t6, $t6, -1
    bne $t6, $zero, width_patch

    lw $s2, 0x10($s5)
    addiu $s1, $s5, 0x14
item_loop:
    beq $s2, $zero, items_done
    lhu $t0, 0($s1)
    lhu $t1, 2($s1)
    nop
    sltu $t2, $s7, $t0
    bne $t2, $zero, item_next
    sltu $t2, $s7, $t1
    beq $t2, $zero, item_next
    lw $t0, 8($s1)
    lhu $a1, 4($s1)
    lhu $a2, 6($s1)
    nop
    addu $a0, $s6, $t0
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
item_next:
    addiu $s1, $s1, 12
    addiu $s2, $s2, -1
    j item_loop
items_done:
    lui $t3, 0x{template_patch_start >> 16:X}
    ori $t3, $t3, 0x{template_patch_start & 0xFFFF:X}
    ori $t5, $zero, 0x{DIGIT_STOCK_CLUT:X}
    ori $t6, $zero, 10
restore_punct:
    sh $t5, 0($t3)
    addiu $t3, $t3, 16
    addiu $t6, $t6, -1
    bne $t6, $zero, restore_punct
    ori $t5, $zero, 0x{STOCK_TEMPLATE_CLUT:X}
    ori $t6, $zero, 26
restore_letters:
    sh $t5, 0($t3)
    addiu $t3, $t3, 16
    addiu $t6, $t6, -1
    bne $t6, $zero, restore_letters
    lui $t3, 0x{width_patch_ram >> 16:X}
    ori $t3, $t3, 0x{width_patch_ram & 0xFFFF:X}
    lui $t4, {split['width_backup'][0]}
    ori $t4, $t4, {split['width_backup'][1]}
    ori $t6, $zero, {WIDTH_PATCH_COUNT}
width_restore:
    lhu $t5, 0($t4)
    nop
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t4, $t4, 2
    addiu $t6, $t6, -1
    bne $t6, $zero, width_restore

    lw $ra, 0x3c($sp)
    lw $a0, 0x38($sp)
    lw $v1, 0x34($sp)
    lw $s5, 0x30($sp)
    lw $s6, 0x2c($sp)
    lw $s7, 0x28($sp)
    addiu $sp, $sp, 0x40

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
    assert_no_load_delay_hazard(wrapper_code, wrapper_ram)

    payload = bytearray(end_ram - CAVE_START_RAM)
    payload[: len(wrapper_code)] = wrapper_code
    payload[data_ram - CAVE_START_RAM :] = data

    patched_exe = bytearray(original_exe)

    # Patch the hardcoded (start MSF, size) load-table entry per relocated
    # file; the loader reads V0X from this table, not the ISO directory.
    load_table_patches = []
    for relocation in relocations:
        before = struct.pack(
            "<II", msf_word(relocation["old_lba"]), relocation["old_size"]
        )
        after = struct.pack(
            "<II", msf_word(relocation["new_lba"]), relocation["new_size"]
        )
        first = original_exe.find(before)
        if first < 0:
            raise ValueError(f"load-table entry not found for {relocation['path']}")
        if original_exe.find(before, first + 1) >= 0:
            raise ValueError(f"load-table entry ambiguous for {relocation['path']}")
        patched_exe[first : first + 8] = after
        load_table_patches.append(
            {
                "path": relocation["path"],
                "ram": f"0x{EXE_RAM_BASE + first - EXE_HEADER_SIZE:08X}",
                "before": before.hex(),
                "after": after.hex(),
            }
        )

    expected_update = struct.pack("<II", 0x10830003, 0x001080C2)
    update_offset = EXE_HEADER_SIZE + V00_UPDATE_COMPARE_HOOK_RAM - EXE_RAM_BASE
    if original_exe[update_offset : update_offset + 8] != expected_update:
        raise ValueError("compare hook expected-write mismatch")
    patched_exe[update_offset : update_offset + 8] = (
        encode_jump(wrapper_ram, False) + b"\0\0\0\0"
    )
    cave_offset = EXE_HEADER_SIZE + CAVE_START_RAM - EXE_RAM_BASE
    cave_end_offset = EXE_HEADER_SIZE + CAVE_END_RAM - EXE_RAM_BASE
    cave_source_hash = sha256_bytes(bytes(original_exe[cave_offset:cave_end_offset]))
    patched_exe[cave_offset : cave_offset + len(payload)] = payload

    for index in range(len(patched_exe) // disc.LOGICAL_SECTOR_SIZE):
        chunk = patched_exe[
            index * disc.LOGICAL_SECTOR_SIZE : (index + 1) * disc.LOGICAL_SECTOR_SIZE
        ]

        def replace_data(raw: bytearray, chunk=chunk):
            start = disc.MODE2_FORM1_DATA_OFFSET
            raw[start : start + disc.LOGICAL_SECTOR_SIZE] = chunk

        patch_logical_sector(output_bin, exe_entry["lba"] + index, replace_data)

    # The appended sectors sit past the original volume, so the PVD volume
    # space size must grow with them (both endian copies).
    final_sectors = output_bin.stat().st_size // disc.RAW_SECTOR_SIZE

    def patch_volume_size(raw: bytearray, sectors=final_sectors):
        base = disc.MODE2_FORM1_DATA_OFFSET
        struct.pack_into("<I", raw, base + 80, sectors)
        struct.pack_into(">I", raw, base + 84, sectors)

    patch_logical_sector(output_bin, 16, patch_volume_size)

    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
        newline="\n",
    )

    # ------------------------------------------------------------- verify
    reparsed = disc.inspect(output_bin)
    checks = {"exe_roundtrip": False, "relocations": []}
    exe_reparsed = next(e for e in reparsed["entries"] if e["path"] == EXE_PATH)
    with output_bin.open("rb") as track:
        disc_exe, forms = disc.read_extent(
            track, exe_reparsed["lba"], exe_reparsed["size"]
        )
        checks["exe_roundtrip"] = forms == {"form1"} and disc_exe == bytes(patched_exe)
        for relocation in relocations:
            new_entry = next(
                e for e in reparsed["entries"] if e["path"] == relocation["path"]
            )
            data_back, back_forms = disc.read_extent(
                track, new_entry["lba"], new_entry["size"]
            )
            checks["relocations"].append(
                {
                    "path": relocation["path"],
                    "directory_lba_ok": new_entry["lba"] == relocation["new_lba"],
                    "directory_size_ok": new_entry["size"] == relocation["new_size"],
                    "form1": back_forms == {"form1"},
                    "content_ok": len(data_back) == relocation["new_size"],
                }
            )

    report = {
        "gate_id": "BBJ-SUB-V37-001",
        "status": "static_build_passed_runtime_pending",
        "wrapper": {
            "ram": f"0x{wrapper_ram:08X}",
            "size": len(wrapper_code),
            "hook": f"0x{V00_UPDATE_COMPARE_HOOK_RAM:08X}",
            "payload_end_ram": f"0x{end_ram:08X}",
        },
        "cave_table": [
            {"word3": f"0x{w:X}", "section_offset": f"0x{s:X}"} for w, s in cave_table
        ],
        "relocations": relocations,
        "expected_writes": {
            "load_table": load_table_patches,
            "update_hook": {
                "ram": f"0x{V00_UPDATE_COMPARE_HOOK_RAM:08X}",
                "before": expected_update.hex(),
                "after": (encode_jump(wrapper_ram, False) + b"\0\0\0\0").hex(),
            },
            "code_cave": {
                "ram_start": f"0x{CAVE_START_RAM:08X}",
                "ram_end": f"0x{CAVE_END_RAM:08X}",
                "source_sha256": cave_source_hash,
            },
        },
        "static_checks": checks,
        "output": {
            "bin": str(output_bin),
            "cue": str(output_cue),
            "bin_sha256": sha256_file(output_bin),
        },
        "runtime": {
            "status": "pending",
            "required_observation": (
                "every V00 first-battle scene (all six characters) shows its "
                "Korean lines at USA frame windows; other screens clean"
            ),
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"output_cue={output_cue}")
    print(f"wrapper={len(wrapper_code)} bytes")
    print(f"exe_roundtrip={checks['exe_roundtrip']}")
    for check in checks["relocations"]:
        print(check)
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
