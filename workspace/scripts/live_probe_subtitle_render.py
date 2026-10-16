#!/usr/bin/env python3
"""Install a subtitle render hook in live DuckStation RAM for diagnosis."""

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
import inspect_psx_disc as disc  # noqa: E402


EXE_PATH = "SLPM_860.20"
EXE_LBA = 24
EXE_SIZE = 604160
EXE_RAM_BASE = 0x80010000
EXE_HEADER_SIZE = 0x800
ADD_PRIM_RAM = 0x8006A368
APPEND_RAM = 0x800118B4
TIMER_RAM = 0x80121FF0
LAST_FRAME_RAM = 0x80121FF4
PRIMITIVES_RAM = 0x80122000


def write_memory(client: GDBRemote, address: int, data: bytes) -> None:
    for offset in range(0, len(data), 0x100):
        chunk = data[offset : offset + 0x100]
        reply = client.request(f"M{address + offset:x},{len(chunk):x}:{chunk.hex()}")
        if reply != b"OK":
            raise RuntimeError(
                f"memory write failed at {address + offset:#x}: {reply!r}"
            )


def extract_exe(image: Path) -> bytes:
    with image.open("rb") as source:
        data, forms = disc.read_extent(source, EXE_LBA, EXE_SIZE)
    if forms != {"form1"}:
        raise ValueError("patched executable is not stored entirely as Form 1")
    return data


def exe_bytes(exe: bytes, address: int, size: int) -> bytes:
    offset = EXE_HEADER_SIZE + address - EXE_RAM_BASE
    return exe[offset : offset + size]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--build-report", required=True, type=Path)
    parser.add_argument("--display-frames", type=int, default=900)
    parser.add_argument("--sample-seconds", type=float, default=2.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = json.loads(args.build_report.read_text(encoding="utf-8"))
    payload_size = int(report["injection"]["payload_size"])
    exe = extract_exe(args.image)
    hook = exe_bytes(exe, ADD_PRIM_RAM, 8)
    payload = exe_bytes(exe, APPEND_RAM, payload_size)

    client = GDBRemote("127.0.0.1", 2345, 10)
    result: dict = {
        "image": str(args.image),
        "hook_ram": f"0x{ADD_PRIM_RAM:08X}",
        "payload_ram": f"0x{APPEND_RAM:08X}",
        "payload_size": payload_size,
        "display_frames_requested": args.display_frames,
    }
    resumed = False
    try:
        result["qSupported"] = client.request("qSupported").decode(
            "ascii", errors="replace"
        )
        result["initial_stop"] = client.request("?").decode(
            "ascii", errors="replace"
        )
        result["before"] = {
            "hook": read_memory(client, ADD_PRIM_RAM, 8).hex().upper(),
            "payload": read_memory(client, APPEND_RAM, payload_size).hex().upper(),
            "state": read_memory(client, TIMER_RAM, 8).hex().upper(),
            "primitives": read_memory(client, PRIMITIVES_RAM, 32).hex().upper(),
        }
        write_memory(client, APPEND_RAM, payload)
        write_memory(
            client,
            TIMER_RAM,
            struct.pack("<H2xI", args.display_frames, 0xFFFFFFFF),
        )
        write_memory(client, ADD_PRIM_RAM, hook)
        result["installed"] = {
            "hook": read_memory(client, ADD_PRIM_RAM, 8).hex().upper(),
            "payload_matches": read_memory(client, APPEND_RAM, payload_size) == payload,
            "state": read_memory(client, TIMER_RAM, 8).hex().upper(),
        }
        client.continue_without_waiting()
        resumed = True
        time.sleep(args.sample_seconds)
        client.socket.settimeout(5.0)
        client.interrupt()
        resumed = False
        state = read_memory(client, TIMER_RAM, 8)
        result["after_sample"] = {
            "timer": struct.unpack_from("<H", state, 0)[0],
            "last_frame": f"0x{struct.unpack_from('<I', state, 4)[0]:08X}",
            "state": state.hex().upper(),
            "primitives": read_memory(client, PRIMITIVES_RAM, 32).hex().upper(),
        }
    finally:
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"timer_after_sample={result['after_sample']['timer']}")
    print(f"last_frame={result['after_sample']['last_frame']}")
    print(f"primitives={result['after_sample']['primitives']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
