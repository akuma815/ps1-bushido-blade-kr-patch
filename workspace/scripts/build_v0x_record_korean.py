#!/usr/bin/env python3
"""Build the complete DATA7/V00~V08 Korean record source.

The canonical translations come from usa_subtitle_timeline.csv.  Optional
reviewed line wrapping from usa_subtitle_overflow_review.csv takes precedence.
Token order and the two analyzer-synthesized ellipsis records come from
jp_subtitle_scene_schedule.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMELINE = ROOT / "workspace/text/source/usa_subtitle_timeline.csv"
DEFAULT_REVIEW = ROOT / "workspace/text/translation/usa_subtitle_overflow_review.csv"
DEFAULT_SCHEDULE = ROOT / "work/analysis/jp_subtitle_scene_schedule.json"
DEFAULT_OUTPUT = ROOT / "workspace/text/source/v0x_record_korean.json"
DEFAULT_REPORT = ROOT / "work/analysis/v0x_record_korean_build.json"

MAX_WIDTH_PX = 320
# 34 = col1 13 + col2 12 + digit 9 cells; 'Z' is excluded because its stock
# cell (954,464) breaks the contiguous column strip (HANDOFF section 21).
MAX_GLYPH_CELLS = 34
ADVANCE_PX = 16
DOT_ADVANCE_PX = 6
STOCK_CHARS = {".", "!", "?"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def split_translation(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    if not lines or any(not line for line in lines):
        raise ValueError(f"empty display line in translation: {text!r}")
    return lines


def split_review(text: str) -> list[str]:
    lines = [line.strip() for line in re.split(r"\s+/\s+", text.strip())]
    if not lines or any(not line for line in lines):
        raise ValueError(f"invalid reviewed line split: {text!r}")
    return lines


def normalize_line(line: str) -> str:
    return (
        line.replace("...", "…")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", "'")
        .replace("\u201d", "'")
        .replace("\u3000", " ")
    )


def line_width(line: str) -> int:
    width = 0
    for char in normalize_line(line):
        if char == "…":
            width += 3 * DOT_ADVANCE_PX
        elif char == ".":
            width += DOT_ADVANCE_PX
        else:
            width += ADVANCE_PX
    return width


def glyph_cell_count(records: list[list[str]]) -> int:
    custom: set[str] = set()
    needs_blank = False
    for lines in records:
        for line in lines:
            for char in normalize_line(line):
                if char == " ":
                    needs_blank = True
                elif char == "…" or char in STOCK_CHARS:
                    continue
                else:
                    custom.add(char)
    return len(custom) + int(needs_blank)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(path)


def json_sha256(payload: object) -> str:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", type=Path, default=DEFAULT_TIMELINE)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate inputs and compare the expected JSON without writing files",
    )
    args = parser.parse_args()

    timeline_rows = read_csv(args.timeline)
    canonical_battle: dict[str, list[str]] = {}
    for row in timeline_rows:
        if row.get("scope") != "story_battle" or row.get("canonical") != "yes":
            continue
        timeline_id = row.get("timeline_id", "").strip()
        translation = row.get("korean_translation", "")
        if not timeline_id or not translation.strip():
            raise ValueError(f"missing battle id/translation: {row!r}")
        if timeline_id in canonical_battle:
            raise ValueError(f"duplicate canonical timeline id: {timeline_id}")
        canonical_battle[timeline_id] = split_translation(translation)

    reviewed: dict[str, list[str]] = {}
    if args.review.exists():
        for row in read_csv(args.review):
            timeline_id = row.get("timeline_id", "").strip()
            text = row.get("reviewed_translation", "").strip()
            if not text:
                continue
            if timeline_id not in canonical_battle:
                raise ValueError(f"review id is not a canonical battle id: {timeline_id}")
            if timeline_id in reviewed:
                raise ValueError(f"duplicate review id: {timeline_id}")
            reviewed[timeline_id] = split_review(text)

    schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
    output: dict[str, object] = {
        "_comment": (
            "Generated by workspace/scripts/build_v0x_record_korean.py. "
            "Translations come from usa_subtitle_timeline.csv; reviewed line "
            "wrapping comes from usa_subtitle_overflow_review.csv; order follows "
            "jp_subtitle_scene_schedule.json. Each token is a list of display lines."
        )
    }
    used_timeline_ids: set[str] = set()
    synthesized = 0
    total_tokens = 0
    group_report: list[dict[str, object]] = []

    for file_entry in schedule["files"]:
        file_path = file_entry["path"]
        groups_out: dict[str, list[list[str]]] = {}
        for group in file_entry["groups"]:
            records: list[list[str]] = []
            for token in group.get("schedule", []):
                if not token.get("lines_en") and not token.get("lines_ko"):
                    continue
                timeline_id = (token.get("timeline_id") or "").strip()
                if timeline_id:
                    if timeline_id not in canonical_battle:
                        raise ValueError(f"schedule id missing from timeline CSV: {timeline_id}")
                    lines = reviewed.get(timeline_id, canonical_battle[timeline_id])
                    used_timeline_ids.add(timeline_id)
                else:
                    lines = [str(line).strip() for line in token.get("lines_ko") or []]
                    if not lines or any(not line for line in lines):
                        raise ValueError(
                            f"synthesized token lacks Korean text: {file_path} "
                            f"group {group['group']} frame {token.get('frame')}"
                        )
                    synthesized += 1

                for line in lines:
                    width = line_width(line)
                    if width > MAX_WIDTH_PX:
                        raise ValueError(
                            f"line exceeds {MAX_WIDTH_PX}px ({width}px): "
                            f"{timeline_id or file_path} {line!r}"
                        )
                records.append(lines)

            if not records:
                raise ValueError(f"no text records: {file_path} group {group['group']}")
            cells = glyph_cell_count(records)
            if cells > MAX_GLYPH_CELLS:
                raise ValueError(
                    f"{file_path} group {group['group']} exceeds glyph budget: "
                    f"{cells}/{MAX_GLYPH_CELLS}"
                )
            groups_out[str(group["group"])] = records
            total_tokens += len(records)
            group_report.append(
                {
                    "file": file_path,
                    "group": group["group"],
                    "records": len(records),
                    "glyph_cells": cells,
                }
            )
        output[file_path] = groups_out

    unused = sorted(set(canonical_battle) - used_timeline_ids)
    if unused:
        raise ValueError(f"canonical battle ids unused by schedule: {unused}")
    if len(used_timeline_ids) != 77 or synthesized != 2 or total_tokens != 79:
        raise ValueError(
            "unexpected inventory: "
            f"mapped={len(used_timeline_ids)}, synthesized={synthesized}, "
            f"tokens={total_tokens}"
        )

    expected_output_sha256 = json_sha256(output)
    current_output_sha256 = sha256(args.output) if args.output.exists() else None
    report = {
        "status": "passed",
        "inputs": {
            "timeline": str(args.timeline),
            "timeline_sha256": sha256(args.timeline),
            "review": str(args.review),
            "review_sha256": sha256(args.review),
            "schedule": str(args.schedule),
            "schedule_sha256": sha256(args.schedule),
        },
        "output": str(args.output),
        "output_sha256": expected_output_sha256,
        "current_output_sha256": current_output_sha256,
        "current_output_matches": current_output_sha256 == expected_output_sha256,
        "files": len(schedule["files"]),
        "groups": len(group_report),
        "mapped_timeline_records": len(used_timeline_ids),
        "synthesized_records": synthesized,
        "total_records": total_tokens,
        "review_overrides": len(reviewed),
        "max_glyph_cells": max(item["glyph_cells"] for item in group_report),
        "groups_detail": group_report,
    }
    if args.check_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    atomic_write_json(args.output, output)
    report["current_output_sha256"] = sha256(args.output)
    report["current_output_matches"] = (
        report["current_output_sha256"] == expected_output_sha256
    )
    atomic_write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
