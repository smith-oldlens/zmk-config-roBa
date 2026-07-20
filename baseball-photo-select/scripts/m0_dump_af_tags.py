#!/usr/bin/env python3
"""M0 helper: dump Sony AF metadata from real photos and report the AF-point tags.

Automates step 4 of tests/manual/m0-e2e-checklist.md. For each real Sony JPEG
(ideally shot AF-C on a person, in Wide / Tracking / Spot modes) it runs

    exiftool -j -G -Sony:All -Composite:All <file>

saves the full JSON under docs/af-tag-samples/, and then reports which
AF-point tags are present and their values so you can fill in
config.example.yaml -> af.tag_names for the sony_a7c2 profile.

The tags we care about (spec 02 7.2, docs/05 3) in priority order:
    MakerNotes:FocusLocation        (0x2027, "W H X Y", 2015+ generation)
    MakerNotes:FocusLocation2       (0x204a, a9 III / 2023+)
    MakerNotes:FlexibleSpotPosition (older / spot-AF)
    MakerNotes:FocalPlaneAFPointLocation

Reminder (spec 02 7.2): Sony writes the IMAGE CENTRE into FocusLocation when no
AF point is available. This tool flags any FocusLocation whose X,Y sits within
1%% of (W/2, H/2) as "center_suspect" so you know that frame gives no usable AF.

Usage:
    python m0_dump_af_tags.py path/to/DSC0001.JPG [more.jpg ...]
    python m0_dump_af_tags.py --samples-dir ../docs/af-tag-samples *.JPG
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

AF_TAGS_PRIORITY = [
    "MakerNotes:FocusLocation",
    "MakerNotes:FocusLocation2",
    "MakerNotes:FlexibleSpotPosition",
    "MakerNotes:FocalPlaneAFPointLocation",
]
CENTER_TOL = 0.01  # within 1% of image centre => AF point is the "no data" fallback


def find_exiftool(explicit: str | None) -> str:
    exe = explicit or "exiftool"
    if shutil.which(exe) is None:
        sys.exit(f"error: exiftool not found ({exe!r}). Install it or pass --exiftool.")
    return exe


def dump_tags(exiftool: str, path: Path) -> dict:
    out = subprocess.run(
        [exiftool, "-j", "-G", "-Sony:All", "-Composite:All", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    return data[0] if data else {}


def parse_focus_location(value) -> tuple[int, int, int, int] | None:
    """FocusLocation is 'W H X Y' (may arrive as str or list). Returns ints or None."""
    if isinstance(value, (list, tuple)):
        nums = [int(v) for v in value]
    else:
        nums = [int(n) for n in re.findall(r"-?\d+", str(value))]
    if len(nums) >= 4:
        return nums[0], nums[1], nums[2], nums[3]
    return None


def center_suspect(w: int, h: int, x: int, y: int) -> bool:
    if w <= 0 or h <= 0:
        return False
    return abs(x - w / 2) <= w * CENTER_TOL and abs(y - h / 2) <= h * CENTER_TOL


def report(tags: dict) -> list[str]:
    """Return human-readable lines about the AF tags found in one file's tag dump."""
    lines: list[str] = []
    found_any = False
    for tag in AF_TAGS_PRIORITY:
        if tag in tags:
            found_any = True
            val = tags[tag]
            note = ""
            if tag in ("MakerNotes:FocusLocation", "MakerNotes:FocusLocation2"):
                parsed = parse_focus_location(val)
                if parsed:
                    w, h, x, y = parsed
                    if center_suspect(w, h, x, y):
                        note = "  <- center_suspect (AF point == image centre; treat as no AF)"
            lines.append(f"    {tag} = {val!r}{note}")
    if not found_any:
        lines.append("    (no known AF-point tags found — check camera AF mode / firmware)")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jpegs", nargs="+", help="one or more real Sony JPEGs")
    ap.add_argument(
        "--samples-dir",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "af-tag-samples"),
        help="where to save the full JSON dumps (default docs/af-tag-samples)",
    )
    ap.add_argument("--exiftool", help="path to exiftool if not on PATH")
    args = ap.parse_args(argv)

    exiftool = find_exiftool(args.exiftool)
    samples_dir = Path(args.samples_dir)
    samples_dir.mkdir(parents=True, exist_ok=True)

    model_seen: set[str] = set()
    for jpeg in args.jpegs:
        path = Path(jpeg)
        if not path.is_file():
            print(f"skip (not found): {path}")
            continue
        tags = dump_tags(exiftool, path)
        model = str(tags.get("EXIF:Model") or tags.get("Model") or "unknown")
        model_seen.add(model)

        json_path = samples_dir / f"{path.stem}.json"
        json_path.write_text(json.dumps(tags, indent=2, ensure_ascii=False), encoding="utf-8")

        af_area = tags.get("MakerNotes:AFAreaMode") or tags.get("MakerNotes:AFAreaModeSetting") or "?"
        print(f"{path.name}  (model={model}, AFAreaMode={af_area})  -> {json_path.name}")
        for line in report(tags):
            print(line)
        print()

    print("Saved full dumps to:", samples_dir)
    print("Next steps:")
    print("  1. Pick the highest-priority AF tag that is present with real (non-center) values.")
    print("  2. Put it first in config.example.yaml -> af.tag_names.")
    print("  3. Record the confirmed tag name/format in docs/OPEN_QUESTIONS.md (M0 checklist).")
    if len(model_seen) > 1:
        print("  note: multiple camera models seen:", ", ".join(sorted(model_seen)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
