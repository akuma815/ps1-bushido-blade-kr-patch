#!/usr/bin/env python3
"""Scan small files in a raw PS1 ISO for likely Shift-JIS text runs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import unicodedata
from pathlib import Path


def load_inspector(path: Path):
    spec = importlib.util.spec_from_file_location("inspect_psx_disc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unit_length(data: bytes, offset: int) -> int:
    value = data[offset]
    if 0x20 <= value <= 0x7E or 0xA1 <= value <= 0xDF:
        return 1
    if (0x81 <= value <= 0x9F or 0xE0 <= value <= 0xEF) and offset + 1 < len(data):
        trail = data[offset + 1]
        if 0x40 <= trail <= 0xFC and trail != 0x7F:
            return 2
    return 0


def japanese_count(text: str) -> int:
    count = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xFF61 <= codepoint <= 0xFF9F
        ):
            count += 1
    return count


def kana_count(text: str) -> int:
    return sum(
        1
        for char in text
        if 0x3040 <= ord(char) <= 0x30FF or 0xFF61 <= ord(char) <= 0xFF9F
    )


def clean_text(text: str) -> str:
    return "".join(
        char if unicodedata.category(char)[0] != "C" else " " for char in text
    ).strip()


def scan(data: bytes, minimum_japanese: int, minimum_kana: int) -> list[dict]:
    results = []
    offset = 0
    while offset < len(data):
        start = offset
        units = 0
        while offset < len(data):
            length = unit_length(data, offset)
            if not length:
                break
            offset += length
            units += 1
        if offset > start:
            raw = data[start:offset]
            try:
                text = clean_text(raw.decode("shift_jis"))
            except UnicodeDecodeError:
                text = ""
            jp_count = japanese_count(text)
            k_count = kana_count(text)
            if (
                units >= 3
                and len(raw) <= 192
                and jp_count >= minimum_japanese
                and k_count >= minimum_kana
                and jp_count / max(1, len(text)) >= 0.4
            ):
                results.append(
                    {
                        "offset": start,
                        "byte_length": len(raw),
                        "japanese_chars": jp_count,
                        "kana_chars": k_count,
                        "text": text[:240],
                    }
                )
        offset = max(offset + 1, start + 1)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--inspector", required=True, type=Path)
    parser.add_argument("--max-file-size", type=int, default=1_000_000)
    parser.add_argument("--minimum-japanese", type=int, default=3)
    parser.add_argument("--minimum-kana", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inspector = load_inspector(args.inspector)
    disc = inspector.inspect(args.image)
    report = {
        "image": disc["image"],
        "volume_id": disc["volume_id"],
        "settings": {
            "max_file_size": args.max_file_size,
            "minimum_japanese": args.minimum_japanese,
            "minimum_kana": args.minimum_kana,
        },
        "files": [],
    }
    with args.image.open("rb") as track:
        for entry in disc["entries"]:
            if entry["is_dir"] or entry["size"] > args.max_file_size:
                continue
            data, sector_forms = inspector.read_extent(
                track, entry["lba"], entry["size"]
            )
            if "form2" in sector_forms:
                continue
            candidates = scan(data, args.minimum_japanese, args.minimum_kana)
            if candidates:
                report["files"].append(
                    {
                        "path": entry["path"],
                        "size": entry["size"],
                        "candidate_count": len(candidates),
                        "candidates": candidates,
                    }
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"files_with_candidates={len(report['files'])}")
    print(f"candidate_runs={sum(item['candidate_count'] for item in report['files'])}")
    print(args.output)


if __name__ == "__main__":
    main()
