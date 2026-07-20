# baseball-photo-select

少年野球撮影の「ワイヤレステザー → Lightroom自動取り込み → AI自動セレクト」システム。

Sony α7C II で撮影した写真を Wi-Fi (FTP) またはカードコピーで PC に取り込み、
AI パイプラインがピンボケ/ブレを除外・決定的瞬間を優先して星を付け、
Lightroom Classic を開いたときにはセレクト済みの写真だけが並んでいる状態を作る。

## リポジトリ内の構成

```
baseball-photo-select/
├── README.md                 ← このファイル
├── config.example.yaml       ← 実行時設定の完全な仕様(コピーして config.yaml にする)
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
    ├── test_m0_helpers.py    ← M0 ヘルパーの自動テスト(pytest)
    └── manual/
        └── m0-e2e-checklist.md  ← [M0 実装済] 実機 E2E 検証チェックリスト(人間が実施)
```

## 進捗

- **M0 実装済**: E2E 検証チェックリスト(`tests/manual/m0-e2e-checklist.md`)と
  補助スクリプト(`scripts/`)、自動テスト(`tests/test_m0_helpers.py`, 14件パス)。
  次は**人間が**チェックリストを実施(Lightroom への星反映確認+実機 α7C II の AF タグ確認)。
- M1 以降: 未着手(docs/03 参照)。

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
