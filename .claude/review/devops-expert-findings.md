# DevOps Expert レビュー結果

**レビュー日**: 2026-03-08
**レビュアー**: シニアDevOpsエンジニア（Kubernetes/Docker/CI/CD/GCP/SRE 経験10年）
**対象**: Shopee商品ページ最適化ツール（FastAPI + Cloud Run）
**レビュー対象ファイル**: `.github/workflows/deploy.yml`, `Dockerfile`, `requirements.txt`, `app.py`, `shopee_core.py`, `.gcloudignore`

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | 全APIエンドポイントに認証・認可が一切ない。Cloud RunのURLを知る者なら誰でも有料外部API（Rainforest, OpenAI, FAL）を呼び出せる | セキュリティ | 10 | 10 | 100 |
| 2 | APIキー（rainforest_key, openai_key）がクライアントのPOSTボディで送信される設計（`app.py` L2697-2698）。通信経路全体でキーが露出 | セキュリティ | 9 | 9 | 81 |
| 3 | CI/CDにテスト・リント・脆弱性スキャンのステップが皆無。`main`への直接pushが即本番デプロイになる（`deploy.yml` 全体） | CI/CD安全性 | 9 | 8 | 72 |
| 4 | バッチ状態が `BATCH_STORE`（インメモリdict）＋ローカルファイル（`/app/output/_batches/`）の二層管理。Cloud Runのエフェメラルストレージは再起動で消失し、ファイルはtmpfsを消費してOOMリスクがある（`app.py` L58, L302-310） | アーキテクチャ | 9 | 8 | 72 |
| 5 | `deploy.yml`にCloud Runのリソース制限（`--memory`, `--cpu`, `--timeout`, `--concurrency`, `--max-instances`）の指定が一切ない。動画生成（推定165秒/商品 x 最大10商品）でデフォルト制限（256MB RAM, 300秒タイムアウト）に抵触する | コスト/信頼性 | 8 | 8 | 64 |
| 6 | `requirements.txt`がバージョン範囲指定（`>=x.x,<y.0`）のみでピン留めなし。ビルドのたびに異なるパッケージバージョンがインストールされる可能性があり、再現性がない | 信頼性 | 8 | 7 | 56 |
| 7 | `deploy.yml`に`--quiet`フラグと`--source .`（Cloud Build経由）のみ。デプロイ後のスモークテスト・ヘルスチェック確認ステップがなく、デプロイ成功の定義が「Cloud Buildが終了すること」のみ | CI/CD安全性 | 7 | 8 | 56 |
| 8 | ロールバック手順が未定義。`--tag`や`--no-traffic`を使った段階的ロールアウト設定がなく、コンテナイメージに識別タグが付与されないため、特定バージョンへの即時復元手段がない | 運用 | 8 | 7 | 56 |
| 9 | `shopee_core.py`の`subprocess.run()`による`ffmpeg`呼び出しが同期的（L611-737）。uvicornのasyncイベントループをブロックし、動画生成中は他のHTTPリクエストが処理されない | パフォーマンス | 8 | 6 | 48 |
| 10 | `/health`エンドポイントが静的に`{"status": "ok"}`を返すだけ（`app.py` L1667-1669）。依存サービス（Google Drive API, Spreadsheet API, 外部AI API）の疎通確認が含まれない浅いチェック | 監視 | 7 | 7 | 49 |
| 11 | `logging.basicConfig(level=logging.INFO)`のみでプレーンテキストログ（`app.py` L50）。Cloud Loggingのstructured loggingと統合されておらず、JSON形式でないためログ検索・アラート設定が困難 | 監視 | 6 | 7 | 42 |
| 12 | `deploy.yml`の`gcloud run deploy`に`--service-account`未指定。Cloud RunがデフォルトのCompute Engine SAで動作し、プロジェクト全体への過剰権限（Editor相当）が付与される可能性 | セキュリティ | 7 | 6 | 42 |
| 13 | `Dockerfile`がマルチステージビルドでない。`apt-get install ffmpeg`（約200MB）が最終イメージに含まれ、ビルドツール系ファイルも残留してイメージサイズが肥大化 | 最適化 | 5 | 6 | 30 |
| 14 | `deploy.yml`の`--source .`はCloud Buildでビルドされるため、`Dockerfile`の`HEALTHCHECK`ディレクティブがCloud Runでは無視される。Cloud Run独自のヘルスチェック設定（`--port`, `--startup-cpu-boost`）が未設定 | 監視 | 5 | 6 | 30 |
| 15 | `shopee_core.py`がServiceAccountキーをファイルパスから読み込む（L1201, L1287, L1333, L1383）。Cloud RunではSecret Managerマウントが推奨で、キーファイルのローテーション・監査が困難 | セキュリティ | 6 | 5 | 30 |
| 16 | `app.py`L2697-2698でAPIキーをリクエストボディから受け取り、環境変数にフォールバックする設計。GUIに入力フォームがある場合、ブラウザ履歴・ログ等にキーが記録されるリスク | セキュリティ | 6 | 5 | 30 |
| 17 | `app.py`L54-59でKen Burns動画生成時に`/tmp/shopee_gen_YYYYMMDDHHMMSSF`を作成しクリーンアップしない実装リスク。Cloud Runのtmpfsが埋まると後続リクエストが失敗する（`shopee_core.py` L642-645） | 信頼性 | 6 | 5 | 30 |
| 18 | CI/CDパイプラインがステージング環境なしで本番に直接デプロイ。環境変数の不整合や依存サービスの互換性確認の機会がない | 環境管理 | 6 | 5 | 30 |
| 19 | `deploy.yml`にデプロイ完了後の通知（Slack, GitHub Status等）が未設定。デプロイ成否の確認が手動ログ参照のみ | 運用 | 4 | 5 | 20 |
| 20 | `app.py`のSSEストリーム（`/process-stream`, `/restart-from-step`, `/api/batch/{batch_id}/resume`）でgraceful shutdownが未実装。SIGTERMシグナル受信時に進行中の動画生成が中断され、中間状態が不整合になる | 信頼性 | 6 | 4 | 24 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 保守性（20点満点） | 11 | `Dockerfile`はシンプルで理解しやすい。非rootユーザー作成（`appuser`）は評価できる。`app.py`が3867行の単一ファイルにHTML/CSS/JS/ビジネスロジックが混在し、保守コストが高い。`.gcloudignore`は適切に整備されている。 |
| パフォーマンス（15点満点） | 7 | `python:3.12-slim`の採用は妥当。`pip --no-cache-dir`使用。しかし`subprocess.run(ffmpeg)`が同期実行でイベントループをブロックする。Cloud Runのconcurrency未設定で重い動画生成リクエストが競合するリスク。マルチステージビルド未採用。 |
| セキュリティ（15点満点） | 3 | 認証機構が皆無。APIキーがリクエストボディで送受信される。SAキーファイル依存。脆弱性スキャンなし。`requirements.txt`のバージョンピン留めなし（ supply chain attack リスク）。`--service-account`未指定による過剰権限。 |
| 可用性（15点満点） | 6 | Cloud Runのマネージドプラットフォームによるゾーン冗長は恩恵を受けるが、インメモリ＋エフェメラルファイルの状態管理により、インスタンス再起動でバッチ状態が消失する。ヘルスチェックが浅い。graceful shutdown未実装。 |
| 運用性（15点満点） | 5 | WIF（Workload Identity Federation）採用は評価できる。`--quiet`フラグでエラー詳細が隠蔽される。ロールバック手順なし。structured loggingなし。リソース制限未設定。アラート設定なし。デプロイ後の検証ステップなし。 |
| コスト効率（10点満点） | 4 | Cloud Runの従量課金は適切だが、`--max-instances`未設定で予期しないスケールアウトによるコスト急増リスク。外部AI API（FAL, Rainforest, OpenAI）呼び出しにレート制限なし。メモリ未設定でデフォルト256MBでは動画生成が頻繁にOOMKillされ再試行コストが発生。 |
| CI/CD成熟度（10点満点） | 2 | `main`への直接pushで即本番デプロイ。テスト・リント・ビルド検証・セキュリティスキャン・承認フロー・スモークテストが一切ない。事実上「push-to-prod」の状態で、CI/CDパイプラインとして機能していない。 |
| **合計（100点満点）** | **38** | 前回（2026-03-05）のスコア43から5点低下。コードの詳細調査により`subprocess`同期実行・`/tmp`クリーンアップ漏れ・SA過剰権限などの新規リスクを確認したため。 |

---

## デプロイリスク評価

### 高リスク（本番投入前に必須対応）

**1. 認証なし公開エンドポイント**
- 全エンドポイントが認証なしで公開されており、URL漏洩で有料API（OpenAI, Rainforest, FAL）が悪用される
- Cloud Runのデフォルト設定は`--allow-unauthenticated`。即座に`--no-allow-unauthenticated`へ変更が必要
- 本番投入前に**必須**の対応

**2. エフェメラルストレージへの状態永続化**
- `BATCH_STATE_DIR = OUTPUT_BASE / "_batches"`（`app.py` L54）に保存されるバッチ状態は、Cloud Runインスタンスの再起動・スケールダウン後に消失する
- 動画生成中（最長28分）にインスタンスが再起動した場合、クライアントはSSEストリームが切断されエラーを受け取るが、バッチ状態は不整合になる
- 本番投入前に**強く推奨**する対応

**3. Cloud Runタイムアウトと動画生成時間の不整合**
- Cloud Runデフォルトタイムアウト: 300秒（5分）
- 推定最大処理時間: 165秒/商品 x 10商品 = 1650秒（27.5分）
- `--timeout`未設定のため、バッチ処理がCloud Runに強制終了される
- 本番投入前に**必須**の対応（`--timeout=3600`を設定）

### 中リスク（1ヶ月以内に対応）

**4. `subprocess.run(ffmpeg)`のイベントループブロッキング**
- `shopee_core.py` L611, L622, L681, L707, L737でffmpegを同期実行
- uvicornの非同期イベントループが各ffmpeg呼び出し（最長60秒）の間ブロックされ、他のHTTPリクエストが処理されない
- 同時リクエストが複数ある場合に重大なパフォーマンス劣化が発生する

**5. `requirements.txt`の再現性なし**
- バージョン範囲指定のみ（例: `fastapi>=0.115,<1.0`）。ビルド時期によって異なるマイナーバージョンがインストールされ、動作の差異が生じる
- `pip-compile`または`pip freeze`でロックファイルを生成すべき

### 低リスク（3ヶ月以内に対応）

**6. マルチステージビルド未採用**
- 現在のイメージにはffmpeg、curl、フォントツールが含まれ、イメージサイズが大きい
- イメージサイズ最適化で起動時間短縮・転送コスト削減が見込まれるが、機能への影響はない

---

## 改善提案トップ3

### 提案1: Cloud Run認証の有効化とAPIキーのSecret Manager移行

**問題:**
- `deploy.yml`に`--no-allow-unauthenticated`が未指定で、全エンドポイントが公開されている
- `app.py` L2697-2698でAPIキーをリクエストボディから受け取るため、フロントエンドUIにキー入力フォームが存在する

**解決策（設定例含む）:**

```yaml
# .github/workflows/deploy.yml の修正案
- name: Deploy to Cloud Run
  run: |
    gcloud run deploy ${{ secrets.CLOUD_RUN_SERVICE }} \
      --source . \
      --region ${{ secrets.GCP_REGION }} \
      --project ${{ secrets.GCP_PROJECT_ID }} \
      --no-allow-unauthenticated \
      --service-account ${{ secrets.CLOUD_RUN_SA_EMAIL }} \
      --memory=2Gi \
      --cpu=2 \
      --timeout=3600 \
      --concurrency=3 \
      --max-instances=2 \
      --min-instances=0 \
      --set-secrets=RAINFOREST_API_KEY=rainforest-api-key:latest \
      --set-secrets=OPENAI_API_KEY=openai-api-key:latest \
      --set-secrets=FAL_KEY=fal-api-key:latest \
      --quiet
```

```bash
# シークレットの事前登録（一度だけ実行）
gcloud secrets create rainforest-api-key --data-file=- <<< "YOUR_KEY"
gcloud secrets create openai-api-key --data-file=- <<< "YOUR_KEY"
gcloud secrets create fal-api-key --data-file=- <<< "YOUR_KEY"

# Cloud Run SAにシークレットへのアクセス権限を付与
gcloud secrets add-iam-policy-binding rainforest-api-key \
  --member="serviceAccount:$CLOUD_RUN_SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"
```

- **実装コスト:** 低（設定変更のみ）
- **期待効果:** 不正利用による外部API課金リスクをゼロにする。APIキーのローテーションがGCPコンソールで完結する

---

### 提案2: CI/CDパイプラインへのテストゲートとデプロイ安全策の追加

**問題:**
- `deploy.yml`がcheckout直後にデプロイするだけ。テスト・リント・ビルド検証がない
- デプロイ後のスモークテストがなく、デプロイ成功の定義が曖昧
- ロールバック手段がない

**解決策（設定例含む）:**

```yaml
# .github/workflows/deploy.yml 全面改訂案
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"
      - name: Install dependencies
        run: pip install ruff pytest -r requirements.txt
      - name: Lint
        run: ruff check .
      - name: Run tests
        run: pytest tests/ -v --tb=short
        if: always()  # リントが失敗してもテスト結果を確認

  build-and-scan:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker build -t shopee-optimizer:${{ github.sha }} .
      - name: Scan for vulnerabilities
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: shopee-optimizer:${{ github.sha }}
          exit-code: "1"
          severity: "CRITICAL,HIGH"

  deploy:
    runs-on: ubuntu-latest
    needs: build-and-scan
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          service_account: ${{ secrets.GCP_SA_EMAIL }}
      - uses: google-github-actions/setup-gcloud@v2
      - name: Deploy to Cloud Run (with traffic tag)
        run: |
          gcloud run deploy ${{ secrets.CLOUD_RUN_SERVICE }} \
            --source . \
            --region ${{ secrets.GCP_REGION }} \
            --project ${{ secrets.GCP_PROJECT_ID }} \
            --no-allow-unauthenticated \
            --tag=rev-${{ github.sha }} \
            --no-traffic \
            --memory=2Gi --cpu=2 --timeout=3600 --concurrency=3 --max-instances=2
      - name: Smoke test (staging tag)
        run: |
          STAGING_URL=$(gcloud run services describe ${{ secrets.CLOUD_RUN_SERVICE }} \
            --region=${{ secrets.GCP_REGION }} \
            --format="value(status.traffic[].url)" | grep "rev-${{ github.sha }}")
          curl -f "$STAGING_URL/health" || exit 1
      - name: Shift 100% traffic to new revision
        run: |
          gcloud run services update-traffic ${{ secrets.CLOUD_RUN_SERVICE }} \
            --region=${{ secrets.GCP_REGION }} \
            --to-latest
```

**ロールバック手順（手動）:**
```bash
# 直前のリビジョン名を確認
gcloud run revisions list --service=$SERVICE --region=$REGION --limit=5

# 特定リビジョンへ100%戻す
gcloud run services update-traffic $SERVICE \
  --region=$REGION \
  --to-revisions=REVISION_NAME=100
```

- **実装コスト:** 中（`tests/`ディレクトリの整備が必要）
- **期待効果:** バグを本番到達前に検知。ロールバック時間をコマンド1行（30秒以内）に短縮

---

### 提案3: ffmpegの非同期実行化とCloud Runリソース最適化

**問題:**
- `shopee_core.py`の`subprocess.run()`によるffmpeg呼び出しがuvicornのイベントループをブロックする
- `--memory`, `--timeout`未設定で動画生成がOOMKillまたはタイムアウトで失敗する

**解決策（設定例含む）:**

```python
# shopee_core.py の修正案（ffmpegの非同期化）
import asyncio

async def _merge_bgm_async(video_path, output_path, bgm_path):
    """動画にBGMを合成する（非同期版）"""
    bgm_path = Path(bgm_path)
    video_path = Path(video_path)
    output_path = Path(output_path)

    if not bgm_path.exists():
        if video_path != output_path:
            shutil.copy2(video_path, output_path)
        return output_path

    # ffprobeを非同期実行
    probe_proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(probe_proc.communicate(), timeout=15)
    try:
        vid_duration = float(stdout.decode().strip())
    except (ValueError, AttributeError):
        vid_duration = 10.0

    fade_out_start = max(0, vid_duration - 2)

    # ffmpegを非同期実行（イベントループをブロックしない）
    ffmpeg_proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", str(video_path), "-i", str(bgm_path),
        "-filter_complex",
        f"[1:a]atrim=start=0:end={vid_duration},"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out_start}:d=2,"
        f"volume=0.4[bgm]",
        "-map", "0:v", "-map", "[bgm]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(output_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(ffmpeg_proc.communicate(), timeout=120)
    return output_path
```

```python
# app.py - /tmp クリーンアップの明示化
# shopee_core.py L642 の generate_video_kenburns を修正
tmp = Path("/tmp") / f"shopee_gen_{uuid.uuid4().hex}"
try:
    tmp.mkdir(parents=True)
    # ... 動画生成処理 ...
finally:
    shutil.rmtree(tmp, ignore_errors=True)  # 必ずクリーンアップ
```

Cloud Runリソース設定（`deploy.yml`に追加）:
```
--memory=2Gi          # ffmpeg + AI処理に必要な最低ライン
--cpu=2               # 動画エンコード並列処理
--timeout=3600        # 最大1時間（10商品バッチの余裕を持たせた上限）
--concurrency=3       # メモリ消費を考慮し低めに設定
--max-instances=2     # 外部APIクォータ保護のため上限設定
--execution-environment=gen2  # 第2世代はCPU常時割り当てモードに対応
```

- **実装コスト:** 中（ffmpegを呼ぶ関数すべてを`async`化する必要があり、呼び出し側のSSEジェネレータも対応が必要）
- **期待効果:** 動画生成中も他のAPIリクエスト（`/health`, `/batches`等）が応答できる。OOMKillが解消される

---

## 次のPhaseへの引き継ぎ事項

### Phase 1（1週間以内、本番公開前に必須）

| 対応 | ファイル | 具体的アクション |
|------|---------|----------------|
| Cloud Run認証有効化 | `deploy.yml` | `--no-allow-unauthenticated`を追加 |
| Cloud Runリソース設定 | `deploy.yml` | `--memory=2Gi --cpu=2 --timeout=3600 --concurrency=3 --max-instances=2`を追加 |
| APIキーをSecret Managerへ移行 | `deploy.yml`, `app.py` | `--set-secrets`でマウント。リクエストボディからのキー受け取りを廃止 |
| `--service-account`の明示指定 | `deploy.yml` | 最小権限のSAを作成して指定 |
| `requirements.txt`のバージョンピン留め | `requirements.txt` | `pip-compile requirements.in`または`pip freeze`で固定 |

### Phase 2（1ヶ月以内）

1. **CI/CDパイプライン強化**: `lint-and-test` → `build-and-scan` → `deploy(--no-traffic)` → `smoke-test` → `traffic-shift`のフロー整備
2. **Structured logging導入**: `python-json-logger`を追加し、Cloud Loggingと統合。アラートポリシー設定
3. **バッチ状態の外部永続化**: Cloud Firestoreへ移行。`BATCH_STORE`のインメモリ依存を解消
4. **`/tmp`クリーンアップ保証**: `generate_video_kenburns`（`shopee_core.py` L637-）の`try/finally`ブロックで`shutil.rmtree`を確実に実行
5. **ロールバック手順書の整備**: `--tag`付きデプロイへ変更し、`gcloud run services update-traffic`による手順を文書化

### Phase 3（3ヶ月以内）

1. **ffmpegの非同期実行化**: `asyncio.create_subprocess_exec`への移行でイベントループブロッキングを解消
2. **動画生成の非同期ジョブ化**: Cloud Tasks + Cloud Pub/Sub でジョブキュー化し、SSEストリームの長時間接続依存を解消
3. **ステージング環境整備**: `main`ブランチへのmergeでstagingへ自動デプロイ、手動承認後にproductionへ昇格するフロー
4. **SLI/SLO定義**: Cloud Monitoringで「動画生成成功率 >= 95%」「APIレスポンスタイム p99 <= 30秒」等を定義

### Cloud Run特性への注意事項

- **エフェメラルストレージ**: `/app/output`はインスタンス再起動で消失する。現在のバッチ状態ファイル保存（`app.py` L302-310）は一時的なキャッシュとしてのみ信頼すること
- **コールドスタート**: `--min-instances=0`の場合、Dockerイメージのpull + uvicornの起動に数秒かかる。`/health`エンドポイントへのアクセス前にコールドスタートが発生する可能性
- **SSEと長時間接続**: Cloud Runの最大リクエストタイムアウトは3600秒。`--timeout=3600`を設定しても、中間のロードバランサーやプロキシが先にタイムアウトする場合がある。クライアント側の再接続ロジック（`retry: 3000`は`app.py` L2705で実装済み）が必要
- **メモリとtmpfs**: Cloud Runのローカルディスク（`/tmp`含む）はRAMから割り当てられる。`--memory=2Gi`設定時に大量の一時ファイルを作成するとRAMを圧迫する。ffmpegの中間ファイルは必ずクリーンアップすること

### セキュリティ注意事項（別エージェントへの引き継ぎ）

- `.env`ファイル（`RAINFOREST_API_KEY`, `MINIMAX_API_KEY`, `KLING_ACCESS_KEY`, `FAL_KEY`等）がローカルに存在することを確認。`.gitignore`には記載があるが、これらキーは既に漏洩しているリスクがある。**即座にローテーションを推奨する**
- `client_secret_714089953721-...json`がプロジェクトルートに存在する。このファイルは`.gcloudignore`にリストされていないため、`gcloud run deploy --source .`でCloud Buildに転送される可能性がある。`.gcloudignore`に`client_secret_*.json`を追加すること
- `shopee_core.py` L1201等でSAキーを`from_service_account_file()`で読み込む実装は、キーファイルのCloud Run上での扱いを明確にする必要がある。Secret ManagerでJSON文字列として管理し、`from_service_account_info()`で読み込む方式への移行を検討
