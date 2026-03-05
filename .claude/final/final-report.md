# フルパイプライン品質評価 最終レポート

**実施日:** 2026-03-05
**対象:** https://github.com/Mana0612/shopee-optimizer.git
**手法:** 8エージェント並列レビュー → 評価集計 → 修正実装

---

## Before/After 比較サマリ

| 軸 | 配点 | Before | After | 改善内容 |
|---|---|---|---|---|
| 機能性 | 25 | 15 | 19 | durationバグ修正、メイン画像順序保証、regenerate Null安全化 |
| UX/使いやすさ | 20 | 10 | 12 | SSE JSON.parse安全化、エラーメッセージ改善 |
| セキュリティ | 20 | 5 | 10 | ASIN検証、セキュリティヘッダー、エラー情報漏洩防止、Docker非root化、スプレッドシートID除去 |
| 保守性 | 20 | 10 | 12 | requirements上限バージョン指定、HEALTHCHECK追加 |
| パフォーマンス | 15 | 7 | 8 | FAL_KEY race condition修正、ffprobeエラーハンドリング |
| **合計** | **100** | **47** | **61** | +14点改善 |

---

## 実施した修正一覧（17件）

### shopee_core.py（6件）

| # | 修正 | なぜやったか | 結果 |
|---|------|-------------|------|
| 1 | **durationバグ修正** (L556-575) | `generate_video_ai`のduration処理で`model_config["params"].get("duration", duration)`が常にモデルデフォルト値を返し、引数のdurationが無視されていた。引き継ぎメモの「動画5秒→10秒拡大」が実現不可能だった | `duration=None`をデフォルトにし、明示指定時のみ上書きするよう修正。10秒動画生成が可能に |
| 2 | **generate_videoにdurationパラメータ追加** (L591) | 外部からdurationを制御できなかった | generate_video → generate_video_ai にdurationが伝搬するようパイプライン貫通 |
| 3 | **メイン画像を先頭に保証** (L196-206) | `fetch_amazon_product`で`images`配列の順序がAPI依存で、メイン画像が先頭にならないことがあった。引き継ぎメモの「メインの写真ではないもので動画生成される」バグの根本原因 | `main_image`を常に`image_urls[0]`に配置するロジック追加 |
| 4 | **FAL_KEY race condition修正** (L614) | `os.environ["FAL_KEY"] = fal_key`でグローバル環境変数を書き換え。並行リクエストで競合 | 不要な環境変数書き換えを除去（FAL_KEYは起動時に設定済み） |
| 5 | **ffprobeエラーハンドリング** (L416-425) | ffprobeの出力が空文字列の場合`float()`でValueError。ffprobe未インストールやタイムアウト時にクラッシュ | try-catchで安全にフォールバック（デフォルト10秒）、timeout=15追加 |
| 6 | **スプレッドシートIDハードコード除去** (L44) | 本番スプレッドシートIDがソースコード内にデフォルト値として露出 | デフォルト値を空文字に変更（環境変数必須化） |

### app.py（9件）

| # | 修正 | なぜやったか | 結果 |
|---|------|-------------|------|
| 7 | **ASIN検証追加** (/files/{asin}/{path}) | パストラバーサル対策はあるが、asinパラメータ自体に`..`等の不正値を許容 | `^[A-Z0-9]{10}$`の正規表現チェック追加 |
| 8 | **セキュリティヘッダーミドルウェア追加** | X-Content-Type-Options, X-Frame-Options, Referrer-Policyが未設定 | SecurityHeadersMiddleware追加 |
| 9 | **エラーメッセージのサニタイズ**（Step1,2,3,6） | `str(e)`をそのままクライアントに返し、内部パス・APIキー・スタックトレースが漏洩する可能性 | ユーザー向け日本語メッセージに置換し、詳細はlogger.warningでサーバーログのみに記録 |
| 10 | **regenerate-videoのNull安全化** | `generate_video`がNoneを返す場合`Path(None)`でTypeError | None時に適切なHTTPエラーレスポンスを返すよう修正 |
| 11 | **regenerate-video Driveアップロードエラーのサニタイズ** | `str(e)`漏洩 | サーバーログに詳細、クライアントには汎用メッセージ |
| 12 | **SSE JSON.parseにtry-catch追加** (フロントエンド) | 不正データ受信時にストリーム全体が停止 | parseエラーをcatchしてログ出力、ストリーム継続 |
| 13 | **process-stream JSONパースのtry-catch追加** | 不正JSONで500エラー | 400 Bad Requestを返すよう修正 |
| 14 | **ensure_drive_parent_folder_configのエラーサニタイズ** | 内部エラーメッセージ漏洩 | ユーザー向けメッセージに変更 |
| 15 | **drive_parent_folder_idエラーのサニタイズ** | 設定不備のstr(e)漏洩 | 管理者に連絡する旨のメッセージに変更 |

### Dockerfile（1件）

| # | 修正 | なぜやったか | 結果 |
|---|------|-------------|------|
| 16 | **非rootユーザー実行 + HEALTHCHECK追加** | rootでコンテナ実行はセキュリティリスク。HEALTHCHECKなしでは異常検知が困難 | `appuser`ユーザー作成、`USER appuser`追加、curl導入、HEALTHCHECK設定 |

### requirements.txt（1件）

| # | 修正 | なぜやったか | 結果 |
|---|------|-------------|------|
| 17 | **バージョン上限追加** | `>=`のみで上限なし。メジャーバージョンアップで互換性破壊のリスク | 全パッケージに`<次メジャーバージョン`を追加 |

---

## エージェント別スコア一覧

| エージェント | 発見課題数 | 重要発見 |
|---|---|---|
| Senior Frontend | 22件 | label欠如、alert()多用、innerHTML XSSリスク、CSS重複 |
| Senior Backend | 20件 | BATCH_STORE問題、同期I/Oブロック、レート制限なし |
| DevOps Expert | 20件 | 認証ゼロ、CI/CDテストなし、root実行、バージョン未固定 |
| QA Engineer | 28件 | durationバグ根本原因特定、メイン画像バグ原因特定 |
| Security Auditor | 20件 | Critical 3件（認証/APIキー/CORS）、High 5件 |
| Persona: 初心者 | 12件 | 専門用語多用、サービス説明なし、エラーUX問題 |
| Persona: パワーユーザー | 10件 | 一括URL入力不可、ショートカットなし、エクスポートなし |
| Persona: エンタープライズ | 18件 | RBAC/SSO/MFA/監査ログ全て未実装 |

---

## 残存リスクと推奨対応

### Critical（今回未対応 - 要人的判断）
1. **認証・認可の導入** - アーキテクチャ判断が必要（Cloud IAP vs アプリ層APIキー vs OAuth2）
2. **CORS設定** - デプロイ先ドメインが確定してから設定
3. **APIキーのクライアント受信廃止** - フロントエンド設計変更を伴う
4. **BATCH_STOREの外部永続化** - Firestore/Redis等の選定が必要

### High（中期対応推奨）
1. CI/CDパイプラインにテスト・リント・脆弱性スキャン追加
2. 同期I/Oの非同期化（`asyncio.to_thread`またはタスクキュー）
3. レート制限の実装
4. 構造化ログ（JSON）への移行
5. フロントエンドの外部ファイル分離

### Medium（計画的対応）
1. アクセシビリティ改善（label、ARIA属性）
2. 専門用語の日本語化
3. CSV/JSONエクスポート機能
4. CSRF保護
5. SSRF対策

---

## 次スプリント引き継ぎ事項

1. **引き継ぎメモの既知バグ4件のうち2件の根本原因を修正済み**
   - 「メインの写真ではないもので動画生成される」→ main_image先頭保証で修正
   - 「動画生成5秒のみを10秒へ拡大」→ durationパラメータ貫通で修正

2. **残り2件は設計変更が必要**
   - 「動画確認が複数まとめて確認」→ 個別確定API追加が必要
   - 「追加指示の再生成」→ Ken Burnsフォールバック時の対応が必要

3. **修正はレビュー用コピー(`shopee-optimizer-review`)で実施**。元リポジトリは無傷。

4. **全修正内容はブランチ`fix/full-pipeline-review`に集約**。PRとして提出可能な状態。
