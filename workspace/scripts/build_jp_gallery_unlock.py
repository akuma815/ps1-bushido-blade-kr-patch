#!/usr/bin/env python3
"""Force the Japanese movie gallery permanently open, in place.

The unlock bitmask is [0x800A30B8] (HANDOFF 2026-08-02).  Nop-ing the two
branch gates alone left the menu locked, and poking the mask at runtime was
the only thing that worked -- so this does the equivalent statically: every
site that READS the mask returns -1 instead.

Eleven instructions touch the mask.  Four are deliberately left alone:

  0x80049884 / 0x80049894   the unlock setter itself (lw, or, sw) -- forcing
                            its load would make the game write -1 back into
                            the mask and, through the save path, into the
                            player's memory card
  0x80065050 / 0x800650F8   save serialisation -- same reason

That leaves exactly the seven menu-gate loads, the same count the USA build
needed (patching only the two obvious gates was not enough there either).
Each load's base register is also its destination (`lui $r,0x800a;
lw $r,0x30b8($r)`), so replacing the load with `li $r,-1` leaves the lui
harmless.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc
from build_options_poc import rebuild_mode2_form1_integrity
from build_subtitle_v37 import make_raw_sector

EXE_PATH = "SLPM_860.20"
EXE_RAM_BASE = 0x80010000
EXE_HEADER_SIZE = 0x800
MASK = 0x800A30B8

# address -> register number; every one is `lw $r, 0x30b8($r)`
GATE_SITES = {
    0x8004A37C: 5,   # $a1
    0x8004A410: 2,   # $v0
    0x8004A440: 4,   # $a0
    0x8004A510: 3,   # $v1   (the branch at 0x8004A51C is what 2026-08-02 nop'd)
    0x8004A56C: 3,   # $v1   (likewise 0x8004A578)
    0x8004A674: 2,   # $v0
    0x8004A6A4: 4,   # $a0
}


def load_word(reg: int) -> int:
    return 0x8C000000 | (reg << 21) | (reg << 16) | (MASK & 0xFFFF)


def li_minus_one(reg: int) -> int:
    return 0x2400FFFF | (reg << 16)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-place", type=Path, required=True)
    args = parser.parse_args()

    target = args.in_place
    if not target.exists():
        raise SystemExit(f"missing image: {target}")

    info = disc.inspect(target)
    entry = next(e for e in info["entries"] if e["path"] == EXE_PATH)
    with target.open("rb") as handle:
        data, forms = disc.read_extent(handle, entry["lba"], entry["size"])
    assert forms == {"form1"}, forms
    data = bytearray(data)

    changed = 0
    for address, reg in sorted(GATE_SITES.items()):
        offset = EXE_HEADER_SIZE + address - EXE_RAM_BASE
        actual = struct.unpack_from("<I", data, offset)[0]
        want, repl = load_word(reg), li_minus_one(reg)
        if actual == repl:
            print(f"  0x{address:08X}: already unlocked")
            continue
        if actual != want:
            raise SystemExit(
                f"0x{address:08X}: expected 0x{want:08X}, found 0x{actual:08X}")
        struct.pack_into("<I", data, offset, repl)
        print(f"  0x{address:08X}: 0x{actual:08X} -> 0x{repl:08X}  (li $r{reg},-1)")
        changed += 1

    if not changed:
        print("nothing to do")
        return

    sectors = (entry["size"] + 2047) // 2048
    with target.open("rb") as src:
        src.seek(entry["lba"] * disc.RAW_SECTOR_SIZE + 16)
        first_sub = src.read(8)
        src.seek((entry["lba"] + sectors - 1) * disc.RAW_SECTOR_SIZE + 16)
        last_sub = src.read(8)
    with target.open("r+b") as out:
        out.seek(entry["lba"] * disc.RAW_SECTOR_SIZE)
        for index in range(sectors):
            chunk = bytes(data[index * 2048:(index + 1) * 2048]).ljust(2048, b"\x00")
            sub = last_sub if index == sectors - 1 else first_sub
            out.write(make_raw_sector(entry["lba"] + index, sub,
                                      chunk, rebuild_mode2_form1_integrity))

    print(f"{changed} gate(s) forced open in {target.name}")


if __name__ == "__main__":
    main()
