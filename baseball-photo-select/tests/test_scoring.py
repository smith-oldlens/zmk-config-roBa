"""Scoring stages and star assignment (spec 02 §6)."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from bps import db as dbmod
from bps.config import Config
from bps.grouping import group_pending
from bps.ingest import ingest_dir
from bps.scoring.composite import (
    PhotoScore,
    decide_ratings,
    finalize_ready_groups,
    load_image,
    score_photo,
)
from bps.scoring.exposure import exposure_ok
from bps.scoring.sharpness import calibrate, percentile_of, raw_sharpness
from bps.scoring.subject import Box, center_box, find_subject, select_subject
from conftest import write_burst, write_jpeg


def noisy_image(size=(240, 320), seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (size[0], size[1], 3), dtype=np.uint8)


def flat_image(value: int, size=(240, 320)) -> np.ndarray:
    return np.full((size[0], size[1], 3), value, dtype=np.uint8)


# --- §6.1 exposure -------------------------------------------------------


def test_exposure_rejects_black_frame():
    assert not exposure_ok(flat_image(0))


def test_exposure_rejects_white_frame():
    assert not exposure_ok(flat_image(255))


def test_exposure_accepts_normal_frame():
    assert exposure_ok(noisy_image())


def test_exposure_accepts_dark_but_not_crushed():
    """A night game is dark, not blown — it must survive (spec §6.1)."""
    assert exposure_ok(flat_image(30))


def test_exposure_handles_empty_input():
    assert not exposure_ok(np.zeros((0, 0, 3), dtype=np.uint8))


# --- §6.2 subject selection ---------------------------------------------


def test_af_point_picks_smallest_containing_box():
    """A huge foreground body often encloses the AF point too; prefer the tight box."""
    big = Box(0, 0, 300, 300)
    small = Box(100, 100, 40, 40)
    box, reason = select_subject([big, small], (110, 110), (400, 400))
    assert box == small and reason == "af"


def test_af_point_outside_all_boxes_falls_back():
    boxes = [Box(0, 0, 50, 50)]
    box, reason = select_subject(boxes, (390, 390), (400, 400))
    assert reason == "center_weighted" and box == boxes[0]


def test_center_weighted_prefers_central_over_peripheral():
    central = Box(170, 170, 60, 60)
    corner = Box(0, 0, 70, 70)
    box, reason = select_subject([central, corner], None, (400, 400))
    assert box == central and reason == "center_weighted"


def test_no_boxes_returns_none():
    assert select_subject([], (1, 1), (400, 400)) == (None, "none")


def test_find_subject_without_detector_uses_center_crop():
    """Until RTMDet is wired up every frame takes the documented fallback."""
    image = noisy_image()
    box, reason = find_subject(image, None, detector=None)
    assert reason == "no_detector" and box == center_box(image)


def test_find_subject_with_detector_returning_nothing():
    image = noisy_image()
    box, reason = find_subject(image, None, detector=lambda img: [])
    assert reason == "center" and box == center_box(image)


def test_find_subject_uses_detector_boxes():
    image = noisy_image()
    target = Box(10, 10, 30, 30)
    box, reason = find_subject(image, (20, 20), detector=lambda img: [target])
    assert box == target and reason == "af"


def test_center_box_is_fraction_of_frame():
    box = center_box(np.zeros((200, 400, 3), dtype=np.uint8), fraction=0.5)
    assert (box.w, box.h) == (200, 100)
    assert box.x == 100 and box.y == 50


# --- §6.3 sharpness ------------------------------------------------------


def test_sharp_image_scores_above_blurred(tmp_path):
    stamp = datetime(2026, 7, 20, 13, 30, 0)
    sharp = load_image(write_jpeg(tmp_path / "sharp.jpg", stamp, sharp=True))
    blurred = load_image(write_jpeg(tmp_path / "blur.jpg", stamp, sharp=True, blur=6))
    assert raw_sharpness(sharp) > raw_sharpness(blurred)


def test_sharpness_measures_only_the_subject_box():
    """Background detail must not rescue a soft subject (spec §6.3)."""
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:, :100] = noisy_image((240, 100), seed=1)  # busy background on the left
    subject = Box(150, 80, 60, 60)  # flat region
    assert raw_sharpness(image, subject) < raw_sharpness(image, Box(0, 0, 100, 240))


def test_raw_sharpness_of_empty_image():
    assert raw_sharpness(np.zeros((0, 0, 3), dtype=np.uint8)) == 0.0


def test_raw_sharpness_survives_a_json_roundtrip():
    """Percentiles count ties, so the stored value must equal the in-memory one.

    Without this, a resumed session ranks a photo against rounded values while
    scoring it at full precision, and identical frames land on different sides
    of the keeper threshold.
    """
    import json

    value = raw_sharpness(noisy_image())
    assert json.loads(json.dumps(value)) == value


def test_percentile_ranks_within_distribution():
    dist = [1.0, 2.0, 3.0, 4.0]
    assert percentile_of(0.5, dist) == 0.0
    assert percentile_of(5.0, dist) == 1.0
    assert 0.0 < percentile_of(2.5, dist) < 1.0


def test_percentile_of_empty_distribution_is_neutral():
    assert percentile_of(1.0, []) == 0.5


def test_calibrate_uses_bootstrap_below_min_samples():
    """Early in a session there is nothing to rank against (spec §6.3)."""
    assert calibrate(1.0, [5.0] * 10, bootstrap_log10=2.0) == 0.5
    assert calibrate(4.0, [5.0] * 10, bootstrap_log10=2.0) == 1.0


def test_calibrate_uses_percentile_once_enough_samples():
    dist = [float(i) / 100 for i in range(100)]
    assert calibrate(0.99, dist, bootstrap_log10=2.0) > 0.9


# --- §6.5 star assignment ------------------------------------------------


def make_scores(*sharp_pcts: float, moment: float = 0.0) -> list[PhotoScore]:
    return [
        PhotoScore(photo_id=i, new_name=f"p{i}.jpg", sharp_pct=pct, moment=moment)
        for i, pct in enumerate(sharp_pcts, start=1)
    ]


def test_best_of_burst_gets_three_stars(cfg):
    scores = make_scores(0.9, 0.3, 0.2)
    best = decide_ratings(scores, cfg)
    assert best is scores[0] and scores[0].rating == 3


def test_moment_promotes_best_to_five_stars(cfg):
    scores = make_scores(0.9, 0.2, moment=0.8)
    decide_ratings(scores, cfg)
    assert scores[0].rating == 5


def test_non_best_keeper_also_gets_three_stars(cfg):
    scores = make_scores(0.9, 0.6)
    decide_ratings(scores, cfg)
    assert scores[1].rating == 3


def test_soft_frame_rejected_when_alternative_exists(cfg):
    scores = make_scores(0.9, 0.05)
    decide_ratings(scores, cfg)
    assert scores[1].rating == 1 and scores[1].label == cfg.deliver.label_reject


def test_middling_frame_is_unrated_not_rejected(cfg):
    scores = make_scores(0.9, 0.3)
    decide_ratings(scores, cfg)
    assert scores[1].rating == 0 and scores[1].label is None


def test_lone_soft_frame_is_never_auto_rejected(cfg):
    """The only photo of a child must survive even if it is soft (docs/01 §2)."""
    scores = make_scores(0.02)
    decide_ratings(scores, cfg)
    assert scores[0].rating == 0 and scores[0].label is None


def test_lone_sharp_frame_still_earns_stars(cfg):
    scores = make_scores(0.8)
    decide_ratings(scores, cfg)
    assert scores[0].rating == 3


def test_blown_frame_rejected_regardless_of_group(cfg):
    scores = make_scores(0.9)
    scores[0].exposure_ok = False
    assert decide_ratings(scores, cfg) is None
    assert scores[0].rating == 1 and scores[0].label == cfg.deliver.label_reject


def test_blown_frame_does_not_block_the_rest(cfg):
    scores = make_scores(0.9, 0.8)
    scores[0].exposure_ok = False
    best = decide_ratings(scores, cfg)
    assert best is scores[1] and scores[1].rating == 3


def test_ranks_are_assigned_in_order(cfg):
    scores = make_scores(0.2, 0.9, 0.5)
    decide_ratings(scores, cfg)
    assert [s.in_group_rank for s in scores] == [3, 1, 2]


def test_all_soft_group_keeps_its_best(cfg):
    """Every frame soft: still surface one rather than rejecting the whole burst."""
    scores = make_scores(0.05, 0.04, 0.03)
    decide_ratings(scores, cfg)
    assert scores[0].rating == 3
    assert [s.rating for s in scores[1:]] == [1, 1]


# --- end-to-end scoring run ----------------------------------------------


def test_finalize_scores_a_burst(cfg: Config, database, card_dir):
    write_burst(card_dir, 4)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)

    summary = finalize_ready_groups(cfg, database, force=True)

    assert summary["groups"] == 1 and summary["rated"] == 4
    assert database.counts_by_state()[dbmod.SCORED] == 4
    assert database.count_open_groups() == 0
    ratings = [row["rating"] for row in database.photos_in_state(dbmod.SCORED)]
    assert max(ratings) >= 3, "a burst must yield at least one keeper"


def test_finalize_picks_the_sharp_frame(cfg, database, card_dir):
    """The obviously blurred frames must not outrank the sharp one."""
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_jpeg(card_dir / "DSC00001.JPG", start, blur=8)
    write_jpeg(card_dir / "DSC00002.JPG", start + timedelta(milliseconds=100))  # sharp
    write_jpeg(card_dir / "DSC00003.JPG", start + timedelta(milliseconds=200), blur=8)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    finalize_ready_groups(cfg, database, force=True)

    best_id = database.open_groups() or None
    group = database.conn.execute("SELECT best_photo_id FROM groups").fetchone()
    best = database.get_photo(group["best_photo_id"])
    assert best["orig_name"] == "DSC00002.JPG"
    assert best["rating"] == 3


def test_finalize_rejects_black_frame_in_burst(cfg, database, card_dir):
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_jpeg(card_dir / "DSC00001.JPG", start)
    write_jpeg(card_dir / "DSC00002.JPG", start + timedelta(milliseconds=100), sharp=False, fill=(0, 0, 0))
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    finalize_ready_groups(cfg, database, force=True)

    rows = {r["orig_name"]: r for r in database.photos_in_state(dbmod.SCORED)}
    assert rows["DSC00002.JPG"]["rating"] == 1
    assert rows["DSC00002.JPG"]["label"] == cfg.deliver.label_reject


def test_finalize_is_noop_without_ready_groups(cfg, database, card_dir):
    write_burst(card_dir, 2)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    summary = finalize_ready_groups(cfg, database)  # quiet period not elapsed
    assert summary["groups"] == 0
    assert database.counts_by_state()[dbmod.GROUPED] == 2


def test_missing_work_file_marks_failed_not_crash(cfg, database, card_dir):
    """A deleted work file must fail that photo alone, not abort the run."""
    write_burst(card_dir, 2)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    victim = database.photos_in_state(dbmod.GROUPED)[0]
    (cfg.work_dir / victim["new_name"]).unlink()

    summary = finalize_ready_groups(cfg, database, force=True)
    assert summary["missing_files"] == 1
    assert database.counts_by_state()[dbmod.FAILED] == 1
    assert database.counts_by_state()[dbmod.SCORED] == 1


def test_scores_json_records_the_evidence(cfg, database, card_dir):
    write_burst(card_dir, 2)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    finalize_ready_groups(cfg, database, force=True)

    import json

    payload = json.loads(database.photos_in_state(dbmod.SCORED)[0]["scores_json"])
    assert {"exposure_ok", "sharp_raw", "subj_sharp", "subj_box", "moment"} <= set(payload)
    # Synthetic fixtures carry no Sony MakerNotes, so there is no AF data.
    assert payload["subject_source"] == "no_detector"


def test_score_photo_skips_measurement_for_blown_frame(cfg):
    score = score_photo(flat_image(0), 1, "x.jpg", cfg)
    assert score.exposure_ok is False and score.sharp_raw == 0.0
