from resolve_assist.export.edl import format_edl, frames_to_timecode
from resolve_assist.types import Segment


def test_frames_to_timecode():
    assert frames_to_timecode(0, 30) == "00:00:00:00"
    assert frames_to_timecode(30, 30) == "00:00:01:00"
    assert frames_to_timecode(30 * 3600 + 31, 30) == "01:00:01:01"


def test_frames_to_timecode_ntsc_base():
    # 29.97fps はフレームベース 30 の NDF として扱う
    assert frames_to_timecode(30, 29.97) == "00:00:01:00"


def test_format_edl_structure():
    segments = [Segment(1.0, 2.0), Segment(3.0, 4.5)]
    edl = format_edl(segments, fps=30, title="test_cut", clip_name="video.mp4")
    lines = edl.splitlines()
    assert lines[0] == "TITLE: test_cut"
    assert lines[1] == "FCM: NON-DROP FRAME"
    events = [l for l in lines if l and l[0].isdigit()]
    assert len(events) == 2
    # 1本目: src 00:00:01:00-00:00:02:00 → rec 00:00:00:00-00:00:01:00
    assert "00:00:01:00 00:00:02:00 00:00:00:00 00:00:01:00" in events[0]
    # 2本目: rec は連続して積み上がる
    assert "00:00:03:00 00:00:04:15 00:00:01:00 00:00:02:15" in events[1]
    assert "* FROM CLIP NAME: video.mp4" in edl
