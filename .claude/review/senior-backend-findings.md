# Senior Backend Engineer レビュー結果

対象PR: #19 〜 #22
レビュー日: 2026-03-08
対象ファイル: `app.py`、`shopee_core.py`

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | `version` 変数の未定義参照（既存動画スキップパス） | バグ | 9 | 7 | **63** |
| 2 | `/regenerate-video` のASIN入力バリデーション欠如 | セキュリティ | 8 | 6 | **48** |
| 3 | `resume_batch` のAPIキー未保存（バッチJSONに含まれない） | 仕様リスク | 7 | 6 | **42** |
| 4 | ファイルシステム復元時の画像フィルタ欠如（非画像ファイル混入） | バグ | 7 | 5 | **35** |
| 5 | `BATCH_STORE` のインメモリ管理（マルチインスタンス非対応） | スケーラビリティ | 9 | 9 | **81** |
| 6 | `/drive-video` プロキシが動画全体をメモリに展開 | パフォーマンス | 6 | 5 | **30** |
| 7 | `/add-images` のASINバリデーション欠如 + SSRF リスク | セキュリティ | 6 | 4 | **24** |
| 8 | `_search_duckduckgo_images` の `ImportError` が無限リトライ | バグ | 5 | 3 | **15** |
| 9 | `process_stream` と `resume_batch` の処理ロジック重複 | 保守性 | 8 | 8 | **64** |
| 10 | ffmpeg `subprocess.run` の戻り値チェックなし | 信頼性 | 5 | 4 | **20** |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 機能性（25点満点）| 20 | batch消失時フォールバック・画像検索・再生成の各機能は設計通り動作する。課題#1のNameErrorバグは2回目以降の処理でのみ発現するため、初回処理では影響なし。PR#19の-700行削除により全体的なコードの見通しが改善された |
| 保守性（20点満点）| 11 | `process_stream` と `resume_batch` の700行超の処理が重複しており、バグ#1が両方に同じ形で存在することがその証拠。app.pyが3,300行超の単一ファイルで、HTMLが2/3を占める。PR#19で大幅削除されたことは評価できる |
| パフォーマンス（15点満点）| 9 | `drive_video_proxy` のメモリ全展開が問題。sync ジェネレータ内でブロッキングAPI呼び出し（120秒超）があり Cloud Run スレッドプールを長時間占有する |

---

## 改善提案トップ3

### 提案1: `version` 変数の未定義バグを即修正

**問題:**
`process_stream`（Step5スキップパス、行 2388〜2404）と `resume_batch`（行 2880〜2889）の両方で、既存動画が見つかった場合に `version` 変数が設定されないまま終了する。その後 `product_state` の構築時に `"selected_version": version if video_record else ""` で参照するため `NameError` が発生する。

```python
# 現状（バグあり）: else ブランチでしか version が定義されない
if existing_video_files:
    video_path = existing_video_files[-1]
    # version が設定されない
    video_record = {"version": video_path.stem, ...}
    yield emit({"type": "step_skip", ...})
else:
    version = "v1"  # ← else ブランチのみ
    ...

# 後続でNameError
product_state = {
    "selected_version": version if video_record else "",  # ← NameError
}
```

**解決策（コード例）:**
```python
if existing_video_files:
    video_path = existing_video_files[-1]
    version = video_path.stem  # この1行を追加
    effect = "zoom"
    model = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"]).get("model", "hailuo")
    video_record = {
        "version": version,
        ...
    }
```

`resume_batch` の同箇所（行 2880〜2889）にも同様の修正が必要。

**実装コスト:** 低（2箇所にそれぞれ1行追加）
**期待効果:** 同一ASINを2回以上処理した場合の500エラーを完全解消

---

### 提案2: ASINバリデーションを共通化してすべてのエンドポイントに適用

**問題:**
`/files/{asin}` エンドポイントにはASIN形式バリデーション（`^[A-Z0-9]{10}$`）が実装されているが、`/regenerate-video`・`/add-images`・`/finalize` には欠如している。ASINを検証せずにファイルシステムパスを構築するとパストラバーサルのリスクがある。

また `/add-images` は外部URLに対してサーバーからHTTPリクエストを発行しており、URLサニタイズがないためSSRF（Server-Side Request Forgery）のリスクもある。

```python
# 現状（危険）
asin = data.get("asin", "")
output_dir = config["output_base"] / asin  # asin未検証
```

**解決策（コード例）:**
```python
# app.py 上部に共通バリデーション関数を追加
def validate_asin(asin: str) -> str:
    if not asin or not re.match(r'^[A-Z0-9]{10}$', asin):
        raise HTTPException(status_code=400, detail="無効なASIN形式です（10文字の英数字）")
    return asin

# 各エンドポイントで使用
@app.post("/regenerate-video")
async def regenerate_video(request: Request):
    data = await request.json()
    asin = validate_asin(data.get("asin", ""))
    ...

# add-images でのURL検証
for url in image_urls:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="無効なURLです")
```

**実装コスト:** 低（関数1つ追加 + 各エンドポイントに1〜2行追加）
**期待効果:** パストラバーサルとSSRFのリスクを排除。セキュリティ要件を一元管理

---

### 提案3: `process_stream` と `resume_batch` の共通処理を関数化

**問題:**
Step1〜7の処理本体が `process_stream` と `resume_batch` の2箇所にほぼ完全に複製されている。PR#19で `restart-from-step` 相当機能が削除されたことで2箇所に減ったが、それでも700行超の重複が残っている。今回のバグ#1が両方に同じ形で存在していることがその証拠。

**解決策（骨格）:**
```python
# 単一商品の処理を yield するジェネレータ関数
def _process_single_product_steps(
    url: str,
    idx: int,
    batch_id: str,
    config: dict,
    rainforest_key: str,
    openai_key: str,
    skip_image_translate: bool,
    emit,  # callable(payload) -> str
) -> Generator:
    """Step1-7の処理本体。process_stream / resume_batch 共通。"""
    ...

# process_stream
def generate():
    for idx, url in enumerate(urls):
        yield from _process_single_product_steps(url, idx, ...)

# resume_batch
def generate():
    for resume_i, url in enumerate(remaining_urls):
        idx = stopped_at_index + resume_i
        yield from _process_single_product_steps(url, idx, ...)
```

**実装コスト:** 高（リファクタリング規模が大きく、テストが必要）
**期待効果:** バグ修正が1箇所で完結。app.pyのコード量が約35%削減

---

## PR別評価詳細

### PR#19: 演出名日本語化・画像検索UI永続化・-700行削除・resume_batchバグ修正

**評価: 良好（ただし課題あり）**

- `EFFECT_LABELS` 辞書の追加とUIへの適用は適切。サーバー側とクライアント側で対応が取れている
- `searchShownAsins = new Set()` の永続化はUX改善として妥当。ページ再読み込みには消えるがセッション中は維持される
- 再生成ステータス表示（`regenStatus` 要素）は適切に実装されている
- `resume_batch` のAPIキー修正: `batch.get("openai_key", "") or os.environ.get(...)` の形は正しい（環境変数フォールバック）。ただしバッチJSONに `openai_key`・`rainforest_key` が保存されていないため、常に `""` → 環境変数フォールバックとなる。これは環境変数設定済み前提なら問題ないが、保存漏れが根本原因として残る
- **問題:** `version` 変数の未定義バグが混入（または既存バグが残存）している

### PR#20: 画像検索をGoogle CSE→Serper.devに切り替え

**評価: 良好**

- `_search_serper_images` の実装は適切。`X-API-KEY` ヘッダーでの認証、`timeout=15`、例外ハンドリング付き
- `min(num, 10)` でSerper.devの上限を守っている
- Serper失敗時のDuckDuckGoフォールバックにより可用性が向上している
- フォールバック構成（Serper → DuckDuckGo）のログ出力が適切
- **問題:** DuckDuckGoライブラリ未インストール時の `ImportError` が `except Exception` に捕捉されてリトライを繰り返す（無駄なsleep）

```python
# 現状（DuckDuckGo未インストール時に無駄なリトライ）
for attempt in range(max_retries + 1):
    try:
        from duckduckgo_search import DDGS  # ImportError が毎回発生
        ...
    except Exception as e:
        time.sleep(2 ** attempt)  # 1秒 + 2秒 待機して最終的に return []

# 修正案
try:
    from duckduckgo_search import DDGS
except ImportError:
    logger.warning("duckduckgo-search が未インストールです")
    return []
```

### PR#21: /add-imagesがbatch_id消失時もダウンロード可能に

**評価: 良好**

- フォールバック設計が安全で適切:
  ```python
  existing_count = (
      len(product.get("image_paths", [])) if product
      else len(list((output_dir / "images").glob("*"))) if (output_dir / "images").exists()
      else 0
  )
  ```
- バッチが消えても画像ダウンロード自体は実行される設計
- `product` が None の場合の分岐も適切に処理されている
- **問題:** ASINバリデーションが欠如。`asin = data.get("asin", "")` の後に形式チェックがない

### PR#22: /regenerate-videoがbatch消失時もファイルシステムから復元

**評価: 概ね良好（要確認事項あり）**

**ファイルシステム復元ロジックの安全性評価:**

```python
# PR#22 の復元ロジック
if product:
    product_image_paths = product.get("image_paths", [])
    existing_videos = product.get("videos", [])
    folder_id = product.get("drive_folder_id", "")
    folder_url = product.get("drive_folder_url", "")
    product_data = product.get("product", {})
else:
    # ファイルシステムから復元
    images_dir = output_dir / "images"
    product_image_paths = sorted([str(p) for p in images_dir.glob("*")]) if images_dir.exists() else []
    existing_videos = [{"version": p.stem} for p in sorted(videos_dir.glob("*.mp4"))]
    folder_id = ""
    folder_url = ""
    product_data = {}
```

**問題点1（バグ）:** `images_dir.glob("*")` は画像以外のファイル（`.DS_Store`、`.gitkeep`、その他）も拾う。非画像ファイルが `generate_video` に渡されると ffmpeg や fal.ai API がエラーになる。

**問題点2（機能）:** `product_data = {}` の場合、`generate_video_kenburns` 内の `product.get("title_en", "Product")` が `"Product"` になり、テロップが空になる。fal.ai での AI 動画生成ならプロンプト（`EFFECT_PROMPTS`）から生成するため影響は少ないが、Ken Burns モードでは品質が低下する。

**ドライブアップロードのフォールバック:**
```python
if folder_id:
    drive_file_url = upload_file_to_drive_folder(video_path, folder_id, config)
else:
    # folder_id なし → 新規フォルダ作成してアップロード
    drive_meta = upload_to_drive(asin, [], [], video_path, config, return_meta=True)
    folder_url = drive_meta.get("folder_url", "")
    ...
```
この2段階フォールバックは設計として正しい。既存フォルダへのアップロード失敗時にも新規フォルダを作成して動画を保存できる。

```python
# 修正案: 拡張子フィルタを追加
VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
product_image_paths = sorted([
    str(p) for p in images_dir.glob("*")
    if p.suffix.lower() in VALID_IMAGE_EXTS
]) if images_dir.exists() else []
```

---

## 潜在的な技術的負債
（今すぐ直さなくてもいいが、将来リスクになりそうな箇所）

### 1. BATCH_STORE のインメモリ管理

`BATCH_STORE = {}` はプロセスメモリ上のグローバル辞書。Cloud Run がスケールアウトして複数インスタンスになった瞬間、インスタンスAで作成したバッチをインスタンスBで参照できなくなる。現在はJSONファイルへの永続化（`save_batch_state`）で部分的に対応しているが、Cloud Run の各インスタンスがマウントするファイルシステムは独立している（共有ストレージなしの場合）。

回避策: `--min-instances=1 --max-instances=1` で単一インスタンス固定（現状の運用形態なら妥当）
根本解決: Firestore または Cloud Storage への外部永続化

### 2. sync ジェネレータ内のブロッキング処理

`process_stream` と `resume_batch` の `generate()` は同期ジェネレータ。内部の `generate_video_ai`（fal.ai、最大120秒超）・`translate_image_text`（OpenAI、45秒/枚）がブロッキング呼び出しであり、Starletteが `run_in_threadpool` でラップするため、処理中はWorkerスレッドを長時間占有する。同時3〜4バッチでスレッドプールが枯渇するリスクがある。

### 3. drive_video_proxy の OOM リスク

動画ファイル（数十〜数百MB）をメモリに全展開してから返している。Cloud Run のデフォルトメモリ（256MB〜512MB）では大きな動画でOOMになりうる。HTTP Range リクエストにも非対応のため、動画シークが不可能。

推奨: `StreamingResponse` によるチャンク転送、またはリダイレクト。

### 4. gspread認証の毎回実行

`write_to_spreadsheet`・`append_video_generation_log`・`fetch_video_generation_history` が各呼び出しで独立してサービスアカウント認証 → gspread接続を実行している。1商品の処理で7回 `write_step_checkpoint` が呼ばれ、認証オーバーヘッドが蓄積する。プロセス内キャッシュ化で改善可能。

### 5. app.py への巨大HTMLインライン埋め込み

`INDEX_HTML`（約600行）がPythonファイルに直接埋め込まれており、HTMLの変更にPythonの再デプロイが必要。Jinja2テンプレートファイル分離または静的ファイル配信への移行を将来検討。

### 6. ffmpeg の戻り値チェック欠如

`generate_video_kenburns` 内の `subprocess.run` に `check=True` がなく、ffmpegがエラーコードを返しても例外が発生しない。生成された動画が破損していても後続のDriveアップロードまで進む可能性がある。

---

## 次のPhaseへの引き継ぎ事項

### 即時対応必須（バグ）

| 優先 | 内容 | 対象ファイル・行 |
|------|------|----------------|
| P0 | 課題#1: `version` 変数未定義バグの修正 | `app.py` L2388〜2404（process_stream）および L2880〜2889（resume_batch）の既存動画スキップパスに `version = video_path.stem` を追加 |

### 短期対応推奨（セキュリティ）

| 優先 | 内容 | 対象ファイル・行 |
|------|------|----------------|
| P1 | 課題#2+#7: ASINバリデーション共通化 | `app.py` `/regenerate-video`、`/add-images` のエンドポイント先頭にバリデーション追加 |
| P1 | 課題#4: 画像拡張子フィルタ追加 | `app.py` L3063 の `glob("*")` を `glob("*.jpg")` 等に変更 |

### 中期対応推奨（品質・保守性）

| 優先 | 内容 |
|------|------|
| P2 | 提案3: `process_stream` / `resume_batch` の処理共通化（リファクタリング） |
| P2 | 課題#6: `drive_video_proxy` のStreaming化またはリダイレクト化 |
| P2 | ffmpeg `returncode` チェックの追加 |
| P3 | DuckDuckGo `ImportError` の個別ハンドリング |

### 長期対応（アーキテクチャ）

- `BATCH_STORE` の外部ストレージ（Firestore/GCS）移行（Cloud Run スケールアウト対応）
- 重い処理（動画生成・画像翻訳）の非同期ジョブ化（Cloud Tasks 等）
- バッチJSONへの `rainforest_key`・`openai_key` 保存（または完全環境変数化の明示）
- `app.py` の HTMLテンプレート分離による保守性向上
