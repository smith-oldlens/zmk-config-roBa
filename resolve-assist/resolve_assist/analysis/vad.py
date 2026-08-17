"""Silero VAD (音声区間検出) による発話区間の抽出。

ffmpeg silencedetect は「音量」で無音を判定するため、咳払い・ブレス・
物音などの非音声も「音がある=発話」と誤って残してしまう。Silero VAD
(MIT ライセンス) は機械学習で「人の声かどうか」を判定するため、こうした
非音声を除去でき、トーク動画のカット精度が上がる。

torch 依存が重いためオプション扱い:
    pip install 'resolve-assist[vad]'   (silero-vad + torch)
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..media import extract_audio
from ..segments import drop_short_segments, merge_segments, pad_segments
from ..types import Segment

_SAMPLE_RATE = 16000


@dataclass
class VadOptions:
    """Silero VAD のパラメータ。"""

    threshold: float = 0.5          # 発話とみなす確率のしきい値 (0-1, 高いほど厳しい)
    min_speech: float = 0.25        # これ未満の発話は無視 (秒)
    min_silence: float = 0.35       # これ未満の無音では区切らない (秒)
    pad_before: float = 0.10        # 発話区間の頭に残すマージン (秒)
    pad_after: float = 0.15         # 発話区間の尻に残すマージン (秒)
    min_clip: float = 0.30          # これ未満の発話クリップは捨てる (秒)
    merge_gap: float = 0.15         # この間隔以下の発話区間は結合 (秒)


def _load_silero():
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad, read_audio
    except ImportError as e:
        raise RuntimeError(
            "silero-vad がインストールされていません。\n"
            "  pip install 'resolve-assist[vad]'  または  pip install silero-vad"
        ) from e
    return load_silero_vad, read_audio, get_speech_timestamps


def detect_speech_segments_vad(
    media_path: str | Path,
    total_duration: float,
    options: VadOptions | None = None,
) -> list[Segment]:
    """Silero VAD で発話区間を検出し、マージン付きで返す。"""
    opts = options or VadOptions()
    load_model, read_audio, get_speech_timestamps = _load_silero()

    model = load_model()
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = extract_audio(media_path, Path(tmp) / "vad.wav", _SAMPLE_RATE)
        wav = read_audio(str(wav_path), sampling_rate=_SAMPLE_RATE)
        raw = get_speech_timestamps(
            wav,
            model,
            sampling_rate=_SAMPLE_RATE,
            threshold=opts.threshold,
            min_speech_duration_ms=int(opts.min_speech * 1000),
            min_silence_duration_ms=int(opts.min_silence * 1000),
            return_seconds=True,
        )

    speech = [
        Segment(float(t["start"]), min(float(t["end"]), total_duration))
        for t in raw
        if float(t["start"]) < total_duration
    ]
    speech = pad_segments(speech, opts.pad_before, opts.pad_after, total_duration)
    speech = merge_segments(speech, gap=opts.merge_gap)
    return drop_short_segments(speech, opts.min_clip)
