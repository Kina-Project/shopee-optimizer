# Senior Backend Engineer レビュー結果

対象: `app.py`（3,866行）、`shopee_core.py`（1,488行）
レビュー日: 2026-03-08

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | BATCH_STOREのインメモリ管理（マルチインスタンス非対応） | スケーラビリティ | 9 | 9 | 81 |
| 2 | process_stream / resume_batch の処理ロジック完全重複 | 保守性 | 8 | 8 | 64 |
| 3 | Syncジェネレータ内でブロッキングI/O（外部API呼び出し） | パフォーマンス | 9 | 7 | 63 |
| 4 | gspreadの認証・接続を毎回生成（コネクション再利用なし） | パフォーマンス | 7 | 8 | 56 |
| 5 | APIキーをリクエストボディで受け取る設計 | セキュリティ | 8 | 6 | 48 |
| 6 | /restart-from-stepがstep 4の画像翻訳ロジックを再度コピペ | 保守性 | 7 | 6 | 42 |
| 7 | /drive-video プロキシが動画全体をメモリに展開 | パフォーマンス | 7 | 5 | 35 |
| 8 | エンドポイント命名がRESTful原則に従っていない | API設計 | 5 | 7 | 35 |
| 9 | スプレッドシートのASIN検索がO(N)の線形スキャン | パフォーマンス | 6 | 5 | 30 |
| 10 | エラー時の内部例外メッセージがそのままユーザーに露出 | エラーハンドリング | 6 | 5 | 30 |
| 11 | APIバージョニング戦略がない | API設計 | 4 | 7 | 28 |
| 12 | get_configが毎回環境変数を読み直す（キャッシュなし） | パフォーマンス | 4 | 6 | 24 |
| 13 | レート制限が未実装 | スケーラビリティ | 6 | 4 | 24 |
| 14 | 画像ダウンロード失敗時のリトライなし | 信頼性 | 5 | 4 | 20 |
| 15 | download_imagesが拡張子をURLの文字列マッチで判定 | 保守性 | 3 | 4 | 12 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 機能性（25点満点） | 19 | 7ステップのパイプラインは完動している。一時停止・再開・ステップ再実行など要件対応は充実。ただしCloud Runのマルチインスタンス構成では動作しない根本的欠陥がある |
| 保守性（20点満点） | 9 | process_stream / resume_batch / restart-from-stepの3箇所にStep4（画像翻訳ループ）が完全コピペされており、バグ修正が3箇所同期必須。app.pyが3,866行の単一ファイル |
| パフォーマンス（15点満点） | 8 | syncジェネレータ内でブロッキングAPI呼び出し（Rainforest/OpenAI/fal.ai各30〜120秒）。Cloud RunのCPU配分がストリーミング中に抑制されるリスクがある。gspreadの認証を7ステップ毎に再実行 |

**合計: 36 / 60点**

---

## 改善提案トップ3

---

### 提案1: BATCH_STOREをRedisまたはCloud Firestoreに移行する

**問題:**

`app.py` L58 の `BATCH_STORE = {}` はプロセスローカルのインメモリDictionary。Cloud Runはトラフィック増加時に複数インスタンスを起動するため、インスタンスAで作成したバッチをインスタンスBに問い合わせると404になる。現在はJSONファイルへのフォールバック（L313〜L326）で部分的に対応しているが、Cloud RunのコンテナはリクエストごとにCPUが割り当てられるため、ファイルシステムも共有されない（Cloud Storageマウントなしの場合）。

**解決策（最小コスト: Firestore版）:**

```python
# batch_store.py（新規ファイル）
from google.cloud import firestore

_db = None

def _get_db():
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db

def save_batch(batch_id: str, batch: dict) -> None:
    _get_db().collection("batches").document(batch_id).set(batch)

def get_batch(batch_id: str) -> dict | None:
    doc = _get_db().collection("batches").document(batch_id).get()
    return doc.to_dict() if doc.exists else None

def list_batches(limit: int = 50) -> list[dict]:
    docs = (
        _get_db().collection("batches")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [d.to_dict() for d in docs]
```

移行コストを最小にするため、既存の `BATCH_STORE[batch_id]` への代入を `save_batch(batch_id, ...)` に、`BATCH_STORE.get(batch_id)` を `get_batch(batch_id)` に置換するだけで済む。

**実装コスト:** 中（Firestoreの有効化 + app.py内の約10箇所の置換）
**期待効果:** Cloud Runのオートスケールが安全に機能するようになる。バッチ件数が増えてもメモリ圧迫なし

---

### 提案2: process_stream / resume_batch の共通ジェネレータ関数を抽出する

**問題:**

Step1〜7の処理本体が `process_stream`（L2684）、`resume_batch`（L3193）、`restart_from_step`（L2302）の3エンドポイントにほぼ完全にコピーされている。特にStep4（画像翻訳ループ）は L2920〜L2970 / L3397〜L3437 / L2419〜L2461 の3箇所に存在する。

1箇所のバグ修正を3箇所に同期する必要があり、現状すでに細かい差分が混入している。例として `resume_batch` では Step2の名前が「商品画像取得」（L3318）だが `process_stream` では「画像ダウンロード」（L2831）となっている。

**解決策（骨格）:**

```python
# pipeline.py（新規ファイル）

def _process_step4_image_translation(
    image_paths: list,
    product: dict,
    openai_key: str,
    asin: str,
    output_dir: Path,
    idx: int,
    emit,  # callable
) -> Generator:
    """Step4の画像翻訳ループ。3エンドポイント共通。"""
    total_images = len(image_paths)
    est_per_image = 45
    est = f"約{total_images * est_per_image}秒（{total_images}枚）"
    yield emit({"type": "step", "index": idx, "step": 4,
                "total": 7, "name": "画像テキスト英語化", "est": est})

    en_dir = output_dir / "images_en"
    en_dir.mkdir(exist_ok=True)
    translated_paths = []
    consecutive_failures = 0
    max_consecutive_failures = 2
    step4_start = time.time()

    for img_i, img_path in enumerate(image_paths):
        out_path = en_dir / f"{Path(img_path).stem}_en.png"
        # ...（単一実装）
        yield emit({"type": "step_progress", ...})

    return translated_paths
```

各エンドポイントは `yield from _process_step4_image_translation(...)` を呼ぶだけになる。

**実装コスト:** 高（リファクタリング作業量は大きいが、テストを書きながら進めれば安全）
**期待効果:** バグ修正・機能追加が1箇所で済む。app.pyのコード量が約40%削減される見込み

---

### 提案3: gspreadの認証をセッションレベルでキャッシュする

**問題:**

`write_to_spreadsheet`（shopee_core.py L1189）、`append_video_generation_log`（L1264）、`fetch_video_generation_history`（L1322）、`fetch_product_sheet_history`（L1372）の4関数が、それぞれ独立してサービスアカウントの認証情報読み込み → gspread接続 → スプレッドシートオープンを実行している。

7ステップのパイプラインで毎ステップ `write_step_checkpoint` が呼ばれるため、1商品あたり最大7回の認証・接続が発生する。gspread の初期化は1回あたり約0.5〜1秒かかる。

**解決策:**

```python
# shopee_core.py に追加

_gspread_ss_cache: dict = {}

def _get_spreadsheet(config: dict):
    """gspread Spreadsheetオブジェクトをプロセス内でキャッシュ。"""
    key_path = str(config["gcp_key_path"])
    ss_id = config["spreadsheet_id"]
    cache_key = (key_path, ss_id)
    if cache_key in _gspread_ss_cache:
        return _gspread_ss_cache[cache_key]

    creds = SACredentials.from_service_account_file(
        key_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(ss_id)
    _gspread_ss_cache[cache_key] = ss
    return ss

# write_to_spreadsheet, append_video_generation_log 等の先頭を以下に置換:
# Before: creds = SACredentials.from_service_account_file(...) / gc = gspread.authorize(creds) / ss = gc.open_by_key(...)
# After:  ss = _get_spreadsheet(config)
```

**実装コスト:** 低（4関数の先頭10行を置換するだけ。工数1〜2時間）
**期待効果:** スプレッドシートAPI呼び出しの初期化オーバーヘッドが7回→1回に削減。1商品あたり約3〜7秒の短縮

---

## 潜在的な技術的負債

### 1. Syncジェネレータ内のブロッキングAPI呼び出し（将来のスケールボトルネック）

`process_stream`（L2702）の `generate()` は通常のPython同期ジェネレータ。内部で `fetch_amazon_product`（timeout=30秒）、`generate_video_ai`（fal.ai、120秒超）などのブロッキング呼び出しをしている。

FastAPIはasyncフレームワークだが、`StreamingResponse` に同期ジェネレータを渡した場合、Starlette は `run_in_threadpool` でラップして実行する。これはWorkerスレッドプール（デフォルト40スレッド）を長時間占有する。10件バッチ × 165秒 = 約27分間スレッドを占有することになり、同時に3〜4バッチが走るとスレッドプールが枯渇する。

将来の解決策: `async def generate()` + `asyncio.to_thread()` でブロッキング処理をラップ、または専用バックグラウンドタスクキュー（Cloud Tasks）への移行。

### 2. BATCH_PAUSE_REQUESTS の競合状態

`BATCH_PAUSE_REQUESTS`（L59）もインメモリDictionary。`pause_batch` が `BATCH_PAUSE_REQUESTS[batch_id] = True` をセットしても、別インスタンスで動いている `generate()` ループには届かない。Cloud Run単一インスタンス運用中は機能するが、スケールアウト時に停止が効かなくなる。

### 3. /restart-from-step のASINバリデーション欠如

`restart_from_step`（L2303）は `batch_id` と `asin` をリクエストボディから受け取るが、ASINの形式チェック（`r'^[A-Z0-9]{10}$'`）がない。`/files/{asin}` エンドポイント（L2640）にはバリデーションがあるが、バッチ関連エンドポイントでは欠けている。悪意あるリクエストで任意のASIN文字列がバッチデータに混入する可能性がある。

### 4. /finalize のgspread APIコール集中（Rate Limit リスク）

`finalize`（L3748）の `for product in batch["results"]` ループ内で `write_to_spreadsheet` と `append_video_generation_log` を商品数分呼んでいる。各呼び出しがgspread APIを叩くため、10商品で最大20回のAPIコール。gspread の Rate Limit（100 requests/100 seconds）に引っかかる可能性がある。バッチ書き込み（`batch_update`）への移行が将来必要。

### 5. 画像ファイルの拡張子判定ロジックの脆弱性

`download_images`（shopee_core.py L270）がURLに `.png` という文字列が含まれているかで拡張子を決定している。Content-Typeヘッダーを参照すべき。

```python
# 現在（不正確）
ext = "png" if ".png" in url.lower() else "jpg"

# 改善案
content_type = resp.headers.get("content-type", "")
ext = "png" if "png" in content_type else "jpg"
```

### 6. app.pyに埋め込まれた3,000行超のHTMLテンプレート

`INDEX_HTML`（L343〜）がPythonファイルにインライン定義されており、ファイル全体の約80%を占める。HTMLの変更にPythonの再デプロイが必要で、静的ファイルのCDNキャッシュも効かない。将来的にJinja2テンプレートファイル分離またはStatic Files配信への移行を推奨。

---

## 次のPhaseへの引き継ぎ事項

### 即時対応推奨（本番障害リスク）

**[1] Cloud Run設定でmin-instances=1を強制するか、BATCH_STOREをFirestoreに移行する**

現状はインスタンスが2つ以上になった瞬間にバッチが消える。

- 最小コスト回避策: Cloud Runの `--min-instances=1 --max-instances=1` で単一インスタンス固定
- 根本解決策: 提案1のFirestore移行

**[2] APIキー受信方式の変更**

`/process-stream`（L2697）が `rainforest_key` と `openai_key` をリクエストボディで受け取っている。ブラウザのDevToolsで誰でも確認できる状態。

改善案: 全APIキーを環境変数のみから取得し、リクエストボディからのキー受け入れを削除する。

**[3] Cloud Runのリクエストタイムアウト設定確認**

動画生成（fal.ai）のWait時間が最長で120秒を超えることがあり、Cloud Runのデフォルトリクエストタイムアウト（60秒）と競合する可能性がある。`--timeout=600` の設定を明示的に確認・設定すること。

### 中期対応推奨（保守性向上）

**[4] Step4画像翻訳ロジックの共通化（提案2）**

現在3箇所にコピーが存在する。`_process_step4_image_translation(...)` として抽出し、3エンドポイントから呼び出す形に統一する。

**[5] gspreadキャッシュの実装（提案3）**

工数1〜2時間、リスクほぼゼロで実施可能。

### アーキテクチャ観点での申し送り

- 現在のSSEストリーミング設計は「ユーザー1人が1バッチを実行」というユースケースに最適化されており、複数ユーザーが同時実行した場合のスレッドプール枯渇リスクがある
- 将来的に同時5バッチ以上を想定するなら、Cloud Tasks + Pub/Subによる非同期ジョブキューへの移行が必要
- `shopee_core.py` の `process_product`（L1418）はCLI用パイプラインとして残っているが、Web API側の `generate()` ループと実装が乖離しており、将来の機能追加で混乱を招く可能性がある。Webルートに統一するか、CLIを完全分離することを推奨
