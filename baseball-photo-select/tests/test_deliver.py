"""Rating writeback, delivery and RAW export (spec 02 §7.3-§7.4)."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bps import db as dbmod
from bps.deliver import (
    add_to_select_list,
    deliver_scored,
    export_raws,
    read_select_list,
    select_list_path,
    sidecar_path_for,
)
from bps.grouping import group_pending
from bps.ingest import ingest_dir
from bps.metadata import MetadataTool
from bps.scoring.composite import finalize_ready_groups
from conftest import write_burst, write_jpeg

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="exiftool not installed"
)


def run_pipeline(cfg, database, card_dir, count=4):
    write_burst(card_dir, count)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    finalize_ready_groups(cfg, database, force=True)


# --- select list ---------------------------------------------------------


def test_select_list_starts_empty(cfg):
    assert read_select_list(cfg) == []


def test_select_list_dedupes_and_sorts(cfg):
    for name in ("DSC00009.ARW", "DSC00001.ARW", "DSC00009.ARW"):
        add_to_select_list(cfg, name)
    assert read_select_list(cfg) == ["DSC00001.ARW", "DSC00009.ARW"]


def test_sidecar_path_drops_the_raw_extension(cfg):
    """Lightroom pairs a sidecar with its RAW by base name."""
    assert sidecar_path_for(cfg, "DSC01234.ARW").name == "DSC01234.xmp"


# --- rating writeback ----------------------------------------------------


@needs_exiftool
def test_write_rating_roundtrip(cfg, tmp_path):
    photo = write_jpeg(tmp_path / "a.jpg", datetime(2026, 7, 20, 13, 0, 0))
    with MetadataTool(cfg) as meta:
        assert meta.write_rating(photo, 3, "Purple")
        tags = meta.read_tags(photo, ["XMP:Rating", "XMP:Label"])
    assert int(tags["XMP:Rating"]) == 3
    assert tags["XMP:Label"] == "Purple"


@needs_exiftool
def test_write_sidecar_without_a_source_file(cfg):
    """The ARW is still on the card, so the sidecar is built from tags alone."""
    target = sidecar_path_for(cfg, "DSC00001.ARW")
    with MetadataTool(cfg) as meta:
        assert meta.write_sidecar(target, 5)
        assert target.is_file()
        tags = meta.read_tags(target, ["XMP:Rating"])
    assert int(tags["XMP:Rating"]) == 5


@needs_exiftool
def test_sidecar_is_regenerated_not_appended(cfg):
    target = sidecar_path_for(cfg, "DSC00001.ARW")
    with MetadataTool(cfg) as meta:
        meta.write_sidecar(target, 3)
        meta.write_sidecar(target, 5)
        tags = meta.read_tags(target, ["XMP:Rating"])
    assert int(tags["XMP:Rating"]) == 5


# --- delivery ------------------------------------------------------------


@needs_exiftool
def test_delivery_moves_rated_photos(cfg, database, card_dir):
    run_pipeline(cfg, database, card_dir)
    result = deliver_scored(cfg, database)

    assert result.delivered == 4
    assert database.counts_by_state()[dbmod.DELIVERED] == 4
    assert len(list(cfg.deliver_dir.glob("*.jpg"))) == 4
    assert list(cfg.work_dir.glob("*.jpg")) == [], "work/ should be empty after delivery"


@needs_exiftool
def test_delivered_files_carry_their_rating(cfg, database, card_dir):
    """The whole point: the star must be in the file Lightroom imports."""
    run_pipeline(cfg, database, card_dir)
    deliver_scored(cfg, database)

    rows = {r["new_name"]: r for r in database.photos_in_state(dbmod.DELIVERED)}
    with MetadataTool(cfg) as meta:
        for name, row in rows.items():
            tags = meta.read_tags(cfg.deliver_dir / name, ["XMP:Rating"])
            assert int(tags["XMP:Rating"]) == row["rating"]


@needs_exiftool
def test_keepers_get_a_sidecar_and_a_list_entry(cfg, database, card_dir):
    run_pipeline(cfg, database, card_dir)
    deliver_scored(cfg, database)

    keepers = [
        r for r in database.photos_in_state(dbmod.DELIVERED) if r["rating"] >= cfg.ratings.keep
    ]
    assert keepers, "a burst must produce at least one keeper"
    selected = read_select_list(cfg)
    assert len(selected) == len(keepers)
    for arw_name in selected:
        assert sidecar_path_for(cfg, arw_name).is_file()


@needs_exiftool
def test_non_keepers_are_not_queued_for_raw_copy(cfg, database, card_dir):
    """Rejected frames must not drag their 50MB RAW into the import."""
    start = datetime(2026, 7, 20, 13, 0, 0)
    write_jpeg(card_dir / "DSC00001.JPG", start)
    write_jpeg(card_dir / "DSC00002.JPG", start + timedelta(milliseconds=100), blur=9)
    ingest_dir(card_dir, cfg, database)
    group_pending(cfg, database)
    finalize_ready_groups(cfg, database, force=True)
    deliver_scored(cfg, database)

    delivered = {r["orig_name"]: r["rating"] for r in database.photos_in_state(dbmod.DELIVERED)}
    selected = read_select_list(cfg)
    for name, rating in delivered.items():
        arw = name.replace(".JPG", ".ARW")
        assert (arw in selected) == (rating >= cfg.ratings.keep)


@needs_exiftool
def test_delivery_is_idempotent(cfg, database, card_dir):
    run_pipeline(cfg, database, card_dir)
    first = deliver_scored(cfg, database)
    second = deliver_scored(cfg, database)
    assert first.delivered == 4 and second.delivered == 0


@needs_exiftool
def test_missing_file_fails_only_that_photo(cfg, database, card_dir):
    run_pipeline(cfg, database, card_dir)
    victim = database.photos_in_state(dbmod.SCORED)[0]
    (cfg.work_dir / victim["new_name"]).unlink()

    result = deliver_scored(cfg, database)
    assert result.failed == 1 and result.delivered == 3
    assert database.counts_by_state()[dbmod.FAILED] == 1


def test_delivery_without_exiftool_delivers_nothing(cfg, database, card_dir, monkeypatch):
    """A photo must never reach Lightroom without its rating written first."""
    run_pipeline(cfg, database, card_dir)
    monkeypatch.setattr(MetadataTool, "_start", lambda self: False)

    result = deliver_scored(cfg, database)
    assert result.delivered == 0
    assert database.counts_by_state()[dbmod.SCORED] == 4
    assert list(cfg.deliver_dir.glob("*.jpg")) == []


# --- RAW export ----------------------------------------------------------


@needs_exiftool
def test_export_copies_selected_raws_and_sidecars(cfg, database, card_dir, tmp_path):
    run_pipeline(cfg, database, card_dir)
    deliver_scored(cfg, database)
    # The RAWs the camera wrote alongside the JPEGs, still on the card.
    for name in read_select_list(cfg):
        (card_dir / name).write_bytes(b"\x00" * 32)

    dest = tmp_path / "raw_import"
    result = export_raws(cfg, card_dir, dest)

    assert result.copied == len(read_select_list(cfg))
    assert result.sidecars == result.copied
    assert not result.missing
    for name in read_select_list(cfg):
        assert (dest / name).is_file()
        assert (dest / (Path(name).stem + ".xmp")).is_file()


@needs_exiftool
def test_export_leaves_the_card_untouched(cfg, database, card_dir, tmp_path):
    run_pipeline(cfg, database, card_dir)
    deliver_scored(cfg, database)
    for name in read_select_list(cfg):
        (card_dir / name).write_bytes(b"\x00" * 32)
    before = sorted(p.name for p in card_dir.iterdir())

    export_raws(cfg, card_dir, tmp_path / "out")
    assert sorted(p.name for p in card_dir.iterdir()) == before


@needs_exiftool
def test_export_finds_raws_in_subfolders(cfg, database, card_dir, tmp_path):
    run_pipeline(cfg, database, card_dir)
    deliver_scored(cfg, database)
    nested = card_dir / "101MSDCF"
    nested.mkdir()
    for name in read_select_list(cfg):
        (nested / name).write_bytes(b"\x00" * 32)

    result = export_raws(cfg, card_dir, tmp_path / "out")
    assert result.copied == len(read_select_list(cfg))


@needs_exiftool
def test_export_reports_missing_raws(cfg, database, card_dir, tmp_path):
    """A card swapped between shoots must be reported, not silently skipped."""
    run_pipeline(cfg, database, card_dir)
    deliver_scored(cfg, database)

    result = export_raws(cfg, card_dir, tmp_path / "out")
    assert result.copied == 0
    assert len(result.missing) == len(read_select_list(cfg))


def test_export_with_empty_select_list(cfg, card_dir, tmp_path):
    assert export_raws(cfg, card_dir, tmp_path / "out").copied == 0
