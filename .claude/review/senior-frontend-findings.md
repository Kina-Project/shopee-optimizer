# Senior Frontend Engineer レビュー結果

**対象ファイル:** `app.py`（INDEX_HTML 内のHTML/CSS/JavaScript、および履歴ページ等のサーバーサイドHTML生成部分）
**レビュー日:** 2026-03-08（初回: 2026-03-05 / 再レビューで行番号精査・バグ追記）

---

## 発見した課題一覧

| # | 課題 | カテゴリ | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | フォームの `<input>` / `<select>` に `<label>` が一切紐付いていない | アクセシビリティ | 9 | 10 | 90 |
| 2 | 画像の `alt` 属性が機械的（`image-1`, `image-2` 等）で意味を持たない。履歴ページの `<img>` には `alt` 自体が存在しない（行1757・1853・1859・2195・2201） | アクセシビリティ | 8 | 9 | 72 |
| 3 | `alert()` をエラー通知・バリデーションに多用（行792・954・959・1033・1034・1181・1463・1479等）。ユーザーに次の行動を促す情報が不足 | UX | 8 | 9 | 72 |
| 4 | `batchResumeBar` の挿入先ID不一致バグ: 行1174で `getElementById('progressPanel')` を参照しているが実DOMは `id="productPanel"`（行689）→ null参照で再開ボタンが非表示 | バグ / UX | 9 | 6 | 54 |
| 5 | `regenerate()` 関数（行1407〜1457）にローディング状態・disabled制御・カウンターtimerがDOMライフサイクルと連動していない | UX / JS品質 | 8 | 8 | 64 |
| 6 | `finalizeBatch()`（行1520〜1533）にローディング状態・disabled制御がない | UX / JS品質 | 7 | 8 | 56 |
| 7 | history_asin_page の `playVideo()` 関数（行1855〜1872）でURLをそのまま `innerHTML` 文字列に展開。XSSリスク | セキュリティ | 9 | 5 | 45 |
| 8 | `driveVideoFallback()`（行1239〜1261）で `link.href = driveUrl` に `javascript:` スキームバリデーションなし | セキュリティ | 8 | 5 | 40 |
| 9 | SSEストリーム処理が `resumeBatch()`（行934〜997）でコールバック再帰パターン。`runBatch()` は while-loop なのに不統一 | JS品質 | 7 | 7 | 49 |
| 10 | タブUI（`.tab-btn`）に `role="tab"` / `aria-selected` / `role="tablist"` がない。スクリーンリーダー非対応 | アクセシビリティ | 8 | 8 | 64 |
| 11 | プログレスバーに `role="progressbar"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax` がない（行897〜899） | アクセシビリティ | 5 | 5 | 25 |
| 12 | `--c-text-muted: #9298a8` が白背景上で WCAG AA 基準（4.5:1）を満たさない（実測約3.3:1） | アクセシビリティ | 7 | 8 | 56 |
| 13 | ステップドット（行537〜554）が色のみで区別。`.step-done` 以外のステータスに形状による識別がなく色覚多様性に未対応 | アクセシビリティ | 6 | 7 | 42 |
| 14 | `renderActiveProductPanel()`（行883〜919）が毎秒 `innerHTML` で全体を再生成（`elapsedTimer` 1秒間隔）。不要なDOM破棄・再構築 | パフォーマンス | 6 | 7 | 42 |
| 15 | `renderReview()`（行1268〜1389）が選択変更のたびに全パネルを `innerHTML` 再生成 → 再生中の動画が中断される | パフォーマンス / UX | 7 | 7 | 49 |
| 16 | `<video>` 要素に `preload` 属性・`aria-label` がない（行1346付近） | アクセシビリティ / パフォーマンス | 6 | 7 | 42 |
| 17 | 全フロントエンドコードが Python 文字列リテラル1個に格納（約1300行）。コンポーネント分割・テスト・リンティングが不可能 | コード品質 | 8 | 10 | 80 |
| 18 | 履歴3ページのCSS/HTMLがメインページと完全に重複定義。保守性が極めて低い | コード品質 | 7 | 9 | 63 |
| 19 | Google Fonts を3ファミリ同時読み込み。Noto Sans JP は日本語フォントで重く、LCPに悪影響の可能性 | パフォーマンス | 6 | 7 | 42 |
| 20 | SSE接続切断時の自動再接続ロジックがない。fetch+getReader方式はEventSourceと異なりリトライが自動化されない | UX | 6 | 7 | 42 |
| 21 | バリデーションが「実行ボタン押下後」のみ。URL入力時のリアルタイムバリデーション（Amazon URLパターンチェック）がない | UX | 5 | 6 | 30 |
| 22 | `onclick` インラインハンドラにASIN等を文字列埋め込み（行1297・1305・1321等）。ASINにシングルクォートが含まれた場合にJS構文破壊の可能性 | セキュリティ | 7 | 7 | 49 |
| 23 | `getTabLabel()` 関数（行840〜843）が `item.title` を条件分岐しているが `base`（ASIN）のみを返す。title が存在してもタブに表示されない | バグ | 5 | 5 | 25 |
| 24 | `quota_exhausted` イベント（行1148）で `alert()` 後にUIがブロックされるが、次アクション（チャージ方法・再開手順）の案内が画面上に残らない | UX | 7 | 7 | 49 |
| 25 | `setTimeout(syncActiveInputFromField, 0)` と `setTimeout(syncActiveInputFromField, 300)` が「おまじない」的に二重実行（行801〜802）。根本原因への対処になっていない | コード品質 | 4 | 3 | 12 |
| 26 | レスポンシブ対応が `@media(max-width:680px)` の1ブレークポイントのみ。タブレット（768〜1024px）に対する考慮なし | UX | 5 | 6 | 30 |

---

## PR#19〜#22 変更内容の個別評価

### PR#19: 演出名日本語表示・searchShownAsins永続化・再生成ステータス・確定→使用動画・ステップ再開削除

#### 良い変更

**EFFECT_LABELSのPython側定義（行61〜67）**
Python側でEFFECT_LABELSを定義し、`/history/{batch_id}/{asin}` ページの動画一覧表示（行1954）でPythonの `EFFECT_LABELS.get()` を利用している点は正しいアプローチ。サーバーサイドレンダリング箇所での利用は一元管理できている。

**searchShownAsins Set（行710）とrenderReview内の永続化ロジック（行1292〜1293）**
`product_done` イベント時（行1192）と `all_done` イベント時（行1206）の2箇所でSetに追加しており、処理完了後に画像検索UIが不要になっても表示を維持できる設計は意図通り。

**再生成ステータス表示（行1368・1418〜1456）**
`regenStatus_${asin}` 要素を固定的に確保し、カウンターtimerでリアルタイム更新する設計は適切。完了・エラー時の色分け（緑/赤）も分かりやすい。

#### 問題のある変更

**CRITICAL: EFFECT_LABELSの三重定義**

| 定義箇所 | 行番号 | 用途 |
|---------|--------|------|
| Python: `EFFECT_LABELS = {...}` | 61〜67行 | /history/{batch_id}/{asin}ページの動画履歴表示 |
| JS inline object（バージョン選択セレクト内） | 1374行 | レビュー画面の「使用動画」セレクト |
| JS固定HTMLのselectオプション（複数箇所） | 1355〜1359行、historyページ | 演出選択セレクト |

バージョン選択セレクト（1374行）のインラインオブジェクト：
```javascript
({zoom:'ズーム',unbox:'開封',steam:'湯気',condensation:'結露',pickup:'持ち上げ'})[x.effect]||x.effect
```
このオブジェクトはJSグローバル変数として定義されておらず、renderReview()が呼ばれるたびに毎回生成される使い捨てオブジェクトである。演出種類が追加された際にここだけ更新漏れが起きるリスクが高い。

**HIGH: searchShownAsinsがrunBatch時にリセットされない**

```javascript
// runBatch関数（行1004〜）のリセット処理
reviewProducts=[];
activeReviewProductIndex=0;
currentBatchId='';
// searchShownAsins.clear() が存在しない
```

同一ブラウザセッション内で複数バッチを実行した場合、前バッチで「手動で画像検索を開いたASIN」がSetに残り続ける。次バッチで同じASINを処理した際にimage_shortage=falseであっても画像検索UIが表示され続ける。

**CRITICAL: batchResumeBarの挿入先ID不一致（引き続き未修正）**

```javascript
// 行1174: bug存在
const panel=document.getElementById('progressPanel');  // null が返る
if(panel) panel.parentNode.insertBefore(resumeDiv,panel.nextSibling);
// → 再開ボタンが一切表示されない
```

実際のDOMには `id="productPanel"`（行689）が存在し、`progressPanel` というIDは存在しない。

**MEDIUM: regenStatus timer の DOMライフサイクル非連動**

```javascript
// 行1423
const timer=setInterval(()=>{sec++;statusEl.textContent=`動画を再生成中です（${sec}秒経過）...`;},1000);
```

`statusEl` は `renderReview()` が呼ばれると削除されるDOMノード。ユーザーが再生成中に別タブへ切り替えると `renderReview()` が実行され `statusEl` がDOMから消えるが、timerは生き続ける。JavaScriptはデタッチされたDOMノードへの参照を保持するのでGCも走らない。コンソールエラーは出ないが不要なメモリ保持が発生する。

修正案：
```javascript
const timer=setInterval(()=>{
  sec++;
  const currentEl=document.getElementById(`regenStatus_${asin}`);
  if(!currentEl){clearInterval(timer);return;}
  currentEl.textContent=`動画を再生成中です（${sec}秒経過）...`;
},1000);
```

**LOW: resumeBatch完了後のボタンテキスト不一致**

```javascript
// resumeBatch完了時（行972）
btn.disabled=false;btn.textContent='一括処理開始';

// runBatch finally（行1064）
btn.textContent='一括実行';

// HTMLの初期値（行677）
<button id="runBtn" type="button" class="btn">一括実行</button>
```

「一括実行」と「一括処理開始」が混在。初期HTMLに合わせて「一括実行」に統一すべき。

**ステップ再開削除後のUI確認**

`resumeBatch` 関数（行934〜997）は残存しており、`batch_stopped` イベントハンドラ（行1150〜1178）から呼び出される。ステップ再開の削除はサーバーサイドの対応で、フロントのresumeバー表示ロジックは残っている。課題はID不一致で表示されないことだが、表示ロジック自体は削除されていない点は確認済み（意図通りと判断）。

---

### PR#21: /add-imagesのエラーハンドリング変更（batch消失時もダウンロード可能）

**バックエンド変更の方向性**: 正しい。batch消失時でもファイルシステムベースで`existing_count`を取得（行3279）し、ダウンロードを継続する設計はユーザーの作業を継続させる点で良い。

**フロントエンドのUX問題**

```javascript
// addSelectedImages（行1492〜1518）
const data=await res.json();
if(!res.ok){alert(data.detail||'取り込みに失敗しました');return}
// 成功時の処理...
const p=reviewProducts.find(x=>x.asin===asin);
if(p){
  p.image_count=data.total_image_count;
  // ...
}
```

batch消失時（product=None）でも200レスポンスが返るが、フロントでは`reviewProducts`内の`p`を更新するのみ。ユーザーに「バッチデータが見つからないため、ローカルファイルのみに追加しました（次回の再生成で反映されます）」等のインフォメーション表示がない。

また、batch消失時は`product["image_paths"]`の更新がサーバー側でスキップされる（行3286の`if product:`分岐）ため、フロントの`p.image_urls`は更新されても次回サーバーアクセス時に整合性が取れない可能性がある。

---

### PR#22: /regenerate-videoの応答形式変更（batch消失時はファイルシステムから復元）

**バックエンド変更の方向性**: 正しい。`get_batch_or_none`でファイルシステムからの復元を試み、それも失敗した場合のフォールバック（行3060〜3067）は適切。

**フロントエンドへの影響**

batch消失時の `all_videos` 構造（行3145）：
```python
all_videos = (product.get("videos", []) if product else existing_videos + [video_record])
```

`existing_videos` は `[{"version": p.stem} for p in sorted(videos_dir.glob("*.mp4"))]`（行3064）で生成される。このオブジェクトには `effect`, `model`, `video_url`, `drive_file_url` キーが存在しない。

フロントのrenderReview（行1374）で参照している：
```javascript
${(p.videos||[]).map(x=>`<option value="${x.version}" ${...}>${x.version}（${EFFECT_LABELS_OBJ[x.effect]||x.effect}）</option>`)}
```

`x.effect` が `undefined` になるため、「v1（undefined）」と表示される。また `x.video_url` がないため動画再生ができない。

**改善案**: batch消失時の `existing_videos` に最低限 `video_url` を付与する：
```python
existing_videos = [
    {
        "version": p.stem,
        "effect": "zoom",
        "model": "unknown",
        "video_url": f"/files/{asin}/videos/{p.name}",
        "drive_file_url": "",
    }
    for p in sorted(videos_dir.glob("*.mp4"))
]
```

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| UX/使いやすさ（20点満点） | 12 | 操作フローは直感的で、ステップ進捗のリアルタイム表示・タブUI・残り時間表示・再生成ステータスカウンターなど基本フィードバックは実装済み。しかし `alert()` 多用・再生成timerのDOMライフサイクル問題・動画再生中断・バッチ再開ボタン非表示バグが目立つ |
| アクセシビリティ（20点満点） | 6 | label/aria属性がほぼ全フォームで欠落。タブUIにrole指定なし。動画にaria-labelなし。コントラスト比不足。履歴ページ画像はalt属性すらない |
| パフォーマンス（20点満点） | 12 | CSS変数・Googleフォント遅延ロード・`loading="lazy"` は適切。毎秒全体innerHTML再生成・動画要素の再構築が継続する点と、Noto Sans JP の重さが課題 |
| JavaScript品質（20点満点） | 10 | `esc()` によるXSS対策が多くの箇所で実施済みで評価できる。コールバック再帰のSSE処理・EFFECT_LABELS三重定義・searchShownAsins未クリア・batchResumeBar ID不一致がマイナス |
| CSS設計（20点満点） | 13 | CSS変数の活用・セマンティックな命名は良好。履歴ページでのCSS重複定義・JS内ハードコードstyle・インラインstyleの乱用が惜しい |

**合計: 53 / 100点**

---

## 改善提案トップ3

### 提案1: 即時バグ修正 — `batchResumeBar` の挿入先ID修正

**問題:**
行1174で `document.getElementById('progressPanel')` を参照しているが、実際のDOMには `id="productPanel"`（行689）が存在する。`progressPanel` はnullになるため**バッチ再開ボタンが現状では表示されない**。

```javascript
// 行1174 修正前
const panel=document.getElementById('progressPanel');
if(panel) panel.parentNode.insertBefore(resumeDiv,panel.nextSibling);

// 修正後（progressSecの末尾に追加）
const progressSec=document.getElementById('progressSec');
if(progressSec) progressSec.appendChild(resumeDiv);
```

- 実装コスト: 低（5分）
- 期待効果: バッチ停止後の再開ボタンが正常表示される（現状は確実に非表示バグ）

---

### 提案2: searchShownAsinsリセット + EFFECT_LABELS一元化

**問題:**
- runBatch時にsearchShownAsinsがリセットされず、複数バッチで誤表示が継続する
- EFFECT_LABELSが3箇所で個別定義されており、演出追加時に漏れが起きる

**解決策A（即時・1行）**: runBatch冒頭に追加
```javascript
async function runBatch(){
  const urls=collectInputUrls();
  if(!urls.length){alert('URLを入力してください');return}
  searchShownAsins.clear(); // ← 追加
```

**解決策B（中期・EFFECT_LABELS一元化）**: Pythonの`EFFECT_LABELS`をJSONとしてHTMLに注入
```python
# Pythonテンプレート側
EFFECT_LABELS_JS = json.dumps(EFFECT_LABELS, ensure_ascii=False)
# HTMLの<script>先頭に追記
f"const EFFECT_LABELS={EFFECT_LABELS_JS};"
```
これによりPython側1箇所の管理で全表示箇所が同期する。

- 実装コスト: A=低（1行）、B=中（1時間）
- 期待効果: 複数バッチ時の誤表示解消。演出追加コストの削減

---

### 提案3: アクセシビリティ基盤整備（label・ARIA・コントラスト）

**問題:**
フォーム要素全体にlabelがなく、スクリーンリーダーで入力欄の目的が不明。タブUIがARIAセマンティクスを持たない。コントラスト比不足箇所がある。

**優先対応箇所（行番号付き）:**

1. URL入力欄（行670）:
```html
<label for="tabUrlInput" class="visually-hidden">商品URL（Amazon）</label>
<input id="tabUrlInput" .../>
```

2. タブUI生成部分にrole追加（renderInputTabs・renderProgressTabs・renderReview内）:
```javascript
`<div role="tablist">` +
items.map((...) =>
  `<button ... role="tab" aria-selected="${isActive}">`
).join('') +
`</div>`
```

3. プログレスバー（行897〜899）:
```html
<div role="progressbar"
     aria-valuenow="${progressPct}"
     aria-valuemin="0"
     aria-valuemax="100">
```

4. コントラスト改善（行332）:
```css
--c-text-muted: #6b7280;  /* 3.3:1 → 4.6:1 へ改善 */
```

- 実装コスト: 中（1日）
- 期待効果: WCAG 2.1 AA準拠への前進。支援技術ユーザーが基本操作可能になる

---

## 良かった点（維持すべき実装）

1. **`esc()` 関数の一貫した使用（行719）**: `div.textContent = s` で自動エスケープしてから `innerHTML` を返すパターンは正しい。動的HTML生成箇所の大部分で一貫して使われている。

2. **CSS変数の体系的な定義（行320〜352）**: カラー・影・ボーダー半径・フォントをCSS変数で一元管理。デザイン変更コストを最小化する良い設計。

3. **SSEストリームの接続監視（行1023〜1030）**: `streamWatchTimer` で更新が止まったら警告を出す仕組みは「処理が進んでいるか不安」というユーザー心理を正しく解消している。

4. **`beforeunload` ガード（行825〜831）**: 処理中のページ離脱防止は長時間処理ツールとして必須対応で適切。

5. **ファイルサーブのパストラバーサル対策（行2016〜2030）**: `resolve().is_relative_to()` によるディレクトリ外アクセス防止は適切なサーバーサイド対策。

6. **`driveImgFallback()` / `driveVideoFallback()` のフォールバック設計（行1232〜1261）**: ローカルファイル→Drive thumbnail→プロキシ→Driveリンクの多段フォールバックは実用的で良い設計。

7. **再生成ステータスの3状態表示（行1418〜1456）**: 処理中（黄）・完了（緑）・エラー（赤）の色分けとカウンター表示は適切なフィードバック設計。

---

## 次のPhaseへの引き継ぎ事項

### 即座に修正すべき（1〜2時間以内）

| 対応項目 | 該当行 | 理由 |
|---------|--------|------|
| `batchResumeBar` 挿入先を `progressPanel` → `progressSec` に修正 | 行1174 | バグ：再開ボタンが非表示（一時停止・API残高不足後の再開不能） |
| `searchShownAsins.clear()` をrunBatch冒頭に追加 | 行1004 | バグ：複数バッチ実行時の誤表示継続 |
| `resumeBatch`完了時のbtn.textContentを「一括実行」に統一 | 行972 | UX：ボタンテキスト不一致 |

### 短期改善（次スプリント）

- EFFECT_LABELSをPythonからJSON inject → JSグローバル定数として一元管理（3箇所の重複排除）
- `regenStatus` timer にDOMライフサイクル保護（`document.getElementById`での再取得）を追加
- PR#22のbatch消失時existing_videosに `video_url`・`effect` を付与（「v1（undefined）」表示の修正）
- `alert()` をインライントースト通知に置き換え（推定15〜20箇所）
- `finalizeBatch()` にローディング状態追加
- 全フォーム要素への `<label for>` または `aria-label` 付与
- タブボタンへの `role="tab"` + `aria-selected` 付与
- `--c-text-muted` を `#6b7280` に変更（コントラスト比確保）

### 中期改善（運用安定後）

- `renderActiveProductPanel()` / `renderReview()` の差分更新（毎秒full-rerenderを廃止・動画src差し替えのみに）
- 履歴ページCSS重複の解消（FastAPI `StaticFiles` で共通CSS配信）
- インラインHTML全体をJinja2テンプレートまたは静的ファイルに分離
- SSE接続断時の自動再接続ロジック実装（exponential backoff retry）
- URL入力時のリアルタイムバリデーション（Amazon URLパターン正規表現チェック）
- Playwright E2Eテストの整備（SSEストリーム処理・タブ切替・再生成フロー）
