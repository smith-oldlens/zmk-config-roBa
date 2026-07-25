# baseball-photo-select

少年野球撮影の「ワイヤレステザー → Lightroom自動取り込み → AI自動セレクト」システム。

Sony α7C II で撮影した写真を Wi-Fi (FTP) またはカードコピーで PC に取り込み、
AI パイプラインがピンボケ/ブレを除外・決定的瞬間を優先して星を付け、
Lightroom Classic を開いたときにはセレクト済みの写真だけが並んでいる状態を作る。

## リポジトリ内の構成

```
baseball-photo-select/
├── README.md                 ← このファイル
├── pyproject.toml            ← [M1] パッケージ定義(bps コマンド)
├── config.example.yaml       ← 実行時設定の完全な仕様(コピーして config.yaml にする)
├── bps/                      ← 本体パッケージ
│   ├── config.py             ← [M1] 設定ロード+厳格な検証
│   ├── db.py                 ← [M1] SQLite 状態機械(spec §2/§3)
│   ├── ingest.py             ← [M1] 完全性検証・リネーム・登録(spec §4)
│   ├── grouping.py           ← [M2] 連写グループ化・確定判定(spec §5)
│   ├── scoring/              ← [M2] 採点(spec §6)
│   │   ├── exposure.py       ←   露出破綻(唯一の無条件除外)
│   │   ├── subject.py        ←   主被写体選定(検出器は未接続=中央クロップで動作)
│   │   ├── sharpness.py      ←   被写体シャープネス+セッション内校正
│   │   ├── moment.py         ←   決定的瞬間(M5 まで 0.0 で縮退)
│   │   └── composite.py      ←   星の確定と採点ラン
│   ├── cli.py                ← init / ingest / status / finalize / calibrate
│   └── log.py                ← ローテーティングログ
├── docs/
│   ├── 01-architecture.md    ← 確定した全体設計(なぜこの形か、変更禁止事項)
│   ├── 02-spec-pipeline.md   ← 実装仕様書(モジュール/関数シグネチャ/DBスキーマ/アルゴリズム)
│   ├── 03-milestones.md      ← 実装マイルストーン M0〜M6(受け入れ基準+実装プロンプト付き)
│   ├── 04-setup-guide.md     ← α7C II / FileZilla / ルーター / Lightroom の設定手順
│   ├── 05-productization.md  ← 製品化ロードマップ(ライセンス判定表・機種プロファイル・販売チャネル)
│   ├── OPEN_QUESTIONS.md     ← 実装中に確定させる事項(M0 で実機確認)
│   └── af-tag-samples/       ← M0 で実機 α7C II の AF タグ JSON を保存する場所
├── scripts/                  ← [M0 実装済] 実機作業を補助するヘルパー
│   ├── m0_write_test_stars.py  ← テスト JPEG に星 0/3/5 を書き Lightroom 経路を検証
│   └── m0_dump_af_tags.py       ← 実機 JPEG から Sony AF タグを抽出・報告
└── tests/
    ├── conftest.py           ← [M1] 合成 JPEG フィクスチャ(EXIF 付き)
    ├── test_config.py / test_db.py / test_ingest.py  ← [M1] 自動テスト
    ├── test_m0_helpers.py    ← M0 ヘルパーの自動テスト(pytest)
    └── manual/
        └── m0-e2e-checklist.md  ← [M0 実装済] 実機 E2E 検証チェックリスト(人間が実施)
```

## 進捗

- **M0 実装済**: E2E 検証チェックリスト(`tests/manual/m0-e2e-checklist.md`)と
  補助スクリプト(`scripts/`)、自動テスト。
  → **人間の作業待ち**: チェックリストを実施(Lightroom への星反映確認+実機 α7C II の AF タグ確認)。
- **M1 実装済**: config / DB 状態機械 / ingest / CLI(`init`, `ingest`, `status`)。
  受け入れ基準の実測: 合成 100 枚のカード取り込みが **0.5 秒**(予算 5 秒)、
  `bps status` が VERIFIED=100、カード原本は無傷、破損ファイルは削除されず quarantine/ へ。
  3 回連続実行しても状態が変わらない(冪等)。
- **M2 実装済(検出器を除く)**: 連写グループ化・露出判定・被写体シャープネス+セッション内校正・
  星の確定、`bps finalize` / `bps calibrate`。受け入れ基準の実測: 合成 **200 枚を 3.6 秒**(予算 4 分)。
- **AF メタデータ対応済み(M0 の実機確認を反映)**: α7C II が `FocusLocation` に加え
  `FocusFrameSize` を記録し、実運用では瞳追尾で選手を捉えていることを確認。
  ingest 時に AF を読み(XMP 書き込み前に必ず実行)、採点時の主被写体として使う。
  → **人物検出モデルなしでも「背景ボケ」と「被写体ブレ」を区別できる**。
  未着手は §6.2 の RTMDet-nano のみで、足せば精度がさらに上がる位置づけ。
- **テスト: pytest 154 件パス**。
- M3 以降: 未着手(docs/03 参照)。**M0 の残り(Lightroom への星反映確認)は未実施**。

### 使い方(M2 時点)

```bash
pip install -e .              # または: pip install pyyaml piexif opencv-python-headless numpy
cp config.example.yaml config.yaml   # base_dir を自分の環境に合わせる
bps init                      # base_dir 配下のフォルダと DB を作成
bps ingest E:/DCIM/100MSDCF   # 取り込み→グループ化→採点まで一括(カード原本は消さない)
bps status                    # 状態別の枚数を表示
bps calibrate --sample <過去の試合フォルダ>   # 閾値校正用のシャープネス分布を出す
```

現時点では**星は DB 内にとどまります**。ファイルへの XMP 書き込みと Lightroom への配送は M3。

## この設計書の使い方(実装を担当するAIモデルへ)

この設計は上位モデル(設計担当)が調査・レビュー済みで、**設計判断はすべて確定している**。
実装担当は以下のルールで作業すること:

1. **docs/03-milestones.md の M0 から順に実装する。** マイルストーンを飛ばさない。
   各マイルストーンには「実装プロンプト」「成果物」「受け入れ基準」が明記されている。
2. **docs/02-spec-pipeline.md が唯一の実装仕様。** 関数シグネチャ・DBスキーマ・状態遷移・
   スコア計算式・閾値はこの文書の通りに実装する。仕様にない挙動を発明しない。
3. **docs/01-architecture.md の「変更禁止事項」に反する実装をしない。**
   (例: 星の確定前に Lightroom 監視フォルダへファイルを置かない、
   AF メタデータ読み出しを XMP 書き込みより後にしない)
4. 仕様の曖昧さ・矛盾を見つけた場合は、勝手に解釈せず TODO コメントと
   docs/OPEN_QUESTIONS.md への追記で明示し、最も保守的な(データを失わない)実装を選ぶ。
5. 各マイルストーンの受け入れ基準は pytest で自動検証できる形で実装する。
   実カメラ・実 Lightroom が必要な基準は手動チェックリストとして
   `tests/manual/` に markdown で残す。

## 前提環境

- カメラ: **Sony α7C II**(FTP background transfer / 5GHz 対応。docs/04 参照)
- PC: Windows ノート PC(Python 3.12)。GPU なしで全機能が動くこと(必須要件)
- Lightroom Classic(自動読み込み=Auto Import を使用)
- 費用: Phase 0〜3 は追加費用ゼロ。無線化(Phase 4)のみルーター等 約1.5万円

## フェーズ概要(詳細は docs/03)

| Phase | 内容 | 価値 |
|---|---|---|
| 0 | E2E 検証(exiftool 星書き → LR 自動取り込み)+ α7C II の AF タグ実地確認 | 仕組みの成立確認 |
| 1 | 既製品ベンチマーク(facet / LrC Assisted Culling)+ 過去写真で精度基線測定 | 精度目標の確定 |
| 2 | パイプライン本体(カード運用で実戦投入可) | **価値の9割はここで出る** |
| 3 | 決定的瞬間の学習(過去の Lightroom セレクトを教師に)+ 全員カバレッジ保護 | セレクト品質の完成 |
| 4 | ワイヤレス層(FTP 受信・現場無人運用・ハートビート通知) | 「撮って数秒で LR に出る」 |

設計の経緯・調査ソース・2回の独立レビューの要約は docs/01-architecture.md 末尾の付録を参照。
