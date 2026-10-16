#!/usr/bin/env python3
"""Render 8192-byte raw PS1 font candidates in plausible fixed-cell layouts."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def render_16x16_1bpp(data: bytes, lsb_first: bool) -> Image.Image:
    image = Image.new("L", (16 * 16, 16 * 16), 0)
    pixels = image.load()
    for glyph in range(256):
        base = glyph * 32
        gx = (glyph % 16) * 16
        gy = (glyph // 16) * 16
        for y in range(16):
            row = data[base + y * 2 : base + y * 2 + 2]
            for x in range(16):
                byte = row[x // 8]
                bit = (byte >> (x % 8 if lsb_first else 7 - (x % 8))) & 1
                pixels[gx + x, gy + y] = 255 * bit
    return image


def render_8x8_4bpp(data: bytes, low_nibble_first: bool) -> Image.Image:
    image = Image.new("L", (16 * 8, 16 * 8), 0)
    pixels = image.load()
    for glyph in range(256):
        base = glyph * 32
        gx = (glyph % 16) * 8
        gy = (glyph // 16) * 8
        for y in range(8):
            row = data[base + y * 4 : base + y * 4 + 4]
            for x in range(8):
                byte = row[x // 2]
                if (x % 2 == 0) == low_nibble_first:
                    value = byte & 0x0F
                else:
                    value = byte >> 4
                pixels[gx + x, gy + y] = value * 17
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for source in args.inputs:
        data = source.read_bytes()
        if len(data) != 8192:
            raise ValueError(f"{source} is {len(data)} bytes; expected 8192")
        variants = {
            "16x16_1bpp_msb": render_16x16_1bpp(data, False),
            "16x16_1bpp_lsb": render_16x16_1bpp(data, True),
            "8x8_4bpp_low": render_8x8_4bpp(data, True),
            "8x8_4bpp_high": render_8x8_4bpp(data, False),
        }
        for suffix, image in variants.items():
            output = args.output_dir / f"{source.stem}_{suffix}.png"
            image.resize((image.width * 3, image.height * 3), Image.Resampling.NEAREST).save(
                output
            )
            print(output)


if __name__ == "__main__":
    main()
