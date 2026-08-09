#!/usr/bin/env python3
"""Diagnose why the tool's verdicts differ from the owner's, and sweep thresholds.

The first real comparison (2026-07-20) showed the tool marking 63% of a game
"keep" while the owner selected 22%, plus five confident rejects the owner had
actually selected. Before touching thresholds we need to know whether the score
can separate the owner's picks at all — if it cannot, no threshold helps and
the answer is the trained classifier (Phase 3), not tuning.

Prints:
  1. burst size distribution — tiny groups mean "best of group" alone floods
     the keep bucket
  2. score separation between the owner's selects and the rest, including AUC
     (0.5 = the score is noise, 0.7+ = tuning is worth doing)
  3. the misses in detail, so we can see whether they are rescuable
  4. a threshold sweep showing what each setting would cost and save

Usage:
    python3 scripts/diagnose_scores.py --game 2026-07-20
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bps.config import load_config  # noqa: E402


def load_owner(csv_path: Path, game: str) -> dict[str, bool]:
    owner: dict[str, bool] = {}
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if game not in row["path"] or row["file_format"] != "JPG":
                continue
            name = Path(row["path"]).name.upper()
            owner[name] = owner.get(name, False) or row["rating"] not in ("", "0", "0.0")
    return owner


def load_tool(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = []
    for row in conn.execute(
        "SELECT orig_name, group_id, rating, label, scores_json, af_json FROM photos "
        "WHERE scores_json IS NOT NULL"
    ):
        try:
            scores = json.loads(row["scores_json"])
        except json.JSONDecodeError:
            continue
        af = {}
        if row["af_json"]:
            try:
                af = json.loads(row["af_json"])
            except json.JSONDecodeError:
                pass
        rows.append(
            {
                "name": row["orig_name"].upper(),
                "group_id": row["group_id"],
                "rating": row["rating"],
                "label": row["label"],
                "sharp_pct": scores.get("subj_sharp", 0.0),
                "sharp_raw": scores.get("sharp_raw", 0.0),
                "rank": scores.get("in_group_rank", 0),
                "source": scores.get("subject_source", ""),
                "has_af": bool(af) and not af.get("center_suspect"),
            }
        )
    conn.close()
    return rows


def auc(positives: list[float], negatives: list[float]) -> float:
    """Probability a random positive scores above a random negative (rank-based)."""
    if not positives or not negatives:
        return float("nan")
    merged = sorted([(v, 1) for v in positives] + [(v, 0) for v in negatives])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        average_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = average_rank
        i = j + 1
    positive_rank_sum = sum(ranks[k] for k, (_, label) in enumerate(merged) if label == 1)
    n_pos, n_neg = len(positives), len(negatives)
    return (positive_rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game", required=True)
    ap.add_argument("--csv", default="lrcat_ratings.csv")
    ap.add_argument("-c", "--config", default="config.yaml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    owner = load_owner(Path(args.csv), args.game)
    tool = load_tool(cfg.db_path)
    joined = [r for r in tool if r["name"] in owner]
    if not joined:
        sys.exit("error: no overlap between the pipeline DB and the catalog CSV")
    for r in joined:
        r["selected"] = owner[r["name"]]

    total = len(joined)
    picks = [r for r in joined if r["selected"]]
    rest = [r for r in joined if not r["selected"]]
    print(f"=== {args.game}: {total} 枚 (あなたのセレクト {len(picks)} / 非セレクト {len(rest)}) ===\n")

    # 1. burst sizes
    sizes = Counter(Counter(r["group_id"] for r in joined).values())
    groups = sum(sizes.values())
    print(f"[1] 連写グループ: {groups} 個 (平均 {total / groups:.1f} 枚)")
    for size in sorted(sizes):
        share = sizes[size] * size / total
        print(f"    {size:2d}枚のグループ: {sizes[size]:4d} 個  (全体の {share:.0%})")
    singles = sizes.get(1, 0)
    print(f"    → 単写(1枚)グループが {singles} 個。ここは比較相手がなく判定が弱い\n")

    # 2. separation
    pos = [r["sharp_pct"] for r in picks]
    neg = [r["sharp_pct"] for r in rest]
    score_auc = auc(pos, neg)
    print("[2] シャープネス(パーセンタイル)の分離度")
    print(f"    あなたのセレクト  : 中央値 {sorted(pos)[len(pos)//2]:.2f}")
    print(f"    非セレクト        : 中央値 {sorted(neg)[len(neg)//2]:.2f}")
    print(f"    AUC = {score_auc:.3f}", end="  ")
    if score_auc < 0.60:
        print("← ほぼ無力。閾値調整では解決せず、学習モデル(Phase 3)が必要")
    elif score_auc < 0.70:
        print("← 弱い。閾値調整で多少改善するが学習が本命")
    else:
        print("← 有効。閾値調整の価値が大きい")
    af_share = sum(1 for r in joined if r["has_af"]) / total
    print(f"    AF情報が使えたフレーム: {af_share:.0%}\n")

    # 3. misses
    misses = [r for r in picks if r["label"] == cfg.deliver.label_reject]
    print(f"[3] 見逃し {len(misses)} 枚 (あなたのセレクトをツールが除外)")
    for r in sorted(misses, key=lambda r: r["sharp_pct"]):
        print(
            f"    {r['name']:20} sharp_pct={r['sharp_pct']:.2f} "
            f"raw={r['sharp_raw']:.2f} group内{r['rank']}位 AF={'有' if r['has_af'] else '無'}"
        )
    print()

    # 4. threshold sweep
    print("[4] 閾値スイープ (現在: reject_pct=%.2f, keeper_pct=%.2f)"
          % (cfg.sharpness.reject_pct, cfg.sharpness.keeper_pct))
    print("    reject keeper | 見逃し 採用(内 誤) 要確認 | 採用の精度")
    for reject_pct in (0.00, 0.05, 0.10, 0.15):
        for keeper_pct in (0.50, 0.70, 0.85, 0.95):
            keep = review = miss = keep_wrong = 0
            for r in joined:
                best = r["rank"] == 1
                # Mirror composite.decide_ratings with these thresholds.
                if best and r["sharp_pct"] >= keeper_pct:
                    bucket = "keep"
                elif not best and r["sharp_pct"] >= keeper_pct:
                    bucket = "keep"
                elif r["sharp_pct"] < reject_pct:
                    bucket = "reject"
                else:
                    bucket = "review"
                if bucket == "keep":
                    keep += 1
                    if not r["selected"]:
                        keep_wrong += 1
                elif bucket == "review":
                    review += 1
                elif r["selected"]:
                    miss += 1
            precision = (keep - keep_wrong) / keep if keep else 0.0
            print(
                f"    {reject_pct:5.2f} {keeper_pct:6.2f} | {miss:5d} {keep:5d}({keep_wrong:4d}) "
                f"{review:6d} | {precision:.0%}"
            )
    print("\n    「採用の精度」= 採用のうち実際にあなたが選んだ割合。")
    print("    これが高くないと『採用は見なくていい』が成立しない。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
