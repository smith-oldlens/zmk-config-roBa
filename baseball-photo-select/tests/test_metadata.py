"""AF metadata parsing (spec 02 §7.2).

The values here are the real ones measured off the α7C II in M0
(docs/OPEN_QUESTIONS.md) — two frames from an actual game, shot AF-C/Wide with
the camera in Human Eye Tracking.
"""
from __future__ import annotations

import numpy as np
import pytest

from bps.metadata import (
    AfRegion,
    build_af_region,
    is_center_fallback,
    parse_focus_location,
    parse_frame_size,
)
from bps.scoring.subject import box_around, find_subject

# Verbatim from the camera (α7C II, 33MP -> 7008x4672).
REAL_TAGS_A = {
    "MakerNotes:FocusLocation": "7008 4672 4259 2044",
    "MakerNotes:FocusFrameSize": "285x417",
    "MakerNotes:AFAreaMode": "Human Eye Tracking",
    "MakerNotes:FocusMode": "AF-C",
}
REAL_TAGS_B = {
    "MakerNotes:FocusLocation": "7008 4672 2463 2637",
    "MakerNotes:FocusFrameSize": "285x417",
    "MakerNotes:AFAreaMode": "Human Eye Tracking",
}
TAG_ORDER = [
    "MakerNotes:FocusLocation",
    "MakerNotes:FlexibleSpotPosition",
    "MakerNotes:FocalPlaneAFPointLocation",
]


class TestParsing:
    def test_real_focus_location(self):
        assert parse_focus_location("7008 4672 4259 2044") == (7008, 4672, 4259, 2044)

    def test_list_form(self):
        assert parse_focus_location([7008, 4672, 2463, 2637]) == (7008, 4672, 2463, 2637)

    def test_too_few_values(self):
        assert parse_focus_location("7008 4672") is None

    def test_non_numeric(self):
        assert parse_focus_location("(none)") is None

    def test_real_frame_size(self):
        assert parse_frame_size("285x417") == (285, 417)

    def test_frame_size_with_spaces(self):
        assert parse_frame_size("285 x 417") == (285, 417)

    def test_frame_size_absent(self):
        assert parse_frame_size(None) is None


class TestCenterFallback:
    def test_exact_center_is_fallback(self):
        assert is_center_fallback(7008, 4672, 3504, 2336)

    @pytest.mark.parametrize("tags", [REAL_TAGS_A, REAL_TAGS_B])
    def test_real_shots_are_not_fallback(self, tags):
        """Both measured frames sit 10-15% off centre: genuine tracking data."""
        w, h, x, y = parse_focus_location(tags["MakerNotes:FocusLocation"])
        assert not is_center_fallback(w, h, x, y)

    def test_zero_dimensions(self):
        assert not is_center_fallback(0, 0, 0, 0)


class TestBuildRegion:
    def test_builds_from_real_tags(self):
        region = build_af_region(REAL_TAGS_A, TAG_ORDER)
        assert (region.ref_width, region.ref_height) == (7008, 4672)
        assert (region.x, region.y) == (4259, 2044)
        assert (region.frame_w, region.frame_h) == (285, 417)
        assert region.area_mode == "Human Eye Tracking"
        assert region.center_suspect is False

    def test_accepts_ungrouped_tag_names(self):
        """exiftool may return bare names depending on the -G flag used."""
        region = build_af_region({"FocusLocation": "7008 4672 4259 2044"}, TAG_ORDER)
        assert region is not None and region.x == 4259

    def test_returns_none_without_af_tags(self):
        assert build_af_region({"EXIF:Model": "ILCE-7CM2"}, TAG_ORDER) is None

    def test_skips_unparseable_and_keeps_looking(self):
        tags = {"MakerNotes:FocusLocation": "n/a", "MakerNotes:FlexibleSpotPosition": "100 200 30 40"}
        region = build_af_region(tags, TAG_ORDER)
        assert region is not None and region.x == 30

    def test_roundtrips_through_json_dict(self):
        region = build_af_region(REAL_TAGS_A, TAG_ORDER)
        assert AfRegion.from_dict(region.to_dict()) == region

    def test_from_dict_rejects_garbage(self):
        assert AfRegion.from_dict({"nonsense": 1}) is None


class TestScaling:
    def test_same_size_is_identity(self):
        region = build_af_region(REAL_TAGS_A, TAG_ORDER)
        assert region.scaled_to(7008, 4672) is region

    def test_scales_to_a_smaller_copy(self):
        region = build_af_region(REAL_TAGS_A, TAG_ORDER)
        scaled = region.scaled_to(3504, 2336)  # half size, same 3:2 aspect
        assert (scaled.x, scaled.y) == (2130, 1022)
        assert (scaled.frame_w, scaled.frame_h) == (142, 208)  # 285/2, 417/2

    def test_refuses_when_aspect_differs(self):
        """A cropped export would put the old coordinates on a different player."""
        region = build_af_region(REAL_TAGS_A, TAG_ORDER)
        assert region.scaled_to(7168, 5120) is None  # the crop seen in M0

    def test_refuses_degenerate_sizes(self):
        region = build_af_region(REAL_TAGS_A, TAG_ORDER)
        assert region.scaled_to(0, 0) is None


class TestSubjectBoxFromAf:
    def test_box_is_centred_on_the_af_point(self):
        image = np.zeros((4672, 7008, 3), dtype=np.uint8)
        box = box_around(image, (4259.0, 2044.0), (285, 417))
        assert box.contains(4259, 2044)
        cx, cy = box.center
        assert abs(cx - 4259) < 1 and abs(cy - 2044) < 1

    def test_tiny_af_frame_is_grown_for_comparability(self):
        """A 285x417 eye box would not be comparable with other photos' crops."""
        image = np.zeros((4672, 7008, 3), dtype=np.uint8)
        box = box_around(image, (4259.0, 2044.0), (285, 417))
        assert box.w >= int(4672 * 0.20) and box.h >= int(4672 * 0.20)

    def test_box_is_clamped_inside_the_image(self):
        image = np.zeros((400, 600, 3), dtype=np.uint8)
        box = box_around(image, (5.0, 5.0))
        assert box.x >= 0 and box.y >= 0
        assert box.x + box.w <= 600 and box.y + box.h <= 400

    def test_find_subject_uses_af_without_a_detector(self):
        """The M0 finding: AF alone locates the subject, no model needed."""
        image = np.zeros((4672, 7008, 3), dtype=np.uint8)
        box, source = find_subject(image, (4259.0, 2044.0), detector=None, af_frame=(285, 417))
        assert source == "af_box"
        assert box.contains(4259, 2044)

    def test_find_subject_falls_back_without_af(self):
        image = np.zeros((4672, 7008, 3), dtype=np.uint8)
        _, source = find_subject(image, None, detector=None)
        assert source == "no_detector"
