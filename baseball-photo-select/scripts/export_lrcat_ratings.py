#!/usr/bin/env python3
"""Export photo ratings from a Lightroom Classic catalog (.lrcat) to CSV.

The owner's five games of already-rated photos ARE the training and
calibration data (docs/06 R1/R6): their stars encode the selection standard
(0 = not selected, 1 = selected, 2-5 = own child / important). This script
pulls (path, rating, pick, label) out of the catalog so the pipeline's output
can be scored against the owner's real decisions.

Safety: the catalog is copied to a temporary file before opening — the
original is never touched, and the copy works even while Lightroom is running
(the copy may then lag the live catalog by a few edits; close LR for exact
numbers).

Usage:
    python export_lrcat_ratings.py ~/Pictures/Lightroom/MyCatalog.lrcat
    python export_lrcat_ratings.py MyCatalog.lrcat --out ratings.csv

The summary printed at the end (counts per rating, per folder) is what to
share when discussing calibration — it contains no photo content, only
filenames and stars.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

QUERY = """
SELECT
    rf.absolutePath  AS root_path,
    fo.pathFromRoot  AS folder_path,
    lf.idx_filename  AS filename,
    i.rating         AS rating,
    i.pick           AS pick,
    i.colorLabels    AS color_label,
    i.fileFormat     AS file_format,
    i.captureTime    AS capture_time
FROM Adobe_images i
JOIN AgLibraryFile lf       ON i.rootFile  = lf.id_local
JOIN AgLibraryFolder fo     ON lf.folder   = fo.id_local
JOIN AgLibraryRootFolder rf ON fo.rootFolder = rf.id_local
ORDER BY rf.absolutePath, fo.pathFromRoot, lf.idx_filename
"""


def export_ratings(lrcat: Path, out_csv: Path) -> list[dict]:
    """Read every image's rating from the catalog and write a CSV. Returns rows."""
    if not lrcat.is_file():
        sys.exit(f"error: catalog not found: {lrcat}")

    with tempfile.TemporaryDirectory() as tmp:
        working_copy = Path(tmp) / lrcat.name
        shutil.copy2(lrcat, working_copy)
        # -wal/-shm hold recent writes when LR is (or was) open; copy if present.
        for suffix in (".lrcat-wal", ".lrcat-shm"):
            side = lrcat.with_suffix(suffix)
            if side.is_file():
                shutil.copy2(side, Path(tmp) / side.name)

        conn = sqlite3.connect(str(working_copy))
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute(QUERY)]
        except sqlite3.OperationalError as exc:
            sys.exit(
                f"error: could not read catalog ({exc}).\n"
                "The lrcat schema differs between LR versions; report the LR "
                "version and this message."
            )
        finally:
            conn.close()

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "path",
                "rating",
                "pick",
                "color_label",
                "file_format",
                "capture_time",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "path": f"{row['root_path']}{row['folder_path']}{row['filename']}",
                    "rating": row["rating"] if row["rating"] is not None else "",
                    "pick": row["pick"],
                    "color_label": row["color_label"] or "",
                    "file_format": row["file_format"],
                    "capture_time": row["capture_time"] or "",
                }
            )
    return rows


def print_summary(rows: list[dict]) -> None:
    total = len(rows)
    print(f"\n{total} photo(s) in catalog")

    by_rating = Counter(
        "unrated" if r["rating"] is None else f"★{int(r['rating'])}" for r in rows
    )
    print("\nBy rating:")
    for key in sorted(by_rating, key=lambda k: (k == "unrated", k)):
        print(f"  {key:8} {by_rating[key]:6d}")

    by_format = Counter(r["file_format"] for r in rows)
    print("\nBy format: " + ", ".join(f"{k}={v}" for k, v in sorted(by_format.items())))

    # Per-folder rating mix: shows which shoots are fully rated (usable as
    # training data) and which were never culled.
    print("\nPer folder (top-level):")
    folders: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        top = (r["folder_path"] or "").split("/")[0] or "(root)"
        key = "?" if r["rating"] is None else int(r["rating"])
        folders[top][key] += 1
    for folder in sorted(folders):
        counts = folders[folder]
        mix = ", ".join(
            f"★{k}={counts[k]}" for k in sorted(c for c in counts if c != "?")
        )
        unrated = f", unrated={counts['?']}" if counts["?"] else ""
        print(f"  {folder:40.40} {sum(counts.values()):5d} photos  ({mix}{unrated})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("lrcat", help="path to the .lrcat file")
    ap.add_argument("--out", default="lrcat_ratings.csv", help="output CSV path")
    args = ap.parse_args(argv)

    rows = export_ratings(Path(args.lrcat).expanduser(), Path(args.out))
    print_summary(rows)
    print(f"\nWrote {args.out}")
    print("Share the summary above (not the CSV) when discussing calibration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
