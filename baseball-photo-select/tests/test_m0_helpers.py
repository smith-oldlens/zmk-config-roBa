"""Automated regression guard for the M0 helper scripts' pure logic.

The M0 helpers are otherwise exercised by hand against real hardware
(tests/manual/m0-e2e-checklist.md); these tests cover the parsing logic that
must stay correct — FocusLocation parsing and the center-fallback detection
that spec 02 7.2 relies on.

Run: pytest baseball-photo-select/tests/test_m0_helpers.py
"""
import importlib.util
import shutil
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


af = _load("m0_dump_af_tags")


class TestParseFocusLocation:
    def test_string_form(self):
        assert af.parse_focus_location("6000 4000 3000 2000") == (6000, 4000, 3000, 2000)

    def test_list_form(self):
        assert af.parse_focus_location([6000, 4000, 1500, 1000]) == (6000, 4000, 1500, 1000)

    def test_extra_numbers_take_first_four(self):
        assert af.parse_focus_location("6000 4000 1500 1000 99 99") == (6000, 4000, 1500, 1000)

    def test_too_few_numbers_returns_none(self):
        assert af.parse_focus_location("6000 4000") is None

    def test_non_numeric_returns_none(self):
        assert af.parse_focus_location("n/a") is None


class TestCenterSuspect:
    def test_exact_center_is_suspect(self):
        assert af.center_suspect(6000, 4000, 3000, 2000) is True

    def test_within_one_percent_is_suspect(self):
        assert af.center_suspect(6000, 4000, 3020, 2010) is True

    def test_off_center_is_not_suspect(self):
        assert af.center_suspect(6000, 4000, 1500, 1000) is False

    def test_zero_dimensions_not_suspect(self):
        assert af.center_suspect(0, 0, 0, 0) is False


class TestReport:
    def test_flags_center_focus_location(self):
        lines = af.report({"MakerNotes:FocusLocation": "6000 4000 3000 2000"})
        assert any("center_suspect" in ln for ln in lines)

    def test_off_center_not_flagged(self):
        lines = af.report({"MakerNotes:FocusLocation": "6000 4000 1500 1000"})
        assert not any("center_suspect" in ln for ln in lines)

    def test_no_af_tags_reports_absence(self):
        lines = af.report({"EXIF:Model": "ILCE-7CM2"})
        assert any("no known AF-point tags" in ln for ln in lines)

    def test_priority_order_preserved(self):
        tags = {
            "MakerNotes:FocalPlaneAFPointLocation": "x",
            "MakerNotes:FocusLocation": "6000 4000 1500 1000",
        }
        lines = af.report(tags)
        # FocusLocation is higher priority, so it must be listed before the fallback tag.
        joined = "\n".join(lines)
        assert joined.index("FocusLocation") < joined.index("FocalPlaneAFPointLocation")


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_write_test_stars_roundtrip(tmp_path):
    """End-to-end: generate JPEGs, write ratings, verify readback (needs exiftool + Pillow)."""
    pytest.importorskip("PIL")
    stars = _load("m0_write_test_stars")
    paths = stars.generate_jpegs(tmp_path)
    assert len(paths) == 3
    exe = stars.find_exiftool(None)
    for path, rating in zip(paths, stars.RATINGS):
        stars.write_rating(exe, path, rating)
        assert stars.read_rating(exe, path) == rating
