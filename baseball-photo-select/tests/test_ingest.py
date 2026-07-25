"""Ingest behaviour (spec 02 §4) — verification, renaming, registration."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from bps import db as dbmod
from bps.ingest import (
    arw_name_for,
    build_new_name,
    extract_file_number,
    format_shot_time,
    has_jpeg_eoi,
    ingest_dir,
    ingest_file,
    read_shot_time,
    verify_file,
)
from conftest import write_burst, write_jpeg


# --- §4.3 / §4.4 pure naming logic --------------------------------------


def test_build_new_name_matches_spec():
    stamp = datetime(2026, 7, 20, 13, 30, 5, 123_000)
    assert build_new_name(stamp, "DSC01234.JPG") == "20260720_133005_123_DSC01234.jpg"


def test_build_new_name_pads_subseconds():
    stamp = datetime(2026, 7, 20, 13, 30, 5, 0)
    assert build_new_name(stamp, "DSC01234.JPG").split("_")[2] == "000"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("DSC01234.JPG", 1234),
        ("DSC00001.JPG", 1),
        ("_DSC9999.JPG", 9999),
        ("100_DSC01234.JPG", 1234),  # last run wins
        ("IMG.JPG", -1),
        ("ab12.JPG", -1),  # fewer than 4 digits
    ],
)
def test_extract_file_number(name, expected):
    assert extract_file_number(name) == expected


def test_arw_name_for():
    assert arw_name_for("DSC01234.JPG") == "DSC01234.ARW"


def test_format_shot_time():
    assert (
        format_shot_time(datetime(2026, 7, 20, 13, 30, 5, 123_000))
        == "2026-07-20 13:30:05.123"
    )


# --- §4.1 completeness checks -------------------------------------------


def test_read_shot_time_roundtrip(tmp_path: Path):
    stamp = datetime(2026, 7, 20, 13, 30, 5, 456_000)
    path = write_jpeg(tmp_path / "DSC00001.JPG", stamp)
    assert read_shot_time(path) == stamp


def test_read_shot_time_on_non_jpeg(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    assert read_shot_time(path) is None


def test_eoi_detection(tmp_path: Path, cfg):
    good = write_jpeg(tmp_path / "good.JPG", datetime(2026, 7, 20, 13, 30, 5))
    assert has_jpeg_eoi(good)
    truncated = tmp_path / "bad.JPG"
    truncated.write_bytes(good.read_bytes()[: -len(b"\xff\xd9") - 32])
    assert not has_jpeg_eoi(truncated)
    assert not verify_file(truncated, cfg, wait_stable=False)


def test_verify_rejects_empty_file(tmp_path: Path, cfg):
    empty = tmp_path / "empty.JPG"
    empty.touch()
    assert not verify_file(empty, cfg, wait_stable=False)


# --- ingest_file --------------------------------------------------------


def test_ingest_registers_and_moves(cfg, database):
    src = write_jpeg(cfg.inbox_dir / "DSC01234.JPG", datetime(2026, 7, 20, 13, 30, 5, 123_000))
    pid = ingest_file(src, cfg, database, wait_stable=False)

    row = database.get_photo(pid)
    assert row["state"] == dbmod.VERIFIED
    assert row["new_name"] == "20260720_133005_123_DSC01234.jpg"
    assert row["file_number"] == 1234
    assert row["shot_time"] == "2026-07-20 13:30:05.123"
    assert (cfg.work_dir / row["new_name"]).is_file()
    assert not src.exists(), "inbox file should be moved into work/"
    assert database.arw_name_for(row["new_name"]) == "DSC01234.ARW"


def test_ingest_from_card_copies_and_leaves_original(cfg, database, card_dir):
    """A memory card is read-only to us: the original must survive (docs/01 inv. 3)."""
    src = write_jpeg(card_dir / "DSC00001.JPG", datetime(2026, 7, 20, 13, 30, 5))
    ingest_dir(card_dir, cfg, database)
    assert src.is_file(), "card original must not be moved or deleted"
    assert len(list(cfg.work_dir.glob("*.jpg"))) == 1


def test_raw_files_staged_not_registered(cfg, database):
    """ARWs arriving in our own inbox are filed aside, never scored (spec §4.2)."""
    raw = cfg.inbox_dir / "DSC00001.ARW"
    raw.write_bytes(b"\x00" * 128)
    assert ingest_file(raw, cfg, database, wait_stable=False) is None
    assert (cfg.arw_dir / "DSC00001.ARW").is_file()
    assert sum(database.counts_by_state().values()) == 0


def test_raw_on_an_external_source_is_left_alone(cfg, database, card_dir):
    """Copying a shoot's RAWs would mean tens of GB for files export-raw can
    read from the card directly."""
    (card_dir / "DSC00001.ARW").write_bytes(b"\x00" * 128)
    write_burst(card_dir, 1)

    result = ingest_dir(card_dir, cfg, database)

    assert result.raw_in_place == 1 and result.raw_moved == 0
    assert (card_dir / "DSC00001.ARW").is_file(), "source RAW must stay put"
    assert list(cfg.arw_dir.iterdir()) == [], "no RAW should be copied into work/"


def test_other_extensions_ignored(cfg, database):
    other = cfg.inbox_dir / "readme.txt"
    other.write_text("x", encoding="utf-8")
    assert ingest_file(other, cfg, database, wait_stable=False) is None
    assert other.is_file()
    assert sum(database.counts_by_state().values()) == 0


# --- quarantine ---------------------------------------------------------


def test_corrupt_file_quarantined_not_deleted(cfg, database):
    good = write_jpeg(cfg.inbox_dir / "ok.JPG", datetime(2026, 7, 20, 13, 30, 5))
    corrupt = cfg.inbox_dir / "DSC00002.JPG"
    corrupt.write_bytes(good.read_bytes()[:200])  # truncated: no EOI

    assert ingest_file(corrupt, cfg, database, wait_stable=False) is None

    quarantined = list(cfg.quarantine_dir.iterdir())
    assert len(quarantined) == 1, "corrupt file must be preserved in quarantine/"
    assert quarantined[0].read_bytes(), "quarantined file must keep its bytes"
    assert database.counts_by_state()[dbmod.QUARANTINED] == 1


def test_quarantine_avoids_name_clash(cfg, database):
    """Two distinct corrupt files sharing a name both survive in quarantine."""
    for i in range(2):
        sub = cfg.inbox_dir / str(i)
        sub.mkdir()
        bad = sub / "DSC00003.JPG"
        bad.write_bytes(b"not a jpeg" * (i + 1))  # distinct content
        ingest_file(bad, cfg, database, wait_stable=False)
    assert len(list(cfg.quarantine_dir.iterdir())) == 2
    assert database.counts_by_state()[dbmod.QUARANTINED] == 2


# --- collisions and idempotency (M1 acceptance) -------------------------


def test_reingest_is_idempotent(cfg, database, card_dir):
    """Re-running ingest over the same card must not register duplicates."""
    write_burst(card_dir, 5)
    first = ingest_dir(card_dir, cfg, database)
    second = ingest_dir(card_dir, cfg, database)

    assert first.registered == 5
    assert second.registered == 0
    assert second.skipped_duplicate == 5
    assert database.counts_by_state()[dbmod.VERIFIED] == 5
    assert len(list(cfg.work_dir.glob("*.jpg"))) == 5


def test_reingest_does_not_requarantine(cfg, database, card_dir):
    """A corrupt frame stays on the card; re-runs must not stack up copies."""
    write_burst(card_dir, 2)
    (card_dir / "DSC08888.JPG").write_bytes(b"garbage")

    first = ingest_dir(card_dir, cfg, database)
    second = ingest_dir(card_dir, cfg, database)

    assert first.quarantined == 1
    assert second.quarantined == 0
    assert len(list(cfg.quarantine_dir.iterdir())) == 1
    assert database.counts_by_state()[dbmod.QUARANTINED] == 1


def test_different_file_same_name_gets_dup_suffix(cfg, database, card_dir):
    """Same DSC number + timestamp but different bytes: keep both (spec §4.3)."""
    stamp = datetime(2026, 7, 20, 13, 30, 5, 500_000)
    write_jpeg(card_dir / "DSC00007.JPG", stamp, size=(320, 240))
    ingest_dir(card_dir, cfg, database)

    # A second card with the same numbering but a visibly different frame.
    write_jpeg(card_dir / "DSC00007.JPG", stamp, size=(640, 480), fill=(10, 200, 30))
    ingest_dir(card_dir, cfg, database)

    names = sorted(p.name for p in cfg.work_dir.glob("*.jpg"))
    assert names == [
        "20260720_133005_500_DSC00007.jpg",
        "20260720_133005_500_DSC00007_dup1.jpg",
    ]
    assert database.counts_by_state()[dbmod.VERIFIED] == 2


# --- ingest_dir ---------------------------------------------------------


def test_ingest_dir_reports_mixed_content(cfg, database, card_dir):
    write_burst(card_dir, 3)
    (card_dir / "DSC09999.ARW").write_bytes(b"\x00" * 64)
    (card_dir / "notes.txt").write_text("x", encoding="utf-8")
    (card_dir / "DSC08888.JPG").write_bytes(b"garbage")

    result = ingest_dir(card_dir, cfg, database)
    # raw_in_place, not raw_moved: the card's ARW is read from where it lies.
    assert (result.registered, result.raw_in_place, result.ignored, result.quarantined) == (3, 1, 1, 1)


def test_ingest_dir_rejects_non_directory(cfg, database, tmp_path):
    with pytest.raises(NotADirectoryError):
        ingest_dir(tmp_path / "missing", cfg, database)


def test_ingest_100_files_under_5_seconds(cfg, database, card_dir):
    """M1 acceptance: 100 synthetic files ingest in <5s and all land in VERIFIED."""
    write_burst(card_dir, 100)
    start = time.monotonic()
    result = ingest_dir(card_dir, cfg, database)
    elapsed = time.monotonic() - start

    assert result.registered == 100
    assert database.counts_by_state()[dbmod.VERIFIED] == 100
    assert elapsed < 5.0, f"ingest took {elapsed:.2f}s, budget is 5s"
