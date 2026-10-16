#!/usr/bin/env python3
"""Create a flat, numbered listening set from the story voice catalog."""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--audio-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    with args.catalog.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 106:
        raise ValueError(f"expected 106 catalog rows, got {len(rows)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    expected_names: set[str] = set()
    playlist: list[str] = ["#EXTM3U"]
    index_lines = [
        "# 부시도 블레이드 스토리 음성 청취 목록",
        "",
        "`review_no`를 기준으로 대사 여부를 지정한다. 예: `대사: 003, 017, 018`.",
        "원본 좌표는 `subtitle_id`로 보존되며 이 문서의 행 순서는 고정한다.",
        "",
        "| 번호 | 파일 | 화자 코드 | 길이(ms) | subtitle_id |",
        "| ---: | --- | --- | ---: | --- |",
    ]

    for number, row in enumerate(rows, start=1):
        relative = Path(row["wav_note60_path"])
        source_wav = args.audio_root / relative
        if not source_wav.is_file():
            raise FileNotFoundError(source_wav)
        flat_name = f"{number:03d}_{source_wav.name}"
        destination = args.output_dir / flat_name
        expected_names.add(flat_name)
        if not destination.exists() or destination.read_bytes() != source_wav.read_bytes():
            shutil.copyfile(source_wav, destination)
        duration = int(row["estimated_duration_ms"])
        playlist.extend([f"#EXTINF:{duration / 1000:.3f},{number:03d} {row['subtitle_id']}", flat_name])
        index_lines.append(
            f"| {number:03d} | `{flat_name}` | {row['speaker_code']} | "
            f"{duration} | `{row['subtitle_id']}` |"
        )

    unexpected = [
        path.name
        for path in args.output_dir.glob("*.wav")
        if path.name not in expected_names
    ]
    if unexpected:
        raise ValueError(f"unexpected WAV files already in review folder: {unexpected}")

    (args.output_dir / "playlist.m3u8").write_text(
        "\n".join(playlist) + "\n", encoding="utf-8-sig", newline="\n"
    )
    (args.output_dir / "INDEX.md").write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"review_wavs={len(expected_names)}")
    print(f"output_dir={args.output_dir}")


if __name__ == "__main__":
    main()
