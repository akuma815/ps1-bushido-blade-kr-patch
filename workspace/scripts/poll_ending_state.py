#!/usr/bin/env python3
"""Poll the ending-related globals without installing any breakpoint.

Static analysis (HANDOFF section 25) established that the official text
renderer 0x80038B80 links its primitives through 0x80035854, which does
AddPrim([0x800A3010] + 0, prim) -- the frontmost layer of a single global
render context.  The OT address is therefore never state-dependent, so the
remaining unknown for the missing ending subtitles is whether the wrapper's
record lookup matches at run time.

This reads only memory (GDB 'm' packets) and never stops the CPU, so it is
safe to leave running while an ending plays -- unlike the multi-breakpoint
traces that previously froze the screen.
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

STATE_RAM = 0x800A311C  # gp+0x698, main-loop state index (halfword)
STATE_TABLE = 0x80088970  # handler table indexed by state
ENDING_STATE = 50  # handler 0x8004A2C0 -- gallery + ending playback
# The handler table holds exactly 51 entries (0..50); 0x80088A3C.. is filler
# (0x70707070) up to the file load table at 0x80088A4C.
END_INDEX_RAM = 0x800A3178  # ending number n (halfword)
END_FRAME_RAM = 0x80128248  # per-ending 30Hz frame counter
CONTEXT_RAM = 0x800A3010  # render context pointer
DB_INDEX_RAM = 0x800A2F3C  # double-buffer index
FADE_RAM = 0x800A309C  # ending fade counter (0..0xF0)
UNLOCK_RAM = 0x800A30B8  # gallery unlock bitmask
# The game structs at 0x80121BA4/0x80121BBC read back as zero: their four
# callers (0x8003C3A0, 0x8003C40C, 0x8003C884, 0x8003C8F0 -> 0x80068330) simply
# did not run.  Read what libgpu actually applied instead: 0x80068DC8 builds the
# draw-environment primitive at 0x801280D8 (GP0 E1/E3/E4/E5/E6 words) and chain-
# sends it through 0x80069570, so this block IS the live drawing area.
DRAW_ENV_A = 0x801280D8
DRAW_ENV_B = 0x80121BBC

# v41 ending path: base = [0x8013E6D8]; word3 = [base + 0xC] is matched against
# the wrapper's (word3 -> section offset) table.  A miss bails out of the stub
# before the group formula ever runs, so these are the last unverified inputs.
END_BUFFER_PTR_RAM = 0x8013E6D8
TRANSIENT_BASE = 0x8014C000  # base used by the win/cutscene contexts
WORD3_OFFSET = 0xC

# word3 values the v41_end2 wrapper knows about (build_report.json cave_table)
KNOWN_WORD3 = {
    0x3F8,
    0x37C,
    0x634,
    0x438,
    0x378,
    0x6D0,
    0x480,
    0x3E0,
    0x5AC,
    0x230,
}


def snapshot(client: GDBRemote) -> dict:
    def u32(addr: int) -> int:
        return struct.unpack("<I", read_memory(client, addr, 4))[0]

    def i16(addr: int) -> int:
        return struct.unpack("<h", read_memory(client, addr, 2))[0]

    state = i16(STATE_RAM)
    record = {
        "state": state,
        "handler": None,
        "ending_index": i16(END_INDEX_RAM),
        "ending_frame": u32(END_FRAME_RAM),
        "fade": u32(FADE_RAM),
        "context": f"0x{u32(CONTEXT_RAM):08X}",
        "db_index": u32(DB_INDEX_RAM) & 1,
        "unlock": f"0x{u32(UNLOCK_RAM):08X}",
    }
    if 0 <= state <= 50:  # the table holds exactly 51 entries (0..50)
        handler = u32(STATE_TABLE + state * 4)
        record["handler"] = f"0x{handler:08X}"

    base = u32(END_BUFFER_PTR_RAM)
    record["end_base"] = f"0x{base:08X}"
    record["end_word3"] = None
    record["word3_known"] = None
    if 0x80000000 <= base < 0x80200000 - 0x10:
        word3 = u32(base + WORD3_OFFSET)
        record["end_word3"] = f"0x{word3:X}"
        record["word3_known"] = word3 in KNOWN_WORD3
    transient = u32(TRANSIENT_BASE + WORD3_OFFSET)
    record["transient_word3"] = f"0x{transient:X}"

    # Is the global ordering table still being cleared/drawn this frame?  The
    # official text renderer links at depth 0 ([0x800A3010] + 0); if the movie
    # player writes MDEC output straight to the framebuffer, that OT is never
    # submitted and any primitive we add is invisible no matter where we hook.
    # Draw/display environment structures.  0x8003C384 builds
    # a0 = 0x80121BBC + 12*db and 0x8003C3F8 does the same for 0x80121BA4,
    # both passed to 0x80068330.  If a movie displays a VRAM region the game's
    # draw area does not cover, our directly-sent primitives land off-screen --
    # which is what "subtitle only appears after the video" looks like.
    record["env_a"] = read_memory(client, DRAW_ENV_A, 24).hex()

    context = u32(CONTEXT_RAM)
    # DISPENV is the main loop's `$s1 + 0x5C` while the render context is
    # `$s1 + 0x70` (0x80043264 / 0x800431A4), so it hangs off the same global.
    # Read it per sample: the value that matters is the one live WHILE a movie
    # plays, which every earlier measurement missed.
    record["dispenv"] = None
    record["disp_xy"] = None
    if 0x80000000 <= context - 0x14 < 0x80200000:
        raw = read_memory(client, context - 0x14, 20)
        record["dispenv"] = raw.hex()
        x, y, w, h = struct.unpack_from("<4h", raw)
        record["disp_xy"] = f"({x},{y}) {w}x{h}"
    record["ot_head"] = None
    record["ot_depth0"] = None
    if 0x80000000 <= context < 0x80200000 - 0x4000:
        record["ot_head"] = f"0x{u32(context + 0x3FFC):08X}"
        record["ot_depth0"] = f"0x{u32(context):08X}"
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument(
        "--only-changes",
        action="store_true",
        help="record a row only when a field other than the frame counter moves",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    rows: list[dict] = []
    previous_key = None
    started = time.time()
    saw_ending = False
    client = GDBRemote(args.host, args.port, 10.0)
    try:
        client.request("qSupported")
        client.request("?")
        while time.time() - started < args.duration:
            record = snapshot(client)
            record["seconds"] = round(time.time() - started, 2)
            if record["state"] == ENDING_STATE:
                saw_ending = True
            key = tuple(
                record[name]
                for name in ("state", "ending_index", "unlock", "disp_xy")
            )
            if not args.only_changes or key != previous_key:
                rows.append(record)
                previous_key = key
                print(
                    f"t={record['seconds']:7.2f} state={record['state']:3d} "
                    f"handler={record['handler']} idx={record['ending_index']:3d} "
                    f"frame={record['ending_frame']:6d} "
                    f"base={record['end_base']} w3={record['end_word3']} "
                    f"known={record['word3_known']} ot={record['ot_depth0']}"
                    f" disp={record['disp_xy']}"
                )
            time.sleep(args.interval)
    except (ConnectionError, OSError) as exc:
        print(f"disconnected: {exc}")
    finally:
        try:
            client.close()
        except Exception:
            pass
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "saw_ending_state": saw_ending,
                    "sample_count": len(rows),
                    "samples": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"saw_ending_state={saw_ending} samples={len(rows)} -> {args.output}")


if __name__ == "__main__":
    main()
