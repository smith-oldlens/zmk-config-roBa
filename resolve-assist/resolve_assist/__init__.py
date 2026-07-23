"""動画編集補助ツール (DaVinci Resolve / Final Cut Pro / Premiere Pro)。

無音カット・フィラー語検出・シーン検出・字幕生成を行い、各編集ソフトに
取り込めるファイル (cuts.json / EDL / FCPXML / FCP7 XML / SRT) を出力する。
"""

__version__ = "0.5.0"

# Resolve 内スクリプトと GUI が最新の解析結果を受け渡すためのポインタファイル
LATEST_POINTER_DIR = ".resolve_assist"
LATEST_POINTER_NAME = "latest.json"
