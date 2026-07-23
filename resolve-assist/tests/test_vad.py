"""Silero VAD 連携のテスト。

実際の torch/silero-vad は重いため、post-processing (マージン付与・
結合・最短長フィルタ) の正しさをモックで検証する。
"""

import sys
import types

import pytest

from resolve_assist.analysis import vad
from resolve_assist.analysis.vad import VadOptions, detect_speech_segments_vad


def test_vad_options_defaults():
    opts = VadOptions()
    assert 0 < opts.threshold < 1
    assert opts.min_clip > 0


def test_missing_dependency_raises():
    # silero_vad が無い状態を再現
    saved = sys.modules.pop("silero_vad", None)
    sys.modules["silero_vad"] = None  # import を失敗させる
    try:
        with pytest.raises(RuntimeError, match="silero-vad"):
            detect_speech_segments_vad("dummy.mp4", 10.0)
    finally:
        if saved is not None:
            sys.modules["silero_vad"] = saved
        else:
            sys.modules.pop("silero_vad", None)


def test_postprocessing_with_mocked_silero(monkeypatch, tmp_path):
    """VAD 生出力に対しマージン・結合・最短長フィルタが効くことを確認。"""
    # silero_vad モジュールをモック
    fake = types.ModuleType("silero_vad")
    fake.load_silero_vad = lambda: object()
    fake.read_audio = lambda path, sampling_rate=16000: [0.0]
    # 生の発話区間: 2つは近接(結合される)、1つは極小(捨てられる)
    fake.get_speech_timestamps = lambda *a, **k: [
        {"start": 1.0, "end": 2.0},
        {"start": 2.05, "end": 3.0},   # 前と 0.05s 差 → merge_gap で結合
        {"start": 8.0, "end": 8.05},   # 極小 → min_clip 未満で除外
    ]
    monkeypatch.setitem(sys.modules, "silero_vad", fake)
    # extract_audio (ffmpeg) を呼ばせない
    monkeypatch.setattr(vad, "extract_audio", lambda *a, **k: tmp_path / "x.wav")

    opts = VadOptions(
        pad_before=0.0, pad_after=0.0, merge_gap=0.15, min_clip=0.3
    )
    segs = detect_speech_segments_vad("dummy.mp4", total_duration=10.0, options=opts)
    result = [(round(s.start, 2), round(s.end, 2)) for s in segs]
    assert result == [(1.0, 3.0)]  # 2つが結合、極小は除外
