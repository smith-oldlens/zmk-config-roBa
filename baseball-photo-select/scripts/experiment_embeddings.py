#!/usr/bin/env python3
"""The decisive experiment: can an image embedding learn the owner's selections?

Everything cheap has now failed on real data (2026-07-20, 439 frames, 95
selects):

    sharpness alone           AUC 0.473  (below chance)
    + subject size            AUC 0.517
    + AF framing position     AUC 0.592
    + burst rank              AUC 0.577

Eye-AF lands focus on 95% of frames, so "is it sharp" no longer separates
anything — the owner selects on expression and moment, which only a model that
looks at the picture can see. This script settles whether that works, before
any of it is built into the pipeline.

It reads the photos straight from their Lightroom locations (no ingest needed),
embeds each one with CLIP, and trains a linear classifier with
**leave-one-game-out** validation: train on three games, predict the fourth.
Anything less would leak near-identical burst frames between train and test.

Two variants are compared, because a player is small in a wide baseball frame:
  whole  - the full frame
  af     - a crop around the camera's AF point (the tracked player)

Finally it reports the number that actually matters: to catch 95% of the
owner's picks, how many photos would they still have to look at?

Setup (one-off, ~1GB):
    pip install open_clip_torch pillow

Usage:
    python3 scripts/experiment_embeddings.py
    python3 scripts/experiment_embeddings.py --limit 400   # quick smoke test
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_GAMES = ["2026-05-31", "2026-07-19", "2026-07-20", "2026-07-26"]
DECODE_TARGET = 1400  # draft-decode to about this long edge: fast but crop-able
AF_CROP_FRACTION = 0.25  # of the short edge, centred on the AF point


def load_manifest(csv_path: Path, games: list[str], limit: int | None) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["file_format"] != "JPG":
                continue
            game = next((g for g in games if g in row["path"]), None)
            if game is None or row["path"] in seen:
                continue
            seen.add(row["path"])
            rows.append(
                {
                    "path": row["path"],
                    "game": game,
                    "y": 1 if row["rating"] not in ("", "0", "0.0") else 0,
                }
            )
    rows = [r for r in rows if Path(r["path"]).is_file()]
    if limit:
        # Keep whole games intact so leave-one-game-out still works.
        by_game: dict[str, list[dict]] = {}
        for r in rows:
            by_game.setdefault(r["game"], []).append(r)
        per_game = max(1, limit // max(1, len(by_game)))
        rows = [r for game_rows in by_game.values() for r in game_rows[:per_game]]
    return rows


def read_af_points(paths: list[str], exiftool: str = "exiftool") -> dict[str, tuple[int, int, int, int]]:
    """path -> (ref_w, ref_h, x, y) from Sony FocusLocation, in one exiftool pass."""
    points: dict[str, tuple[int, int, int, int]] = {}
    batch = 200
    for start in range(0, len(paths), batch):
        chunk = paths[start : start + batch]
        try:
            out = subprocess.run(
                [exiftool, "-j", "-n", "-FocusLocation", *chunk],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for entry in json.loads(out or "[]"):
                value = entry.get("FocusLocation")
                if not value:
                    continue
                nums = [int(v) for v in str(value).replace(",", " ").split()[:4]]
                if len(nums) == 4 and nums[0] > 0 and nums[1] > 0:
                    points[entry["SourceFile"]] = tuple(nums)  # type: ignore[assignment]
        except (json.JSONDecodeError, ValueError, OSError):
            continue
    return points


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default="lrcat_ratings.csv")
    ap.add_argument("--games", nargs="*", default=DEFAULT_GAMES)
    ap.add_argument("--limit", type=int, default=0, help="only N photos, for a smoke test")
    ap.add_argument("--cache", default="embeddings_cache.npz")
    ap.add_argument("--model", default="ViT-B-32")
    ap.add_argument("--pretrained", default="laion2b_s34b_b79k")
    args = ap.parse_args(argv)

    try:
        import numpy as np
        import open_clip
        import torch
        from PIL import Image
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        sys.exit(f"error: {exc}\n  pip install open_clip_torch pillow scikit-learn")

    manifest = load_manifest(Path(args.csv), args.games, args.limit or None)
    if len(manifest) < 100:
        sys.exit(
            f"error: only {len(manifest)} readable JPEGs found.\n"
            "  Is the SSD mounted? Are the catalog paths still valid?"
        )
    games = sorted({r["game"] for r in manifest})
    positives = sum(r["y"] for r in manifest)
    print(f"対象: {len(manifest)} 枚 / セレクト {positives} ({positives/len(manifest):.0%})")
    for game in games:
        rows = [r for r in manifest if r["game"] == game]
        print(f"  {game}: {len(rows):5d} 枚 (セレクト {sum(r['y'] for r in rows)})")

    cache_path = Path(args.cache)
    cached: dict[str, np.ndarray] = {}
    if cache_path.is_file():
        blob = np.load(cache_path, allow_pickle=True)
        cached = {k: blob[k] for k in blob.files}
        print(f"\nキャッシュ {len(cached)} 件を読み込み")

    todo = [r for r in manifest if r["path"] not in cached]
    if todo:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        print(f"\n埋め込み抽出: {len(todo)} 枚 (device={device})")
        model, _, preprocess = open_clip.create_model_and_transforms(
            args.model, pretrained=args.pretrained
        )
        model = model.to(device).eval()

        print("AF座標を読み取り中...")
        af_points = read_af_points([r["path"] for r in todo])
        print(f"  AF取得: {len(af_points)}/{len(todo)} 枚")

        started = time.time()
        batch_images: list = []
        batch_keys: list[str] = []

        def flush() -> None:
            if not batch_images:
                return
            with torch.no_grad():
                stacked = torch.stack(batch_images).to(device)
                features = model.encode_image(stacked)
                features = features / features.norm(dim=-1, keepdim=True)
            arr = features.cpu().numpy().astype("float32")
            half = len(batch_keys) // 2
            for i, key in enumerate(batch_keys):
                cached[key] = arr[i]
            batch_images.clear()
            batch_keys.clear()

        for index, row in enumerate(todo, start=1):
            path = row["path"]
            try:
                with Image.open(path) as img:
                    img.draft("RGB", (DECODE_TARGET, DECODE_TARGET))  # fast DCT-scaled decode
                    img = img.convert("RGB")
                    width, height = img.size
                    whole = img.copy()
                    point = af_points.get(path)
                    if point:
                        ref_w, ref_h, ax, ay = point
                        sx, sy = width / ref_w, height / ref_h
                        size = int(min(width, height) * AF_CROP_FRACTION)
                        cx, cy = int(ax * sx), int(ay * sy)
                        left = max(0, min(cx - size // 2, width - size))
                        top = max(0, min(cy - size // 2, height - size))
                        crop = img.crop((left, top, left + size, top + size))
                    else:
                        side = int(min(width, height) * AF_CROP_FRACTION)
                        left, top = (width - side) // 2, (height - side) // 2
                        crop = img.crop((left, top, left + side, top + side))
            except Exception:
                continue
            batch_images.append(preprocess(whole))
            batch_keys.append(path + "|whole")
            batch_images.append(preprocess(crop))
            batch_keys.append(path + "|af")
            if len(batch_images) >= 64:
                flush()
            if index % 250 == 0:
                rate = index / (time.time() - started)
                print(f"  {index}/{len(todo)}  ({rate:.1f} 枚/秒, 残り {(len(todo)-index)/rate/60:.1f} 分)")
        flush()
        np.savez_compressed(cache_path, **cached)
        print(f"  キャッシュ保存: {cache_path}")

    usable = [r for r in manifest if (r["path"] + "|whole") in cached]
    print(f"\n埋め込みあり: {len(usable)} 枚")

    def evaluate(kind: str) -> None:
        X = np.array([cached[r["path"] + "|" + kind] for r in usable])
        y = np.array([r["y"] for r in usable])
        game_of = np.array([r["game"] for r in usable])
        scores = np.zeros(len(usable))
        for game in games:
            test = game_of == game
            train = ~test
            if not test.any() or not train.any():
                continue
            scaler = StandardScaler().fit(X[train])
            model = LogisticRegression(class_weight="balanced", max_iter=3000, C=0.1)
            model.fit(scaler.transform(X[train]), y[train])
            scores[test] = model.predict_proba(scaler.transform(X[test]))[:, 1]

        from experiment_features import auc  # reuse the tie-aware implementation

        overall = auc(list(scores), list(y))
        print(f"\n[{kind}] 全体 AUC = {overall:.3f}", end="  ")
        print("← 使える" if overall >= 0.70 else "← 弱い" if overall >= 0.60 else "← 無力")
        for game in games:
            mask = game_of == game
            if mask.sum() and y[mask].sum():
                print(f"    {game}: AUC {auc(list(scores[mask]), list(y[mask])):.3f}")

        # The practical question: to catch 95% of the picks, how much is left to look at?
        order = np.argsort(-scores)
        needed = int(np.ceil(y.sum() * 0.95))
        found = 0
        for seen, idx in enumerate(order, start=1):
            found += int(y[idx])
            if found >= needed:
                print(
                    f"    → セレクトの95%({needed}枚)を拾うには上位 {seen} 枚"
                    f" ({seen/len(usable):.0%}) を見れば足りる"
                )
                break

    for kind in ("whole", "af"):
        evaluate(kind)

    print(
        "\n判定: 0.70以上なら本実装へ。0.60未満なら、自動セレクトではなく"
        "\n「高速レビューUI」に方針転換すべきというサイン。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
