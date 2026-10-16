#!/usr/bin/env python3
"""Read the persistent file-atlas trace from a USA diagnostic build."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import read_memory  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--address", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, 10.0)
    try:
        client.request("qSupported")
        client.request("?")
        raw = read_memory(client, args.address, 48)
    finally:
        client.continue_without_waiting()
        client.close()

    (token, section, entry, col1, col2, digit, last_before,
     upload_selected, upload_completed, match_count, candidate_base,
     record_index_x4) = struct.unpack("<12I", raw)
    record_index = None
    if section and entry >= section + 12 and (entry - section - 12) % 20 == 0:
        record_index = (entry - section - 12) // 20
    result = {
        "trace_address": f"0x{args.address:08X}",
        "token": f"0x{token:08X}",
        "section": f"0x{section:08X}",
        "entry": f"0x{entry:08X}",
        "record_index": record_index,
        "col1_offset": f"0x{col1:X}",
        "col2_offset": f"0x{col2:X}",
        "digit_offset": f"0x{digit:X}",
        "last_token_before": f"0x{last_before:08X}",
        "upload_selected": bool(upload_selected),
        "upload_completed": bool(upload_completed),
        "match_count": match_count,
        "candidate_base": f"0x{candidate_base:08X}",
        "record_index_x4": record_index_x4,
        "raw_hex": raw.hex().upper(),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
