#!/usr/bin/env python
"""ResolveAssist: SRT 字幕取り込みスクリプト (Resolve 内部実行用)。

DaVinci Resolve の Workspace > Scripts メニューから実行する。
GUI/CLI (resolve-assist) が生成した最新の subtitles.srt をメディアプールに
取り込む。タイムラインへの配置は Resolve の仕様上スクリプトから完全自動化
できないため、取り込み後の手順を表示する。
"""

import json
import os


def _get_resolve():
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


def main():
    resolve_app = _get_resolve()
    if resolve_app is None:
        print("Resolve に接続できませんでした。Resolve の Scripts メニューから実行してください。")
        return

    pointer = os.path.join(os.path.expanduser("~"), ".resolve_assist", "latest.json")
    if not os.path.exists(pointer):
        print("解析結果が見つかりません。先に Resolve Assist で動画を解析してください。")
        return
    with open(pointer, "r", encoding="utf-8") as f:
        data = json.load(f)
    srt = data.get("srt")
    if not srt or not os.path.exists(srt):
        print("SRT が見つかりません。解析時に「字幕を生成」を有効にしてください。")
        return

    pm = resolve_app.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        print("プロジェクトが開かれていません。")
        return
    media_pool = project.GetMediaPool()
    imported = media_pool.ImportMedia([srt])
    if not imported:
        print("SRT の取り込みに失敗しました: %s" % srt)
        return

    print("SRT をメディアプールに取り込みました: %s" % os.path.basename(srt))
    print("")
    print("次の手順でタイムラインに配置してください:")
    print("  1. エディットページで対象のタイムラインを開く")
    print("  2. メディアプールで取り込んだ字幕を右クリック")
    print("  3. 「選択した字幕をタイムラインに挿入」を選ぶ")


main()
