# Shopee Optimizer チーム品質評価 最終レポート（第2回）

**評価日**: 2026-03-08
**対象PR**: #19〜#22（日本語化・画像検索・バッチ消失対応・Serper移行・セキュリティ修正）
**評価チーム**: 8名（エンジニア5名 + ペルソナ3名）
**前回評価**: PR#17時点 45/100 → 今回 62/100（+17）

---

## 総合スコア: 62/100点

| 評価軸 | 配点 | スコア | 前回 | 変化 | 主な根拠 |
|--------|------|--------|------|------|----------|
| 機能性 | 25 | 18 | 17 | +1 | batch消失対応・Serper移行は良好。video_record上書きバグ→修正済み |
| UX/使いやすさ | 20 | 12 | 12 | ±0 | ステップ進捗・再生成ステータス良好。alert多用・再開ボタン非表示→修正済み |
| セキュリティ | 20 | 12 | 4 | +8 | ASIN検証・SSRF対策追加。認証未実装はHIGH(将来対応) |
| 保守性 | 20 | 11 | 7 | +4 | restart-from-step削除で3重→2重に。700行重複は残存 |
| パフォーマンス | 15 | 9 | 5 | +4 | SSE設計は良好。innerHTML毎秒再生成が課題 |

---

## 本セッションで修正済みの問題

### P0: バグ修正（5件）

| # | 問題 | 修正内容 |
|---|------|---------|
| 1 | `version`変数未定義（Step5スキップパス） | `version = video_path.stem` を process_stream + resume_batch に追加 |
| 2 | `video_record`がif/else外で`None`に上書き | `video_record = None`を`else`ブロック内に移動 |
| 3 | `searchShownAsins`が次バッチでリセットされない | `runBatch()`冒頭に`searchShownAsins.clear()`追加 |
| 4 | `batchResumeBar`挿入先IDが存在しない | `progressPanel`→`progressSec`+`appendChild`に修正 |
| 5 | ボタンテキスト不一致 | 「一括実行」に統一 |

### CRITICAL: セキュリティ修正（3件）

| # | 問題 | 修正内容 |
|---|------|---------|
| 1 | `/regenerate-video`と`/add-images`でASIN未検証（パストラバーサル） | `validate_asin()`共通関数追加 |
| 2 | `/add-images`のimage_urlsにSSRF対策なし | `_is_safe_url()`で内部ネットワーク・メタデータサーバーをブロック |
| 3 | ファイルシステム復元時に非画像ファイル混入 | `_IMAGE_EXTS`フィルタ追加 |

---

## 未対応の指摘事項

### HIGH（次スプリント推奨）

| # | 問題 | 報告元 |
|---|------|--------|
| 1 | 全エンドポイントに認証なし | security-auditor |
| 2 | APIキーをリクエストボディから受け取れる | security-auditor |
| 3 | EFFECT_LABELS 3箇所重複定義 | senior-frontend |
| 4 | regenStatus timerのDOMライフサイクル問題 | senior-frontend |
| 5 | /drive-video IDORリスク | security-auditor |

### MEDIUM（計画対応）

| # | 問題 | 報告元 |
|---|------|--------|
| 1 | CSP/HSTSヘッダー未設定 | security-auditor |
| 2 | process_stream/resume_batch 700行重複 | backend, devops |
| 3 | BATCH_STOREインメモリ（スケールダウンで消失） | devops |
| 4 | /finalizeにbatch消失時復元パスなし | qa-engineer |
| 5 | SERPER_API_KEYをSecret Manager移行 | devops |
| 6 | alert()をトースト通知に置換 | senior-frontend |
| 7 | アクセシビリティ: label/ARIA/コントラスト | senior-frontend |
| 8 | batch消失時のexisting_videosにvideo_url/effect欠如 | senior-frontend |

### LOW / 長期

- app.py HTMLインライン→テンプレート分離
- drive_video_proxyのStreaming化
- ffmpeg戻り値チェック
- GCSバッチ状態永続化
- DuckDuckGo ImportError個別ハンドリング

---

## ペルソナ評価

| ペルソナ | 主な指摘 |
|---------|---------|
| 初心者 | 専門用語が多い。URLの貼り方がわからない。「タブ追加」の意味不明 |
| パワーユーザー | キーボードショートカットなし。CSVエクスポートなし |
| エンタープライズ | 認証なし・監査ログなし。社内審査を通過不可 |

---

## デプロイ判定

- [x] P0バグ5件 → 修正済み
- [x] CRITICALセキュリティ3件 → 修正済み
- [ ] HIGH認証問題 → Cloud Run IAM設定で外部アクセス制限済みなら許容

**→ デプロイ可能。** PR作成・マージ・デプロイに進める。
