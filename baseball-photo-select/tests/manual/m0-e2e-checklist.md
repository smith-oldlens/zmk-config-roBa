# M0: E2E 成立検証+実機タグ確認 チェックリスト

目的(docs/03 M0): パイプラインを書く前に、(1)「exiftool で星を書いた JPEG を
Lightroom が自動取り込みで星付き表示する」E2E 経路と、(2) α7C II の AF タグの実形式を確認する。

このマイルストーンは**人間の作業が主**。下の各項目に結果(テキスト/スクリーンショット)を記入する。
自動化できる部分は `scripts/m0_write_test_stars.py` と `scripts/m0_dump_af_tags.py` が肩代わりする。

前提: PC に **exiftool** をインストール(Windows は exiftool.org の実行ファイルを
`exiftool.exe` にリネームして PATH に置く)。`--generate` を使うなら `pip install pillow`。

---

## 手順 1〜3: exiftool の星 → Lightroom 自動取り込み → スマートコレクション

### 1. テスト JPEG 3 枚に星 0 / 3 / 5 を書く
```
cd baseball-photo-select/scripts
python m0_write_test_stars.py --generate --out-dir ./m0_test
```
→ `m0_test/` に星 0/3/5 の 3 枚が生成され、読み戻し検証まで自動で行われる。
（手持ちの JPEG を使う場合: `python m0_write_test_stars.py a.jpg b.jpg c.jpg`。順に 0/3/5 が付く）

- [ ] 3 枚とも `-> OK` で書き込み・読み戻しできた
  - 結果メモ: __________________________________________

### 2. Lightroom Classic を docs/04 の手順で設定
- [ ] 自動読み込み(監視フォルダ = `deliver/`、移動先 = LR 取り込みフォルダ)を設定した
- [ ] カラーラベルセットを英語名(Red/Yellow/Green/Blue/**Purple**)にした
- [ ] スマートコレクション「AIセレクト」(評価 ≥ ★3 かつ 取り込み日 = 今日)を作成した
- [ ] スマートコレクション「決定的瞬間」(評価 ≥ ★5)を作成した

### 3. 監視フォルダに 3 枚を置いて確認
- [ ] 監視フォルダに手順 1 の 3 枚を置いた
- [ ] スマートコレクション「AIセレクト」に **★3 と ★5 の 2 枚だけ**が現れた
      (★0 は現れない)
- [ ] スマートコレクション「決定的瞬間」に **★5 の 1 枚だけ**が現れた
  - 結果メモ / スクショ: __________________________________________

> ここまで成立すれば「メタデータ → Lightroom」の主経路が確定し、M1 以降の実装がこの経路に
> 乗せられることが保証される(パイプラインは同じ `XMP-xmp:Rating` を書くだけ)。

---

## 手順 4: α7C II の AF タグ実形式を確認

AF-C で人物を撮った実 JPEG を用意する。**AF エリアモードを変えて数枚ずつ**撮ると確実:
ワイド / トラッキング / スポット(中央以外)。

```
cd baseball-photo-select/scripts
python m0_dump_af_tags.py path/to/DSC0001.JPG path/to/DSC0002.JPG ...
```
→ 各ファイルの全 Sony タグ JSON が `docs/af-tag-samples/<name>.json` に保存され、
AF-point 系タグ(FocusLocation ほか)の実名・値が一覧表示される。
中央 AF の「データ無し」フォールバック値は `center_suspect` として警告表示される。

- [ ] `docs/af-tag-samples/` に実機 JSON を保存した(最低 3 枚、AF モード違い)
- [ ] 存在した AF-point タグと形式を確認した
  - 検出タグ名: __________________________________________
  - 値の例(W H X Y): __________________________________________
  - `center_suspect` の出方(中央 AF で中心座標が入るか): __________________
- [ ] `config.example.yaml` の `af.tag_names` を実測の優先順で更新した
- [ ] 結果を `docs/OPEN_QUESTIONS.md` の該当項目に記入した

---

## 手順 5: 過去写真の棚卸し(Phase 3 教師データの成立条件)

Phase 3(決定的瞬間の学習)は「不採用カットも含めて全ショットが残っている過去試合」を
負例として使う。削除済みだと学習が成立しない(spec 02 10.1)。

- [ ] 全ショットが残っている過去試合の数を数えた: **______ 試合**(概算総枚数 ______ 枚)
- [ ] うち自分が採用(★3 以上 or フラグ)した枚数の概算: ______ 枚
- [ ] 結果を `docs/OPEN_QUESTIONS.md` に記入した
  - 正例が 500 枚未満なら、Phase 3 はポーズ/ボール特徴主体に切替(spec 02 10.1 の警告条件)

---

## M0 完了判定
- [ ] 手順 1〜3 が成立(Lightroom に星付きで自動取り込みされた)
- [ ] 手順 4 の AF タグが確定し config / OPEN_QUESTIONS に反映
- [ ] 手順 5 の棚卸し結果が OPEN_QUESTIONS に記入

3 つすべてにチェックが付いたら M1(プロジェクト骨格+ingest+DB)へ進む。
