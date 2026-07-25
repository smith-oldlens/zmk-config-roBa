"""Star assignment and the scoring run (spec 02 §6.5).

The rule that shapes everything here: a frame is only rejected when the burst
holds a better alternative. Deleting the one photo a given child appears in is
a worse failure than keeping a soft frame (docs/01 §2), so a lone frame is
never auto-rejected however soft it is — it just doesn't earn stars.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .. import db as dbmod
from ..config import Config
from ..grouping import ready_groups
from ..log import get_logger
from . import sharpness as sharpmod
from .exposure import exposure_ok
from .moment import moment_score
from .subject import Box, PersonDetector, find_subject

log = get_logger("bps.scoring")

RATING_BEST = 3
RATING_MOMENT = 5
RATING_KEEPER = 3
RATING_REJECT = 1
RATING_UNRATED = 0


@dataclass
class PhotoScore:
    """Working scores for one photo while its group is being decided."""

    photo_id: int
    new_name: str
    exposure_ok: bool = True
    sharp_raw: float = 0.0
    sharp_pct: float = 0.0
    moment: float = 0.0
    subject_box: list[int] = field(default_factory=list)
    subject_fallback: str = ""
    keep_score: float = 0.0
    in_group_rank: int = 0
    rating: int | None = None
    label: str | None = None

    def to_json(self) -> str:
        payload = {
            "exposure_ok": self.exposure_ok,
            # Already rounded in raw_sharpness(); persisting it unchanged keeps
            # the stored distribution identical to the in-memory one.
            "sharp_raw": self.sharp_raw,
            "subj_sharp": round(self.sharp_pct, 4),
            "subj_box": self.subject_box,
            "moment": round(self.moment, 4),
            "in_group_rank": self.in_group_rank,
            "keep_score": round(self.keep_score, 4),
        }
        if self.subject_fallback:
            payload["subject_fallback"] = self.subject_fallback
        return json.dumps(payload, ensure_ascii=False)


def load_image(path: Path) -> np.ndarray | None:
    """Read a JPEG. Uses imdecode so non-ASCII paths work on Windows too."""
    try:
        buffer = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buffer.size == 0:
        return None
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


# --- raw scoring (per photo) --------------------------------------------


def score_photo(
    image: np.ndarray,
    photo_id: int,
    new_name: str,
    cfg: Config,
    af_point: tuple[float, float] | None = None,
    detector: PersonDetector | None = None,
) -> PhotoScore:
    """Everything measurable from one frame alone; percentiles come later."""
    score = PhotoScore(photo_id=photo_id, new_name=new_name)
    score.exposure_ok = exposure_ok(image)
    if not score.exposure_ok:
        return score

    box, fallback = find_subject(image, af_point, detector, cfg.subject.center_sigma)
    score.subject_box = box.as_list()
    score.subject_fallback = "" if fallback in ("af", "center_weighted") else fallback
    score.sharp_raw = sharpmod.raw_sharpness(image, box)
    score.moment = moment_score(image, cfg.moment.classifier)
    return score


# --- star assignment (per group) ----------------------------------------


def decide_ratings(scores: list[PhotoScore], cfg: Config) -> PhotoScore | None:
    """Assign stars across one finalised burst. Returns the best photo, if any."""
    for score in scores:
        if not score.exposure_ok:
            # The one unconditional rejection: a black or white frame (spec §6.1).
            score.rating = RATING_REJECT
            score.label = cfg.deliver.label_reject

    usable = [s for s in scores if s.exposure_ok]
    if not usable:
        return None

    for score in usable:
        score.keep_score = 0.7 * score.sharp_pct + 0.3 * score.moment

    ranked = sorted(usable, key=lambda s: (-s.keep_score, s.new_name))
    for rank, score in enumerate(ranked, start=1):
        score.in_group_rank = rank

    best = ranked[0]
    lone_frame = len(scores) == 1
    if lone_frame and best.sharp_pct < cfg.sharpness.reject_pct:
        # Nothing to compare against and nothing to replace it with: leave it
        # unrated rather than either promoting or rejecting it (spec §6.5 step 5).
        best.rating = RATING_UNRATED
    elif best.moment >= cfg.moment.star5_threshold:
        best.rating = RATING_MOMENT
    else:
        best.rating = RATING_BEST

    for score in ranked[1:]:
        if score.sharp_pct >= cfg.sharpness.keeper_pct:
            score.rating = RATING_KEEPER

    has_alternative = any(s.rating is not None and s.rating >= RATING_KEEPER for s in usable)
    for score in usable:
        if score.rating is not None:
            continue
        if score.sharp_pct < cfg.sharpness.reject_pct and has_alternative:
            score.rating = RATING_REJECT
            score.label = cfg.deliver.label_reject
        else:
            score.rating = RATING_UNRATED
    return best


# --- the run -------------------------------------------------------------


def _session_distribution(database: dbmod.Database, session_started_at: float) -> list[float]:
    values: list[float] = []
    for blob in database.scores_since(session_started_at):
        try:
            raw = json.loads(blob).get("sharp_raw")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(raw, (int, float)) and raw > 0:
            values.append(float(raw))
    return values


def _af_point_from_row(row: sqlite3.Row) -> tuple[float, float] | None:
    """AF coordinates recorded by M3; absent (and so None) until then."""
    blob = row["af_json"]
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if data.get("center_suspect"):
        # Sony writes the image centre when it has no AF point (spec §7.2), so
        # this is "no data", not "focused dead centre".
        return None
    x, y = data.get("x"), data.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return float(x), float(y)
    return None


def finalize_ready_groups(
    cfg: Config,
    database: dbmod.Database,
    *,
    force: bool = False,
    detector: PersonDetector | None = None,
    now: float | None = None,
    progress: bool = False,
) -> dict[str, int]:
    """Score and rate every group that is ready, returning a small summary.

    Raw measurements for all ready groups are taken first so the percentile
    calibration sees the whole batch; scoring a burst against only the handful
    of frames that happened to precede it would rank the first group of a game
    against almost nothing.
    """
    groups = ready_groups(cfg, database, now=now, force=force)
    if not groups:
        return {"groups": 0, "photos": 0, "rated": 0, "missing_files": 0}

    session_start = float(database.get_meta("session_started_at") or 0.0)
    pending: dict[int, list[PhotoScore]] = {}
    missing_files = 0

    for group in groups:
        group_id = int(group["id"])
        scores: list[PhotoScore] = []
        for row in database.photos_in_group(group_id):
            if row["state"] != dbmod.GROUPED:
                continue
            path = cfg.work_dir / row["new_name"]
            image = load_image(path)
            if image is None:
                log.error("cannot read %s; marking FAILED", path)
                database.transition(
                    int(row["id"]), dbmod.GROUPED, dbmod.FAILED, error=f"unreadable: {path.name}"
                )
                missing_files += 1
                continue
            score = score_photo(
                image,
                int(row["id"]),
                row["new_name"],
                cfg,
                af_point=_af_point_from_row(row),
                detector=detector,
            )
            database.set_scores(score.photo_id, score.to_json())
            scores.append(score)
        pending[group_id] = scores

    distribution = _session_distribution(database, session_start)
    rated = 0
    for group_id, scores in pending.items():
        for score in scores:
            score.sharp_pct = sharpmod.calibrate(
                score.sharp_raw, distribution, cfg.sharpness.bootstrap_log10
            )
        best = decide_ratings(scores, cfg)
        for score in scores:
            database.transition(
                score.photo_id,
                dbmod.GROUPED,
                dbmod.SCORED,
                scores_json=score.to_json(),
                rating=score.rating,
                label=score.label,
            )
            rated += 1
        database.close_group(group_id, best.photo_id if best else None, when=now or time.time())
        if progress:
            stars = ", ".join(f"{s.rating}★" for s in sorted(scores, key=lambda s: s.in_group_rank))
            print(f"  group {group_id}: {len(scores)} frame(s) -> {stars}", flush=True)

    log.info("scored %d group(s), %d photo(s)", len(pending), rated)
    return {
        "groups": len(pending),
        "photos": sum(len(s) for s in pending.values()),
        "rated": rated,
        "missing_files": missing_files,
    }
