#!/usr/bin/env python3
"""Wait for a DuckStation write breakpoint on a VAB header slot."""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_duckstation_ram import GDBRemote  # noqa: E402


REGISTER_NAMES = [f"r{index}" for index in range(32)] + [
    "sr",
    "lo",
    "hi",
    "badvaddr",
    "cause",
    "pc",
]


def read_memory(client: GDBRemote, address: int, size: int) -> bytes:
    chunks = []
    for offset in range(0, size, 0x4000):
        chunk_size = min(0x4000, size - offset)
        reply = client.request(f"m{address + offset:x},{chunk_size:x}")
        if reply.startswith(b"E"):
            raise RuntimeError(
                f"GDB memory read failed at {address + offset:#x}: "
                f"{reply.decode('ascii', errors='replace')}"
            )
        data = bytes.fromhex(reply.decode("ascii"))
        if len(data) != chunk_size:
            raise ValueError(
                f"short GDB memory read at {address + offset:#x}: "
                f"expected={chunk_size}, actual={len(data)}"
            )
        chunks.append(data)
    return b"".join(chunks)


def normalize_ram_pointer(value: int) -> int | None:
    """Return a KSEG0 address for a pointer into the PS1's 2 MiB main RAM."""
    physical = value & 0x1FFFFFFF
    if physical >= 0x200000:
        return None
    return 0x80000000 | physical


def pbav_addresses(data: bytes, base: int) -> list[str]:
    return [
        f"0x{base + offset:08X}"
        for offset in range(len(data) - 3)
        if data[offset : offset + 4] == b"pBAV"
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument(
        "--address", action="append", type=lambda value: int(value, 0), required=True
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-output", required=True, type=Path)
    parser.add_argument("--source-size", type=lambda value: int(value, 0), default=0x20000)
    args = parser.parse_args()

    client = GDBRemote(args.host, args.port, args.timeout)
    breakpoints_added = []
    resumed = False
    try:
        client.request("qSupported")
        client.request("?")
        for address in args.address:
            reply = client.request(f"Z2,{address:x}")
            if reply != b"OK":
                raise RuntimeError(
                    f"failed to add write breakpoint at {address:#x}: {reply!r}"
                )
            breakpoints_added.append(address)

        started_at = time.time()
        client.continue_without_waiting()
        resumed = True
        stop_reply = client.receive_packet()
        resumed = False

        raw_registers = client.request("g")
        register_bytes = bytes.fromhex(raw_registers.decode("ascii"))
        register_values = [
            struct.unpack_from("<I", register_bytes, index * 4)[0]
            for index in range(len(REGISTER_NAMES))
        ]
        registers = {
            name: f"0x{value:08X}"
            for name, value in zip(REGISTER_NAMES, register_values, strict=True)
        }
        pc = register_values[REGISTER_NAMES.index("pc")]
        code_start = max(0, pc - 0x80)

        report = {
            "host": args.host,
            "port": args.port,
            "stop_reply": stop_reply.decode("ascii", errors="replace"),
            "wait_seconds": round(time.time() - started_at, 3),
            "pc": f"0x{pc:08X}",
            "registers": registers,
            "code_start": f"0x{code_start:08X}",
            "code_hex": None,
            "code_error": None,
            "pointer_captures": [],
            "source_capture": None,
            "slot_captures": [],
            "capture_errors": [],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)

        # Persist the decisive stop state before any optional memory read.  A
        # loader may use a different source register or an invalid sentinel.
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        try:
            code = read_memory(client, code_start, 0x100)
            report["code_hex"] = code.hex()
        except Exception as exc:
            report["code_error"] = str(exc)

        pointer_pages: dict[int, dict[str, list]] = {}
        for name, value in zip(REGISTER_NAMES, register_values, strict=True):
            normalized = normalize_ram_pointer(value)
            if normalized is None:
                continue
            capture_start = normalized & ~0xFFF
            page = pointer_pages.setdefault(
                capture_start, {"registers": [], "pointer_values": []}
            )
            page["registers"].append(name)
            page["pointer_values"].append(f"0x{value:08X}")

        for capture_start, metadata in sorted(pointer_pages.items()):
            capture_size = min(0x4000, 0x80200000 - capture_start)
            capture = {
                "capture_start": f"0x{capture_start:08X}",
                "capture_size": capture_size,
                "registers": metadata["registers"],
                "pointer_values": metadata["pointer_values"],
                "capture_hex": None,
                "pBAV_offsets": [],
                "error": None,
            }
            try:
                data = read_memory(client, capture_start, capture_size)
                capture["capture_hex"] = data.hex()
                capture["pBAV_offsets"] = pbav_addresses(data, capture_start)
            except Exception as exc:
                capture["error"] = str(exc)
                report["capture_errors"].append(str(exc))
            report["pointer_captures"].append(capture)

        # Prefer a pointer page that actually contains a VAB wrapper.  This is
        # deliberately register-agnostic because different loaders do not keep
        # their transient input buffer in the same argument register.
        source_candidate = next(
            (
                capture
                for capture in report["pointer_captures"]
                if capture["pBAV_offsets"]
                and capture["capture_start"] not in {"0x80135000", "0x80136000"}
            ),
            None,
        )
        if source_candidate is None:
            source_candidate = next(
                (
                    capture
                    for capture in report["pointer_captures"]
                    if capture["pBAV_offsets"]
                ),
                None,
            )

        if source_candidate is not None:
            source_base = int(source_candidate["capture_start"], 16)
            source_size = min(args.source_size, 0x80200000 - source_base)
            try:
                source_data = read_memory(client, source_base, source_size)
                args.source_output.parent.mkdir(parents=True, exist_ok=True)
                args.source_output.write_bytes(source_data)
                report["source_capture"] = {
                    "source_base": f"0x{source_base:08X}",
                    "source_size": len(source_data),
                    "source_output": str(args.source_output),
                    "source_pBAV_offsets": pbav_addresses(source_data, source_base),
                    "registers": source_candidate["registers"],
                }
            except Exception as exc:
                report["capture_errors"].append(str(exc))

        slot_captures = []
        for address in args.address:
            slot_start = address & ~0xFFF
            capture = {
                "breakpoint_address": f"0x{address:08X}",
                "capture_start": f"0x{slot_start:08X}",
                "capture_size": 0x2000,
                "capture_hex": None,
                "pBAV_offsets": [],
                "error": None,
            }
            try:
                data = read_memory(client, slot_start, 0x2000)
                capture["capture_size"] = len(data)
                capture["capture_hex"] = data.hex()
                capture["pBAV_offsets"] = pbav_addresses(data, slot_start)
            except Exception as exc:
                capture["error"] = str(exc)
                report["capture_errors"].append(str(exc))
            slot_captures.append(capture)
        report["slot_captures"] = slot_captures

        for address in breakpoints_added:
            reply = client.request(f"z2,{address:x}")
            if reply != b"OK":
                raise RuntimeError(
                    f"failed to remove write breakpoint at {address:#x}: {reply!r}"
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
                client.request(f"z2,{address:x}")
            except Exception:
                pass
        if not resumed:
            client.continue_without_waiting()
        client.close()


if __name__ == "__main__":
    main()
