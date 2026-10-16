#!/usr/bin/env python3
"""Build the JP (V0X file, group) scene map with USA alignment.

Statically parses DATA7/V00..V08.DAT from both discs using the layout proven
at runtime (JP scene-start function 0x80039F6C):

  cmp_ptr  = base + [base+4] + group*8      -> (slot_word, length)
  source   = base + [base+0x38 + group*4]   -> record {stream_count, n1, n2}

Runtime group recovery (no persistent state):
  group = ([gp+0x2F8] - 0x8014C000 - [0x8014C004]) / 8

File identity at runtime: header words at 0x8014C000 (fingerprint below).

Output: work/analysis/jp_v0x_scene_map.json
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc

ROOT = Path(__file__).resolve().parents[2]
JP_BIN = ROOT / "original" / "Bushido Blade (Japan).bin"
USA_BIN = ROOT / "original" / "Bushido Blade (USA).bin"
OUTPUT = ROOT / "work" / "analysis" / "jp_v0x_scene_map.json"
TRANSIENT_BASE = 0x8014C000


def read_file(bin_path: Path, wanted: str) -> bytes | None:
    info = disc.inspect(bin_path)
    with bin_path.open("rb") as source:
        for entry in info["entries"]:
            if entry["path"] == wanted:
                data, _forms = disc.read_extent(source, entry["lba"], entry["size"])
                return data
    return None


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
        stream_count, n1, n2 = struct.unpack_from("<III", data, record_offset)
        if stream_count == 0 or stream_count > 8:
            break
        groups.append(
            {
                "group": group,
                "cmp_offset": f"0x{cmp_offset:X}",
                "cmp_ptr_runtime": f"0x{TRANSIENT_BASE + cmp_offset:08X}",
                "slot_word": slot_word,
                "length_frames": length,
                "record_offset": f"0x{record_offset:X}",
                "record_header": [stream_count, n1, n2],
            }
        )
    return groups


def main() -> None:
    result = {"transient_base": f"0x{TRANSIENT_BASE:08X}", "files": []}
    for index in range(9):
        name = f"DATA7/V0{index}.DAT"
        jp = read_file(JP_BIN, name)
        usa = read_file(USA_BIN, name)
        if jp is None:
            continue
        entry = {
            "path": name,
            "jp_size": len(jp),
            "usa_size": len(usa) if usa else None,
            "jp_header_words": [
                f"0x{value:X}" for value in struct.unpack_from("<8I", jp, 0)
            ],
            "jp_groups": parse_groups(jp),
            "usa_groups": parse_groups(usa) if usa else None,
        }
        entry["jp_group_count"] = len(entry["jp_groups"])
        entry["usa_group_count"] = (
            len(entry["usa_groups"]) if entry["usa_groups"] is not None else None
        )
        result["files"].append(entry)

    fingerprints = {}
    for entry in result["files"]:
        key = tuple(entry["jp_header_words"][:4])
        fingerprints.setdefault(key, []).append(entry["path"])
    result["fingerprint_unique"] = all(
        len(paths) == 1 for paths in fingerprints.values()
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for entry in result["files"]:
        jp_lengths = [group["length_frames"] for group in entry["jp_groups"]]
        usa_lengths = (
            [group["length_frames"] for group in entry["usa_groups"]]
            if entry["usa_groups"]
            else None
        )
        aligned = jp_lengths == usa_lengths
        print(
            f"{entry['path']}: jp_groups={entry['jp_group_count']} "
            f"usa_groups={entry['usa_group_count']} lengths={jp_lengths} "
            f"usa_match={aligned}"
        )
    print(f"fingerprint_unique={result['fingerprint_unique']}")
    print(f"output={OUTPUT}")


if __name__ == "__main__":
    main()
