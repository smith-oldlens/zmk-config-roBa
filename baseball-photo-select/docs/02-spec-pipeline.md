# 02. 実装仕様書(唯一の実装リファレンス)

言語: Python 3.12 / パッケージ名: `bps`(baseball-photo-select)。
依存(すべて CPU で動作すること): `opencv-python-headless`, `onnxruntime`, `ultralytics`(YOLO11n の
ONNX エクスポートにのみ使用。ランタイムは onnxruntime 直), `open_clip_torch`+`torch`(CPU版),
`pyexiftool`, `watchdog`, `pyyaml`, `piexif`(EXIF 高速読み取り), `numpy`, `scikit-learn`(Phase 3)。
exiftool 本体はシステムインストール(パスは config で指定可)。

## 1. ディレクトリ構成

### ソースツリー
```
baseball-photo-select/
├── pyproject.toml            # bps = "bps.cli:main" のエントリポイント
├── config.example.yaml
├── bps/
│   ├── cli.py                # argparse サブコマンド(§8)
│   ├── config.py             # yaml ロード+検証(dataclass)
│   ├── db.py                 # SQLite ラッパ(§3)
│   ├── ingest.py             # §4
│   ├── grouping.py           # §5
│   ├── scoring/
│   │   ├── subject.py        # 主被写体特定(§6.2)
│   │   ├── sharpness.py      # §6.3
│   │   ├── exposure.py       # §6.1
│   │   ├── moment.py         # §6.4(Phase 3)
│   │   └── composite.py      # 星決定(§6.5)
│   ├── metadata.py           # exiftool 常駐ラッパ / AF読み出し / XMP書き込み(§7)
│   ├── deliver.py            # deliver/ への move と ARW 選抜出力(§7.4)
│   ├── watch.py              # 常駐モード(§9)
│   ├── train.py              # lrcat 教師抽出+分類器学習(Phase 3, §10)
│   └── notify.py             # ntfy.sh ハートビート(Phase 4, §11)
└── tests/
    ├── conftest.py           # 合成 JPEG フィクスチャ生成(§12)
    ├── test_*.py
    └── manual/               # 実機/実LRが必要な手動チェックリスト(.md)
```

### 実行時レイアウト(config の `base_dir` 配下に自動作成)
```
base_dir/
├── inbox/        # FTP 受信先 or カードコピー先(フラット。ここのファイルは削除しない)
├── work/         # リネーム済み・処理中(パイプラインの作業領域)
├── deliver/      # Lightroom Auto Import の監視フォルダ(確定済みのみ置く)
├── raw_select/   # ARW 用サイドカー .xmp と選抜リスト txt の出力先
├── quarantine/   # 完全性検証に失敗したファイル
├── models/       # yolo11n.onnx / openclip キャッシュ / moment_classifier.pkl
├── logs/         # bps.log(RotatingFileHandler, 10MB×5)
└── state.db      # SQLite
```

## 2. 状態機械(photos.state)

```
RECEIVED ──検証OK──> VERIFIED ──グループ割当──> GROUPED ──グループ確定──> SCORED
    │                                                                      │
    └─検証NG(リトライ3回後)──> QUARANTINED                    XMP書込完了 ─> WRITTEN
                                                                           │
                                                              deliver/へmove ─> DELIVERED
任意の状態 ──例外──> FAILED(error に traceback 要約を記録。再起動時に VERIFIED から再試行)
```

- 状態遷移は必ず `db.transition(photo_id, from_state, to_state, **fields)` を通す。
  from_state が一致しない場合は例外(二重処理防止)。
- 再起動時の復旧: `RECEIVED/VERIFIED/GROUPED/SCORED/FAILED` の全行を対象に、
  ファイル実在を確認した上で各状態のハンドラへ再投入する(§9.3)。

## 3. SQLite スキーマ(db.py)

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS photos (
  id            INTEGER PRIMARY KEY,
  orig_name     TEXT NOT NULL,             -- 'DSC01234.JPG'
  new_name      TEXT UNIQUE NOT NULL,      -- '20260720_133005_123_DSC01234.JPG'(§4.3)
  file_number   INTEGER NOT NULL,          -- 1234(DSC番号。欠番判定用)
  shot_time     TEXT NOT NULL,             -- 'YYYY-MM-DD HH:MM:SS.fff'(EXIF DateTimeOriginal+SubSec)
  received_at   REAL NOT NULL,             -- epoch秒(inboxで検知した時刻)
  state         TEXT NOT NULL,
  group_id      INTEGER REFERENCES groups(id),
  af_json       TEXT,                      -- AF位置の生データ+解釈済み座標(§7.2)。XMP書込前に必ず充填
  scores_json   TEXT,                      -- {"exposure_ok":bool,"subj_sharp":float,"subj_box":[x,y,w,h],
                                           --  "moment":float,"in_group_rank":int}
  rating        INTEGER,                   -- 0/1/3/5
  label         TEXT,                      -- 'Purple'(除外候補) / NULL
  error         TEXT,
  updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_state ON photos(state);
CREATE INDEX IF NOT EXISTS idx_photos_group ON photos(group_id);

CREATE TABLE IF NOT EXISTS groups (
  id            INTEGER PRIMARY KEY,
  start_shot    TEXT NOT NULL,             -- グループ内最古 shot_time
  end_shot      TEXT NOT NULL,             -- グループ内最新 shot_time
  last_received REAL NOT NULL,             -- グループ内で最後に received_at が更新された時刻
  finalized_at  REAL,                      -- NULL=未確定
  best_photo_id INTEGER
);

CREATE TABLE IF NOT EXISTS name_map (      -- 新名⇔元名の対応(ARWサイドカー生成とロールオーバー対策)
  new_name  TEXT PRIMARY KEY,
  orig_name TEXT NOT NULL,
  arw_name  TEXT NOT NULL                  -- 'DSC01234.ARW'(orig_name の拡張子置換)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
-- meta に保存: schema_version, session_started_at, last_heartbeat_at,
--              sharpness_calibration(セッション内パーセンタイル校正値, §6.3)
```

## 4. ingest(ingest.py)

### 4.1 完全性検証 `verify_file(path) -> bool`
1. ファイルサイズを 2 秒間隔で 2 回取得し不変であること。
2. 排他オープン(Windows: `os.open(path, O_RDONLY)` 後に `msvcrt.locking` 試行、
   失敗したら 1 秒待ちリトライ、最大 5 回)。
3. 末尾 64KB 内に JPEG EOI マーカー `FF D9` が存在すること。
4. `piexif.load()` が例外なく DateTimeOriginal を返すこと。
- 3 回(config: `ingest.max_verify_retries`)失敗したら quarantine/ へ move し QUARANTINED。

### 4.2 対象拡張子
`.JPG .JPEG`(大文字小文字不問)。`.ARW` が inbox に来た場合(カード一括コピー時)は
work/arw/ へ move するだけで DB 登録しない(選抜コピーの照合元として温存)。
その他の拡張子は無視(ログのみ)。

### 4.3 リネーム規則
`{shot_time:%Y%m%d_%H%M%S}_{subsec:03d}_{orig_stem}.jpg`
例: `DSC01234.JPG`(2026-07-20 13:30:05.123 撮影)→ `20260720_133005_123_DSC01234.jpg`
- shot_time は EXIF DateTimeOriginal + SubSecTimeOriginal(無ければ `000`)。
- work/ へ move し、photos と name_map に登録(1トランザクション)。
- new_name 衝突時(完全同名)はサフィックス `_dupN` を付け、WARNING ログ。

### 4.4 file_number 抽出
`orig_name` から正規表現 `(\d{4,5})` の最後のマッチを整数化。抽出不能なら -1(欠番判定から除外)。

## 5. 連写グループ化(grouping.py)

### 5.1 割り当て `assign_group(photo) -> group_id`
- 直近の未確定グループの end_shot との差が `grouping.gap_seconds`(既定 2.0 秒)以内なら同グループ。
  超えたら新グループ作成。shot_time 順に処理すること(受信順ではない。DB から shot_time 順で取り出す)。
- グループの start_shot/end_shot/last_received を更新。

### 5.2 確定判定 `finalizable(group, now) -> bool`(§9 のループから毎 10 秒呼ぶ)
以下の AND:
1. `now - group.last_received >= grouping.quiet_seconds`(既定 120。バッチモード時は 0 に上書き)
2. **欠番なし**: グループ内 photos の file_number 集合を mod 10000 で昇順に並べ、
   隣接差がすべて 1 であること。差が 2 以上の欠番が「DB 全体にも存在しない」場合のみ欠番とみなす
   (連写の切れ目でユーザーが単写した番号は別グループに存在するため)。
   欠番がある場合は確定を保留し、保留が `grouping.gap_wait_max_seconds`(既定 600)を超えたら
   WARNING ログを出して確定を強行する(永久ブロック防止)。
- バッチモード(`bps ingest`)では全ファイル登録完了後に全グループを即confirm する。

## 6. スコアリング(scoring/)

### 6.1 露出破綻 `exposure_ok(img) -> bool`(exposure.py)
グレースケール 256bin ヒストグラムで、輝度 <5 の画素が 98% 以上(真っ黒)または
輝度 >250 の画素が 98% 以上(真っ白)のとき False。False は即 rating=1, label='Purple'
(グループ内代替の有無を問わず。完全な黒/白コマは写真として救済不能のため唯一の例外)。

### 6.2 主被写体特定 `find_subject(img, af_point) -> Box | None`(subject.py)
1. YOLO11n(ONNX, 入力 640, conf 0.25, class=person のみ)で人物 bbox 群を取得。
2. `af_point`(§7.2 で取得した AF ピクセル座標、フル解像度基準)があれば:
   AF 点を含む bbox のうち面積最小のもの(手前の別人の巨大 bbox より、AF が刺さった選手を優先)。
3. AF 点がどの bbox にも含まれない/af_point が None のフォールバック:
   `score = area_norm * exp(-d2/(2*sigma^2))`(d2=bbox中心と画像中心の正規化距離二乗, sigma=0.35)
   が最大の bbox。
4. 人物ゼロ検出なら None → 主被写体シャープネスは「画像中央 40% クロップ」で代替計測し、
   scores_json に `"subject_fallback":"center"` を記録。

### 6.3 被写体シャープネス `subject_sharpness(img, box) -> float`(sharpness.py)
- box を 10% パディングしてクロップ → 長辺 512px に縮小 → グレースケール
  → `cv2.Laplacian(ddepth=CV_64F).var()` → `log10(1+var)` を生値とする。
- **セッション内校正**: 生値をそのまま閾値比較しない。DB 内の当セッション
  (meta.session_started_at 以降)の生値分布に対するパーセンタイルに変換し 0..1 とする。
  サンプルが 50 枚未満の間は暫定閾値(config: `sharpness.bootstrap_log10 = 2.0`)との比で代用。
- ブレ/ピンボケ判定: percentile < `sharpness.reject_pct`(既定 0.15)を「低シャープ」とする。

### 6.4 決定的瞬間 `moment_score(img, emb) -> float`(moment.py, Phase 3 まで恒等 0.0)
- グループ間: OpenCLIP ViT-B-32 の画像埋め込み(512 次元)→ 学習済みロジスティック回帰
  (train.py が models/moment_classifier.pkl を出力)の確率 [0..1]。
  **埋め込みは顔クロップを含む全画像だが、学習時に顔 identity を使わない**(§10.3)。
- モデルファイルが無ければ 0.0 を返す(Phase 2 でも動く縮退動作)。

### 6.5 星の確定 `finalize_group(group)`(composite.py)
グループ確定時に一括実行:
1. 各 photo: exposure_ok=False → rating=1/label='Purple'。以降の対象から除外。
2. 各 photo の `keep_score = 0.7 * sharp_pct + 0.3 * moment`。
3. グループ内順位付け(in_group_rank)。**グループ内ベスト** = keep_score 最大。
4. rating 決定:
   - ベスト: rating=3(moment >= `moment.star5_threshold`(既定 0.7)なら 5)
   - ベスト以外で sharp_pct >= `sharpness.keeper_pct`(既定 0.5): rating=3
   - 低シャープ(§6.3)**かつ** グループ内に rating>=3 が存在: rating=1, label='Purple'
   - 上記以外(低シャープだが代替なし、または中間): rating=0(無印。LRには入るが選外)
5. グループサイズ 1(単写)の場合: 低シャープでも rating=0 止まり(自動除外しない)。

## 7. メタデータ I/O(metadata.py)

### 7.1 exiftool 常駐
`pyexiftool.ExifToolHelper` を watch/ingest プロセスで 1 インスタンス保持。
起動オプション: `-n`(数値出力)。すべての読み書きはこのインスタンス経由。

### 7.2 AF 位置読み出し `read_af_point(path) -> AfPoint | None`
- **必ず XMP 書き込み前に呼ぶ**(01-architecture の変更禁止事項)。
- config の `af.tag_names`(M0 で実機確認して確定。既定候補順:
  `MakerNotes:FocusLocation` → `MakerNotes:FlexibleSpotPosition` → `MakerNotes:FocalPlaneAFPointLocation`)
  を順に試し、最初に取れた値を解釈する。
- `FocusLocation` は "W H X Y" 形式(画像幅・高さ・焦点座標)を想定。パース結果と生文字列の
  両方を af_json に保存(後から解釈を修正できるように)。

### 7.3 XMP 書き込み `write_rating(path, rating, label)`
```
exiftool -overwrite_original -XMP-xmp:Rating={rating} [-XMP-xmp:Label={label}] {path}
```
- label は英語名のみ('Purple')。Lightroom 側のカラーラベルセットと文字列一致が必要(docs/04)。
- 書き込み後、読み戻して Rating が一致することを検証(不一致は FAILED)。

### 7.4 配送(deliver.py)
- WRITTEN の photo を deliver/ へ `shutil.move`(同一ボリューム前提。config で検証)。
- rating>=3 の photo について:
  - `raw_select/{arw_name}.xmp` サイドカーを生成:
    `exiftool -o {arw_stem}.xmp -XMP-xmp:Rating={rating} [-XMP-xmp:Label=...]`
    ※既存サイドカーがあれば上書き(`-overwrite_original` 相当の再生成)。
  - `raw_select/select_list.txt` に arw_name を追記(重複排除、ソート済みで保つ)。
- 帰宅後の RAW 選抜コピー: `bps export-raw --card <DCIMパス> --dest <LR取り込み先>` が
  select_list.txt に基づき ARW+サイドカーをまとめてコピーする(存在しない ARW は WARNING)。

## 8. CLI(cli.py)

| コマンド | 動作 |
|---|---|
| `bps init` | base_dir 配下のフォルダ作成+DB 初期化+config 検証 |
| `bps ingest <dir>` | dir(カード/任意フォルダ)を一括処理。quiet_seconds=0 で全グループ即確定。進捗を tqdm 表示 |
| `bps watch` | 常駐モード(§9)。Ctrl-C で安全停止(処理中の 1 枚を完了してから) |
| `bps finalize --all` | 未確定グループを強制確定(試合後一括モード) |
| `bps export-raw --card <dir> --dest <dir>` | §7.4 の ARW 選抜コピー |
| `bps status` | 状態別枚数・未確定グループ数・直近エラーを表示 |
| `bps train --lrcat <path.lrcat> --photo-root <dir>` | Phase 3(§10) |
| `bps calibrate --sample <dir>` | 過去写真でシャープネス分布・閾値の校正レポートを出す(Phase 1) |

## 9. 常駐モード(watch.py)

### 9.1 スレッド構成
- **observer**: watchdog で inbox/ の created/moved を監視 → キューに path を投入するだけ。
- **ingest worker**(1本): キューから取り出し §4 を実行。
- **finalize loop**(1本): 10 秒ごとに §5.2 → 確定グループに §6 → §7 → §7.4。
- **rescan loop**(1本): 60 秒ごとに inbox/ を listdir し、DB 未登録ファイルをキューへ
  (watchdog 取りこぼし対策)。加えて FAILED の再試行(最大3回)。
- **heartbeat**(Phase 4): §11。

### 9.2 推論リソース
YOLO/CLIP のモデルは finalize loop スレッドでのみロード・使用(シングルスレッド推論。
onnxruntime の intra_op threads = 物理コア数-1)。

### 9.3 起動時復旧
DB の非終端状態(RECEIVED/VERIFIED/GROUPED/SCORED/FAILED)を §2 の通り再投入。
work/ に実在するが DB に無いファイルは ingest からやり直す。

## 10. Phase 3: 教師データ学習(train.py)

### 10.1 lrcat 抽出
- .lrcat は SQLite。**Lightroom 終了中にファイルを一時コピーしてから開く**(ロック回避)。
- クエリ: `Adobe_images`(rating, pick, colorLabels)と
  `AgLibraryFile / AgLibraryFolder / AgLibraryRootFolder` を結合し、
  (絶対パス, rating, pick) を得る。正例= rating>=3 または pick=1。
  負例= 同一フォルダ内の rating が NULL/0 かつ pick!=1 の写真。
- 出力: `train_manifest.csv`(path, label)。存在しないパスは除外し、
  正例数・負例数を表示。**負例が正例の 1/3 未満なら中断してユーザーに警告**
  (過去に不採用カットを削除済みのケース。その場合 Phase 3 はポーズ/ボール特徴主体に切替判断)。

### 10.2 学習
OpenCLIP ViT-B-32 埋め込み(バッチ処理、キャッシュを models/emb_cache.npz に保存)
→ `sklearn.linear_model.LogisticRegression(class_weight='balanced', C=1.0)`
→ 5-fold CV の PR-AUC をレポート → models/moment_classifier.pkl に保存。

### 10.3 顔 identity の排除
学習マニフェストから、InsightFace 等での顔クラスタリングは**行わない**。
代わりに「同一人物への過学習」を抑えるため、埋め込みに対しグループ単位で
train/test を分割する(同一連写グループが両側に入らない GroupKFold。group=撮影日+グループ)。

## 11. Phase 4: ハートビート(notify.py)
- 10 分ごとに `https://ntfy.sh/{config.notify.topic}` へ POST:
  `📷 受信{n_received} 処理{n_delivered} 未確定G{n_open_groups} ERR{n_failed}`
- 直近 10 分の受信が 0 かつ 直前 10 分は >0 のとき「⚠️ 受信停止の可能性」を送る。
- ネットワーク不達は握りつぶす(現場はオフライン運用もあり得る。ログのみ)。

## 12. テスト戦略(tests/)
- **合成フィクスチャ**: PIL で 6000x4000 の画像を生成し、(a) シャープな人型矩形+ボケ背景、
  (b) 全面ブレ(GaussianBlur)、(c) 露出破綻(全黒/全白)を作り、piexif で
  DateTimeOriginal/SubSec/ファイル名を付与して連写グループを構成する。
- YOLO/CLIP を使うテストは `@pytest.mark.slow`。CI では合成画像に対する
  「人物ゼロ検出でも落ちない」「モデルファイル欠如で moment=0.0」の縮退パスを必ず検証。
- 状態機械: 「途中クラッシュ→再起動→二重処理なしで DELIVERED に到達」を
  DB 直接操作でシミュレートするテストを必須とする。
- 手動チェックリスト(tests/manual/): LR への星反映、ARW サイドカー反映、
  α7C II の AF タグ確認手順、FTP 受信リハーサル。
