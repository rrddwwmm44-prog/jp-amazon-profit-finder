# 日本Amazon 利益商品発見システム

Keepa API や Amazon ページのスクレイピングに依存せず、メーカー変更、廃番、楽天/Yahoo価格変化、セラーCSV差分、Amazon公式APIの観測結果を統合するための基盤です。認証情報なしでも mock パイプラインを完走します。Python 3.11以上（現在の確認環境は3.14.7）を対象にしています。

現在は楽天・Yahoo Provider、利益・ROI・スコア・Confidence、SQLite migration v1、Provider障害分離、Engine Protocol / Registry / Feature Flag、CSV出力を実装済みです。Amazon SP-API、Keepa、価格履歴等は未実装です。

## Windows セットアップ

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
python -m app doctor
python -m app run-all --mode mock
python -m unittest discover -s tests
```

`.env` は秘密情報を含むため、絶対にGitへcommitしないでください。公開用の設定項目は `.env.example` を参照します。

結果は `data/exports/candidates.csv`、履歴は `data/profit_finder.db` に保存されます。CSVは Excel / Google Sheets にそのまま取り込めます。

## コマンド

- `python -m app doctor`: Python、DB、書込権限、各API設定を日本語で診断
- `python -m app run-all --mode mock`: 認証情報不要の全パイプライン
- `python -m app run-all --mode live`: 設定済み Provider を起動。未設定 Provider はエラー記録して継続
- `python -m app run market-price --mode mock`: `market_price` Engineを個別実行
- `python -m app resume`: 前回中断後の安全な再実行（保存は一意キーで冪等）
- `python -m app status`: 最近のジョブ状態

## Live 接続

`.env` に `RAKUTEN_APP_ID`、`RAKUTEN_ACCESS_KEY`、`YAHOO_CLIENT_ID` とカンマ区切りの `MARKET_SEARCH_QUERIES` を設定すると、楽天市場商品検索API（2026-07-01）とYahoo!ショッピング商品検索v3を利用します。秘密情報はコード、DB、CSV、ログへ保存しません。Amazon商品ページの無理なスクレイピング、CAPTCHA回避、アクセス制限回避は設計対象外です。

## 判定原則

JANを第一キー、型番を第二キーとし、商品名類似だけなら PROVISIONAL です。欠損値はゼロと推測せず、`unknown`、`not_observed`、`provider_unavailable`、`not_applicable`、`verified_zero` を区別するデータモデルを用意しています。スコアは0–100に制限し、既定では85点以上を「今日見る商品」とします。
