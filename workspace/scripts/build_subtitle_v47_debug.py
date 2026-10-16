#!/usr/bin/env python3
"""v47-debug: investigation-only build that records what the wrapper sees.

Breakpoint tracing at a per-frame hook stalls CD/MDEC streaming, so the ending
frame counter never advances and every probe reads a frozen state (HANDOFF 41).
This build instead has the wrapper write four counters into the cave, which
poll_ending_state.py can read at full speed:

  +0  shared-body entry count
  +4  last frame value the record loop compared ($s7)
  +8  last group index ($t2)
  +12 record_hit count

NOT FOR RELEASE -- the shipping build keeps zero custom state RAM.


Three hook contexts share one render body because the code cave only holds
1233 bytes (v38's single wrapper already used 1016):

  context     hook        group source                     frame source
  --------    ----------  -------------------------------  ---------------
  win (V0X)   0x8003A88C  ([gp+0x2F8]-base-[base+4])/8      a0
  cutscene    0x8003B334  [gp+0x338] (movie_id 0..5)        [0x8013E6E4]*2
  ending      0x8004A244  n=[0x800A3178]; n<=6 ? 2(n-1)     [0x80128248]
                                        : 2(n-7)+1

Each stub loads the group-blob pointer into $s5, the frame into $s7 and a
return-handler address into $t8, then jumps to the shared body, which ends
with `jr $t8`. The handler replays the instructions the hook overwrote.

Everything the wrapper reads is game-owned: no persistent custom state.
See docs/HANDOFF_2026-08-01.md sections 21-22 for the evidence trail.
"""

from __future__ import annotations

import argparse
import csv
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
    sha256_bytes,
    split_address,
)
from build_subtitle_poc import assemble, encode_jump, patch_logical_sector, sha256_file
from build_subtitle_v37 import (
    ADVANCE_PX,
    COL1_CHARS,
    COL2_CHARS,
    DIG_CHARS,
    DOT_ADVANCE_PX,
    FORMAT_PREFIX,
    GLYPH_BYTES,
    RECT_COL1,
    RECT_COL2,
    RECT_DIG,
    SECTION_MAGIC,
    WIDTH_PATCH_COUNT,
    WIDTH_PATCH_FIRST,
    WIDTH_TABLE_RAM,
    GroupLayout,
    assert_no_load_delay_hazard,
    make_raw_sector,
    msf_word,
)
from build_subtitle_scene_poc_v36 import STOCK_TEMPLATE_CLUT

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = ROOT / "work" / "analysis" / "jp_subtitle_scene_schedule.json"
KOREAN_PATH = ROOT / "workspace" / "text" / "source" / "v0x_record_korean.json"
CSV_PATH = ROOT / "workspace" / "text" / "source" / "usa_subtitle_timeline.csv"

TRANSIENT_BASE = 0x8014C000
DIGIT_STOCK_CLUT = 0x7678

WIN_HOOK_RAM = 0x8003A88C
WIN_TAKEN_RAM = 0x8003A89C
WIN_FALLTHROUGH_RAM = 0x8003A894
WIN_HOOK_BEFORE = struct.pack("<II", 0x10830003, 0x001080C2)

CUT_HOOK_RAM = 0x8003B334
CUT_CONTINUE_RAM = 0x8003B33C
CUT_HOOK_BEFORE = struct.pack("<II", 0x3C04800A, 0x8C843010)
CUT_MOVIE_ID_GP = 0x338
CUT_FRAME_RAM = 0x8013E6E4

# The old 0x8004A244 site runs once per ending (measured: 4 hits over three
# endings, frame always 1). The real per-frame point is the main loop, right
# after it calls the current state handler through the jump table.
END_HOOK_RAM = 0x80043204
END_RETURN_RAM = 0x80043210
END_VSYNC_RAM = 0x80078360
END_HOOK_BEFORE = struct.pack("<II", 0x3C04F200, 0x0C01E0D8)
END_INDEX_RAM = 0x800A3178
END_FRAME_RAM = 0x80128248
# The file loader stores its destination buffer here (0x80044AF0). During an
# ending it points at O14 (measured: 0x800AE6C0), so the wrapper reaches the
# appended Korean section without hardcoding an allocator result.
END_BUFFER_PTR_RAM = 0x8013E6D8

# --------------------------------------------------------------- v42 additions
# Movies never build an ordering table.  Measured over an ending playback,
# [ctx+0] -- the exact slot the official renderer's AddPrim writes -- held one
# single value for all 169 samples while the double buffer kept swapping, and
# the playback function 0x8003F60C draws through no OT at all (HANDOFF 26-27).
# So for video contexts the primitives have to reach the GPU directly.
#
# 0x80069570 is libgpu's linked-list DMA send (HANDOFF 28.3):
#   GP1 = 0x04000002 ; MADR = $a0 ; BCR = 0 ; CHCR = 0x01000401
# It takes the chain head in $a0 and nothing else.
CHAIN_DMA_SEND_RAM = 0x80069570
CONTEXT_PTR_RAM = 0x800A3010
# GPU port table (HANDOFF 28.2): 0x8008A590 holds the DMA2 CHCR address,
# reached as lui 0x8009 + (-0x5A70).
GPU_PORT_HI = 0x8009
CHCR_PORT_LO = -0x5A70
# A CHCR busy gate was tried and removed.  Counting hits at the send call over
# a whole ending gave ZERO (work/analysis/v44_send_hits.json): the check was
# taken every single frame, so nothing was ever sent.  libgpu's 0x80069570 is
# itself called with no busy check by the game's own PutDrawEnv path, so we now
# match that.  (DuckStation's GDB stub only maps main RAM, so the register could
# not be read back to confirm what the bit actually held.)
# Written into [ctx+0] before rendering, as `lui $t4, 0xFF` -> 0x00FF0000.
#
# The high byte MUST be zero.  AddPrim keeps the OT entry's high byte and only
# replaces the low 24 bits, so a terminator of 0xFFFFFFFF leaves the entry as
# 0xFF000000|prim -- a DMA chain node claiming 255 payload words.  v42's first
# build did exactly that: the send was skipped (the head read back negative)
# and the game's own later DrawOTag pushed 255 garbage words into the GPU,
# freezing the display while the CPU kept running.
#
# 0x00FF0000 still terminates the chain (the DMA stops on bit 23 of the link)
# and leaves the word count at 0, which is what a cleared OT entry holds.
OT_TERMINATOR = 0x00FF0000
# The drawing offset is (0,0) over the whole VRAM, but the buffer actually on
# screen sits at DISPENV.y (measured 240 during an ending).  Rather than
# prepending a GP0 E5 primitive -- which needs a chain node plus assembly code
# the cave cannot spare -- shift the subtitle's Y by DISPENV.y so it lands in
# the displayed buffer.  DISPENV is the main loop's `$s1 + 0x5C` and the render
# context is `$s1 + 0x70`, so DISPENV = [0x800A3010] - 0x14.
#
# Measured during an actual ending (HANDOFF 34): the movie is 320x240 and flips
# between VRAM (0,0) and (480,240), while normal screens are 640x240 at (0,0) /
# (0,240).  v43 shifted only Y and stayed invisible because the (480,240) buffer
# was still missed by 480 in X -- BOTH axes have to move.
#
# DISPENV starts with RECT {x, y, w, h} as shorts, so one `lw` yields
# (y << 16) | x and the loop unpacks it per line.  Note db_index (0x800A2F3C)
# stays 1 for the whole playback: it does NOT track the movie buffer, so DISPENV
# is the only valid source.
DISPENV_OFFSET = -0x14
# If a record rendered nothing, [ctx+0] would still hold the terminator and the
# DMA would be pointed at 0x00FF0000.  A runtime guard costs cave bytes we do
# not have (an `sltiu` against 0x200000 does not fit a 16-bit immediate, so the
# assembler expands it), so validate_records() rules the case out at build time
# instead -- the shipped data is fixed, so this is the stronger check.
def validate_records(path: str, group: int, records) -> None:
    for index, (start, end, lines) in enumerate(records):
        if not lines or not any(line.strip() for line in lines):
            raise ValueError(
                f"{path} group {group} record {index} ({start}-{end}) has no "
                "text: the video path would send an unterminated chain"
            )

CUT_FILE = "DATA3/O05.DAT"
END_FILE = "DATA3/O14.DAT"


def normalize_line(line: str) -> str:
    return (
        line.replace("...", "…")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", "'")
        .replace("”", "'")
        .replace("　", " ")
    )


def read_file(bin_path: Path, wanted: str, info: dict) -> tuple[bytes, dict]:
    entry = next(item for item in info["entries"] if item["path"] == wanted)
    with bin_path.open("rb") as source:
        data, forms = disc.read_extent(source, entry["lba"], entry["size"])
    if forms != {"form1"}:
        raise ValueError(f"{wanted} is not Form 1")
    return data, entry


def layout_lines(layout: GroupLayout, lines: list[str]) -> list[tuple[int, int, str]]:
    """Encode display lines, returning (x, y_offset_index, encoded)."""
    encoded_lines = []
    for index, line in enumerate(lines):
        encoded = layout.encode_line(line)
        width = sum(
            DOT_ADVANCE_PX if char == "." else ADVANCE_PX for char in encoded
        )
        encoded_lines.append((max(0, (320 - width) // 2), index, encoded))
    return encoded_lines


RECORD_STRIDE = 28
LINE_STRIDE = 8


def build_group_blob(records: list[tuple[int, int, list[str]]], font: Path) -> tuple[bytes, dict]:
    """One glyph atlas per record, because a group's whole script can far
    exceed the 34 reusable cells while a single on-screen record never does.

    layout: u32 record_count, then per record
      u16 f0, u16 f1, u16 line_count, u16 pad,
      u32 col1_off, u32 col2_off, u32 dig_off, u32 clut_off, u32 lines_off
    followed by per-record line tables (u16 x, u16 y, u32 string_off),
    strings and pixel blocks. All offsets are relative to the blob start.
    """
    clut = struct.pack("<16H", 0x0000, 0x8000, 0x7FFF, *([0x0000] * 13))
    built = []
    for start, end, lines in records:
        layout = GroupLayout(font)
        placed = layout_lines(layout, lines)
        col1, col2, dig, _meta = layout.build_pixel_blocks()
        entries = []
        for x, line_index, encoded in placed:
            y = 190 - 9 * (len(placed) - 1) + 18 * line_index
            entries.append((x, y, encoded))
        built.append((start, end, entries, layout.used, col1, col2, dig))

    header_size = 4 + RECORD_STRIDE * len(built)
    cursor = header_size
    line_tables = []
    for _start, _end, entries, _used, _c1, _c2, _dg in built:
        line_tables.append(cursor)
        cursor += LINE_STRIDE * len(entries)
    strings = bytearray()
    string_offsets = []
    for _start, _end, entries, _used, _c1, _c2, _dg in built:
        offsets = []
        for _x, _y, encoded in entries:
            offsets.append(cursor + len(strings))
            strings += (FORMAT_PREFIX + encoded).encode("ascii") + b"\x00"
        string_offsets.append(offsets)
    while len(strings) % 4:
        strings += b"\x00"
    cursor += len(strings)

    pixels = bytearray()
    block_offsets = []
    for _start, _end, _entries, used, col1, col2, dig in built:
        col1_off = cursor + len(pixels) if used["col1"] else 0
        if used["col1"]:
            pixels += col1
        col2_off = cursor + len(pixels) if used["col2"] else 0
        if used["col2"]:
            pixels += col2
        dig_off = cursor + len(pixels) if used["dig"] else 0
        if used["dig"]:
            pixels += dig
        clut_off = cursor + len(pixels)
        pixels += clut
        block_offsets.append((col1_off, col2_off, dig_off, clut_off))

    blob = bytearray(struct.pack("<I", len(built)))
    for index, (start, end, entries, _used, _c1, _c2, _dg) in enumerate(built):
        col1_off, col2_off, dig_off, clut_off = block_offsets[index]
        blob += struct.pack(
            "<4H5I",
            start,
            end,
            len(entries),
            0,
            col1_off,
            col2_off,
            dig_off,
            clut_off,
            line_tables[index],
        )
    for index, (_start, _end, entries, _used, _c1, _c2, _dg) in enumerate(built):
        for (x, y, _encoded), string_off in zip(entries, string_offsets[index]):
            blob += struct.pack("<2HI", x, y, string_off)
    blob += strings + pixels
    meta = {
        "records": [
            {
                "f0": start,
                "f1": end,
                "lines": [text for _x, _y, text in entries],
            }
            for start, end, entries, _used, _c1, _c2, _dg in built
        ]
    }
    return bytes(blob), meta


def build_section(groups: dict[int, list[tuple[int, int, list[str]]]], font: Path,
                  max_groups: int) -> tuple[bytes, dict]:
    """groups: index -> [(start_frame, end_frame, korean lines)]."""
    blobs: list[bytes | None] = [None] * max_groups
    meta_groups = []
    for index in range(max_groups):
        records = groups.get(index)
        if not records:
            continue
        validate_records(getattr(groups, "source_path", "?"), index, records)
        blobs[index], blob_meta = build_group_blob(records, font)
        meta_groups.append(
            {"group": index, "blob_size": len(blobs[index]), **blob_meta}
        )
    header = bytearray(struct.pack("<I", SECTION_MAGIC)) + bytearray(max_groups * 4)
    cursor = len(header)
    body = bytearray()
    for index, blob in enumerate(blobs):
        if blob is None:
            continue
        struct.pack_into("<I", header, 4 + index * 4, cursor)
        body += blob
        cursor += len(blob)
        while cursor % 4:
            body += b"\x00"
            cursor += 1
    section = bytes(header) + bytes(body)
    return section, {"size": len(section), "groups": meta_groups}


def collect_v0x(font: Path) -> dict[str, dict[int, list]]:
    schedule = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    korean = json.loads(KOREAN_PATH.read_text(encoding="utf-8"))
    result: dict[str, dict[int, list]] = {}
    for entry in schedule["files"]:
        path = entry["path"]
        per_group: dict[int, list] = {}
        for group in entry["groups"]:
            tokens = group.get("schedule", [])
            text_tokens = [t for t in tokens if t["lines_en"] or t.get("lines_ko")]
            records = korean.get(path, {}).get(str(group["group"]))
            if not records:
                continue
            if len(records) != len(text_tokens):
                raise ValueError(
                    f"{path} group {group['group']}: {len(records)} korean "
                    f"records vs {len(text_tokens)} tokens"
                )
            built = []
            for index, token in enumerate(text_tokens):
                position = tokens.index(token)
                start = token["frame"]
                end = (
                    tokens[position + 1]["frame"]
                    if position + 1 < len(tokens)
                    else group["length_frames"]
                )
                built.append((start, end, [normalize_line(l) for l in records[index]]))
            per_group[group["group"]] = built
        result[path] = per_group
    return result


def collect_movie(usa_bin: Path, info: dict, path: str) -> dict[int, list]:
    """Read a USA movie subtitle block and pair it with the Korean CSV."""
    data, _entry = read_file(usa_bin, path, info)
    count = struct.unpack_from("<I", data, 0)[0]
    offsets = struct.unpack_from(f"<{count + 1}I", data, 4)
    block = data[offsets[-2] : offsets[-1]]
    group_count = struct.unpack_from("<I", block, 0)[0]
    boundaries = list(struct.unpack_from(f"<{group_count + 1}I", block, 4))
    total = boundaries[-1]
    base = 4 + (group_count + 1) * 4
    token_offsets = list(struct.unpack_from(f"<{total}I", block, base))

    korean_rows: list[str] = []
    with CSV_PATH.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["source_path"] == path and row["korean_translation"].strip():
                korean_rows.append(row["korean_translation"])
    cursor = 0
    groups: dict[int, list] = {}
    for group in range(group_count):
        records = []
        entries = []
        for token in range(boundaries[group], boundaries[group + 1]):
            position = base + token_offsets[token]
            head = block[position : position + 5]
            if len(head) < 5 or not head.isdigit():
                continue
            frame = int(head[:4])
            lines = int(head[4:5])
            position += 5
            texts = []
            for _ in range(lines):
                end = block.index(0, position)
                texts.append(block[position:end].decode("ascii", errors="replace"))
                position = end + 1
            entries.append((frame, texts))
        for index, (frame, texts) in enumerate(entries):
            end_frame = entries[index + 1][0] if index + 1 < len(entries) else frame + 180
            if not texts or not any(t.strip() for t in texts):
                continue
            if cursor >= len(korean_rows):
                raise ValueError(f"{path}: ran out of Korean rows at group {group}")
            korean = [normalize_line(l) for l in korean_rows[cursor].splitlines()]
            cursor += 1
            records.append((frame, end_frame, korean))
        if records:
            groups[group] = records
    return groups


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--usa-image", type=Path, default=ROOT / "original" / "Bushido Blade (USA).bin")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--font",
        type=Path,
        default=Path(r"G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf"),
    )
    parser.add_argument("--base-name", default="Bushido Blade (Japan) [KO Subtitle v47dbg]")
    parser.add_argument(
        "--skip",
        nargs="*",
        default=[],
        choices=["win", "cut", "end"],
        help="leave these hooks unpatched (bisecting runtime failures)",
    )
    parser.add_argument(
        "--no-movies",
        action="store_true",
        help="skip O05/O14 entirely (V0X only, matching the proven v38 data)",
    )
    args = parser.parse_args()

    if sha256_file(args.image) != EXPECTED_BIN_SHA256:
        raise ValueError("unsupported source BIN hash")
    info = disc.inspect(args.image)
    exe_entry = next(e for e in info["entries"] if e["path"] == EXE_PATH)
    if exe_entry["sha256"].upper() != EXPECTED_EXE_SHA256:
        raise ValueError("unsupported Japanese executable baseline")
    original_exe, _entry = read_file(args.image, EXE_PATH, info)

    usa_info = disc.inspect(args.usa_image)

    # ---------------------------------------------------------------- data
    targets: list[tuple[str, dict[int, list], int]] = []
    v0x = collect_v0x(args.font)
    for index in range(9):
        path = f"DATA7/V0{index}.DAT"
        targets.append((path, v0x[path], 12))
    # --skip also drops that context's data file, so a context can be taken
    # out of a build entirely while bisecting runtime failures.
    if not args.no_movies and "cut" not in args.skip:
        targets.append((CUT_FILE, collect_movie(args.usa_image, usa_info, CUT_FILE), 12))
    if not args.no_movies and "end" not in args.skip:
        targets.append((END_FILE, collect_movie(args.usa_image, usa_info, END_FILE), 12))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = args.output_dir / f"{args.base_name}.bin"
    output_cue = args.output_dir / f"{args.base_name}.cue"
    report_path = args.output_dir / "build_report.json"
    shutil.copyfile(args.image, output_bin)

    from build_options_poc import rebuild_mode2_form1_integrity

    image_sectors = args.image.stat().st_size // disc.RAW_SECTOR_SIZE
    next_lba = image_sectors
    relocations = []
    cave_table: list[tuple[int, int]] = []

    with args.image.open("rb") as source:
        for path, groups, max_groups in targets:
            original, entry = read_file(args.image, path, info)
            word3 = struct.unpack_from("<I", original, 0xC)[0]
            section_offset = (len(original) + 3) & ~3
            section, section_meta = build_section(groups, args.font, max_groups)
            new_file = (
                bytes(original)
                + b"\x00" * (section_offset - len(original))
                + section
            )
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

            parent_path = path.rsplit("/", 1)[0]
            parent = next(e for e in info["entries"] if e["path"] == parent_path)
            directory_data, _forms = disc.read_extent(
                source, parent["lba"], parent["size"]
            )
            wanted = path.rsplit("/", 1)[1].upper()
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
                raise ValueError(f"directory record not found: {path}")

            def patch_record(raw: bytearray, in_sector=record_offset % 2048,
                             new_lba=new_lba, size=len(new_file)):
                base = disc.MODE2_FORM1_DATA_OFFSET + in_sector
                struct.pack_into("<I", raw, base + 2, new_lba)
                struct.pack_into(">I", raw, base + 6, new_lba)
                struct.pack_into("<I", raw, base + 10, size)
                struct.pack_into(">I", raw, base + 14, size)

            patch_logical_sector(
                output_bin, parent["lba"] + record_offset // 2048, patch_record
            )
            relocations.append(
                {
                    "path": path,
                    "old_lba": entry["lba"],
                    "old_size": entry["size"],
                    "new_lba": new_lba,
                    "new_size": len(new_file),
                    "section_offset": section_offset,
                    "word3": f"0x{word3:X}",
                    "section": section_meta,
                }
            )
            cave_table.append((word3, section_offset))

    # ------------------------------------------------------------ payload
    enable_cut = not args.no_movies and "cut" not in args.skip
    payload, wrapper_meta, data_layout = build_payload(
        cave_table, original_exe, enable_cut=enable_cut
    )

    patched_exe = bytearray(original_exe)
    load_table_patches = []
    for relocation in relocations:
        before = struct.pack(
            "<II", msf_word(relocation["old_lba"]), relocation["old_size"]
        )
        after = struct.pack(
            "<II", msf_word(relocation["new_lba"]), relocation["new_size"]
        )
        first = original_exe.find(before)
        if first < 0 or original_exe.find(before, first + 1) >= 0:
            raise ValueError(f"load-table entry not unique for {relocation['path']}")
        patched_exe[first : first + 8] = after
        load_table_patches.append(
            {
                "path": relocation["path"],
                "ram": f"0x{EXE_RAM_BASE + first - EXE_HEADER_SIZE:08X}",
                "before": before.hex(),
                "after": after.hex(),
            }
        )

    hook_writes = []
    hook_plan = [
        ("win", WIN_HOOK_RAM, WIN_HOOK_BEFORE,
         encode_jump(wrapper_meta["win_stub"], False) + b"\0\0\0\0"),
        ("end", END_HOOK_RAM, END_HOOK_BEFORE,
         encode_jump(wrapper_meta["end_stub"], False)),
    ]
    if enable_cut:
        # Without the cutscene context there is no stub to jump to, so the
        # hook site must stay untouched.
        hook_plan.insert(
            1,
            ("cut", CUT_HOOK_RAM, CUT_HOOK_BEFORE,
             encode_jump(wrapper_meta["cut_stub"], False) + b"\0\0\0\0"),
        )
    for name, ram, before, after in hook_plan:
        if name in args.skip:
            continue
        offset = EXE_HEADER_SIZE + ram - EXE_RAM_BASE
        actual = bytes(original_exe[offset : offset + len(before)])
        if actual != before:
            raise ValueError(
                f"hook expected-write mismatch at 0x{ram:08X}: {actual.hex()}"
            )
        patched_exe[offset : offset + len(after)] = after
        hook_writes.append(
            {"ram": f"0x{ram:08X}", "before": before.hex(), "after": after.hex()}
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

    reparsed = disc.inspect(output_bin)
    exe_reparsed = next(e for e in reparsed["entries"] if e["path"] == EXE_PATH)
    with output_bin.open("rb") as track:
        disc_exe, forms = disc.read_extent(
            track, exe_reparsed["lba"], exe_reparsed["size"]
        )
        exe_ok = forms == {"form1"} and disc_exe == bytes(patched_exe)
        checks = []
        for relocation in relocations:
            new_entry = next(
                e for e in reparsed["entries"] if e["path"] == relocation["path"]
            )
            checks.append(
                {
                    "path": relocation["path"],
                    "lba_ok": new_entry["lba"] == relocation["new_lba"],
                    "size_ok": new_entry["size"] == relocation["new_size"],
                }
            )

    report = {
        "gate_id": "BBJ-SUB-V41-001",
        "status": "static_build_passed_runtime_pending",
        "contexts": {
            "win": f"0x{WIN_HOOK_RAM:08X}",
            "cutscene": f"0x{CUT_HOOK_RAM:08X}",
            "ending": f"0x{END_HOOK_RAM:08X}",
        },
        "wrapper": wrapper_meta,
        "data_layout": data_layout,
        "cave_table": [
            {"word3": f"0x{w:X}", "section_offset": f"0x{s:X}"} for w, s in cave_table
        ],
        "relocations": relocations,
        "expected_writes": {
            "load_table": load_table_patches,
            "hooks": hook_writes,
            "code_cave": {
                "ram_start": f"0x{CAVE_START_RAM:08X}",
                "ram_end": f"0x{CAVE_END_RAM:08X}",
                "source_sha256": cave_source_hash,
            },
        },
        "static_checks": {"exe_roundtrip": exe_ok, "relocations": checks},
        "output": {
            "bin": str(output_bin),
            "cue": str(output_cue),
            "bin_sha256": sha256_file(output_bin),
        },
        "runtime": {"status": "pending"},
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"output_cue={output_cue}")
    print(f"payload={wrapper_meta['payload_size']}/{CAVE_END_RAM - CAVE_START_RAM}")
    print(f"exe_roundtrip={exe_ok}")
    for check in checks:
        print(check)
    print(f"output_sha256={report['output']['bin_sha256']}")


def build_payload(cave_table, original_exe, enable_cut=True, retry_with=None):
    """Assemble the shared render body plus the three context stubs.

    The shared body is an ordinary `jal` callee: inputs $s5 (group blob
    pointer) and $s7 (frame), saves $ra/$s1/$s2/$s6, ends with `jr $ra`.
    Every stub pushes the registers it is about to overwrite, computes the
    two inputs, calls the body, restores, then replays the instructions its
    hook overwrote before returning to the game.
    """
    shared_ram = CAVE_START_RAM
    data_ram = retry_with if retry_with is not None else CAVE_START_RAM + 0x3E0
    rect_col1 = data_ram
    rect_col2 = data_ram + 8
    rect_dig = data_ram + 16
    rect_clut = data_ram + 24
    table_ram = data_ram + 32
    table_data = b"".join(struct.pack("<II", w, s) for w, s in cave_table)
    table_data += struct.pack("<II", 0, 0)
    width_offset = (
        EXE_HEADER_SIZE + WIDTH_TABLE_RAM - EXE_RAM_BASE + WIDTH_PATCH_FIRST * 2
    )
    # The stock widths all fit in a byte, so back them up as u8 to keep the
    # cave data small; the restore loop widens them again.
    width_pairs = struct.unpack_from(
        f"<{WIDTH_PATCH_COUNT}H", original_exe, width_offset
    )
    if any(value > 0xFF for value in width_pairs):
        raise ValueError("stock glyph width does not fit in a byte")
    width_backup = bytes(width_pairs)
    width_backup_ram = table_ram + len(table_data)
    data = (
        struct.pack("<hhhh", *RECT_COL1)
        + struct.pack("<hhhh", *RECT_COL2)
        + struct.pack("<hhhh", *RECT_DIG)
        + struct.pack("<hhhh", *CLUT_RECT)
        + table_data
        + width_backup
        + bytes(24)          # debug slots + self-maintained frame counter
    )
    debug_ram = data_ram + len(data) - 24
    counter_ram = debug_ram + 16
    end_ram = data_ram + len(data)
    if end_ram > CAVE_END_RAM:
        raise ValueError(f"cave data overflow: end=0x{end_ram:08X}")

    split = {
        name: split_address(value)
        for name, value in {
            "rect_col1": rect_col1,
            "rect_col2": rect_col2,
            "rect_dig": rect_dig,
            "rect_clut": rect_clut,
            "table": table_ram,
            "width_backup": width_backup_ram,
            "debug": debug_ram,
            "counter": counter_ram,
        }.items()
    }
    width_patch_ram = WIDTH_TABLE_RAM + WIDTH_PATCH_FIRST * 2
    template_patch_ram = JP_GLYPH_TABLE_RAM + 3 * 16 + 6

    # The video output path costs ~110 bytes and the cave was already at
    # 1228/1233.  Dropping the cutscene context reclaims its dispatch and
    # group block; intro cutscenes cannot work until O05 is reworked as a
    # container block addition anyway (HANDOFF 24), so nothing regresses.
    if enable_cut:
        cut_dispatch = "    ori $t0, $zero, 1\n    beq $t9, $t0, group_cut\n    nop"
        cut_group_block = f"""group_cut:
    lw $t2, 0x{CUT_MOVIE_ID_GP:X}($gp)
    lui $t7, 0x{CUT_FRAME_RAM >> 16:X}
    ori $t7, $t7, 0x{CUT_FRAME_RAM & 0xFFFF:X}
    lw $s7, 0($t7)
    nop
    sll $s7, $s7, 1
"""
    else:
        cut_dispatch = ""
        cut_group_block = ""

    # Record offsets: f0@0 f1@2 line_count@4 col1@8 col2@12 dig@16 clut@20
    # lines@24 (see build_group_blob).
    upload_blocks = "\n".join(
        f"""
    lui $a0, {split[rect][0]}
    ori $a0, $a0, {split[rect][1]}
    lw $t0, {slot}($s1)
    nop
    beq $t0, $zero, skip_upload_{slot}
    addu $a1, $s6, $t0
    jal 0x{JP_LOAD_IMAGE_RAM:08x}
skip_upload_{slot}:
"""
        for slot, rect in (
            (8, "rect_col1"),
            (12, "rect_col2"),
            (16, "rect_dig"),
            (20, "rect_clut"),
        )
    )

    # $t9 selects the context so all three hooks share one body: 0 win,
    # 1 cutscene, 2 ending. Duplicating the table walk in each stub would
    # not fit the 1233-byte cave.
    shared_source = f"""
    addiu $sp, $sp, -0x40
    sw $ra, 0x3c($sp)
    sw $s1, 0x38($sp)
    sw $s2, 0x34($sp)
    sw $s5, 0x2c($sp)
    sw $s6, 0x30($sp)
    sw $s7, 0x28($sp)
    ori $t0, $zero, 2
    bne $t9, $t0, base_transient
    nop
    lui $t5, 0x{END_BUFFER_PTR_RAM >> 16:X}
    ori $t5, $t5, 0x{END_BUFFER_PTR_RAM & 0xFFFF:X}
    lw $t5, 0($t5)
    nop
    beq $t5, $zero, all_done
    j have_base
base_transient:
    lui $t5, 0x8014
    ori $t5, $t5, 0xC000
have_base:
    lw $t7, 0xC($t5)
    lui $t4, {split['table'][0]}
    ori $t4, $t4, {split['table'][1]}
table_walk:
    lw $t8, 0($t4)
    lw $t3, 4($t4)
    beq $t8, $zero, all_done
    beq $t8, $t7, table_found
    addiu $t4, $t4, 8
    j table_walk
table_found:
    addu $t3, $t5, $t3
    beq $t9, $zero, group_win
{cut_dispatch}
    lui $t7, 0x{END_INDEX_RAM >> 16:X}
    lh $t2, 0x{END_INDEX_RAM & 0xFFFF:X}($t7)
    nop
    blez $t2, all_done
    slti $t7, $t2, 7
    beq $t7, $zero, group_end_good
    addiu $t2, $t2, -1
    j group_end_join
group_end_good:
    addiu $t2, $t2, -7
    sll $t2, $t2, 1
    addiu $t2, $t2, 1
    j group_end_frame
group_end_join:
    sll $t2, $t2, 1
group_end_frame:
    lui $t7, 0x{CUT_FRAME_RAM >> 16:X}
    ori $t7, $t7, 0x{CUT_FRAME_RAM & 0xFFFF:X}
    lw $s7, 0($t7)
    nop
    sll $s7, $s7, 1
    j have_group
group_win:
    lw $t2, 0x2F8($gp)
    lw $t7, 4($t5)
    nop
    subu $t2, $t2, $t5
    subu $t2, $t2, $t7
    srl $t2, $t2, 3
    move $s7, $a0
    j have_group
{cut_group_block}have_group:
    lui $t6, {split['debug'][0]}
    ori $t6, $t6, {split['debug'][1]}
    sw $s7, 4($t6)
    sltiu $t7, $t2, 12
    beq $t7, $zero, all_done
    sll $t2, $t2, 2
    addu $t2, $t3, $t2
    lw $t2, 4($t2)
    nop
    beq $t2, $zero, all_done
    addu $s5, $t3, $t2
    move $s6, $s5
    lw $s2, 0($s5)
    addiu $s1, $s5, 4
record_loop:
    beq $s2, $zero, all_done
    lhu $t0, 0($s1)
    lhu $t1, 2($s1)
    nop
    sltu $t2, $s7, $t0
    bne $t2, $zero, record_next
    sltu $t2, $s7, $t1
    bne $t2, $zero, record_hit
record_next:
    addiu $s1, $s1, {RECORD_STRIDE}
    addiu $s2, $s2, -1
    j record_loop
record_hit:
    lui $t6, {split['debug'][0]}
    ori $t6, $t6, {split['debug'][1]}
    lw $t4, 12($t6)
    nop
    addiu $t4, $t4, 1
    sw $t4, 12($t6)
{upload_blocks}
    lui $t3, 0x{template_patch_ram >> 16:X}
    ori $t3, $t3, 0x{template_patch_ram & 0xFFFF:X}
    ori $t5, $zero, 0x{CLUT_ID:X}
    ori $t6, $zero, 36
clut_patch:
    sh $t5, 0($t3)
    addiu $t3, $t3, 16
    addiu $t6, $t6, -1
    bne $t6, $zero, clut_patch
    lui $t3, 0x{width_patch_ram >> 16:X}
    ori $t3, $t3, 0x{width_patch_ram & 0xFFFF:X}
    ori $t5, $zero, 16
    ori $t6, $zero, {WIDTH_PATCH_COUNT}
width_patch:
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t6, $t6, -1
    bne $t6, $zero, width_patch

    lhu $s2, 4($s1)
    lw $t0, 24($s1)
    nop
    addu $s1, $s6, $t0
line_loop:
    beq $s2, $zero, items_done
    lui $t6, {split['debug'][0]}
    ori $t6, $t6, {split['debug'][1]}
    lw $t1, 8($t6)
    lhu $a1, 0($s1)
    addiu $t1, $t1, 1
    sw $t1, 8($t6)
    lhu $a2, 2($s1)
    lw $t0, 4($s1)
    nop
    addu $a0, $s6, $t0
    ori $a3, $zero, 15
    sw $a3, 0x10($sp)
    ori $t5, $zero, 0x1000
    sw $t5, 0x14($sp)
    ori $t5, $zero, 0x80
    sw $t5, 0x18($sp)
    sw $t5, 0x1c($sp)
    sw $t5, 0x20($sp)
    jal 0x{JP_TEXT_RENDERER_RAM:08x}
    addiu $s1, $s1, {LINE_STRIDE}
    addiu $s2, $s2, -1
    j line_loop
items_done:
    lui $t3, 0x{template_patch_ram >> 16:X}
    ori $t3, $t3, 0x{template_patch_ram & 0xFFFF:X}
    ori $t5, $zero, 0x{DIGIT_STOCK_CLUT:X}
    ori $t6, $zero, 36
    ori $t4, $zero, 26
restore_loop:
    sh $t5, 0($t3)
    addiu $t3, $t3, 16
    addiu $t6, $t6, -1
    bne $t6, $t4, restore_next
    ori $t5, $zero, 0x{STOCK_TEMPLATE_CLUT:X}
restore_next:
    bne $t6, $zero, restore_loop
    lui $t3, 0x{width_patch_ram >> 16:X}
    ori $t3, $t3, 0x{width_patch_ram & 0xFFFF:X}
    lui $t4, {split['width_backup'][0]}
    ori $t4, $t4, {split['width_backup'][1]}
    ori $t6, $zero, {WIDTH_PATCH_COUNT}
width_restore:
    lbu $t5, 0($t4)
    nop
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t4, $t4, 1
    addiu $t6, $t6, -1
    bne $t6, $zero, width_restore
all_done:
    lw $ra, 0x3c($sp)
    lw $s1, 0x38($sp)
    lw $s2, 0x34($sp)
    lw $s5, 0x2c($sp)
    lw $s6, 0x30($sp)
    lw $s7, 0x28($sp)
    addiu $sp, $sp, 0x40
    jr $ra
    """
    shared_code = assemble(shared_source, shared_ram)
    assert_no_load_delay_hazard(shared_code, shared_ram)

    # Thin stubs: set the context id, call the shared body, then replay the
    # instructions the hook overwrote.
    win_stub_ram = shared_ram + len(shared_code)
    win_source = f"""
    addiu $sp, $sp, -0x10
    sw $ra, 0xc($sp)
    sw $a0, 8($sp)
    sw $v1, 4($sp)
    ori $t9, $zero, 0
    jal 0x{shared_ram:08x}
    lw $ra, 0xc($sp)
    lw $a0, 8($sp)
    lw $v1, 4($sp)
    addiu $sp, $sp, 0x10
    srl $s0, $s0, 3
    beq $a0, $v1, win_taken
    j 0x{WIN_FALLTHROUGH_RAM:08x}
win_taken:
    j 0x{WIN_TAKEN_RAM:08x}
    """
    win_code = assemble(win_source, win_stub_ram)
    assert_no_load_delay_hazard(win_code, win_stub_ram)

    cut_stub_ram = win_stub_ram + len(win_code)
    if enable_cut:
        cut_source = f"""
    addiu $sp, $sp, -0x10
    sw $ra, 0xc($sp)
    ori $t9, $zero, 1
    jal 0x{shared_ram:08x}
    lw $ra, 0xc($sp)
    addiu $sp, $sp, 0x10
    lui $a0, 0x800a
    lw $a0, 0x3010($a0)
    j 0x{CUT_CONTINUE_RAM:08x}
    """
        cut_code = assemble(cut_source, cut_stub_ram)
        assert_no_load_delay_hazard(cut_code, cut_stub_ram)
    else:
        cut_code = b""

    end_stub_ram = cut_stub_ram + len(cut_code)
    # Replays `lui $a0,0xF200 / jal 0x80078360 / ori $a0,$a0,1` and returns
    # to 0x80043210 with $v0 (the call's result) intact.
    end_source = f"""
    addiu $sp, $sp, -0x10
    sw $ra, 0xc($sp)
    ori $t9, $zero, 2
    jal 0x{shared_ram:08x}
    lw $ra, 0xc($sp)
    addiu $sp, $sp, 0x10
    lui $a0, 0xF200
    ori $a0, $a0, 1
    jal 0x{END_VSYNC_RAM:08x}
    j 0x{END_RETURN_RAM:08x}
    """
    end_code = assemble(end_source, end_stub_ram)
    assert_no_load_delay_hazard(end_code, end_stub_ram)

    code_end = end_stub_ram + len(end_code)
    if retry_with is None:
        # Instruction encodings are fixed width, so re-running with the data
        # packed directly after the code yields identical code sizes.
        return build_payload(
            cave_table,
            original_exe,
            enable_cut=enable_cut,
            retry_with=(code_end + 3) & ~3,
        )
    if code_end > data_ram:
        raise ValueError(
            f"wrapper code overruns the data block: end=0x{code_end:08X} "
            f"> 0x{data_ram:08X}"
        )

    payload = bytearray(end_ram - CAVE_START_RAM)
    for base, blob in (
        (shared_ram, shared_code),
        (win_stub_ram, win_code),
        (cut_stub_ram, cut_code),
        (end_stub_ram, end_code),
        (data_ram, data),
    ):
        offset = base - CAVE_START_RAM
        payload[offset : offset + len(blob)] = blob

    meta = {
        "shared_body": f"0x{shared_ram:08X}",
        "shared_size": len(shared_code),
        "win_stub": win_stub_ram,
        "win_size": len(win_code),
        "cut_stub": cut_stub_ram,
        "cut_size": len(cut_code),
        "end_stub": end_stub_ram,
        "end_size": len(end_code),
        "code_end": f"0x{code_end:08X}",
        "data_start": f"0x{data_ram:08X}",
        "payload_end_ram": f"0x{end_ram:08X}",
        "payload_size": len(payload),
        "cave_free": CAVE_END_RAM - end_ram,
    }
    layout = {
        "rect_col1": f"0x{rect_col1:08X}",
        "rect_col2": f"0x{rect_col2:08X}",
        "rect_dig": f"0x{rect_dig:08X}",
        "rect_clut": f"0x{rect_clut:08X}",
        "table": f"0x{table_ram:08X}",
        "width_backup": f"0x{width_backup_ram:08X}",
    }
    return bytes(payload), meta, layout


if __name__ == "__main__":
    main()
