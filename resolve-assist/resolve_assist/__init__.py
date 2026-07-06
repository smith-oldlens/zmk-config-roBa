"""DaVinci Resolve 編集補助ツール。

無音カット・フィラー語検出・シーン検出・字幕生成を行い、
DaVinci Resolve に取り込めるファイル (cuts.json / EDL / SRT) を出力する。
"""

__version__ = "0.1.0"

# Resolve 内スクリプトと GUI が最新の解析結果を受け渡すためのポインタファイル
LATEST_POINTER_DIR = ".resolve_assist"
LATEST_POINTER_NAME = "latest.json"
