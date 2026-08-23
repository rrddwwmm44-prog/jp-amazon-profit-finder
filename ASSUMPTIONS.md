# 前提・判断記録

- `profit_finder_starter.zip` は添付されていなかったため、新規プロジェクトとして構築する。
- Python 3.11+ と標準ライブラリだけで mock モードを完走可能にする。
- Live API は認証情報がない状態でも停止せず、provider_unavailable として記録する。
- Amazon は公式 API のみを接続対象とし、商品ページのスクレイピングは行わない。
- 外部 API の具体的な契約・最新 endpoint は利用者の認証情報と契約範囲に依存するため、Provider 境界まで実装する。
- Google Sheets 未設定時は同じ列構造の CSV を `data/exports` に出力する。
- 金額は円、率は小数（0.30 = 30%）で内部保持する。
