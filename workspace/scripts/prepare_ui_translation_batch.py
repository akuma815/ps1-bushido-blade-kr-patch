#!/usr/bin/env python3
"""Prepare the remaining DATA3 UI TIM images as a protected editing batch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path


TARGETS = {
    "DATA3/O02.DAT": "character_weapon_select",
    "DATA3/O04.DAT": "options_system",
    "DATA3/O12.DAT": "main_menu",
    "DATA3/O13.DAT": "versus_stage_settings",
    "DATA3/O14.DAT": "character_profile",
}

CHARACTER_NAME_OFFSETS = {
    0x0000E8EC,
    0x0000ED64,
    0x0000F1DC,
    0x0000F654,
    0x0000FACC,
    0x0000FF44,
}

# These assets already have a separate Korean PoC/edit path and are not part of
# the user's next manual image-editing batch.
COMPLETED_ELSEWHERE = {
    ("DATA3/O04.DAT", 0x00003CCC): "options_header_poc",
    ("DATA3/O12.DAT", 0x000001CC): "title_logo_verified",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_catalog", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    catalog = json.loads(args.candidate_catalog.read_text(encoding="utf-8"))

    source_root = output_dir / "source"
    edited_root = output_dir / "edited"
    rows: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    preserved_edits = 0
    created_edits = 0

    for candidate in catalog["candidates"]:
        source = candidate["source"]
        source_path = source["path"]
        if source_path not in TARGETS:
            continue

        offset = source["offset"]
        completed_reason = COMPLETED_ELSEWHERE.get((source_path, offset))
        if completed_reason:
            excluded.append(
                {
                    "source_path": source_path,
                    "offset": offset,
                    "offset_hex": source["offset_hex"],
                    "reason": completed_reason,
                }
            )
            continue

        group = (
            "character_names"
            if source_path == "DATA3/O02.DAT"
            and offset in CHARACTER_NAME_OFFSETS
            else TARGETS[source_path]
        )
        source_png = project_root / candidate["source_png"]
        if not source_png.is_file():
            raise FileNotFoundError(source_png)

        output_name = source_png.name
        batch_source = source_root / group / output_name
        batch_edited = edited_root / group / output_name
        batch_source.parent.mkdir(parents=True, exist_ok=True)
        batch_edited.parent.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(source_png, batch_source)
        if batch_edited.exists():
            preserved_edits += 1
        else:
            shutil.copyfile(source_png, batch_edited)
            created_edits += 1

        source_hash = sha256(source_png)
        if sha256(batch_source) != source_hash:
            raise ValueError(f"source copy verification failed: {batch_source}")

        item_id = f"BBJ-UI-{source_path[6:9]}-{offset:08X}"
        rows.append(
            {
                "item_id": item_id,
                "group": group,
                "source_path": source_path,
                "offset": offset,
                "offset_hex": source["offset_hex"],
                "width": source["pixel_width"],
                "height": source["pixel_height"],
                "bpp": source["bpp"],
                "tim_total_size": source["tim_total_size"],
                "source_png_sha256": source_hash,
                "decoded_pixel_sha256": source["decoded_pixel_sha256"],
                "source_png": batch_source.relative_to(project_root).as_posix(),
                "edited_png": batch_edited.relative_to(project_root).as_posix(),
                "source_text": "",
                "korean_text": "",
                "status": "pending_edit",
                "runtime_reachability": candidate["runtime_reachability"],
                "notes": "Keep filename and canvas size unchanged.",
            }
        )

    rows.sort(key=lambda row: (str(row["source_path"]), int(row["offset"])))

    manifest = {
        "catalog_version": 1,
        "scope": "Remaining DATA3 UI graphics for manual Korean image editing",
        "baseline": catalog["baseline"],
        "status": "extracted_pending_edit",
        "item_count": len(rows),
        "groups": {
            group: sum(1 for row in rows if row["group"] == group)
            for group in sorted({str(row["group"]) for row in rows})
        },
        "excluded_completed_assets": excluded,
        "rules": [
            "Do not modify files under source.",
            "Edit only the matching files under edited.",
            "Keep each PNG filename, pixel dimensions, and canvas position unchanged.",
            "Do not add or remove palette-dependent transparency without a reinsertion test.",
        ],
        "items": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "edit_catalog.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    csv_fields = [
        "item_id",
        "group",
        "source_path",
        "offset_hex",
        "width",
        "height",
        "bpp",
        "source_text",
        "korean_text",
        "status",
        "source_png",
        "edited_png",
        "notes",
    ]
    with (output_dir / "edit_catalog.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"items={len(rows)}")
    print(f"created_edit_templates={created_edits}")
    print(f"preserved_edit_templates={preserved_edits}")
    for group, count in manifest["groups"].items():
        print(f"{group}={count}")
    print(f"excluded_completed={len(excluded)}")
    print(f"catalog={output_dir / 'edit_catalog.json'}")


if __name__ == "__main__":
    main()
