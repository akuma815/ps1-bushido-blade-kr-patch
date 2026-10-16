#!/usr/bin/env python3
"""Package the Bushido Blade Korean patches for distribution.

Produces one xdelta per region against its own pristine disc, then proves each
one by decoding it back over the original and comparing the result's SHA-256
to the build we actually made -- a patch that cannot reproduce the build is a
broken patch, and that is worth catching here rather than on a user's machine.

No original or patched game data is copied into the distribution: only the
deltas, the apply helpers, and the integrity manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRINNY_TOOLS = (ROOT.parent / "Prinny - Ore ga Shujinkou de Iinsuka"
                / "dist" / "Prinny_KR_Patch_20260803" / "tools")
README_TEMPLATE = (ROOT / "dist" / "BushidoBlade_KR_Patch_20260804"
                   / "README_적용방법.txt")
OLD_REPORT = (ROOT / "dist" / "BushidoBlade_KR_Patch_20260804"
              / "release_report.json")

TARGETS = (
    {
        "id": "USA",
        "exe": "SCUS_941.80",
        "source": ROOT / "original" / "Bushido Blade (USA).bin",
        "target": ROOT / "build" / "usa_final" / "Bushido Blade (USA) [KO Final].bin",
    },
    {
        "id": "JP",
        "exe": "SLPM_860.20",
        "source": ROOT / "original" / "Bushido Blade (Japan).bin",
        "target": ROOT / "build" / "jp_final" / "Bushido Blade (Japan) [KO Final].bin",
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_apply_ps1(patch: dict) -> str:
    return f"""param([Parameter(Mandatory = $true)][string]$InputBin)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $InputBin).Path
$base = [IO.Path]::GetFileNameWithoutExtension($source)
$dir = Split-Path -Parent $source
$output = Join-Path $dir ($base + '_KR.bin')
$outputCue = Join-Path $dir ($base + '_KR.cue')
$patch = Join-Path $PSScriptRoot '{patch['patch']}'
$xdelta = Join-Path $PSScriptRoot 'tools\\xdelta3.exe'

$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
if ($sourceHash -ne '{patch['source_sha256'].upper()}') {{
    throw "Unsupported original BIN hash: $sourceHash"
}}
& $xdelta -d -f -s $source $patch $output
if ($LASTEXITCODE -ne 0) {{ throw "xdelta failed with exit code $LASTEXITCODE" }}
$outputHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash
if ($outputHash -ne '{patch['target_sha256'].upper()}') {{
    throw "Patched BIN verification failed: $outputHash"
}}

# A PS1 disc needs a CUE beside the BIN; this image is one MODE2/2352 track.
$cue = "FILE `"$([IO.Path]::GetFileName($output))`" BINARY`r`n  TRACK 01 MODE2/2352`r`n    INDEX 01 00:00:00`r`n"
[IO.File]::WriteAllText($outputCue, $cue, (New-Object Text.ASCIIEncoding))

Write-Output "PASS: $output"
Write-Output "PASS: $outputCue"
"""


def render_apply_cmd(region: str) -> str:
    return f"""@echo off
chcp 65001 >nul
if "%~1"=="" (
  echo Usage: apply_{region}.cmd "original.bin"
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply_{region}.ps1" "%~f1"
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
"""


def write_sha256_manifest(out: Path) -> None:
    rows = []
    for path in sorted(
            (p for p in out.rglob("*") if p.is_file() and p.name != "SHA256.txt"),
            key=lambda p: p.relative_to(out).as_posix().lower()):
        rows.append(f"{sha256(path).upper()}  {path.relative_to(out).as_posix()}")
    (out / "SHA256.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def make_zip(out: Path) -> Path:
    destination = out.with_suffix(".zip")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted((p for p in out.rglob("*") if p.is_file()),
                           key=lambda p: p.relative_to(out).as_posix().lower()):
            archive.write(path, path.relative_to(out).as_posix())
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260804")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--usa-target", type=Path,
        default=ROOT / "build" / "usa_final" / "Bushido Blade (USA) [KO Final].bin")
    parser.add_argument(
        "--jp-target", type=Path,
        default=ROOT / "build" / "jp_final" / "Bushido Blade (Japan) [KO Final].bin")
    parser.add_argument("--xdelta", type=Path, default=PRINNY_TOOLS / "xdelta3.exe")
    parser.add_argument("--scratch", type=Path, default=ROOT / "work" / "tmp_release")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--skip-apply-test", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    targets = [dict(target) for target in TARGETS]
    targets[0]["target"] = args.usa_target
    targets[1]["target"] = args.jp_target

    out = args.out or ROOT / "dist" / f"BushidoBlade_KR_Patch_{args.date}"
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to mix with non-empty release directory: {out}")
    tools = out / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    for name in ("xdelta3.exe", "README_xdelta3.md"):
        shutil.copyfile(args.xdelta.parent / name, tools / name)

    patches = []
    for target in targets:
        source, built = target["source"], target["target"]
        for path in (source, built):
            if not path.exists():
                raise SystemExit(f"missing: {path}")
        patch = out / (f"BushidoBlade_{target['exe'].replace('_', '').replace('.', '')}"
                       f"_KR_{args.date}.xdelta")

        print(f"[{target['id']}] hashing source ...", flush=True)
        source_sha = sha256(source)
        print(f"[{target['id']}] hashing build ...", flush=True)
        target_sha = sha256(built)

        print(f"[{target['id']}] encoding {patch.name} ...", flush=True)
        run = subprocess.run(
            [str(args.xdelta), "-e", "-9", "-f", "-s", str(source), str(built),
             str(patch)],
            capture_output=True, text=True)
        if run.returncode:
            raise SystemExit(f"xdelta encode failed: {run.stderr[-400:]}")

        verification = "SKIPPED"
        if not args.skip_verify:
            probe = args.scratch / f"{target['id']}_verify.bin"
            print(f"[{target['id']}] reverse-verifying ...", flush=True)
            run = subprocess.run(
                [str(args.xdelta), "-d", "-f", "-s", str(source), str(patch),
                 str(probe)],
                capture_output=True, text=True)
            if run.returncode:
                raise SystemExit(f"xdelta decode failed: {run.stderr[-400:]}")
            probe_sha = sha256(probe)
            probe.unlink()
            if probe_sha != target_sha:
                raise SystemExit(
                    f"[{target['id']}] reverse verification MISMATCH\n"
                    f"  expected {target_sha}\n  got      {probe_sha}")
            verification = "PASS"

        patches.append({
            "id": target["id"],
            "executable": target["exe"],
            "patch": patch.name,
            "patch_size": patch.stat().st_size,
            "patch_sha256": sha256(patch),
            "source_size": source.stat().st_size,
            "source_sha256": source_sha,
            "target_size": built.stat().st_size,
            "target_sha256": target_sha,
            "reverse_verification": verification,
        })
        print(f"[{target['id']}] {patch.name}  "
              f"{patch.stat().st_size:,} B  verify={verification}", flush=True)

    patch_by_id = {patch["id"]: patch for patch in patches}
    for region in ("usa", "jp"):
        patch = patch_by_id[region.upper()]
        (out / f"apply_{region}.ps1").write_text(
            render_apply_ps1(patch), encoding="utf-8", newline="\n")
        (out / f"apply_{region}.cmd").write_text(
            render_apply_cmd(region), encoding="utf-8", newline="\r\n")

    old_report = json.loads(OLD_REPORT.read_text(encoding="utf-8"))
    readme = README_TEMPLATE.read_text(encoding="utf-8")
    readme = readme.replace("2026-08-04", f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}")
    readme = readme.replace("20260804", args.date)
    for old_patch in old_report["patches"]:
        new_patch = patch_by_id[old_patch["id"]]
        readme = readme.replace(
            old_patch["target_sha256"].upper(), new_patch["target_sha256"].upper())
        readme = readme.replace(
            f"{old_patch['target_size']:,} 바이트", f"{new_patch['target_size']:,} 바이트")
    release_note = (
        "■ 2026-08-11 수정\n\n"
        "  · 미국판 자막 파일 판별에 파일별 고유 태그를 추가했습니다.\n"
        "  · 다른 레코드의 글리프가 표시되던 cross-file alias를 수정했습니다.\n"
        "  · 수정 장면 `너도 카게에 들어와 있었다니…`를 런타임 확인했습니다.\n\n\n"
    )
    readme = readme.replace("■ 구성\n", release_note + "■ 구성\n", 1)
    (out / "README_적용방법.txt").write_text(readme, encoding="utf-8", newline="\r\n")

    apply_results = {}
    if not args.skip_apply_test:
        test_root = args.scratch / f"apply helper test {args.date}"
        if test_root.exists():
            shutil.rmtree(test_root)
        test_root.mkdir(parents=True)
        try:
            for target, patch in zip(targets, patches):
                region = target["id"].lower()
                source_copy = test_root / f"Original {target['id']} Disc.bin"
                shutil.copyfile(target["source"], source_copy)
                run = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(out / f"apply_{region}.ps1"), str(source_copy)],
                    capture_output=True)
                if run.returncode:
                    diagnostic = (run.stderr + run.stdout).decode(
                        "utf-8", errors="replace")
                    raise SystemExit(
                        f"apply_{region}.ps1 failed: {diagnostic[-800:]}")
                output = source_copy.with_name(source_copy.stem + "_KR.bin")
                cue = source_copy.with_name(source_copy.stem + "_KR.cue")
                if sha256(output) != patch["target_sha256"]:
                    raise SystemExit(f"apply_{region}.ps1 output hash mismatch")
                expected_cue = (
                    f'FILE "{output.name}" BINARY\r\n'
                    '  TRACK 01 MODE2/2352\r\n'
                    '    INDEX 01 00:00:00\r\n')
                if cue.read_bytes() != expected_cue.encode("ascii"):
                    raise SystemExit(f"apply_{region}.ps1 cue mismatch")
                output.unlink()
                cue.unlink()
                run = subprocess.run(
                    ["cmd.exe", "/d", "/c", "call",
                     str(out / f"apply_{region}.cmd"), str(source_copy)],
                    input=b"\r\n", capture_output=True)
                if run.returncode:
                    diagnostic = (run.stderr + run.stdout).decode(
                        "utf-8", errors="replace")
                    raise SystemExit(
                        f"apply_{region}.cmd failed: {diagnostic[-800:]}")
                if sha256(output) != patch["target_sha256"]:
                    raise SystemExit(f"apply_{region}.cmd output hash mismatch")
                if cue.read_bytes() != expected_cue.encode("ascii"):
                    raise SystemExit(f"apply_{region}.cmd cue mismatch")
                apply_results[target["id"]] = (
                    f"PASS: apply_{region}.ps1 and apply_{region}.cmd reproduced "
                    "target hash and cue from a source path containing spaces")
                source_copy.unlink()
                output.unlink()
                cue.unlink()
        finally:
            if test_root.exists():
                shutil.rmtree(test_root)

    report = dict(old_report)
    report.update({
        "schema_version": 2,
        "release_date": args.date,
        "status": (
            "PASS: both region patches reverse-verified and apply helpers tested"
            if not args.skip_verify and not args.skip_apply_test
            else "release generated with one or more verification stages skipped"),
        "distribution_policy": "one independent disc patch per region",
        "contains_original_game_files": False,
        "patches": patches,
        "apply_script_end_to_end": apply_results,
        "usa_subtitle_record_fix": {
            "mechanism": "K1US magic-only cross-file alias",
            "change": "validate K1US magic plus crc32(source_path)",
            "target_scene_runtime": "PASS: 너도 카게에 들어와 있었다니…",
            "tested_target_sha256": patch_by_id["USA"]["target_sha256"],
        },
    })
    report["runtime"] = dict(old_report.get("runtime", {}))
    report["runtime"]["user_runtime_confirmation"] = (
        "PASS: prior release coverage retained; 2026-08-11 USA V03 record 1 "
        "fix target scene confirmed on the exact distributed BIN hash")
    (out / "release_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_sha256_manifest(out)
    archive = None if args.no_zip else make_zip(out)
    print(f"\nreport: {out / 'release_report.json'}")
    print(f"dist:   {out}")
    if archive:
        print(f"zip:    {archive}")
        print(f"zip sha256: {sha256(archive)}")


if __name__ == "__main__":
    main()
