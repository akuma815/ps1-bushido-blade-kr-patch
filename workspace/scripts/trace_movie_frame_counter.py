#!/usr/bin/env python3
"""Log movie frame-counter candidates at each cutscene path-B execution.

Single breakpoint at 0x8003B334 (JP cutscene subtitle insertion point),
resumed immediately per hit. Records [0x800A309C] and [0x8013E6E4] per hit
to establish the hit:counter ratio and reset behaviour.

--register additionally stores CPU registers per hit; the OT-linking helper
0x80038B44 takes the render context in $a0 (OT = $a0 + 0x3FFC), which memory
sampling alone cannot recover.  --filter-address/--filter-mode keeps only the
hits taken while a chosen counter is (non-)zero, so a breakpoint that fires
~17x per second during normal play can still be aimed at an ending.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import REGISTER_NAMES, read_memory  # noqa: E402

BREAK_ADDRESS = 0x8003B334
CANDIDATE_A = 0x800A309C
CANDIDATE_B = 0x8013E6E4
# Movie discriminators readable at hook time (HANDOFF section 22):
# 0x80086A3C/0x80086A3E hold [0x80086A44 + movie_id*4], written by the movie
# start function; 0x8014C00C is the loaded file's header word3 fingerprint.
MOVIE_PARAM = 0x80086A3C
TRANSIENT_WORD3 = 0x8014C00C
# The file loader stores the last requested file id here (0x80044AE0:
# sh $a0, -0x1940(0x8014)). movie file id = 0x57 + movie_id.
LAST_FILE_ID = 0x8013E6C0
# Ending playback frame counter (30Hz, resets per ending).
ENDING_FRAME = 0x80128248

# MIPS ABI aliases so --register a0 works as well as --register r4.
REGISTER_ALIASES = {
    "zero": "r0",
    "at": "r1",
    "v0": "r2",
    "v1": "r3",
    "a0": "r4",
    "a1": "r5",
    "a2": "r6",
    "a3": "r7",
    "s0": "r16",
    "s1": "r17",
    "s2": "r18",
    "s3": "r19",
    "gp": "r28",
    "sp": "r29",
    "fp": "r30",
    "ra": "r31",
}


def resolve_register(name: str) -> tuple[str, int]:
    """Return the (display name, index) of a register given a name or alias."""
    canonical = REGISTER_ALIASES.get(name, name)
    if canonical not in REGISTER_NAMES:
        raise argparse.ArgumentTypeError(f"unknown register: {name}")
    return name, REGISTER_NAMES.index(canonical)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-hits", type=int, default=400)
    parser.add_argument(
        "--address",
        type=lambda value: int(value, 0),
        default=BREAK_ADDRESS,
        help="breakpoint to count (default: the cutscene step point)",
    )
    parser.add_argument(
        "--register",
        action="append",
        type=resolve_register,
        default=[],
        help="CPU register to record per hit (name or MIPS alias, repeatable)",
    )
    parser.add_argument(
        "--filter-address",
        type=lambda value: int(value, 0),
        help="only record hits while the u32 at this address matches --filter-mode",
    )
    parser.add_argument(
        "--filter-mode",
        choices=("any", "nonzero", "zero"),
        default="any",
        help="how --filter-address gates recording (default: any)",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    pc_index = REGISTER_NAMES.index("pc")
    started = time.time()
    hits: list[dict] = []
    skipped = 0
    status = "running"
    installed = False
    running = False
    client = GDBRemote(args.host, args.port, 10.0)
    try:
        client.request("qSupported")
        client.request("?")
        reply = client.request(f"Z0,{args.address:x}")
        if reply != b"OK":
            raise RuntimeError(f"breakpoint failed: {reply!r}")
        installed = True
        client.continue_without_waiting()
        running = True
        client.socket.settimeout(0.25)
        while time.time() - started < args.timeout and len(hits) < args.max_hits:
            try:
                client.receive_packet()
            except socket.timeout:
                continue
            running = False
            raw = bytes.fromhex(client.request("g").decode("ascii"))
            pc = struct.unpack_from("<I", raw, pc_index * 4)[0]
            if pc == args.address:
                if args.filter_address is not None and args.filter_mode != "any":
                    gate = struct.unpack(
                        "<I", read_memory(client, args.filter_address, 4)
                    )[0]
                    if (args.filter_mode == "nonzero") != (gate != 0):
                        skipped += 1
                        client.continue_without_waiting()
                        running = True
                        continue
                candidate_a = struct.unpack("<I", read_memory(client, CANDIDATE_A, 4))[0]
                candidate_b = struct.unpack("<I", read_memory(client, CANDIDATE_B, 4))[0]
                param_lo, param_hi = struct.unpack(
                    "<HH", read_memory(client, MOVIE_PARAM, 4)
                )
                word3 = struct.unpack("<I", read_memory(client, TRANSIENT_WORD3, 4))[0]
                registers = {
                    name: f"0x{struct.unpack_from('<I', raw, index * 4)[0]:08X}"
                    for name, index in args.register
                }
                hits.append(
                    {
                        "index": len(hits),
                        "seconds": round(time.time() - started, 3),
                        **({"registers": registers} if registers else {}),
                        "counter_a_0x800A309C": candidate_a,
                        "counter_b_0x8013E6E4": candidate_b,
                        "movie_param": [f"0x{param_lo:04X}", f"0x{param_hi:04X}"],
                        "file_word3": f"0x{word3:X}",
                        "last_file_id": struct.unpack(
                            "<H", read_memory(client, LAST_FILE_ID, 2)
                        )[0],
                        "ending_frame": struct.unpack(
                            "<I", read_memory(client, ENDING_FRAME, 4)
                        )[0],
                    }
                )
            client.continue_without_waiting()
            running = True
        status = "max_hits" if len(hits) >= args.max_hits else "timeout"
        if running:
            client.socket.settimeout(10.0)
            client.interrupt()
            running = False
        client.request(f"z0,{args.address:x}")
        installed = False
    except (ConnectionError, OSError):
        status = "disconnected"
        installed = False
        running = True
    finally:
        if installed:
            try:
                client.request(f"z0,{args.address:x}")
            except Exception:
                pass
        if not running:
            try:
                client.continue_without_waiting()
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "status": status,
                    "address": f"0x{args.address:08X}",
                    "hit_count": len(hits),
                    "skipped_by_filter": skipped,
                    "hits": hits,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"status={status} hits={len(hits)} skipped={skipped}")


if __name__ == "__main__":
    main()
