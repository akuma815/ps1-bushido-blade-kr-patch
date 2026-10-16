#!/usr/bin/env python3
"""Build an editable, stable catalog from the USA official subtitle timeline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def load_voice_catalog(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    result = {row["subtitle_id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate subtitle_id in {path}")
    return result


def mapping_key(source_path: str, start_frame: int, english_text: str) -> tuple[str, int, str]:
    return source_path, start_frame, english_text


def timeline_id(source_path: str, record_index: int) -> str:
    stem = source_path.lower().replace(".dat", "").replace("_", "-")
    return f"usa/{stem}/{record_index:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--analysis",
        type=Path,
        default=Path("work/analysis/usa_subtitle_comparison.json"),
    )
    parser.add_argument(
        "--voice-catalog",
        type=Path,
        default=Path("workspace/text/source/voice_catalog.csv"),
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path("workspace/config/usa_subtitle_voice_mapping.json"),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("workspace/text/source/usa_subtitle_timeline.csv"),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("work/analysis/usa_subtitle_timeline_catalog.json"),
    )
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    voice_rows = load_voice_catalog(args.voice_catalog)
    mapping_document = json.loads(args.mapping.read_text(encoding="utf-8"))
    mapping_rows: dict[tuple[str, int, str], dict] = {}
    for mapping in mapping_document["mappings"]:
        key = mapping_key(
            mapping["source_path"], mapping["start_frame"], mapping["english_text"]
        )
        if key in mapping_rows:
            raise ValueError(f"duplicate mapping key: {key}")
        unknown = [
            subtitle_id
            for subtitle_id in mapping["voice_subtitle_ids"]
            if subtitle_id not in voice_rows
        ]
        if unknown:
            raise ValueError(f"unknown voice subtitle ids for {key}: {unknown}")
        mapping_rows[key] = mapping

    fields = [
        "timeline_id",
        "scope",
        "canonical",
        "duplicate_of",
        "source_path",
        "record_index",
        "file_offset_hex",
        "usa_group",
        "start_frame",
        "end_frame",
        "duration_frames",
        "end_mode",
        "line_count",
        "english_text",
        "voice_subtitle_ids",
        "korean_translation",
        "mapping_status",
        "notes",
    ]
    catalog = []
    matched_mapping_keys = set()
    records_by_path = analysis["official_subtitle_records"]
    for source_path in sorted(records_by_path):
        if source_path.startswith("DATA7/"):
            scope = "story_battle"
        elif source_path.startswith("DATA3/"):
            scope = "story_cutscene"
        else:
            scope = "executable_ui"
        canonical = source_path != "DATA3/O20.DAT"
        duplicate_of = "DATA3/O14.DAT" if not canonical else ""
        for record_index, record in enumerate(records_by_path[source_path]):
            english_text = record.get("full_text", record["text"])
            key = mapping_key(source_path, record["start_frame"], english_text)
            mapping = mapping_rows.get(key)
            if mapping:
                matched_mapping_keys.add(key)
                voice_ids = mapping["voice_subtitle_ids"]
                korean = "\n".join(voice_rows[item]["korean_translation"] for item in voice_ids)
                mapping_status = mapping["mapping_status"]
                usa_group = mapping.get("usa_group", "")
                notes = mapping.get("notes", "")
            else:
                voice_ids = []
                korean = ""
                mapping_status = "unmapped"
                usa_group = ""
                notes = ""
            catalog.append(
                {
                    "timeline_id": timeline_id(source_path, record_index),
                    "scope": scope,
                    "canonical": "yes" if canonical else "no",
                    "duplicate_of": duplicate_of,
                    "source_path": source_path,
                    "record_index": record_index,
                    "file_offset_hex": f"0x{record['offset']:X}",
                    "usa_group": usa_group,
                    "start_frame": record["start_frame"],
                    "end_frame": "" if record["end_frame"] is None else record["end_frame"],
                    "duration_frames": ""
                    if record["duration_frames"] is None
                    else record["duration_frames"],
                    "end_mode": record["end_mode"],
                    "line_count": record["line_count"],
                    "english_text": english_text,
                    "voice_subtitle_ids": "|".join(voice_ids),
                    "korean_translation": korean,
                    "mapping_status": mapping_status,
                    "notes": notes,
                }
            )

    unused = sorted(set(mapping_rows) - matched_mapping_keys)
    if unused:
        raise ValueError(f"mapping entries did not match analysis records: {unused}")

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(catalog)

    canonical_rows = [row for row in catalog if row["canonical"] == "yes"]
    report = {
        "schema_version": 1,
        "inputs": {
            "analysis": str(args.analysis),
            "voice_catalog": str(args.voice_catalog),
            "mapping": str(args.mapping),
        },
        "output_csv": str(args.output_csv),
        "counts": {
            "all_records": len(catalog),
            "canonical_records": len(canonical_rows),
            "duplicate_records": len(catalog) - len(canonical_rows),
            "story_battle_records": sum(
                row["scope"] == "story_battle" and row["canonical"] == "yes"
                for row in catalog
            ),
            "mapped_records": sum(row["mapping_status"] != "unmapped" for row in catalog),
            "runtime_confirmed_records": sum(
                row["mapping_status"] == "runtime_confirmed" for row in catalog
            ),
            "records_with_explicit_end": sum(
                bool(row["end_frame"]) and row["canonical"] == "yes" for row in catalog
            ),
        },
        "runtime_confirmed": [
            row for row in catalog if row["mapping_status"] == "runtime_confirmed"
        ],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.output_csv} ({len(catalog)} rows)")
    print(f"Wrote {args.output_json}")


if __name__ == "__main__":
    main()
