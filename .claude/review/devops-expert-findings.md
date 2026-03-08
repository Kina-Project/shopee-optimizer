# DevOps Expert レビュー結果

対象 PR: #19〜#22
レビュー日: 2026-03-08
レビュアー: シニアDevOpsエンジニア（経験10年）

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | BATCH_STOREがオンメモリ辞書のためCloud Run再起動で消失 | 耐障害性 | 10 | 10 | 100 |
| 2 | BATCH_STATE_DIRがCloud Runのエフェメラルストレージ上に保存 | 耐障害性 | 9 | 9 | 81 |
| 3 | resume_batchでopenai_key/rainforest_keyがバッチ状態に保存されていない | バグ | 9 | 8 | 72 |
| 4 | SERPER_API_KEYが環境変数直接参照（Secret Manager未使用） | セキュリティ | 8 | 7 | 56 |
| 5 | process-streamとresume_batchに700行超の処理ロジックが重複 | 保守性 | 7 | 10 | 70 |
| 6 | duckduckgo-searchが非公式スクレイピングで突然失敗するリスク | 可用性 | 7 | 6 | 42 |
| 7 | openai_key/rainforest_keyがリクエストボディから受け取れる設計が残存 | セキュリティ | 7 | 7 | 49 |
| 8 | /drive-videoプロキシが動画全体をメモリバッファに展開 | パフォーマンス | 6 | 5 | 30 |
| 9 | 旧Google CSE環境変数（GOOGLE_CSE_API_KEY, GOOGLE_CSE_CX）の残骸 | 環境管理 | 6 | 4 | 24 |
| 10 | ファイルシステムフォールバック復元時にfolder_id/folder_urlが空 | 機能 | 7 | 6 | 42 |
| 11 | /healthエンドポイントがDrive/Spreadsheet接続確認なし（Shallow Healthcheck） | 監視 | 6 | 5 | 30 |
| 12 | duckduckgo-searchのバージョン固定が緩い（>=7.0,<8.0） | 再現性 | 5 | 4 | 20 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 保守性（20点満点） | 11 | resume_batchにprocess-streamと同一の700行超のロジックが重複。DRY原則違反。変更時の同期漏れリスクが高い（PR#19のresume修正はこの問題の典型例） |
| パフォーマンス（15点満点） | 8 | SSE streaming設計は適切。ただし同期ジェネレータがCloud Runの1インスタンスを長時間占有する構造 |
| セキュリティ（20点満点） | 12 | ASIN/file_idバリデーション、XSS対策(html.escape)、パストラバーサル対策は実装済み。APIキーのリクエスト受け渡しとSecret Manager未使用が減点 |
| 信頼性・耐障害性（25点満点） | 13 | ファイルシステムフォールバック(PR#21-22)は正しい方向だが、Cloud RunのエフェメラルFS自体が根本解決になっていない |
| CI/CD・デプロイ安全性（20点満点） | 8 | Dockerfile・CI定義が確認できず評価困難。requirements.txtのバージョン固定は良好。デプロイ手順の文書化なし |

**合計: 52/100**

---

## デプロイリスク評価

### 高リスク（本番投入前に必須対応）

**[リスク1] BATCH_STOREとBATCH_STATE_DIRの消失問題（課題#1, #2）**

Cloud Runはリクエストがない時間帯にインスタンスを終了する（コールドスタート設計）。
その際、オンメモリの `BATCH_STORE` とコンテナFS上の `BATCH_STATE_DIR` が両方失われる。

- `OUTPUT_BASE` は `Path(__file__).parent / "output"` にデフォルト設定されており、コンテナローカルに依存している
- `lifespan` 起動時の `load_batch_store()` はファイルが残っていれば復元できるが、新規コンテナ起動後にはファイルが存在しない
- PR#21-22のファイルシステムフォールバックは「同一インスタンス上でBATCH_STOREがなぜか欠落した場合」にのみ有効

影響: バッチ処理完了後にユーザーがページ離脱→Cloud Runがスケールダウン→再訪問すると `batch_id` が消えており、再生成・確定・履歴参照が不能になる。

**[リスク2] resume_batchのAPIキー取得バグ（課題#3）**

```python
# app.py L2628-2629（resume_batch内）
openai_key = batch.get("openai_key", "") or os.environ.get("OPENAI_API_KEY", "")
rainforest_key = batch.get("rainforest_key", "") or os.environ.get("RAINFOREST_API_KEY", "")
```

`process-stream`（L2075-2076）はAPIキーをリクエストボディから受け取るが、バッチ状態（BATCH_STORE）に保存していない。
そのため `resume` 時に `batch.get("openai_key")` は常に空文字を返す。

環境変数 `OPENAI_API_KEY` / `RAINFOREST_API_KEY` がCloud Runに設定されていれば動作するが、
フォールバックが機能しているだけであり、設計上の意図と実装が乖離している。
PR#19でos.environ.getに統一したことで表面上は動いているが、根本的な設計ミスは残存。

### 中リスク（次スプリント以内に対応推奨）

**[リスク3] SERPER_API_KEYの管理（課題#4）**

`shopee_core.py` L297:
```python
api_key = os.environ.get("SERPER_API_KEY", "").strip()
```

Cloud Runの環境変数への設定自体は問題ないが、GCP Secret Managerを使用していない。
Cloud Runの環境変数はIAM権限を持つユーザーがコンソールから閲覧可能であり、
キーローテーション時の対応が手動になる。

**[リスク4] duckduckgo-searchのフォールバック依存（課題#6）**

DuckDuckGoは非公式ライブラリ経由のスクレイピングであり、利用規約変更やbot対策強化により突然失敗するリスクがある。
`duckduckgo-search>=7.0,<8.0` のバージョン固定は行われているが、DuckDuckGo側のサービス変更には無力。
Serperが機能している間は問題ないが、Serper API枯渇時の最終手段として依存していることを認識すること。

### 低リスク（改善推奨）

**[リスク5] 旧CSE環境変数の残骸（課題#9）**

PR#20でSerper.devに移行したが、Cloud Runに `GOOGLE_CSE_API_KEY` / `GOOGLE_CSE_CX` が残存している場合、
コードから参照されないデッドな秘密情報を保持し続けることになる。セキュリティ監査時の混乱要因になる。

---

## 改善提案トップ3

### 提案1: バッチ状態の永続化をCloud Storageに移行

**問題:**
`BATCH_STATE_DIR` がコンテナローカルファイルシステムに依存しており、
Cloud Runのインスタンス再起動・スケールイン後にバッチ状態が完全消失する。
PR#21-22のファイルシステムフォールバックは同一インスタンス内でのBATCH_STORE欠落には対応するが、
インスタンス消滅には対応していない。

**解決策（設定例含む）:**

```python
# app.py に追加: GCSへのバッチ状態保存
import os
from google.cloud import storage as gcs_storage

GCS_BATCH_BUCKET = os.environ.get("GCS_BATCH_BUCKET", "")

def save_batch_state_gcs(batch_id: str, batch: dict):
    """バッチ状態をGCSに保存（ローカル保存と併用）"""
    if not GCS_BATCH_BUCKET:
        return
    try:
        client = gcs_storage.Client()
        bucket = client.bucket(GCS_BATCH_BUCKET)
        blob = bucket.blob(f"batches/{batch_id}.json")
        blob.upload_from_string(
            json.dumps(batch, ensure_ascii=False),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning("GCSバッチ状態保存失敗（ローカルは保存済み）: %s", e)

def load_batch_from_gcs(batch_id: str) -> dict | None:
    """GCSからバッチ状態を復元"""
    if not GCS_BATCH_BUCKET:
        return None
    try:
        client = gcs_storage.Client()
        bucket = client.bucket(GCS_BATCH_BUCKET)
        blob = bucket.blob(f"batches/{batch_id}.json")
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception:
        return None
```

`get_batch_or_none` を以下に変更:
```python
def get_batch_or_none(batch_id: str):
    # 1. メモリキャッシュ（高速）
    batch = BATCH_STORE.get(batch_id)
    if batch:
        return batch
    # 2. ローカルファイル（同一インスタンスでのメモリ欠落）
    path = batch_state_path(batch_id)
    if path.exists():
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            BATCH_STORE[batch_id] = batch
            return batch
        except Exception:
            pass
    # 3. GCS（インスタンス再起動後の完全復元）
    batch = load_batch_from_gcs(batch_id)
    if batch:
        BATCH_STORE[batch_id] = batch
        return batch
    return None
```

`save_batch_state` 内で `save_batch_state_gcs` を追加呼び出しする。

Cloud Runに追加する環境変数:
```
GCS_BATCH_BUCKET=your-project-shopee-batches
```

Cloud Runのサービスアカウントに `roles/storage.objectAdmin` を付与。
google-cloud-storage を requirements.txt に追加。

- 実装コスト: 中
- 期待効果: インスタンス再起動後もバッチ状態が復元可能。ユーザー体験の大幅改善。GCSの料金は月数円以下（JSONファイルは小サイズ）

---

### 提案2: SERPER_API_KEYをGCP Secret Managerで管理

**問題:**
`SERPER_API_KEY` がCloud Runの環境変数として平文設定されている。
GCPコンソールの権限管理で制限できるが、キーローテーションが手動になる。

**解決策（設定例含む）:**

```bash
# Secret Managerにシークレットを作成
gcloud secrets create serper-api-key \
  --replication-policy="automatic"

echo -n "your-serper-api-key" | \
  gcloud secrets versions add serper-api-key --data-file=-

# Cloud Runのサービスアカウントにシークレット参照権限を付与
gcloud secrets add-iam-policy-binding serper-api-key \
  --role="roles/secretmanager.secretAccessor" \
  --member="serviceAccount:YOUR_SERVICE_ACCOUNT@PROJECT.iam.gserviceaccount.com"

# デプロイ時のコマンド変更
# 変更前: --set-env-vars SERPER_API_KEY=xxx
# 変更後:
gcloud run deploy shopee \
  --set-secrets SERPER_API_KEY=serper-api-key:latest
```

コードの変更は不要（Cloud Runが自動的に環境変数として注入する）。

同様に `RAINFOREST_API_KEY` / `OPENAI_API_KEY` / `FAL_KEY` / `DRIVE_REFRESH_TOKEN` 等の全シークレットを順次移行することを推奨する。

- 実装コスト: 低（コードゼロ変更、デプロイコマンド変更のみ）
- 期待効果: キーローテーション自動化、アクセス監査ログ、最小権限原則の徹底

---

### 提案3: process-streamとresume_batchの共通ロジック抽出

**問題:**
`process-stream`（L2080〜L2577）と `resume_batch`（L2625〜L3017）に、7ステップの処理ロジックがほぼ完全に重複している。
約400行の同一コードが2箇所に存在し、バグ修正や機能追加時に両方を修正する必要がある。
実際PR#19の「resume_batch APIキー修正」はこの重複構造が原因でデグレードが生まれた典型例。

**解決策（設定例含む）:**

```python
# app.py: 7ステップ処理を共通ジェネレータに抽出
def _run_batch_pipeline(
    batch_id: str,
    urls: list[str],
    start_idx: int,
    existing_results: list,
    rainforest_key: str,
    openai_key: str,
    skip_image_translate: bool,
    auto_finalize: bool,
):
    """バッチ処理の共通ジェネレータ。process-streamとresume_batchで共用。"""
    config = get_config()
    started = datetime.now()
    est_total = len(urls) * EST_PER_PRODUCT_SEC
    results = list(existing_results)

    def emit(payload):
        elapsed = int((datetime.now() - started).total_seconds())
        payload["batch_id"] = batch_id
        payload["remaining_sec"] = max(0, est_total - elapsed)
        return sse_event(payload)

    # Proxy/CDN buffering対策（初回のみ）
    if start_idx == 0:
        yield ":" + (" " * 2048) + "\n\n"
        yield "retry: 3000\n\n"

    for resume_i, url in enumerate(urls):
        idx = start_idx + resume_i
        # Step 1〜7 の共通処理...
        yield ...

    # 完了後の後処理
    BATCH_STORE[batch_id]["results"] = results
    BATCH_STORE[batch_id]["updated_at"] = now_iso()
    save_batch_state(batch_id)
    yield emit({"type": "all_done", "batch_id": batch_id, "results": results})


@app.post("/process-stream")
async def process_stream(request: Request):
    # バリデーションのみ
    # ...
    batch_id = _initialize_batch(urls, auto_finalize)
    return StreamingResponse(
        _run_batch_pipeline(batch_id, urls, 0, [], rainforest_key, openai_key, skip_image_translate, auto_finalize),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/batch/{batch_id}/resume")
async def resume_batch(batch_id: str):
    # バッチ取得・バリデーションのみ
    # ...
    return StreamingResponse(
        _run_batch_pipeline(batch_id, remaining_urls, stopped_at_index, existing_results, rainforest_key, openai_key, skip_image_translate, auto_finalize),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- 実装コスト: 高（大規模リファクタリング。デグレードリスクあり。十分なE2Eテストが必要）
- 期待効果: 保守性の大幅向上。将来の機能追加・バグ修正コストを半減。PR#19のような「片方だけ直した」系のバグを構造的に防止できる

---

## 次のPhaseへの引き継ぎ事項

### セキュリティレビューへの申し送り

1. **旧CSE環境変数の削除確認**: Cloud Runのコンソールで `GOOGLE_CSE_API_KEY` / `GOOGLE_CSE_CX` が残存していないか確認・削除を依頼する
2. **APIキーのリクエストボディ受け渡しの廃止**: `process-stream`（L2075-2076）でリクエストボディから `rainforest_key` / `openai_key` を受け取る設計は攻撃面を増やす。環境変数のみに統一する対応を検討すること
3. **全シークレットのSecret Manager移行**: SERPER_API_KEYを皮切りに、RAINFOREST_API_KEY/OPENAI_API_KEY/FAL_KEY/DRIVE_REFRESH_TOKEN等を順次移行する

### インフラチームへの申し送り

1. **Cloud Run最小インスタンス数の設定確認**: 常時稼働が要件なら `--min-instances=1` を設定すること。BATCH_STORE消失リスクを軽減できる（GCS移行と合わせて判断）
2. **GCS_BATCH_BUCKET の準備**: バッチ状態永続化対応のためGCSバケットを作成し、Cloud Runサービスアカウントに `roles/storage.objectAdmin` を付与する
3. **ヘルスチェックの強化**: 現在の `/health` は `{"status": "ok"}` を返すだけ。Drive認証・Spreadsheet接続の疎通確認を追加することで本番障害の早期検知が可能になる

### 開発チームへの申し送り

1. **PR#21-22の効果範囲の明確化**: ファイルシステムからの復元フォールバックは「同一インスタンスでBATCH_STOREがなぜか欠落した場合」にのみ有効。インスタンス再起動後の復元には対応していない。コードコメントとドキュメントに明記すること
2. **duckduckgo-searchの監視強化**: 非公式ライブラリのため突然の利用不能に備えてエラーログを監視すること。Serperが月2,500回無料枠を超えた場合の有料プラン判断を先に決めておく
3. **resume_batchのAPIキー設計の正常化**: `process-stream` でAPIキーをバッチ状態に保存するか、完全に環境変数のみに統一するかを決定すること。現状は「環境変数にあれば偶然動く」状態

### テストへの申し送り

- Cloud Runインスタンス再起動シミュレーション（コンテナ再起動後の `/batch/{batch_id}` 取得が正常応答するか）
- Serper APIキー未設定時の `search-images` エンドポイントがDuckDuckGoにフォールバックし、タイムアウトせずに応答するか
- `resume_batch` がBATCH_STOREに存在しないbatch_idでもファイルから復元できるか
- 10件バッチの途中でインスタンスが再起動した場合のユーザー体験
