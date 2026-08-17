#!/usr/bin/env python3
"""Re-ask the question properly, using the embeddings already cached.

Three independent approaches all landed at chance on real selections:

    sharpness           0.473
    cheap features      0.592
    CLIP whole frame    0.538
    CLIP AF crop        0.607

When everything fails equally, the model is usually not the problem — the
question is. "Did the owner select this frame?" bundles together two decisions
of very different nature:

  A. Was this play worth keeping at all?      (contextual, per burst)
  B. Which frame of the burst is the one?     (near-identical candidates)

B is partly a coin flip — three frames 120ms apart are equally good and the
human clicks one. Asking a model to predict that specific click caps the
achievable score no matter how good the features are. So this script measures
A and B separately:

  [A] burst-level: mean embedding per burst -> did the owner keep anything?
  [B] within-burst: among the frames of a kept burst, is the owner's pick
      ranked first? Compared against the 1/burst_size baseline.

Bursts are rebuilt from the capture times already in lrcat_ratings.csv, so no
photo is read again — this runs in seconds off the existing cache.

Usage:
    python3 scripts/experiment_reframe.py
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_GAMES = ["2026-05-31", "2026-07-19", "2026-07-20", "2026-07-26"]
GAP_SECONDS = 2.0


def parse_time(text: str) -> datetime | None:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def load_rows(csv_path: Path, games: list[str]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["file_format"] != "JPG" or row["path"] in seen:
                continue
            game = next((g for g in games if g in row["path"]), None)
            if game is None:
                continue
            stamp = parse_time(row["capture_time"])
            if stamp is None:
                continue
            seen.add(row["path"])
            rows.append(
                {
                    "path": row["path"],
                    "game": game,
                    "time": stamp,
                    "y": 1 if row["rating"] not in ("", "0", "0.0") else 0,
                }
            )
    return rows


def assign_bursts(rows: list[dict]) -> None:
    """Same rule as the pipeline: a gap over GAP_SECONDS starts a new burst."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[r["game"]].append(r)
    burst_id = 0
    for game in sorted(by_game):
        ordered = sorted(by_game[game], key=lambda r: r["time"])
        previous: datetime | None = None
        for r in ordered:
            if previous is None or (r["time"] - previous).total_seconds() > GAP_SECONDS:
                burst_id += 1
            r["burst"] = burst_id
            previous = r["time"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="lrcat_ratings.csv")
    ap.add_argument("--cache", default="embeddings_cache.npz")
    ap.add_argument("--games", nargs="*", default=DEFAULT_GAMES)
    ap.add_argument("--kind", default="af", choices=["af", "whole"])
    args = ap.parse_args(argv)

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        sys.exit(f"error: {exc}")

    cache_path = Path(args.cache)
    if not cache_path.is_file():
        sys.exit(f"error: {cache_path} not found — run experiment_embeddings.py first")
    blob = np.load(cache_path, allow_pickle=True)
    cached = {k: blob[k] for k in blob.files}

    rows = load_rows(Path(args.csv), args.games)
    rows = [r for r in rows if (r["path"] + "|" + args.kind) in cached]
    if len(rows) < 200:
        sys.exit(f"error: only {len(rows)} photos have both a capture time and an embedding")
    assign_bursts(rows)

    games = sorted({r["game"] for r in rows})
    bursts: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        bursts[r["burst"]].append(r)
    print(f"対象 {len(rows)} 枚 / {len(bursts)} バースト / セレクト {sum(r['y'] for r in rows)}")
    sizes = [len(v) for v in bursts.values()]
    kept = [b for b in bursts.values() if any(r["y"] for r in b)]
    print(f"平均バースト {sum(sizes)/len(sizes):.1f} 枚, 中身を1枚でも採用したバースト {len(kept)}/{len(bursts)}")
    multi_single = [b for b in kept if len(b) > 1 and sum(r["y"] for r in b) == 1]
    print(f"「複数枚から1枚だけ選んだ」バースト: {len(multi_single)}\n")

    from experiment_features import auc  # tie-aware

    # --- [A] is this play worth keeping at all? ---------------------------
    burst_ids = sorted(bursts)
    X_a = np.array(
        [np.mean([cached[r["path"] + "|" + args.kind] for r in bursts[b]], axis=0) for b in burst_ids]
    )
    y_a = np.array([1 if any(r["y"] for r in bursts[b]) else 0 for b in burst_ids])
    game_a = np.array([bursts[b][0]["game"] for b in burst_ids])
    scores_a = np.zeros(len(burst_ids))
    for game in games:
        test = game_a == game
        train = ~test
        if not test.any() or not train.any() or not y_a[train].any():
            continue
        scaler = StandardScaler().fit(X_a[train])
        model = LogisticRegression(class_weight="balanced", max_iter=3000, C=0.1)
        model.fit(scaler.transform(X_a[train]), y_a[train])
        scores_a[test] = model.predict_proba(scaler.transform(X_a[test]))[:, 1]
    auc_a = auc(list(scores_a), list(y_a))
    print(f"[A] 「このプレーを残すか」バースト単位の判定")
    print(f"    AUC = {auc_a:.3f}", end="  ")
    print("← 使える" if auc_a >= 0.70 else "← 弱い" if auc_a >= 0.62 else "← 無力")
    order = np.argsort(-scores_a)
    need = int(np.ceil(y_a.sum() * 0.95))
    found = 0
    for seen_count, idx in enumerate(order, start=1):
        found += int(y_a[idx])
        if found >= need:
            print(
                f"    → 採用バーストの95%を拾うには上位 {seen_count}/{len(burst_ids)} バースト "
                f"({seen_count/len(burst_ids):.0%}) を見れば足りる"
            )
            break

    # --- [B] which frame of the burst is the one? -------------------------
    print(f"\n[B] 「バースト内でどれがベストか」")
    if len(multi_single) < 30:
        print("    比較可能なバーストが少なすぎます")
        return 0
    X_b = np.array([cached[r["path"] + "|" + args.kind] for r in rows])
    y_b = np.array([r["y"] for r in rows])
    game_b = np.array([r["game"] for r in rows])
    scores_b = np.zeros(len(rows))
    for game in games:
        test = game_b == game
        train = ~test
        if not test.any() or not train.any():
            continue
        scaler = StandardScaler().fit(X_b[train])
        model = LogisticRegression(class_weight="balanced", max_iter=3000, C=0.1)
        model.fit(scaler.transform(X_b[train]), y_b[train])
        scores_b[test] = model.predict_proba(scaler.transform(X_b[test]))[:, 1]
    index_of = {id(r): i for i, r in enumerate(rows)}

    hits = 0
    baseline = 0.0
    for burst in multi_single:
        ranked = sorted(burst, key=lambda r: -scores_b[index_of[id(r)]])
        if ranked[0]["y"] == 1:
            hits += 1
        baseline += 1 / len(burst)
    accuracy = hits / len(multi_single)
    baseline /= len(multi_single)
    print(f"    正解率 {accuracy:.1%} (ランダムなら {baseline:.1%})", end="  ")
    if accuracy >= baseline * 1.5:
        print("← 有意に効いている")
    elif accuracy >= baseline * 1.2:
        print("← わずかに効いている")
    else:
        print("← 効いていない")

    print(
        "\n判定: [A] が 0.70 以上なら「プレー単位の足切り」を自動化できる。"
        "\n      [B] がランダムの1.5倍以上なら「連写内のベスト選定」を自動化できる。"
        "\n      どちらもダメなら、自動判定ではなく高速レビューUIに切り替える。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
