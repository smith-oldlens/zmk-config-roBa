"""Burst grouping (spec 02 §5).

A burst is the unit the pipeline reasons about: ratings are only decided once a
group is complete, because Lightroom reads a photo's metadata exactly once at
import (docs/01 invariant 2). Closing a group too early is therefore
unrecoverable, which is why finalisation needs both a quiet period *and* a
contiguous run of file numbers.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime

from . import db as dbmod
from .config import Config
from .log import get_logger

log = get_logger("bps.grouping")

FILE_NUMBER_MODULUS = 10000  # Sony DSC numbering wraps at 10000 (spec §5.2)


def _parse_shot_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")


def _seconds_between(earlier: str, later: str) -> float:
    return (_parse_shot_time(later) - _parse_shot_time(earlier)).total_seconds()


# --- §5.1 assignment ----------------------------------------------------


def assign_group(photo: sqlite3.Row, cfg: Config, database: dbmod.Database) -> int:
    """Put one photo in a burst group, creating one if needed.

    Photos must be fed in shot_time order (spec §5.1) — over FTP they arrive in
    a different order than they were taken, and grouping by arrival would split
    a burst in half.
    """
    shot = photo["shot_time"]
    candidates = database.open_groups()
    target = None
    for group in candidates:
        # Compare against the group's span so out-of-order arrivals still land
        # in the burst they belong to.
        if -cfg.grouping.gap_seconds <= _seconds_between(group["end_shot"], shot) <= cfg.grouping.gap_seconds:
            target = group
            break
        if -cfg.grouping.gap_seconds <= _seconds_between(shot, group["start_shot"]) <= cfg.grouping.gap_seconds:
            target = group
            break

    now = time.time()
    if target is None:
        group_id = database.create_group(shot, shot, photo["received_at"] or now)
    else:
        group_id = int(target["id"])
        start = min(target["start_shot"], shot)
        end = max(target["end_shot"], shot)
        last_received = max(float(target["last_received"]), float(photo["received_at"] or now))
        with database.conn:
            database.conn.execute(
                "UPDATE groups SET start_shot = ?, end_shot = ?, last_received = ? WHERE id = ?",
                (start, end, last_received, group_id),
            )
    database.transition(int(photo["id"]), dbmod.VERIFIED, dbmod.GROUPED, group_id=group_id)
    return group_id


def group_pending(cfg: Config, database: dbmod.Database) -> int:
    """Assign every VERIFIED photo to a group, oldest shot first. Returns count."""
    pending = database.photos_in_state(dbmod.VERIFIED)  # ordered by shot_time
    for photo in pending:
        assign_group(photo, cfg, database)
    if pending:
        log.info("grouped %d photo(s) into %d open group(s)", len(pending), database.count_open_groups())
    return len(pending)


# --- §5.2 finalisation --------------------------------------------------


def circular_run(numbers: set[int], modulus: int = FILE_NUMBER_MODULUS) -> list[int]:
    """Order file numbers as a run, tolerating the DSC09999 -> DSC00001 wrap."""
    nums = sorted(n % modulus for n in numbers)
    if len(nums) < 2:
        return nums
    gaps = [(nums[(i + 1) % len(nums)] - nums[i]) % modulus for i in range(len(nums))]
    start = (gaps.index(max(gaps)) + 1) % len(nums)
    return [nums[(start + i) % len(nums)] for i in range(len(nums))]


def missing_file_numbers(
    group_numbers: set[int], known_numbers: set[int], modulus: int = FILE_NUMBER_MODULUS
) -> list[int]:
    """Numbers inside the group's run that exist nowhere in the database.

    A number that shows up in another group is not missing — the photographer
    simply shot a single frame between bursts (spec §5.2). Only a true hole,
    i.e. a frame still in flight over FTP, blocks finalisation.
    """
    usable = {n for n in group_numbers if n >= 0}
    if len(usable) < 2:
        return []
    known = {n % modulus for n in known_numbers if n >= 0}
    run = circular_run(usable, modulus)
    missing: list[int] = []
    for current, following in zip(run, run[1:]):
        step = (following - current) % modulus
        for offset in range(1, step):
            candidate = (current + offset) % modulus
            if candidate not in known:
                missing.append(candidate)
    return missing


def group_file_numbers(group_id: int, database: dbmod.Database) -> set[int]:
    return {
        int(row["file_number"])
        for row in database.conn.execute(
            "SELECT file_number FROM photos WHERE group_id = ?", (group_id,)
        )
    }


def finalizable(
    group: sqlite3.Row, cfg: Config, database: dbmod.Database, now: float | None = None
) -> bool:
    """True when a group may be scored: quiet long enough AND no missing frames."""
    now = time.time() if now is None else now
    quiet_for = now - float(group["last_received"])
    if quiet_for < cfg.grouping.quiet_seconds:
        return False

    missing = missing_file_numbers(group_file_numbers(int(group["id"]), database), database.file_numbers())
    if not missing:
        return True

    # Never block forever: a frame that never arrives would otherwise pin the
    # whole burst open and nothing would ever reach Lightroom.
    if quiet_for >= cfg.grouping.gap_wait_max_seconds:
        log.warning(
            "group %s: finalising despite %d missing file number(s) %s after %.0fs",
            group["id"],
            len(missing),
            missing[:5],
            quiet_for,
        )
        return True
    log.debug("group %s: holding, missing file numbers %s", group["id"], missing[:5])
    return False


def ready_groups(
    cfg: Config, database: dbmod.Database, now: float | None = None, force: bool = False
) -> list[sqlite3.Row]:
    """Open groups that may be scored now (all of them when `force`)."""
    groups = database.open_groups()
    if force:
        return groups
    return [g for g in groups if finalizable(g, cfg, database, now)]
