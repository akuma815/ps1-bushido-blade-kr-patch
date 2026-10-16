#!/usr/bin/env python3
"""Poll ending-playback counters and report any reset (value dropping).

Used to decide which counter restarts at each ending, i.e. which one the
v40 subtitle wrapper can use as its frame source without keeping state.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import read_memory  # noqa: E402

WATCH = {
    "c_30hz_0x80128248": 0x80128248,
    "c_60hz_0x80128234": 0x80128234,
    "c_alt_0x80128E84": 0x80128E84,
    "ending_index_0x800A3178": 0x800A3178,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    started = time.time()
    samples: list[dict] = []
    resets: list[dict] = []
    previous: dict[str, int] = {}
    try:
        while time.time() - started < args.seconds:
            client = GDBRemote(args.host, args.port, 10.0)
            try:
                client.request("qSupported")
                client.request("?")
                values = {
                    name: struct.unpack("<I", read_memory(client, addr, 4))[0]
                    for name, addr in WATCH.items()
                }
                client.continue_without_waiting()
            finally:
                client.close()
            stamp = round(time.time() - started, 2)
            samples.append({"t": stamp, **values})
            for name, value in values.items():
                old = previous.get(name)
                if old is not None and value < old:
                    resets.append(
                        {"t": stamp, "counter": name, "from": old, "to": value}
                    )
                    print(f"[{stamp:7.2f}s] RESET {name}: {old} -> {value}")
            previous = values
            time.sleep(args.interval)
    except (ConnectionError, OSError) as exc:
        print(f"disconnected: {exc}")
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"sample_count": len(samples), "resets": resets, "samples": samples},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"samples={len(samples)} resets={len(resets)} output={args.output}")


if __name__ == "__main__":
    main()
