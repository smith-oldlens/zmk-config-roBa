# 03. 実装マイルストーン(M0〜M6)

実装担当モデルへ: **M0 から順に、1マイルストーン=1PR(または1コミット群)で進める。**
各マイルストーンの「実装プロンプト」はそのまま作業指示として使える粒度で書いてある。
受け入れ基準を満たすまで次へ進まない。仕様の詳細はすべて docs/02-spec-pipeline.md(以下「spec」)。

---

## M0: E2E 成立検証+実機タグ確認(コード最小・半日)【人間の作業が主】

**目的**: 「exiftool で星を書いた JPEG を Lightroom が自動取り込みで星付き表示する」ことと、
α7C II の AF タグの実形式を、パイプラインを書く前に確認する。

**実装プロンプト**:
- `tests/manual/m0-e2e-checklist.md` を作成し、以下の手順書を書く:
  1. 適当な JPEG 3枚に `exiftool -overwrite_original -XMP-xmp:Rating=3` 等で星 0/3/5 を書く
  2. docs/04 の手順で LrC の自動読み込みとスマートコレクションを設定
  3. 監視フォルダに 3 枚を置き、スマートコレクション「AIセレクト」に ★3/★5 の 2 枚だけが
     現れることを確認
  4. α7C II で撮った実 JPEG(AF-C で人物撮影したもの)に対し
     `exiftool -j -G -Sony:All -Composite:All <file>` を実行し、出力 JSON を
     `docs/af-tag-samples/` に保存。FocusLocation 系タグの実名・形式を確認し、
     config.example.yaml の `af.tag_names` を実測値で更新する
  5. 過去写真の棚卸し: 「全ショット(不採用含む)が残っている過去試合」が何試合分あるかを記録
     (Phase 3 の教師データ成立条件。→ `docs/OPEN_QUESTIONS.md` に結果を記入)

**受け入れ基準**: チェックリストの全項目に結果(スクリーンショット or テキスト)が記録されている。

---

## M1: プロジェクト骨格+ingest+DB(コードの土台)

**実装プロンプト**:
- pyproject.toml(deps は spec 冒頭の通り。torch/open_clip は開発スクリプト専用の
  optional-dependencies `[dev-ml]` に分離し、ランタイム依存に入れない。sklearn は `[ml]`)、`bps/config.py`、`bps/db.py`、`bps/ingest.py`、
  `bps/cli.py` の `init` / `status` / `ingest`(グループ化なし版)を実装。
- spec §3 のスキーマ、§4 の検証/リネーム/状態遷移を厳密に実装。
- tests: 合成 JPEG での ingest 正常系、EOI 欠損ファイルの quarantine 行き、
  同名衝突 `_dupN`、再実行しても二重登録されない冪等性。

**受け入れ基準**: `pytest` 緑。`bps ingest <合成100枚>` が 5 秒以内に完了し
`bps status` が VERIFIED=100 を表示。quarantine 系テストでファイルが一切削除されないこと。

---

## M2: グループ化+シャープネス+星確定(AI最小構成)

**実装プロンプト**:
- spec §5(grouping)、§6.1(露出)、§6.2(主被写体: RTMDet-nano ONNX。
  `models/rtmdet_nano.onnx` の取得/変換スクリプト `scripts/fetch_models.py` を同梱。
  mmdetection の公開済み ONNX または rtmlib 配布物を利用。**Ultralytics は使わない** —
  docs/01 §6 のライセンス制約)、§6.3(シャープネス+セッション校正)、
  §6.5(星確定。moment は恒等 0.0)を実装。
- `bps ingest` を「登録→全グループ即確定→スコア→星決定」まで通す。
  この時点では XMP 書き込みはスタブ(DB の rating 更新まで)。
- tests: 合成連写グループでベスト 1 枚に rating=3、全面ブレに rating=1+Purple、
  露出破綻に即 rating=1、単写の低シャープが rating=0 に留まること、
  欠番保留→gap_wait_max 超過で強行確定、mod 10000 ロールオーバー跨ぎ。

**受け入れ基準**: `pytest`(slow 含む)緑。合成 200 枚のグループ判定・星分布が仕様通り。
CPU で 200 枚が 4 分以内。

---

## M3: メタデータ I/O+配送(Lightroom 接続=Phase 2 完成)

**実装プロンプト**:
- spec §7 を実装: exiftool 常駐、AF 読み出し(XMP 書込前の順序を assert で強制)、
  XMP 書き込み+読み戻し検証、deliver/ への move、ARW サイドカー+select_list.txt、
  `bps export-raw`。
- `bps ingest` のフルフロー(カード→deliver/ に星付き JPEG が並ぶ)を完成させる。
- tests: 書き込み後の読み戻し一致、サイドカー内容、select_list の重複排除、
  export-raw の欠落 ARW WARNING。
- `tests/manual/m3-lightroom-checklist.md`: 実 LR での星反映確認、ARW 後入れで
  サイドカーの星が付くこと、2重登録が起きないこと。

**受け入れ基準**: `pytest` 緑+手動チェックリスト完了。
**この時点で実戦投入可能**(帰宅後: カード挿入 → `bps ingest E:\DCIM\...` → LR を開くだけ)。

---

## M4: 常駐モード(watch)

**実装プロンプト**:
- spec §9 を実装(observer/ingest worker/finalize loop/rescan loop、起動時復旧、
  Ctrl-C 安全停止)。quiet_seconds=120+欠番 AND 条件が生きる経路。
- tests: スレッド無しで各ループ関数を直接呼ぶ単体テスト+
  「クラッシュ再起動シミュレーション」(spec §12)。
- `scripts/setup_windows.ps1`: スリープ無効化・Windows Update 一時停止・
  電源プラン設定を行う PowerShell(実行は人間の判断)。

**受け入れ基準**: `pytest` 緑。合成ファイルを 1 枚/2秒で inbox に流し込む
シミュレーションテストで、グループが 120 秒後に確定し deliver/ に到達する。

---

## M5: Phase 3 — 決定的瞬間+カバレッジ保護

**実装プロンプト**:
- spec §10(train.py: lrcat 抽出→負例チェック→SigLIP2 埋め込み(ONNX)→LogisticRegression
  →GroupKFold レポート)、§6.4 の本実装、§6.5 への moment 合流。
- グループ内序列の補助特徴: RTMPose-m(ONNX)で主被写体のキーポイントを取り、
  「手首の高さ」「肘角度」「体幹傾き」を in-group タイブレークに使う
  (keep_score 同率±0.05 のときのみ適用。壊れやすいルールを主判定にしない)。
- カバレッジ保護(spec §6.5 の拡張): 顔クラスタは Phase 3 では実装せず、
  代替として「試合単位で rating>=3 の枚数がグループ数の 5% 未満のグループには
  ベスト 1 枚を必ず rating=3 にする」既存ルール(グループ内ベストは常に★3)を検証で確認。
  顔ベースの本実装は OPEN_QUESTIONS に将来項目として残す。
- tests: モデル欠如時の縮退、負例不足での中断、GroupKFold のリーク防止。

**受け入れ基準**: `bps train` が過去データで PR-AUC をレポートし、
`bps calibrate` の混同行列レポート(Phase 1 基線)に対して取りこぼし率が改善。

---

## M6: Phase 4 — ワイヤレス層

**実装プロンプト**:
- spec §11(ntfy ハートビート)実装+`bps watch` への組み込み。
- `tests/manual/m6-ftp-rehearsal.md`: docs/04 の FTP/ルーター設定で自宅リハーサル
  (連写 100 枚バーストの到達率 100%・最悪遅延・グループ完結時間の記録表つき)。
- FTP 運用時の推奨 config プリセット `config.field.yaml`
  (quiet_seconds=120、notify 有効、試合後 `bps finalize --all` の運用メモ)。

**受け入れ基準**: 自宅リハーサル表が埋まる+練習試合 3 時間の無人完走
(ハートビートが途切れず、帰宅後 finalize で全量が DELIVERED)。

---

> M6 完了後の製品化トラック(P2: 汎用モデル+カメラプロファイル+内蔵FTP → P3: GUI+
> インストーラ → P4: 販売)は docs/05-productization.md を参照。

## Phase 1(ソフト実装ではなく計測作業。M2 と並行可)

- `bps calibrate --sample <過去1試合フォルダ>` を M2 に含めて実装:
  シャープネス生値の分布ヒストグラム、暫定閾値での除外リスト CSV を出力。
- 人間の作業: 過去 1,000 枚に対する自分の手動セレクトとの突合
  (ネット越し/流し撮り/連写を層別)。facet と LrC Assisted Culling も同じ 1,000 枚に
  かけて比較表を作る → 結果を `docs/phase1-baseline.md` に記録し、
  config の reject_pct / keeper_pct を校正する。
