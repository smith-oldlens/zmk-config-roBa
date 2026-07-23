from resolve_assist.segments import (
    drop_short_segments,
    invert_segments,
    merge_segments,
    pad_segments,
    subtract_segments,
)
from resolve_assist.types import Segment


def seg_tuples(segments):
    return [(round(s.start, 3), round(s.end, 3)) for s in segments]


def test_merge_overlapping():
    merged = merge_segments([Segment(0, 2), Segment(1, 3), Segment(5, 6)])
    assert seg_tuples(merged) == [(0, 3), (5, 6)]


def test_merge_with_gap():
    merged = merge_segments([Segment(0, 1), Segment(1.1, 2)], gap=0.2)
    assert seg_tuples(merged) == [(0, 2)]


def test_invert():
    speech = invert_segments([Segment(1, 2), Segment(3, 4)], total_duration=5)
    assert seg_tuples(speech) == [(0, 1), (2, 3), (4, 5)]


def test_invert_silence_at_edges():
    speech = invert_segments([Segment(0, 1), Segment(4, 5)], total_duration=5)
    assert seg_tuples(speech) == [(1, 4)]


def test_pad_clamps_to_bounds():
    padded = pad_segments([Segment(0.05, 1), Segment(4, 4.95)], 0.1, 0.1, 5.0)
    assert seg_tuples(padded) == [(0, 1.1), (3.9, 5)]


def test_subtract_middle():
    result = subtract_segments([Segment(0, 10)], [Segment(4, 5)])
    assert seg_tuples(result) == [(0, 4), (5, 10)]


def test_subtract_drops_tiny_remainder():
    result = subtract_segments([Segment(0, 1)], [Segment(0.02, 1)], min_remainder=0.05)
    assert result == []


def test_drop_short():
    result = drop_short_segments([Segment(0, 0.1), Segment(1, 2)], 0.3)
    assert seg_tuples(result) == [(1, 2)]
