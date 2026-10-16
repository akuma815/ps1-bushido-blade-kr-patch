#!/usr/bin/env python3
"""Read the subtitle PoC state and hook bytes once, then resume DuckStation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import read_memory  # noqa: E402


READS = {
    "legacy_state_unsafe": (0x800A2E90, 12),
    "official_timed_state": (0x80011D78, 12),
    "group_hook": (0x8003A1A0, 8),
    "group_update_hook": (0x8003A8A8, 8),
    "render_hook": (0x80067B54, 8),
    "official_glyph_init": (0x80011C78, 1),
    "official_glyph_render_callsite": (0x8004322C, 4),
    "official_A_template": (0x8008597C, 16),
    "jp_scene_source_gp_304": (0x800A2D88, 4),
    "jp_scene_frame_gp_314": (0x800A2D98, 4),
    "jp_scene_countdown_gp_318": (0x800A2D9C, 4),
    "jp_scene_stream_gp_31c": (0x800A2DA0, 4),
    "jp_scene_stream2_gp_320": (0x800A2DA4, 4),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, args.timeout)
    resumed = False
    try:
        client.request("qSupported")
        stop = client.request("?").decode("ascii", errors="replace")
        print(f"stop={stop}")
        for name, (address, size) in READS.items():
            data = read_memory(client, address, size)
            print(f"{name}=0x{address:08X}:{data.hex().upper()}")
        client.continue_without_waiting()
        resumed = True
    finally:
        if not resumed:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        client.close()


if __name__ == "__main__":
    main()
