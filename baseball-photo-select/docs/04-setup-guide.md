# 04. セットアップガイド(α7C II / ネットワーク / FTP / Lightroom)

> **対象環境**: 開発者の実環境は **macOS**。以下は Mac を主として書き、Windows は補足に回す。
> パイプライン本体(`bps` コマンド)は OS 非依存で、違いはツールの入れ方とパス表記だけ。

## 0. 事前準備(Mac)

```bash
brew install exiftool               # 必須(星の読み書きに使う)
cd baseball-photo-select
pip install -e .                    # bps コマンドと依存を導入
cp config.example.yaml config.yaml  # base_dir を自分のパスに(例 /Users/<name>/bps)
bps init
```

Windows の場合: exiftool.org から Windows Executable を落として `exiftool.exe` にリネームし
PATH に置く。`base_dir` は `D:/bps` のようなドライブ表記にする。


## 1. α7C II カメラ設定

### 記録設定(Phase 2 から共通)
- 記録: **RAW+JPEG**(RAW はカード保持、解析・転送は JPEG)
- JPEG 品質: ファイン、サイズ L(2.4GHz 混雑時など転送が詰まる場合のみ M に落とす)
- 連写時刻の精度のため、日時設定を PC と合わせておく(ズレはグループ化に影響しないが照合が楽)

### FTP 転送設定(Phase 4)
MENU → ネットワーク → FTP転送機能:
1. `FTP機能: 入`
2. サーバー登録(最大 9 件):
   - ホスト名: PC の IP(ルーター DHCP で固定割り当てにする。例 192.168.8.10)
   - ポート: 21 / パッシブモード: 入
   - ユーザー名/パスワード: FileZilla Server で作成したもの
   - セキュアプロトコル: **切**(平文 FTP。ローカル閉域網なので許容)
3. `自動FTP転送: 入`(撮影と同時にバックグラウンド転送)
4. `転送対象ファイル: JPEGのみ`(RAW+JPEG 記録時)
5. Wi-Fi 接続先: ルーターの **5GHz SSID**(WPA2-Personal。WPA3 専用 SSID は不可)
6. パワーセーブ開始時間: 長め(30秒〜)に設定(短いと転送が中断される)

### 電源(Phase 4 現場運用)
- Wi-Fi 常時 ON で電池消費は体感 1.5〜2 倍。**予備バッテリー(NP-FZ100)2〜3 本**、
  またはベンチ滞在時に USB-C モバイルバッテリー給電。

## 2. ネットワーク(Phase 4)

推奨: **5GHz 対応トラベルルーター**(GL.iNet GL-AXT1800 等)+モバイルバッテリー。
- インターネット不要のローカル AP として動作させる(WAN 未接続で OK)
- 5GHz SSID にカメラと PC の両方を接続。PC の IP を DHCP 予約で固定
- 設置はバックネット裏中央付近(5GHz は金網・人体で減衰、実効 20〜30m。
  撮影者が移動して圏外になっても FTP はキュー&リトライで自動追い付きする)
- ハートビート通知(ntfy)を使う場合のみ、ルーターの WAN にスマホテザリングを繋ぐ
  (通知はオプション。オフラインでもパイプラインは動く)

代替(機材ゼロ): Windows モバイルホットスポットにカメラ直結。ただし
「共有元のネット接続がないと不安定」「無接続アイドルで自動オフ」「パブリック扱いで
ファイアウォールが FTP を塞ぐ」の 3 つの罠があるため、リハーサルで問題が出たらルーターに移行。

## 3. PC 側 FTP 受信(Phase 4)

**Mac の推奨**: `pyftpdlib`(MIT)を使う。macOS は標準の FTP サーバを廃止しており、
インストール不要で一行起動できるこれが最も素直:

```bash
pip install pyftpdlib
python -m pyftpdlib -p 2121 -w -d /Users/<name>/bps/inbox -u sony -P <password>
```
- `-w` = 書き込み許可、`-u/-P` = カメラに入力するユーザー名/パスワード
- 1024 番未満(21 番)を使うには root 権限が要るため、**2121 番など高いポートを使い、
  カメラ側の FTP ポートも同じ値にする**のが簡単
- macOS のファイアウォールが有効なら、初回起動時のダイアログで python の着信接続を許可

Windows の場合: FileZilla Server(無料)。ホームを `base_dir/inbox/` にし、
**「Allow plain FTP」を明示的に有効化**(1.x 系は既定で TLS 必須。これをしないと
カメラが繋がらない)。パッシブポート範囲を固定し、Defender の受信規則で 21 番と
その範囲を許可する。

## 4. PC 設定(現場運用)

**Mac**: スリープを止めないと転送も処理も落ちる。`bps watch` を `caffeinate` 経由で起動する:
```bash
caffeinate -dimsu bps watch          # 実行中はスリープ/画面オフを抑止
```
- 蓋を閉じて運用したい場合は電源接続+外部ディスプレイなしだとスリープするため、
  蓋は開けたまま伏せない運用にするか、`caffeinate` に加えて「バッテリー使用時に自動スリープしない」を
  システム設定 > バッテリー で確認する
- ソフトウェアアップデートの自動再起動を一時停止しておく

**共通**: 設置はハードケース内・日陰。**夏場の車内放置は禁止**(60℃ 超で熱停止する)。
盗難対策に人目につかない位置+ケーブルロック。

## 5. Lightroom Classic 設定

### 自動読み込み(Auto Import)
1. `ファイル → 自動読み込み → 自動読み込み設定`
2. 監視フォルダ: `base_dir/deliver/`(**空フォルダであること・サブフォルダ不可**)
3. 移動先: LR 管理下の取り込みフォルダ(例 `D:\Photos\auto\`)
   ※ Auto Import はファイルを監視フォルダから移動先へ**移動**する仕様
4. ファイル名: 変更なし / 現像設定: なし / メタデータ: なし(星は XMP から読まれる)
5. 不安定な場合の代替: Jeffrey's "Folder Watch" プラグイン(in-place 読み込み・
   ファイル安定待ちあり) https://regex.info/blog/lightroom-goodies/folder-watch

### カラーラベルセット
`メタデータ → カラーラベルセット → 編集` で、ラベル名が英語
(Red/Yellow/Green/Blue/Purple)になっているセットを選ぶ。
**パイプラインは 'Purple' という文字列を書くため、名称が一致しないと色が表示されない。**

### スマートコレクション
- 「AIセレクト」: 評価 ≥ ★3 かつ 取り込み日 = 今日
- 「決定的瞬間」: 評価 ≥ ★5
- 「要確認(除外候補)」: ラベル = パープル → 人間が最終確認して削除判断
  (システムは削除しない。ここだけは人間の仕事)

### RAW の後入れ(帰宅後)
1. `bps export-raw --card /Volumes/<CARD>/DCIM --dest ~/Photos/raw_selects`
   → ★3 以上の ARW と .xmp サイドカーだけがコピーされる(カードは読むだけ)
2. LR で `~/Photos/raw_selects` を通常読み込み → 星付きで入る
3. JPEG と ARW は別ファイルとしてカタログに載る(JPEG=全量+星、ARW=選抜のみ)。
   現像は ARW 側で行う運用

## 6. 運用チートシート

### 帰宅後カード運用(Phase 2 — 現在ここまで実装済み)
```bash
# 1. カードを挿す(/Volumes/<CARD> にマウントされる)
# 2. 取り込み→採点→星書き込み→Lightroom監視フォルダへ配送まで一括
bps ingest /Volumes/<CARD>/DCIM/100MSDCF

# 3. Lightroom を開く → スマートコレクション「AIセレクト」に★3以上が並ぶ

# 4. 現像したいカットの RAW だけカードから取り出す
bps export-raw --card /Volumes/<CARD>/DCIM --dest ~/Photos/raw_selects
```
補足コマンド:
```bash
bps status                       # 状態別の枚数・エラー
bps ingest <dir> --no-deliver    # 星は付けるが Lightroom には渡さない(確認用)
bps deliver                      # --no-deliver の後で、あらためて配送する
bps calibrate --sample <過去の試合フォルダ>   # 閾値校正用のシャープネス分布
```

### 現場無線運用(Phase 4 — 未実装)
```
前夜: 自宅で FTP リハーサル(接続確認+連写バースト到達テスト)
現場: ルーター起動 → caffeinate -dimsu bps watch → カメラの Wi-Fi 接続確認 → 撮影に集中
      スマホに 10 分ごとのハートビート通知が届く(止まったら異常)
試合後: bps finalize --all → 帰路/帰宅後に LR を開くと完成済み
```
