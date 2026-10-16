#!/usr/bin/env python3
"""List an ISO 9660 filesystem stored in a raw MODE2/2352 PS1 track."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


RAW_SECTOR_SIZE = 2352
MODE2_FORM1_DATA_OFFSET = 24
LOGICAL_SECTOR_SIZE = 2048


def read_sector(track, lba: int) -> tuple[bytes, str]:
    track.seek(lba * RAW_SECTOR_SIZE)
    raw = track.read(RAW_SECTOR_SIZE)
    if len(raw) != RAW_SECTOR_SIZE:
        raise ValueError(f"short sector read at LBA {lba}")
    if raw[:12] != b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00":
        raise ValueError(f"invalid raw-sector sync at LBA {lba}")
    if raw[15] != 2:
        raise ValueError(f"expected MODE2 sector at LBA {lba}, got mode {raw[15]}")
    if raw[18] & 0x20:
        return raw[MODE2_FORM1_DATA_OFFSET : MODE2_FORM1_DATA_OFFSET + 2324], "form2"
    return (
        raw[
            MODE2_FORM1_DATA_OFFSET : MODE2_FORM1_DATA_OFFSET + LOGICAL_SECTOR_SIZE
        ],
        "form1",
    )


def read_extent(track, lba: int, size: int) -> tuple[bytes, set[str]]:
    chunks = []
    sector_forms = set()
    remaining = size
    current_lba = lba
    while remaining:
        sector, sector_form = read_sector(track, current_lba)
        sector_forms.add(sector_form)
        take = min(remaining, len(sector))
        chunks.append(sector[:take])
        remaining -= take
        current_lba += 1
    return b"".join(chunks), sector_forms


def parse_record(record: bytes) -> dict:
    lba_le = struct.unpack_from("<I", record, 2)[0]
    lba_be = struct.unpack_from(">I", record, 6)[0]
    size_le = struct.unpack_from("<I", record, 10)[0]
    size_be = struct.unpack_from(">I", record, 14)[0]
    if lba_le != lba_be or size_le != size_be:
        raise ValueError("ISO 9660 little/big-endian directory fields disagree")
    name_len = record[32]
    name_raw = record[33 : 33 + name_len]
    if name_raw == b"\x00":
        name = "."
    elif name_raw == b"\x01":
        name = ".."
    else:
        name = name_raw.decode("ascii", errors="replace").split(";", 1)[0]
    return {
        "name": name,
        "lba": lba_le,
        "size": size_le,
        "flags": record[25],
        "is_dir": bool(record[25] & 0x02),
    }


def iter_directory_records(data: bytes):
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset = ((offset // LOGICAL_SECTOR_SIZE) + 1) * LOGICAL_SECTOR_SIZE
            continue
        record = data[offset : offset + length]
        if len(record) != length:
            raise ValueError("truncated ISO 9660 directory record")
        yield parse_record(record)
        offset += length


def scan_signatures(data: bytes) -> dict:
    tim = b"\x10\x00\x00\x00"
    return {
        "psx_exe": data[:8] == b"PS-X EXE",
        "tim_header_offsets": [
            offset
            for offset in range(0, max(0, len(data) - 8))
            if data[offset : offset + 4] == tim
            and (int.from_bytes(data[offset + 4 : offset + 8], "little") & ~0x0F) == 0
        ][:100],
    }


def inspect(image: Path) -> dict:
    entries = []
    with image.open("rb") as track:
        pvd, pvd_form = read_sector(track, 16)
        if pvd_form != "form1":
            raise ValueError("ISO 9660 primary volume descriptor is not Form 1")
        if pvd[0] != 1 or pvd[1:6] != b"CD001":
            raise ValueError("ISO 9660 primary volume descriptor not found at LBA 16")
        volume_id = pvd[40:72].decode("ascii", errors="replace").rstrip()
        root_length = pvd[156]
        root = parse_record(pvd[156 : 156 + root_length])

        def walk(directory: dict, parent: str) -> None:
            directory_data, directory_forms = read_extent(
                track, directory["lba"], directory["size"]
            )
            if directory_forms != {"form1"}:
                raise ValueError(f"directory {parent or '/'} is not entirely Form 1")
            for entry in iter_directory_records(directory_data):
                if entry["name"] in {".", ".."}:
                    continue
                path = f"{parent}/{entry['name']}" if parent else entry["name"]
                item = {
                    "path": path,
                    "lba": entry["lba"],
                    "size": entry["size"],
                    "is_dir": entry["is_dir"],
                }
                if not entry["is_dir"]:
                    data, sector_forms = read_extent(
                        track, entry["lba"], entry["size"]
                    )
                    item["sha256"] = hashlib.sha256(data).hexdigest()
                    item["sector_forms"] = sorted(sector_forms)
                    item.update(scan_signatures(data))
                entries.append(item)
                if entry["is_dir"]:
                    walk(entry, path)

        walk(root, "")
    return {
        "image": image.name,
        "image_size": image.stat().st_size,
        "volume_id": volume_id,
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--extract", nargs="*", default=[])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = inspect(args.image)
    if args.extract:
        if args.output_dir is None:
            parser.error("--output-dir is required with --extract")
        wanted = {name.upper().replace("\\", "/") for name in args.extract}
        entry_map = {
            entry["path"].upper(): entry
            for entry in result["entries"]
            if not entry["is_dir"]
        }
        missing = sorted(wanted - entry_map.keys())
        if missing:
            parser.error(f"paths not found in image: {', '.join(missing)}")
        with args.image.open("rb") as track:
            for name in sorted(wanted):
                entry = entry_map[name]
                data, _ = read_extent(track, entry["lba"], entry["size"])
                destination = args.output_dir / Path(entry["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                print(f"Extracted {entry['path']} -> {destination}")
        if not args.json:
            return
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"Volume: {result['volume_id']}")
    for entry in result["entries"]:
        kind = "DIR " if entry["is_dir"] else "FILE"
        suffix = ""
        if entry.get("psx_exe"):
            suffix += " [PS-X EXE]"
        if entry.get("tim_header_offsets"):
            suffix += f" [TIM candidates: {len(entry['tim_header_offsets'])}]"
        if entry.get("sector_forms"):
            suffix += f" [{'+'.join(entry['sector_forms'])}]"
        print(
            f"{kind} LBA={entry['lba']:6d} SIZE={entry['size']:10d} "
            f"{entry['path']}{suffix}"
        )


if __name__ == "__main__":
    main()
