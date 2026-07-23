import pytest

from resolve_assist.cli import parse_range, parse_time
from resolve_assist.shorts import (
    build_shorts_filtergraph,
    clip_segments_to_window,
    map_transcript_to_clip,
)
from resolve_assist.types import Segment, TranscriptSegment


def test_parse_time():
    assert parse_time("95") == 95.0
    assert parse_time("1:35") == 95.0
    assert parse_time("0:01:35") == 95.0
    assert parse_time("1:35.5") == 95.5
    with pytest.raises(ValueError):
        parse_time("abc")


def test_parse_range():
    assert parse_range("2:15-3:10") == (135.0, 190.0)
    with pytest.raises(ValueError):
        parse_range("3:10-2:15")
    with pytest.raises(ValueError):
        parse_range("120")


def test_clip_segments_to_window():
    speech = [Segment(0, 5), Segment(10, 20), Segment(30, 40)]
    clipped = clip_segments_to_window(speech, 3, 35)
    assert [(s.start, s.end) for s in clipped] == [(3, 5), (10, 20), (30, 35)]


def test_map_transcript_to_clip():
    transcript = [
        TranscriptSegment(start=1.0, end=4.0, text="最初の発言"),
        TranscriptSegment(start=11.0, end=14.0, text="次の発言"),
        TranscriptSegment(start=50.0, end=55.0, text="窓の外"),
    ]
    subs = [Segment(0, 5), Segment(10, 15)]
    events = map_transcript_to_clip(transcript, subs)
    assert len(events) == 2
    # 1つ目: ソース1-4s → クリップ1-4s
    assert (round(events[0].start, 2), round(events[0].end, 2)) == (1.0, 4.0)
    # 2つ目: ソース11-14s → 2番目サブ区間 (offset5s) 内の 1-4s → クリップ6-9s
    assert (round(events[1].start, 2), round(events[1].end, 2)) == (6.0, 9.0)


def test_map_transcript_drops_tiny_overlaps():
    transcript = [TranscriptSegment(start=4.95, end=6.0, text="ほぼ窓外")]
    subs = [Segment(0, 5)]
    assert map_transcript_to_clip(transcript, subs) == []


def test_filtergraph_blur_with_ass():
    graph = build_shorts_filtergraph(
        [Segment(1.0, 2.0), Segment(3.0, 4.0)], "blur", 1080, 1920, "sub.ass"
    )
    assert "trim=start=1.000:end=2.000" in graph
    assert "concat=n=2:v=1:a=1[vc][ac]" in graph
    assert "gblur" in graph
    assert "overlay=(W-w)/2:(H-h)/2" in graph
    assert "ass=sub.ass" in graph
    assert graph.count("[0:v]") == 2
    assert graph.count("[0:a]") == 2


def test_filtergraph_crop_without_ass():
    graph = build_shorts_filtergraph([Segment(0.0, 5.0)], "crop", 1080, 1920, None)
    assert "scale=-2:1920,crop=1080:1920" in graph
    assert "ass=" not in graph
    assert "[vo]null[vout]" in graph
