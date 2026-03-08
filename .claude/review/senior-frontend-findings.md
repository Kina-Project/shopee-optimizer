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
| 4 | `batchResumeBar` の挿入先ID不一致バグ: 行1206で `getElementById('progressPanel')` を参照しているが実DOMは `id="productPanel"`（行722）→ null参照で再開ボタンが非表示 | バグ / UX | 9 | 6 | 54 |
| 5 | `regenerate()` 関数（行1451〜1469）にローディング状態・disabled制御・try-catchが一切ない。二重送信リスクあり | UX / JS品質 | 8 | 8 | 64 |
| 6 | `finalizeBatch()`（行1533〜1546）にローディング状態・disabled制御がない | UX / JS品質 | 7 | 8 | 56 |
| 7 | history_asin_page の `playVideo()` 関数（行1963〜1974）でURLをそのまま `innerHTML` 文字列に展開。XSSリスク | セキュリティ | 9 | 5 | 45 |
| 8 | `driveVideoFallback()`（行1269〜1291）で `link.href = driveUrl` に `javascript:` スキームバリデーションなし | セキュリティ | 8 | 5 | 40 |
| 9 | SSEストリーム処理が `resumeBatch()`（行1000〜1022）と `startRestartFromMain()`（行1597〜1613）でコールバック再帰パターン。`runBatch()` は while-loop なのに不統一 | JS品質 | 7 | 7 | 49 |
| 10 | タブUI（`.tab-btn`）に `role="tab"` / `aria-selected` / `role="tablist"` がない。スクリーンリーダー非対応 | アクセシビリティ | 8 | 8 | 64 |
| 11 | プログレスバーに `role="progressbar"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax` がない（行928〜931） | アクセシビリティ | 5 | 5 | 25 |
| 12 | `--c-text-muted: #9298a8` が白背景上で WCAG AA 基準（4.5:1）を満たさない（実測約3.3:1） | アクセシビリティ | 7 | 8 | 56 |
| 13 | ステップドット（行576〜587）が色のみで区別。`.step-done` 以外のステータスに形状による識別がなく色覚多様性に未対応 | アクセシビリティ | 6 | 7 | 42 |
| 14 | `renderActiveProductPanel()`（行915〜951）が毎秒 `innerHTML` で全体を再生成（`elapsedTimer` 1秒間隔）。不要なDOM破棄・再構築 | パフォーマンス | 6 | 7 | 42 |
| 15 | `renderReview()`（行1298〜1432）が選択変更のたびに全パネルを `innerHTML` 再生成 → 再生中の動画が中断される | パフォーマンス / UX | 7 | 7 | 49 |
| 16 | `<video>` 要素に `preload` 属性・`aria-label` がない（行1374・2207） | アクセシビリティ / パフォーマンス | 6 | 7 | 42 |
| 17 | 全フロントエンドコードが Python 文字列リテラル1個に格納（約1300行）。コンポーネント分割・テスト・リンティングが不可能 | コード品質 | 8 | 10 | 80 |
| 18 | 履歴3ページのCSS/HTMLがメインページと完全に重複定義。保守性が極めて低い | コード品質 | 7 | 9 | 63 |
| 19 | Google Fonts を3ファミリ同時読み込み。Noto Sans JP は日本語フォントで重く、LCPに悪影響の可能性 | パフォーマンス | 6 | 7 | 42 |
| 20 | `SSE接続切断時の自動再接続ロジックがない。fetch+getReader方式はEventSourceと異なりリトライが自動化されない | UX | 6 | 7 | 42 |
| 21 | バリデーションが「実行ボタン押下後」のみ。URL入力時のリアルタイムバリデーション（Amazon URLパターンチェック）がない | UX | 5 | 6 | 30 |
| 22 | `onclick` インラインハンドラにASIN等を文字列埋め込み（行1325・1349・1394等）。ASINにシングルクォートが含まれた場合にJS構文破壊の可能性 | セキュリティ | 7 | 7 | 49 |
| 23 | `handleRestartEventMain()` 内 `step_skip`（行1625）のSTEP_NAMES参照が `STEP_NAMES[ev.step]` → 0-indexedと1-indexedの混在によりインデックスずれ | バグ | 6 | 4 | 24 |
| 24 | `quota_exhausted` イベント（行1181）で `alert()` 後にUIがブロックされるが、次アクション（チャージ方法・再開手順）の案内が画面上に残らない | UX | 7 | 7 | 49 |
| 25 | `setTimeout(syncActiveInputFromField, 0)` と `setTimeout(syncActiveInputFromField, 300)` が「おまじない」的に二重実行（行833〜834）。根本原因への対処になっていない | コード品質 | 4 | 3 | 12 |
| 26 | `getTabLabel()` 関数（行872〜875）が `item.title` を取得しながら `base`（ASIN）のみを返す。title が存在してもタブに表示されない | バグ | 5 | 5 | 25 |
| 27 | history_asin_page の1行インラインHTML（行1948）が1400文字超。可読性ゼロ | コード品質 | 5 | 4 | 20 |
| 28 | レスポンシブ対応が `@media(max-width:680px)` の1ブレークポイントのみ。タブレット（768〜1024px）に対する考慮なし | UX | 5 | 6 | 30 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| UX/使いやすさ（20点満点） | 12 | 操作フローは直感的で、ステップ進捗のリアルタイム表示・タブUI・残り時間表示など基本フィードバックは実装済み。しかし `alert()` 多用・再生成/確定のローディング状態欠如・動画再生中断・バリデーション不足・バッチ再開ボタン非表示バグが目立つ |
| アクセシビリティ（20点満点） | 6 | label/aria属性がほぼ全フォームで欠落。タブUIにrole指定なし。動画にaria-labelなし。コントラスト比不足。履歴ページ画像はalt属性すらない |
| パフォーマンス（20点満点） | 12 | CSS変数・Googleフォント遅延ロード・`loading="lazy"` は適切。毎秒全体innerHTML再生成・動画要素の再構築が継続する点と、Noto Sans JP の重さが課題 |
| JavaScript品質（20点満点） | 10 | `esc()` によるXSS対策が多くの箇所で実施済みで評価できる。コールバック再帰のSSE処理・try-catch抜け・`alert()` 依存・複数のバグ（ID不一致・インデックスずれ）がマイナス |
| CSS設計（20点満点） | 13 | CSS変数の活用・セマンティックな命名は良好。履歴ページでのCSS重複定義・JS内ハードコードstyle・インラインstyleの乱用が惜しい |

**合計: 53 / 100点**

---

## 改善提案トップ3

### 提案1: 即時バグ修正 — `batchResumeBar` の挿入先ID + XSSリスク修正

**問題:**
行1206で `document.getElementById('progressPanel')` を参照しているが、実際のDOMには `id="productPanel"`（行722）が存在する。`progressPanel` はnullになるため**バッチ再開ボタンが現状では表示されない**。

```javascript
// 行1206 修正前
const panel = document.getElementById('progressPanel');
if(panel) panel.parentNode.insertBefore(resumeDiv, panel.nextSibling);

// 修正後
const progressSec = document.getElementById('progressSec');
if(progressSec) progressSec.appendChild(resumeDiv);
```

同時に `playVideo()` 関数のXSS修正:

```javascript
// 行1971 修正前 — URLをinnerHTML文字列展開（XSSリスク）
area.innerHTML = '<video id="main-video" src="' + url + '" controls autoplay ...>';

// 修正後 — DOM API で組み立てる
const vid = document.createElement('video');
vid.id = 'main-video';
vid.src = url;           // src への直接代入はXSS安全
vid.controls = true;
vid.autoplay = true;
vid.style.cssText = 'max-width:480px;width:100%;border-radius:12px;background:#000';
area.innerHTML = '';
area.appendChild(vid);
```

- 実装コスト: 低（30分以内）
- 期待効果: バッチ停止後の再開ボタンが正常表示される（現状は確実に非表示バグ）、XSSリスクを排除

---

### 提案2: ユーザーフィードバックの統一化と非同期操作のUX改善

**問題:**
- `alert()` がエラー通知・バリデーション・確認で多用されており、モーダルブロックでUXを阻害
- `regenerate()` / `finalizeBatch()` / `searchImages()` / `addSelectedImages()` にローディング状態がない
- `renderReview()` が毎回 `innerHTML` 全再構築するため、再生中の動画が選択変更時に中断される

**解決策:**

1. `alert()` をインラインのトースト関数に置き換え（15〜20箇所）:
```javascript
function showToast(msg, type = 'error') {
  const toast = document.createElement('div');
  toast.style.cssText = `position:fixed;bottom:24px;right:24px;padding:12px 20px;border-radius:8px;
    background:${type==='error'?'var(--c-error-bg)':'var(--c-success-bg)'};
    color:${type==='error'?'var(--c-error)':'var(--c-success)'};
    border:1px solid currentColor;font-size:13px;z-index:9999;max-width:360px;box-shadow:var(--shadow-lg)`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}
```

2. `regenerate()` にローディング制御を追加:
```javascript
async function regenerate(asin) {
  const btn = event.currentTarget;  // onclickからeventで取得
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-spinner"></span>生成中...';
  try {
    const res = await fetch('/regenerate-video', { ... });
    // ...
  } catch(e) {
    showToast('再生成エラー: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '再生成';
  }
}
```

3. `renderReview()` の動画要素は `src` の付け替えのみで済むよう、パネル全体の再構築を避ける（動画要素は1回だけ生成して `src` を差し替える）

- 実装コスト: 中（半日〜1日）
- 期待効果: ユーザーの操作迷子・二重送信を防止。エラー発生時の自己解決率向上。動画レビュー時の体験が大幅に改善

---

### 提案3: アクセシビリティ基盤整備（label・ARIA・コントラスト）

**問題:**
フォーム要素全体にlabelがなく、スクリーンリーダーで入力欄の目的が不明。タブUIがARIAセマンティクスを持たない。コントラスト比不足箇所がある。

**優先対応箇所（行番号付き）:**

1. URL入力欄（行703）:
```html
<label for="tabUrlInput" class="visually-hidden">商品URL（Amazon）</label>
<input id="tabUrlInput" class="url-input" type="text"
       aria-describedby="url-hint"
       placeholder="https://www.amazon.co.jp/dp/XXXXXXXXXX" />
<span id="url-hint" class="small">AmazonのASINを含むURLを入力してください</span>
```

2. タブUI生成部分（行767・887・1310付近）に `role` 追加:
```javascript
`<div role="tablist" aria-label="商品タブ">` +
inputTabsState.map((t,i) =>
  `<button class="tab-btn ${...}" role="tab"
    aria-selected="${i===activeInputTabIndex}"
    onclick="setActiveInputTab(${i})">${esc(...)}</button>`
).join('') +
`</div>`
```

3. プログレスバー（行929〜931）:
```html
<div style="height:3px;..."
     role="progressbar"
     aria-valuenow="${progressPct}"
     aria-valuemin="0"
     aria-valuemax="100"
     aria-label="処理進捗">
```

4. コントラスト改善（行364）:
```css
/* 修正前 */
--c-text-muted: #9298a8;   /* 白背景で約3.3:1 — AA不合格 */
/* 修正後 */
--c-text-muted: #6b7280;   /* 白背景で約4.6:1 — AA合格 */
```

5. 動画要素（行1374）に `aria-label` と `preload` 追加:
```javascript
`<video controls preload="metadata"
  aria-label="${esc(p.asin)} の商品動画"
  src="${esc(v.video_url)}"
  onerror="driveVideoFallback(this,'${esc(v.drive_file_url||'')}')">`
```

- 実装コスト: 中（1日）
- 期待効果: WCAG 2.1 AA準拠への大きな前進。支援技術ユーザーが基本操作可能になる

---

## 個別バグ・CRITICAL指摘

### CRITICAL: `batchResumeBar` 挿入先IDの不一致（行1206）

`document.getElementById('progressPanel')` は常に null を返す。DOMには `id="productPanel"` しか存在しない。バッチが一時停止・API残高不足で停止した際の再開ボタンがユーザーに見えない。

### CRITICAL: `playVideo()` XSS（行1970〜1972）

```javascript
// 問題のコード（行1971）
area.innerHTML = '<video id="main-video" src="' + url + '" controls autoplay style="...">';
```

`url` は呼び出し元HTMLで `html.escape` 済みだが、JSの文字列結合コンテキストではRaw文字列として扱われる。Drive動画のURLに悪意あるペイロードが混入した場合にXSS成立の可能性。DOM API（`createElement` + `src` 直接代入）で代替する。

### HIGH: `handleRestartEventMain` のインデックスずれ（行1625）

```javascript
// 問題（行1625）
list.innerHTML += '<div>Step ' + ev.step + ': ' + (ev.name || STEP_NAMES[ev.step-1] || '') + '...</div>';
// STEP_NAMES は 0-indexed配列なので ev.step-1 が正しいが、
// 別の箇所（行2114）では STEP_NAMES[ev.step] を使っており不統一
```

`STEP_NAMES[ev.step]` は Step1のときに `STEP_NAMES[1]` = "画像ダウンロード" を返す（1つずれ）。統一して `STEP_NAMES[ev.step - 1]` を使う。

### HIGH: `getTabLabel()` が title を使っていない（行872〜875）

```javascript
function getTabLabel(item, index) {
  const base = item.asin || `商品${index+1}`;
  return item.title ? `${base}` : base;  // titleがあっても base だけ返す（titleを使っていない）
}
// 修正
  return item.title ? `${base}: ${item.title.slice(0, 20)}` : base;
```

---

## 良かった点（維持すべき実装）

1. **`esc()` 関数の一貫した使用（行751）**: `div.textContent = s` で自動エスケープしてから `innerHTML` を返すパターンは正しい。動的HTML生成箇所の大部分で一貫して使われている。

2. **CSS変数の体系的な定義（行353〜385）**: カラー・影・ボーダー半径・フォントをCSS変数で一元管理。デザイン変更コストを最小化する良い設計。

3. **SSEストリームの接続監視（行1056〜1062）**: `streamWatchTimer` で更新が止まったら警告を出す仕組みは「処理が進んでいるか不安」というユーザー心理を正しく解消している。

4. **`beforeunload` ガード（行857〜863）**: 処理中のページ離脱防止は長時間処理ツールとして必須対応で適切。

5. **ファイルサーブのパストラバーサル対策（行2638〜2652）**: `resolve().is_relative_to()` によるディレクトリ外アクセス防止は適切なサーバーサイド対策。

6. **`driveImgFallback()` / `driveVideoFallback()` のフォールバック設計（行1262〜1291）**: ローカルファイル→Drive thumbnail→プロキシ→Driveリンクの多段フォールバックは実用的で良い設計。

---

## 次のPhaseへの引き継ぎ事項

### 即座に修正すべき（1〜2時間以内）

| 対応項目 | 該当行 | 理由 |
|---------|--------|------|
| `batchResumeBar` 挿入先IDを `progressSec` に修正 | 行1206 | バグ：再開ボタンが非表示 |
| `playVideo()` を DOM API 化 | 行1970〜1972 | XSSリスク |
| `driveVideoFallback()` に https バリデーション追加 | 行1275 | XSSリスク |
| `getTabLabel()` で title を使うよう修正 | 行874 | バグ：titleが表示されない |
| `STEP_NAMES[ev.step]` → `STEP_NAMES[ev.step-1]` に統一 | 行2114 | バグ：ステップ名ずれ |

### 短期改善（次スプリント）

- `resumeBatch()` / `startRestartFromMain()` のSSE処理を `runBatch()` と同様のwhile-loopに統一
- `alert()` をインライントースト通知に置き換え（推定15〜20箇所）
- `regenerate()` / `finalizeBatch()` にローディング状態追加
- 全フォーム要素への `<label for>` または `aria-label` 付与
- タブボタンへの `role="tab"` + `aria-selected` 付与
- `--c-text-muted` を `#6b7280` に変更（コントラスト比確保）

### 中期改善（運用安定後）

- `renderActiveProductPanel()` / `renderReview()` の差分更新（毎秒full-rerenderを廃止・動画src差し替えのみに）
- 履歴ページCSS重複の解消（FastAPI `StaticFiles` で共通CSS配信）
- インラインHTML全体をJinja2テンプレートまたは静的ファイルに分離
- SSE接続断時の自動再接続ロジック実装（`EventSource` への切り替え検討、またはfetch+getReader方式でのexponential backoff retry）
- URL入力時のリアルタイムバリデーション（Amazon URLパターン正規表現チェック）
- グローバル変数による状態管理を軽量なStore（Pub/Sub）パターンへ移行
- Playwright E2Eテストの整備（SSEストリーム処理・タブ切替・再生成フロー）
