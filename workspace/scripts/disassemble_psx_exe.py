#!/usr/bin/env python3
"""Print PS-X EXE header allocation fields and disassemble RAM ranges."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("range", nargs="+", help="RAM start:end")
    args = parser.parse_args()

    data = args.exe.read_bytes()
    if data[:8] != b"PS-X EXE":
        raise ValueError("not a PS-X EXE")
    fields = {
        "pc": u32(data, 0x10),
        "gp": u32(data, 0x14),
        "text_address": u32(data, 0x18),
        "text_size": u32(data, 0x1C),
        "data_address": u32(data, 0x20),
        "data_size": u32(data, 0x24),
        "bss_address": u32(data, 0x28),
        "bss_size": u32(data, 0x2C),
        "stack_address": u32(data, 0x30),
        "stack_size": u32(data, 0x34),
    }
    for name, value in fields.items():
        print(f"{name}=0x{value:08X}")

    text_address = fields["text_address"]
    disassembler = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    for item in args.range:
        start_text, end_text = item.split(":", 1)
        start = int(start_text, 0)
        end = int(end_text, 0)
        file_start = 0x800 + start - text_address
        file_end = 0x800 + end - text_address
        if file_start < 0 or file_end > len(data) or file_start >= file_end:
            raise ValueError(f"range outside loaded text: {item}")
        print(f"--- {item} ---")
        for instruction in disassembler.disasm(data[file_start:file_end], start):
            print(
                f"0x{instruction.address:08X}: "
                f"{instruction.mnemonic:<8} {instruction.op_str}"
            )


if __name__ == "__main__":
    main()
