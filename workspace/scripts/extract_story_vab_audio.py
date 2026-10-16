#!/usr/bin/env python3
"""Extract Bushido Blade story VAB tones as VAG and WAV catalog entries."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import struct
import wave
from pathlib import Path


VAB_MAGIC = b"pBAV"
VAB_BASE = 8
PROGRAM_TABLE_SIZE = 128 * 16
TONES_PER_PROGRAM = 16
TONE_SIZE = 32
VAG_SIZE_TABLE_ENTRIES = 256
SPU_BASE_RATE = 44_100
PREDICTORS = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))


def load_inspector(path: Path):
    spec = importlib.util.spec_from_file_location("inspect_psx_disc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inspector: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_vab(data: bytes) -> dict:
    if len(data) < VAB_BASE + 32 or data[VAB_BASE : VAB_BASE + 4] != VAB_MAGIC:
        raise ValueError("wrapped pBAV header not found")

    program_count = struct.unpack_from("<H", data, VAB_BASE + 18)[0]
    tone_count = struct.unpack_from("<H", data, VAB_BASE + 20)[0]
    vag_count = struct.unpack_from("<H", data, VAB_BASE + 22)[0]
    body_offset = struct.unpack_from("<I", data, 4)[0]
    calculated_body_offset = (
        VAB_BASE
        + 32
        + PROGRAM_TABLE_SIZE
        + program_count * TONES_PER_PROGRAM * TONE_SIZE
        + VAG_SIZE_TABLE_ENTRIES * 2
    )
    if body_offset != calculated_body_offset:
        raise ValueError(
            f"body offset mismatch: wrapper={body_offset:#x}, "
            f"calculated={calculated_body_offset:#x}"
        )

    size_table_offset = body_offset - VAG_SIZE_TABLE_ENTRIES * 2
    size_units = struct.unpack_from("<256H", data, size_table_offset)
    if size_units[0] != 0:
        raise ValueError(f"VAG size table sentinel is not zero: {size_units[0]}")
    sizes = [size_units[index] * 8 for index in range(1, vag_count + 1)]
    if body_offset + sum(sizes) != len(data):
        raise ValueError(
            f"VAG data size mismatch: body={len(data) - body_offset}, "
            f"table={sum(sizes)}"
        )

    samples = []
    cursor = body_offset
    for vag_index, size in enumerate(sizes, start=1):
        samples.append(
            {
                "vag_index": vag_index,
                "offset": cursor,
                "size": size,
                "data": data[cursor : cursor + size],
            }
        )
        cursor += size

    programs = []
    program_table_offset = VAB_BASE + 32
    tone_table_offset = program_table_offset + PROGRAM_TABLE_SIZE
    for program_index in range(program_count):
        program_offset = program_table_offset + program_index * 16
        tone_slots = data[program_offset]
        tones = []
        for tone_index in range(tone_slots):
            tone_offset = (
                tone_table_offset
                + (program_index * TONES_PER_PROGRAM + tone_index) * TONE_SIZE
            )
            tone = data[tone_offset : tone_offset + TONE_SIZE]
            vag_index = struct.unpack_from("<H", tone, 22)[0]
            if not 1 <= vag_index <= vag_count:
                raise ValueError(
                    f"invalid VAG reference p{program_index} t{tone_index}: {vag_index}"
                )
            tones.append(
                {
                    "tone_index": tone_index,
                    "priority": tone[0],
                    "mode": tone[1],
                    "volume": tone[2],
                    "pan": tone[3],
                    "center_note": tone[4],
                    "fine_shift": tone[5],
                    "note_min": tone[6],
                    "note_max": tone[7],
                    "adsr1": struct.unpack_from("<H", tone, 16)[0],
                    "adsr2": struct.unpack_from("<H", tone, 18)[0],
                    "parent_program": struct.unpack_from("<H", tone, 20)[0],
                    "vag_index": vag_index,
                }
            )
        programs.append(
            {
                "program_index": program_index,
                "tone_slots": tone_slots,
                "tones": tones,
            }
        )

    return {
        "program_count": program_count,
        "tone_count_header": tone_count,
        "vag_count": vag_count,
        "body_offset": body_offset,
        "programs": programs,
        "samples": samples,
    }


def clamp_int16(value: int) -> int:
    return max(-32768, min(32767, value))


def decode_spu_adpcm(data: bytes) -> tuple[list[int], dict]:
    if len(data) % 16:
        raise ValueError(f"SPU ADPCM size is not 16-byte aligned: {len(data)}")

    pcm = []
    previous_1 = 0
    previous_2 = 0
    end_frame = None
    loop_start_frames = []
    loop_repeat_frames = []

    for frame_index in range(len(data) // 16):
        frame = data[frame_index * 16 : (frame_index + 1) * 16]
        shift = frame[0] & 0x0F
        predictor = frame[0] >> 4
        flags = frame[1]
        if predictor >= len(PREDICTORS):
            raise ValueError(
                f"unsupported SPU ADPCM predictor {predictor} at frame {frame_index}"
            )
        coefficient_1, coefficient_2 = PREDICTORS[predictor]

        for packed in frame[2:]:
            for nibble in (packed & 0x0F, packed >> 4):
                signed = nibble - 16 if nibble >= 8 else nibble
                sample = (signed << 12) >> shift
                sample += (
                    previous_1 * coefficient_1
                    + previous_2 * coefficient_2
                    + 32
                ) >> 6
                sample = clamp_int16(sample)
                pcm.append(sample)
                previous_2 = previous_1
                previous_1 = sample

        if flags & 0x04:
            loop_start_frames.append(frame_index)
        if flags & 0x02:
            loop_repeat_frames.append(frame_index)
        if flags & 0x01 and end_frame is None:
            end_frame = frame_index
            break

    decoded_frames = len(pcm) // 28
    return pcm, {
        "source_frame_count": len(data) // 16,
        "decoded_frame_count": decoded_frames,
        "end_flag_frame": end_frame,
        "trailing_frames_after_end": (
            len(data) // 16 - decoded_frames if end_frame is not None else 0
        ),
        "loop_start_frames": loop_start_frames,
        "loop_repeat_frames": loop_repeat_frames,
    }


def estimated_rate(center_note: int, fine_shift: int, trigger_note: int) -> int:
    semitones = trigger_note - center_note - fine_shift / 128.0
    return max(1, round(SPU_BASE_RATE * math.pow(2.0, semitones / 12.0)))


def write_wav(path: Path, pcm: list[int], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = struct.pack(f"<{len(pcm)}h", *pcm)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(payload)


def write_vag(path: Path, adpcm: bytes, sample_rate: int, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded_name = name.encode("ascii", errors="strict")[:16].ljust(16, b"\x00")
    header = b"".join(
        (
            b"VAGp",
            struct.pack(">I", 0x20),
            struct.pack(">I", 0),
            struct.pack(">I", len(adpcm)),
            struct.pack(">I", sample_rate),
            b"\x00" * 12,
            encoded_name,
        )
    )
    if len(header) != 48:
        raise AssertionError(f"unexpected VAG header size: {len(header)}")
    path.write_bytes(header + adpcm)


def bank_parts(path: str) -> tuple[str, str, str, str]:
    group, filename = path.split("/", 1)
    stem, event = filename.rsplit(".", 1)
    speaker = stem[:3]
    return group, stem, event, speaker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--inspector", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--trigger-note", type=int, default=60)
    args = parser.parse_args()

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"output directory is not empty: {args.output_dir}")
    if not 0 <= args.trigger_note <= 127:
        parser.error("--trigger-note must be between 0 and 127")

    inspector = load_inspector(args.inspector)
    disc = inspector.inspect(args.image)
    entries = [
        entry
        for entry in disc["entries"]
        if not entry["is_dir"]
        and entry["path"].split("/", 1)[0].upper() in {"SE3", "SE4"}
    ]
    entries.sort(key=lambda entry: entry["path"])

    rows = []
    banks_report = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with args.image.open("rb") as track:
        for entry in entries:
            data, sector_forms = inspector.read_extent(
                track, entry["lba"], entry["size"]
            )
            if data[VAB_BASE : VAB_BASE + 4] != VAB_MAGIC:
                continue
            vab = parse_vab(data)
            sample_map = {
                sample["vag_index"]: sample for sample in vab["samples"]
            }
            group, stem, event, speaker = bank_parts(entry["path"])
            bank_rows = []

            for program in vab["programs"]:
                for tone in program["tones"]:
                    sample = sample_map[tone["vag_index"]]
                    pcm, decode_info = decode_spu_adpcm(sample["data"])
                    rate = estimated_rate(
                        tone["center_note"], tone["fine_shift"], args.trigger_note
                    )
                    file_id = (
                        f"{stem}_{event}_p{program['program_index']:02d}_"
                        f"t{tone['tone_index']:02d}_v{tone['vag_index']:02d}"
                    )
                    relative_wav = Path("wav_note60") / group / f"{file_id}.wav"
                    relative_wav_44100 = (
                        Path("wav_spu44100") / group / f"{file_id}.wav"
                    )
                    relative_vag = Path("vag_note60") / group / f"{file_id}.vag"
                    write_wav(args.output_dir / relative_wav, pcm, rate)
                    write_wav(
                        args.output_dir / relative_wav_44100, pcm, SPU_BASE_RATE
                    )
                    write_vag(
                        args.output_dir / relative_vag,
                        sample["data"],
                        rate,
                        file_id,
                    )

                    subtitle_id = (
                        f"voice/{group.lower()}/{stem.lower()}/{event}/"
                        f"p{program['program_index']:02d}/"
                        f"t{tone['tone_index']:02d}/v{tone['vag_index']:02d}"
                    )
                    row = {
                        "subtitle_id": subtitle_id,
                        "bank_path": entry["path"],
                        "bank_lba": entry["lba"],
                        "bank_file_size": entry["size"],
                        "event_number": event,
                        "speaker_code": speaker,
                        "program_index": program["program_index"],
                        "tone_index": tone["tone_index"],
                        "vag_index": tone["vag_index"],
                        "vag_file_offset": sample["offset"],
                        "vag_size": sample["size"],
                        "adpcm_sha256": hashlib.sha256(sample["data"]).hexdigest(),
                        "center_note": tone["center_note"],
                        "fine_shift": tone["fine_shift"],
                        "assumed_trigger_note": args.trigger_note,
                        "estimated_sample_rate_hz": rate,
                        "decoded_sample_count": len(pcm),
                        "estimated_duration_ms": round(len(pcm) * 1000 / rate),
                        "wav_note60_path": relative_wav.as_posix(),
                        "wav_spu44100_path": relative_wav_44100.as_posix(),
                        "vag_note60_path": relative_vag.as_posix(),
                        "japanese_transcript": "",
                        "korean_translation": "",
                        "status": "untranscribed",
                        "notes": "",
                    }
                    rows.append(row)
                    bank_rows.append(
                        {
                            **{key: value for key, value in row.items() if key not in {
                                "japanese_transcript",
                                "korean_translation",
                                "status",
                                "notes",
                            }},
                            "decode": decode_info,
                        }
                    )

            banks_report.append(
                {
                    "path": entry["path"],
                    "lba": entry["lba"],
                    "file_size": entry["size"],
                    "sector_forms": sorted(sector_forms),
                    "program_count": vab["program_count"],
                    "tone_count_header": vab["tone_count_header"],
                    "vag_count": vab["vag_count"],
                    "body_offset": vab["body_offset"],
                    "catalog_rows": bank_rows,
                }
            )

    fieldnames = list(rows[0].keys()) if rows else []
    args.catalog.parent.mkdir(parents=True, exist_ok=True)
    with args.catalog.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "image": disc["image"],
        "volume_id": disc["volume_id"],
        "scope": ["SE3", "SE4"],
        "spu_base_rate_hz": SPU_BASE_RATE,
        "estimated_rate_assumption": {
            "trigger_note": args.trigger_note,
            "trigger_fine": 0,
            "formula": "44100 * 2^((trigger_note-center-fine_shift/128)/12)",
            "runtime_verified": False,
        },
        "bank_count": len(banks_report),
        "catalog_row_count": len(rows),
        "unique_adpcm_count": len({row["adpcm_sha256"] for row in rows}),
        "banks": banks_report,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"bank_count={report['bank_count']}")
    print(f"catalog_row_count={report['catalog_row_count']}")
    print(f"unique_adpcm_count={report['unique_adpcm_count']}")
    print(f"catalog={args.catalog}")
    print(f"report={args.report}")
    print(f"audio={args.output_dir}")


if __name__ == "__main__":
    main()
