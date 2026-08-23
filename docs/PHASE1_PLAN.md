# Phase 1 開発計画

## P1-0 安全基盤

- Provider例外隔離（最初の修正として実施）
- schema migration versioning
- engine/provider run記録
- Engine protocol、registry、Feature Flag
- 個別Engine CLIとv1互換`run-all`
- checkpointとjob lockの最小実装

完了条件: 既存テスト成功、1 Provider障害でも他Provider完走、既存DBを保持。

## P1-1 Market Price Engine

- 楽天/Yahoo観測値を店舗単位で永続化
- JAN優先の商品master統合
- 7/30日平均・中央値、最安値、急落率
- 複数店舗同時値下げイベント
- 軽量一次候補フィルタ

完了条件: 日を跨ぐfixtureから統計と急落eventを再現可能。

## P1-2 Keepa Budget Manager

- token snapshot / usage ledger / cache
- engine別、時間別、日別消費
- 推定他システム消費
- S/A/B/C admission control
- 必要tokens/minと推奨枠の計算

完了条件: Keepaを呼ばずにbudget判断をunit test可能。

## P1-3 Amazon Arbitrage Engine

- Keepa候補query contract
- Amazon本体値下げ、在庫復活、価格周期event
- Opportunity/Urgency分離
- Keepa cache再利用

完了条件: Keepa fixtureで候補生成、重複API呼出なし。

## P1-4 Seller Decline Engine

- 7/30/90日seller observation
- 減少率・加速度・価格・rank維持を評価
- Seller Decline Score 0-100
- 需要減退と供給不足のconfidence分離

完了条件: 20→18→13→6等のfixtureを正しく高評価。

## P1-5 Opportunity統合・仮想仕入れ

- ASIN/JAN単位のOpportunity集約
- signal重複排除
- Expected Profit、資金拘束、初期推奨数量
- 仮想仕入れと7/14/30/60日評価
- 戦略別集計の基礎

完了条件: 複数Engine signalが1 Opportunityになり、追跡評価可能。

## P1-6 運用安定化

- engine別Windows実行
- retention/aggregation
- 障害注入・再開・二重起動test
- 運用READMEとdoctor拡張

完了条件: Codexなしの日次運転手順が成立。
