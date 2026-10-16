#!/usr/bin/env python3
"""Wait for a DuckStation GDB execute breakpoint and preserve CPU state."""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402
from wait_duckstation_vab_write import REGISTER_NAMES, read_memory  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument(
        "--address", action="append", type=lambda value: int(value, 0), required=True
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--a0", action="append", type=lambda value: int(value, 0))
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, args.timeout)
    breakpoints_added = []
    resumed = False
    try:
        client.request("qSupported")
        client.request("?")
        for address in args.address:
            reply = client.request(f"Z0,{address:x}")
            if reply != b"OK":
                raise RuntimeError(
                    f"failed to add execute breakpoint at {address:#x}: {reply!r}"
                )
            breakpoints_added.append(address)

        started_at = time.time()
        client.continue_without_waiting()
        resumed = True
        unexpected_stops = []
        rejected_hits = []
        while True:
            stop_reply = client.receive_packet()
            resumed = False
            raw_registers = client.request("g")
            register_bytes = bytes.fromhex(raw_registers.decode("ascii"))
            register_values = [
                struct.unpack_from("<I", register_bytes, index * 4)[0]
                for index in range(len(REGISTER_NAMES))
            ]
            stopped_pc = register_values[REGISTER_NAMES.index("pc")]
            a0 = register_values[4]
            if stopped_pc in args.address and (args.a0 is None or a0 in args.a0):
                break
            stop_record = {
                "stop_reply": stop_reply.decode("ascii", errors="replace"),
                "pc": f"0x{stopped_pc:08X}",
                "a0": f"0x{a0:08X}",
                "ra": f"0x{register_values[31]:08X}",
                "elapsed_seconds": round(time.time() - started_at, 3),
            }
            if stopped_pc in args.address:
                rejected_hits.append(stop_record)
            else:
                unexpected_stops.append(stop_record)
            client.continue_without_waiting()
            resumed = True

        registers = {
            name: f"0x{value:08X}"
            for name, value in zip(REGISTER_NAMES, register_values, strict=True)
        }
        pc = register_values[REGISTER_NAMES.index("pc")]
        code_start = pc - 0x40
        report = {
            "host": args.host,
            "port": args.port,
            "breakpoint_addresses": [f"0x{address:08X}" for address in args.address],
            "a0_filter": (
                [f"0x{value:08X}" for value in args.a0] if args.a0 else None
            ),
            "stop_reply": stop_reply.decode("ascii", errors="replace"),
            "wait_seconds": round(time.time() - started_at, 3),
            "unexpected_stops": unexpected_stops,
            "rejected_hits": rejected_hits,
            "pc": f"0x{pc:08X}",
            "registers": registers,
            "code_start": f"0x{code_start:08X}",
            "code_hex": None,
            "code_error": None,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            report["code_hex"] = read_memory(client, code_start, 0x100).hex()
        except Exception as exc:
            report["code_error"] = str(exc)

        for address in breakpoints_added:
            reply = client.request(f"z0,{address:x}")
            if reply != b"OK":
                raise RuntimeError(
                    f"failed to remove execute breakpoint at {address:#x}: {reply!r}"
                )
        breakpoints_added.clear()
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"stop_reply={report['stop_reply']}")
        print(f"pc={report['pc']}")
        print(f"wait_seconds={report['wait_seconds']}")
        print(f"output={args.output}")
    finally:
        for address in breakpoints_added:
            try:
                client.request(f"z0,{address:x}")
            except Exception:
                pass
        if not resumed:
            client.continue_without_waiting()
        client.close()


if __name__ == "__main__":
    main()
