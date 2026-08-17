import json

from resolve_assist.analysis.loudness import _parse_loudnorm_output
from resolve_assist.export.srt import format_srt, parse_srt
from resolve_assist.segments import timeline_time_to_source_time
from resolve_assist.style import (
    _quantile,
    learn_structure,
    learn_subtitle_style,
    load_style,
    save_style,
    silence_options_from_style,
    structure_guide_markers,
)
from resolve_assist.types import Segment, TranscriptSegment

SAMPLE_SRT = """\
1
00:00:01,000 --> 00:00:03,500
今日はキーボードの話を
していきます

2
00:00:04,000 --> 00:00:06,000
よろしくお願いします
"""


def test_parse_srt():
    caps = parse_srt(SAMPLE_SRT)
    assert len(caps) == 2
    assert caps[0].start == 1.0
    assert caps[0].end == 3.5
    assert caps[0].text == "今日はキーボードの話を\nしていきます"
    assert caps[1].text == "よろしくお願いします"


def test_parse_srt_without_index_line():
    srt = "00:00:00,000 --> 00:00:01,000\nこんにちは\n"
    caps = parse_srt(srt)
    assert len(caps) == 1
    assert caps[0].text == "こんにちは"


def test_quantile():
    assert _quantile([], 0.5) == 0.0
    assert _quantile([1.0], 0.5) == 1.0
    assert _quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5


def test_learn_subtitle_style_from_srt():
    caps = parse_srt(SAMPLE_SRT)
    stats = learn_subtitle_style(caps, duration=10.0, source="srt")
    assert stats["source"] == "srt"
    assert stats["caption_count"] == 2
    assert stats["max_lines"] == 2
    assert 8 <= stats["max_chars_per_line"] <= 40
    assert stats["chars_per_sec"] > 0
    assert 0 < stats["coverage_ratio"] < 1


def test_learn_structure():
    s = learn_structure(
        duration=100.0, first_speech_sec=2.0, scene_cuts=[8.0, 40.0, 92.0]
    )
    assert s["intro_ratio"] == 0.08   # 最初の発話後のシーン切替 (8s)
    assert s["outro_ratio"] == 0.08   # 最後のシーン切替から終端 (8s)


def test_learn_structure_caps_at_20_percent():
    s = learn_structure(duration=100.0, first_speech_sec=0.0, scene_cuts=[50.0])
    assert s["intro_ratio"] <= 0.2
    assert s["outro_ratio"] <= 0.2


def test_style_roundtrip_and_silence_options(tmp_path):
    profile = {
        "version": 1,
        "source": "ref.mp4",
        "duration_sec": 60.0,
        "cut_tempo": {
            "silence_options": {
                "min_silence": 0.4, "pad_before": 0.08,
                "pad_after": 0.2, "merge_gap": 0.3,
            }
        },
        "subtitles": None,
        "structure": {"intro_ratio": 0.1, "outro_ratio": 0.05},
        "loudness": None,
    }
    path = save_style(profile, tmp_path / "style.json")
    loaded = load_style(path)
    assert loaded == profile
    assert silence_options_from_style(loaded)["min_silence"] == 0.4


def test_load_style_rejects_unknown_version(tmp_path):
    p = tmp_path / "style.json"
    p.write_text(json.dumps({"version": 99}), encoding="utf-8")
    try:
        load_style(p)
        raise AssertionError("ValueError が発生するはず")
    except ValueError:
        pass


def test_timeline_time_to_source_time():
    segments = [Segment(10.0, 12.0), Segment(20.0, 25.0)]
    assert timeline_time_to_source_time(1.0, segments) == 11.0
    assert timeline_time_to_source_time(3.0, segments) == 21.0  # 2s 消費後 +1s
    assert timeline_time_to_source_time(100.0, segments) is None


def test_structure_guide_markers():
    profile = {"structure": {"intro_ratio": 0.25, "outro_ratio": 0.25}}
    segments = [Segment(0.0, 2.0), Segment(10.0, 12.0)]  # 計4秒のタイムライン
    markers = structure_guide_markers(profile, segments)
    assert len(markers) == 2
    # イントロ終わり: タイムライン1.0s → ソース1.0s
    assert markers[0].sec == 1.0
    # 締め開始: タイムライン3.0s → ソース11.0s
    assert markers[1].sec == 11.0
    assert all(m.color == "Cyan" for m in markers)


def test_parse_loudnorm_output():
    stderr = """\
[Parsed_loudnorm_0 @ 0x600]
{
    "input_i" : "-23.5",
    "input_tp" : "-5.2",
    "input_lra" : "6.7",
    "input_thresh" : "-33.7",
    "output_i" : "-24.0",
    "normalization_type" : "dynamic",
    "target_offset" : "0.5"
}
"""
    result = _parse_loudnorm_output(stderr)
    assert result == {
        "integrated_lufs": -23.5,
        "true_peak_db": -5.2,
        "lra": 6.7,
    }
    assert _parse_loudnorm_output("no json here") is None


def test_format_srt_min_duration_extends_but_respects_next():
    segments = [
        TranscriptSegment(start=0.0, end=0.5, text="短い"),
        TranscriptSegment(start=1.0, end=1.4, text="次も短い"),
    ]
    srt = format_srt(segments, min_duration=1.5)
    # 1枚目は次の字幕の直前 (1.0s) まで延長、2枚目は 1.5s 表示 (2.5s まで)
    assert "00:00:00,000 --> 00:00:00,999" in srt
    assert "00:00:01,000 --> 00:00:02,500" in srt
