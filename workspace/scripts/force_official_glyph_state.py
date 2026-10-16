#!/usr/bin/env python3
"""Force and sample the v26 official-glyph subtitle state in live DuckStation."""

from __future__ import annotations

import argparse
import json
import struct
import time
from pathlib import Path

from capture_duckstation_ram import GDBRemote
from live_probe_subtitle_render import write_memory
from wait_duckstation_vab_write import read_memory


STATE_RAM = 0x800A2E90


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame", type=int, default=60)
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/analysis/official_glyph_live_force.json"),
    )
    args = parser.parse_args()

    client = GDBRemote("127.0.0.1", 2345, 10)
    result = {}
    resumed = False
    try:
        result["qSupported"] = client.request("qSupported").decode("ascii", errors="replace")
        result["stop"] = client.request("?").decode("ascii", errors="replace")
        before = read_memory(client, STATE_RAM, 12)
        forced = bytearray(before)
        forced[3] = 1
        struct.pack_into("<I", forced, 8, args.frame)
        write_memory(client, STATE_RAM, bytes(forced))
        result["before"] = before.hex().upper()
        result["forced"] = read_memory(client, STATE_RAM, 12).hex().upper()
        client.continue_without_waiting()
        resumed = True
        time.sleep(args.sample_seconds)
        client.interrupt()
        resumed = False
        after = read_memory(client, STATE_RAM, 12)
        result["after"] = after.hex().upper()
        result["after_active"] = after[3]
        result["after_frame"] = struct.unpack_from("<I", after, 8)[0]
    finally:
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"before={result['before']}")
    print(f"forced={result['forced']}")
    print(f"after={result['after']}")
    print(f"after_active={result['after_active']}")
    print(f"after_frame={result['after_frame']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
