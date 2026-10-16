#!/usr/bin/env python3
"""Compare the Japanese and USA discs and inventory the USA subtitle system."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from inspect_psx_disc import inspect, read_extent  # noqa: E402


TEXT_FILES = {
    "DATA3/O05.DAT",
    "DATA3/O14.DAT",
    "DATA3/O20.DAT",
    *(f"DATA7/V{i:02d}.DAT" for i in range(9)),
    "SCUS_941.80",
}

NEEDLES = [
    b"For the first time",
    b"I've taken a life",
    b"Select Character",
    b"TATSUMI",
    b"clean fight",
    b"Meikyokan",
    b"secret group",
    b"assassins",
]

# One record is either START(5)+text or END(5)+START(5)+text.
RECORD_RE = re.compile(rb"(?<![0-9])([0-9]{5})([0-9]{5})?([ -~]{1,160})\x00")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_map(result: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in result["entries"] if not entry["is_dir"]}


def exe_info(data: bytes) -> dict:
    if data[:8] != b"PS-X EXE":
        raise ValueError("not a PS-X EXE")
    return {
        "pc": f"0x{struct.unpack_from('<I', data, 0x10)[0]:08X}",
        "load_address": f"0x{struct.unpack_from('<I', data, 0x18)[0]:08X}",
        "text_size": struct.unpack_from("<I", data, 0x1C)[0],
    }


def find_all(data: bytes, needle: bytes) -> list[int]:
    positions = []
    start = 0
    while True:
        offset = data.lower().find(needle.lower(), start)
        if offset < 0:
            return positions
        positions.append(offset)
        start = offset + 1


def ram_to_file_offset(address: int, load_address: int = 0x80010000) -> int:
    return address - load_address + 0x800


def jal_calls(data: bytes, target: int, load_address: int = 0x80010000) -> list[str]:
    calls = []
    for offset in range(0x800, len(data) - 3, 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if word >> 26 != 3:
            continue
        pc = load_address + offset - 0x800
        destination = ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)
        if destination == target:
            calls.append(f"0x{pc:08X}")
    return calls


def read_pointer_strings(data: bytes, table_address: int, count: int) -> list[str]:
    values = []
    table_offset = ram_to_file_offset(table_address)
    for index in range(count):
        pointer = struct.unpack_from("<I", data, table_offset + index * 4)[0]
        offset = ram_to_file_offset(pointer)
        end = data.index(0, offset)
        values.append(data[offset:end].decode("ascii"))
    return values


def subtitle_records(path: str, data: bytes, minimum_offset: int = 0) -> list[dict]:
    records = []
    for match in RECORD_RE.finditer(data):
        if match.start() < minimum_offset:
            continue
        text = match.group(3).decode("ascii")
        if not any(character.isalpha() for character in text):
            continue
        end_code = match.group(1).decode("ascii") if match.group(2) else None
        start_code = (match.group(2) or match.group(1)).decode("ascii")
        line_count = int(start_code[4])
        # RECORD_RE deliberately anchors only the first string so it can also
        # recognize the optional zero-line end token.  Preserve every declared
        # NUL-terminated line here; USA story subtitles commonly combine two
        # Japanese voice clips in one timed record.
        lines = []
        cursor = match.start(3)
        try:
            for _ in range(line_count):
                terminator = data.index(0, cursor)
                lines.append(data[cursor:terminator].decode("ascii"))
                cursor = terminator + 1
        except (ValueError, UnicodeDecodeError):
            # Keep the already validated first line if a malformed declaration
            # is encountered instead of dropping the entire inventory record.
            lines = [text]
        records.append(
            {
                "offset": match.start(),
                "end_code_before": end_code,
                "start_frame": int(start_code[:4]),
                "line_count": line_count,
                "text": text,
                "lines": lines,
                "full_text": "\n".join(lines),
            }
        )
    if path == "SCUS_941.80":
        records = [record for record in records if 0x1A00 <= record["offset"] < 0x1E00]
    # A zero-line token immediately before the next start token is the end
    # marker for the current subtitle.  Some groups omit it and rely on the
    # next scene/group selection to clear the subtitle instead.
    for index, record in enumerate(records):
        next_record = records[index + 1] if index + 1 < len(records) else None
        end_code = next_record["end_code_before"] if next_record else None
        end_frame = int(end_code[:4]) if end_code else None
        record["end_frame"] = end_frame
        record["duration_frames"] = (
            end_frame - record["start_frame"] if end_frame is not None else None
        )
        record["end_mode"] = "zero_line_token" if end_frame is not None else "scene_or_group_change"
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--japan", type=Path, default=Path("original/Bushido Blade (Japan).bin"))
    parser.add_argument("--usa", type=Path, default=Path("original/Bushido Blade (USA).bin"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/analysis/usa_subtitle_comparison.json"),
    )
    args = parser.parse_args()

    japan = inspect(args.japan)
    usa = inspect(args.usa)
    japan_files = file_map(japan)
    usa_files = file_map(usa)
    common = sorted(set(japan_files) & set(usa_files))

    usa_exe_name = next(path for path, entry in usa_files.items() if entry["psx_exe"])
    japan_exe_name = next(path for path, entry in japan_files.items() if entry["psx_exe"])

    hits: dict[str, list[dict]] = {}
    records: dict[str, list[dict]] = {}
    usa_payloads: dict[str, bytes] = {}
    with args.usa.open("rb") as track:
        for path in sorted(TEXT_FILES):
            if path not in usa_files:
                continue
            entry = usa_files[path]
            data, _ = read_extent(track, entry["lba"], entry["size"])
            usa_payloads[path] = data
            file_hits = []
            for needle in NEEDLES:
                for offset in find_all(data, needle):
                    file_hits.append({"text": needle.decode("ascii"), "offset": offset})
            if file_hits:
                hits[path] = sorted(file_hits, key=lambda item: item["offset"])
            minimum_offset = 0
            if path in japan_files and path != usa_exe_name:
                minimum_offset = japan_files[path]["size"]
            file_records = subtitle_records(path, data, minimum_offset)
            if file_records:
                records[path] = file_records

    with args.japan.open("rb") as track:
        japan_exe, _ = read_extent(
            track, japan_files[japan_exe_name]["lba"], japan_files[japan_exe_name]["size"]
        )
    usa_exe = usa_payloads[usa_exe_name]

    o14 = usa_payloads["DATA3/O14.DAT"]
    o20 = usa_payloads["DATA3/O20.DAT"]

    report = {
        "discs": {
            "japan": {
                "path": str(args.japan),
                "size": args.japan.stat().st_size,
                "sha256": sha256(args.japan),
                "file_count": len(japan_files),
                "executable": japan_exe_name,
                "executable_sha256": japan_files[japan_exe_name]["sha256"],
                "exe": exe_info(japan_exe),
            },
            "usa": {
                "path": str(args.usa),
                "size": args.usa.stat().st_size,
                "sha256": sha256(args.usa),
                "file_count": len(usa_files),
                "executable": usa_exe_name,
                "executable_sha256": usa_files[usa_exe_name]["sha256"],
                "exe": exe_info(usa_exe),
            },
        },
        "filesystem_comparison": {
            "common_files": len(common),
            "byte_identical_common_files": sum(
                japan_files[path]["sha256"] == usa_files[path]["sha256"] for path in common
            ),
            "changed_common_files": [
                path for path in common if japan_files[path]["sha256"] != usa_files[path]["sha256"]
            ],
            "japan_only": sorted(set(japan_files) - set(usa_files)),
            "usa_only": sorted(set(usa_files) - set(japan_files)),
        },
        "official_text_hits": hits,
        "official_subtitle_records": records,
        "o20_relationship": {
            "o20_size": len(o20),
            "o20_offset_in_o14": o14.find(o20),
            "o14_trailing_bytes_after_o20": len(o14) - (o14.find(o20) + len(o20)),
        },
        "usa_engine": {
            "event_selector": "0x8003C4D8",
            "table_selector": "0x8003C578",
            "subtitle_update_draw": "0x8003C5A8",
            "line_draw_wrapper": "0x8003C868",
            "section_relocator": "0x8003C920",
            "ascii_text_renderer": "0x80036EF4",
            "character_name_pointer_table": "0x80080F18",
            "character_names": read_pointer_strings(usa_exe, 0x80080F18, 6),
            "subtitle_group_pointer_table": "0x80086458",
            "static_event_pointer_table": "0x80086518",
            "state_frame": "gp+0x420",
            "state_descriptor": "gp+0x424",
            "state_enable": "gp+0x428",
            "calls": {
                "event_selector": jal_calls(usa_exe, 0x8003C4D8),
                "table_selector": jal_calls(usa_exe, 0x8003C578),
                "subtitle_update_draw": jal_calls(usa_exe, 0x8003C5A8),
                "line_draw_wrapper": jal_calls(usa_exe, 0x8003C868),
                "section_relocator": jal_calls(usa_exe, 0x8003C920),
                "ascii_text_renderer": jal_calls(usa_exe, 0x80036EF4),
            },
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
