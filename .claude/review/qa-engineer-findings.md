# QA Engineer レビュー・テスト結果

**対象ファイル:**
- `C:\Users\ThinkPad\Desktop\shopee\shopee-optimizer-review\app.py`
- `C:\Users\ThinkPad\Desktop\shopee\shopee-optimizer-review\shopee_core.py`

**レビュー実施日:** 2026-03-05
**レビュアー:** QAエンジニア（シニア）

---

## 発見した課題一覧（課題抽出フェーズ）

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | BATCH_STOREがインメモリ辞書でプロセス間共有不可。Cloud Run複数インスタンスでバッチ状態が不整合になる | 並行アクセス・競合状態 | 9 | 9 | 81 |
| 2 | process-streamのgenerate()が同期ジェネレータであり、外部API呼び出し（Rainforest, fal.ai, Google Drive等）をブロッキングで実行。FastAPIのイベントループをブロックし、他リクエストが処理不能になる | 機能性・並行アクセス | 9 | 9 | 81 |
| 3 | BATCH_STOREへの同時読み書きに排他制御がない。複数リクエストが同一batch_idに対して同時にregenerate/finalize等を行うとデータ競合が発生する | 並行アクセス・競合状態 | 8 | 8 | 64 |
| 4 | serve_file エンドポイントでパストラバーサル対策はあるが、asinパラメータに `..` を含む値が渡された場合、`OUTPUT_BASE / asin / path` の構築時に意図しないディレクトリにアクセスされる可能性がある（is_relative_toチェックは存在するが、asin自体の検証がない） | セキュリティ | 8 | 7 | 56 |
| 5 | generate_video_ai の duration パラメータが無視される設計。`arguments["duration"]`が常に`model_config["params"].get("duration", duration)`となり、FAL_MODELSに既にdurationが定義されている場合は引数のdurationが上書きされない。既知バグ「5秒のみを10秒へ拡大」の根本原因 | 機能性 | 8 | 7 | 56 |
| 6 | ffprobeの出力が空文字列の場合、`float(probe.stdout.strip())`でValueError発生。ffprobeが失敗した場合のエラーハンドリングが欠如 | 異常系フロー | 7 | 7 | 49 |
| 7 | process-streamのStep 1でエラー発生時に`continue`で次URLへ進むが、resultsにはエラー商品が追加されない。クライアント側のprogressProductsとサーバー側のresultsでインデックスがずれる | 機能性 | 7 | 7 | 49 |
| 8 | generate_video関数でsource_image_pathが存在しないファイルを指す場合、image_paths[0]へフォールバックするが、image_paths[0]も存在しない場合の考慮がない | 境界値・異常系 | 6 | 6 | 36 |
| 9 | subprocess.runのtimeoutが30-60秒に設定されているが、タイムアウト時のSubprocessTimeoutExpiredが捕捉されていない。ffmpegタイムアウトでプロセス全体が中断する可能性 | 異常系フロー | 7 | 5 | 35 |
| 10 | translate_text で GoogleTranslator のレート制限や一時的なネットワークエラーに対するリトライ機構がない | 異常系フロー | 6 | 6 | 36 |
| 11 | download_images で画像URLが不正な場合、1枚の失敗で全体がraise_for_statusにより中断。部分的な成功を許容しない | 異常系フロー | 6 | 6 | 36 |
| 12 | extract_asin が大文字のASINのみマッチ（`[A-Z0-9]{10}`）。小文字を含むURLや小文字ASINが拒否される | 境界値 | 5 | 5 | 25 |
| 13 | write_to_spreadsheet で既存行の検出がcol_values(3)の全行スキャンであり、大量データ時にO(n)。また、ASINの重複チェックが最初の一致のみで、複数行に同一ASINがあった場合に最初の行のみ更新 | 大量データ | 5 | 5 | 25 |
| 14 | load_batch_storeが起動時に最大200件のJSONをメモリに読み込む。大量バッチ蓄積時にメモリ消費が増大 | 大量データ | 5 | 4 | 20 |
| 15 | history_pageでfetch_video_generation_history(limit=1000)とfetch_product_sheet_history(limit=1000)を同期的にGoogleシートAPIから取得。レスポンスが数十秒になりうる | パフォーマンス | 6 | 5 | 30 |
| 16 | SSEストリームのJSONパース失敗時のクライアント側エラーハンドリングが不足。`JSON.parse(line.slice(6))`が例外を投げた場合にストリーム全体が停止 | 異常系フロー | 5 | 5 | 25 |
| 17 | regenerate-videoでout=Noneの場合（generate_videoがNoneを返す場合）、`Path(out)`でTypeError発生 | 境界値 | 7 | 4 | 28 |
| 18 | APIキー（RAINFOREST_API_KEY, OPENAI_API_KEY, FAL_KEY等）がリクエストボディからも受け付け可能。ログやエラーメッセージ経由で意図しない露出リスク | セキュリティ | 6 | 5 | 30 |
| 19 | process-stream のリクエストボディを`json.loads(body)`で直接パースしており、不正JSONでValueError/JSONDecodeErrorが未捕捉の500エラーになる | 異常系フロー | 5 | 4 | 20 |
| 20 | 一時ディレクトリ `/tmp/shopee_gen_*` のクリーンアップがgenerate_video_kenburnsの正常終了時のみ。例外発生時に一時ファイルが残留する | リソースリーク | 4 | 4 | 16 |
| 21 | generate_video_kenburns内のffmpegコマンドでcapture_output=Trueとしているが、戻り値(returncode)を検査していない。ffmpegが失敗しても次の処理に進む | 機能性 | 6 | 5 | 30 |
| 22 | フロントエンドHTMLがapp.py内にインラインで約1200行埋め込まれている。保守性・テスタビリティが極めて低い | 保守性 | 4 | 6 | 24 |
| 23 | CSRFトークンの実装がなく、POSTエンドポイントに対するCSRF攻撃に無防備 | セキュリティ | 5 | 5 | 25 |
| 24 | 認証・認可の仕組みが一切ない。URLを知っている誰でも全機能にアクセス可能 | セキュリティ・権限外アクセス | 8 | 6 | 48 |
| 25 | fal_client.subscribe がブロッキング呼び出しで、タイムアウト設定がない。fal.aiサービスが応答しない場合、無限待機になる可能性 | 異常系フロー | 7 | 5 | 35 |
| 26 | renderReviewでASINをonclickハンドラにstring interpolationで直接埋め込んでおり、ASINに`'`が含まれた場合にJS構文エラーが発生する（通常のASINでは起きないが防御的コーディングとして不足） | 境界値 | 3 | 3 | 9 |
| 27 | gspread認証が各関数呼び出しごとに毎回実行される（write_to_spreadsheet, append_video_generation_log, fetch_*_history）。認証トークンのキャッシュがなくAPI呼び出し回数が増大 | パフォーマンス | 5 | 5 | 25 |
| 28 | _merge_bgm でffprobeが利用不可の環境ではfloat変換でクラッシュ。ffmpegの有無チェックがない | 異常系フロー | 5 | 4 | 20 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| 機能性（25点満点） | 14 | 基本的なパイプライン（取得->翻訳->動画生成->アップロード->記録）は一通り実装されている。しかし、動画のdurationパラメータが意図通りに動作しない（#5）、エラー時のインデックスずれ（#7）、regenerateのNullPointer（#17）など、コアフローに影響するバグが複数存在する。既知バグ4件中、根本原因がコード内に確認できるものが少なくとも2件ある |
| 信頼性（20点満点） | 8 | 外部API（Rainforest, Google Translate, OpenAI, fal.ai, Google Drive, Google Sheets）に強く依存しているにも関わらず、リトライ機構がほぼゼロ。タイムアウト設定が不十分（fal_client.subscribeにタイムアウトなし）。ffmpegの戻り値未検査。一時ファイルのリーク。同期ブロッキング処理によるイベントループの枯渇リスク |
| セキュリティ（15点満点） | 5 | 認証・認可が完全に欠如（#24）。CSRFトークンなし（#23）。パストラバーサル対策はis_relative_toで存在するが、asin値自体の検証不足（#4）。APIキーがリクエストボディ経由で受付可能（#18）。html.escapeは適用されているがフロントエンドのinnerHTML利用箇所が多く、XSSリスクの表面積が大きい |
| パフォーマンス（15点満点） | 6 | 同期ジェネレータでのブロッキング処理（#2）が最大の問題。gspread認証の繰り返し（#27）。履歴ページのGoogle Sheets全データ取得（#15）。BATCH_STOREのインメモリ制限（#14） |
| 保守性（15点満点） | 5 | HTMLが1200行以上app.pyにインライン埋め込み（#22）。shopee_core.pyとapp.pyの責務分離は存在するが、テストがゼロ。エラーハンドリングのパターンが一貫していない（raise vs return None vs 空リスト等）。ログレベルの使い分けが不十分 |
| テスタビリティ（10点満点） | 3 | ユニットテスト・統合テストが一切存在しない。外部依存（API呼び出し、subprocess、ファイルI/O）のモック化が考慮されていない設計。依存注入パターンが使われていない。HTMLインラインのフロントエンドはE2Eテスト以外でのテストが困難 |
| **合計（100点満点）** | **41** | |

---

## 既知バグの根本原因分析

### 1. 動画確認が複数まとめて確認になっている
**根本原因:** `finalizeBatch()` (app.py L1161-1174) の設計が「全商品を一括で確定」する仕組みになっている。個別商品ごとの確定UIは存在するが、確定ボタンは`finalizeBatch()`のみで全商品を対象とする。`finalize`エンドポイント (L1980-2047) も`batch["results"]`全体をループして処理する。商品ごとの個別確定APIが存在しない。

**推奨:** 個別商品の確定エンドポイント(`/finalize-product`)を追加し、UIに個別確定ボタンを設ける。

### 2. 複数写真がある場合、メインの写真ではないもので動画生成されることがある
**根本原因:** `generate_video` 関数 (shopee_core.py L591-640) では`source_image_path`が指定されない場合、`image_paths[0]`をフォールバックとして使用する (L617)。しかし、`download_images` (L222-232) でダウンロードされる画像の順序はAmazon APIのレスポンスにおける`images`配列の順序に依存する。Amazon APIが必ずしもメイン画像を先頭に返す保証がない。

さらに、`fetch_amazon_product` (L170-215) では `product.get("images", [])` の全画像URLを取得した後、メイン画像がない場合のみ `main_image` をフォールバックとして追加する (L200-202)。`images` 配列が存在する場合は `main_image` の位置が不定であり、`images[0]`がメイン画像である保証がない。

**推奨:** `main_image`を必ず`image_urls`の先頭に配置するロジックを追加する。または、メイン画像のフラグ/インデックスを明示的に保持する。

### 3. 追加指示を与えて再生成できるようにする
**現状分析:** コード上は既に部分的に実装済み。`regenerate-video`エンドポイント (app.py L1850-1977) は`prompt_extra`パラメータを受け付け、`generate_video`関数に`prompt_suffix`として渡す。`generate_video` (shopee_core.py L622-623) ではプロンプトに追加指示を結合している。フロントエンド (app.py L1041) にも`prompt_extra`入力フィールドが存在する。

**問題点:** 追加指示が適用されるのはAI動画生成（fal.ai使用時）のみ。FAL_KEYが未設定でKen Burnsフォールバックになる場合、`prompt_suffix`は完全に無視される (shopee_core.py L612)。ユーザーへのフィードバックがない。

### 4. 動画生成5秒のみを10秒へ拡大
**根本原因:** `generate_video_ai` (shopee_core.py L556-588) のdurationパラメータ処理にバグがある。

```python
if duration:
    arguments["duration"] = model_config["params"].get("duration", duration)
```

この行 (L574-575) は以下の問題がある:
- `model_config["params"]`に`"duration"`キーが既に存在する場合（hailuoモデル: `{"duration": 6}`、klingモデル: `{"duration": "5"}`）、引数の`duration`は無視される
- `duration`のデフォルト値が5であり、この引数を変更してもFAL_MODELSの定義値が優先される
- さらに、L570の`**model_config["params"]`で既にdurationが設定済みのため、L575で上書きしても既存値と同じ値が再設定されるだけ

**修正方針:** `generate_video_ai`の呼び出し元である`generate_video`関数はdurationパラメータを`generate_video_ai`に渡していない。`generate_video`のシグネチャにdurationを追加し、`generate_video_ai`のduration処理ロジックを`arguments["duration"] = duration`に修正する必要がある。FAL_MODELSの定義値はデフォルト値として扱い、明示指定時は上書きすべき。

---

## テストが不足している領域

### 1. ユニットテスト（完全に欠如）
- `extract_asin`: 各種URLパターン、不正URL、空文字列、特殊文字を含むURL
- `parse_drive_folder_id`: 各種Drive URL形式、空文字列、不正URL
- `next_video_version`: 空リスト、大量バージョン
- `resolve_selected_image_path`: 空リスト、一致なし、パス正規化
- `parse_ts`: 各種日時フォーマット、不正文字列、空文字列
- `translate_text`: 空文字列、超長文字列、特殊文字
- `_merge_bgm`: BGMファイル不在、ffprobe失敗、動画ファイル不正

### 2. 統合テスト（完全に欠如）
- process-stream のE2Eフロー（モック外部API使用）
- regenerate-video の動画再生成->Driveアップロード->ログ記録フロー
- finalize のスプレッドシート書き込み->状態更新フロー
- add-images の画像追加->バッチ状態更新フロー

### 3. 異常系テスト
- 各外部APIのタイムアウト・ネットワーク断
- Google Drive認証失敗時のフォールバック動作
- ffmpeg/ffprobeが未インストールの環境での動作
- ディスク容量不足時の画像/動画保存
- 不正なJSON入力に対するAPIレスポンス
- Google Sheets APIレート制限超過時

### 4. 境界値テスト
- URL 0件、1件、10件（MAX）、11件（MAX超過）の一括処理
- 画像 0枚、1枚、5枚（上限）の商品
- ASIN形式の境界（10文字未満、10文字超、小文字、特殊文字）
- バッチ状態ファイルの同時アクセス
- スプレッドシートの行数上限

### 5. セキュリティテスト
- パストラバーサル攻撃（`/files/../../etc/passwd`等）
- XSSペイロードをASINやURLに含めた場合のレンダリング
- CSRF攻撃シナリオ
- 認証なしでの全エンドポイントアクセス
- 大量リクエストによるDoS耐性

### 6. パフォーマンステスト
- 10件同時バッチ処理時のメモリ使用量・処理時間
- 履歴ページのレスポンスタイム（大量データ時）
- BATCH_STOREに数百バッチ蓄積した場合のメモリ消費

---

## 次のPhaseへの引き継ぎ事項

### 最優先で対応すべき事項
1. **同期ブロッキング問題 (#2):** `process-stream`の`generate()`内で外部API呼び出しが同期的に実行される。FastAPIの非同期性が活かされておらず、1リクエスト処理中は他リクエストが完全にブロックされる。`run_in_executor`でスレッドプールに逃がすか、非同期ライブラリ（httpx等）への移行が必要
2. **認証・認可の欠如 (#24):** 本番環境（Cloud Run）では最低限IAMまたはAPIキー認証を導入すべき
3. **duration バグ修正 (#5):** 既知バグの根本原因が特定済み。`generate_video_ai`のduration処理ロジックの修正が必要

### アーキテクチャ上の懸念
- Cloud Run環境ではインスタンスが自動スケールするため、BATCH_STOREのインメモリ管理は致命的。永続ストレージ（Firestore, Cloud SQL等）への移行を検討
- フロントエンドのインライン化は開発速度には寄与するが、テスト・デバッグ・複数人開発のボトルネック。分離を検討
- gspread認証のコネクションプーリング/キャッシュが未実装

### テスト基盤構築の推奨
- pytest + pytest-asyncio によるテスト基盤の構築
- 外部APIモック（responses, respx, unittest.mock）
- E2Eテスト用のDocker Compose環境
- CI/CDパイプラインへのテスト組み込み（現在のdeploy.ymlにはテストステップがない）

### 既知バグ対応の優先順位
1. duration バグ（#5）-- コード変更量が少なく、根本原因が明確
2. メイン画像選択バグ（#2の既知バグ）-- fetch_amazon_productのimage_urls構築ロジック修正
3. 動画確認の一括問題（#1の既知バグ）-- 個別確定APIの追加
4. 追加指示の件（#3の既知バグ）-- Ken Burnsフォールバック時の対応検討
