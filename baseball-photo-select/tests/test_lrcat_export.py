"""lrcat ratings export against a synthetic catalog (scripts/export_lrcat_ratings.py).

Builds a minimal SQLite database with the four Lightroom tables the query
touches, shaped like the community-documented lrcat schema, so the export
logic is proven without a real catalog.
"""
from __future__ import annotations

import csv
import importlib.util
import sqlite3
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location(
    "export_lrcat_ratings", SCRIPTS / "export_lrcat_ratings.py"
)
lrcat_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lrcat_export)


def make_fake_lrcat(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(
            """
            CREATE TABLE AgLibraryRootFolder (
              id_local INTEGER PRIMARY KEY, absolutePath TEXT, name TEXT
            );
            CREATE TABLE AgLibraryFolder (
              id_local INTEGER PRIMARY KEY, rootFolder INTEGER, pathFromRoot TEXT
            );
            CREATE TABLE AgLibraryFile (
              id_local INTEGER PRIMARY KEY, folder INTEGER, idx_filename TEXT
            );
            CREATE TABLE Adobe_images (
              id_local INTEGER PRIMARY KEY, rootFile INTEGER,
              rating REAL, pick REAL, colorLabels TEXT,
              fileFormat TEXT, captureTime TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO AgLibraryRootFolder VALUES (1, '/Volumes/SSD/', 'SSD')"
        )
        conn.execute("INSERT INTO AgLibraryFolder VALUES (10, 1, '2026.7.20 game/')")
        photos = [
            # (file_id, name, rating, pick, label, fmt)  — the owner's scheme:
            # 0 = not selected, 1 = selected, 2+ = own child / important.
            (100, "DSC00001.JPG", 0, 0, "", "JPG"),
            (101, "DSC00002.JPG", 1, 0, "", "JPG"),
            (102, "DSC00003.JPG", 3, 0, "", "JPG"),
            (103, "DSC00004.JPG", None, 0, "", "JPG"),
            (104, "DSC00002.ARW", 1, 0, "", "RAW"),
        ]
        for file_id, name, rating, pick, label, fmt in photos:
            conn.execute(
                "INSERT INTO AgLibraryFile VALUES (?, 10, ?)", (file_id, name)
            )
            conn.execute(
                "INSERT INTO Adobe_images VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_id + 1000, file_id, rating, pick, label, fmt, "2026-07-20T13:00:00"),
            )
    conn.close()


def test_export_reads_ratings_and_builds_paths(tmp_path: Path):
    lrcat = tmp_path / "test.lrcat"
    make_fake_lrcat(lrcat)
    out = tmp_path / "ratings.csv"

    rows = lrcat_export.export_ratings(lrcat, out)
    assert len(rows) == 5

    with out.open(encoding="utf-8") as fh:
        by_path = {r["path"]: r for r in csv.DictReader(fh)}

    key = "/Volumes/SSD/2026.7.20 game/DSC00002.JPG"
    assert key in by_path, "path must be root + folder + filename"
    assert by_path[key]["rating"] == "1.0" or by_path[key]["rating"] == "1"
    unrated = by_path["/Volumes/SSD/2026.7.20 game/DSC00004.JPG"]
    assert unrated["rating"] == ""
    raw = by_path["/Volumes/SSD/2026.7.20 game/DSC00002.ARW"]
    assert raw["file_format"] == "RAW"


def test_export_does_not_touch_the_original(tmp_path: Path):
    """The catalog is the user's most precious file; we read a copy only."""
    lrcat = tmp_path / "test.lrcat"
    make_fake_lrcat(lrcat)
    before = lrcat.read_bytes()

    lrcat_export.export_ratings(lrcat, tmp_path / "out.csv")
    assert lrcat.read_bytes() == before
    assert lrcat.stat().st_size > 0


def test_summary_counts_do_not_crash(tmp_path: Path, capsys):
    lrcat = tmp_path / "test.lrcat"
    make_fake_lrcat(lrcat)
    rows = lrcat_export.export_ratings(lrcat, tmp_path / "out.csv")
    lrcat_export.print_summary(rows)
    printed = capsys.readouterr().out
    assert "5 photo(s)" in printed
    assert "★1" in printed
