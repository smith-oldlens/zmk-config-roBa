"""ffmpeg / ffprobe ラッパー。素材情報の取得と音声抽出。"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from .types import MediaInfo


class FFmpegNotFoundError(RuntimeError):
    pass


def _require(cmd: str) -> str:
    path = shutil.which(cmd)
    if not path:
        raise FFmpegNotFoundError(
            f"{cmd} が見つかりません。Homebrew でインストールしてください: brew install ffmpeg"
        )
    return path


def probe(video_path: str | Path) -> MediaInfo:
    """ffprobe で fps・長さ・解像度・音声有無を取得する。"""
    ffprobe = _require("ffprobe")
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "stream=codec_type,r_frame_rate,avg_frame_rate,width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)

    duration = float(data.get("format", {}).get("duration", 0.0))
    fps = 0.0
    width = height = 0
    has_audio = False
    for stream in data.get("streams", []):
        ctype = stream.get("codec_type")
        if ctype == "video" and fps == 0.0:
            rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
            try:
                fps = float(Fraction(rate))
            except (ValueError, ZeroDivisionError):
                fps = 0.0
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        elif ctype == "audio":
            has_audio = True
    if fps <= 0:
        # 音声のみのファイルなどはタイムコード計算用に既定値を使う
        fps = 30.0
    return MediaInfo(
        path=str(video_path),
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        has_audio=has_audio,
    )


def extract_audio(
    video_path: str | Path,
    out_wav: str | Path,
    sample_rate: int = 16000,
) -> Path:
    """Whisper 用にモノラル 16kHz WAV を抽出する。"""
    ffmpeg = _require("ffmpeg")
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out_wav
