# QA Engineer レビュー結果（PR #19〜#22）

レビュー実施日: 2026-03-08
対象ファイル: app.py / shopee_core.py
対象PR: #19（演出名日本語化・DuckDuckGo・resume_batchバグ修正等）、#20（Serper.dev切り替え）、#21（/add-imagesのbatch消失対応）、#22（/regenerate-videoのbatch消失対応）

---

## テストサマリ

- 静的コードレビューによる課題抽出（実行環境なし）
- 発見バグ: **8件**（Critical 1件、High 3件、Medium 3件、Low 1件）
- テストが不足している領域: 5箇所

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | process-streamでvideo_record = Noneがif/else外に置かれ既存動画スキップ時も上書き | バグ（ロジック） | 9 | 9 | 81 |
| 2 | resume_batchでrainforest_keyがbatch未保存のため環境変数に依存 | バグ（APIキー） | 8 | 8 | 64 |
| 3 | /regenerate-videoのbatch消失時にimages_dir.glob("*")で非画像が混入 | バグ（境界値） | 7 | 7 | 49 |
| 4 | /add-imagesのbatch消失時existing_count算出に非画像ファイルが混入 | バグ（境界値） | 6 | 6 | 36 |
| 5 | searchShownAsins SetがrunBatch()再実行時にリセットされない | バグ（状態管理） | 7 | 5 | 35 |
| 6 | /finalizeがbatch.json消失時に404でファイルシステム復元パスがない | 設計上の欠落 | 6 | 6 | 36 |
| 7 | DuckDuckGoで全試行成功かつraw=[]の場合、raw初期化なしでループ後に参照 | バグ（エッジケース） | 4 | 4 | 16 |
| 8 | SERPER_API_KEY未設定時のエラーメッセージがユーザーに伝わらない | UX | 3 | 3 | 9 |

---

## バグレポート

---

### Bug #1: process-streamで既存動画スキップルートのvideo_recordがNoneに上書きされる

**重大度**: Critical
**再現率**: 毎回（output/{asin}/videos/に既存mp4がある場合）

**環境**: Python 3.x / FastAPI

**再現手順**:
1. 同一ASINを2回以上処理する（1回目でvideos/v1.mp4が生成済み）
2. 2回目の/process-streamで同ASINを処理する
3. Step5でexisting_video_filesにv1.mp4が検出される
4. if existing_video_files: ブロックでvideo_recordが設定される（2392〜2404行）
5. yield emit(step_skip)が発火
6. 2433行の `video_record = None` がif/else外で実行される

**期待される動作**: 既存動画を参照したvideo_recordがproduct_stateの"videos"に含まれる

**実際の動作**: video_record = None（2433行）により上書きされ、product_stateの"videos": []になる。レビュー画面に動画が表示されない。

**該当コード（app.py 2387〜2447行）**:
```python
if existing_video_files:
    ...
    video_record = {          # ← 2392行: skipルートで設定
        "version": video_path.stem, ...
    }
    yield emit({"type": "step_skip", ...})
else:
    effect = "zoom"
    ...

video_record = None           # ← 2433行: if/else外にあるため両ルートで実行される
if video_path and video_path.exists():
    video_record = { ... }    # ← elseルートのみに意図した上書き
```

**根本原因の仮説**:
`video_record = None`（2433行）がif/elseブロック外に置かれている。`else:` ブロックの先頭に移動することで修正できる。

**影響範囲**: 既存動画があるASINの2回目以降の処理。product_stateの"videos"が空になりfinalize時も動画なし扱いになる。

---

### Bug #2: resume_batchでRainforest APIキーがbatchに未保存のため再開失敗リスク

**重大度**: High
**再現率**: 毎回（RAINFOREST_API_KEY環境変数が未設定の環境）

**環境**: Python 3.x / FastAPI

**再現手順**:
1. /process-streamでバッチ開始（rainforest_keyはリクエストボディから取得）
2. 一時停止によりバッチ停止（BATCH_STOREとbatch.jsonに保存されるが、rainforest_keyは未含）
3. /api/batch/{batch_id}/resumeを呼び出す
4. generate()内で `batch.get("rainforest_key", "")` が空文字列になる
5. `or os.environ.get("RAINFOREST_API_KEY", "")` が空なら処理失敗

**期待される動作**: 再開時にも正しいRainforest APIキーでStep1が実行される

**実際の動作**: 環境変数RAINFOREST_API_KEYが設定されている場合は動作するが、フロントエンドからのキー指定が引き継がれない

**該当コード（app.py 2106〜2112行 と 2629行）**:
```python
# process-stream: 保存時にキーが含まれない
BATCH_STORE[batch_id] = {
    "batch_id": batch_id,
    "created_at": now_iso(),
    "urls": urls,
    "results": results,
    "auto_finalize": auto_finalize,
    # rainforest_key は未保存
}

# resume_batch: batchから読もうとするが存在しない
rainforest_key = batch.get("rainforest_key", "") or os.environ.get("RAINFOREST_API_KEY", "")
```

**根本原因の仮説**:
PR#19の「resume_batch APIキーバグ修正」でフォールバック先を`os.environ`に設定したが、根本原因（batch保存時にキーを含める）が未解決。Cloud Run環境変数でRainforest/OpenAIキーを管理している場合は問題ないが、それに依存した設計になっている。

**影響範囲**: バッチ再開機能全体。ローカル開発環境での再開時に問題が顕在化しやすい。

---

### Bug #3: /regenerate-videoでbatch消失時のimage_paths収集に非画像ファイルが混入

**重大度**: High
**再現率**: batch消失時かつimagesディレクトリに.json/.txtが存在する場合

**環境**: Python 3.x / FastAPI

**再現手順**:
1. batch_idがBATCH_STOREにもbatch.jsonにも存在しない状態で/regenerate-videoを呼び出す
2. output/{asin}/images/ に非画像ファイルが存在する
3. `images_dir.glob("*")` がすべてのファイルを返す
4. 非画像パスがgenerate_video()のimage_pathsに渡される

**期待される動作**: 画像ファイル(.jpg/.jpeg/.png)のみが収集される

**実際の動作**: 非画像ファイルも含まれ、fal.aiへのアップロード時にエラーになる可能性がある

**該当コード（app.py 3063行）**:
```python
product_image_paths = sorted([str(p) for p in images_dir.glob("*")]) if images_dir.exists() else []
```

**根本原因の仮説**:
`glob("*")` を拡張子でフィルタする必要がある。

**修正案**:
```python
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
product_image_paths = sorted([
    str(p) for p in images_dir.glob("*")
    if p.suffix.lower() in IMAGE_EXTS
]) if images_dir.exists() else []
```

**影響範囲**: PR#22のbatch消失時復元パス全体。正常フロー（batch存在時）は影響なし。

---

### Bug #4: /add-imagesでbatch消失時のexisting_count算出が非画像ファイルを含む

**重大度**: High
**再現率**: batch消失時かつimagesディレクトリに非画像ファイルが存在する場合

**環境**: Python 3.x / FastAPI

**再現手順**:
1. batch消失状態で/add-imagesを呼び出す
2. output/{asin}/images/ にファイルが3枚（01.jpg, 02.jpg, metadata.json）存在する
3. existing_count = 3（正しくは2）になる
4. start_index=3でdownload_supplemental_imagesが呼ばれる
5. 追加画像が04_sup.jpgになる（正しくは03_sup.jpg）

**期待される動作**: 画像ファイルのみをカウントした値がstart_indexになる

**実際の動作**: 非画像ファイルも含めたファイル数がstart_indexになり、命名連番がズレる

**該当コード（app.py 3279行）**:
```python
existing_count = len(product.get("image_paths", [])) if product else len(list((output_dir / "images").glob("*"))) if (output_dir / "images").exists() else 0
```

**根本原因の仮説**: Bug#3と同根。glob("*")を画像拡張子でフィルタする。

**影響範囲**: PR#21のbatch消失時ダウンロードパス。ダウンロード自体は成功するが連番が不正になる。

---

### Bug #5: searchShownAsins SetがrunBatch()再実行時にリセットされない

**重大度**: Medium
**再現率**: 同一セッション内で2回以上runBatchを実行した場合

**環境**: ブラウザ / JavaScript

**再現手順**:
1. バッチ1を実行。ASINAがimage_shortage=true → searchShownAsinsに追加
2. レビュー画面で画像検索UIが表示される（期待通り）
3. 新しいバッチ2を実行（runBatch()呼び出し）
4. バッチ2ではASINAはimage_shortageなし
5. レビュー画面でASINAの画像検索UIが依然表示される（不正）

**期待される動作**: 新規runBatch時にsearchShownAsinsがリセットされ、image_shortageのある商品のみUI表示

**実際の動作**: 前バッチのASINが残留し不要な画像検索UIが表示される

**該当コード（app.py 710行、999〜1013行）**:
```javascript
const searchShownAsins = new Set();  // グローバル（constのため再代入不可）

async function runBatch(){
  reviewProducts = [];                // リセットあり
  // searchShownAsins.clear() がない ← 欠落
```

**修正案**: `runBatch()`冒頭に `searchShownAsins.clear();` を追加

**影響範囲**: PR#19の「画像検索UI永続化」機能のSSEイベント後の維持は正しく動作しているが、バッチ間のリセットが欠落している。

---

### Bug #6: /finalizeがbatch.json消失時にファイルシステム復元パスを持たない

**重大度**: Medium
**再現率**: batch.jsonが削除された場合（通常運用では低頻度）

**環境**: Python 3.x / FastAPI

**再現手順**:
1. バッチ処理完了後、OUTPUT_BASE/_batches/{batch_id}.jsonを手動削除またはサーバーごと移行
2. フロントエンドでfinalizeBatch()を呼び出す
3. get_batch_or_none()がNoneを返す
4. HTTP 404が返る

**期待される動作**: PR#21/#22と同様にファイルシステムから部分復元して確定処理を実行

**実際の動作**: 404エラーでfinalizeが完全に失敗する

**該当コード（app.py 3182〜3184行）**:
```python
batch = get_batch_or_none(batch_id)
if not batch:
    raise HTTPException(status_code=404, detail="batch_idが見つかりません")
```

**根本原因の仮説**: /regenerate-videoと/add-imagesにはbatch消失時の復元パスが実装されたが、/finalizeには未実装。PR#21/#22の改修思想を/finalizeにも適用すべき。

**影響範囲**: batch.json消失後のfinalize操作全体。get_batch_or_none()のJSON復元が機能している限りは通常発生しない。

---

### Bug #7: DuckDuckGo検索で全試行後rawが空リストのまま参照される

**重大度**: Medium
**再現率**: DuckDuckGo検索がRatelimitなく成功したが0件の場合

**環境**: Python 3.x / shopee_core.py

**再現手順**:
1. SERPER_API_KEY未設定でsearch_google_imagesが呼ばれる
2. _search_duckduckgo_imagesが実行される
3. max_retries=2回の試行で、いずれも`raw = []`（空リストだがbreak条件を満たさない）
4. ループ終了後、rawは[]として定義済みだがforループに到達（問題なし）

**補足**: 厳密にはNameErrorは発生しないが、ループ内で全試行が例外なく成功しても0件の場合、rawが初期化されずにループを抜ける経路が存在する可能性がある。

**該当コード（shopee_core.py 330〜353行）**:
```python
for attempt in range(max_retries + 1):
    try:
        raw = list(ddgs.images(...))
        if raw:
            break
        # raw = [] の場合はbreakしない → 次の試行へ
    except Exception:
        ...
# ループ後: raw = [] は定義済みだが明示的な初期化がない
results = []
for item in raw:    # rawが定義済みなら問題ないが...
```

**修正案**: ループ前に `raw = []` を明示的に初期化してコードの意図を明確化

**影響範囲**: 軽微。DuckDuckGoが0件の場合に正しく空リストが返されることは変わらないが、可読性の問題。

---

### Bug #8: SERPER_API_KEY未設定時のフロントエンドメッセージが不親切

**重大度**: Low
**再現率**: SERPER_API_KEY環境変数が未設定の環境でDuckDuckGoも失敗した場合

**環境**: Python 3.x / FastAPI + ブラウザ

**再現手順**:
1. SERPER_API_KEY未設定 + DuckDuckGoが失敗する環境で検索実行
2. /search-imagesが{"ok":True,"results":[]}を返す
3. フロントエンドが「結果が見つかりませんでした。別のキーワードで試してください」を表示

**期待される動作**: APIキー設定問題である旨を示すメッセージを表示

**実際の動作**: キーワードの問題と誤認させるメッセージが表示される

**影響範囲**: 運用設定ミスの検出困難。DuckDuckGoが機能する間は問題ない。

---

## テスト観点別の検証結果

### batch消失時の各エンドポイントの挙動

| エンドポイント | batch存在 | batch.jsonのみ存在 | 両方消失 |
|---|---|---|---|
| /add-images | 正常（Bug#4あり） | 正常（JSON復元、Bug#4あり） | ファイルシステム復元で動作（Bug#4あり） |
| /regenerate-video | 正常 | 正常（JSON復元） | ファイルシステム復元で動作（Bug#3あり） |
| /finalize | 正常 | 正常（JSON復元） | 404エラー（Bug#6） |

### Serper.dev → DuckDuckGo フォールバック

| 状況 | 挙動 |
|---|---|
| SERPER_API_KEY設定済み、結果あり | Serperのみで返す（正常） |
| SERPER_API_KEY設定済み、0件 | DuckDuckGoにフォールバック（正常） |
| SERPER_API_KEY設定済み、HTTP 429 | DuckDuckGoにフォールバック（正常） |
| SERPER_API_KEY未設定 | logWarningしてDuckDuckGoへ（正常動作だが設定ミスが不透明、Bug#8） |
| 両方0件 | {"results":[]}を返す（フロントは「結果なし」表示で正常） |
| 両方失敗 | {"results":[]}を返す（フロントは「結果なし」表示、Bug#8） |

### searchShownAsins Setの状態管理

| タイミング | 動作 |
|---|---|
| product_doneイベント（image_shortage=true） | searchShownAsins.add() 実行（正常） |
| all_doneイベント | forEach+add() で再構築（正常） |
| renderReview()呼び出し | p.image_shortage時にadd()（正常、永続化） |
| 再生成後のrenderReview() | 維持される（正常） |
| runBatch()再実行 | **リセットされない（Bug#5）** |

### ステップ再開削除後の関連機能確認

- process-streamのSSEストリームは正常動作
- resume_batchエンドポイントはstep1から全処理を再実行（途中ステップ再開は削除済み）
- フロントのresumeBatch()関数は残存しており、batch_stoppedイベント時のボタン表示も正常
- resumeDiv挿入先の`progressPanel`（app.py 1174行）は実際のHTMLに存在しない（`productPanel`が正しい）
  - これはbatch_stoppedが発火した場合に再開バーが表示されないUI不具合

### resume_batchのAPIキー修正後の動作

- 環境変数RAINFOREST_API_KEY/OPENAI_API_KEYが設定されている場合: 正常動作
- 環境変数が未設定でbatch.jsonにもキーがない場合: rainforest_key=""でStep1失敗（Bug#2参照）
- Cloud Run環境での利用を想定しているため、実際の運用では環境変数設定で問題ないが、ドキュメント化が必要

### 画像アスペクト比エラー時の動画生成失敗ハンドリング

- fal.aiからのエラーは一般Exceptionとして捕捉されHTTP 500を返す（app.py 3093〜3095行）
- フロントエンドでstatusElにエラーメッセージ表示（app.py 1433〜1437行）
- 現状のエラーメッセージ「動画の再生成に失敗しました。しばらくしてから再度お試しください」はアスペクト比問題を示していない
- 改善推奨: fal.aiのエラー文字列に"aspect"が含まれる場合に専用メッセージを返す

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 機能性（25点満点）| 16 | Bug#1（video_record上書き）はCriticalでレビュー画面に動画表示不可となる重大問題。batch消失対応設計は良好 |
| 信頼性（25点満点）| 16 | get_batch_or_noneのJSON復元は堅牢。resume_batchのAPIキー問題は環境変数依存の前提なら許容範囲 |
| セキュリティ（25点満点）| 22 | ASINバリデーション、パストラバーサル防止、セキュリティヘッダーミドルウェア実装済み。高評価 |
| 保守性（25点満点）| 17 | process-streamとresume_batchで同一ステップ処理を重複実装（DRY違反）。共通関数化推奨 |
| **合計** | **71/100** | |

---

## 次のPhaseへの引き継ぎ事項

### 即座に修正が必要（Critical/High）

**1. Bug#1（app.py 2433行付近）**: `video_record = None` をelseブロック内に移動
```python
else:
    video_record = None  # ← elseブロック先頭に移動
    effect = "zoom"
    model = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"]).get("model", "hailuo")
    version = "v1"
    ...
```

**2. Bug#3（app.py 3063行）**: 画像拡張子フィルタを追加
```python
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
product_image_paths = sorted([
    str(p) for p in images_dir.glob("*")
    if p.suffix.lower() in _IMAGE_EXTS
]) if images_dir.exists() else []
```

**3. Bug#4（app.py 3279行）**: existing_count算出を画像のみに限定
```python
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
existing_count = (
    len(product.get("image_paths", [])) if product
    else len([p for p in (output_dir / "images").glob("*") if p.suffix.lower() in _IMAGE_EXTS])
    if (output_dir / "images").exists() else 0
)
```

**4. resumeDivの挿入先（app.py 1174行）**: `progressPanel` → `productPanel` に修正
```javascript
const panel = document.getElementById('productPanel');
```

### 優先度中の修正

**5. Bug#5（app.py 999行周辺）**: runBatch()冒頭にリセット追加
```javascript
async function runBatch(){
  searchShownAsins.clear();  // ← 追加
  ...
}
```

**6. Bug#7（shopee_core.py 329行）**: rawの明示的初期化
```python
raw = []
for attempt in range(max_retries + 1):
    ...
```

### ドキュメント化推奨

- SERPER_API_KEY未設定時の動作（DuckDuckGo自動フォールバック）をSETUP.mdに記載
- batch.jsonの保存場所（OUTPUT_BASE/_batches/）とライフサイクルの明記
- 環境変数でのRainforest/OpenAI APIキー管理が再開機能の前提条件であることの明記
