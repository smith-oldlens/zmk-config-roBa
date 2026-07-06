"""ffmpeg silencedetect による無音検出と発話区間の算出。"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..media import _require
from ..segments import (
    drop_short_segments,
    invert_segments,
    merge_segments,
    pad_segments,
)
from ..types import Segment

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


@dataclass
class SilenceOptions:
    """無音検出のパラメータ。GUI/CLI から調整できる。"""

    noise_db: float = -35.0     # これ以下の音量を無音とみなす (dB)
    min_silence: float = 0.35   # この秒数以上続いた無音だけをカット対象にする
    pad_before: float = 0.10    # 発話区間の頭に残すマージン (秒)
    pad_after: float = 0.15     # 発話区間の尻に残すマージン (秒)
    min_clip: float = 0.30      # これ未満の発話クリップは捨てる (秒)
    merge_gap: float = 0.15     # この間隔以下で隣接する発話区間は結合する (秒)


def detect_silences(
    media_path: str | Path, options: SilenceOptions | None = None
) -> list[Segment]:
    """無音区間のリストを返す。"""
    opts = options or SilenceOptions()
    ffmpeg = _require("ffmpeg")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i", str(media_path),
        "-vn",
        "-af", f"silencedetect=noise={opts.noise_db}dB:d={opts.min_silence}",
        "-f", "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg silencedetect が失敗しました:\n{proc.stderr[-2000:]}")
    return parse_silencedetect_output(proc.stderr)


def parse_silencedetect_output(stderr: str) -> list[Segment]:
    """silencedetect が stderr に出すログを (start, end) のリストにする。"""
    silences: list[Segment] = []
    current_start: float | None = None
    for line in stderr.splitlines():
        m = _SILENCE_START.search(line)
        if m:
            current_start = max(0.0, float(m.group(1)))
            continue
        m = _SILENCE_END.search(line)
        if m and current_start is not None:
            silences.append(Segment(current_start, float(m.group(1))))
            current_start = None
    # 末尾が無音のままファイルが終わると silence_end が出ないので開いたままにする
    if current_start is not None:
        silences.append(Segment(current_start, float("inf")))
    return silences


def speech_segments_from_silences(
    silences: list[Segment],
    total_duration: float,
    options: SilenceOptions | None = None,
) -> list[Segment]:
    """無音区間を反転して、マージン付きの発話区間リストを作る。"""
    opts = options or SilenceOptions()
    bounded = [
        Segment(s.start, min(s.end, total_duration)) for s in silences
        if s.start < total_duration
    ]
    speech = invert_segments(bounded, total_duration)
    speech = pad_segments(speech, opts.pad_before, opts.pad_after, total_duration)
    speech = merge_segments(speech, gap=opts.merge_gap)
    return drop_short_segments(speech, opts.min_clip)


def detect_speech_segments(
    media_path: str | Path,
    total_duration: float,
    options: SilenceOptions | None = None,
) -> list[Segment]:
    """無音検出から発話区間の算出までを一括で行う。"""
    silences = detect_silences(media_path, options)
    return speech_segments_from_silences(silences, total_duration, options)
