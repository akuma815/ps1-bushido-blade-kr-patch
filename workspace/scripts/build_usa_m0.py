#!/usr/bin/env python3
"""USA milestone 0: prove in-place subtitle text replacement.

Replaces V00 record 0's English line ("Nothing personal, babushka.", shown when
winning the first Tessa scene) with cell-code letters, entirely in place -- no
relocation, no atlas, no wrapper.  If the win scene then shows those letters in
the stock ASCII font, the drawer tolerates our replacement and the token offset
table (not string-walking) drives scheduling, validating the whole data plan
(HANDOFF_USA 2.5/2.9).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc
from build_subtitle_poc import patch_logical_sector

USA_BIN_SHA_HEAD = None  # single-source disc; keep simple for a probe build

TARGET_FILE = "DATA7/V00.DAT"
RECORD_OFFSET = 0x35F40          # "00301Nothing personal, babushka.\x00"
OLD = b"00301Nothing personal, babushka.\x00"
# same span: header keeps frame 0030 / 1 line; text becomes cell codes
NEW_TEXT = b"ACEGIKMOQS"
NEW = (b"00301" + NEW_TEXT + b"\x00").ljust(len(OLD), b"\x00")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, nargs="?",
                        default=Path("original/Bushido Blade (USA).bin"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("build/usa_m0"))
    args = parser.parse_args()

    assert len(NEW) == len(OLD)
    info = disc.inspect(args.image)
    entry = next(e for e in info["entries"] if e["path"] == TARGET_FILE)
    with args.image.open("rb") as f:
        data, forms = disc.read_extent(f, entry["lba"], entry["size"])
    assert forms == {"form1"}
    actual = data[RECORD_OFFSET:RECORD_OFFSET + len(OLD)]
    if actual != OLD:
        raise SystemExit(f"anchor mismatch: {actual!r}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "Bushido Blade (USA) [KO m0].bin"
    shutil.copyfile(args.image, out)

    lba = entry["lba"] + RECORD_OFFSET // 2048
    inner = RECORD_OFFSET % 2048
    if inner + len(NEW) > 2048:
        raise SystemExit("record crosses a sector boundary; extend the patcher")

    def apply(raw: bytearray) -> None:
        base = disc.MODE2_FORM1_DATA_OFFSET
        assert raw[base + inner:base + inner + len(OLD)] == OLD
        raw[base + inner:base + inner + len(NEW)] = NEW

    patch_logical_sector(out, lba, apply)
    (args.output_dir / out.name.replace(".bin", ".cue")).write_text(
        f'FILE "{out.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii",
    )
    print(f"patched record 0 -> {NEW_TEXT.decode()} @V00+0x{RECORD_OFFSET:X}")
    print(f"output: {out}")


if __name__ == "__main__":
    main()
