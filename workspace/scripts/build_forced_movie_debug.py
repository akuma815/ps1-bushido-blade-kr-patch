#!/usr/bin/env python3
"""Debug build: force every kind-0 cutscene to play a chosen movie id.

Investigation aid only — never ship this. The JP movie start function is
0x8003AA78(a0=kind, a1=buffer, a2=movie_id); it keeps the id in s2 via
`move $s2, $a2` at 0x8003AA9C (the delay slot of the kind test). Replacing
that single instruction with `ori $s2, $zero, N` makes any cutscene load
file id 0x57+N and use param-table entry N, so the ending movie can be
reached from the opening without a full playthrough.

File id map (load table 0x80088A4C, 8 bytes/entry, verified against the
disc): 0x56=O05 common, 0x57+id=O06..O14 -> id 0..5 = the six character
endings, 6=O12, 7=O13, 8=O14.
"""

from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

import inspect_psx_disc as disc
from build_official_glyph_poc import (
    EXE_HEADER_SIZE,
    EXE_PATH,
    EXE_RAM_BASE,
    EXPECTED_BIN_SHA256,
    EXPECTED_EXE_SHA256,
)
from build_subtitle_poc import assemble, patch_logical_sector, sha256_file

MOVIE_ID_INSTRUCTION_RAM = 0x8003AA9C
EXPECTED_INSTRUCTION = "2190c000"  # move $s2, $a2 (addu $s2, $a2, $zero)
KIND_INSTRUCTION_RAM = 0x8003AA80
EXPECTED_KIND_INSTRUCTION = "21888000"  # move $s1, $a0
# The intro-cutscene call site (movie_id = [0x800A30A0] % 6). Redirecting it
# to another setup function runs that sequence from the opening instead.
INTRO_CALLSITE_RAM = 0x80049A44
ENDING_SETUP_RAM = 0x8003C950  # loads DATA3/O14.DAT (endings) into the presenter
POEM_SETUP_HINT = 0x8003CA6C  # loads DATA3/O18.DAT, for reference
# Ending-gallery unlock gates: [0x800A30B8] holds the unlock bitmask
# (bit s1 = normal ending, bit s1+6 = good ending). Replacing both
# "branch away when locked" tests with nops makes every entry selectable,
# so any ending can be replayed without meeting its story conditions.
UNLOCK_GATES = (0x8004A51C, 0x8004A578)
EXPECTED_GATE_ENCODINGS = ("21006010", "0a006010")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--movie-id", type=int, default=8)
    parser.add_argument(
        "--kind",
        type=int,
        default=None,
        help="also force the movie kind (0 = per-id file, 1 = fixed O15 path)",
    )
    parser.add_argument(
        "--force-setup",
        type=lambda value: int(value, 0),
        default=None,
        help=(
            "redirect the intro-cutscene call site to this setup function "
            f"(e.g. 0x{ENDING_SETUP_RAM:08X} = the O14 ending sequence)"
        ),
    )
    parser.add_argument(
        "--unlock-endings",
        action="store_true",
        help="make every ending-gallery entry selectable (nops the unlock gates)",
    )
    parser.add_argument("--base-name", default=None)
    args = parser.parse_args()

    if not 0 <= args.movie_id <= 13:
        raise ValueError("movie id outside the load-table range")
    if args.unlock_endings:
        suffix = "endings unlocked"
    elif args.force_setup is not None:
        suffix = f"setup 0x{args.force_setup:08X}"
    else:
        suffix = f"movie {args.movie_id}" + (
            f" kind {args.kind}" if args.kind is not None else ""
        )
    base_name = args.base_name or f"Bushido Blade (Japan) [DEBUG forced {suffix}]"

    if sha256_file(args.image) != EXPECTED_BIN_SHA256:
        raise ValueError("unsupported source BIN hash")
    inspection = disc.inspect(args.image)
    entry = next(item for item in inspection["entries"] if item["path"] == EXE_PATH)
    if entry["sha256"].upper() != EXPECTED_EXE_SHA256:
        raise ValueError("unsupported Japanese executable baseline")
    with args.image.open("rb") as source:
        original_exe, forms = disc.read_extent(source, entry["lba"], entry["size"])
    if forms != {"form1"}:
        raise ValueError("Japanese executable is not Form 1")

    patched_exe = bytearray(original_exe)
    extra_offsets: list[int] = []

    if args.unlock_endings:
        offset = None
        for gate_ram, expected in zip(UNLOCK_GATES, EXPECTED_GATE_ENCODINGS):
            gate_offset = EXE_HEADER_SIZE + gate_ram - EXE_RAM_BASE
            gate_before = bytes(original_exe[gate_offset : gate_offset + 4])
            if gate_before.hex() != expected:
                raise ValueError(
                    f"unexpected unlock gate at 0x{gate_ram:08X}: {gate_before.hex()}"
                )
            patched_exe[gate_offset : gate_offset + 4] = b"\x00\x00\x00\x00"
            extra_offsets.append(gate_offset)
            print(f"patch 0x{gate_ram:08X}: {gate_before.hex()} -> 00000000 (nop)")
    elif args.force_setup is not None:
        offset = EXE_HEADER_SIZE + INTRO_CALLSITE_RAM - EXE_RAM_BASE
        before = bytes(original_exe[offset : offset + 4])
        expected_call = struct.pack(
            "<I", 0x0C000000 | ((0x8003AA78 >> 2) & 0x03FFFFFF)
        )
        if before != expected_call:
            raise ValueError(f"unexpected intro call site: {before.hex()}")
        after = struct.pack(
            "<I", 0x0C000000 | ((args.force_setup >> 2) & 0x03FFFFFF)
        )
        patched_exe[offset : offset + 4] = after
        print(
            f"patch 0x{INTRO_CALLSITE_RAM:08X}: {before.hex()} -> {after.hex()} "
            f"(jal 0x{args.force_setup:08X})"
        )
    else:
        offset = EXE_HEADER_SIZE + MOVIE_ID_INSTRUCTION_RAM - EXE_RAM_BASE
        before = bytes(original_exe[offset : offset + 4])
        if before.hex() != EXPECTED_INSTRUCTION:
            raise ValueError(f"unexpected instruction at hook: {before.hex()}")
        after = assemble(f"ori $s2, $zero, {args.movie_id}", MOVIE_ID_INSTRUCTION_RAM)
        if len(after) != 4:
            raise ValueError("replacement must stay one instruction")
        patched_exe[offset : offset + 4] = after

    kind_offset = None
    if args.kind is not None and args.force_setup is None and not args.unlock_endings:
        kind_offset = EXE_HEADER_SIZE + KIND_INSTRUCTION_RAM - EXE_RAM_BASE
        kind_before = bytes(original_exe[kind_offset : kind_offset + 4])
        if kind_before.hex() != EXPECTED_KIND_INSTRUCTION:
            raise ValueError(f"unexpected kind instruction: {kind_before.hex()}")
        kind_after = assemble(
            f"ori $s1, $zero, {args.kind}", KIND_INSTRUCTION_RAM
        )
        patched_exe[kind_offset : kind_offset + 4] = kind_after
        print(
            f"patch 0x{KIND_INSTRUCTION_RAM:08X}: "
            f"{kind_before.hex()} -> {kind_after.hex()} (kind={args.kind})"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = args.output_dir / f"{base_name}.bin"
    output_cue = args.output_dir / f"{base_name}.cue"
    shutil.copyfile(args.image, output_bin)

    sectors = set()
    if not args.unlock_endings:
        sectors.add(offset // disc.LOGICAL_SECTOR_SIZE)
    for extra in extra_offsets:
        sectors.add(extra // disc.LOGICAL_SECTOR_SIZE)
    if kind_offset is not None:
        sectors.add(kind_offset // disc.LOGICAL_SECTOR_SIZE)
    for sector in sorted(sectors):
        chunk = patched_exe[
            sector * disc.LOGICAL_SECTOR_SIZE : (sector + 1) * disc.LOGICAL_SECTOR_SIZE
        ]

        def replace_data(raw: bytearray, chunk=chunk):
            start = disc.MODE2_FORM1_DATA_OFFSET
            raw[start : start + disc.LOGICAL_SECTOR_SIZE] = chunk

        patch_logical_sector(output_bin, entry["lba"] + sector, replace_data)
    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
        newline="\n",
    )

    reparsed = disc.inspect(output_bin)
    patched_entry = next(i for i in reparsed["entries"] if i["path"] == EXE_PATH)
    with output_bin.open("rb") as track:
        disc_exe, disc_forms = disc.read_extent(
            track, patched_entry["lba"], patched_entry["size"]
        )
    if disc_forms != {"form1"} or disc_exe != bytes(patched_exe):
        raise ValueError("patched executable failed disc roundtrip")

    if args.force_setup is None and not args.unlock_endings:
        print(f"movie_id={args.movie_id} (file id 0x{0x57 + args.movie_id:02X})")
        print(
            f"patch 0x{MOVIE_ID_INSTRUCTION_RAM:08X}: "
            f"{before.hex()} -> {after.hex()}"
        )
    print(f"output_cue={output_cue}")
    print(f"output_sha256={sha256_file(output_bin)}")


if __name__ == "__main__":
    main()
