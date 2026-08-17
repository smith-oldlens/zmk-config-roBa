#!/usr/bin/env python3
"""Compare the tool's verdicts against the owner's Lightroom ratings (R1).

After `bps ingest <game folder> --no-deliver` has scored a game, and
`export_lrcat_ratings.py` has produced lrcat_ratings.csv, this prints the
confusion matrix between:

  * the tool: 採用 (keep/moment), 除外 (reject/Purple), 要確認 (review/Yellow)
  * the owner: star (selected in Lightroom) vs no-star

The number that matters most is 見逃し (owner selected it, tool confidently
rejected it) — the design goal is zero, because a wrongly rejected photo is
never seen again, while everything else just costs review time.

Usage:
    python3 scripts/compare_with_lrcat.py --game 2026-07-20
    python3 scripts/compare_with_lrcat.py --game 2026-07-20 --csv lrcat_ratings.csv -c config.yaml
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bps.config import load_config  # noqa: E402

BUCKET_KEEP = "採用"
BUCKET_REJECT = "除外"
BUCKET_REVIEW = "要確認"


def owner_ratings(csv_path: Path, game: str) -> dict[str, bool]:
    """filename -> owner selected? — JPEG rows of one game folder only.

    The M0/R1 check confirmed stars live on the JPG rows; RAW rows are
    unrated shadows of the same frames and would double-count as negatives.
    """
    selected: dict[str, bool] = {}
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if game not in row["path"] or row["file_format"] != "JPG":
                continue
            name = Path(row["path"]).name.upper()
            has_star = row["rating"] not in ("", "0", "0.0")
            selected[name] = selected.get(name, False) or has_star
    return selected


def tool_buckets(db_path: Path, cfg) -> dict[str, str]:
    """orig_name -> tool bucket, for every scored photo in the pipeline DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    buckets: dict[str, str] = {}
    for row in conn.execute(
        "SELECT orig_name, rating, label FROM photos "
        "WHERE state IN ('SCORED', 'WRITTEN', 'DELIVERED')"
    ):
        rating = row["rating"] if row["rating"] is not None else 0
        if row["label"] == cfg.deliver.label_review:
            bucket = BUCKET_REVIEW
        elif row["label"] == cfg.deliver.label_reject:
            bucket = BUCKET_REJECT
        elif int(rating) >= cfg.ratings.keep:
            bucket = BUCKET_KEEP
        else:
            bucket = BUCKET_REVIEW  # unrated with no label: treat as review
        buckets[row["orig_name"].upper()] = bucket
    conn.close()
    return buckets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game", required=True, help="folder substring, e.g. 2026-07-20")
    ap.add_argument("--csv", default="lrcat_ratings.csv", help="output of export_lrcat_ratings.py")
    ap.add_argument("-c", "--config", default="config.yaml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if not cfg.db_path.is_file():
        sys.exit(f"error: pipeline DB not found at {cfg.db_path} — run `bps ingest` first")
    csv_path = Path(args.csv)
    if not csv_path.is_file():
        sys.exit(f"error: {csv_path} not found — run export_lrcat_ratings.py first")

    owner = owner_ratings(csv_path, args.game)
    tool = tool_buckets(cfg.db_path, cfg)
    if not owner:
        sys.exit(f"error: no JPG rows matching {args.game!r} in {csv_path}")
    if not tool:
        sys.exit("error: no scored photos in the pipeline DB")

    matched = sorted(set(owner) & set(tool))
    only_owner = len(set(owner) - set(tool))
    only_tool = len(set(tool) - set(owner))

    grid: Counter = Counter()
    for name in matched:
        grid[(tool[name], owner[name])] += 1

    total = len(matched)
    print(f"突合: {total} 枚 (カタログのみ {only_owner}, ツールのみ {only_tool})\n")
    print(f"{'ツール判定':<8} {'あなた=セレクト':>12} {'あなた=非セレクト':>14}")
    for bucket in (BUCKET_KEEP, BUCKET_REVIEW, BUCKET_REJECT):
        star = grid[(bucket, True)]
        nostar = grid[(bucket, False)]
        print(f"  {bucket:<8} {star:>10} {nostar:>14}")

    missed = grid[(BUCKET_REJECT, True)]
    caught = grid[(BUCKET_KEEP, True)]
    review_count = grid[(BUCKET_REVIEW, True)] + grid[(BUCKET_REVIEW, False)]
    owner_total = sum(1 for v in owner.values() if v)
    decided = total - review_count

    print()
    print(f"  見逃し(あなたのセレクトをツールが除外): {missed} 枚"
          + ("  ← 目標ゼロ" if missed else "  ← 達成"))
    if owner_total:
        print(f"  あなたのセレクト {owner_total} 枚のうち、ツールも採用: {caught} ({caught / owner_total:.0%})")
    print(f"  要確認バケツ: {review_count} 枚 ({review_count / total:.0%}) — あなたが見るのはここだけ")
    print(f"  自動確定: {decided} 枚 ({decided / total:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
