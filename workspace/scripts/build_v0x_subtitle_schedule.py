#!/usr/bin/env python3
"""Build the definitive (V0X file, group) -> subtitle schedule mapping.

USA DATA7/V0X.DAT = JP file + appended subtitle section at jp_size:
  u32 magic            (observed 0x1111)
  u32 record_count     (tokens with line_count > 0)
  u32 boundaries[]     per-group starting token index, ending with total
  u32 token_offsets[]  boundaries[-1] entries, relative to this array's start
  text blob            tokens: 4 ASCII digits frame + 1 digit line_count,
                       then line_count NUL-terminated lines. count 0 = clear.

Korean text joins from workspace/text/source/usa_subtitle_timeline.csv by
(file, in-file record order).

Runtime key (proven, zero persistent state):
  file  : header-word fingerprint of the buffer at 0x8014C000
  group : ([gp+0x2F8] - 0x8014C000 - [0x8014C004]) / 8
  frame : scene frame (a0 at 0x8003A88C, counts 1..length)

Output: work/analysis/jp_subtitle_scene_schedule.json
"""

from __future__ import annotations

import csv
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc

ROOT = Path(__file__).resolve().parents[2]
JP_BIN = ROOT / "original" / "Bushido Blade (Japan).bin"
USA_BIN = ROOT / "original" / "Bushido Blade (USA).bin"
CSV_PATH = ROOT / "workspace" / "text" / "source" / "usa_subtitle_timeline.csv"
OUTPUT = ROOT / "work" / "analysis" / "jp_subtitle_scene_schedule.json"
TRANSIENT_BASE = 0x8014C000


def read_files(bin_path: Path, names: list[str]) -> dict[str, bytes]:
    info = disc.inspect(bin_path)
    found: dict[str, bytes] = {}
    with bin_path.open("rb") as source:
        for entry in info["entries"]:
            if entry["path"] in names:
                data, _forms = disc.read_extent(source, entry["lba"], entry["size"])
                found[entry["path"]] = data
    return found


def parse_groups(data: bytes) -> list[dict]:
    table_offset = struct.unpack_from("<I", data, 4)[0]
    groups = []
    for group in range(16):
        record_offset = struct.unpack_from("<I", data, 0x38 + group * 4)[0]
        cmp_offset = table_offset + group * 8
        if cmp_offset + 8 > len(data) or record_offset + 16 > len(data):
            break
        slot_word, length = struct.unpack_from("<II", data, cmp_offset)
        if slot_word == 0 or slot_word > 0x100 or length == 0 or length > 0x10000:
            break
        stream_count = struct.unpack_from("<I", data, record_offset)[0]
        if stream_count == 0 or stream_count > 8:
            break
        groups.append(
            {
                "group": group,
                "cmp_ptr_runtime": f"0x{TRANSIENT_BASE + cmp_offset:08X}",
                "slot_word": slot_word,
                "length_frames": length,
            }
        )
    return groups


def parse_tokens(usa: bytes, offsets_base: int, total_tokens: int) -> list[dict] | None:
    token_offsets = [
        struct.unpack_from("<I", usa, offsets_base + index * 4)[0]
        for index in range(total_tokens)
    ]
    tokens = []
    for relative in token_offsets:
        position = offsets_base + relative
        header = usa[position : position + 5]
        if len(header) < 5:
            # final offset can point at end-of-file as a terminator
            tokens.append({"frame": None, "lines": [], "eof_sentinel": True})
            continue
        if not header.isdigit():
            return None
        frame = int(header[:4].decode("ascii"))
        line_count = int(header[4:5].decode("ascii"))
        position += 5
        lines = []
        for _ in range(line_count):
            end = usa.find(0, position)
            if end < 0:
                return None
            lines.append(usa[position:end].decode("ascii", errors="replace"))
            position = end + 1
        tokens.append({"frame": frame, "lines": lines})
    return tokens


def parse_usa_section(usa: bytes) -> tuple[list[list[dict]], int]:
    """The USA file appends one section (registered last in the header's
    section-offset table) holding the subtitle timeline:
      u32 record_count, u32 boundaries[], u32 token_offsets[], text blob.
    The boundary array length is not stored; find the split by validating
    that every token parses as ASCII digits."""
    section_count = struct.unpack_from("<I", usa, 0)[0]
    section_offsets = [
        struct.unpack_from("<I", usa, 4 + index * 4)[0]
        for index in range(section_count)
    ]
    section = max(section_offsets)
    record_count = struct.unpack_from("<I", usa, section)[0]
    for boundary_count in range(2, 33):
        words = [
            struct.unpack_from("<I", usa, section + 4 + index * 4)[0]
            for index in range(boundary_count)
        ]
        if words[0] != 0 or any(b > a for a, b in zip(words[1:], words[:-1])):
            continue
        total_tokens = words[-1]
        if total_tokens == 0 or total_tokens > 512:
            continue
        offsets_base = section + 4 + boundary_count * 4
        first_offset = struct.unpack_from("<I", usa, offsets_base)[0]
        if first_offset != 4 * total_tokens:
            continue
        tokens = parse_tokens(usa, offsets_base, total_tokens)
        if tokens is None:
            continue
        per_group = [
            tokens[words[index] : words[index + 1]]
            for index in range(len(words) - 1)
        ]
        return per_group, record_count
    raise ValueError("could not locate subtitle boundary/token arrays")


def main() -> None:
    names = [f"DATA7/V0{index}.DAT" for index in range(9)]
    jp_files = read_files(JP_BIN, names)
    usa_files = read_files(USA_BIN, names)

    korean: dict[str, list[dict]] = {}
    with CSV_PATH.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["source_path"].startswith("DATA7/V0"):
                korean.setdefault(row["source_path"], []).append(row)
    for rows in korean.values():
        rows.sort(key=lambda row: int(row["file_offset_hex"], 16))

    result = {
        "runtime_key": {
            "transient_base": f"0x{TRANSIENT_BASE:08X}",
            "group_formula": "([gp+0x2F8] - 0x8014C000 - [0x8014C004]) / 8",
            "frame_source": "a0 at 0x8003A88C (1..length)",
        },
        "files": [],
    }
    total_records = 0
    for name in names:
        jp = jp_files.get(name)
        usa = usa_files.get(name)
        if jp is None or usa is None:
            continue
        groups = parse_groups(jp)
        per_group_tokens, record_count = parse_usa_section(usa)
        csv_rows = korean.get(name, [])
        csv_cursor = 0
        for group_index, tokens in enumerate(per_group_tokens):
            if group_index >= len(groups):
                break
            schedule = []
            for token in tokens:
                if token.get("eof_sentinel"):
                    continue
                item = {
                    "frame": token["frame"],
                    "lines_en": token["lines"],
                    "lines_ko": None,
                }
                if token["lines"]:
                    joined = False
                    if csv_cursor < len(csv_rows):
                        row = csv_rows[csv_cursor]
                        token_text = " ".join(
                            line.strip() for line in token["lines"]
                        )
                        csv_text = " ".join(
                            line.strip()
                            for line in row["english_text"].splitlines()
                        )
                        if token_text == csv_text:
                            item["timeline_id"] = row["timeline_id"]
                            item["lines_ko"] = row["korean_translation"].splitlines()
                            csv_cursor += 1
                            joined = True
                    if not joined:
                        # wordless pause subtitles the CSV analyzer skipped
                        # (e.g. `..." ): synthesize Korean punctuation directly
                        item["lines_ko"] = [
                            line.replace("`", "“")
                            .replace('"', "”")
                            .replace("......", "……")
                            .replace("...", "…")
                            for line in token["lines"]
                        ]
                        item["synthesized"] = True
                    total_records += 1
                schedule.append(item)
            groups[group_index]["schedule"] = schedule
        result["files"].append(
            {
                "path": name,
                "jp_size": len(jp),
                "jp_header_words": [
                    f"0x{value:X}" for value in struct.unpack_from("<4I", jp, 0)
                ],
                "usa_record_count_field": record_count,
                "csv_rows": len(csv_rows),
                "csv_joined": csv_cursor,
                "groups": groups,
            }
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for entry in result["files"]:
        joined = f"csv {entry['csv_joined']}/{entry['csv_rows']}"
        counts = [len(group.get("schedule", [])) for group in entry["groups"]]
        print(f"{entry['path']}: tokens_per_group={counts} {joined}")
    print(f"total_text_records={total_records}")
    print(f"output={OUTPUT}")


if __name__ == "__main__":
    main()
