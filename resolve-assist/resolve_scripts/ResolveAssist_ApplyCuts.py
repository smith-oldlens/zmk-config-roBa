#!/usr/bin/env python
"""ResolveAssist: カットリスト適用スクリプト (Resolve 内部実行用)。

DaVinci Resolve の Workspace > Scripts メニューから実行する。
GUI/CLI (resolve-assist) が出力した最新の cuts.json を読み込み、
発話区間だけを並べたカット済みタイムラインを自動生成する。

- 無償版でも動作する (Resolve 内部からのスクリプト実行のみ使用)
- 対象の cuts.json は ~/.resolve_assist/latest.json 経由で自動発見する
- Resolve の Python 環境に追加パッケージは不要 (標準ライブラリのみ)
"""

import json
import os


def _get_resolve():
    # Scripts メニューから実行すると resolve / bmd がグローバルに存在する
    r = globals().get("resolve")
    if r is not None:
        return r
    b = globals().get("bmd")
    if b is not None:
        return b.scriptapp("Resolve")
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        return None


def _load_latest_cuts():
    pointer = os.path.join(os.path.expanduser("~"), ".resolve_assist", "latest.json")
    if not os.path.exists(pointer):
        return None, (
            "解析結果が見つかりません。先に Resolve Assist (GUI または CLI) で\n"
            "動画を解析してください。(探した場所: %s)" % pointer
        )
    with open(pointer, "r", encoding="utf-8") as f:
        data = json.load(f)
    cuts_path = data.get("cuts")
    if not cuts_path or not os.path.exists(cuts_path):
        return None, "cuts.json が見つかりません: %s" % cuts_path
    with open(cuts_path, "r", encoding="utf-8") as f:
        cuts = json.load(f)
    return cuts, None


def _find_clip_by_path(folder, target_path):
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


def _source_frame_to_timeline_frame(source_frame, segments):
    offset = 0
    for seg in segments:
        start, end = seg["start_frame"], seg["end_frame"]
        if start <= source_frame < end:
            return offset + (source_frame - start)
        offset += end - start
    return None


def main():
    resolve_app = _get_resolve()
    if resolve_app is None:
        print("Resolve に接続できませんでした。Resolve の Scripts メニューから実行してください。")
        return

    cuts, err = _load_latest_cuts()
    if err:
        print(err)
        return

    source = cuts["source"]
    print("カットリスト: %s" % source)
    print("  区間数: %d" % len(cuts.get("segments", [])))

    pm = resolve_app.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        print("プロジェクトが開かれていません。")
        return
    media_pool = project.GetMediaPool()

    if not os.path.exists(source):
        print("元動画が見つかりません: %s" % source)
        return

    clip = _find_clip_by_path(media_pool.GetRootFolder(), source)
    if clip is None:
        print("メディアプールに取り込み中: %s" % source)
        imported = media_pool.ImportMedia([source])
        if not imported:
            print("取り込みに失敗しました。")
            return
        clip = imported[0]

    # タイムライン名の重複を避ける
    existing = set()
    for i in range(int(project.GetTimelineCount())):
        tl = project.GetTimelineByIndex(i + 1)
        if tl:
            existing.add(tl.GetName())
    name = cuts.get("timeline_name") or "ResolveAssist_cut"
    unique = name
    n = 2
    while unique in existing:
        unique = "%s_%d" % (name, n)
        n += 1

    timeline = media_pool.CreateEmptyTimeline(unique)
    if timeline is None:
        print("タイムラインを作成できませんでした: %s" % unique)
        return
    project.SetCurrentTimeline(timeline)

    clip_infos = []
    for seg in cuts.get("segments", []):
        clip_infos.append({
            "mediaPoolItem": clip,
            "startFrame": seg["start_frame"],
            "endFrame": max(seg["start_frame"] + 1, seg["end_frame"] - 1),
        })
    if not clip_infos:
        print("カット区間が空です。")
        return
    if not media_pool.AppendToTimeline(clip_infos):
        print("AppendToTimeline に失敗しました。")
        return
    print("タイムライン '%s' に %d クリップを配置しました。" % (unique, len(clip_infos)))

    marker_count = 0
    for marker in cuts.get("markers", []):
        tl_frame = _source_frame_to_timeline_frame(
            marker["frame"], cuts.get("segments", [])
        )
        if tl_frame is None:
            continue
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
        print("マーカーを %d 個打ちました (赤=フィラー候補, 青=シーン切替)。" % marker_count)
    print("完了!")


main()
