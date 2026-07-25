"""Burst grouping and finalisation gating (spec 02 §5)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from bps import db as dbmod
from bps.grouping import (
    assign_group,
    circular_run,
    finalizable,
    group_pending,
    missing_file_numbers,
    ready_groups,
)
from bps.ingest import ingest_dir
from conftest import write_burst, write_jpeg


# --- pure helpers --------------------------------------------------------


def test_circular_run_plain_sequence():
    assert circular_run({3, 1, 2}) == [1, 2, 3]


def test_circular_run_handles_rollover():
    """DSC09998, 09999, 00001 is one burst, not a 9997-wide gap."""
    assert circular_run({9998, 9999, 1, 2}) == [9998, 9999, 1, 2]


def test_no_missing_numbers_when_contiguous():
    assert missing_file_numbers({1, 2, 3}, {1, 2, 3}) == []


def test_missing_number_detected():
    assert missing_file_numbers({1, 2, 4}, {1, 2, 4}) == [3]


def test_number_present_elsewhere_is_not_missing():
    """A single frame shot between bursts sits in another group (spec §5.2)."""
    assert missing_file_numbers({1, 2, 4}, {1, 2, 3, 4}) == []


def test_missing_across_rollover():
    assert missing_file_numbers({9999, 2}, {9999, 2}) == [0, 1]


def test_unknown_file_numbers_excluded():
    assert missing_file_numbers({-1}, set()) == []
    assert missing_file_numbers({5}, {5}) == []


# --- assignment ----------------------------------------------------------


def ingest_and_group(cfg, database, card_dir):
    ingest_dir(card_dir, cfg, database)
    return group_pending(cfg, database)


def test_burst_becomes_one_group(cfg, database, card_dir):
    write_burst(card_dir, 5, interval_ms=100)
    assert ingest_and_group(cfg, database, card_dir) == 5
    assert database.count_open_groups() == 1


def test_gap_starts_new_group(cfg, database, card_dir):
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_burst(card_dir, 3, start=start, interval_ms=100, first_number=1)
    write_burst(card_dir, 3, start=start + timedelta(seconds=30), interval_ms=100, first_number=4)
    ingest_and_group(cfg, database, card_dir)
    assert database.count_open_groups() == 2


def test_gap_exactly_at_threshold_stays_together(cfg, database, card_dir):
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_jpeg(card_dir / "DSC00001.JPG", start)
    write_jpeg(card_dir / "DSC00002.JPG", start + timedelta(seconds=2))  # gap_seconds default
    ingest_and_group(cfg, database, card_dir)
    assert database.count_open_groups() == 1


def test_out_of_order_arrival_still_groups(cfg, database, card_dir):
    """FTP delivers frames out of order; grouping is by shot time, not arrival."""
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_jpeg(card_dir / "DSC00003.JPG", start + timedelta(milliseconds=200))
    write_jpeg(card_dir / "DSC00001.JPG", start)
    write_jpeg(card_dir / "DSC00002.JPG", start + timedelta(milliseconds=100))
    ingest_and_group(cfg, database, card_dir)
    assert database.count_open_groups() == 1


def test_grouping_transitions_state(cfg, database, card_dir):
    write_burst(card_dir, 2)
    ingest_and_group(cfg, database, card_dir)
    counts = database.counts_by_state()
    assert counts[dbmod.GROUPED] == 2 and counts[dbmod.VERIFIED] == 0


def test_group_pending_is_idempotent(cfg, database, card_dir):
    write_burst(card_dir, 3)
    ingest_and_group(cfg, database, card_dir)
    assert group_pending(cfg, database) == 0
    assert database.count_open_groups() == 1


# --- finalisation gating -------------------------------------------------


def test_group_not_finalizable_while_recent(cfg, database, card_dir):
    write_burst(card_dir, 3)
    ingest_and_group(cfg, database, card_dir)
    group = database.open_groups()[0]
    assert not finalizable(group, cfg, database, now=time.time())


def test_group_finalizable_after_quiet_period(cfg, database, card_dir):
    write_burst(card_dir, 3)
    ingest_and_group(cfg, database, card_dir)
    group = database.open_groups()[0]
    later = time.time() + cfg.grouping.quiet_seconds + 1
    assert finalizable(group, cfg, database, now=later)


def test_missing_frame_holds_group_open(cfg, database, card_dir):
    """A hole in the numbering means a frame is still in flight — wait."""
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_jpeg(card_dir / "DSC00001.JPG", start)
    write_jpeg(card_dir / "DSC00003.JPG", start + timedelta(milliseconds=200))
    ingest_and_group(cfg, database, card_dir)
    group = database.open_groups()[0]
    later = time.time() + cfg.grouping.quiet_seconds + 1
    assert not finalizable(group, cfg, database, now=later)


def test_missing_frame_forced_after_max_wait(cfg, database, card_dir):
    """But never wait forever: a frame that never arrives must not block delivery."""
    start = datetime(2026, 7, 20, 13, 30, 0)
    write_jpeg(card_dir / "DSC00001.JPG", start)
    write_jpeg(card_dir / "DSC00003.JPG", start + timedelta(milliseconds=200))
    ingest_and_group(cfg, database, card_dir)
    group = database.open_groups()[0]
    later = time.time() + cfg.grouping.gap_wait_max_seconds + 1
    assert finalizable(group, cfg, database, now=later)


def test_ready_groups_force_ignores_gating(cfg, database, card_dir):
    write_burst(card_dir, 3)
    ingest_and_group(cfg, database, card_dir)
    assert ready_groups(cfg, database) == []
    assert len(ready_groups(cfg, database, force=True)) == 1
