# kyujo-akijoho

名古屋から車でおおむね1.5時間圏の、硬式野球が使える球場の空き状況をまとめて見るためのチーム内ツールです。

- 閲覧ページ: GitHub Pages（`docs/`）
- 取得: GitHub Actions が毎日 6:07 と 18:07（日本時間）に各予約システムの空き表を読み取り、`docs/data/availability.json` を更新します
- 球場の一覧: `data/fields.csv`（硬式可否・予約システム・電話番号など）

## 構成

| パス | 内容 |
| --- | --- |
| `data/fields.csv` | 球場マスタ。行を足すと閲覧ページの「球場」タブに出る |
| `scraper/config/*.json` | 予約システムごとの画面遷移の手順。画面が変わったらここを直す |
| `scraper/common.py` | 空き表（○×）の読み取り |
| `scraper/browser.py` | 手順どおりにブラウザを動かし、各画面を `debug/` に保存 |
| `scraper/run.py` | 取得の本体。失敗した球場は前回の結果を残す |
| `docs/index.html` | スマホ向けの閲覧ページ |

## 対応状況

| 予約システム | 球場 | 状態 |
| --- | --- | --- |
| あいち共同利用型施設予約システム | 春日井・大府・豊橋・蒲郡 | 手順は推測。初回実行の結果を見て調整 |
| 名古屋市スポレク | 熱田・瑞穂 | 同上 |
| 小牧市・刈谷市・四日市市の各システム | 小牧・刈谷・四日市 | 未対応（設定ファイルのみ） |
| 電話・窓口のみ | 一宮・津島・岡崎・豊田・長良川・岐阜県立高校 | 「球場」タブに連絡先を表示 |

## 手順の調整のしかた

1. Actions タブで「空き状況を取得」を手動実行（`only` に `aichi_kyodo` などを入れると1システムだけ）
2. 実行結果の Artifacts の `debug-pages`、または `debug` ブランチで各画面を見る
3. `pages.txt` と画面のスクリーンショットを見て、どこで止まったか確認
4. `scraper/config/<システム>.json` のボタン名（`text`）を実際の表記に直す

## アクセスのマナー

- 取得は1日2回、操作ごとに2秒待つ
- `robots.txt` で禁止されているサイトは取得しない
- ログインが必要な画面には入らない（公開されている空き状況だけを見る）
- 各システムの利用規約で自動取得が禁止されていたら、そのシステムは `enabled: false` にする

## ローカルで動かす

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m scraper.run --only aichi_kyodo
python -m http.server -d docs 8000   # http://localhost:8000
```
