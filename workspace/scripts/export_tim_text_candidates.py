#!/usr/bin/env python3
"""Export reviewed TIM text candidates without overwriting user edits."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def parse_offset(value: str | int) -> int:
    if isinstance(value, int):
        return value
    return int(value, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    by_source = {
        (record["path"], record["offset"]): record for record in inventory["records"]
    }
    source_dir = args.output_dir / "source"
    edited_dir = args.output_dir / "edited"
    source_dir.mkdir(parents=True, exist_ok=True)
    edited_dir.mkdir(parents=True, exist_ok=True)

    exported = []
    missing = []
    created_edit_templates = 0
    preserved_edit_templates = 0
    for group in selection["groups"]:
        path = group["path"]
        for offset_value in group["offsets"]:
            offset = parse_offset(offset_value)
            record = by_source.get((path, offset))
            if record is None:
                missing.append({"path": path, "offset": offset})
                continue
            input_png = Path(record["png"])
            if not input_png.is_absolute():
                input_png = args.project_root / input_png
            safe_path = path.replace("/", "_").replace("\\", "_").replace(".", "_")
            name = f"{safe_path}_{offset:08X}.png"
            source_png = source_dir / name
            edited_png = edited_dir / name
            shutil.copyfile(input_png, source_png)
            if edited_png.exists():
                preserved_edit_templates += 1
            else:
                shutil.copyfile(input_png, edited_png)
                created_edit_templates += 1
            candidate_key = f"{path}@{offset:08X}"
            exported.append(
                {
                    "candidate_id": "BBJ-GFX-"
                    + hashlib.sha1(candidate_key.encode("utf-8")).hexdigest()[
                        :12
                    ].upper(),
                    "state": "found",
                    "classification": group["classification"],
                    "protected_graphic": bool(group.get("protected_graphic", False)),
                    "source": {
                        "path": path,
                        "offset": offset,
                        "offset_hex": f"0x{offset:08X}",
                        "pixel_width": record["pixel_width"],
                        "pixel_height": record["pixel_height"],
                        "bpp": record["bpp"],
                        "tim_total_size": next(
                            occurrence["tim_total_size"]
                            for visual in inventory["visual_groups"]
                            if visual["visual_id"] == record["visual_id"]
                            for occurrence in visual["occurrences"]
                            if occurrence["path"] == path
                            and occurrence["offset"] == offset
                        ),
                        "decoded_pixel_sha256": next(
                            visual["decoded_pixel_sha256"]
                            for visual in inventory["visual_groups"]
                            if visual["visual_id"] == record["visual_id"]
                        ),
                    },
                    "source_png": source_png.relative_to(args.project_root).as_posix(),
                    "edited_png": edited_png.relative_to(args.project_root).as_posix(),
                    "runtime_reachability": "unknown",
                    "text_blocks": [],
                }
            )

    if missing:
        raise ValueError(f"selection contains missing TIM records: {missing}")
    result = {
        "catalog_version": 1,
        "baseline": inventory["baseline"],
        "scope": selection["scope"],
        "review_status": selection["review_status"],
        "candidate_count": len(exported),
        "notes": selection["notes"],
        "candidates": exported,
    }
    manifest = args.output_dir / "candidate_catalog.json"
    manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"candidates={len(exported)}")
    print(f"created_edit_templates={created_edit_templates}")
    print(f"preserved_edit_templates={preserved_edit_templates}")
    print(f"catalog={manifest}")


if __name__ == "__main__":
    main()
