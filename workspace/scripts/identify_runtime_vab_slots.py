#!/usr/bin/env python3
"""Match pBAV headers captured from DuckStation RAM to disc VAB banks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
from pathlib import Path


def load_inspector(path: Path):
    spec = importlib.util.spec_from_file_location("inspect_psx_disc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inspector: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("--inspector", required=True, type=Path)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    inspector = load_inspector(args.inspector)
    disc = inspector.inspect(args.image)

    banks = []
    with args.image.open("rb") as track:
        for entry in disc["entries"]:
            if entry["is_dir"]:
                continue
            group = entry["path"].split("/", 1)[0].upper()
            if group not in {"SE0", "SE1", "SE2", "SE3", "SE4", "SE5"}:
                continue
            data, _ = inspector.read_extent(track, entry["lba"], entry["size"])
            if len(data) >= 40 and data[8:12] == b"pBAV":
                banks.append((entry["path"], data))

    source_capture = report.get("source_capture")
    if source_capture:
        source = Path(source_capture["source_output"]).read_bytes()
        source_matches = []
        for path, wrapped_bank in banks:
            compare_size = min(len(source), len(wrapped_bank))
            if source[:compare_size] == wrapped_bank[:compare_size]:
                source_matches.append(
                    {
                        "path": path,
                        "file_size": len(wrapped_bank),
                        "matched_bytes": compare_size,
                        "complete_file_matched": compare_size == len(wrapped_bank),
                    }
                )
        print(json.dumps({"source_matches": source_matches}, ensure_ascii=False))

    seen = set()
    for capture in report["slot_captures"]:
        if not capture.get("capture_hex"):
            continue
        capture_start = int(capture["capture_start"], 16)
        capture_data = bytes.fromhex(capture["capture_hex"])
        for address_text in capture["pBAV_offsets"]:
            address = int(address_text, 16)
            if address in seen:
                continue
            seen.add(address)
            offset = address - capture_start
            runtime = capture_data[offset:]
            if len(runtime) < 32:
                continue
            program_count = struct.unpack_from("<H", runtime, 18)[0]
            header_size = 32 + 128 * 16 + program_count * 16 * 32 + 256 * 2
            runtime_header = runtime[:header_size]
            matches = []
            ranked = []
            for path, wrapped_bank in banks:
                bank = wrapped_bank[8:]
                compare_size = min(len(runtime_header), len(bank), header_size)
                if compare_size and runtime_header[:compare_size] == bank[:compare_size]:
                    matches.append({"path": path, "matched_bytes": compare_size})
                equal_bytes = sum(
                    left == right
                    for left, right in zip(runtime_header[:compare_size], bank[:compare_size])
                )
                ranked.append(
                    {
                        "path": path,
                        "equal_bytes": equal_bytes,
                        "compared_bytes": compare_size,
                        "header32_equal_bytes": sum(
                            left == right
                            for left, right in zip(runtime_header[:32], bank[:32])
                        ),
                    }
                )
            ranked.sort(
                key=lambda item: (item["equal_bytes"], item["header32_equal_bytes"]),
                reverse=True,
            )
            print(
                json.dumps(
                    {
                        "address": address_text,
                        "version": struct.unpack_from("<I", runtime, 4)[0],
                        "bank_id": struct.unpack_from("<I", runtime, 8)[0],
                        "vab_size": struct.unpack_from("<I", runtime, 12)[0],
                        "program_count": program_count,
                        "tone_count": struct.unpack_from("<H", runtime, 20)[0],
                        "vag_count": struct.unpack_from("<H", runtime, 22)[0],
                        "expected_header_size": header_size,
                        "available_header_bytes": len(runtime_header),
                        "matches": matches,
                        "closest": ranked[:5],
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
