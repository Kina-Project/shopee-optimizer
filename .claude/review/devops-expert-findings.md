# DevOps Expert レビュー結果

**レビュー日**: 2026-03-05
**レビュアー**: シニアDevOpsエンジニア（Kubernetes/Docker/CI/CD/GCP/SRE専門）
**対象**: Shopee商品ページ最適化ツール（Cloud Run デプロイ構成）

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | APIエンドポイントに認証・認可機構が一切ない | セキュリティ | 10 | 10 | 100 |
| 2 | APIキー（Rainforest, OpenAI）がクライアントからPOSTリクエストで送信される設計 | セキュリティ | 9 | 9 | 81 |
| 3 | CI/CDパイプラインにテスト・リント・脆弱性スキャンのステップがない | CI/CD安全性 | 9 | 8 | 72 |
| 4 | Dockerコンテナがroot権限で実行されている（USERディレクティブなし） | セキュリティ | 8 | 8 | 64 |
| 5 | requirements.txtでバージョン上限が未指定（>=のみ）。再現不可能なビルド | 信頼性 | 8 | 8 | 64 |
| 6 | Cloud Runデプロイに`--quiet`フラグ使用。エラー時の確認プロンプト抑制でサイレント障害の危険 | CI/CD安全性 | 7 | 8 | 56 |
| 7 | ロールバック手順・戦略が未定義。deploy.ymlにロールバックジョブなし | 運用 | 8 | 7 | 56 |
| 8 | ヘルスチェック（`/health`）が依存サービスの状態を確認していない（shallow check） | 監視 | 7 | 7 | 49 |
| 9 | BATCH_STOREがインメモリ辞書。Cloud Runインスタンス間で状態共有不可。スケールアウト不可 | アーキテクチャ | 8 | 6 | 48 |
| 10 | レート制限・同時実行制御がなく、外部API（Rainforest, FAL, OpenAI）のクォータ超過リスク | 信頼性 | 7 | 7 | 49 |
| 11 | CORS設定なし。フロントエンドがインラインHTMLのため現時点では問題ないが、分離時に障害 | セキュリティ | 5 | 5 | 25 |
| 12 | Graceful shutdown処理が未実装。SIGTERMハンドリングなし。長時間動画生成中の中断でデータ不整合 | 信頼性 | 7 | 7 | 49 |
| 13 | ログがstructured logging（JSON形式）でない。Cloud Loggingとの統合が不十分 | 監視 | 6 | 6 | 36 |
| 14 | Dockerイメージがマルチステージビルドでない。ffmpegなどのビルド依存とランタイムが分離されていない | 最適化 | 5 | 5 | 25 |
| 15 | デプロイが`--source .`（Cloud Build経由）のみ。タグ付きイメージの管理・追跡ができない | 運用 | 6 | 6 | 36 |
| 16 | `os.environ["FAL_KEY"] = fal_key`（shopee_core.py L614）でグローバルに環境変数を書き換え。並行リクエストで競合状態（race condition） | バグ | 8 | 6 | 48 |
| 17 | `/files/{asin}/{path:path}`でパストラバーサル対策はあるが、`asin`パラメータの入力検証が不十分 | セキュリティ | 6 | 6 | 36 |
| 18 | SpreadsheetのIDがソースコードにハードコード（shopee_core.py L45） | セキュリティ | 5 | 5 | 25 |
| 19 | Cloud Runのリソース制限（memory, cpu, timeout, concurrency）がデプロイコマンドで未指定 | コスト/信頼性 | 6 | 6 | 36 |
| 20 | GCPサービスアカウントキーファイルのパスがデフォルトでホームディレクトリ配下にフォールバック | セキュリティ | 5 | 4 | 20 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 保守性（20点満点） | 10 | Dockerfileはシンプルで理解しやすい。環境変数による設定切替は適切。しかしapp.pyが2100行超の単一ファイルでフロントHTML/CSS/JSが埋め込まれており、保守が困難。CI/CDパイプラインが最小限で、テスト・品質ゲートがないため変更時の安全性が低い。 |
| パフォーマンス（15点満点） | 8 | python:3.12-slimベースイメージの選択は妥当。pip --no-cache-dir使用。apt-get clean相当処理あり。しかしマルチステージビルド未採用でイメージサイズに改善余地あり。Cloud Runのconcurrency/memory設定が未指定で、動画生成のような重い処理でのリソース競合リスクがある。 |
| セキュリティ（15点満点） | 4 | 認証機構が皆無。APIキーがクライアントから直接渡される設計。rootでコンテナ実行。依存パッケージのバージョンピン留めなし。脆弱性スキャンなし。パストラバーサル対策はあるが不十分。 |
| 可用性（15点満点） | 7 | Cloud Runのマネージドプラットフォームによるゼロダウンタイムデプロイの恩恵はあるが、明示的な設定がない。ヘルスチェックはshallow。graceful shutdownなし。インメモリ状態によるスケールアウト制約。 |
| 運用性（15点満点） | 6 | .gcloudignoreは適切に設定。WIF認証採用は良い。しかしロールバック手順なし、structured loggingなし、アラート設定なし、リソース制限未設定。 |
| コスト効率（10点満点） | 5 | Cloud Runの従量課金モデルは適切だが、リソース制限未設定で予期しないコスト増のリスク。動画生成タスクの長時間実行がCloud Runのタイムアウト上限に抵触する可能性。外部API呼び出しにレート制限がなく、クォータ超過による課金リスク。 |
| CI/CD成熟度（10点満点） | 3 | mainブランチへのpushで自動デプロイされるが、テスト・リント・ビルド検証・承認フローが一切ない。事実上「push-to-prod」状態。ステージング環境なし。 |
| **合計（100点満点）** | **43** | |

---

## デプロイリスク評価

### ゼロダウンタイムデプロイ

**判定: 条件付き可能（リスクあり）**

Cloud Run自体はトラフィック分割によるゼロダウンタイムデプロイをサポートするが、現在の構成には以下のリスクがある:

- `gcloud run deploy --source .`はCloud Buildでビルド後に新リビジョンへ100%トラフィックを即時切替する。`--no-traffic`や`--tag`による段階的ロールアウトが設定されていない
- BATCH_STOREがインメモリのため、新リビジョンへの切替時に進行中のバッチ処理状態が失われる
- graceful shutdown未実装のため、旧インスタンス終了時に長時間実行中の動画生成リクエストが中断される可能性がある
- Cloud Runのデフォルトタイムアウト（300秒）で動画生成処理（推定165秒/商品 x 最大10商品）が完了しない可能性

### ロールバック手順

**判定: 未整備**

- deploy.ymlにロールバックジョブが定義されていない
- コンテナイメージにタグが付与されていないため、特定バージョンへの復元が困難
- `gcloud run services update-traffic`による手動ロールバックは可能だが、手順書なし
- 推奨: `gcloud run deploy`に`--tag`を付与し、`gcloud run services update-traffic --to-revisions=REVISION=100`でロールバック可能にする

### 障害シナリオ

| シナリオ | 現在の対応 | リスクレベル |
|---------|-----------|------------|
| デプロイ失敗 | Cloud Buildエラーで停止、既存リビジョンは維持 | 低 |
| ランタイムエラー（新リビジョン） | 自動ロールバックなし。手動で前リビジョンへ切替が必要 | 高 |
| 外部API障害（Rainforest/FAL/OpenAI） | サーキットブレーカーなし。個別エラーハンドリングはあるが伝播 | 中 |
| メモリ不足（動画生成） | Cloud Runのメモリ制限未設定。OOMKillでインスタンス再起動 | 高 |
| 状態喪失（インスタンス再起動） | バッチ状態がファイルに永続化されるが、Cloud Runのephemeral storageのため失われる | 高 |

---

## 改善提案トップ3

### 1. 認証・認可の導入とAPIキー管理の改善（優先度: 最高）

**現状の問題**:
- 全エンドポイントが認証なしで公開されている
- APIキー（Rainforest, OpenAI）がクライアントからHTTPリクエストボディで送信される
- 誰でもAPIを叩いて外部API呼び出し（有料）を実行できる

**改善案**:
- Cloud Run の `--ingress=internal` もしくは IAM認証（`--no-allow-unauthenticated`）を有効化
- APIキーをGCP Secret Managerに格納し、Cloud Runの環境変数としてマウント
- デプロイコマンドに `--set-secrets` を追加:
  ```
  gcloud run deploy ... \
    --set-secrets=RAINFOREST_API_KEY=rainforest-key:latest,OPENAI_API_KEY=openai-key:latest
  ```
- フロントエンドからのAPIキー入力フィールドを廃止

### 2. CI/CDパイプラインの強化（優先度: 高）

**現状の問題**:
- テスト・リント・セキュリティスキャンなしの「push-to-prod」状態
- mainブランチへの直接pushで即時本番デプロイ

**改善案**:
```yaml
# deploy.yml に追加すべきステップ
- name: Lint & Type Check
  run: |
    pip install ruff mypy
    ruff check .
    mypy --ignore-missing-imports .

- name: Run Tests
  run: pytest tests/ -v

- name: Build & Scan Image
  run: |
    docker build -t $IMAGE .
    trivy image --exit-code 1 --severity HIGH,CRITICAL $IMAGE

- name: Deploy to Staging
  run: gcloud run deploy $SERVICE-staging ...

- name: Smoke Test
  run: curl -f https://$STAGING_URL/health

- name: Deploy to Production
  run: gcloud run deploy $SERVICE --tag=rev-$GITHUB_SHA ...
  environment: production  # GitHub Environment protection rules
```
- mainブランチへの直接pushを禁止し、PR必須化
- GitHub Environments の protection rules で承認フローを導入

### 3. 状態管理の外部化とCloud Runリソース設定（優先度: 高）

**現状の問題**:
- BATCH_STOREがインメモリ + ローカルファイル。Cloud Runのephemeral filesystemでインスタンス再起動時に喪失
- リソース制限未設定で、動画生成の重い処理がデフォルト設定（256MB RAM, 300秒タイムアウト）に抵触

**改善案**:
- バッチ状態をCloud Firestore/Cloud SQLに永続化
- デプロイコマンドにリソース制限を明示:
  ```
  gcloud run deploy ... \
    --memory=2Gi \
    --cpu=2 \
    --timeout=900 \
    --concurrency=5 \
    --max-instances=3 \
    --min-instances=0
  ```
- 動画生成などの長時間処理はCloud Tasksにオフロードし、非同期化を検討

---

## 次のPhaseへの引き継ぎ事項

### 即座に対応すべき事項（Phase 1 - 1週間以内）
1. **Cloud Runの認証有効化**: `--no-allow-unauthenticated` を追加し、IAP または API Gateway で認証を追加
2. **APIキーをSecret Managerへ移行**: クライアント送信をやめ、サーバーサイドで管理
3. **Dockerfileに`USER nonroot`追加**: 非rootユーザーでコンテナを実行
4. **requirements.txtのバージョンピン留め**: `pip freeze` でexact versionを固定（例: `fastapi==0.115.6`）
5. **Cloud Runリソース制限の設定**: memory, cpu, timeout, concurrency を明示的に指定

### 中期対応事項（Phase 2 - 1ヶ月以内）
1. **CI/CDパイプラインにテスト・リント・脆弱性スキャンを追加**
2. **Structured logging（JSON形式）への移行**: Cloud Loggingとの統合強化
3. **バッチ状態の外部永続化**（Firestore推奨）
4. **`os.environ["FAL_KEY"]`のrace condition修正**: 環境変数への書き込みをやめ、fal-clientにキーを直接渡す方法に変更
5. **ロールバック手順書の整備とデプロイタグ付与**

### 長期対応事項（Phase 3 - 3ヶ月以内）
1. **フロントエンドの分離**: インラインHTML（2100行のapp.py）からSPA + APIの分離
2. **動画生成の非同期化**: Cloud Tasks / Cloud Pub/Sub を用いたジョブキュー化
3. **監視ダッシュボード構築**: Cloud Monitoring でSLI/SLO設定、アラートポリシー定義
4. **ステージング環境の整備**: 本番と同一構成のステージングCloud Runサービスを追加
5. **負荷テストの実施**: 同時リクエスト数と外部API制限のキャパシティプランニング

### アーキテクチャ上の考慮事項
- Cloud Runの最大リクエストタイムアウトは3600秒（1時間）だが、バッチ処理（10商品 x 165秒 = 約28分）はSSEストリームで1リクエスト内に収める設計。HTTP接続の安定性に依存しており、中間プロキシのタイムアウトに注意
- `BATCH_STATE_DIR`へのファイルI/OはCloud Runのtmpfsに書き込まれる。メモリを消費するため、大量バッチ処理時にOOMリスクあり
- `shopee_core.py`の`subprocess`呼び出し（ffmpeg）は同期的であり、uvicornのイベントループをブロックする。`asyncio.create_subprocess_exec`への移行を検討

### セキュリティ注意事項
- 現在の構成では、Cloud RunのURLを知っている人が誰でもAPIを利用可能
- `/files/{asin}/{path:path}`エンドポイントは`is_relative_to`チェックがあるが、`asin`に`..`を含む入力への対応は確認が必要
- `gcloud run deploy`に`--service-account`が未指定。デフォルトのCompute Engine SAが使用され、過剰な権限が付与されている可能性
