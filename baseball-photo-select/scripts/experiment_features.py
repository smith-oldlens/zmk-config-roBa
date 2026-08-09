#!/usr/bin/env python3
"""Does anything we already measure predict the owner's picks? (R1 follow-up)

The first real comparison killed the sharpness hypothesis: AUC 0.473 on
2026-07-20, i.e. worse than a coin flip, with keep-bucket precision (14-20%)
at or below the 21.6% base rate at every threshold. With eye-AF hitting on 95%
of frames, focus is simply not what separates the owner's selects — expression
and moment are, and sharpness cannot see either.

Before building anything on embeddings, this checks whether the cheap features
already sitting in the database carry any signal at all:

    sharp      - subject-crop sharpness percentile (known baseline)
    size       - subject box area relative to the frame (how big the player is)
    position   - AF point distance from centre (framing)
    rank       - position within the burst

Cross-validation is by burst, never random: two frames from the same burst are
near-identical, and splitting them across train and test would inflate every
score into meaninglessness.

Usage:
    python3 scripts/experiment_features.py
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bps.config import load_config  # noqa: E402

FEATURE_SETS = {
    "sharp のみ (現状のツール)": ["sharp"],
    "sharp + 被写体サイズ": ["sharp", "size"],
    "sharp + サイズ + 位置": ["sharp", "size", "cx", "cy", "dist"],
    "全部 + burst内順位": ["sharp", "size", "cx", "cy", "dist", "rank", "group_size"],
}


def load_rows(cfg, csv_path: Path, games: list[str]) -> list[dict]:
    import csv as csvmod

    owner: dict[str, bool] = {}
    game_of: dict[str, str] = {}
    with csv_path.open(encoding="utf-8") as fh:
        for row in csvmod.DictReader(fh):
            if row["file_format"] != "JPG":
                continue
            for game in games:
                if game in row["path"]:
                    name = Path(row["path"]).name.upper()
                    owner[name] = owner.get(name, False) or row["rating"] not in ("", "0", "0.0")
                    game_of[name] = game
                    break

    conn = sqlite3.connect(str(cfg.db_path))
    conn.row_factory = sqlite3.Row
    rows: list[dict] = []
    group_sizes: dict[int, int] = {}
    raw = list(
        conn.execute(
            "SELECT orig_name, group_id, scores_json, af_json FROM photos "
            "WHERE scores_json IS NOT NULL"
        )
    )
    conn.close()
    for r in raw:
        group_sizes[r["group_id"]] = group_sizes.get(r["group_id"], 0) + 1

    for r in raw:
        name = r["orig_name"].upper()
        if name not in owner:
            continue
        scores = json.loads(r["scores_json"])
        box = scores.get("subj_box") or [0, 0, 0, 0]
        af = {}
        if r["af_json"]:
            try:
                af = json.loads(r["af_json"])
            except json.JSONDecodeError:
                pass
        ref_w = af.get("ref_width") or 7008
        ref_h = af.get("ref_height") or 4672
        cx = (af.get("x", ref_w / 2) / ref_w) - 0.5
        cy = (af.get("y", ref_h / 2) / ref_h) - 0.5
        rows.append(
            {
                "name": name,
                "game": game_of[name],
                "group": r["group_id"],
                "y": 1 if owner[name] else 0,
                "sharp": scores.get("subj_sharp", 0.0),
                "size": math.sqrt(max(0, box[2] * box[3])) / max(1, ref_w),
                "cx": cx,
                "cy": cy,
                "dist": math.hypot(cx, cy),
                "rank": scores.get("in_group_rank", 0),
                "group_size": group_sizes.get(r["group_id"], 1),
            }
        )
    return rows


def auc(scores: list[float], labels: list[int]) -> float:
    pairs = sorted(zip(scores, labels))
    ranks: list[float] = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    pos_rank_sum = sum(ranks[k] for k, (_, label) in enumerate(pairs) if label == 1)
    return (pos_rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="lrcat_ratings.csv")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument(
        "--games",
        nargs="*",
        default=["2026-05-31", "2026-07-19", "2026-07-20", "2026-07-26"],
    )
    args = ap.parse_args(argv)

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import GroupKFold
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        sys.exit("error: pip install scikit-learn")

    cfg = load_config(args.config)
    rows = load_rows(cfg, Path(args.csv), args.games)
    if len(rows) < 50:
        sys.exit(f"error: only {len(rows)} photos matched — ingest a game first")

    games_present = sorted({r["game"] for r in rows})
    positives = sum(r["y"] for r in rows)
    print(f"データ: {len(rows)} 枚 (セレクト {positives} = {positives/len(rows):.0%})")
    print(f"試合: {', '.join(games_present)}\n")

    # Split by game when we have several, otherwise by burst within the game.
    if len(games_present) > 1:
        groups = [games_present.index(r["game"]) for r in rows]
        n_splits = min(len(games_present), 4)
        split_by = "試合ごと"
    else:
        groups = [r["group"] for r in rows]
        n_splits = 5
        split_by = "連写グループごと"
    print(f"交差検証: {split_by} ({n_splits}分割)\n")

    y = np.array([r["y"] for r in rows])
    print(f"{'特徴量':<28} {'AUC':>6}   判定")
    for label, names in FEATURE_SETS.items():
        X = np.array([[r[n] for n in names] for r in rows], dtype=float)
        held_out_scores = np.zeros(len(rows))
        for train_idx, test_idx in GroupKFold(n_splits=n_splits).split(X, y, groups):
            scaler = StandardScaler().fit(X[train_idx])
            model = LogisticRegression(class_weight="balanced", max_iter=1000)
            model.fit(scaler.transform(X[train_idx]), y[train_idx])
            held_out_scores[test_idx] = model.predict_proba(scaler.transform(X[test_idx]))[:, 1]
        value = auc(list(held_out_scores), list(y))
        verdict = (
            "使える" if value >= 0.70 else "弱い" if value >= 0.60 else "ほぼ無力"
        )
        print(f"{label:<28} {value:6.3f}   {verdict}")

    print(
        "\n0.5 = ランダム。0.70 以上なら実用、0.60 未満なら画像を見る特徴量"
        "(埋め込み)が必須という結論になる。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
