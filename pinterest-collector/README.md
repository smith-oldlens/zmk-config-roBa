# pinterest-collector

Pinterest から自分好みの写真・イラストを自動収集する CLI ツールです。

## 仕組み

1. **収集** — 2つのソースからピンを取得します
   - **公式 API v5**: キーワード検索(要アクセストークン)
   - **公開 RSS フィード**: 好きな作家・ボードの新着(APIキー不要)
2. **スコアリング** — タイトル・説明文に含まれる「好きなキーワード」で加点、「嫌いなキーワード」で減点。オプションで CLIP モデルにより**画像の内容そのもの**を自然言語プロンプトと照合してスコアリングできます
3. **収集** — スコアが `min_score` 以上のピンを:
   - ローカルにダウンロード(画像 + メタデータ JSON)
   - （オプション)自分の Pinterest ボードに自動保存(API ソースのみ)
4. **重複防止** — 一度評価したピンは `state.json` に記録され、次回以降スキップされます。さらに **pHash(dHash)** でURLやピンIDが違っても同じ画像なら除外します
5. **HTML ギャラリー** — 収集画像を一覧できる `gallery.html` を自動生成。**カテゴリタブ・検索・並び替え(スコア順/新着順)・ライトボックス拡大**に対応し、各画像に 👍/👎 を付けられます
6. **お気に入り学習** — 👍/👎 の評価から特徴的なキーワードを学習し、次回以降のスコアリング精度を自動で高めます
7. **通知** — 収集結果を Gmail でメール通知(頻度・時刻を設定可能)

## セットアップ

```bash
cd pinterest-collector
pip install -r requirements.txt
cp config.example.yaml config.yaml
# config.yaml を編集して好みのキーワードとフィードを設定
```

### RSS だけで使う(いちばん簡単)

Pinterest の公開ユーザー/ボードには RSS フィードがあります:

```
https://www.pinterest.com/<ユーザー名>/feed.rss          # ユーザーの全ピン
https://www.pinterest.com/<ユーザー名>/<ボード名>.rss    # 特定のボード
```

好きな作家やキュレーションボードのフィードを `config.yaml` の `sources.rss.feeds` に並べるだけで動きます。

### 公式 API を使う(キーワード検索・ボード自動保存)

#### かんたん版(手動更新・お試し)

1. <https://developers.pinterest.com/apps/> でアプリを作成(Trial Access が自動付与、無料)
2. アプリ管理画面からアクセストークンを発行し、環境変数に設定:

```bash
export PINTEREST_ACCESS_TOKEN="xxxxx"
```

3. `config.yaml` で `sources.api.enabled: true` にして `queries` を設定

この方法はトークンが約30日で失効するため、その都度再発行が必要です。

#### 自動更新版(リフレッシュトークン・おすすめ)

一度セットアップすれば、以後トークンを手動で貼り直す必要がなくなります。

1. アプリ管理画面で **Client ID** と **Client Secret** を確認し、環境変数に設定:

```bash
export PINTEREST_CLIENT_ID="xxxxx"
export PINTEREST_CLIENT_SECRET="xxxxx"
```

2. 初回だけ、対話形式のセットアップコマンドを実行:

```bash
python -m collector --config config.yaml --setup-auth
```

表示されたURLをブラウザで開いて認可し、リダイレクト先URL(`?code=...` を含む)をそのまま貼り付けます。成功すると `token_cache_file`(既定で `token_cache.json`)にアクセストークンとリフレッシュトークンが保存されます。

3. 以降は `python -m collector -c config.yaml` を実行するだけで、トークンが期限切れの場合は自動的に更新されます
4. GitHub Actions で使う場合は、コマンドの出力に表示される `PINTEREST_CLIENT_ID` / `PINTEREST_CLIENT_SECRET` / `PINTEREST_REFRESH_TOKEN` をリポジトリの Secrets に登録してください(ワークフローは `token_cache.json` もキャッシュするため、Actions 上でも自動更新が継続します)

## 実行

```bash
# まずはドライラン(何が選ばれるかスコア付きで表示のみ)
python -m collector --config config.yaml --dry-run

# 本番実行(ダウンロード + ボード保存)
python -m collector --config config.yaml
```

出力例:

```
INFO Fetched 78 pins (42 new).
INFO 12 pins passed the preference filter (min_score=1.0).
INFO   [3.00] 水彩で描く朝の湖畔 https://www.pinterest.com/pin/1234.../ {'keywords': ['+水彩', '+landscape']}
...
INFO Downloaded 12 images to /path/to/collected
INFO Gallery updated: /path/to/collected/gallery.html (12 items)
```

## ギャラリーとお気に入り学習

収集を実行すると、`collected/gallery.html` が自動生成されます(`output.gallery: false` で無効化可)。ブラウザで開くとサムネイル一覧が表示され、サーバー不要で動作します。

- **カテゴリタブ** — `config.yaml` の `categories` で定義したカテゴリごとにタブ表示。どれにも該当しない画像は「その他」タブへ。config を編集すれば次回のギャラリー生成時に全画像が再分類されます
- **検索** — ヘッダーの検索ボックスでタイトル・説明文を絞り込み
- **並び替え** — スコア順 / 新着順をボタンで切り替え
- **ライトボックス** — 画像クリックで拡大表示。←/→ キーで前後移動、Esc で閉じる(拡大表示中も 👍/👎 可)

```bash
# 画像を集めずにギャラリーだけ再生成したいとき(カテゴリ定義の変更後などに)
python -m collector -c config.yaml --gallery
```

### 好みを学習させる手順

1. `gallery.html` をブラウザで開き、気に入った画像に 👍、好みでない画像に 👎 を付けます(評価はブラウザ内に保存されます)
2. ヘッダーの **「⬇ Export feedback.json」** ボタンで評価をダウンロードし、`pinterest-collector/feedback.json` として保存します
3. 学習コマンドを実行します:

```bash
python -m collector -c config.yaml --learn
```

👍/👎 した画像の説明文から特徴的な語を抽出し、`learned.json` に好み/苦手キーワードとして蓄積します。以降の収集では、この学習済みキーワードが `config.yaml` の設定に上乗せされてスコアリングに反映されます(手書きの `config.yaml` は変更しません)。使うほど精度が上がります。

出力例:

```
INFO Learning from 8 liked and 3 disliked pins.
INFO Learned 6 like / 2 dislike keywords (total now 6 / 2).
INFO   liked terms:    watercolor, misty, forest, morning, ...
INFO   disliked terms: neon, city
```

### キーワード候補の提案

収集済み画像の説明文から、まだ登録していない頻出語を集計して候補表示します。手動で `config.yaml` に足したい語を見つけるのに便利です。

```bash
python -m collector -c config.yaml --suggest
```

## 重複画像の除外(pHash)

同じ絵が別URL・別ピンIDで何度も再ピンされることがあります。ダウンロード時に各画像の **dHash**(知覚ハッシュ)を計算し、収集済み画像とハミング距離を比較して、閾値以下なら同一画像とみなしてスキップします。

```yaml
dedupe:
  enabled: true
  max_distance: 5   # 0=完全一致のみ。大きくするほど「似た画像」も除外
```

## メール通知(Gmail)

収集結果を Gmail でメール送信できます。**無料**で、Google アカウントのアプリパスワードのみで動作します(外部サービス不要)。

### セットアップ

1. Google アカウントで **2段階認証**を有効化
2. <https://myaccount.google.com/apppasswords> で**アプリパスワード**を発行
3. 環境変数を設定:

```bash
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="発行された16桁のパスワード"
```

4. `config.yaml` で有効化:

```yaml
notify:
  email:
    enabled: true
    to: null            # 送信先。省略時は GMAIL_ADDRESS 宛
    frequency: daily    # every_run(毎回) | daily(日次) | weekly(週次)
    hour: 8             # daily/weekly: この時刻以降の最初の実行で送信
    min_items: 1        # 溜まった件数がこれ未満なら次回に持ち越し
    max_images: 5       # メールに埋め込む画像の上限
```

採用したピンは `state.json` にキューされ、送信条件を満たした実行でまとめてダイジェスト送信されます(スコア上位N枚をメールに埋め込み)。送信後キューはクリアされ、次回分から再び蓄積します。

> **注意**: メールは collector が実際に実行されたときにのみ送信されます。`frequency: daily` は「1日1回の実行がある前提で、hour 以降の最初の実行時に送る」動作です。cron や GitHub Actions の実行スケジュールに合わせて設定してください。GitHub Actions で使う場合はリポジトリの Secrets に `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` を登録します。

## CLIP による画像スコアリング(オプション)

キャプションではなく**画像の見た目**で判定したい場合:

```bash
pip install -r requirements-clip.txt
```

`config.yaml` の `preferences.clip.enabled: true` にして、好み/苦手を英語の自然文で記述します(例: `"a beautiful watercolor landscape painting"`)。初回はモデル(数百MB)のダウンロードが走ります。

## 自動実行

### ローカル(cron)

```cron
0 8 * * * cd /path/to/pinterest-collector && python -m collector -c config.yaml >> collector.log 2>&1
```

### GitHub Actions

リポジトリに `.github/workflows/pinterest-collect.yml` を同梱しています。

1. リポジトリの Settings → Secrets and variables → Actions で `PINTEREST_ACCESS_TOKEN` を登録(API を使う場合)
2. `pinterest-collector/config.yaml` をコミットするか、ワークフロー内で `config.example.yaml` を使用
3. 毎日定時に実行され、収集した画像は **Actions の Artifacts** としてダウンロードできます

## 注意事項

- 公式 API と公開 RSS のみを使用しており、Pinterest のスクレイピング(ログインした画面の自動操作等)は行いません。これは Pinterest の利用規約への抵触を避けるためです
- ダウンロードした画像は**個人的な閲覧目的**に留めてください。著作権は各作者に帰属します
- API のレート制限に注意してください(`per_query` と `max_items_per_run` で調整可能)

## ディレクトリ構成

```
pinterest-collector/
├── collector/
│   ├── __main__.py      # CLI エントリポイント
│   ├── config.py        # 設定読み込み
│   ├── models.py        # Pin データモデル
│   ├── scoring.py       # キーワードスコアリング
│   ├── clip_scorer.py   # CLIP 画像スコアリング(オプション)
│   ├── categorize.py    # カテゴリ自動振り分け
│   ├── gallery.py       # HTML ギャラリー生成(タブ/検索/ライトボックス)
│   ├── learn.py         # フィードバックからの好み学習・キーワード提案
│   ├── dedupe.py        # 重複画像検出(pHash)
│   ├── notify.py        # Gmail 通知
│   ├── downloader.py    # 画像ダウンロード
│   ├── state.py         # 収集済み・ハッシュ・通知キュー管理
│   └── sources/
│       ├── api.py       # 公式 API v5(検索・ボード保存)
│       └── rss.py       # 公開 RSS フィード
├── config.example.yaml
├── requirements.txt
└── requirements-clip.txt
```
