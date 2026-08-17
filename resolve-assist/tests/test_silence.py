from resolve_assist.analysis.silence import (
    SilenceOptions,
    parse_silencedetect_output,
    speech_segments_from_silences,
)

SAMPLE_STDERR = """\
[silencedetect @ 0x7f8] silence_start: 1.5
[silencedetect @ 0x7f8] silence_end: 3.0 | silence_duration: 1.5
frame= 100 fps=0.0 q=-0.0 size=N/A
[silencedetect @ 0x7f8] silence_start: 5.25
[silencedetect @ 0x7f8] silence_end: 6.75 | silence_duration: 1.5
"""


def test_parse_silencedetect():
    silences = parse_silencedetect_output(SAMPLE_STDERR)
    assert [(s.start, s.end) for s in silences] == [(1.5, 3.0), (5.25, 6.75)]


def test_parse_unclosed_silence_at_eof():
    stderr = "[silencedetect] silence_start: 8.0\n"
    silences = parse_silencedetect_output(stderr)
    assert len(silences) == 1
    assert silences[0].start == 8.0
    assert silences[0].end == float("inf")


def test_speech_segments_with_margins():
    silences = parse_silencedetect_output(SAMPLE_STDERR)
    opts = SilenceOptions(pad_before=0.1, pad_after=0.2, min_clip=0.3, merge_gap=0.0)
    speech = speech_segments_from_silences(silences, total_duration=10.0, options=opts)
    result = [(round(s.start, 2), round(s.end, 2)) for s in speech]
    # 発話は 0-1.5, 3.0-5.25, 6.75-10 → マージン付与後
    assert result == [(0.0, 1.7), (2.9, 5.45), (6.65, 10.0)]


def test_open_ended_silence_is_clamped():
    stderr = "[silencedetect] silence_start: 8.0\n"
    silences = parse_silencedetect_output(stderr)
    opts = SilenceOptions(pad_before=0.0, pad_after=0.0)
    speech = speech_segments_from_silences(silences, total_duration=10.0, options=opts)
    assert [(s.start, s.end) for s in speech] == [(0.0, 8.0)]
