#!/usr/bin/env python3
"""Read the ordered V00 selector context log from DuckStation RAM."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from build_v00_event_context_log import (  # noqa: E402
    LOG_ENTRY_SIZE,
    LOG_HEADER_SIZE,
    LOG_MAGIC,
    LOG_MAX_ENTRIES,
    LOG_RAM,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    size = LOG_HEADER_SIZE + LOG_ENTRY_SIZE * LOG_MAX_ENTRIES
    client = GDBRemote(args.host, args.port, 10.0)
    try:
        client.request("qSupported")
        stop = client.request("?")
        raw = bytes.fromhex(client.request(f"m{LOG_RAM:x},{size:x}").decode("ascii"))
    finally:
        client.continue_without_waiting()
        client.close()

    magic, count = struct.unpack_from("<II", raw, 0)
    valid = magic == LOG_MAGIC
    count = min(count, LOG_MAX_ENTRIES) if valid else 0
    fields = ("sequence", "a0", "a1", "a2", "a3", "s0", "s1", "s2")
    entries = []
    for index in range(count):
        values = struct.unpack_from("<8I", raw, LOG_HEADER_SIZE + index * LOG_ENTRY_SIZE)
        entry = {name: value for name, value in zip(fields, values)}
        entry.update({f"{name}_hex": f"0x{value:08X}" for name, value in zip(fields[1:], values[1:])})
        entries.append(entry)
    report = {
        "status": "captured" if valid else "log_magic_missing",
        "stop_reply": stop.decode("ascii", errors="replace"),
        "log_ram": f"0x{LOG_RAM:08X}",
        "magic": f"0x{magic:08X}",
        "expected_magic": f"0x{LOG_MAGIC:08X}",
        "count": count,
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
