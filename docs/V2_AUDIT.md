# 日本Amazon物販OS v2 現行監査

監査日: 2026-08-19

## 基準動作

- Python 3.14.7 / SQLite 3.50.4
- `doctor`: 基盤正常。楽天・Yahoo設定済み、検索語・Amazon・Google Sheets未設定
- `unittest`: 監査開始時8件成功
- mock: 候補3件、85点以上1件、ジョブ完了

## 正常動作する機能

- 標準ライブラリのみのCLI、dotenv読込、SQLite作成
- 楽天市場商品検索API / Yahoo商品検索v3のレスポンス変換
- HTTP timeout、429/5xx retry、backoff、最小呼出間隔
- JAN検証、JAN/型番/商品名の簡易照合
- 利益・利益率・ROI、単一Score、Confidence、VERIFIED/PROVISIONAL
- 候補・ジョブ・エラーのSQLite保存、CSV出力
- mock統合パイプライン

## 不完全または未実装

- `resume` はmock再実行でありcheckpoint再開ではない
- `snapshots` テーブルは未使用。価格履歴・7/30日統計なし
- 楽天JANは説明文の13桁抽出のみ。送料額も未取得
- 複数Providerの商品統合、Opportunityイベント統合なし
- Amazon SP-API、Keepa、Seller CSV、仮想仕入れ未実装
- Opportunity/Urgency、Expected Profit、推奨数量なし
- Feature Flag、Engine単独CLI、job/engine/provider詳細状態、二重起動防止なし
- Google Sheets、販売管理、Windowsスケジュール未実装

## 技術的負債

- `runner.py` がProvider選択、評価、保存、CSVを一括担当
- `ProductSignal.evidence` に非構造化情報が集中
- DB migrationが`CREATE TABLE IF NOT EXISTS`だけでschema versionなし
- 外部キー、検索用index、engine列、provider run列がない
- `UNIQUE(job_id, identity_key)` のため同一ジョブ内の複数店舗観測を失う可能性
- `manufacturer` に店舗名を格納しており意味が混在
- 欠損状態Enumは定義のみで各フィールドに適用されていない
- APIレスポンスpagination、cache、cursor永続化なし
- HTTPレート制御がプロセス内・Client単位のみ
- 設定閾値の一部が実際の候補フィルタに使われていない

## 再利用するコード

- Provider ABCと楽天/Yahoo変換ロジック
- `JsonHttpClient` の基本retry/backoff
- JAN正規化、利益計算、既存スコアの互換層
- SQLite接続のWindows向けclose処理
- CLIの既存コマンドとmock fixture
- 現行8テストをv1互換回帰テストとして維持

## v2構造方針

全面移行はせず、`app/core` と `app/engines` を追加し、既存moduleをadapterとして残す。

1. Engine protocol / registry / feature flags
2. version付きadditive DB migrations
3. observationとopportunityを分離
4. market price engineを最初のv2 Engineへ移行
5. Keepa budget、seller decline、virtual purchaseを独立Engineとして追加
6. 既存`run-all`はregistryを呼ぶ互換コマンドへ変更

## DB migration方針

- `schema_migrations(version, applied_at)` を追加
- 既存4テーブルを削除・renameしない
- v2 tableはadditive migrationで追加
- 最初の候補: engine_runs, provider_runs, products, product_identifiers,
  market_observations, opportunities, opportunity_signals, checkpoints,
  keepa_usage, keepa_cache, virtual_purchases, virtual_purchase_reviews
- migrationごとに空DBと既存v1 DBの両方をテスト

## CLI移行方針

- 既存: `doctor`, `run-all`, `resume`, `status` を維持
- 追加: `python -m app run <engine> --mode mock|live`
- 追加: `engines`, `migrate-status`, `keepa-budget`
- `run-all` は有効なEngineだけを順に起動

## Provider追加方針

### Keepa

- Keepa clientとBudget Managerを分離
- API応答とtoken消費をSQLite cache/usageへ保存
- 優先度S/A/B/Cで残量低下時にadmission control
- 全件照会は禁止し、Python一次選別後だけ呼び出す

### Amazon SP-API

- 読取系から開始し、認証・rate limit・endpoint versionを隔離
- Catalog / Pricing / Listings / Ordersを別capabilityとして実装
- 書込操作は初期Phaseで無効、明示Feature Flag必須
- 取得不能値はprovider_unavailable等で保持し推測しない

## テスト方針

- v1の既存テストを全維持
- Engine contract、Feature Flag、migration、checkpointをunit test
- Providerはfixture responseのみで決定的にtest
- 1 Provider障害時の継続、再開、二重起動をintegration test
- Live testは通常suiteから分離し、秘密情報なしではskip
