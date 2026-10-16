#!/usr/bin/env python3
"""Install an official-glyph PoC executable patch into a live DuckStation session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import inspect_psx_disc as disc
from capture_duckstation_ram import GDBRemote
from live_probe_subtitle_render import write_memory
from wait_duckstation_vab_write import read_memory


EXE_PATH = "SLPM_860.20"
EXE_RAM_BASE = 0x80010000
EXE_HEADER_SIZE = 0x800
CAVE_RAM = 0x800118B4
GROUP_HOOK_RAM = 0x8003A1A0
UPDATE_HOOK_RAM = 0x8003A8A8
RENDER_HOOK_RAM = 0x8004322C
A_TEMPLATE_RAM = 0x8008597C
FONT_LOAD_RETURN_HOOK_RAM = 0x80067864


def exe_bytes(exe: bytes, address: int, size: int) -> bytes:
    offset = EXE_HEADER_SIZE + address - EXE_RAM_BASE
    return exe[offset : offset + size]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--build-report", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/analysis/official_glyph_live_install.json"),
    )
    args = parser.parse_args()

    report = json.loads(args.build_report.read_text(encoding="utf-8"))
    payload_size = report["injection"]["payload_size"]
    inspection = disc.inspect(args.image)
    entry = next(item for item in inspection["entries"] if item["path"] == EXE_PATH)
    with args.image.open("rb") as source:
        exe, forms = disc.read_extent(source, entry["lba"], entry["size"])
    if forms != {"form1"}:
        raise ValueError("patched executable is not Form 1")

    writes = {
        "payload": (CAVE_RAM, exe_bytes(exe, CAVE_RAM, payload_size)),
        "group_hook": (GROUP_HOOK_RAM, exe_bytes(exe, GROUP_HOOK_RAM, 8)),
        "update_hook": (UPDATE_HOOK_RAM, exe_bytes(exe, UPDATE_HOOK_RAM, 8)),
        "render_hook": (RENDER_HOOK_RAM, exe_bytes(exe, RENDER_HOOK_RAM, 4)),
        "A_template": (A_TEMPLATE_RAM, exe_bytes(exe, A_TEMPLATE_RAM, 16)),
        "font_load_return_hook": (
            FONT_LOAD_RETURN_HOOK_RAM,
            exe_bytes(exe, FONT_LOAD_RETURN_HOOK_RAM, 8),
        ),
    }

    client = GDBRemote("127.0.0.1", 2345, 10)
    result = {"image": str(args.image), "writes": {}}
    resumed = False
    try:
        result["qSupported"] = client.request("qSupported").decode("ascii", errors="replace")
        result["stop"] = client.request("?").decode("ascii", errors="replace")
        for name, (address, data) in writes.items():
            before = read_memory(client, address, len(data))
            write_memory(client, address, data)
            after = read_memory(client, address, len(data))
            if after != data:
                raise RuntimeError(f"live write verification failed: {name}")
            result["writes"][name] = {
                "address": f"0x{address:08X}",
                "size": len(data),
                "before": before.hex().upper(),
                "after": after.hex().upper(),
            }
        client.continue_without_waiting()
        resumed = True
    finally:
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"installed_payload={payload_size}")
    for name, item in result["writes"].items():
        print(f"{name}=0x{int(item['address'], 16):08X}:{item['size']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
