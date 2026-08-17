"""ffmpeg loudnorm によるラウドネス計測。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..media import _require


def _parse_loudnorm_output(stderr: str) -> dict | None:
    """loudnorm が stderr の末尾に出す JSON ブロックをパースする。"""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
        return {
            "integrated_lufs": float(data["input_i"]),
            "true_peak_db": float(data["input_tp"]),
            "lra": float(data["input_lra"]),
        }
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def measure_loudness(media_path: str | Path) -> dict | None:
    """統合ラウドネス (LUFS)・トゥルーピーク・LRA を計測する。失敗時 None。"""
    ffmpeg = _require("ffmpeg")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i", str(media_path),
        "-vn",
        "-af", "loudnorm=print_format=json",
        "-f", "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return _parse_loudnorm_output(proc.stderr)
