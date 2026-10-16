#!/usr/bin/env python3
"""Search logical PS1 disc files for raw copies of a known TIM pixel payload."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import inspect_psx_disc as disc
import scan_psx_tim as timlib


def entropy(data: bytes) -> float:
    counts = Counter(data)
    total = len(data)
    return -sum(
        (count / total) * math.log2(count / total) for count in counts.values()
    )


def nibble_swap(data: bytes) -> bytes:
    return bytes(((value & 0x0F) << 4) | (value >> 4) for value in data)


def all_offsets(data: bytes, pattern: bytes):
    start = 0
    while True:
        offset = data.find(pattern, start)
        if offset < 0:
            return
        yield offset
        start = offset + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--tim-offset", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--window-size", type=int, default=128)
    parser.add_argument("--window-count", type=int, default=12)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    inspection = disc.inspect(args.image)
    target_path = args.target_path.upper().replace("\\", "/")
    target_entry = next(
        entry
        for entry in inspection["entries"]
        if not entry["is_dir"] and entry["path"].upper() == target_path
    )
    with args.image.open("rb") as track:
        target_data, _ = disc.read_extent(
            track, target_entry["lba"], target_entry["size"]
        )
    parsed = timlib.parse_tim(target_data, args.tim_offset)
    if parsed is None:
        raise ValueError("target TIM did not validate")
    image_meta = parsed["image"]
    payload = target_data[
        image_meta["payload_offset"] :
        image_meta["payload_offset"] + image_meta["payload_size"]
    ]

    candidates = []
    for offset in range(0, len(payload) - args.window_size + 1, args.window_size):
        window = payload[offset : offset + args.window_size]
        candidates.append((entropy(window), offset, window))
    selected = sorted(candidates, reverse=True)[: args.window_count]
    selected.sort(key=lambda item: item[1])

    signatures = []
    for index, (score, offset, window) in enumerate(selected):
        signatures.append(
            {
                "signature_id": f"raw-{index:02d}",
                "payload_offset": offset,
                "length": len(window),
                "entropy": score,
                "variant": "raw",
                "bytes": window,
            }
        )
        signatures.append(
            {
                "signature_id": f"nibble-swap-{index:02d}",
                "payload_offset": offset,
                "length": len(window),
                "entropy": score,
                "variant": "nibble_swap",
                "bytes": nibble_swap(window),
            }
        )

    matches = []
    full_payload_matches = []
    with args.image.open("rb") as track:
        for entry in inspection["entries"]:
            if entry["is_dir"]:
                continue
            data, forms = disc.read_extent(track, entry["lba"], entry["size"])
            for offset in all_offsets(data, payload):
                full_payload_matches.append(
                    {
                        "path": entry["path"],
                        "file_offset": offset,
                        "sector_forms": sorted(forms),
                    }
                )
            for signature in signatures:
                for offset in all_offsets(data, signature["bytes"]):
                    matches.append(
                        {
                            "signature_id": signature["signature_id"],
                            "variant": signature["variant"],
                            "target_payload_offset": signature["payload_offset"],
                            "path": entry["path"],
                            "file_offset": offset,
                            "sector_forms": sorted(forms),
                        }
                    )

    by_location: dict[tuple[str, int, str], list[str]] = {}
    for match in matches:
        base = match["file_offset"] - match["target_payload_offset"]
        key = (match["path"], base, match["variant"])
        by_location.setdefault(key, []).append(match["signature_id"])
    clusters = [
        {
            "path": path,
            "estimated_payload_start": base,
            "estimated_payload_start_hex": f"0x{base:08X}",
            "variant": variant,
            "matched_signature_count": len(ids),
            "matched_signature_ids": sorted(ids),
        }
        for (path, base, variant), ids in by_location.items()
    ]
    clusters.sort(
        key=lambda item: (
            -item["matched_signature_count"],
            item["path"],
            item["estimated_payload_start"],
            item["variant"],
        )
    )

    report = {
        "image": inspection["image"],
        "target": {
            "path": target_entry["path"],
            "tim_offset": args.tim_offset,
            "tim_offset_hex": f"0x{args.tim_offset:08X}",
            "pixel_width": parsed["pixel_width"],
            "pixel_height": parsed["pixel_height"],
            "bpp": parsed["bpp"],
            "image_payload_offset": image_meta["payload_offset"],
            "image_payload_size": len(payload),
        },
        "files_scanned": sum(
            1 for entry in inspection["entries"] if not entry["is_dir"]
        ),
        "signature_window_size": args.window_size,
        "signature_window_count_per_variant": args.window_count,
        "full_payload_matches": full_payload_matches,
        "clusters": clusters,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"files_scanned={report['files_scanned']}")
    print(f"full_payload_matches={len(full_payload_matches)}")
    print(f"clusters={len(clusters)}")
    for cluster in clusters[:20]:
        print(
            f"{cluster['matched_signature_count']:2d} "
            f"{cluster['variant']:11s} {cluster['path']} "
            f"{cluster['estimated_payload_start_hex']}"
        )
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
