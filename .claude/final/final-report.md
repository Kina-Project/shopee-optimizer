# Shopee Optimizer - フルパイプライン品質評価 最終レポート

**評価日:** 2026-03-08
**対象PR:** #17 (feat/per-step-spreadsheet-write)
**評価チーム:** 8名（エンジニア5名 + ペルソナ3名）

---

## 総合スコア: 45/100点 (閾値80点未達)

| 評価軸 | 配点 | スコア | 主な根拠 |
|--------|------|--------|----------|
| 機能性 | 25 | 17 | 7ステップ処理は動作するが、resume時のAPIキー消失バグ、fal.aiタイムアウト未設定 |
| UX/使いやすさ | 20 | 12 | 進捗表示は良好だが、専門用語多い、再開ボタン表示バグ、アクセシビリティ不足 |
| セキュリティ | 20 | 4 | 認証ゼロ、OAuthシークレット平文、SSRF、XSS（playVideo） |
| 保守性 | 20 | 7 | 3関数1120行コピペ、テストゼロ、ファイル3600行超 |
| パフォーマンス | 15 | 5 | ブロッキングI/O、BATCH_STOREインメモリ、Cloud Runタイムアウト未設定 |

---

## チーム別スコアサマリー

| チーム | スコア | 主要指摘 |
|--------|--------|----------|
| security-auditor | 6/20 | OAuthシークレット平文、認証欠如、SSRF |
| senior-backend | 36/60 | BATCH_STOREインメモリ、3関数コピペ、ブロッキングI/O |
| senior-frontend | 53/100 | progressPanelバグ、playVideo XSS、アクセシビリティ |
| devops-expert | 38/100 | 認証なし公開、テストゲート皆無、タイムアウト未設定 |
| qa-engineer | 36/100 | テストゼロ、fal.aiタイムアウトなし、APIキー引き継ぎ不能 |
| persona-beginner | 59/140 | 専門用語、エラーメッセージに対策なし |
| persona-poweruser | 68/100 | バッチ上限10件、キーボードショートカットなし |
| persona-enterprise | 20/100 | 認証なし、監査ログなし、マルチテナント非対応 |

---

## CRITICAL指摘一覧（必ず修正）

| # | 指摘 | 報告元 | PR#17関連 |
|---|------|--------|-----------|
| C-1 | OAuthシークレット平文コミット（client_secret_*.json） | security | いいえ（既存） |
| C-2 | 全エンドポイント認証なし | security, devops, enterprise | いいえ（既存） |
| C-3 | BATCH_STOREインメモリ（マルチインスタンスで消失） | backend, qa | いいえ（既存） |
| C-4 | テストカバレッジゼロ | qa | いいえ（既存） |
| C-5 | fal_client.subscribeにタイムアウトなし（無限ハング） | qa | いいえ（既存） |

## HIGH指摘一覧（可能な限り修正）

| # | 指摘 | 報告元 | PR#17関連 |
|---|------|--------|-----------|
| H-1 | playVideo() XSSリスク（innerHTML直接展開） | frontend | **はい（新規）** |
| H-2 | バッチ再開ボタン表示バグ（progressPanel→productPanel） | frontend | いいえ（既存） |
| H-3 | SSRFリスク（/add-imagesのURL未検証） | security | いいえ（既存） |
| H-4 | resume_batch APIキー引き継ぎ不能 | qa | いいえ（既存） |
| H-5 | process_stream/resume_batch/restart 3重コピペ1120行 | backend | いいえ（既存） |
| H-6 | CI/CDにテストゲート皆無（push-to-prod状態） | devops | いいえ（既存） |
| H-7 | Cloud Runタイムアウト未設定（動画生成が300秒超過） | devops | いいえ（既存） |

---

## PR#17 マージ判定

### 判定: 条件付きマージ可

PR#17自体の変更（スプレッドシート中間保存、動画プレビュー、再生成UI）は機能的に正しく、
既存の問題を悪化させるものではない。

**マージ前に必須修正（PR#17で導入された問題）:**

1. **H-1: playVideo() XSSリスク** — innerHTMLを使わずDOM APIで動画要素を生成する

**マージ後に対応すべき既存課題（優先度順）:**

1. C-1: client_secret_*.jsonを.gitignoreに追加 + git履歴からパージ + シークレットローテーション
2. H-2: progressPanel → productPanel のID修正
3. C-5: fal_client.subscribeにタイムアウト追加
4. H-4: resume_batch APIキー引き継ぎ
5. C-2: Cloud IAP or API Gateway認証の導入
6. H-7: Cloud Runタイムアウト/メモリ/CPU設定の明示

---

## 改善ロードマップ

### Phase 1: 即時対応（今回PR内）
- [ ] playVideo() XSS修正

### Phase 2: 短期（1-2週間）
- [ ] client_secret.jsonの除去 + .gitignore
- [ ] progressPanel IDバグ修正
- [ ] fal_client タイムアウト追加
- [ ] resume_batch APIキー引き継ぎ修正

### Phase 3: 中期（2-4週間）
- [ ] Cloud IAP認証導入
- [ ] Cloud Run設定最適化（タイムアウト/メモリ/CPU）
- [ ] CI/CDにテストゲート追加
- [ ] 3関数コピペのリファクタリング

### Phase 4: 長期（1-2ヶ月）
- [ ] BATCH_STOREのFirestore移行
- [ ] テスト基盤構築（80%カバレッジ目標）
- [ ] 監査ログ実装
- [ ] SSRF対策（URL allowlist）

---

## プロセス改善: consistency-checker エージェントの導入

### 問題: 今回の評価サイクルで修正回数が多かった

今回のPR #17では、マージ後に以下の追加修正が必要になった：

| # | 修正内容 | 原因 |
|---|---------|------|
| 1 | playVideo() XSS修正 | チームレビューで検出 → 修正 |
| 2 | Drive動画プレビュー不可 | DriveのURL形式が再生に不向き → プロキシ配信に変更 |
| 3 | 履歴ページでローカル動画がない | Cloud Runエフェメラル前提の考慮漏れ |

これらの修正は**1箇所を直すたびに、同じパターンの別箇所で同じ問題が残る**という連鎖を生みやすい。
例えば `drive_file_url` の表示方法を履歴ページで修正しても、レビュー画面やバッチ結果画面では未修正のまま、という状態。

### 対策: consistency-checker エージェントを新設

**役割:** 1つの修正パターンが入ったとき、同じパターンを全コードベースでGrep検索し、修正漏れを洗い出す。

**期待効果:**
- 修正→テスト→別箇所発見→再修正 のループが減る
- 3関数コピペ（process_stream / resume_batch / restart_from_step）間の不整合を早期検出
- 1回のレビューサイクルで関連箇所を一括修正できる

**具体例（今回のケースに当てはめると）:**
- Drive動画URLをプロキシに変換する修正を入れた時点で
- consistency-checkerが `drive_file_url` の全使用箇所を検索
- 履歴ページ・レビュー画面・バッチ結果・動画生成ログなど全箇所をリスト化
- 同じ修正が必要な箇所をMUST_FIXとして報告
- → 1回の修正サイクルで全箇所対応完了

**設置場所:** `~/.claude/agents/consistency-checker.md`
**起動タイミング:** `/team` 実行時に他の8エージェントと並列起動（計9エージェント）
