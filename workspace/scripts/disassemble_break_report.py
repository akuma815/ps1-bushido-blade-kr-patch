#!/usr/bin/env python3
"""Disassemble addresses from RAM pages embedded in a VAB break report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("address", nargs="+", type=lambda value: int(value, 0))
    parser.add_argument("--before", type=lambda value: int(value, 0), default=0x40)
    parser.add_argument("--size", type=lambda value: int(value, 0), default=0x100)
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    pages = []
    for capture in report["pointer_captures"]:
        if capture.get("capture_hex"):
            pages.append(
                (
                    int(capture["capture_start"], 16),
                    bytes.fromhex(capture["capture_hex"]),
                )
            )

    disassembler = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    for address in args.address:
        start = address - args.before
        payload = None
        for page_start, page_data in pages:
            offset = start - page_start
            if 0 <= offset and offset + args.size <= len(page_data):
                payload = page_data[offset : offset + args.size]
                break
        print(f"--- target=0x{address:08X} start=0x{start:08X} ---")
        if payload is None:
            if args.exe is not None:
                exe = args.exe.read_bytes()
                file_offset = 0x800 + start - 0x80010000
                if 0 <= file_offset and file_offset + args.size <= len(exe):
                    payload = exe[file_offset : file_offset + args.size]
            if payload is None:
                print("not captured")
                continue
        for instruction in disassembler.disasm(payload, start):
            marker = ">" if instruction.address == address else " "
            print(
                f"{marker} 0x{instruction.address:08X}: "
                f"{instruction.mnemonic:<8} {instruction.op_str}"
            )


if __name__ == "__main__":
    main()
