#!/usr/bin/env python3
"""USA Korean subtitle builder -- stage B4 (token-pointer keyed lookup).

Stage A (unchanged): replaces every translated subtitle token in place with
cell-coded Korean text and appends a per-file glyph-atlas section, relocating
grown files to the image end (JP v37 pipeline).

Stage B4 rewrites the cave wrapper after the b3 diagnosis (HANDOFF 2.14-2.17):
the drawer has TWO record sources (EXE-internal token table and file buffers),
so the loader-destination global [0x8013D0EC] is not a usable key.  The new
wrapper keys on the current token pointer [[gp+0x424]+0], which is unique
regardless of source:

  1. EXE path: token address matches an entry planted in the boot-copied pool
     (EXE tokens are static, addresses known at build time).
  2. File path: derive the buffer base as (T - token_offset) per candidate
     record and verify a two-word file fingerprint (words at +0xC/+0x10).
     No dependence on loader state.
  3. Fail-safe: no match -> call the original drawer UNPATCHED (b2's missing
     stock uppercase came from patching without a matched upload).

EXE-internal subtitles (19 records, HANDOFF 1.4/2.17-2) are replaced in place
and served from a glyph pool: unique glyphs, uploaded per-glyph at record
start.  The pool plus all wrapper data lives at POOL_BASE (high RAM, below
the stack); it is carried as an extension of the EXE image (185-sector disc
gap follows the EXE, so no relocation) and copied there once at boot by an
entry-point stub, which then zeroes the extension region so BSS stays
pristine and jumps to the original entry.

Section format appended to each subtitle-bearing file:
  u32 magic 'K1US'
  u32 path_tag = crc32(source_path)
  u32 record_count
  entries[record_count]:
    u32 token_offset      -- offset of the token inside the original file;
                             unique per file (frame numbers are NOT unique)
    u32 col1_off, col2_off, dig_off, clut_off   -- 0 if the block is unused
  pixel blobs (offsets relative to section start)
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspect_psx_disc as disc
from build_subtitle_poc import patch_logical_sector
from build_subtitle_v37 import (
    COL1_CHARS, COL2_CHARS, DIG_CHARS,
    GroupLayout,
    make_raw_sector,
    msf_word,
)
from build_options_poc import rebuild_mode2_form1_integrity
from build_subtitle_poc import assemble
from build_subtitle_v37 import (
    RECT_COL1, RECT_COL2, RECT_DIG,
    WIDTH_PATCH_COUNT, WIDTH_PATCH_FIRST,
    assert_no_load_delay_hazard,
)
from build_subtitle_scene_poc_v36 import STOCK_TEMPLATE_CLUT
import build_subtitle_scene_poc_v36 as _v36_mod
import build_subtitle_v37 as _v37_mod

# ------------------------------------------------------------------ USA fixed
EXE_RAM_BASE = 0x80010000
EXE_HEADER_SIZE = 0x800
CAVE_START = 0x80011E34          # byte-identical twin of the proven JP cave
CAVE_END = 0x80012305
DRAWER = 0x8003C5A8
DRAWER_CALLERS = (0x80045700, 0x80038DA4, 0x8003989C, 0x80064B00)
DRAWER_JAL = 0x0C00F16A          # jal 0x8003C5A8 (verified at all four sites)
USA_LOAD_IMAGE = 0x8006780C      # JP 0x80069024 - 0x1818 (prologue-verified)
USA_TEMPLATE = 0x80083774        # byte-identical to JP 0x800858AC
USA_WIDTH = 0x80083D74
DIGIT_STOCK_CLUT = 0x7678
CLUT_RECT = (1008, 112, 16, 1)   # free 16px row in BOTH usa VRAM dumps
GLYPH_BYTES = 128
# The font renders with no anti-aliasing (fill=6, stroke_fill=1), so every
# glyph pixel is one of three CLUT indices -- measured over all 106 glyphs of
# c6: {0: 13153, 1: 8173, 6: 5810}.  Two bits per pixel is therefore lossless,
# halving the pool's dominant cost (128 B -> 64 B per glyph, 6.8 KB saved),
# which is what finally makes the nameplate glyphs fit inside the verified
# 0x801FB000..0x801FF000 band.  A 512 B LUT expands a packed byte (4 pixels)
# straight into the 4bpp halfword the GPU wants.
GLYPH_CODES = (0, 1, 6)
GLYPH_PACKED_BYTES = 64

# Pool: every wrapper data structure + EXE glyph atlas, copied here at boot.
# 0x801FB000..0x801FF000 was zero in ALL JP RAM dumps (movie/ending/gallery);
# the USA engine is the same family and uses the same stack top (0x801FFFF0),
# but this band is NOT yet runtime-verified on USA -- see build report risks.
# c1..c3 lowered POOL_BASE to 0x801FA000 to make room for the nameplate
# glyphs; that put the tables in the 4 KB *below* the band JP dumps proved
# zero, and every c-series symptom (c1 hang with the stub at the pool head,
# c3 nameplate lookup finding no match while the table/stub were statically
# perfect) is explained by that window not being free.  Stay inside the
# verified band.  c4 tried borrowing the extra kilobyte from the stack margin
# (POOL_LIMIT 0x801FF400) and the character-select screen came up as one giant
# corrupt quad -- the stub landed at 0x801FF1D8, inside stack territory.  The
# safe window really is exactly 0x801FB000..0x801FF000, so the nameplate
# glyphs have to be paid for out of the pool, not out of the margins.
POOL_BASE = 0x801FB000
POOL_LIMIT = 0x801FF000
UP_STUB_SIZE = 0x100             # shared 2bpp glyph uploader (pool tail)
NAME_STUB_SIZE = 0x280           # code slot at the pool tail (see build_pool)
EXT_RAM = 0x800A1000             # extension load address (= old EXE image end)
EXT_STUB_SIZE = 0x80             # boot copy stub reserved bytes

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "workspace" / "text" / "source" / "usa_subtitle_timeline.csv"
COMPARISON = ROOT / "work" / "analysis" / "usa_subtitle_comparison.json"

SECTION_MAGIC = int.from_bytes(b"K1US", "little")


def section_tag(path: str) -> int:
    """Stable file-unique second word for K1US candidate validation."""
    return zlib.crc32(path.encode("ascii")) & 0xFFFFFFFF

TARGET_FILES = [
    "DATA7/V00.DAT", "DATA7/V01.DAT", "DATA7/V02.DAT", "DATA7/V03.DAT",
    "DATA7/V04.DAT", "DATA7/V05.DAT", "DATA7/V06.DAT", "DATA7/V07.DAT",
    "DATA7/V08.DAT", "DATA3/O05.DAT", "DATA3/O14.DAT",
]
EXE_SCOPE = "SCUS_941.80"

# Character-select nameplates: drawn by calling the text renderer directly
# (0x8003294C and 0x80032BB4, $a0 = [0x80080F18 + index]), not through the
# subtitle drawer -- so they need their own thin stub.  Strings are EXE-
# resident with a "#X#Y#F" format prefix; the byte budget is the span up to
# the next string minus prefix and terminator.
USA_TEXT_RENDERER = 0x80036EF4
NAME_RENDER_SITES = (0x8003294C, 0x80032BB4)
NAME_RENDER_JAL = 0x0C00DBBD                      # jal 0x80036EF4
NAME_STRINGS = {
    0x80010EA8: ("KANNUKI", "칸누키", 16),
    0x80010EB8: ("UTSUSEMI", "우츠세미", 16),
    0x80010EC8: ("BLACK LOTUS", "블랙 로터스", 20),
    0x80010EDC: ("MIKADO", "미카도", 16),
    0x80010EEC: ("TATSUMI", "타츠미", 16),
    0x80010EFC: ("RED SHADOW", "호타루비", 20),
}
NAME_PREFIX = b"#X#Y#F"
# `#F` is not decoration: its handler (0x800371D8) reads the next vararg and,
# when it is 1 -- which is exactly what both call sites pass in $a3 -- sets the
# font base [gp+0x7c] to 0x39.  Every glyph lookup in the per-char drawer
# (0x8003727C) is then indexed (charindex + 57), so the nameplates read
# template/width entries 70..94 while c1..d1 were patching 13..37.  That is why
# d1 fixed the corruption but still drew stock letters.  Font 1 also carries a
# different CLUT (0x2BBF vs 0x7838), so the patch has to set +6 as well.
NAME_FONT_BASE = 57
NAME_CELL_FIRST = 13             # cell 'A'
NAME_CELL_COUNT = 25             # cells 'A'..'Y' -> font-1 entries 70..94
# Font 1 carries its own CLUT and it is already the palette our glyphs are
# rendered against -- VRAM (1008,174) at the character-select screen reads
# 0:transparent 1:black 2..5:grays 6:white, i.e. exactly the {0,1,6} the font
# renderer emits.  d2 wrote font 0's 0x7838 here instead; at this screen that
# VRAM slot holds unrelated data whose entry 0 is cyan, which is where the
# background box and the wrong glyph colour came from.
NAME_CLUT = 0x2BBF


def pack_glyph_2bpp(pixels: bytes) -> bytes:
    """4bpp 16x16 (128 B) -> 2bpp (64 B).  Nibble order is preserved: the low
    nibble of a 4bpp byte is the left pixel, so 2bpp bit pairs run 0,2,4,6."""
    code = {v: i for i, v in enumerate(GLYPH_CODES)}
    flat = []
    for byte in pixels:
        flat.append(code[byte & 0xF])
        flat.append(code[byte >> 4])
    out = bytearray()
    for i in range(0, len(flat), 4):
        p0, p1, p2, p3 = flat[i:i + 4]
        out.append(p0 | (p1 << 2) | (p2 << 4) | (p3 << 6))
    return bytes(out)


def glyph_lut() -> bytes:
    """LUT[packed byte] = the two 4bpp bytes it expands to, little-endian."""
    out = bytearray()
    for b in range(256):
        px = [GLYPH_CODES[(b >> s) & 3] if (b >> s) & 3 < len(GLYPH_CODES)
              else 0 for s in (0, 2, 4, 6)]
        out.append(px[0] | (px[1] << 4))
        out.append(px[2] | (px[3] << 4))
    return bytes(out)


def build_upload_stub(pool: dict) -> bytes:
    """Shared glyph uploader: $a0 = packed glyph, $a1 = band slot.

    Expands 64 packed bytes into the 128 B scratch through the LUT, points the
    scratch rect at the slot, then transfers it.

    The transfer is bracketed by bounded waits on DMA channel 2's busy bit
    (D2_CHCR 0x1F8010A8 bit 24).  LoadImage is asynchronous -- it kicks a DMA
    and returns -- so calling it from inside the character-select nameplate
    render path collided with the chain the game already had in flight and
    the portrait came back as one giant garbage quad (c4/c7; the c8/c9 A/B
    pinned it to the upload, not the template patch).  The subtitle wrapper
    never hit this because its last-token guard uploads only when a new line
    appears, not every frame.  The counters are bounded so a stuck bit
    degrades to a dropped glyph instead of a hang."""
    sp = {k: split_address(v) for k, v in pool.items()}
    source = f"""
    lui $t0, {sp['gscratch'][0]}
    ori $t0, $t0, {sp['gscratch'][1]}
    lui $t7, {sp['glyph_lut'][0]}
    ori $t7, $t7, {sp['glyph_lut'][1]}
    ori $t1, $zero, {GLYPH_PACKED_BYTES}
up_loop:
    lbu $t2, 0($a0)
    nop
    sll $t2, $t2, 1
    addu $t2, $t7, $t2
    lhu $t3, 0($t2)
    nop
    sh $t3, 0($t0)
    addiu $a0, $a0, 1
    addiu $t0, $t0, 2
    addiu $t1, $t1, -1
    bne $t1, $zero, up_loop
    nop
    sll $t4, $a1, 2
    addiu $t4, $t4, 960
    lui $a0, {sp['scratch_rect'][0]}
    ori $a0, $a0, {sp['scratch_rect'][1]}
    sh $t4, 0($a0)
    lui $a1, {sp['gscratch'][0]}
    ori $a1, $a1, {sp['gscratch'][1]}
    lui $t5, 0x1F80
    lui $t6, 0x10
up_w1:
    lw $t2, 0x10A8($t5)
    nop
    srl $t2, $t2, 24
    andi $t2, $t2, 1
    beq $t2, $zero, up_go
    nop
    addiu $t6, $t6, -1
    bne $t6, $zero, up_w1
    nop
up_go:
    addiu $sp, $sp, -8
    sw $ra, 4($sp)
    jal 0x{USA_LOAD_IMAGE:08x}
    nop
    lw $ra, 4($sp)
    addiu $sp, $sp, 8
    lui $t5, 0x1F80
    lui $t6, 0x10
up_w2:
    lw $t2, 0x10A8($t5)
    nop
    srl $t2, $t2, 24
    andi $t2, $t2, 1
    beq $t2, $zero, up_done
    nop
    addiu $t6, $t6, -1
    bne $t6, $zero, up_w2
    nop
up_done:
    jr $ra
    nop
"""
    code = assemble(source, pool["up_stub"])
    if len(code) > UP_STUB_SIZE:
        raise ValueError(f"upload stub {len(code)} > {UP_STUB_SIZE}")
    return code


def split_address(value: int) -> tuple[str, str]:
    """lui/ori pair: ori zero-extends, so NO carry on the high half."""
    return f"0x{(value >> 16) & 0xFFFF:X}", f"0x{value & 0xFFFF:X}"


def usa_render_glyph_fit(font_path: Path, character: str, pixel_size: int = 16):
    """v36 render_glyph_fit adapted for the b6 no-CLUT design:

    - pixel indices target the STOCK letter CLUT 0x7838 (VRAM-sampled:
      idx6 = white core, idx1 = black outline, idx2-5 = grays) so glyphs
      render correctly with NO custom CLUT upload anywhere -- CLUT DMA
      drops during FMV playback were b5's invisible-opening cause
    - vertical placement preserves the font baseline instead of centering
      (a centered comma floated mid-cell, b5 ending runtime)
    """
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(font_path), pixel_size)
    canvas = Image.new("P", (48, 48), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 16), character, font=font, fill=6, stroke_width=1, stroke_fill=1)
    raw_indices = Image.frombytes("L", canvas.size, canvas.tobytes())
    ink_box = raw_indices.point(lambda v: 255 if v else 0).getbbox()
    if ink_box is None:
        raise ValueError(f"empty glyph for {character!r}")
    ink = canvas.crop(ink_box)
    squeezed = ink.size
    if ink.width > 16 or ink.height > 16:
        squeezed = (min(ink.width, 16), min(ink.height, 16))
        ink = ink.resize(squeezed, Image.Resampling.NEAREST)
    y_off = max(0, min(ink_box[1] - 16, 16 - ink.height))
    cell = Image.new("P", (16, 16), 0)
    cell.paste(ink, ((16 - ink.width) // 2, y_off))
    pixels = cell.load()
    packed = bytearray()
    for y in range(16):
        for x in range(0, 16, 2):
            packed.append((pixels[x, y] & 0x0F) | ((pixels[x + 1, y] & 0x0F) << 4))
    meta = {
        "character": character,
        "ink_size": list(ink.size),
        "squeezed_to": list(squeezed),
        "y_offset": y_off,
    }
    return bytes(packed), None, meta


# the file-section pipeline (GroupLayout in v37) must render with the same
# indices/baseline; both modules resolve render_glyph_fit at call time
_v36_mod.render_glyph_fit = usa_render_glyph_fit
_v37_mod.render_glyph_fit = usa_render_glyph_fit
render_glyph_fit = usa_render_glyph_fit


def template_index(cell: str) -> int:
    """Subtitle template entry index of a cell character (drawer dispatch:
    '0'-'9' -> 3+n at the (920,400) digit grid, 'A'-'Z' -> 13+n)."""
    if "0" <= cell <= "9":
        return 3 + ord(cell) - 0x30
    return 13 + ord(cell) - 0x41


def normalize_line(line: str) -> str:
    return (
        line.replace("...", "…")
        .replace("\u2018", "'").replace("\u2019", "'")
        .replace("\u201C", "'").replace("\u201D", "'")
        .replace("\u3000", " ")
    )


def token_span(data: bytes, offset: int) -> int:
    """Return the byte length of the token at offset (header + lines)."""
    count = data[offset + 4] - 0x30
    p = offset + 5
    for _ in range(count):
        end = data.index(0, p)
        p = end + 1
    return p - offset


def build_record_atlas(korean_lines: list[str], font: Path):
    """Encode lines and build strip pixel blocks (file-section records)."""
    layout = GroupLayout(font)
    cell_lines = [layout.encode_line(line) for line in korean_lines]
    col1, col2, dig, _meta = layout.build_pixel_blocks()
    clut = struct.pack("<16H", 0x0000, 0x8000, 0x7FFF, *([0x0000] * 13))
    return cell_lines, layout.blank_char, layout.used, col1, col2, dig, clut


def cell_vram_xy(cell: str) -> tuple[int, int]:
    """VRAM halfword-column/row of a stock font cell (per-glyph upload)."""
    if cell in COL1_CHARS:
        i = COL1_CHARS.index(cell)
        return 950, 256 + 16 * i
    if cell in COL2_CHARS:
        i = COL2_CHARS.index(cell)
        return 954, 256 + 16 * i
    i = DIG_CHARS.index(cell)
    return 920 + 4 * (i % 3), 400 + 16 * (i // 3)


def build_exe_record(korean_lines: list[str], font: Path):
    """Encode one EXE record; returns (cell_lines, [(cell, source_char)])."""
    layout = GroupLayout(font)
    cell_lines = [layout.encode_line(line) for line in korean_lines]
    glyphs = [(cell, source) for source, cell in layout.assignments.items()]
    return cell_lines, glyphs


def encode_token(frame: int, cell_lines: list[str]) -> bytes:
    token = f"{frame:04d}{len(cell_lines)}".encode("ascii")
    body = b"".join(s.encode("ascii") + b"\x00" for s in cell_lines)
    return token + body


# --------------------------------------------------------------------- wrapper
def build_wrapper(pool: dict, exe_count: int, original_exe: bytes,
                  trace_file_upload: bool = False) -> bytes:
    """Cave wrapper called in place of every `jal drawer`.

    Token-pointer keyed: EXE entry walk (exact address match) -> file walk
    (base derivation + 2-word fingerprint) -> fail-safe plain drawer call.
    On a match: upload pixels when the CURRENT TOKEN CHANGES (b4 runtime:
    gp+0x420 resets per stream, not per record -- tick gating replayed the
    first record's glyphs for every later record), patch width + template
    around the real drawer, then restore the template from a baked copy.
    No CLUT uploads anywhere since b6: glyph pixels target the stock letter
    CLUT 0x7838 (CLUT DMA dropped during FMV playback -- b3 diagnosis,
    b5 invisible opening).  File records keep the proven stock-cell strip
    hijack; EXE (battle) records upload per-glyph to the free VRAM band
    (960,112) and patch template UV/tpage per used cell every frame, so
    stock font pixels stay intact (b5: CONTINUE screen corruption).
    """
    width_patch_ram = USA_WIDTH + WIDTH_PATCH_FIRST * 2
    template_patch_ram = USA_TEMPLATE + 3 * 16 + 6
    tmpl_restore_ram = USA_TEMPLATE + 3 * 16

    sp = {name: split_address(addr) if isinstance(addr, int) else addr
          for name, addr in pool.items()}

    trace_match = trace_last = trace_upload = trace_done = ""
    if trace_file_upload:
        trace_match = f"""
    lui $t8, {sp['file_debug'][0]}
    ori $t8, $t8, {sp['file_debug'][1]}
    sw $t6, 0($t8)
    sw $s2, 4($t8)
    sw $s1, 8($t8)
    lw $t1, 4($s1)
    lw $t2, 8($s1)
    nop
    sw $t1, 12($t8)
    sw $t2, 16($t8)
    lw $t1, 12($s1)
    nop
    sw $t1, 20($t8)
    sw $t0, 40($t8)
    sw $t5, 44($t8)
    sw $zero, 28($t8)
    sw $zero, 32($t8)
"""
        trace_last = f"""
    lui $t8, {sp['file_debug'][0]}
    ori $t8, $t8, {sp['file_debug'][1]}
    sw $t0, 24($t8)
    lw $t1, 36($t8)
    nop
    addiu $t1, $t1, 1
    sw $t1, 36($t8)
"""
        trace_upload = """
    ori $t1, $zero, 1
    sw $t1, 28($t8)
"""
        trace_done = f"""
    lui $t8, {sp['file_debug'][0]}
    ori $t8, $t8, {sp['file_debug'][1]}
    ori $t1, $zero, 1
    sw $t1, 32($t8)
"""

    upload_blocks = "\n".join(f"""
    lw $t0, {slot}($s1)
    lui $a0, {sp[rect][0]}
    beq $t0, $zero, skip_up_{slot}
    ori $a0, $a0, {sp[rect][1]}
    addu $a1, $s2, $t0
    jal 0x{USA_LOAD_IMAGE:08x}
    nop
skip_up_{slot}:
""" for slot, rect in ((4, "rect_col1"), (8, "rect_col2"), (12, "rect_dig")))

    source = f"""
    addiu $sp, $sp, -0x28
    sw $ra, 0x24($sp)
    sw $s1, 0x20($sp)
    sw $s2, 0x1c($sp)
    sw $a0, 0x18($sp)
    sw $s3, 0x14($sp)
    lw $t6, 0x424($gp)
    nop
    beq $t6, $zero, plain
    nop
    lw $t6, 0($t6)
    nop
    beq $t6, $zero, plain
    nop
    lui $t4, {sp['exe_table'][0]}
    ori $t4, $t4, {sp['exe_table'][1]}
    ori $t2, $zero, {exe_count}
exe_walk:
    lw $t8, 0($t4)
    nop
    beq $t8, $t6, exe_found
    nop
    addiu $t4, $t4, 12
    addiu $t2, $t2, -1
    bne $t2, $zero, exe_walk
    nop
    lui $t4, {sp['file_table'][0]}
    ori $t4, $t4, {sp['file_table'][1]}
file_loop:
    lw $t7, 0($t4)
    nop
    beq $t7, $zero, plain
    nop
    lw $t1, 4($t4)
    lw $t2, 12($t4)
    lw $t3, 16($t4)
    move $t5, $zero
rec_loop:
    lw $t0, 0($t3)
    nop
    subu $t0, $t6, $t0
    srl $t9, $t0, 22
    xori $t9, $t9, 0x200
    bne $t9, $zero, rec_next
    nop
    lw $t9, 8($t4)
    nop
    addu $t8, $t0, $t9
    lw $t9, 0($t8)
    nop
    bne $t9, $t7, rec_next
    nop
    lw $t9, 4($t8)
    nop
    beq $t9, $t1, file_found
    nop
rec_next:
    addiu $t3, $t3, 4
    addiu $t5, $t5, 1
    addiu $t2, $t2, -1
    bne $t2, $zero, rec_loop
    nop
    addiu $t4, $t4, 20
    j file_loop
    nop
file_found:
    lw $t8, 8($t4)
    nop
    addu $s2, $t0, $t8
    sll $t9, $t5, 4
    sll $t5, $t5, 2
    addu $t9, $t9, $t5
    addiu $s1, $s2, 12
    addu $s1, $s1, $t9
{trace_match}
    lui $t9, {sp['last_token'][0]}
    ori $t9, $t9, {sp['last_token'][1]}
    lw $t0, 0($t9)
    nop
{trace_last}
    beq $t0, $t6, do_patch
    nop
    sw $t6, 0($t9)
{trace_upload}
{upload_blocks}
{trace_done}
    j do_patch
    nop
exe_found:
    move $s3, $t4
    lui $t9, {sp['last_token'][0]}
    ori $t9, $t9, {sp['last_token'][1]}
    lw $t0, 0($t9)
    nop
    beq $t0, $t6, exe_patch
    nop
    sw $t6, 0($t9)
    lw $s1, 4($s3)
    lw $s2, 8($s3)
    nop
exe_uploop:
    beq $s2, $zero, exe_patch
    nop
    lhu $a1, 6($s1)
    lw $a0, 0($s1)
    jal 0x{pool['up_stub']:08x}
    nop
    addiu $s1, $s1, 8
    addiu $s2, $s2, -1
    j exe_uploop
    nop
exe_patch:
    lw $s1, 4($s3)
    lw $s2, 8($s3)
    lui $t7, {split_address(USA_TEMPLATE)[0]}
    ori $t7, $t7, {split_address(USA_TEMPLATE)[1]}
exe_ploop:
    beq $s2, $zero, do_patch
    nop
    lhu $t1, 4($s1)
    lhu $t2, 6($s1)
    addu $t1, $t7, $t1
    sll $t3, $t2, 4
    ori $t3, $t3, 0x7000
    sh $t3, 4($t1)
    addiu $t5, $t3, 15
    sh $t5, 8($t1)
    ori $t8, $zero, 0x2F
    sh $t8, 10($t1)
    addiu $t5, $t3, 0x0F00
    sh $t5, 12($t1)
    addiu $t5, $t5, 15
    sh $t5, 14($t1)
    addiu $s1, $s1, 8
    addiu $s2, $s2, -1
    j exe_ploop
    nop
plain:
    lui $t9, {sp['last_token'][0]}
    ori $t9, $t9, {sp['last_token'][1]}
    sw $zero, 0($t9)
    lw $a0, 0x18($sp)
    jal 0x{DRAWER:08x}
    nop
    j epilogue
    nop
do_patch:
    lui $t3, {split_address(width_patch_ram)[0]}
    ori $t3, $t3, {split_address(width_patch_ram)[1]}
    ori $t5, $zero, 16
    ori $t6, $zero, {WIDTH_PATCH_COUNT}
wpatch:
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t6, $t6, -1
    bne $t6, $zero, wpatch
    nop
    lui $t3, {split_address(template_patch_ram)[0]}
    ori $t3, $t3, {split_address(template_patch_ram)[1]}
    ori $t5, $zero, 0x{STOCK_TEMPLATE_CLUT:X}
    ori $t6, $zero, 36
cpatch:
    sh $t5, 0($t3)
    addiu $t3, $t3, 16
    addiu $t6, $t6, -1
    bne $t6, $zero, cpatch
    nop
    lw $a0, 0x18($sp)
    jal 0x{DRAWER:08x}
    nop
    lui $t3, {split_address(tmpl_restore_ram)[0]}
    ori $t3, $t3, {split_address(tmpl_restore_ram)[1]}
    lui $t4, {sp['tmpl_backup'][0]}
    ori $t4, $t4, {sp['tmpl_backup'][1]}
    ori $t6, $zero, 144
trest:
    lw $t5, 0($t4)
    addiu $t4, $t4, 4
    sw $t5, 0($t3)
    addiu $t3, $t3, 4
    addiu $t6, $t6, -1
    bne $t6, $zero, trest
    nop
    lui $t3, {split_address(width_patch_ram)[0]}
    ori $t3, $t3, {split_address(width_patch_ram)[1]}
    lui $t4, {sp['width_backup'][0]}
    ori $t4, $t4, {sp['width_backup'][1]}
    ori $t6, $zero, {WIDTH_PATCH_COUNT}
wrest:
    lbu $t5, 0($t4)
    nop
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t4, $t4, 1
    addiu $t6, $t6, -1
    bne $t6, $zero, wrest
    nop
epilogue:
    lw $ra, 0x24($sp)
    lw $s1, 0x20($sp)
    lw $s2, 0x1c($sp)
    lw $s3, 0x14($sp)
    addiu $sp, $sp, 0x28
    jr $ra
    nop
"""
    code = assemble(source, CAVE_START)
    assert_no_load_delay_hazard(code, CAVE_START)
    return code


def build_name_stub(stub_ram: int, pool: dict, exe_count: int,
                    do_upload: bool = True, do_patch: bool = True) -> bytes:
    """Nameplate stub, placed at the head of the boot-copied pool.

    Replaces `jal 0x80036EF4` at the two character-select call sites.  The
    renderer takes the string address in $a0 -- the same key the EXE record
    table uses -- so this walks that table, uploads the record's glyphs to the
    free VRAM band, patches the template UV/tpage per used cell, calls the
    renderer, then restores the template from the baked copy.  No match ->
    renderer called untouched.

    It lives in the pool rather than the cave because the cave (1233 B) is
    already full; the pool is copied to RAM at boot by the entry stub.
    """
    sp = {k: split_address(v) for k, v in pool.items()}
    # A/B switches: c7 broke the character-select portrait once the match path
    # actually ran, and the two things it does (glyph upload, template patch)
    # are indistinguishable statically -- these isolate them.
    skip_upload = "" if do_upload else "    j nm_patch\n    nop\n"
    skip_patch = "" if do_patch else "    j nm_draw\n    nop\n"
    source = f"""
    addiu $sp, $sp, -0x28
    sw $ra, 0x24($sp)
    sw $s1, 0x20($sp)
    sw $s2, 0x1c($sp)
    sw $s3, 0x18($sp)
    sw $a0, 0x14($sp)
    sw $a1, 0x10($sp)
    sw $a2, 0xc($sp)
    move $t6, $a0
    lui $t7, {sp['debug'][0]}
    ori $t7, $t7, {sp['debug'][1]}
    lw $t9, 0($t7)
    nop
    addiu $t9, $t9, 1
    sw $t9, 0($t7)
    sw $a0, 4($t7)
    lui $t4, {sp['exe_table'][0]}
    ori $t4, $t4, {sp['exe_table'][1]}
    lw $t9, 0($t4)
    nop
    sw $t9, 12($t7)
    ori $t2, $zero, {exe_count}
nm_walk:
    lw $t8, 0($t4)
    nop
    beq $t8, $t6, nm_found
    nop
    addiu $t4, $t4, 12
    addiu $t2, $t2, -1
    bne $t2, $zero, nm_walk
    nop
    j nm_draw
    nop
nm_found:
    lw $t9, 8($t7)
    nop
    addiu $t9, $t9, 1
    sw $t9, 8($t7)
    move $s3, $t4
    lw $s1, 4($s3)
    lw $s2, 8($s3)
    nop
{skip_upload}nm_uploop:
    beq $s2, $zero, nm_patch
    nop
    lhu $a1, 6($s1)
    lw $a0, 0($s1)
    jal 0x{pool['up_stub']:08x}
    nop
    addiu $s1, $s1, 8
    addiu $s2, $s2, -1
    j nm_uploop
    nop
nm_patch:
{skip_patch}    lw $s1, 4($s3)
    lw $s2, 8($s3)
    lui $t7, {split_address(USA_TEMPLATE + NAME_FONT_BASE * 16)[0]}
    ori $t7, $t7, {split_address(USA_TEMPLATE + NAME_FONT_BASE * 16)[1]}
nm_ploop:
    beq $s2, $zero, nm_widths
    nop
    lhu $t1, 4($s1)
    lhu $t2, 6($s1)
    addu $t1, $t7, $t1
    sll $t3, $t2, 4
    ori $t3, $t3, 0x7000
    sh $t3, 4($t1)
    ori $t8, $zero, 0x{NAME_CLUT:X}
    sh $t8, 6($t1)
    addiu $t5, $t3, 15
    sh $t5, 8($t1)
    ori $t8, $zero, 0x2F
    sh $t8, 10($t1)
    addiu $t5, $t3, 0x0F00
    sh $t5, 12($t1)
    addiu $t5, $t5, 15
    sh $t5, 14($t1)
    addiu $s1, $s1, 8
    addiu $s2, $s2, -1
    j nm_ploop
    nop
nm_widths:
    lui $t3, {split_address(USA_WIDTH + (NAME_FONT_BASE + NAME_CELL_FIRST) * 2)[0]}
    ori $t3, $t3, {split_address(USA_WIDTH + (NAME_FONT_BASE + NAME_CELL_FIRST) * 2)[1]}
    ori $t5, $zero, 16
    ori $t6, $zero, {NAME_CELL_COUNT}
nm_wloop:
    sh $t5, 0($t3)
    addiu $t3, $t3, 2
    addiu $t6, $t6, -1
    bne $t6, $zero, nm_wloop
    nop
nm_draw:
    lw $a0, 0x14($sp)
    lw $a1, 0x10($sp)
    lw $a2, 0xc($sp)
    ori $a3, $zero, 1
    jal 0x{USA_TEXT_RENDERER:08x}
    nop
    lui $t0, {sp['name_tmpl_backup'][0]}
    ori $t0, $t0, {sp['name_tmpl_backup'][1]}
    lui $t1, {split_address(USA_TEMPLATE + (NAME_FONT_BASE + NAME_CELL_FIRST) * 16)[0]}
    ori $t1, $t1, {split_address(USA_TEMPLATE + (NAME_FONT_BASE + NAME_CELL_FIRST) * 16)[1]}
    ori $t2, $zero, {NAME_CELL_COUNT * 4}
nm_rest:
    lw $t3, 0($t0)
    addiu $t0, $t0, 4
    sw $t3, 0($t1)
    addiu $t1, $t1, 4
    addiu $t2, $t2, -1
    bne $t2, $zero, nm_rest
    nop
    lui $t0, {sp['name_adv_backup'][0]}
    ori $t0, $t0, {sp['name_adv_backup'][1]}
    lui $t1, {split_address(USA_WIDTH + (NAME_FONT_BASE + NAME_CELL_FIRST) * 2)[0]}
    ori $t1, $t1, {split_address(USA_WIDTH + (NAME_FONT_BASE + NAME_CELL_FIRST) * 2)[1]}
    ori $t2, $zero, {NAME_CELL_COUNT}
nm_arest:
    lhu $t3, 0($t0)
    addiu $t0, $t0, 2
    sh $t3, 0($t1)
    addiu $t1, $t1, 2
    addiu $t2, $t2, -1
    bne $t2, $zero, nm_arest
    nop
    lw $ra, 0x24($sp)
    lw $s1, 0x20($sp)
    lw $s2, 0x1c($sp)
    lw $s3, 0x18($sp)
    addiu $sp, $sp, 0x28
    jr $ra
    nop
"""
    code = assemble(source, stub_ram)
    assert_no_load_delay_hazard(code, stub_ram)
    if len(code) > NAME_STUB_SIZE:
        raise ValueError(f"name stub {len(code)} > {NAME_STUB_SIZE} bytes")
    return code


def build_finisher(finisher_ram: int, ext_words: int, entry_orig: int) -> bytes:
    """Runs from the cave after the boot copy: zero the extension region
    (so it stays pristine BSS) and jump to the original entry point."""
    source = f"""
    lui $t0, {split_address(EXT_RAM)[0]}
    ori $t0, $t0, {split_address(EXT_RAM)[1]}
    ori $t2, $zero, {ext_words}
zloop:
    sw $zero, 0($t0)
    addiu $t0, $t0, 4
    addiu $t2, $t2, -1
    bne $t2, $zero, zloop
    nop
    j 0x{entry_orig:08x}
    nop
"""
    return assemble(source, finisher_ram)


def build_boot_stub(pool_words: int, finisher_ram: int) -> bytes:
    """Placed at EXT_RAM; the EXE header entry point is redirected here.
    Copies the pool image to POOL_BASE, then runs the cave finisher."""
    src = EXT_RAM + EXT_STUB_SIZE
    source = f"""
    lui $t0, {split_address(src)[0]}
    ori $t0, $t0, {split_address(src)[1]}
    lui $t1, {split_address(POOL_BASE)[0]}
    ori $t1, $t1, {split_address(POOL_BASE)[1]}
    ori $t2, $zero, {pool_words}
copyloop:
    lw $t3, 0($t0)
    addiu $t0, $t0, 4
    sw $t3, 0($t1)
    addiu $t1, $t1, 4
    addiu $t2, $t2, -1
    bne $t2, $zero, copyloop
    nop
    j 0x{finisher_ram:08x}
    nop
"""
    code = assemble(source, EXT_RAM)
    if len(code) > EXT_STUB_SIZE:
        raise ValueError("boot stub exceeds reserved bytes")
    return code


# ------------------------------------------------------------------------ pool
def build_pool(file_infos: list[dict], exe_records: list[dict],
               original_exe: bytes) -> tuple[bytes, dict]:
    """Assemble the boot-copied pool image and its address map.

    Layout (all pointers inside are absolute POOL_BASE-relative addresses):
      +0x00 scratch rect {x, y, 4, 16}     (x/y written by the wrapper)
      ... (tables/glyphs) ...
      tail: nameplate stub code (NAME_STUB_SIZE bytes, filled after assembly).
      It sits at the END because POOL_BASE had to drop 4 KB below the band
      that JP dumps proved zero; c1 put the stub in that unverified low
      window and the character-select screen hung the moment it was called.
      +0x08 clut rect
      +0x10 col1/col2/dig strip rects
      +0x28 width backup (byte per entry)
      then: EXE entry table, per-record glyph entries, shared clut,
            glyph pixels (deduped), file table, per-file offset arrays.
    """
    width_offset = (EXE_HEADER_SIZE + USA_WIDTH - EXE_RAM_BASE
                    + WIDTH_PATCH_FIRST * 2)
    widths = struct.unpack_from(f"<{WIDTH_PATCH_COUNT}H", original_exe,
                                width_offset)
    if any(v > 0xFF for v in widths):
        raise ValueError("stock width does not fit a byte")

    blob = bytearray()
    blob += struct.pack("<4h", 0, 112, 4, 16)        # scratch rect (x runtime)
    blob += struct.pack("<4h", *CLUT_RECT)           # (unused since b6)
    blob += struct.pack("<4h", *RECT_COL1)
    blob += struct.pack("<4h", *RECT_COL2)
    blob += struct.pack("<4h", *RECT_DIG)
    blob += bytes(widths)                            # 36 bytes
    while len(blob) % 4:
        blob += b"\x00"

    # nameplate stub instrumentation (c6): +0 stub entries, +4 last $a0 key,
    # +8 table matches, +12 first table word (proves the boot copy landed).
    # The next 48 bytes are optional file-upload trace state.
    debug_off = len(blob)
    blob += bytes(64)
    file_debug_off = debug_off + 16

    name_ent = NAME_FONT_BASE + NAME_CELL_FIRST
    name_tmpl_off = len(blob)
    src = EXE_HEADER_SIZE + USA_TEMPLATE - EXE_RAM_BASE + name_ent * 16
    blob += original_exe[src:src + NAME_CELL_COUNT * 16]
    name_adv_off = len(blob)
    src = EXE_HEADER_SIZE + USA_WIDTH - EXE_RAM_BASE + name_ent * 2
    blob += original_exe[src:src + NAME_CELL_COUNT * 2]
    while len(blob) % 4:
        blob += bytes(1)

    lut_off = len(blob)
    blob += glyph_lut()                              # 512 B, 2bpp -> 4bpp
    gscratch_off = len(blob)
    blob += bytes(GLYPH_BYTES)                       # one decoded glyph

    last_token_off = len(blob)                       # last uploaded token ptr
    blob += bytes(4)

    # original template entries 3..38 (16B each) for the post-draw restore
    tmpl_backup_off = len(blob)
    tmpl_src = EXE_HEADER_SIZE + USA_TEMPLATE - EXE_RAM_BASE + 3 * 16
    blob += original_exe[tmpl_src:tmpl_src + 36 * 16]

    exe_table_off = len(blob)
    blob += bytes(12 * len(exe_records))             # filled below

    # EXE records draw from the free VRAM band (960,112): 16 cells of 4hw,
    # template UV/tpage patched per used cell -- STOCK CELLS STAY INTACT
    # (b5 runtime: hijacked stock pixels corrupted the CONTINUE screen).
    # glyph entry: {src_addr u32, template_entry_off u16, band_slot u16}
    glyph_blob = bytearray()
    glyph_cache: dict[str, int] = {}                 # source char -> blob off

    def glyph_off(source_char: str, font: Path) -> int:
        if source_char in glyph_cache:
            return glyph_cache[source_char]
        if source_char == "\x00blank":
            pixels = bytes(GLYPH_BYTES)
        else:
            pixels, _preview, _meta = render_glyph_fit(font, source_char)
        off = len(glyph_blob)
        glyph_blob.extend(pack_glyph_2bpp(pixels))
        glyph_cache[source_char] = off
        return off

    entry_lists = []
    for rec in exe_records:
        items = []
        for slot, (cell, source) in enumerate(rec["glyphs"]):
            items.append((glyph_off(source, rec["font"]),
                          template_index(cell) * 16, slot))
        if len(items) > 16:
            raise ValueError(
                f"EXE record at 0x{rec['token_addr']:08X}: "
                f"{len(items)} glyphs exceed the 16-cell band")
        entry_lists.append(items)

    entry_offsets = []
    for items in entry_lists:
        entry_offsets.append(len(blob))
        for goff, toff, slot in items:
            blob += struct.pack("<IHH", goff, toff, slot)  # goff fixed below

    glyph_pixels_off = len(blob)
    blob += glyph_blob

    # fix up glyph src offsets -> absolute addresses
    for rec_index, items in enumerate(entry_lists):
        base = entry_offsets[rec_index]
        for k, (goff, toff, slot) in enumerate(items):
            struct.pack_into(
                "<IHH", blob, base + 8 * k,
                POOL_BASE + glyph_pixels_off + goff, toff, slot)

    # fill EXE entry table
    for i, rec in enumerate(exe_records):
        struct.pack_into(
            "<III", blob, exe_table_off + 12 * i,
            rec["token_addr"],
            POOL_BASE + entry_offsets[i],
            len(entry_lists[i]))

    # file table + offset arrays
    offs_pos = []
    file_table_off = len(blob)
    blob += bytes(20 * len(file_infos) + 4)          # + zero terminator
    for f in file_infos:
        offs_pos.append(len(blob))
        for off in f["offsets"]:
            blob += struct.pack("<I", off)
    for i, f in enumerate(file_infos):
        struct.pack_into(
            "<5I", blob, file_table_off + 20 * i,
            f["w3"], f["w4"], f["section_offset"],
            len(f["offsets"]), POOL_BASE + offs_pos[i])

    while len(blob) % 4:
        blob += b"\x00"
    up_stub_off = len(blob)
    blob += bytes(UP_STUB_SIZE)
    name_stub_off = len(blob)
    blob += bytes(NAME_STUB_SIZE)

    if POOL_BASE + len(blob) > POOL_LIMIT:
        raise ValueError(f"pool overflow: {len(blob)} bytes")

    addresses = {
        "name_stub": POOL_BASE + name_stub_off,
        "scratch_rect": POOL_BASE + 0x00,
        "rect_col1": POOL_BASE + 0x10,
        "rect_col2": POOL_BASE + 0x18,
        "rect_dig": POOL_BASE + 0x20,
        "width_backup": POOL_BASE + 0x28,
        "debug": POOL_BASE + debug_off,
        "file_debug": POOL_BASE + file_debug_off,
        "name_tmpl_backup": POOL_BASE + name_tmpl_off,
        "name_adv_backup": POOL_BASE + name_adv_off,
        "glyph_lut": POOL_BASE + lut_off,
        "gscratch": POOL_BASE + gscratch_off,
        "up_stub": POOL_BASE + up_stub_off,
        "last_token": POOL_BASE + last_token_off,
        "tmpl_backup": POOL_BASE + tmpl_backup_off,
        "exe_table": POOL_BASE + exe_table_off,
        "file_table": POOL_BASE + file_table_off,
    }
    meta = {
        "name_stub": POOL_BASE + name_stub_off,
        "debug": f"0x{POOL_BASE + debug_off:08X}",
        "file_debug": f"0x{POOL_BASE + file_debug_off:08X}",
        "up_stub": f"0x{POOL_BASE + up_stub_off:08X}",
        "pool_base": f"0x{POOL_BASE:08X}",
        "pool_size": len(blob),
        "pool_end": f"0x{POOL_BASE + len(blob):08X}",
        "glyphs_unique": len(glyph_cache),
        "exe_records": len(exe_records),
    }
    return bytes(blob), addresses, meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, nargs="?",
                        default=ROOT / "original" / "Bushido Blade (USA).bin")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--font", type=Path,
        default=Path(r"G:\Downloads\Korean Traslate Project\Font\솔뫼 김대건 Medium.ttf"))
    parser.add_argument("--base-name", default="Bushido Blade (USA) [KO B5]")
    parser.add_argument("--no-names", action="store_true",
                        help="leave the character-select nameplates in English "
                             "(keeps the pool inside the 16 KB verified band)")
    parser.add_argument("--name-no-upload", action="store_true",
                        help="A/B: nameplate stub patches the template but "
                             "uploads no glyphs")
    parser.add_argument("--name-no-patch", action="store_true",
                        help="A/B: nameplate stub uploads glyphs but leaves "
                             "the template alone")
    parser.add_argument("--no-name-stub", action="store_true",
                        help="translate the nameplate strings but leave the two "
                             "renderer call sites alone (isolates stub vs data)")
    parser.add_argument(
        "--trace-file-upload", action="store_true",
        help="record the last matched file token/section/entry/upload decision")
    args = parser.parse_args()

    comparison = json.loads(COMPARISON.read_text(encoding="utf-8"))
    records_by_path = comparison["official_subtitle_records"]
    korean: dict[tuple[str, int], str] = {}
    with CSV_PATH.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            text = row["korean_translation"].strip()
            if text and row["source_path"] in TARGET_FILES + [EXE_SCOPE]:
                korean[(row["source_path"], int(row["record_index"]))] = text

    info = disc.inspect(args.image)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_bin = args.output_dir / f"{args.base_name}.bin"
    shutil.copyfile(args.image, out_bin)

    image_sectors = out_bin.stat().st_size // disc.RAW_SECTOR_SIZE
    next_lba = image_sectors
    report = {"files": [], "skipped": [], "load_table": [], "exe_records": []}
    file_infos: list[dict] = []
    tags = {path: section_tag(path) for path in TARGET_FILES}
    if len(set(tags.values())) != len(tags):
        raise ValueError("K1US source-path tag collision")

    exe_entry = next(e for e in info["entries"] if e["path"] == "SCUS_941.80")
    with args.image.open("rb") as f:
        exe, _ = disc.read_extent(f, exe_entry["lba"], exe_entry["size"])
    exe = bytearray(exe)

    # ------------------------------------------------------------- stage A
    for path in TARGET_FILES:
        entry = next(e for e in info["entries"] if e["path"] == path)
        with args.image.open("rb") as f:
            data, forms = disc.read_extent(f, entry["lba"], entry["size"])
        assert forms == {"form1"}, path
        data = bytearray(data)

        entries = []
        blobs = bytearray()
        header_guess = 12 + 20 * sum(
            1 for i, _ in enumerate(records_by_path[path])
            if (path, i) in korean
        )
        cursor = header_guess

        for index, record in enumerate(records_by_path[path]):
            key = (path, index)
            if key not in korean:
                continue
            # when a record is preceded by a hide token ("%04d0", 5 bytes) the
            # comparison offset points at that token; the show token follows
            offset = record["offset"] + (5 if record.get("end_code_before") else 0)
            span = token_span(bytes(data), offset)
            frame = record["start_frame"]
            lines = [normalize_line(l) for l in korean[key].splitlines() if l.strip()]
            try:
                cell_lines, blank_char, used, col1, col2, dig, clut = \
                    build_record_atlas(lines, args.font)
            except Exception as exc:
                report["skipped"].append(
                    {"path": path, "index": index, "reason": str(exc)})
                continue
            new = encode_token(frame, cell_lines)
            # squeeze blank cells (spaces) one at a time if a byte or two over
            while len(new) > span and blank_char and \
                    any(blank_char in s for s in cell_lines):
                longest = max(range(len(cell_lines)),
                              key=lambda i: len(cell_lines[i])
                              if blank_char in cell_lines[i] else -1)
                pos = cell_lines[longest].rfind(blank_char)
                cell_lines[longest] = (cell_lines[longest][:pos]
                                       + cell_lines[longest][pos + 1:])
                new = encode_token(frame, cell_lines)
            if len(new) > span:
                # last resort: drop punctuation from the source and re-encode
                squeezed = [l.replace(",", "").replace("  ", " ") for l in lines]
                cell_lines, blank_char, used, col1, col2, dig, clut = \
                    build_record_atlas(squeezed, args.font)
                new = encode_token(frame, cell_lines)
                if len(new) <= span:
                    report.setdefault("punct_dropped", []).append(
                        {"path": path, "index": index})
            if len(new) > span:
                report["skipped"].append(
                    {"path": path, "index": index,
                     "reason": f"does not fit: {len(new)} > {span}"})
                continue
            data[offset:offset + span] = new.ljust(span, b"\x00")

            offs = []
            for block, used_key in ((col1, "col1"), (col2, "col2"), (dig, "dig")):
                if used.get(used_key):
                    offs.append(cursor + len(blobs))
                    blobs += block
                else:
                    offs.append(0)
            offs.append(cursor + len(blobs))
            blobs += clut
            entries.append((offset, *offs))

        if not entries:
            continue
        tag = section_tag(path)
        header = struct.pack("<III", SECTION_MAGIC, tag, len(entries))
        for e in entries:
            header += struct.pack("<5I", *e)
        assert len(header) <= header_guess
        header = header.ljust(header_guess, b"\x00")
        section = header + blobs
        section_offset = len(data)
        data += section

        # Container files (O05/O14: count=2, then count+1 block offsets at +4)
        # end their last block exactly at the original file size, so an appended
        # section falls outside it.  O14 works anyway because its 66 KB fit in
        # one whole-file read, but O05 is 523 KB and only its subtitle block
        # reaches RAM -- the wrapper then derives a base outside the buffer and
        # falls through to the plain drawer (intro cutscene shows cell codes).
        # Extending the terminating offset pulls the section into that block.
        block_count = struct.unpack_from("<I", data, 0)[0]
        if 1 <= block_count <= 8:
            offsets = list(struct.unpack_from(f"<{block_count + 1}I", data, 4))
            if offsets[0] == 4 + (block_count + 1) * 4 and \
                    offsets[-1] == section_offset:
                struct.pack_into("<I", data, 4 + block_count * 4, len(data))
                report.setdefault("block_extended", []).append(
                    {"path": path, "old_end": hex(section_offset),
                     "new_end": hex(len(data))})
        file_infos.append({
            "path": path,
            # NOT a header fingerprint: O05/O14 are containers whose word at
            # +0xC is the file-end offset, so for O05 it equalled its own
            # section offset and the check aliased.  Verify the section magic
            # at the candidate base instead -- always exact.
            "w3": SECTION_MAGIC,
            "w4": tag,
            "section_offset": section_offset,
            "offsets": [e[0] for e in entries],
        })

        new_size = len(data)
        sectors = (new_size + 2047) // 2048
        new_lba = next_lba
        next_lba += sectors

        with args.image.open("rb") as src:
            src.seek(entry["lba"] * disc.RAW_SECTOR_SIZE + 16)
            first_sub = src.read(8)
            last_src = entry["lba"] + (entry["size"] + 2047) // 2048 - 1
            src.seek(last_src * disc.RAW_SECTOR_SIZE + 16)
            last_sub = src.read(8)
        with out_bin.open("r+b") as out:
            out.seek(new_lba * disc.RAW_SECTOR_SIZE)
            for s_index in range(sectors):
                chunk = bytes(data[s_index * 2048:(s_index + 1) * 2048]).ljust(2048, b"\x00")
                sub = last_sub if s_index == sectors - 1 else first_sub
                out.write(make_raw_sector(new_lba + s_index, sub, chunk,
                                          rebuild_mode2_form1_integrity))

        # directory record: find the entry LBA/size and patch (both endians)
        parent = next(e for e in info["entries"]
                      if e["path"] == path.split("/")[0] and e.get("is_dir", True))
        patched_dir = False
        for dir_lba in range(parent["lba"], parent["lba"] + 4):
            def try_patch(raw: bytearray, want_lba=entry["lba"], want_size=entry["size"],
                          nl=new_lba, ns=new_size):
                nonlocal patched_dir
                base = disc.MODE2_FORM1_DATA_OFFSET
                block = raw[base:base + 2048]
                pos = 0
                while pos < 2048:
                    length = block[pos]
                    if length == 0:
                        break
                    lba_le = struct.unpack_from("<I", block, pos + 2)[0]
                    size_le = struct.unpack_from("<I", block, pos + 10)[0]
                    if lba_le == want_lba and size_le == want_size:
                        struct.pack_into("<I", raw, base + pos + 2, nl)
                        struct.pack_into(">I", raw, base + pos + 6, nl)
                        struct.pack_into("<I", raw, base + pos + 10, ns)
                        struct.pack_into(">I", raw, base + pos + 14, ns)
                        patched_dir = True
                    pos += length
            patch_logical_sector(out_bin, dir_lba, try_patch)
            if patched_dir:
                break
        if not patched_dir:
            raise SystemExit(f"directory record not found for {path}")

        # load table entry in the EXE: unique 8-byte {BCD MSF, size} match
        before = struct.pack("<II", msf_word(entry["lba"]), entry["size"])
        after = struct.pack("<II", msf_word(new_lba), new_size)
        first = exe.find(before)
        if first < 0 or exe.find(before, first + 1) >= 0:
            raise SystemExit(f"load-table entry not unique for {path}")
        exe[first:first + 8] = after
        report["load_table"].append(
            {"path": path, "exe_offset": hex(first),
             "before": before.hex(), "after": after.hex()})

        report["files"].append({
            "path": path, "records": len(entries),
            "old_lba": entry["lba"], "new_lba": new_lba,
            "old_size": entry["size"], "new_size": new_size,
            "section_offset": hex(section_offset),
            "section_tag": f"0x{tag:08X}",
        })

    # ------------------------------------------- stage B4: EXE token records
    exe_records = []
    for index, record in enumerate(records_by_path[EXE_SCOPE]):
        key = (EXE_SCOPE, index)
        if key not in korean:
            report["skipped"].append(
                {"path": EXE_SCOPE, "index": index, "reason": "no translation"})
            continue
        offset = record["offset"] + (5 if record.get("end_code_before") else 0)
        span = token_span(bytes(exe), offset)
        frame = record["start_frame"]
        lines = [normalize_line(l) for l in korean[key].splitlines() if l.strip()]
        try:
            cell_lines, glyphs = build_exe_record(lines, args.font)
        except Exception as exc:
            report["skipped"].append(
                {"path": EXE_SCOPE, "index": index, "reason": str(exc)})
            continue
        new = encode_token(frame, cell_lines)
        if len(new) > span:
            report["skipped"].append(
                {"path": EXE_SCOPE, "index": index,
                 "reason": f"does not fit: {len(new)} > {span}"})
            continue
        exe[offset:offset + span] = new.ljust(span, b"\x00")
        token_addr = EXE_RAM_BASE + offset - EXE_HEADER_SIZE
        exe_records.append({
            "token_addr": token_addr,
            "glyphs": glyphs,
            "font": args.font,
        })
        report["exe_records"].append(
            {"index": index, "token_addr": f"0x{token_addr:08X}",
             "cells": len(glyphs), "bytes": f"{len(new)}/{span}"})

    # ------------------------------------- nameplates (direct renderer calls)
    # Reuse the EXE record machinery: same per-glyph pool upload and template
    # UV patching, keyed on the string address the renderer receives in $a0.
    report["names"] = []
    for addr, (english, korean_name, span) in (
            {} if args.no_names else NAME_STRINGS).items():
        off = EXE_HEADER_SIZE + addr - EXE_RAM_BASE
        current = bytes(exe[off:off + span])
        expected = NAME_PREFIX + english.encode("ascii") + b"\x00"
        if not current.startswith(expected):
            raise SystemExit(f"name mismatch at 0x{addr:08X}: {current!r}")
        cell_lines, glyphs = build_exe_record([korean_name], args.font)
        new = NAME_PREFIX + cell_lines[0].encode("ascii") + b"\x00"
        if len(new) > span:
            raise SystemExit(
                f"name too long at 0x{addr:08X}: {len(new)} > {span}")
        exe[off:off + span] = new.ljust(span, b"\x00")
        # the renderer gets the string address itself as the key
        exe_records.append({"token_addr": addr, "glyphs": glyphs,
                            "font": args.font})
        report["names"].append(
            {"addr": f"0x{addr:08X}", "en": english, "ko": korean_name,
             "cells": len(glyphs), "bytes": f"{len(new)}/{span}"})

    # ------------------------------------------------- pool + wrapper + stub
    pool_blob, pool_addr, pool_meta = build_pool(
        file_infos, exe_records, bytes(exe))

    wrapper = build_wrapper(
        pool_addr, len(exe_records), bytes(exe),
        trace_file_upload=args.trace_file_upload)
    finisher_ram = CAVE_START + len(wrapper)
    ext_len = EXT_STUB_SIZE + len(pool_blob)
    ext_len = (ext_len + 2047) & ~2047                # keep the EXE 2048-multiple
    finisher = build_finisher(finisher_ram, ext_len // 4,
                              struct.unpack_from("<I", exe, 0x10)[0])
    # both stubs live in the pool (the cave is full); assemble them now that
    # build_pool has handed back the addresses they reference
    name_stub_ram = pool_addr["name_stub"]
    name_stub = build_name_stub(name_stub_ram, pool_addr, len(exe_records),
                                do_upload=not args.name_no_upload,
                                do_patch=not args.name_no_patch)
    up_stub = build_upload_stub(pool_addr)
    pool_blob = bytearray(pool_blob)
    for ram, code in (
            (name_stub_ram, name_stub),
            (pool_addr["up_stub"], up_stub)):
        off = ram - POOL_BASE
        pool_blob[off:off + len(code)] = code
    pool_blob = bytes(pool_blob)
    payload = wrapper + finisher
    if CAVE_START + len(payload) > CAVE_END:
        raise ValueError(f"cave overflow: {len(payload)} bytes")
    cave_off = EXE_HEADER_SIZE + CAVE_START - EXE_RAM_BASE
    exe[cave_off:cave_off + len(payload)] = payload

    pool_words = (len(pool_blob) + 3) // 4
    stub = build_boot_stub(pool_words, finisher_ram)

    # hook the four drawer call sites
    stub_jal = 0x0C000000 | ((CAVE_START & 0x0FFFFFFF) >> 2)
    for caller in DRAWER_CALLERS:
        off = EXE_HEADER_SIZE + caller - EXE_RAM_BASE
        actual = struct.unpack_from("<I", exe, off)[0]
        if actual != DRAWER_JAL:
            raise SystemExit(f"drawer call mismatch at 0x{caller:08X}: 0x{actual:08X}")
        struct.pack_into("<I", exe, off, stub_jal)

    # nameplate renderer calls -> name stub
    name_jal = 0x0C000000 | ((name_stub_ram & 0x0FFFFFFF) >> 2)
    for site in (() if args.no_name_stub else NAME_RENDER_SITES):
        off = EXE_HEADER_SIZE + site - EXE_RAM_BASE
        actual = struct.unpack_from("<I", exe, off)[0]
        if actual != NAME_RENDER_JAL:
            raise SystemExit(
                f"name render call mismatch at 0x{site:08X}: 0x{actual:08X}")
        struct.pack_into("<I", exe, off, name_jal)
    report["name_stub"] = f"0x{name_stub_ram:08X}"

    # gallery permanently unlocked: force EVERY UI-side read of the unlock
    # mask [0x800A10D8] to -1.  b3 patched only two of nine access sites --
    # the real per-item gates are the bit tests at 0x80048368/0x800483C4.
    # Left untouched: the unlock setter RMW (0x800477B8) and the save-data
    # pack/unpack pair (0x80063418/0x800634C0), so save data stays honest.
    for site, want, repl in (
        (0x80048268, 0x8C4210D8, 0x2402FFFF),   # lw $v0 -> li $v0, -1
        (0x800484CC, 0x8C4210D8, 0x2402FFFF),
        (0x800481D4, 0x8CA510D8, 0x2405FFFF),   # lw $a1 -> li $a1, -1
        (0x80048298, 0x8C8410D8, 0x2404FFFF),   # lw $a0 -> li $a0, -1
        (0x800484FC, 0x8C8410D8, 0x2404FFFF),
        (0x80048368, 0x8C6310D8, 0x2403FFFF),   # lw $v1 -> li $v1, -1
        (0x800483C4, 0x8C6310D8, 0x2403FFFF),
    ):
        off = EXE_HEADER_SIZE + site - EXE_RAM_BASE
        actual = struct.unpack_from("<I", exe, off)[0]
        if actual != want:
            raise SystemExit(f"unlock read mismatch at 0x{site:08X}: 0x{actual:08X}")
        struct.pack_into("<I", exe, off, repl)

    # extend the EXE image: boot stub + pool, loaded at EXT_RAM
    entry_orig = struct.unpack_from("<I", exe, 0x10)[0]
    t_addr, t_size = struct.unpack_from("<II", exe, 0x18)
    if t_addr + t_size != EXT_RAM:
        raise SystemExit(f"unexpected text end: 0x{t_addr + t_size:08X}")
    extension = (stub.ljust(EXT_STUB_SIZE, b"\x00") + pool_blob).ljust(ext_len, b"\x00")
    exe += extension
    struct.pack_into("<I", exe, 0x10, EXT_RAM)        # entry pc -> boot stub
    struct.pack_into("<I", exe, 0x1C, t_size + ext_len)
    new_exe_size = len(exe) - EXE_HEADER_SIZE + EXE_HEADER_SIZE
    assert len(exe) % 2048 == 0

    # There is NO gap after the EXE: the next extent (BIN1's directory) starts
    # exactly at LBA 315 = 24 + 291 sectors.  So the grown EXE is relocated to
    # the image end like every other file.  The boot loader finds it through
    # the root directory record (SYSTEM.CNF names it), not a load-table entry,
    # so patching that record's LBA *and* size is sufficient.
    old_sectors = (exe_entry["size"] + 2047) // 2048
    new_sectors = len(exe) // 2048
    exe_new_lba = next_lba
    next_lba += new_sectors

    with args.image.open("rb") as src:
        src.seek(exe_entry["lba"] * disc.RAW_SECTOR_SIZE + 16)
        exe_first_sub = src.read(8)
        src.seek((exe_entry["lba"] + old_sectors - 1) * disc.RAW_SECTOR_SIZE + 16)
        exe_last_sub = src.read(8)
    with out_bin.open("r+b") as out:
        out.seek(exe_new_lba * disc.RAW_SECTOR_SIZE)
        for s_index in range(new_sectors):
            chunk = bytes(exe[s_index * 2048:(s_index + 1) * 2048])
            sub = exe_last_sub if s_index == new_sectors - 1 else exe_first_sub
            out.write(make_raw_sector(exe_new_lba + s_index, sub, chunk,
                                      rebuild_mode2_form1_integrity))
    report["exe_relocation"] = {
        "old_lba": exe_entry["lba"], "new_lba": exe_new_lba,
        "old_size": exe_entry["size"], "new_size": len(exe)}

    # root directory record for the EXE: size grows, LBA unchanged
    with args.image.open("rb") as track:
        pvd, _ = disc.read_sector(track, 16)
    root = disc.parse_record(pvd[156:156 + pvd[156]])
    patched_exe_dir = False
    for dir_lba in range(root["lba"], root["lba"] + 4):
        def patch_exe_record(raw: bytearray):
            nonlocal patched_exe_dir
            base = disc.MODE2_FORM1_DATA_OFFSET
            block = raw[base:base + 2048]
            pos = 0
            while pos < 2048:
                length = block[pos]
                if length == 0:
                    break
                lba_le = struct.unpack_from("<I", block, pos + 2)[0]
                size_le = struct.unpack_from("<I", block, pos + 10)[0]
                if lba_le == exe_entry["lba"] and size_le == exe_entry["size"]:
                    struct.pack_into("<I", raw, base + pos + 2, exe_new_lba)
                    struct.pack_into(">I", raw, base + pos + 6, exe_new_lba)
                    struct.pack_into("<I", raw, base + pos + 10, len(exe))
                    struct.pack_into(">I", raw, base + pos + 14, len(exe))
                    patched_exe_dir = True
                pos += length
        patch_logical_sector(out_bin, dir_lba, patch_exe_record)
        if patched_exe_dir:
            break
    if not patched_exe_dir:
        raise SystemExit("EXE directory record not found")

    # PVD volume size (files relocated to the image end)
    final_sectors = out_bin.stat().st_size // disc.RAW_SECTOR_SIZE

    def patch_pvd(raw: bytearray, sectors=final_sectors):
        base = disc.MODE2_FORM1_DATA_OFFSET
        struct.pack_into("<I", raw, base + 80, sectors)
        struct.pack_into(">I", raw, base + 84, sectors)
    patch_logical_sector(out_bin, 16, patch_pvd)

    report["wrapper"] = {
        "stub": f"0x{CAVE_START:08X}",
        "wrapper_size": len(wrapper),
        "finisher": f"0x{finisher_ram:08X}",
        "cave_free": CAVE_END - (CAVE_START + len(payload)),
        "boot_stub": f"0x{EXT_RAM:08X}",
        "entry_orig": f"0x{entry_orig:08X}",
        "ext_len": ext_len,
        "exe_size": len(exe),
    }
    report["pool"] = pool_meta
    report["file_match"] = {
        "header_words": ["K1US", "crc32(source_path)", "record_count"],
        "validated_words": ["magic", "path_tag"],
        "reason": (
            "A V03 record-1 token aliased V02 record 4 when only the shared "
            "K1US magic was checked"
        ),
        "runtime_status": "PENDING",
        "trace_enabled": args.trace_file_upload,
        "trace_address": pool_meta["file_debug"],
    }
    report["hooks"] = [f"0x{c:08X}" for c in DRAWER_CALLERS]
    report["gallery_unlocked"] = [
        "0x80048268", "0x800484CC", "0x800481D4", "0x80048298",
        "0x800484FC", "0x80048368", "0x800483C4"]
    report["risks"] = [
        "The file-unique K1US path-tag match is statically verified but still "
        "requires V03 record-1 runtime QA and adjacent-record regression",
    ]

    cue = args.output_dir / f"{args.base_name}.cue"
    cue.write_text(
        f'FILE "{out_bin.name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n',
        encoding="ascii")
    (args.output_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(f["records"] for f in report["files"])
    print(f"files: {len(report['files'])}  records replaced: {total}  "
          f"exe records: {len(report['exe_records'])}  "
          f"skipped: {len(report['skipped'])}")
    for s in report["skipped"][:8]:
        print("  SKIP", s)
    print(f"pool: {pool_meta['pool_size']}B at {pool_meta['pool_base']}"
          f" .. {pool_meta['pool_end']} ({pool_meta['glyphs_unique']} glyphs)")
    print(f"output: {out_bin}")


if __name__ == "__main__":
    main()
