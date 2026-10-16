#!/usr/bin/env python3
"""Build a decoded-pixel deduplicated inventory from the PS1 TIM manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def decoded_hash(path: Path) -> tuple[str, str]:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        digest = hashlib.sha256()
        digest.update(f"RGBA:{rgba.width}x{rgba.height}\0".encode("ascii"))
        digest.update(rgba.tobytes())
        return digest.hexdigest().upper(), rgba.mode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    groups: dict[str, list[dict]] = defaultdict(list)
    bpp_counts: Counter[int] = Counter()
    path_prefix_counts: Counter[str] = Counter()

    for record in source["records"]:
        png_value = record.get("png")
        if not png_value:
            continue
        png = Path(png_value)
        if not png.is_file():
            raise FileNotFoundError(png)
        pixel_sha256, decoded_mode = decoded_hash(png)
        logical_key = f"{record['path']}@{record['offset']:08X}"
        occurrence_id = "BBJ-TIM-" + hashlib.sha1(
            logical_key.encode("utf-8")
        ).hexdigest()[:12].upper()
        occurrence = {
            "occurrence_id": occurrence_id,
            "path": record["path"],
            "file_lba": record["file_lba"],
            "file_size": record["file_size"],
            "offset": record["offset"],
            "offset_hex": f"0x{record['offset']:08X}",
            "bpp": record["bpp"],
            "pixel_width": record["pixel_width"],
            "pixel_height": record["pixel_height"],
            "tim_total_size": record["total_size"],
            "has_clut": record["has_clut"],
            "png": relative_path(png, args.project_root),
            "decoded_mode": decoded_mode,
            "decoded_pixel_sha256": pixel_sha256,
        }
        groups[pixel_sha256].append(occurrence)
        bpp_counts[record["bpp"]] += 1
        path_prefix_counts[record["path"].split("/", 1)[0]] += 1

    visual_groups = []
    representatives = []
    for pixel_sha256, occurrences in sorted(
        groups.items(), key=lambda item: (item[1][0]["path"], item[1][0]["offset"])
    ):
        representative = occurrences[0]
        visual_id = f"BBJ-VIS-{pixel_sha256[:12]}"
        visual_groups.append(
            {
                "visual_id": visual_id,
                "state": "unreviewed",
                "classification": "unknown",
                "representative_png": representative["png"],
                "decoded_pixel_sha256": pixel_sha256,
                "pixel_width": representative["pixel_width"],
                "pixel_height": representative["pixel_height"],
                "occurrence_count": len(occurrences),
                "occurrences": occurrences,
            }
        )
        representatives.append(
            {
                "visual_id": visual_id,
                "path": representative["path"],
                "offset": representative["offset"],
                "pixel_width": representative["pixel_width"],
                "pixel_height": representative["pixel_height"],
                "bpp": representative["bpp"],
                "png": str((args.project_root / representative["png"]).resolve()),
            }
        )

    result = {
        "inventory_version": 1,
        "baseline": {
            "image": source["image"],
            "source_manifest": relative_path(args.manifest, args.project_root),
        },
        "states": [
            "unreviewed",
            "no_text",
            "text_found",
            "uncertain",
            "translated",
            "verified",
        ],
        "classifications": [
            "unknown",
            "text",
            "logo",
            "font_or_glyph_table",
            "protected_graphic",
            "blank_or_mask",
        ],
        "summary": {
            "tim_occurrence_count": sum(len(items) for items in groups.values()),
            "unique_decoded_visual_count": len(groups),
            "duplicate_occurrence_count": sum(
                len(items) - 1 for items in groups.values()
            ),
            "bpp_occurrence_counts": {
                str(key): value for key, value in sorted(bpp_counts.items())
            },
            "top_level_path_counts": dict(sorted(path_prefix_counts.items())),
            "reviewed_unique_visual_count": 0,
            "unreviewed_unique_visual_count": len(groups),
        },
        "visual_groups": visual_groups,
        "records": representatives,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"tim_occurrences={result['summary']['tim_occurrence_count']}")
    print(f"unique_visuals={result['summary']['unique_decoded_visual_count']}")
    print(f"duplicates={result['summary']['duplicate_occurrence_count']}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
