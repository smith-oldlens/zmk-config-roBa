# 01. 全体設計(確定版)

対象カメラ: **Sony α7C II**(FTP background transfer 搭載・5GHz 11ac 対応・
RAW+JPEG 記録時に JPEG のみ転送可・EXIF Makernotes に AF 位置情報を記録)。

## 1. データフロー

```
入力(2経路、以降の処理は完全に同一):
  A) カードリーダー → inbox/ へ一括コピー           …… Phase 2 の主経路(確実)
  B) α7C II ─ Wi-Fi FTP 自動転送(JPEGのみ)─→ 5GHzルーター → PC の FTPサーバ → inbox/
                                                     …… Phase 4(オプション)
        │
  ① ingest        : 完全性検証 → リネーム → SQLite に状態記録
  ② 確定パス      : 連写グループ確定 → スコアリング → 星の確定
  ③ 書き込み      : JPEG に XMP Rating 埋め込み(exiftool 常駐モード)
                    ★3以上について対応 ARW 用サイドカー .xmp とファイル名リストを出力
  ④ 配送          : deliver/(Lightroom Auto Import の監視フォルダ)へ move
        │
  Lightroom Classic 自動読み込み → スマートコレクション
    「AIセレクト」= 評価≥3 かつ 取り込み日=今日
    「決定的瞬間」= 評価≥5
```

## 2. 確定した設計判断とその理由

| 判断 | 理由 |
|---|---|
| 星(XMP-xmp:Rating)+カラーラベルを主キーにする。Pick フラグは使わない | Lightroom は XMP からの Pick 読み込みが保証されない。Rating/Label は取り込み時に確実に反映される |
| JPEG は埋め込み XMP、ARW はサイドカー .xmp | Lightroom は JPEG のサイドカーを読まない(RAW のみサイドカー対応) |
| 星の確定は連写グループ完結後のみ。確定前に deliver/ へ置かない | Lightroom は取り込み時に一度しかメタデータを読まない。後から XMP を書き換えても反映されない |
| グループ完結判定 =「最終受信から120秒静穏」AND「ファイル番号の欠番なし」 | FTP 運用時の Wi-Fi 断で連写が分割着弾してもグループが千切れない |
| ingest と確定パスの2段構成(それ以上分割しない) | 現場では軽い処理のみ・重い推論は確定パスに寄せる(電池/熱対策)。段数を増やすと状態遷移が複雑化するだけ |
| 主被写体 = AF 位置(Makernotes)と重なる person 検出枠 | 少年野球は画面内に打者/捕手/審判/野手が同時に写る。最大 bbox 選択では別人を評価してしまう |
| シャープネス評価は主被写体クロップ内のみ | 背景ボケ・流し撮り・バックネット金網による誤判定(良カットの誤除外/失敗カットの誤採用)を防ぐ |
| 全体画像での即時除外は「露出の完全破綻」のみ | 全体シャープネスによる粗除外は流し撮り・望遠開放のベストカットを門前払いするリスクが最大 |
| 決定的瞬間: グループ間の序列=埋め込み分類器(過去の LR セレクトが教師)、グループ内の序列=シャープネス+ポーズ特徴 | 連写の隣接フレームは埋め込みがほぼ同一で、分類器はグループ内選定には効かない |
| 自動 Reject は「同一グループ内に代替カットがある場合」のみ | 「ある子の唯一の1枚」を品質理由で消すのはピンボケを残すより重い失敗 |
| RAW は全量取り込みせず「★3以上の ARW 選抜コピー」方式 | JPEG 先行取り込み+ARW 後入れはカタログ2重登録になる。選抜リストで必要カットだけコピー |
| facet 等の既製 OSS をランタイム依存にしない | facet の AI 採点は GPU 限定・XMP 出力は JPEG 経路で使えない。必要部品(検出/埋め込み/OpenCV)を直接使う方が薄い |
| 検出=RTMDet、pose=RTMPose、埋め込み=SigLIP2(いずれも Apache-2.0)。**Ultralytics YOLO は使わない** | YOLO11 は AGPL-3.0 で、エクスポート済み重みにも AGPL が及ぶという Ultralytics の主張がある。将来の販売(§6)で係争リスク。最初から Apache 系で実装すれば差し替え不要 |
| exiftool は `-stay_open` 常駐モード必須 | Windows でのプロセス起動は 200〜600ms/回。1枚1プロセスでは破綻する |

## 3. 変更禁止事項(実装時に破ってはならない制約)

1. **AF 位置(Makernotes)の読み出しは、そのファイルへの XMP 書き込みより前に行う。**
   exiftool の再書き込みで Makernotes のオフセットが動き、AF 情報が読めなくなる恐れがある。
2. **deliver/ へ move してよいのは、星が確定し XMP 書き込みが完了したファイルのみ。**
3. **inbox/ のファイルを削除しない。** 処理後は work/ へ move。失敗時は quarantine/ へ move。
   いかなるエラーパスでも撮影データを消さない。
4. **状態はすべて SQLite に永続化し、プロセス再起動時は DB と work/ の再スキャンから復旧する。**
   watchdog のイベントを信頼の根拠にしない(取りこぼし前提)。
5. **GPU を前提にしない。** すべての推論は CPU(ONNX Runtime / PyTorch CPU)で動くこと。
6. **削除の最終判断は人間。** システムは星とラベルで沈めるだけで、ファイル削除機能を持たない。

## 4. α7C II 固有の前提

- FTP background transfer: `[自動FTP転送:入]` で撮影と同時にバックグラウンド転送。
  `[転送対象ファイル: JPEGのみ]` を使い、記録は RAW+JPEG、転送は JPEG のみ(流量制御)。
- Wi-Fi: 5GHz (11ac) 対応。実効 5〜15MB/s → JPEG 1〜2秒/枚。連写はキューイングされ撮影は阻害されない。
- AF 位置: exiftool の Sony Makernotes タグ(`FocusLocation` ほか)にピクセル座標が入る想定。
  **実タグ名と形式は M0 で実機 JPEG に対して確認し、config.yaml の `af.tag_names` に記録する**(下位互換のためのフォールバック順もそこで定義)。
- ファイル名: `DSC0xxxx.JPG`、10000 でロールオーバー。欠番判定は mod 10000 の距離で行う。
- 電源: Wi-Fi 常時 ON で電池消費が増える。現場運用では予備バッテリー2〜3本(docs/04)。

## 5. 処理性能の目標値

| 項目 | 目標 | 備考 |
|---|---|---|
| ingest(検証+リネーム+DB) | 20枚/秒以上 | I/O バウンド |
| 確定パス(検出+採点) | 実効 0.5〜1.0 秒/枚(CPU) | RTMDet-nano 640px + Laplacian + 埋め込み |
| 1試合 1,000 枚の総処理 | 35分以内(バッチ) | 帰宅後カード運用で「10分で最初の閲覧開始」を体感させるため、グループ確定は到着順に逐次行う |
| 除外判定(ブレ/ピンボケ) | recall 90% 以上 | Phase 1 で基線を実測して閾値校正 |
| ベスト取りこぼし | 人間のベストの 70〜90% を ★3 以上に | グループ間序列の目標 |

## 6. 製品化制約(将来の販売を見据えた設計ルール)

本ツールは将来「Sony 機対応の写真自動セレクト製品」として販売する構想がある(docs/05-productization.md)。
個人利用段階から以下を守ることで、製品化時の作り直しを防ぐ:

1. **ランタイム依存のライセンスは Apache-2.0 / MIT / BSD 系のみ。AGPL・研究限定ライセンスは禁止。**
   新しいモデル・ライブラリを追加する際は docs/05 の判定表に追記してから使う。
   (既知の NG: Ultralytics YOLO 系 = AGPL、Apple MobileCLIP 重み = 研究限定)
2. **ExifTool は無改変で外部プロセスとして呼ぶ**(Artistic License 側を選択)。改変・静的組み込みをしない。
   配布時はライセンス表記を同梱(XnView MP 等に同梱配布の前例あり)。
3. **推論は onnxruntime に統一。torch を実行時依存にしない。**
   顧客 PC 内でのパーソナライズ学習(将来機能)も「ONNX 埋め込み + scikit-learn」で完結させる。
4. **機種依存は「カメラプロファイル」に外部化する。** AF タグの優先順・座標形式・FTP 可否を
   コードでなくデータ(config の camera_profile)として持つ。FocusLocation は 2015 年世代以降の
   Sony 機でほぼ共通のため、Sony 横断対応の見通しは良い(spec §7.2 の中心フォールバック判別が前提)。
5. **ローカル完結を製品の中核価値とする。** 子供の写真を一切クラウドに送らない
   (ハートビート通知は枚数などの数値のみ)。VLM 採点等のクラウド機能を将来入れる場合も
   明示的オプトインとする。

## 7. 付録: 設計の経緯(要約)

- 3本の詳細調査(2026-07 時点): Sony FTP 転送の機種別対応と実効速度 / Lightroom Auto Import と
  XMP 反映条件 / 無料・OSS でのカリング手法(pyiqa, YOLO, CLIP, pose, facet 等)。
- 独立レビュー1回目(現場運用視点): リアルタイム処理と星確定のレース条件、バッテリー/熱/無人監視、
  ネット越し撮影の誤判定、全員カバレッジ問題を指摘 → 確定パス分離・バッチ優先のフェーズ順に修正。
- 独立レビュー2回目(ML実装視点): facet の CPU 採点は不可(GPU限定)という事実誤認の訂正、
  機種確認の Phase 0 前倒し、欠番 AND 条件、.lrcat 教師データ抽出の成立確認(SQLite 直読)、
  分類器の適用範囲をグループ間に限定、ARW 選抜コピー方式への一本化 → すべて本設計に反映済み。

主要ソース(実装時に参照が必要になり得るもの):
- Sony α7C II FTP ヘルプガイド: https://helpguide.sony.net/ilc/2360/v1/ja/contents/0704L_ftp_transfer.html
- LrC Auto Import: https://helpx.adobe.com/lightroom-classic/help/import-photos-automatically.html
- Jeffrey's Folder Watch(Auto Import 代替): https://regex.info/blog/lightroom-goodies/folder-watch
- Sony AF 可視化(タグ実在の根拠): https://github.com/SK-Hardwired/s_afv
- facet(Phase 1 ベンチマーク対象): https://github.com/ncoevoet/facet
