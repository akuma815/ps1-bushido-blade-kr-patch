#!/usr/bin/env python3
"""Build a no-subtitle V00 selector context logger for Bushido Blade (Japan)."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import inspect_psx_disc as disc
import build_subtitle_poc as sub


LOG_RAM = 0x80122000
LOG_MAGIC = 0x4C303056  # little-endian bytes: V00L
LOG_HEADER_SIZE = 16
LOG_ENTRY_SIZE = 32
LOG_MAX_ENTRIES = 60


def build_logger() -> bytes:
    source = f"""
        addiu $sp, $sp, -0x30
        sw $v0, 0x00($sp)
        sw $v1, 0x04($sp)
        sw $t0, 0x08($sp)
        sw $t1, 0x0c($sp)
        sw $t2, 0x10($sp)
        sw $t3, 0x14($sp)
        sw $t4, 0x18($sp)
        sw $t5, 0x1c($sp)
        sw $t6, 0x20($sp)
        sw $t7, 0x24($sp)
        sw $t8, 0x28($sp)
        sw $t9, 0x2c($sp)

        lui $t0, 0x8012
        ori $t0, $t0, 0x2000
        lui $t1, 0x{LOG_MAGIC >> 16:04x}
        ori $t1, $t1, 0x{LOG_MAGIC & 0xffff:04x}
        lw $t2, 0($t0)
        nop
        beq $t1, $t2, log_ready
        nop
        sw $t1, 0($t0)
        sw $zero, 4($t0)
        sw $zero, 8($t0)
        sw $zero, 12($t0)
    log_ready:
        lw $t2, 4($t0)
        nop
        sltiu $t3, $t2, {LOG_MAX_ENTRIES}
        beq $t3, $zero, log_restore
        nop
        sll $t4, $t2, 5
        addiu $t4, $t4, {LOG_HEADER_SIZE}
        addu $t4, $t0, $t4
        sw $t2, 0($t4)
        sw $a0, 4($t4)
        sw $a1, 8($t4)
        sw $a2, 12($t4)
        sw $a3, 16($t4)
        sw $s0, 20($t4)
        sw $s1, 24($t4)
        sw $s2, 28($t4)
        addiu $t2, $t2, 1
        sw $t2, 4($t0)
    log_restore:
        lw $v0, 0x00($sp)
        lw $v1, 0x04($sp)
        lw $t0, 0x08($sp)
        lw $t1, 0x0c($sp)
        lw $t2, 0x10($sp)
        lw $t3, 0x14($sp)
        lw $t4, 0x18($sp)
        lw $t5, 0x1c($sp)
        lw $t6, 0x20($sp)
        lw $t7, 0x24($sp)
        lw $t8, 0x28($sp)
        lw $t9, 0x2c($sp)
        addiu $sp, $sp, 0x30
        j 0x{sub.V00_EVENT_CONTINUE_RAM:08x}
        nop
    """
    return sub.assemble(source, sub.APPEND_RAM)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--base-name",
        default="Bushido Blade (Japan) [V00 Event Context Log v1]",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if sub.sha256_file(args.image) != sub.EXPECTED_BIN_SHA256:
        raise ValueError("unsupported source BIN hash")
    inspection = disc.inspect(args.image)
    entry = next(item for item in inspection["entries"] if item["path"] == sub.EXE_PATH)
    if entry["sha256"].upper() != sub.EXPECTED_EXE_SHA256:
        raise ValueError("unsupported SLPM executable baseline")
    with args.image.open("rb") as track:
        original_exe, forms = disc.read_extent(track, entry["lba"], entry["size"])
    if forms != {"form1"}:
        raise ValueError("SLPM executable is not entirely Mode2 Form1")

    hook_offset = sub.EXE_HEADER_SIZE + sub.V00_EVENT_HOOK_RAM - sub.EXE_RAM_BASE
    expected = sub.encode_jump(sub.V00_EVENT_CONTINUE_RAM, link=False) + b"\0\0\0\0"
    if original_exe[hook_offset : hook_offset + 8] != expected:
        raise ValueError("V00 selector expected-write mismatch")
    replacement = sub.encode_jump(sub.APPEND_RAM, link=False) + sub.assemble(
        "move $a0, $s2", sub.V00_EVENT_HOOK_RAM + 4
    )

    logger = build_logger()
    if sub.APPEND_RAM + len(logger) > sub.CAVE_END_RAM:
        raise ValueError("event logger exceeds verified code cave")
    cave_offset = sub.APPEND_FILE_OFFSET
    cave_end_offset = sub.EXE_HEADER_SIZE + sub.CAVE_END_RAM - sub.EXE_RAM_BASE
    cave_source_hash = sub.sha256_bytes(original_exe[cave_offset:cave_end_offset])
    patched_exe = bytearray(original_exe)
    patched_exe[hook_offset : hook_offset + 8] = replacement
    patched_exe[cave_offset : cave_offset + len(logger)] = logger

    output_bin = args.output_dir / f"{args.base_name}.bin"
    output_cue = args.output_dir / f"{args.base_name}.cue"
    shutil.copyfile(args.image, output_bin)
    touched = []
    for index in range(len(patched_exe) // disc.LOGICAL_SECTOR_SIZE):
        chunk = patched_exe[
            index * disc.LOGICAL_SECTOR_SIZE : (index + 1) * disc.LOGICAL_SECTOR_SIZE
        ]
        lba = entry["lba"] + index

        def replace_data(raw: bytearray, chunk=chunk) -> None:
            start = disc.MODE2_FORM1_DATA_OFFSET
            raw[start : start + disc.LOGICAL_SECTOR_SIZE] = chunk

        touched.append(sub.patch_logical_sector(output_bin, lba, replace_data))

    output_cue.write_text(
        f'FILE "{output_bin.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
        newline="\n",
    )
    reparsed = disc.inspect(output_bin)
    patched_entry = next(item for item in reparsed["entries"] if item["path"] == sub.EXE_PATH)
    with output_bin.open("rb") as track:
        roundtrip, roundtrip_forms = disc.read_extent(
            track, patched_entry["lba"], patched_entry["size"]
        )
    if roundtrip_forms != {"form1"} or roundtrip != bytes(patched_exe):
        raise ValueError("patched SLPM failed disc roundtrip")

    report = {
        "gate_id": "BBJ-V00-EVENT-LOG-001",
        "status": "static_build_passed_runtime_capture_pending",
        "baseline": {
            "bin_sha256": sub.EXPECTED_BIN_SHA256,
            "exe_sha256": sub.EXPECTED_EXE_SHA256,
        },
        "hook": {
            "ram": f"0x{sub.V00_EVENT_HOOK_RAM:08X}",
            "continue_ram": f"0x{sub.V00_EVENT_CONTINUE_RAM:08X}",
            "before": expected.hex().upper(),
            "after": replacement.hex().upper(),
            "argument_contract": "a0=s2 in jump delay slot",
        },
        "logger": {
            "ram": f"0x{sub.APPEND_RAM:08X}",
            "size": len(logger),
            "cave_source_sha256": cave_source_hash,
            "log_ram": f"0x{LOG_RAM:08X}",
            "magic": f"0x{LOG_MAGIC:08X}",
            "header_size": LOG_HEADER_SIZE,
            "entry_size": LOG_ENTRY_SIZE,
            "max_entries": LOG_MAX_ENTRIES,
            "fields": ["sequence", "a0", "a1", "a2", "a3", "s0", "s1", "s2"],
            "registers_restored": ["v0", "v1", *[f"t{i}" for i in range(10)]],
        },
        "static_checks": {
            "source_hash": True,
            "expected_write": True,
            "mode2_form1_integrity": True,
            "disc_size_preserved": output_bin.stat().st_size == args.image.stat().st_size,
            "same_exe_size": len(patched_exe) == len(original_exe),
            "code_cave_fit": sub.APPEND_RAM + len(logger) <= sub.CAVE_END_RAM,
            "exe_roundtrip": True,
        },
        "output": {
            "bin": str(output_bin),
            "cue": str(output_cue),
            "bin_sha256": sub.sha256_file(output_bin),
        },
        "sector_changes": {"exe_sector_count": len(touched)},
    }
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"output_cue={output_cue}")
    print(f"logger_size={len(logger)}")
    print(f"output_sha256={report['output']['bin_sha256']}")


if __name__ == "__main__":
    main()
