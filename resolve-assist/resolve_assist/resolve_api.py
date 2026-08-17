"""DaVinci Resolve API コネクタ。

- Resolve 内部 (Workspace > Scripts) から実行される場合はグローバルの
  `resolve` / `bmd` が使える。
- Studio 版では外部プロセス (この GUI/CLI) から DaVinciResolveScript を
  import して接続できる。無償版では外部接続は常に失敗する(仕様)。

apply_cuts_to_resolve() は cuts.json の内容を Resolve のタイムラインに
適用する共通実装。resolve_scripts/ 内のスクリプトは Resolve の Python
環境にこのパッケージが無くても動くよう、同等の処理を自己完結で持つ。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Callable

# macOS / Windows / Linux の標準モジュール配置
_DEFAULT_MODULE_DIRS = [
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    os.path.expandvars(
        r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
    ),
    "/opt/resolve/Developer/Scripting/Modules",
]


def _import_resolve_script_module():
    candidates = []
    env_api = os.environ.get("RESOLVE_SCRIPT_API")
    if env_api:
        candidates.append(str(Path(env_api) / "Modules"))
    candidates.extend(_DEFAULT_MODULE_DIRS)

    for d in candidates:
        module_path = Path(d) / "DaVinciResolveScript.py"
        if module_path.exists():
            spec = importlib.util.spec_from_file_location(
                "DaVinciResolveScript", module_path
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["DaVinciResolveScript"] = mod
            spec.loader.exec_module(mod)
            return mod
    try:
        import DaVinciResolveScript  # 既に sys.path にある場合

        return DaVinciResolveScript
    except ImportError:
        return None


def get_resolve() -> Any | None:
    """Resolve への接続を試みる。失敗時は None (無償版の外部実行など)。"""
    dvr = _import_resolve_script_module()
    if dvr is None:
        return None
    try:
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def apply_cuts_to_resolve(
    resolve: Any,
    cuts: dict,
    log: Callable[[str], None] = print,
) -> bool:
    """cuts.json の内容から、カット済みタイムラインを Resolve 上に作る。

    無償版でも使える API のみを使用する:
    ImportMedia / CreateEmptyTimeline / AppendToTimeline / AddMarker
    """
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        log("プロジェクトが開かれていません。Resolve でプロジェクトを開いてください。")
        return False

    media_pool = project.GetMediaPool()
    source = cuts["source"]

    # 既にメディアプールにあるか、ファイルパスで探す
    clip = _find_clip_by_path(media_pool.GetRootFolder(), source)
    if clip is None:
        log(f"メディアプールに取り込み中: {source}")
        imported = media_pool.ImportMedia([source])
        if not imported:
            log(f"取り込みに失敗しました: {source}")
            return False
        clip = imported[0]

    # タイムライン名の重複を避ける
    existing = {
        project.GetTimelineByIndex(i + 1).GetName()
        for i in range(int(project.GetTimelineCount()))
    }
    name = cuts.get("timeline_name") or "ResolveAssist_cut"
    unique = name
    n = 2
    while unique in existing:
        unique = f"{name}_{n}"
        n += 1

    timeline = media_pool.CreateEmptyTimeline(unique)
    if timeline is None:
        log(f"タイムラインを作成できませんでした: {unique}")
        return False
    project.SetCurrentTimeline(timeline)

    clip_infos = [
        {
            "mediaPoolItem": clip,
            "startFrame": seg["start_frame"],
            "endFrame": max(seg["start_frame"] + 1, seg["end_frame"] - 1),
        }
        for seg in cuts.get("segments", [])
    ]
    if not clip_infos:
        log("カット区間が空です。")
        return False
    appended = media_pool.AppendToTimeline(clip_infos)
    if not appended:
        log("AppendToTimeline に失敗しました。")
        return False
    log(f"タイムライン '{unique}' に {len(clip_infos)} クリップを配置しました。")

    # ソース基準のマーカー位置をタイムライン位置へ変換して打つ
    marker_count = 0
    for marker in cuts.get("markers", []):
        tl_frame = _source_frame_to_timeline_frame(
            marker["frame"], cuts.get("segments", [])
        )
        if tl_frame is None:
            continue  # カットで消えた区間のマーカーはスキップ
        ok = timeline.AddMarker(
            tl_frame,
            marker.get("color", "Red"),
            marker.get("name", "marker"),
            marker.get("note", ""),
            max(1, int(marker.get("duration_frames", 1))),
        )
        if ok:
            marker_count += 1
    if marker_count:
        log(f"マーカーを {marker_count} 個打ちました。")
    return True


def _find_clip_by_path(folder: Any, target_path: str) -> Any | None:
    """メディアプールを再帰的に探索して同一ファイルのクリップを探す。"""
    target = os.path.normpath(target_path)
    for clip in folder.GetClipList() or []:
        try:
            if os.path.normpath(clip.GetClipProperty("File Path")) == target:
                return clip
        except Exception:
            continue
    for sub in folder.GetSubFolderList() or []:
        found = _find_clip_by_path(sub, target_path)
        if found:
            return found
    return None


def _source_frame_to_timeline_frame(
    source_frame: int, segments: list[dict]
) -> int | None:
    """ソースのフレーム番号を、カット後タイムライン上のフレーム番号へ変換。"""
    offset = 0
    for seg in segments:
        start, end = seg["start_frame"], seg["end_frame"]
        if start <= source_frame < end:
            return offset + (source_frame - start)
        offset += end - start
    return None
