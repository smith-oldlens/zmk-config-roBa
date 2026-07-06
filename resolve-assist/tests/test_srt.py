from resolve_assist.export.srt import (
    _format_timestamp,
    format_srt,
    split_segment_for_subtitles,
    wrap_japanese,
)
from resolve_assist.types import TranscriptSegment


def test_timestamp_format():
    assert _format_timestamp(0) == "00:00:00,000"
    assert _format_timestamp(3661.5) == "01:01:01,500"
    assert _format_timestamp(-1) == "00:00:00,000"


def test_wrap_short_text_unchanged():
    assert wrap_japanese("こんにちは", 26) == ["こんにちは"]


def test_wrap_prefers_punctuation():
    text = "今日はキーボードの話をします、それでは始めましょう"
    lines = wrap_japanese(text, 15)
    assert lines[0].endswith("、")
    assert all(len(line) <= 15 for line in lines)


def test_split_long_segment():
    text = "あ" * 120
    seg = TranscriptSegment(start=0.0, end=12.0, text=text)
    parts = split_segment_for_subtitles(seg, max_chars=20, max_lines=2)
    assert len(parts) == 3
    assert parts[0].start == 0.0
    assert parts[-1].end == 12.0
    # 時間が単調増加している
    for prev, cur in zip(parts, parts[1:]):
        assert cur.start >= prev.start


def test_format_srt_structure():
    segments = [
        TranscriptSegment(start=0.0, end=2.0, text="こんにちは"),
        TranscriptSegment(start=1.9, end=4.0, text="キーボードの話です"),
    ]
    srt = format_srt(segments)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 2
    assert blocks[0].startswith("1\n00:00:00,000 --> 00:00:02,000")
    # 重なりが解消されている
    assert "00:00:02,001" in blocks[1]


def test_format_srt_skips_empty():
    segments = [TranscriptSegment(start=0, end=1, text="  ")]
    assert format_srt(segments) == ""
