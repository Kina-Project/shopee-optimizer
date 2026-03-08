# QA Engineer レビュー・テスト結果

**対象ファイル:**
- `app.py`（全エンドポイント、SSEストリーミング、バッチ処理）
- `shopee_core.py`（ビジネスロジック）

**レビュー実施日:** 2026-03-08
**レビュアー:** QAエンジニア（シニア・8年）
**レビュー形式:** 静的コード解析（Phase 1 課題抽出）

---

## テストサマリ

| 項目 | 状態 |
|------|------|
| ユニットテスト | 存在しない（0件） |
| 統合テスト | 存在しない（0件） |
| E2Eテスト | 存在しない（0件） |
| 手動テストスクリプト | 5件（動画生成プロンプト比較・画像翻訳の実機確認のみ） |
| テストカバレッジ | 推定 0% |

> test_*.py が5ファイル存在するが、いずれもpytestではなく手動実行スクリプト（fal.ai実機呼び出し、実際のAPIキー必要）。shopee_core.pyやapp.pyのビジネスロジックをテストするものは皆無。

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重大度 | 影響範囲 | 優先度スコア |
|---|------|----------|--------|----------|------------|
| 1 | BATCH_STOREがインメモリ辞書。Cloud Run複数インスタンス間でバッチ状態が不整合になる | 競合状態 | CRITICAL | 10 | 100 |
| 2 | process-streamのgenerate()が同期ジェネレータで外部API呼び出しをブロッキング実行。FastAPIイベントループを占有し他リクエストが処理不能 | 並行アクセス | CRITICAL | 9 | 90 |
| 3 | BATCH_STOREへの同時読み書きに排他制御（ロック）がない。regenerate/finalize/pause/resumeの同時呼び出しでデータ競合 | 競合状態 | HIGH | 8 | 64 |
| 4 | 認証・認可が完全に欠如。URLを知る誰でも全エンドポイントにアクセス・全バッチ情報を閲覧可能 | セキュリティ | HIGH | 8 | 64 |
| 5 | generate_video_aiのdurationパラメータが常に無視される。FAL_MODELSの固定値が優先され「10秒のみ」問題の根本原因（shopee_core.py L771-775） | 機能バグ | HIGH | 7 | 56 |
| 6 | 同一ASINの並列処理：resume_batchとrestart_from_stepを同時実行すると、product_stateが双方から書き換えられ、最終的にどちらの状態が保存されるか不定 | 競合状態 | HIGH | 7 | 49 |
| 7 | バッチ途中停止→再開（resume_batch）が全ステップを最初から実行し直す設計。既存成果物（ローカル画像・Drive保存済みデータ）を再利用しない | バッチ信頼性 | HIGH | 7 | 49 |
| 8 | download_imagesで1枚でもresp.raise_for_statusが失敗すると全画像ダウンロードが中断。部分的成功を許容しない | エラーパス | HIGH | 7 | 49 |
| 9 | fal_client.subscribeにタイムアウト設定なし。fal.aiが無応答の場合、SSEストリームが無限ブロック（shopee_core.py L780） | 異常系 | HIGH | 6 | 42 |
| 10 | ffmpegのsubprocess.run(timeout=60)でTimeoutExpiredが捕捉されていない。タイムアウト時にAPIサーバーが異常終了する可能性 | 異常系 | HIGH | 6 | 42 |
| 11 | 一時停止（pause_batch）とresume_batchのレースコンディション：pause後・resume受付前にBATCH_PAUSE_REQUESTSをpopするタイミングによっては一時停止が無効化される | 競合状態 | MEDIUM | 6 | 36 |
| 12 | write_to_spreadsheetで全行スキャン（col_values(3)）し最初のASIN一致のみ更新。同一ASINが複数行あると古い行が取り残される（データ整合性問題） | データ整合性 | MEDIUM | 6 | 36 |
| 13 | restart_from_stepのStep 6で upload_to_drive が全ファイルを再アップロード。既にDriveに保存済みの画像が重複アップロードされる | データ整合性 | MEDIUM | 5 | 30 |
| 14 | translate_textにリトライ機構なし。GoogleTranslatorのレート制限・一時エラーでそのまま原文が返却されるが、ユーザーへの通知がない | エラーパス | MEDIUM | 5 | 25 |
| 15 | extract_asinが大文字ASIN（`[A-Z0-9]{10}`）のみにマッチ。小文字を含むURLをハードコードすると`ValueError: ASINが見つかりません`が発生 | エッジケース | MEDIUM | 5 | 25 |
| 16 | fetch_amazon_productでcredits_remainingが0の場合QuotaExhaustedErrorを発生させるが、credits_remainingが"-1"など負数文字列の場合にも対応できているか（int変換後の境界値） | エッジケース | MEDIUM | 5 | 25 |
| 17 | save_batch_stateがbatch["results"]に巨大なbase64画像パスを含む可能性があり、JSONファイルが数十MB規模になりうる。Cloud RunのCloud Storageへの書き込みではなくローカルFS依存 | バッチ信頼性 | MEDIUM | 5 | 25 |
| 18 | generate_video_kenburns内のffmpegコマンドでreturncode未確認。ffmpegが失敗（終了コード1）しても次の処理へ進み、破損した動画が生成される | エラーパス | MEDIUM | 5 | 25 |
| 19 | _merge_bgmのffprobe失敗時にvid_duration=10.0をデフォルト使用するが、float変換の失敗パスは`except (ValueError, AttributeError)`のみ。ffprobeが存在しない場合（FileNotFoundError）は未捕捉 | エラーパス | MEDIUM | 4 | 20 |
| 20 | generate_video_kenburnsで`/tmp/shopee_gen_*`の一時ディレクトリが例外時にクリーンアップされない（finally節がない） | リソースリーク | MEDIUM | 4 | 20 |
| 21 | add-imagesエンドポイントでimage_urlsの上限チェックがない。100件を超えるURLを送信すると長時間ブロッキング処理が発生 | エッジケース | MEDIUM | 4 | 20 |
| 22 | api_search_imagesのqueryパラメータに長さ制限・サニタイズがない。SQLi的なクエリやコントロール文字が外部API（Google CSE/DuckDuckGo）へ送信される | セキュリティ | MEDIUM | 4 | 20 |
| 23 | history_pageでGoogleシートAPIを同期呼び出しで最大2000行取得。シートが大きい場合のレスポンスタイムが数十秒になりUXを著しく損なう | パフォーマンス | MEDIUM | 5 | 25 |
| 24 | gspread認証が関数呼び出しごとに毎回SACredentials→gc = gspread.authorize(creds)を実行。1バッチで7ステップ×複数商品分の認証リクエストが発生 | パフォーマンス | MEDIUM | 4 | 20 |
| 25 | upload_translated_images_to_driveが毎回"images_en"サブフォルダを新規作成しようとする。再実行時に同名フォルダが複数生成される | データ整合性 | MEDIUM | 4 | 20 |
| 26 | load_batch_storeが起動時に最大200件のJSONを一括メモリ読み込み。各JSONに長いimage_pathsリストが含まれると起動時メモリが急増 | リソース | LOW | 3 | 15 |
| 27 | CSRFトークンの実装がなく、全POSTエンドポイントにCSRF攻撃が可能（内部ツールとしても認証なし環境では影響あり） | セキュリティ | LOW | 3 | 12 |
| 28 | parse_tsが複数フォーマットを試行するが、ISO形式（タイムゾーン付き）のフォールバック処理でエラーが発生する場合にdatetime.minを返す。ソート時に古いデータが意図せず末尾に配置される | エッジケース | LOW | 3 | 9 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 機能性（25点満点） | 14 | 基本的な7ステップパイプラインは動作する。ただし動画durationバグ（#5）・エラー時のindex不整合・ffmpegエラー無視（#18）など重大な機能欠陥が複数存在する |
| 信頼性（20点満点） | 7 | 6つの外部APIに強依存しながらリトライ機構が一部しか存在しない。fal.ai無限待機（#9）、ffmpegタイムアウト未捕捉（#10）、翻訳エラー通知なし（#14）など、途中停止・データ欠損リスクが高い |
| セキュリティ（15点満点） | 4 | 認証・認可が完全欠如（#4）、CSRF未対策（#27）。パストラバーサルはis_relative_toで防止済みだが、APIキーをリクエストボディ受け付け（ログリスク）など未解消問題が残る |
| パフォーマンス（15点満点） | 5 | 同期ブロッキング処理（#2）がFastAPIの非同期性を完全に無効化。gspread認証キャッシュなし（#24）、履歴ページの全件取得（#23）が重なり、実環境でのスループットが極めて低い |
| 保守性（15点満点） | 5 | HTMLをapp.pyにインライン（約1500行）、resume_batchとprocess_streamの処理が500行以上重複。コードが長大で変更時の回帰リスクが高い |
| テスタビリティ（10点満点） | 1 | ユニット・統合・E2Eテストが完全にゼロ。外部依存が依存注入されておらずモック化が困難。テストフレームワーク（pytest）すら未導入 |
| **合計（100点満点）** | **36** | |

---

## 8観点別 詳細分析

### 1. テストカバレッジ

**現状: CRITICAL（推定カバレッジ 0%）**

`test_*.py`ファイルが5件存在するが、内容はすべてfal.ai APIへの実機呼び出しスクリプトであり、pytestフレームワークを使用していない。`shopee_core.py`・`app.py`のビジネスロジックに対するテストは皆無。

テストが存在しない主要コンポーネント:
- `extract_asin`（ASINパース）
- `parse_drive_folder_id`
- `can_restart_from`
- `translate_text` / `translate_product`
- `write_to_spreadsheet`（スプレッドシート書き込みロジック）
- `generate_video_kenburns`（ffmpegパイプライン）
- `_drive_api_retry`（リトライロジック）
- SSEストリームの全エンドポイント
- バッチ状態の永続化・復元（`save_batch_state` / `get_batch_or_none`）

**推奨テストケース（優先順）:**

```python
# extract_asin の境界値テスト例
def test_extract_asin_standard_dp_url():
    assert extract_asin("https://www.amazon.co.jp/dp/B0XXXXXXXXX") == "B0XXXXXXXXX"

def test_extract_asin_empty_raises():
    with pytest.raises(ValueError):
        extract_asin("")

def test_extract_asin_invalid_url_raises():
    with pytest.raises(ValueError):
        extract_asin("https://example.com/product/invalid")

def test_extract_asin_lowercase_asin():
    # 小文字ASINが含まれるURL（現在のコードでは拒否される）
    with pytest.raises(ValueError):
        extract_asin("https://www.amazon.co.jp/dp/b0xxxxxxxxx")
```

---

### 2. エッジケース

**空入力・NULL系:**

| テストケース | 対象関数 | 現在の挙動 | 問題の有無 |
|---|---|---|---|
| `urls=[]`でprocess-stream呼び出し | process_stream | SSEで「URLが必要です」を返す | 正常 |
| `urls=[""]`（空文字URL）| process_stream | `extract_asin`でValueError→continueで次へ | 正常だが空ループ |
| `image_urls=[]`のAmazon商品（画像なし）| fetch_amazon_product→download_images | 空リスト→download_images実行→0件 | `generate_video`がimage_pathsで`image_paths[0]`参照時にIndexError発生リスク |
| `title_ja=""`（空タイトル）| translate_product | translate_textが`""`→空文字返却 | 動画テロップが空になる。Silent fail |
| `product={}` | translate_product | `product["title_ja"]`でKeyError | **未捕捉のKeyError。バグ** |

**大量入力系:**

| テストケース | 期待動作 | 実装の問題 |
|---|---|---|
| `urls`に11件（MAX超過） | 400エラー | 正常に検知される |
| 1商品の画像が20枚（Amazon上限超え） | download_imagesが全件ダウンロード | メモリ・ディスク使用量に上限なし |
| 100件の画像URLをadd-imagesで送信 | 適切なエラーか制限 | 上限チェックなし。長時間ブロッキング |
| バッチJSON が100MBを超える場合 | ファイル書き込み成功 | Cloud Runのメモリ上限に依存 |

**タイムアウト系:**

| シナリオ | 現在のタイムアウト設定 | 未捕捉例外 |
|---|---|---|
| Rainforest API | `timeout=30` | 正常。TimeoutErrorはraise_for_statusで捕捉 |
| fal.ai AI動画生成 | **なし** | `fal_client.subscribe`が無限ブロック |
| ffmpeg動画処理 | `timeout=60` | `TimeoutExpired`が未捕捉 |
| Google Drive API | `_drive_api_retry`内でデフォルト | retry内でraise |
| gspread API | デフォルト（無制限） | ネットワーク断で無限待機の可能性 |

---

### 3. エラーパス

**API失敗時の挙動:**

| APIエラー | 発生箇所 | 挙動 | 問題 |
|---|---|---|---|
| Rainforest API 403 | Step 1 | HTTPErrorがraiseされ`continue` | 問題なし |
| Rainforest クレジット0 | fetch_amazon_product | QuotaExhaustedError→バッチ停止 | 問題なし |
| Google Translate ネットワーク断 | Step 3 | 原文をそのままfallback返却 | ユーザーへの通知なし。英語タイトルが実は日本語のまま |
| OpenAI insufficiant_quota | Step 4 | QuotaExhaustedError→バッチ停止 | 問題なし |
| fal.ai タイムアウト | Step 5 | 無限待機 | **CRITICAL: サーバーがハング** |
| Google Drive 403 権限エラー | Step 6 | エラーメッセージを返す | 問題なし |
| gspread 書き込みエラー | Step 7 | step_warnを発行してcontinue | 問題なし（警告扱い） |

**Drive接続エラー時の詳細分析:**

```
シナリオ: DRIVE_REFRESH_TOKEN が期限切れの場合
1. ensure_drive_parent_folder_config() でDrive認証チェック（process-stream開始前）
2. _get_drive_credentials() が試みる: OAuth2(失敗) → ADC(失敗) → gcloud CLI(失敗)
3. ensure_drive_folder() で RuntimeError
4. process-stream開始時（バッチ作成前）にエラー返却 → バッチIDが発行されない

問題: バッチIDが発行されない場合、SSE接続がエラーで終わるが、
      クライアント側がbatch_idを保持していないため、エラー詳細の確認手段がない
```

**シート書き込み失敗時の挙動:**

`write_to_spreadsheet`は各ステップ完了時と最終Step 7で計8回呼ばれる。Step 7以外はエラーを`step_warn`として通知しバッチ継続する。しかし：
- Step 7でのシート書き込みエラーも`step_warn`扱いで継続される（ステータスが「完了」にならない）
- バッチ状態ファイルには「完了」と記録されるが、スプレッドシートには記録されないためデータ不整合が生じる

---

### 4. データ整合性

**スプレッドシートの行更新 vs 新規追加:**

`write_to_spreadsheet`（shopee_core.py L1212-1250）の問題:

```python
existing = ws.col_values(3)  # C列（ASIN列）の全値を取得
row_idx = None
for i, val in enumerate(existing):
    if val == asin:
        row_idx = i + 1
        break  # 最初の一致のみ更新
```

問題1: 同一ASINが複数行存在する場合、最初の行のみ更新され古いデータが残る
問題2: `col_values(3)`の返却値はヘッダー行込みのため、index計算が1行ズレる可能性
問題3: `ws.update()`と`ws.insert_row()`が非原子的に実行される。ネットワーク断が挟まると、「行は更新されたが、ログ行は追加されていない」状態が生まれる

**バッチ状態の整合性:**

```
BATCH_STOREとJSONファイルの2重管理による不整合リスク:

1. save_batch_state() でJSONに書き込み
2. インスタンス再起動でBATCH_STOREがクリア
3. get_batch_or_none() でJSONから復元（BATCH_STOREに再追加）

問題: 複数インスタンスが同一batch_idのJSONを同時更新した場合、
      後書きが勝つ（Last-Write-Wins）。処理中の状態が失われる。
```

**`results`リストへの商品追加と参照の不整合:**

`process-stream`では以下のようにresultsとBATCH_STOREが別々に管理されている:

```python
results = []
BATCH_STORE[batch_id] = {..., "results": results}
# ...ループ内で
results.append(product_state)  # resultsに追加
save_batch_state(batch_id)      # BATCH_STOREから読んで保存
```

`results`は`list`オブジェクトで`BATCH_STORE[batch_id]["results"]`と同一参照。問題はないが、`resume_batch`では:
```python
existing_results = batch.get("results", [])
results = list(existing_results)  # コピーを作成
```
コピーを作成して`BATCH_STORE[batch_id]["results"] = results`（L3581）で再代入する。この間にregenerate-videoなどが`batch["results"]`を参照すると古いデータを参照する。

---

### 5. 競合状態

**Bug #1: BATCH_STOREへの無保護な同時アクセス**

**重大度:** CRITICAL
**再現率:** Cloud Runマルチインスタンス環境では必発

シナリオ:
```
インスタンスA: process-stream でbatch_idを生成 → BATCH_STORE[batch_id] = {...}
インスタンスB: 同じbatch_idへのregenerate-video → BATCH_STORE.get(batch_id) → None
インスタンスB: JSONファイルから復元 → 古い状態で上書き
```

現在の排他制御: **なし**
必要な対策: asyncio.Lockまたは外部ストレージ（Firestore等）への移行

**Bug #2: 同一ASINの並列処理**

`find_batch_for_asin`は同一ASINを複数バッチで処理可能な設計（最新バッチを返す）。

シナリオ:
1. バッチAでASIN "B0XXXXXXXXX" の処理中
2. 同時にバッチBでも同ASINをバッチ処理開始
3. 同じ `OUTPUT_BASE / asin / images/` ディレクトリへ並列書き込み
4. `01.jpg`, `02.jpg` が互いに上書きされる

ローカルファイルシステムへの無保護な並列書き込みは発生するが、ファイルロック機構がない。

**Bug #3: pause_batch → resume_batch のレースコンディション**

```python
# pause_batch:
BATCH_PAUSE_REQUESTS[batch_id] = True

# process-streamのループ内（generate()の同期関数内）:
if BATCH_PAUSE_REQUESTS.pop(batch_id, False):
    ...
    return  # ジェネレータ終了

# resume_batch:
batch.pop("stopped_reason", None)  # 停止状態クリア
# → 新しいgenerate()を開始
```

問題: pause後にresume_batchが呼ばれ、新しいgenerate()が開始した後に、元のgenerate()がpopを検出してreturnする。`stopped_reason`が既にクリアされているため、バッチが完全に停止できずゾンビ状態になる可能性がある。

**Bug #4: restart_from_stepとregenerate-videoの同時実行**

両エンドポイントとも`product_state["videos"]`に対して`append`操作を行う。同時実行時にバージョン番号（`next_video_version`）が重複する可能性がある:

```python
# restart_from_step:
existing_videos = product_state.get("videos", [])
version = next_video_version(existing_videos)  # "v2"

# 同時にregenerate-video:
product = next((p for p in batch["results"] if p.get("asin") == asin), None)
version = next_video_version(product.get("videos", []))  # "v2"（同じ）

# 両者が "v2.mp4" を生成・保存 → 後書きが勝つ
```

---

### 6. バッチ処理の信頼性

**途中停止→再開のデータ復元**

`resume_batch`の実装（app.py L3193-3594）には重大な設計上の問題がある。

問題1: 既存成果物を再利用しない
```python
# resume_batch のStep 2（画像ダウンロード）:
image_paths = download_images(product["image_urls"], images_dir)
# → 停止前にダウンロード済みの画像があっても、再ダウンロードする
# → Amazon画像URLがCDNキャッシュ切れで変更されている場合に失敗
```

問題2: APIキーがconfig経由でのみ取得される
```python
openai_key = config.get("openai_key", "")    # 常に ""
rainforest_key = config.get("rainforest_key", "")  # 常に ""
```
`get_config()`の返却値に`openai_key`や`rainforest_key`は含まれない（環境変数キー名が違う）。resume_batchでは、process_streamと異なりリクエストボディからAPIキーを取得する機構がない。環境変数（`OPENAI_API_KEY`、`RAINFOREST_API_KEY`）が設定されていなければ、resumeで翻訳・商品取得ステップが無音で失敗する。

問題3: stopped_at_indexが「商品の開始インデックス」を指すが、再開は常にStep 1から
```python
remaining_urls = urls[stopped_at_index:]
# stopped_at_stepが保存されているが、resume_batchではstep単位での再開は行わない
# → 途中のステップで停止した場合、そのステップから再開できない
```

`restart_from_step`エンドポイントが存在するが、resume_batchとは独立したコードパスであり、`resume_batch`から自動で呼び出されない。

**バッチ状態ファイルの信頼性:**

| シナリオ | 挙動 | 問題 |
|---|---|---|
| JSON書き込み中にCloud Runコンテナがシグナル受信 | ファイルが破損する可能性 | write_textはアトミックではない |
| JSONの最大サイズ超過（例: 大量画像パス） | IOError | 未捕捉 |
| BATCH_STATE_DIRのディスク容量不足 | OSError | 未捕捉 |
| 200件を超える古いバッチ | load_batch_storeで上限200件を超えるものは読み込まれない | 古いバッチへのアクセスが404になる |

---

### 7. 回帰リスク

**コードの重複がもたらす回帰リスク:**

以下の3つのコードパスがほぼ同一の7ステップ処理を実装している:
1. `process_stream`のgenerate()（app.py L2702-3170: 約470行）
2. `resume_batch`のgenerate()（app.py L3218-3588: 約370行）
3. `restart_from_step`のgenerate()（app.py L2343-2625: 約280行）

合計約1120行の重複コード。一方を修正しても他方への反映が漏れるリスクが高い。

実際の例として、process_streamとresume_batchの差異:
- Step 2のエラー時メッセージが異なる（「画像のダウンロードに失敗しました。」vs「画像ダウンロードに失敗しました。」）
- Step 1後のDriveフォルダ事前作成がresume_batchでは例外を無視してのみ続行（`logger.warning`のみ）
- resume_batchではStep 5のvideo_recordに`drive_file_url`や`source_image_path`フィールドが存在しない（process_streamには存在）

**新機能追加による既存機能への影響リスク:**

| 変更 | 影響する既存機能 |
|---|---|
| step追加（Step 8を追加）| STEP_NAMES辞書、can_restart_from、restartable_steps、3つのgenerate()全て |
| 新effectの追加 | EFFECT_PROMPTS、FAL_MODELS、regenerate-videoのバリデーション |
| gspreadのシート列変更 | write_to_spreadsheet、fetch_product_sheet_history、append_video_generation_log |
| バッチ状態フィールド追加 | save_batch_state、get_batch_or_none、3つのproduct_state構築箇所 |

---

### 8. ユーザーシナリオテスト

#### Beginnerペルソナシナリオ

**シナリオ A: 初回正常系（Amazon URL 1件入力）**

```
1. TOP画面でAmazon URL 1件入力
2. Rainforest APIキーを入力
3. 「最適化開始」ボタンをクリック
4. SSEで進捗を確認
5. 全ステップ完了後、レビュー画面で動画を確認
6. 「確定」ボタンを押す
```

期待動作: 正常完了、スプレッドシートに1件記録
リスク: Step 3（翻訳）がサイレントfallbackで原文返却されても気づけない

**シナリオ B: 誤ったURLを入力（異常系）**

```
1. 「https://www.amazon.co.jp/」（ASINなし）を入力して実行
```

実際の動作: SSEでerrorイベント（ASINが見つかりません）を返し`continue`で次URLへ
問題: バッチは継続されるが、エラーになった商品がresultsに追加されないため、後から再試行する手段がない（batch_idが発行された後のエラーのため）

**シナリオ C: ネットワーク断（接続が途中で切れる）**

```
1. 処理中にSSE接続が切断
2. ページをリロード
```

期待動作: バッチIDを保持して処理状態を再確認できる
問題: SSEは一方向通信のため、接続断後にサーバー側のgenerate()は継続実行される。クライアントがbatch_idを保持していなければ、進行中のバッチを参照する手段がない（履歴ページでは確認可能）

#### Poweruserペルソナシナリオ

**シナリオ D: 10件URL一括処理中に一時停止→再開**

```
1. 10件URLで処理開始
2. 5件目の処理中にpauseボタンをクリック
3. 5件目が完了次第、一時停止
4. resumeボタンをクリック
```

期待動作: 6件目から再開
実際のリスク: resume_batchは環境変数からAPIキーを取得する。process_streamはリクエストボディからAPIキーを取得していた場合（環境変数未設定）、resumeでAPIキーがemptyになり全ステップが失敗する（#7の問題）

**シナリオ E: 同一ASINを別タブで同時に処理**

```
タブA: ASIN "B0XXXXXXXXX" の処理実行
タブB: 同ASIN "B0XXXXXXXXX" の処理実行
```

実際の動作: 両方が`output/B0XXXXXXXXX/images/`に書き込み競合
問題: ファイルが互いに上書きされ、どちらのバッチも破損した成果物を持つ

**シナリオ F: 動画再生成を短時間で複数回実行**

```
1. regenerate-videoを立て続けに3回実行
2. それぞれv2, v3, v4が生成されるはず
```

実際のリスク: 排他制御なしのため`next_video_version`が重複バージョンを返し、`v2.mp4`が複数回生成・上書きされる可能性

#### Enterpriseペルソナシナリオ

**シナリオ G: 監査証跡の確認**

```
1. /history ページで過去の処理履歴を確認
```

問題: gspreadの全件取得（最大2000行）が同期的に実行されるため、シートが大きい場合にレスポンスが30秒以上になる。Cloud Runのリクエストタイムアウト（デフォルト60秒）に引っかかるリスクがある

**シナリオ H: 権限外ユーザーによるアクセス試行**

```
1. URLを知っている外部ユーザーが /batches にアクセス
2. 全バッチ一覧（ASIN・商品URL・処理状態）が取得できる
3. /batch/{batch_id} で各バッチの詳細（商品情報・Drive URL）が取得できる
4. /finalize でバッチを誤確定できる
```

問題: 認証なしで全操作が可能。内部ツールとはいえ、Cloud RunのURLが外部から到達可能な場合、データ漏洩・誤操作リスクがある

---

## バグレポート

### Bug #1: fal.ai タイムアウト未設定による無限ハング

**重大度:** HIGH
**再現率:** fal.aiサービス高負荷時・ネットワーク問題発生時

**環境:** Cloud Run / ローカル環境共通

**再現手順:**
1. fal.aiのサービスが応答しない状態でprocess-streamを実行
2. Step 5（動画生成）でfal_client.subscribeが呼ばれる
3. 応答がないまま無限待機

**期待される動作:** 一定時間後にタイムアウトエラーを返し、次のステップへ

**実際の動作:** SSEコネクションが永続的にハングし、他のリクエストも処理不能になる

**根本原因の仮説:**
```python
# shopee_core.py L780
result = fal_client.subscribe(
    model_config["model_id"],
    arguments=arguments,
    with_logs=True,
    # timeout パラメータなし
)
```
fal_clientのsubscribeはデフォルトタイムアウトが設定されていない。

**影響範囲:** 全ての動画生成処理（通常フロー・再生成・再開）

---

### Bug #2: resume_batch でAPIキーが空になる

**重大度:** HIGH
**再現率:** 毎回（環境変数未設定時）

**環境:** Rainforest APIキーをリクエストボディで渡している環境

**再現手順:**
1. process_streamに `{"rainforest_key": "xxx", "urls": [...]}` でリクエスト
2. 処理中にpauseし、resume_batchを呼ぶ

**期待される動作:** 一時停止前と同じAPIキーで処理継続

**実際の動作:** 環境変数`RAINFOREST_API_KEY`が未設定の場合、rainforest_keyが空文字になりStep 1で失敗

**根本原因:**
```python
# app.py L3221-3223
openai_key = config.get("openai_key", "")         # 常に ""（get_configに含まれない）
rainforest_key = config.get("rainforest_key", "") # 常に ""（get_configに含まれない）
```
`get_config()`は`openai_key`・`rainforest_key`を返さない設計。resume時にリクエストボディからキーを引き継ぐ実装がない。

**影響範囲:** バッチの一時停止→再開機能

---

### Bug #3: translate_productでproduct["title_ja"]のKeyError

**重大度:** MEDIUM
**再現率:** 特定条件のみ（product辞書の構造が想定外の場合）

**再現手順:**
1. `translate_product({})`を呼び出す（または`title_ja`キーがないproductを渡す）

**実際の動作:** `KeyError: 'title_ja'`が発生

**根本原因:**
```python
# shopee_core.py L443
def translate_product(product):
    title_en = translate_text(product["title_ja"])  # キーがないとKeyError
```
`product.get("title_ja", "")`であるべきところが`product["title_ja"]`になっている。

**影響範囲:** process_streamのStep 3、resume_batchのStep 3、restart_from_stepのStep 3

---

### Bug #4: upload_translated_images_to_driveが毎回images_enフォルダを新規作成

**重大度:** MEDIUM
**再現率:** restart_from_step（Step 4以降から再開）実行時に必発

**再現手順:**
1. バッチを通常実行（images_enフォルダがDriveに作成される）
2. restart_from_step（from_step=4）を実行

**実際の動作:** Driveに`images_en`フォルダが複数作成される

**根本原因:**
```python
# shopee_core.py L1074-1081
def upload_translated_images_to_drive(drive, translated_paths, folder_id):
    en_folder_meta = {
        "name": "images_en",
        # 既存フォルダの確認なし。常に新規作成
        ...
    }
    en_folder = _drive_api_retry(_do_create_en_folder)
```
`_find_existing_folder`を呼ばずに常に新規作成する。

**影響範囲:** データ整合性。Driveに重複フォルダが蓄積される

---

## テストが不足している領域

### 優先度 CRITICAL（テスト基盤が存在しない）
1. **全ビジネスロジック関数のユニットテスト**（shopee_core.py）
   - `extract_asin`: 10種類以上のURLパターン
   - `translate_text`: 空文字、超長文字、改行のみ
   - `can_restart_from`: 各ステップの可否判定
   - `_drive_api_retry`: リトライロジック

2. **APIエンドポイントの統合テスト**（FastAPIのTestClient使用）
   - 全エンドポイントのHTTPステータスコード確認
   - エラーレスポンスの形式確認
   - SSEイベントのフォーマット検証

### 優先度 HIGH
3. **バッチ状態の永続化テスト**
   - `save_batch_state` → `get_batch_or_none` のラウンドトリップ
   - 破損JSONファイルからの復元
   - `load_batch_store` のメモリ制限動作

4. **エラーパステスト（モック使用）**
   - Rainforest API タイムアウト
   - fal.ai QuotaExhaustedError
   - gspreadネットワーク断
   - ffmpegバイナリ不在

5. **競合状態テスト**
   - 同一batch_idへの同時アクセス（asyncio並列実行）
   - pause直後のresume呼び出し

### 優先度 MEDIUM
6. **E2Eシナリオテスト**（モック外部API）
   - 正常系7ステップ完走
   - Step 1でエラー→continueして次URLへ
   - QuotaExhaustedError→バッチ停止

7. **境界値テスト**
   - `urls`が0件、1件、10件、11件
   - 画像が0枚（image_urlsが空）の商品
   - 文字数が100文字ちょうどのtitle_en

---

## 次のPhaseへの引き継ぎ事項

### 即時対応が必要な事項（本番稼働前に必須）

1. **fal.ai無限ハング対策 (#9):** `fal_client.subscribe`にタイムアウトを設定するか、スレッドタイムアウト機構を追加。推奨: `concurrent.futures.ThreadPoolExecutor`でラップして`timeout=300`

2. **resume_batchのAPIキー引き継ぎ (#Bug #2):** バッチ状態にAPIキーを（ハッシュ化または環境変数参照として）保存するか、resumeリクエストでもAPIキーを要求する

3. **ffmpeg TimeoutExpiredの捕捉:** subprocess.runのTimeoutExpiredを明示的に捕捉してエラーを返す

### 中期対応事項

4. **3つのgenerate()コードを共通関数に集約:** 重複コードの修正漏れによる回帰リスクを排除

5. **テスト基盤の構築:** pytestを導入し、外部API依存関数をモック化したユニットテストを整備（目標カバレッジ60%以上）

6. **Cloud Run環境での認証追加:** 最低限、IAP（Identity-Aware Proxy）またはAPIキーヘッダー認証の導入

### アーキテクチャ上の懸念

- BATCH_STOREのインメモリ管理はCloud Runのオートスケール（複数インスタンス）と根本的に非互換。Firestoreへの移行が中長期的に必要
- gspread認証をバッチ処理ごとに繰り返す設計は、1商品あたり7〜8回の認証リクエストを発生させる。接続プーリングまたはキャッシュが必要
