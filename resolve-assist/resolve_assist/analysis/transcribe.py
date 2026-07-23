"""faster-whisper によるローカル文字起こし。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..types import TranscriptSegment, Word


@dataclass
class TranscribeOptions:
    model_size: str = "small"       # tiny / base / small / medium / large-v3
    language: str = "ja"
    word_timestamps: bool = True    # フィラー検出に必須
    compute_type: str = "auto"      # Mac CPU では int8 が速い
    initial_prompt: str | None = None


def transcribe(
    audio_path: str | Path,
    options: TranscribeOptions | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> list[TranscriptSegment]:
    """音声ファイルを文字起こしして TranscriptSegment のリストを返す。

    progress_cb には (処理済み秒数, 直近セグメントのテキスト) が渡される。
    """
    opts = options or TranscribeOptions()
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper がインストールされていません。\n"
            "  pip install 'resolve-assist[transcribe]'  または  pip install faster-whisper"
        ) from e

    try:
        model = WhisperModel(opts.model_size, compute_type=opts.compute_type)
    except Exception as e:
        raise RuntimeError(
            f"Whisper モデル '{opts.model_size}' の読み込みに失敗しました。\n"
            "初回はモデルのダウンロードにインターネット接続が必要です。\n"
            f"詳細: {e}"
        ) from e
    segments_iter, _info = model.transcribe(
        str(audio_path),
        language=opts.language,
        word_timestamps=opts.word_timestamps,
        initial_prompt=opts.initial_prompt,
        vad_filter=True,
    )

    results: list[TranscriptSegment] = []
    for seg in segments_iter:
        words = [
            Word(start=w.start, end=w.end, text=w.word)
            for w in (seg.words or [])
        ]
        results.append(
            TranscriptSegment(
                start=seg.start, end=seg.end, text=seg.text.strip(), words=words
            )
        )
        if progress_cb:
            progress_cb(seg.end, seg.text.strip())
    return results


def all_words(segments: list[TranscriptSegment]) -> list[Word]:
    return [w for seg in segments for w in seg.words]


def plain_text(segments: list[TranscriptSegment]) -> str:
    return "\n".join(seg.text for seg in segments)
