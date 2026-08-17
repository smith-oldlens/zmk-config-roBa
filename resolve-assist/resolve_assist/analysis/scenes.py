"""PySceneDetect による映像のシーン切り替わり検出。"""

from __future__ import annotations

from pathlib import Path


def detect_scene_cuts(
    video_path: str | Path, threshold: float = 27.0
) -> list[float]:
    """シーンの切り替わり位置(秒)のリストを返す。先頭 0 秒は含まない。"""
    try:
        from scenedetect import ContentDetector, detect
    except ImportError as e:
        raise RuntimeError(
            "scenedetect がインストールされていません。\n"
            "  pip install 'resolve-assist[scenes]'  または  pip install 'scenedetect[opencv]'"
        ) from e

    scene_list = detect(str(video_path), ContentDetector(threshold=threshold))
    cuts: list[float] = []
    for start, _end in scene_list:
        sec = start.get_seconds()
        if sec > 0.0:
            cuts.append(sec)
    return cuts
