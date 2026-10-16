#!/usr/bin/env python3
"""Search every logical file in a raw PS1 image for exact encoded terms."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def load_inspector(path: Path):
    spec = importlib.util.spec_from_file_location("inspect_psx_disc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inspector: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    parser.add_argument("--inspector", required=True, type=Path)
    parser.add_argument("--term", action="append", required=True)
    parser.add_argument("--encoding", default="shift_jis")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    inspector = load_inspector(args.inspector)
    disc = inspector.inspect(args.image)
    encoded_terms = [
        (term, term.encode(args.encoding, errors="strict")) for term in args.term
    ]
    matches = []

    with args.image.open("rb") as track:
        for entry in disc["entries"]:
            if entry["is_dir"]:
                continue
            data, sector_forms = inspector.read_extent(
                track, entry["lba"], entry["size"]
            )
            for term, pattern in encoded_terms:
                for offset in all_offsets(data, pattern):
                    context_start = max(0, offset - 24)
                    context_end = min(len(data), offset + len(pattern) + 24)
                    context = data[context_start:context_end]
                    matches.append(
                        {
                            "term": term,
                            "encoded_hex": pattern.hex(),
                            "path": entry["path"],
                            "file_lba": entry["lba"],
                            "file_size": entry["size"],
                            "sector_forms": sorted(sector_forms),
                            "file_offset": offset,
                            "context_start": context_start,
                            "context_hex": context.hex(),
                            "context_decoded_lossy": context.decode(
                                args.encoding, errors="replace"
                            ),
                        }
                    )

    report = {
        "image": disc["image"],
        "volume_id": disc["volume_id"],
        "encoding": args.encoding,
        "terms": [
            {"text": term, "encoded_hex": pattern.hex()}
            for term, pattern in encoded_terms
        ],
        "files_scanned": sum(1 for entry in disc["entries"] if not entry["is_dir"]),
        "match_count": len(matches),
        "matches": matches,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"files_scanned={report['files_scanned']}")
    print(f"match_count={report['match_count']}")
    print(args.output)


if __name__ == "__main__":
    main()
