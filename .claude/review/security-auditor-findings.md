# Security Auditor レビュー結果

**審査日**: 2026-03-08
**審査対象**: Shopee Optimizer (FastAPI + Cloud Run)
**審査担当**: シニアセキュリティエンジニア (CISSP / 経験10年)
**審査ファイル**: app.py (3866行), shopee_core.py (1488行), Dockerfile (38行), deploy.yml, requirements.txt

---

## 🔴 Critical（即時対応必須）

### C-1: OAuthクライアントシークレットのリポジトリ内平文保存

**該当ファイル**: `client_secret_714089953721-q825i5sic1c716opkpc6afb02mrmpj8m.apps.googleusercontent.com.json`

プロジェクトルートに Google OAuth クライアントシークレットが JSON ファイルとして存在する。ファイル名自体にも `client_id` が含まれており、以下の機密情報が平文で確認できる状態にある。

```
client_id:     714089953721-q825i5sic1c716opkpc6afb02mrmpj8m.apps.googleusercontent.com
client_secret: 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  (確認済み・要失効)
project_id:    gcloudcli-487108
```

**攻撃シナリオ**: このファイルが Git リポジトリに含まれていた場合、Gitの公開履歴からシークレットを取得し、`redirect_uris` に含まれる `oauthplayground` を悪用した OAuth フローにより、プロジェクトに紐づく Google アカウントへの不正アクセスが可能となる。

**即時対応手順**:
```bash
# 1. Gitの管理から除外
git rm --cached "client_secret_714089953721-q825i5sic1c716opkpc6afb02mrmpj8m.apps.googleusercontent.com.json"

# 2. .gitignore に追加
echo "client_secret_*.json" >> .gitignore

# 3. Google Cloud Console でシークレットを即座に失効・再発行
# https://console.cloud.google.com/apis/credentials
```

---

### C-2: 認証・認可が全エンドポイントで完全に欠如

**該当ファイル**: `app.py` 全体（L141-145, L1662-3866）

全エンドポイントに認証ミドルウェアが一切存在しない。

```python
# app.py L141-145
app = FastAPI(
    title="Shopee商品ページ最適化API",
    version="2.0.0",
    lifespan=lifespan,
)
# 認証ミドルウェアの追加なし
```

**攻撃シナリオ**:
- `/process-stream` を繰り返し呼び出し、Rainforest API / OpenAI / fal.ai のクレジットを枯渇させる
- `/batch/{batch_id}` で IDOR（他者のバッチデータ閲覧）
- `/files/{asin}/{path}` で任意ユーザーの商品画像・動画を取得
- `/add-images` を悪用した SSRF（H-1 参照）

**対応**: Cloud Run の `--no-unauthenticated` フラグを有効化し、Cloud IAP で制御する。または FastAPI レベルで API キー認証を実装する。

```python
# app.py に追加
import secrets
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def require_api_key(api_key: str = Security(api_key_header)) -> str:
    expected = os.environ.get("APP_API_KEY", "")
    if not expected or not secrets.compare_digest(api_key or "", expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return api_key
```

---

## 🟠 High（早急に対応）

### H-1: SSRF（サーバーサイドリクエストフォージェリ）

**該当ファイル**: `shopee_core.py` L361-378 (`download_supplemental_images`), `app.py` L3817-3859 (`/add-images`)

ユーザーが `/add-images` で送信した任意の URL に対してサーバーが HTTP リクエストを発行する。URL のスキーム・ホストのバリデーションが存在しない。

```python
# shopee_core.py L366-373
for i, url in enumerate(image_urls):
    ...
    resp = requests.get(url, headers=HEADERS, timeout=15)  # 任意URLへ無検証でリクエスト
```

**攻撃シナリオ**: `http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token` を指定することで GCP メタデータサービスからサービスアカウントトークンを窃取し、Cloud Run に付与された IAM 権限（Drive API 等）を不正利用できる。

**修正方法**:
```python
# shopee_core.py に追加
import ipaddress
from urllib.parse import urlparse

def _validate_external_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        blocked = {"169.254.169.254", "metadata.google.internal", "metadata.goog",
                   "localhost", "127.0.0.1", "::1"}
        if host.lower() in blocked:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False

# download_supplemental_images の先頭で呼び出す
if not _validate_external_url(url):
    logger.warning("SSRF対策: 不正URLをスキップ: %s", url)
    continue
```

---

### H-2: レート制限の欠如

**該当ファイル**: `app.py` 全エンドポイント

`/process-stream` は Amazon API・AI 動画生成 API を順次呼び出す重量処理（推定 165 秒/件）であり、レート制限なしで繰り返し呼び出せる。

**攻撃シナリオ**: 悪意あるユーザーがバッチを連続送信し、従量課金の外部 API（Rainforest / OpenAI / fal.ai）のクレジットを枯渇させる。

**対応**:
```python
# requirements.txt に追加: slowapi>=0.1.9
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.post("/process-stream")
@limiter.limit("5/minute")
async def process_stream(request: Request):
    ...
```

---

### H-3: Google Drive 任意ファイルIDプロキシ

**該当ファイル**: `app.py` L2655-2680 (`/drive-video/{file_id}`)

認証なしで任意の Google Drive ファイル ID をプロキシするエンドポイント。サービスアカウントがアクセスできる Drive ファイルならば種別を問わずダウンロードできる。

```python
# app.py L2655
@app.get("/drive-video/{file_id}")
async def drive_video_proxy(file_id: str):
    if not re.match(r'^[a-zA-Z0-9_-]+$', file_id):  # 形式チェックのみ
        raise HTTPException(status_code=400, detail="Invalid file ID")
    # 所有権確認なし。バッチデータと無関係なファイルIDも通過する
    request = drive.files().get_media(fileId=file_id)
```

**対応**: アクセス可能な file_id を BATCH_STORE 内の既知動画 file_id に限定する。

```python
def _is_known_drive_file_id(file_id: str) -> bool:
    for batch in BATCH_STORE.values():
        for product in batch.get("results", []):
            for video in product.get("videos", []):
                drive_url = video.get("drive_file_url", "")
                m = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
                if m and m.group(1) == file_id:
                    return True
    return False
```

---

### H-4: CSP および HSTS ヘッダーの欠如

**該当ファイル**: `app.py` L148-157 (`SecurityHeadersMiddleware`)

```python
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # CSP なし, HSTS なし, Permissions-Policy なし
        return response
```

3800 行超のインライン JavaScript が含まれる HTML ページに CSP が設定されていないため、XSS が発生した場合の被害を拡大させる。

**対応**:
```python
response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
response.headers["Content-Security-Policy"] = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "  # インラインJS多用のため暫定
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' https://lh3.googleusercontent.com https://drive.google.com data:; "
    "connect-src 'self';"
)
response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
```

---

### H-5: 入力バリデーション不足（URL・メモフィールド）

**該当ファイル**: `app.py` L2695 (`/process-stream`), L3603 (`/regenerate-video`)

処理対象URLはAmazon URLかどうかのチェックがなく、`memo` フィールドは長さ制限なしにそのままスプレッドシートに書き込まれる（CSVインジェクションリスク）。

```python
# app.py L2695: strip()のみでURLバリデーションなし
urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]

# app.py L3603: メモに長さ制限・サニタイズなし
memo = data.get("memo", "")
```

**修正例**:
```python
from urllib.parse import urlparse

def is_valid_amazon_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and "amazon." in (p.netloc or "")
    except Exception:
        return False

urls = [u.strip() for u in urls if isinstance(u, str) and is_valid_amazon_url(u.strip())]

# メモのサニタイズ
def sanitize_memo(memo: str) -> str:
    memo = (memo or "")[:200].strip()
    if memo.startswith(("=", "+", "-", "@")):  # スプレッドシート数式インジェクション対策
        memo = "'" + memo
    return memo
```

---

## 🟡 Medium（計画的に対応）

### M-1: APIキーをリクエストボディ経由で受け付ける設計

**該当ファイル**: `app.py` L2697-2698

```python
rainforest_key = data.get("rainforest_key", "") or os.environ.get("RAINFOREST_API_KEY", "")
openai_key = data.get("openai_key", "") or os.environ.get("OPENAI_API_KEY", "")
```

クライアントから API キーをリクエストボディで渡すことができ、サーバーログに混入するリスクがある。

**対応**: 環境変数のみを使用するよう修正する。
```python
rainforest_key = os.environ.get("RAINFOREST_API_KEY", "")
openai_key = os.environ.get("OPENAI_API_KEY", "")
```

---

### M-2: CORS 設定の欠如

**該当ファイル**: `app.py` 全体

明示的な CORS ミドルウェアが設定されていない。本番 URL が固定される Cloud Run 環境ではオリジンを制限すべき。

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-service-url.run.app"],  # ワイルドカード (*) 禁止
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```

---

### M-3: バッチIDが推測容易

**該当ファイル**: `app.py` L2724

```python
batch_id = now_iso().replace(":", "").replace("-", "") + "_" + uuid.uuid4().hex[:8]
# 例: 20260308T143000+0900_a1b2c3d4  ← タイムスタンプ + 8文字hex
```

タイムスタンプが先頭にあるため、処理時刻の絞り込みが可能。バッチID 全体のエントロピーが低い。

**対応**:
```python
import secrets
batch_id = secrets.token_urlsafe(32)
```

---

### M-4: ログへの機密情報混入リスク

**該当ファイル**: `shopee_core.py` L185-193 (`fetch_amazon_product`)

`api_key` がクエリパラメータとして URL に含まれるため、プロキシログや `requests` デバッグログに API キーが記録される可能性がある。これは Rainforest API の仕様に依存するが、ログ設定の注意が必要。

**対応**: `logging` レベルを本番環境では `INFO` 以上に維持し、HTTPアダプタのデバッグログを無効化する。

---

### M-5: 認証なしのバッチ停止操作

**該当ファイル**: `app.py` L3183-3190

```python
@app.post("/api/batch/{batch_id}/pause")
async def pause_batch(batch_id: str):
    # 認証なし。他者のバッチを停止できる
    BATCH_PAUSE_REQUESTS[batch_id] = True
```

C-2 の認証実装で解決される。

---

## 🟢 Low（余裕があれば対応）

### L-1: エラーメッセージへの内部情報露出

**該当ファイル**: `app.py` L272, L2383, L2565等

例外メッセージが `str(e)` でそのままSSEイベントやHTTPレスポンスに含まれ、ファイルパス・設定値・内部サービスのエンドポイントが漏洩する可能性がある。

---

### L-2: 依存パッケージの脆弱性スキャンが CI に未組み込み

**該当ファイル**: `.github/workflows/deploy.yml`

```yaml
# deploy.yml に追加を推奨
- name: Security audit
  run: pip install pip-audit && pip-audit -r requirements.txt
```

---

### L-3: Dockerfile のベースイメージがダイジェスト固定されていない

**該当ファイル**: `Dockerfile` L1

```dockerfile
FROM python:3.12-slim  # タグのみ。ダイジェスト固定を推奨
```

サプライチェーン攻撃のリスクを低減するため、`python:3.12-slim@sha256:...` の形式でダイジェストを固定することを推奨する。

---

### L-4: /health エンドポイントの情報露出への注意

現在は `{"status": "ok"}` のみであり問題ない。将来的にバージョン情報・設定情報・依存サービスの状態等を追加しないよう注意する。

---

## 発見した課題一覧

| # | 課題 | OWASP分類 | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| C-1 | OAuthシークレットの平文コミット | A02 暗号化の失敗 | 10 | 10 | **100** |
| C-2 | 認証・認可の完全欠如 | A01/A07 | 9 | 10 | **90** |
| H-1 | SSRF（任意URLへのサーバーリクエスト） | A03 インジェクション | 8 | 7 | **56** |
| H-2 | レート制限なし | A04 安全でない設計 | 7 | 8 | **56** |
| H-3 | Drive任意ファイルIDプロキシ | A01 アクセス制御不備 | 7 | 6 | **42** |
| H-4 | CSP/HSTSヘッダー欠如 | A05 設定ミス | 5 | 6 | **30** |
| H-5 | URLバリデーション不足・CSVインジェクション | A03 | 5 | 5 | **25** |
| M-1 | APIキーをリクエストボディで受け付け | A02 | 5 | 5 | **25** |
| M-2 | CORS設定の欠如 | A05 | 4 | 4 | **16** |
| M-3 | バッチIDが推測容易 | A07 | 4 | 4 | **16** |
| M-4 | ログへのAPIキー混入リスク | A09 | 4 | 4 | **16** |
| M-5 | 認証なしのバッチ停止 | A01 | 4 | 5 | **20** |
| L-1 | エラーメッセージへの内部情報露出 | A05 | 3 | 3 | **9** |
| L-2 | 依存パッケージ脆弱性スキャンなし | A09 | 3 | 4 | **12** |
| L-3 | Dockerfileベースイメージ未固定 | A05 | 2 | 2 | **4** |
| L-4 | /healthエンドポイントの情報露出リスク | A05 | 1 | 2 | **2** |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| セキュリティ（20点満点）| **6 / 20** | セキュリティヘッダーの部分実装、ASIN形式バリデーション、パストラバーサル対策（不完全）、Dockerでの非rootユーザー、WIF採用は評価できる。しかし認証機能の完全欠如・OAuthシークレット平文コミット・SSRFリスクが致命的で減点が大きい。 |

### 加点評価
- `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` をミドルウェアで設定（+1）
- ASIN を正規表現 `^[A-Z0-9]{10}$` で厳密に検証（+1）
- `is_relative_to` によるパストラバーサル防止チェックあり（+1）
- Dockerfile で非rootユーザー（uid 1001）を使用（+1）
- deploy.yml で Workload Identity Federation を採用しサービスアカウントキーを GitHub Secrets に置かない（+1）
- Drive クエリ文字列を `_escape_drive_query` でエスケープ（+1）

### 減点評価
- 認証・認可機能が完全に欠如（-6）
- OAuthクライアントシークレットがリポジトリ内に平文存在（-4）
- SSRF対策なし（-2）
- CSP/HSTS未設定（-1）
- APIキーのリクエストボディ受け付け（-1）

---

## デプロイ可否判定

- [x] **Critical項目あり → 修正後に再審査必須**

**現時点でのデプロイは推奨しません。**

C-1（OAuthシークレット）は「デプロイ前」ではなく「現時点で既に存在する脅威」であるため、本日中の対応が必要です。C-2（認証機能の欠如）はCloud Run の IAP（Identity-Aware Proxy）設定で代替可能ですが、その設定が現在確認できていません。少なくとも `--no-allow-unauthenticated` を deploy.yml で明示する必要があります。

---

## 改善提案（コード例付き）

### 提案1: OAuthクライアントシークレットの即時対応

**脆弱性の説明**: `client_secret_*.json` がプロジェクトルートに存在し、Git 履歴に残る可能性がある。

**攻撃シナリオ**: Git リポジトリをクローンまたは過去のコミットを参照することで `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` を取得し、OAuth フローを悪用して Google アカウントへ不正アクセスできる。

**修正手順**:
```bash
# ステップ1: Gitの管理から即座に除外
git rm --cached "client_secret_714089953721-q825i5sic1c716opkpc6afb02mrmpj8m.apps.googleusercontent.com.json"

# ステップ2: .gitignore に追加
echo "client_secret_*.json" >> .gitignore
echo "*.apps.googleusercontent.com.json" >> .gitignore
git add .gitignore

# ステップ3: コミット
git commit -m "security: remove OAuth client secret from repository"

# ステップ4: Google Cloud Console でシークレットを失効
# https://console.cloud.google.com/apis/credentials
# → OAuth 2.0 クライアント ID → 該当クライアント → シークレットを再生成

# ステップ5: Git 履歴からファイルを完全削除（git push 前に必須）
git filter-branch --force --index-filter \
  'git rm --cached --ignore-unmatch "client_secret_*.json"' \
  --prune-empty --tag-name-filter cat -- --all
```

---

### 提案2: deploy.yml への認証フラグ追加

**脆弱性の説明**: Cloud Run サービスが公開アクセス可能な状態になっている可能性がある。

**攻撃シナリオ**: 認証なしで誰でも `/process-stream` を呼び出せ、API クレジットを消費させられる。

**修正方法**:
```yaml
# .github/workflows/deploy.yml
- name: Deploy to Cloud Run
  run: |
    gcloud run deploy ${{ secrets.CLOUD_RUN_SERVICE }} \
      --source . \
      --region ${{ secrets.GCP_REGION }} \
      --project ${{ secrets.GCP_PROJECT_ID }} \
      --no-allow-unauthenticated \
      --quiet

# セキュリティスキャンをデプロイ前に追加
- name: Security audit
  run: |
    pip install pip-audit
    pip-audit -r requirements.txt
```

---

### 提案3: SSRF 対策の実装

**脆弱性の説明**: `/add-images` エンドポイントがユーザー指定 URL にサーバーからHTTPリクエストを発行する。

**攻撃シナリオ**: `http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token` を指定することで、Cloud Run のサービスアカウントトークンを窃取できる。

**修正方法（shopee_core.py の download_supplemental_images を修正）**:
```python
import ipaddress
from urllib.parse import urlparse

def _validate_external_url(url: str) -> bool:
    """SSRF対策: 内部アドレスへのアクセスをブロックする"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        if not host:
            return False
        blocked_hosts = {
            "169.254.169.254", "metadata.google.internal", "metadata.goog",
            "localhost", "127.0.0.1", "::1",
        }
        if host.lower() in blocked_hosts:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # ホスト名（ドメイン）は許可
        return True
    except Exception:
        return False


def download_supplemental_images(image_urls, output_dir, start_index=0):
    """追加画像をダウンロード（SSRF対策付き）"""
    images_dir = Path(output_dir) / "images"
    images_dir.mkdir(exist_ok=True)
    paths = []
    for i, url in enumerate(image_urls):
        # SSRF対策バリデーション
        if not _validate_external_url(url):
            logger.warning("SSRF対策: 不正URLをスキップ: %s", url)
            continue
        idx = start_index + i + 1
        ext = "png" if ".png" in url.lower() else "jpg"
        filepath = images_dir / f"{idx:02d}_sup.{ext}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            # Content-Type が画像であることを確認
            content_type = resp.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                logger.warning("非画像レスポンスをスキップ: %s (%s)", url, content_type)
                continue
            filepath.write_bytes(resp.content)
            _ensure_min_size(filepath)
            paths.append(filepath)
        except Exception:
            continue
    return paths
```

---

## 次のPhaseへの引き継ぎ事項

### 即時対応（本日中）
1. `client_secret_714089953721-*.json` を Git 管理から削除し、Google Cloud Console でシークレットを失効・再発行する
2. Cloud Run サービスの公開アクセス設定を確認し、`--no-allow-unauthenticated` を deploy.yml に明示する

### 1週間以内
3. SSRF対策（`_validate_external_url`）を `download_supplemental_images` および `download_images` に適用する
4. `slowapi` によるレート制限を `/process-stream`, `/regenerate-video`, `/add-images` に実装する
5. `/drive-video/{file_id}` を既知バッチの file_id のみに制限する
6. CSP・HSTS・Permissions-Policy ヘッダーを `SecurityHeadersMiddleware` に追加する

### 1ヶ月以内
7. `pip-audit` を deploy.yml の CI ステップに組み込む
8. APIキーをリクエストボディで受け付ける設計を廃止し、環境変数のみに統一する
9. Amazon URL バリデーションを `/process-stream` に追加する
10. スプレッドシート書き込み時の CSV インジェクション対策（`memo` フィールドのサニタイズ）
11. バッチID を `secrets.token_urlsafe(32)` に変更する
12. Dockerfile のベースイメージをダイジェスト固定にする
