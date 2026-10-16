#!/usr/bin/env python3
"""Audit wrapped PlayStation VAB banks stored in a raw MODE2/2352 image."""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path


VAB_MAGIC = b"pBAV"
WRAPPER_SIZE = 8


def load_inspector(path: Path):
    spec = importlib.util.spec_from_file_location("inspect_psx_disc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inspector: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_wrapped_vab(data: bytes) -> dict | None:
    if len(data) < WRAPPER_SIZE + 32 or data[WRAPPER_SIZE : WRAPPER_SIZE + 4] != VAB_MAGIC:
        return None

    base = WRAPPER_SIZE
    vab_size = struct.unpack_from("<I", data, base + 12)[0]
    trailing_byte_count = len(data) - (vab_size + WRAPPER_SIZE)
    result = {
        "wrapper_word_0": struct.unpack_from("<I", data, 0)[0],
        "wrapper_word_1": struct.unpack_from("<I", data, 4)[0],
        "version": struct.unpack_from("<I", data, base + 4)[0],
        "bank_id": struct.unpack_from("<I", data, base + 8)[0],
        "vab_size": vab_size,
        "reserved_0": struct.unpack_from("<H", data, base + 16)[0],
        "program_count": struct.unpack_from("<H", data, base + 18)[0],
        "tone_count": struct.unpack_from("<H", data, base + 20)[0],
        "vag_count": struct.unpack_from("<H", data, base + 22)[0],
        "master_volume": data[base + 24],
        "master_pan": data[base + 25],
        "bank_attribute": struct.unpack_from("<H", data, base + 26)[0],
        "reserved_1": struct.unpack_from("<I", data, base + 28)[0],
        "trailing_byte_count": trailing_byte_count,
        "size_matches_wrapper": trailing_byte_count == 0,
        "trailing_bytes_are_zero": (
            trailing_byte_count >= 0
            and not any(data[vab_size + WRAPPER_SIZE :])
        ),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--inspector", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    inspector = load_inspector(args.inspector)
    disc = inspector.inspect(args.image)
    banks = []

    with args.image.open("rb") as track:
        for entry in disc["entries"]:
            if entry["is_dir"]:
                continue
            top_dir = entry["path"].split("/", 1)[0].upper()
            if top_dir not in {"SE0", "SE1", "SE2", "SE3", "SE4", "SE5"}:
                continue
            data, sector_forms = inspector.read_extent(
                track, entry["lba"], entry["size"]
            )
            parsed = parse_wrapped_vab(data)
            if parsed is None:
                continue
            banks.append(
                {
                    "path": entry["path"],
                    "lba": entry["lba"],
                    "file_size": entry["size"],
                    "sector_forms": sorted(sector_forms),
                    **parsed,
                }
            )

    groups: dict[str, list[dict]] = defaultdict(list)
    for bank in banks:
        groups[bank["path"].split("/", 1)[0]].append(bank)

    group_summary = {}
    for name in sorted(groups):
        items = groups[name]
        group_summary[name] = {
            "bank_count": len(items),
            "program_count_total": sum(item["program_count"] for item in items),
            "tone_count_total": sum(item["tone_count"] for item in items),
            "vag_count_total": sum(item["vag_count"] for item in items),
            "size_mismatch_count": sum(
                not item["size_matches_wrapper"] for item in items
            ),
        }

    report = {
        "image": disc["image"],
        "volume_id": disc["volume_id"],
        "files_scanned": sum(
            1
            for entry in disc["entries"]
            if not entry["is_dir"]
            and entry["path"].split("/", 1)[0].upper()
            in {"SE0", "SE1", "SE2", "SE3", "SE4", "SE5"}
        ),
        "wrapped_vab_bank_count": len(banks),
        "size_mismatch_count": sum(
            not bank["size_matches_wrapper"] for bank in banks
        ),
        "zero_padding_only_count": sum(
            bank["trailing_byte_count"] > 0
            and bank["trailing_bytes_are_zero"]
            for bank in banks
        ),
        "version_counts": dict(
            sorted(Counter(bank["version"] for bank in banks).items())
        ),
        "groups": group_summary,
        "banks": banks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"files_scanned={report['files_scanned']}")
    print(f"wrapped_vab_bank_count={report['wrapped_vab_bank_count']}")
    print(f"size_mismatch_count={report['size_mismatch_count']}")
    for name, summary in group_summary.items():
        print(
            f"{name}: banks={summary['bank_count']} "
            f"programs={summary['program_count_total']} "
            f"tones={summary['tone_count_total']} "
            f"vags={summary['vag_count_total']}"
        )
    print(args.output)


if __name__ == "__main__":
    main()
