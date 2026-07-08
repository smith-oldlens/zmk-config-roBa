# Resolve Assist — 動画編集補助ツール (DaVinci Resolve / Final Cut Pro / Premiere Pro)

トーク系動画の編集を時短するためのツールです。動画を解析して、以下を自動化します。

- **無音部分の自動カット** — 無音区間を検出し、発話部分だけを並べたタイムラインを生成
- **フィラー語の検出** — 「えー」「あの」「えっと」等を検出し、カット候補としてマーカー表示(オプションで自動カット)
- **シーン検出** — 映像の切り替わりにマーカーを配置
- **字幕生成** — Whisper によるローカル文字起こし → 日本語向けに整形した SRT を生成
- **お手本スタイル学習** — 既成の動画から編集の型(カットテンポ・字幕体裁・構成・音量感)を
  プロファイル化し、新しい動画に自動適用

すべてオフライン・無料で動作します(クラウド API 不使用)。

## 対応環境

- macOS (Apple Silicon / Intel)
- 対応編集ソフト(GUI/CLI で出力先を選択):
  - **DaVinci Resolve 無償版 / Studio**
    - 無償版: 解析結果を Resolve 内のスクリプトメニューから適用(本ツールが対応済み)
    - Studio: GUI の「Resolveへ直接適用」ボタンでワンクリック適用
  - **Final Cut Pro** — カット済みタイムラインを FCPXML で読み込み(マーカー付き)
  - **Premiere Pro** — カット済みシーケンスを FCP7 XML で読み込み(マーカー付き)
- Python 3.10 以上

## セットアップ

### 1. ffmpeg と Python

```bash
brew install ffmpeg python@3.12
```

### 2. Resolve Assist のインストール

このリポジトリの `resolve-assist/` フォルダで:

```bash
cd resolve-assist
pip3 install -e '.[full]'      # 全機能 (Whisper文字起こし + シーン検出)
# 最小構成 (無音カットのみ) なら: pip3 install -e .
```

### 3. Resolve 内スクリプトのインストール (Resolve を使う場合のみ)

```bash
./install_resolve_scripts.sh
```

DaVinci Resolve の **Workspace > Scripts > Utility** メニューに
`ResolveAssist_ApplyCuts` と `ResolveAssist_ImportSRT` が追加されます
(表示されない場合は Resolve を再起動)。

## 使い方

### GUI で使う

```bash
resolve-assist-gui
```

1. 動画ファイルを選択
2. 実行する処理にチェック(無音カット / 字幕 / フィラー検出 / シーン検出)
3. 「対象の編集ソフト」で使うソフトにチェック(複数可)
4. 「解析実行」を押す
   - 初回は Whisper モデルのダウンロードで数分かかります
5. 完了したら、お使いの編集ソフトで取り込み:

**DaVinci Resolve の場合**
- **Workspace > Scripts > Utility > ResolveAssist_ApplyCuts** を実行
  → 発話部分だけを並べたタイムラインが自動生成され、フィラー位置に赤マーカーが付く
- 字幕を作った場合は **ResolveAssist_ImportSRT** を実行
  → SRT がメディアプールに入るので、右クリック →「選択した字幕をタイムラインに挿入」
- スクリプトは「最後に解析した動画」を自動で見つけるので、ファイル選択などの操作は不要

**Final Cut Pro の場合**
- **File > Import > XML** で `timeline.fcpxml` を読み込む
  → イベント「Resolve Assist」にカット済みプロジェクトが作られる(フィラーはクリップマーカー)
- 字幕は **File > Import > Captions** で `subtitles.srt` を読み込む

**Premiere Pro の場合**
- **File > Import** で `timeline_premiere.xml` を読み込む
  → カット済みシーケンスが追加される(フィラーはシーケンスマーカー)
- 字幕は `subtitles.srt` をプロジェクトに読み込み、キャプショントラックへ配置

### CLI で使う

```bash
# 無音カットのみ (既定は Resolve 向け出力)
resolve-assist analyze talk.mp4

# Final Cut Pro / Premiere Pro 向けに出力
resolve-assist analyze talk.mp4 --target fcp
resolve-assist analyze talk.mp4 --target premiere
resolve-assist analyze talk.mp4 --target all      # 3ソフト分すべて

# 無音カット + 字幕 + フィラー検出
resolve-assist analyze talk.mp4 --subtitles --fillers

# フィラーも自動カットに含める / 高精度モデルを使う
resolve-assist analyze talk.mp4 --subtitles --cut-fillers --model medium

# パラメータ調整の例 (静かな環境の録音なら -45dB 程度がおすすめ)
resolve-assist analyze talk.mp4 --silence-db -45 --min-silence 0.5
```

### 出力ファイル

`<動画名>_assist/` フォルダに出力されます。

| ファイル | 内容 |
|---|---|
| `cuts.json` | カットリスト(Resolve 内スクリプトが読む) |
| `timeline.edl` | カット済みタイムラインの EDL(汎用。Resolve/Premiere で読める) |
| `timeline.fcpxml` | Final Cut Pro 用カット済みタイムライン(`--target fcp` 時) |
| `timeline_premiere.xml` | Premiere Pro 用カット済みシーケンス(`--target premiere` 時) |
| `subtitles.srt` | 生成した字幕(3ソフトすべてで読み込み可) |
| `transcript.txt` | 文字起こし全文 |
| `fillers.txt` | 検出したフィラー語の一覧(時刻付き) |

### EDL を使う場合(スクリプトが使えないとき)

1. Resolve のメディアプールに元動画を取り込む
2. **File > Import > Timeline** で `timeline.edl` を選択
3. フレームレートを動画に合わせて読み込む

## お手本スタイル学習

「この動画みたいな編集にしたい」というお手本(自分の過去作など)から編集の型を学習し、
新しい動画の解析に適用できます。

```bash
# 1. お手本から学習 (SRT があれば字幕体裁を正確に学習できる)
resolve-assist learn ohandbook.mp4 --srt ohandbook.srt
# → ohandbook_style.json ができる

# 2. 新しい動画に適用
resolve-assist analyze new_video.mp4 --style ohandbook_style.json --subtitles
```

GUI では「お手本から学習...」ボタンで同じことができます(学習後、自動でパラメータ欄に反映)。

### 学習される内容

| 項目 | 学習内容 | 適用のされ方 |
|---|---|---|
| カットテンポ | お手本に残っている間(ま)の長さ・発話密度・ショット長 | 無音カットのしきい値・マージンを自動調整し、同じテンポ感に |
| 字幕体裁 | 1行文字数・行数・表示時間・読み上げ速度 | 生成する SRT を同じ体裁に整形(最短表示時間も確保) |
| 構成の型 | イントロ/本編/締めの長さ比率 | 新タイムラインに水色のガイドマーカーを配置 |
| 音量感 | 統合ラウドネス (LUFS) | 新動画との差分を `style_report.txt` に提示(±何dB調整すべきか) |

- お手本の字幕は **SRT ファイルがあれば正確に**学習できます。ない場合は Whisper の
  文字起こしから推定します(体裁は概算になります)。
- 明示的に指定した CLI オプション(`--min-silence` 等)はスタイルより優先されます。
- **学習できないもの**: テロップのフォント・色・アニメーション、トランジション等の
  デザイン要素は映像からの抽出精度が実用にならないため対象外です。

## パラメータの目安

| パラメータ | 既定値 | 説明 |
|---|---|---|
| 無音しきい値 | -35 dB | 声が小さい/ノイズが多い録音は -30、静かな録音は -45 に |
| 最小無音長 | 0.35 秒 | 短くすると細かく刻む。テンポ重視なら 0.25 |
| 頭/尻マージン | 0.10 / 0.15 秒 | 語頭・語尾が切れる場合は増やす |
| Whisper モデル | small | 速度重視 base、精度重視 medium |

フィラー辞書は `--filler-dict my_fillers.txt`(1行1語)で差し替えできます。

## 仕組みと制約

- **無償版 Resolve は外部アプリからの制御ができない**(Studio のみ)ため、
  「①このツールで解析してファイル生成 → ②Resolve 内のスクリプトで適用」の2段構成です。
  解析結果の受け渡しは `~/.resolve_assist/latest.json` 経由で自動化しています。
- Studio 版に移行すると、GUI の「Resolveへ直接適用 (Studio)」ボタンが使えるようになり、
  Resolve 側の操作なしでタイムラインが生成されます。
- フィラー検出は Whisper の単語タイムスタンプに依存するため、多少の誤検出・取りこぼしがあります。
  既定ではカットせずマーカー提示に留めているのはこのためです。

## 開発

```bash
pip3 install -e '.[dev]'
pytest tests/
```
