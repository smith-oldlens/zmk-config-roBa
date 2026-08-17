#!/usr/bin/env python3
"""M0 helper: write XMP star ratings to sample JPEGs and verify the readback.

This automates step 1 of tests/manual/m0-e2e-checklist.md. It writes
``XMP-xmp:Rating`` 0 / 3 / 5 (the ratings the pipeline will use, spec 02 6.5)
to three JPEGs so you can drop them into the Lightroom watched folder and
confirm the smart collection surfaces only the 3 / 5 star files.

The star value is written into the XMP-xmp:Rating tag exactly the way the real
pipeline writes it (spec 02 7.3), so a successful Lightroom import here proves
the whole metadata->Lightroom path before any pipeline code exists.

Usage:
    # Generate three synthetic JPEGs and rate them 0/3/5:
    python m0_write_test_stars.py --generate --out-dir ./m0_test

    # Or rate three JPEGs you already have (order = rating 0, 3, 5):
    python m0_write_test_stars.py a.jpg b.jpg c.jpg

Requires: exiftool on PATH (or --exiftool PATH). Pillow only for --generate.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# The three ratings written, in order. 0 = should NOT appear in the
# "AI select" smart collection; 3 and 5 = should appear.
RATINGS = [0, 3, 5]


def find_exiftool(explicit: str | None) -> str:
    exe = explicit or "exiftool"
    if shutil.which(exe) is None:
        sys.exit(
            f"error: exiftool not found ({exe!r}). Install it (Windows: exiftool.org, "
            "rename to exiftool.exe on PATH) or pass --exiftool."
        )
    return exe


def generate_jpegs(out_dir: Path) -> list[Path]:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit("error: --generate needs Pillow (pip install pillow).")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, rating in enumerate(RATINGS):
        img = Image.new("RGB", (900, 600), (60 + 40 * i, 90, 120))
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 60, 840, 540), outline=(255, 255, 255), width=6)
        draw.text((90, 90), f"M0 test  rating={rating}", fill=(255, 255, 255))
        p = out_dir / f"m0_test_{i}_rating{rating}.jpg"
        img.save(p, "JPEG", quality=90)
        paths.append(p)
    return paths


def write_rating(exiftool: str, path: Path, rating: int) -> None:
    subprocess.run(
        [exiftool, "-overwrite_original", f"-XMP-xmp:Rating={rating}", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def read_rating(exiftool: str, path: Path) -> int | None:
    out = subprocess.run(
        [exiftool, "-j", "-n", "-XMP-xmp:Rating", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    if data and "Rating" in data[0]:
        return int(data[0]["Rating"])
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jpegs", nargs="*", help="exactly 3 JPEGs to rate (order = 0, 3, 5)")
    ap.add_argument("--generate", action="store_true", help="create 3 synthetic JPEGs instead")
    ap.add_argument("--out-dir", default="./m0_test", help="output dir for --generate (default ./m0_test)")
    ap.add_argument("--exiftool", help="path to exiftool if not on PATH")
    args = ap.parse_args(argv)

    exiftool = find_exiftool(args.exiftool)

    if args.generate:
        if args.jpegs:
            sys.exit("error: pass either --generate or explicit JPEGs, not both.")
        paths = generate_jpegs(Path(args.out_dir))
    else:
        if len(args.jpegs) != 3:
            sys.exit("error: pass exactly 3 JPEGs, or use --generate.")
        paths = [Path(p) for p in args.jpegs]
        for p in paths:
            if not p.is_file():
                sys.exit(f"error: not found: {p}")

    ok = True
    for path, rating in zip(paths, RATINGS):
        write_rating(exiftool, path, rating)
        got = read_rating(exiftool, path)
        status = "OK" if got == rating else f"MISMATCH (read {got})"
        if got != rating:
            ok = False
        print(f"  {path.name:32}  wrote Rating={rating}  ->  {status}")

    print()
    if ok:
        print("All ratings written and verified.")
        print("Next: drop these files into the Lightroom watched folder (docs/04) and")
        print("confirm the 'AI select' smart collection shows only the rating 3 and 5 files.")
        return 0
    print("Some ratings did not read back. Check exiftool version / file permissions.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
