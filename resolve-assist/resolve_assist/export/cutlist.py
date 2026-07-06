"""cuts.json — Resolve 内スクリプトが読み込む中間フォーマット。

構造 (version 1):
{
  "version": 1,
  "source": "/abs/path/to/video.mp4",
  "fps": 29.97,
  "duration_sec": 123.4,
  "timeline_name": "video_cut",
  "segments":   [{"start_sec": .., "end_sec": .., "start_frame": .., "end_frame": ..}, ...],
  "markers":    [{"sec": .., "frame": .., "name": .., "note": .., "color": ..}, ...],
  "scene_cuts": [{"sec": .., "frame": ..}, ...],
  "srt": "/abs/path/to/subtitles.srt"   # 生成した場合のみ
}

frame 値はソースクリップの先頭を 0 とするフレーム番号。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import LATEST_POINTER_DIR, LATEST_POINTER_NAME
from ..types import Marker, MediaInfo, Segment

CUTLIST_VERSION = 1


def sec_to_frame(sec: float, fps: float) -> int:
    return int(round(sec * fps))


def build_cutlist(
    info: MediaInfo,
    segments: list[Segment],
    markers: list[Marker] | None = None,
    scene_cuts: list[float] | None = None,
    srt_path: str | Path | None = None,
    timeline_name: str | None = None,
) -> dict:
    source = Path(info.path).resolve()
    return {
        "version": CUTLIST_VERSION,
        "source": str(source),
        "fps": info.fps,
        "duration_sec": info.duration,
        "timeline_name": timeline_name or f"{source.stem}_cut",
        "segments": [
            {
                "start_sec": round(s.start, 4),
                "end_sec": round(s.end, 4),
                "start_frame": sec_to_frame(s.start, info.fps),
                "end_frame": sec_to_frame(s.end, info.fps),
            }
            for s in segments
        ],
        "markers": [
            {
                "sec": round(m.sec, 4),
                "frame": sec_to_frame(m.sec, info.fps),
                "name": m.name,
                "note": m.note,
                "color": m.color,
                "duration_frames": max(1, sec_to_frame(m.duration_sec, info.fps)),
            }
            for m in (markers or [])
        ],
        "scene_cuts": [
            {"sec": round(c, 4), "frame": sec_to_frame(c, info.fps)}
            for c in (scene_cuts or [])
        ],
        "srt": str(Path(srt_path).resolve()) if srt_path else None,
    }


def write_cutlist(cutlist: dict, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(
        json.dumps(cutlist, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def read_cutlist(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != CUTLIST_VERSION:
        raise ValueError(f"未対応の cuts.json バージョンです: {data.get('version')}")
    return data


def latest_pointer_path() -> Path:
    return Path.home() / LATEST_POINTER_DIR / LATEST_POINTER_NAME


def write_latest_pointer(cuts_path: str | Path, srt_path: str | Path | None) -> Path:
    """Resolve 内スクリプトが「最新の解析結果」を見つけるためのポインタを書く。"""
    pointer = latest_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "cuts": str(Path(cuts_path).resolve()),
                "srt": str(Path(srt_path).resolve()) if srt_path else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return pointer
