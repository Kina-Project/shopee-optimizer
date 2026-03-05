# Security Auditor レビュー結果

**レビュー対象:** Shopee商品ページ最適化ツール
**レビュー日:** 2026-03-05
**レビュアー:** Security Auditor (CISSP / 10年経験)
**対象ファイル:** app.py, shopee_core.py, Dockerfile, deploy.yml, requirements.txt

---

## 🔴 Critical（即時対応必須）

### C-1: 認証・認可の完全な欠如 (OWASP A01 / A07)

**場所:** `app.py` 全エンドポイント
**説明:** アプリケーション全体に認証・認可機構が一切存在しない。全てのAPIエンドポイント（`/process-stream`, `/regenerate-video`, `/finalize`, `/add-images`, `/search-images`, `/files/{asin}/{path}`, `/batches`, `/batch/{batch_id}`）が匿名アクセス可能。
**影響:**
- 第三者がAPIキー（Rainforest, OpenAI, FAL）を環境変数経由で使い放題（APIコスト発生）
- Google Drive/スプレッドシートへの不正書き込み
- 保存された全商品データ・動画・画像への不正アクセス
- バッチ処理の不正実行によるDoS

**推奨対策:**
```python
# 最低限: APIキーベースの認証ミドルウェア
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    expected = os.environ.get("APP_API_KEY", "")
    if not expected or api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return api_key

# 各エンドポイントに適用
@app.post("/process-stream")
async def process_stream(request: Request, _key: str = Depends(verify_api_key)):
    ...
```

---

### C-2: APIキーをクライアントから受信可能 (OWASP A02 / A07)

**場所:** `app.py` L1575-1576
```python
rainforest_key = data.get("rainforest_key", "") or os.environ.get("RAINFOREST_API_KEY", "")
openai_key = data.get("openai_key", "") or os.environ.get("OPENAI_API_KEY", "")
```
**説明:** `/process-stream`エンドポイントがリクエストボディからAPIキーを受け取る設計になっている。クライアント側のJavaScriptコードではAPIキーを送信していないが、エンドポイント自体がAPIキーをJSON経由で受け取れるため、以下のリスクがある：
- クライアントサイドにAPIキーが露出するアーキテクチャを誘発
- リクエストログにAPIキーが記録される可能性
- キーの一元管理が不可能

**推奨対策:** APIキーはサーバー側の環境変数からのみ取得し、リクエストボディでの受け取りを廃止する。

---

### C-3: CORS設定の完全な欠如 (OWASP A05)

**場所:** `app.py` FastAPIアプリケーション全体
**説明:** CORSMiddlewareが設定されていないため、FastAPIはデフォルトでCORSヘッダーを返さない。現在の設計ではSPAではなくサーバーサイドHTMLレンダリングのため直接の問題は限定的だが、APIエンドポイントが存在する以上、別オリジンからのCSRF攻撃が成立しうる。

**推奨対策:**
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-cloudrun-domain.run.app"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)
```

---

## 🟠 High（早急に対応）

### H-1: レート制限の欠如 (OWASP A04)

**場所:** `app.py` 全エンドポイント
**説明:** レート制限が一切実装されていない。以下の攻撃が可能：
- `/process-stream`の大量呼び出しによるRainforest/OpenAI/fal.aiのAPIクレジット枯渇
- `/search-images`の大量呼び出しによるGoogle CSE APIクレジット枯渇
- `/regenerate-video`の繰り返し呼び出しによるサーバーリソース占有

**推奨対策:**
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/process-stream")
@limiter.limit("5/minute")
async def process_stream(request: Request):
    ...
```

---

### H-2: コマンドインジェクションのリスク (OWASP A03)

**場所:** `shopee_core.py` L416-434, L483-514, L539-546
**説明:** `subprocess.run`で`ffmpeg`/`ffprobe`コマンドを実行している。コマンドはリスト形式で呼び出されているため、直接的なシェルインジェクションリスクは低い。しかし、以下の点に注意が必要：
- `telop_file`（L504）にユーザー起源のテキスト（商品名等）がファイルパスとして使われ、`drawtext=textfile=`パラメータに渡されている。ffmpegのフィルタ式内でのパス解釈に依存。
- `font_path`（L504）が環境変数`FONT_PATH`から取得され、ffmpegコマンドに直接渡されている。

**推奨対策:**
- ffmpegフィルタ式のパス部分に対する入力検証を追加
- テロップテキストのサニタイズ（特殊文字のエスケープ）

---

### H-3: パストラバーサル対策の不完全性 (OWASP A01)

**場所:** `app.py` L1549-1561
```python
@app.get("/files/{asin}/{path:path}")
async def serve_file(asin: str, path: str):
    file_path = OUTPUT_BASE / asin / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not file_path.resolve().is_relative_to(OUTPUT_BASE.resolve()):
        raise HTTPException(status_code=403, detail="Forbidden")
```
**説明:** `is_relative_to`チェックでパストラバーサルを防止しているのは良い設計だが、`asin`パラメータの入力検証がない。`asin`に`..`を含む値が入った場合、`resolve()`前のパス構築段階で意図しないディレクトリにアクセスする可能性がある。実際には`resolve()`+`is_relative_to`で防がれるが、防御の多層化が望ましい。

**推奨対策:**
```python
import re

@app.get("/files/{asin}/{path:path}")
async def serve_file(asin: str, path: str):
    if not re.match(r'^[A-Z0-9]{10}$', asin):
        raise HTTPException(status_code=400, detail="Invalid ASIN format")
    # ...既存のチェック
```

---

### H-4: ハードコードされたスプレッドシートID (OWASP A02)

**場所:** `shopee_core.py` L44-45
```python
"spreadsheet_id": os.environ.get(
    "SPREADSHEET_ID", "1OJKpekPSatqg5ypLc8-R2EkibGORMsQBL9yBPyEJZwc"
),
```
**説明:** Google スプレッドシートIDがデフォルト値としてハードコードされている。これにより：
- ソースコードが公開された場合にスプレッドシートが特定される
- 環境変数未設定時に意図しないスプレッドシートに書き込まれる

**推奨対策:** デフォルト値を空にし、環境変数未設定時はエラーを返す。

---

### H-5: エラーメッセージによる内部情報の露出 (OWASP A05)

**場所:** `app.py` L1631, L1701, L1725, L1772, L1902 等
**説明:** 例外メッセージ（`str(e)`）がそのままSSEイベントやHTTPレスポンスとしてクライアントに返されている。スタックトレースやファイルパス、API応答の詳細がクライアントに露出する可能性がある。

```python
yield emit({"type": "error", "index": idx, "step": 1, "message": str(e)})
raise HTTPException(status_code=500, detail=f"再生成失敗: {e}")
```

**推奨対策:** ユーザー向けには汎用メッセージを返し、詳細はサーバーログにのみ記録する。

---

## 🟡 Medium（計画的に対応）

### M-1: CSRF保護の欠如 (OWASP A01)

**場所:** `app.py` 全POSTエンドポイント
**説明:** CSRFトークンが実装されていない。認証が存在しない現時点では優先度は低いが、認証実装後に必須となる。

---

### M-2: SSRF（Server-Side Request Forgery）リスク (OWASP A10)

**場所:** `shopee_core.py` L275-291 (`download_supplemental_images`), `app.py` L2062-2104 (`/add-images`)
**説明:** `/add-images`エンドポイントでユーザーが指定した任意のURLから画像をダウンロードする。内部ネットワーク（`169.254.169.254`等のメタデータサーバー、`localhost`、プライベートIP）へのリクエストが可能。

**推奨対策:**
```python
from urllib.parse import urlparse
import ipaddress

def is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass  # hostname is a domain, not IP
    return True
```

---

### M-3: 入力サイズの制限なし (OWASP A04)

**場所:** `app.py` L1564-1567
**説明:** `/process-stream`のリクエストボディサイズに制限がない。`MAX_BATCH_SIZE = 10`でURL数は制限されているが、各URLの長さやJSON全体のサイズは制限されていない。

---

### M-4: 依存関係のバージョンピニング不足

**場所:** `requirements.txt`
**説明:** 全依存関係が`>=`で最小バージョンのみ指定されている。将来的に脆弱なバージョンが自動インストールされるリスクがある。

```
requests>=2.31
openai>=1.0
fastapi>=0.115
```

**推奨対策:** `requirements.txt`にはピンされたバージョンを使用する（例: `requests==2.32.3`）。または `pip-compile` などのツールでロックファイルを生成する。

---

### M-5: Dockerfileのセキュリティ強化不足 (OWASP A05)

**場所:** `Dockerfile`
**説明:**
1. **rootユーザーで実行**: `USER`ディレクティブがないため、コンテナがroot権限で動作する
2. **ヘルスチェック未定義**: `HEALTHCHECK`命令がない
3. **マルチステージビルド未使用**: 不要なビルドツールがランタイムイメージに残る可能性

**推奨対策:**
```dockerfile
RUN adduser --disabled-password --gecos '' appuser
USER appuser

HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8080/health || exit 1
```

---

### M-6: セキュリティヘッダーの欠如 (OWASP A05)

**場所:** `app.py` 全レスポンス
**説明:** 以下のセキュリティヘッダーが設定されていない：
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy`
- `Strict-Transport-Security`
- `Referrer-Policy`

**推奨対策:**
```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

---

### M-7: セキュリティログと監視の不足 (OWASP A09)

**場所:** `app.py`, `shopee_core.py` 全体
**説明:**
- 認証失敗のログがない（認証自体がないため）
- APIコール回数のモニタリングがない
- 異常なバッチサイズや頻度の検出がない
- 構造化ログ（JSON形式）ではなく、Cloud Loggingとの統合が困難

---

## 🟢 Low（余裕があれば対応）

### L-1: デバッグモード制御の不在

**場所:** `app.py` L60-64
**説明:** FastAPIアプリに`debug`パラメータが明示されていない。本番環境で意図せずデバッグモードが有効になるリスクは低いが、明示的に`debug=False`を設定すべき。

---

### L-2: サービスアカウントキーのファイルパスデフォルト値

**場所:** `shopee_core.py` L40-43
```python
"gcp_key_path": Path(os.environ.get(
    "GCP_KEY_PATH",
    str(Path.home() / ".config" / "gcloud" / "keys" / "mcp-sheets-key.json"),
)),
```
**説明:** GCPサービスアカウントキーのデフォルトパスがハードコードされている。ローカル開発用だが、Docker環境では`Path.home()`がrootホームを指し、存在しないため実害はない。Workload Identityの利用が推奨される。

---

### L-3: インラインHTMLテンプレートのXSS対策

**場所:** `app.py` L198-1178 (INDEX_HTML), L1303-1344 (history_page)等
**説明:** サーバーサイドHTMLレンダリングにおいて、`html.escape()`が適切に使用されており、XSSの直接的な脆弱性は確認されなかった。ただし、巨大なインラインHTMLテンプレートはレビューの困難さを招き、将来的なXSS混入リスクを高める。テンプレートエンジン（Jinja2等）の採用を推奨。

---

### L-4: バッチ状態の永続化がファイルベース

**場所:** `app.py` L173-195
**説明:** バッチ状態がローカルファイルシステムに保存されている。Cloud Runのインスタンスが再起動するとデータが失われる。セキュリティ上の直接的リスクは低いが、データ整合性の問題がある。

---

### L-5: `/health` エンドポイントの情報露出

**場所:** `app.py` L1186-1188
**説明:** 現在は`{"status": "ok"}`のみで問題ないが、将来的にバージョン情報等を追加しないよう注意が必要。

---

## 発見した課題一覧

| # | 課題 | OWASP分類 | 重要度(1-10) | 影響範囲(1-10) | 優先度スコア |
|---|------|----------|------------|--------------|------------|
| C-1 | 認証・認可の完全な欠如 | A01, A07 | 10 | 10 | 100 |
| C-2 | APIキーをクライアントから受信可能 | A02, A07 | 9 | 8 | 72 |
| C-3 | CORS設定の欠如 | A05 | 8 | 7 | 56 |
| H-1 | レート制限の欠如 | A04 | 8 | 8 | 64 |
| H-2 | コマンドインジェクションのリスク | A03 | 7 | 6 | 42 |
| H-3 | パストラバーサル対策の不完全性 | A01 | 6 | 7 | 42 |
| H-4 | ハードコードされたスプレッドシートID | A02 | 6 | 5 | 30 |
| H-5 | エラーメッセージによる内部情報露出 | A05 | 6 | 6 | 36 |
| M-1 | CSRF保護の欠如 | A01 | 5 | 5 | 25 |
| M-2 | SSRFリスク | A10 | 7 | 6 | 42 |
| M-3 | 入力サイズの制限なし | A04 | 5 | 5 | 25 |
| M-4 | 依存関係のバージョンピニング不足 | - | 5 | 6 | 30 |
| M-5 | Dockerfileのセキュリティ強化不足 | A05 | 5 | 5 | 25 |
| M-6 | セキュリティヘッダーの欠如 | A05 | 5 | 5 | 25 |
| M-7 | セキュリティログと監視の不足 | A09 | 5 | 6 | 30 |
| L-1 | デバッグモード制御の不在 | A05 | 3 | 3 | 9 |
| L-2 | サービスアカウントキーパスのデフォルト値 | A02 | 3 | 3 | 9 |
| L-3 | インラインHTMLテンプレート管理 | A03 | 3 | 4 | 12 |
| L-4 | ファイルベースの状態永続化 | - | 2 | 3 | 6 |
| L-5 | /healthの情報露出リスク | A05 | 2 | 2 | 4 |

---

## 評価スコア

| 軸 | スコア | 根拠 |
|---|-------|------|
| セキュリティ（20点満点） | 5 | 認証・認可の完全欠如が致命的。パストラバーサル対策やXSSエスケープなど部分的な対策は見られるが、アプリケーション全体として最低限の認証すら実装されていない。OWASP Top10のA01（アクセス制御の不備）、A02（暗号化の失敗）、A04（安全でない設計）、A05（セキュリティ設定ミス）、A07（認証の不備）、A09（ログ不足）に該当する問題が多数存在する。唯一の加点要素はファイル配信時のパストラバーサル防止とHTML出力時の`html.escape()`使用。 |

---

## デプロイ可否判定

**判定: デプロイ不可（ブロッカーあり）**

以下のCritical項目が解消されるまで、パブリックインターネットへのデプロイは行うべきではない：

1. **C-1: 認証の実装** - 最低限、APIキーまたはIAP（Identity-Aware Proxy）による認証が必須
2. **C-2: クライアントからのAPIキー受信廃止** - サーバー側環境変数からのみ取得する設計に変更
3. **C-3: CORS設定** - 許可オリジンの明示的指定

**暫定的な緩和策（すぐに本番対応できない場合）：**
- Cloud RunのIAP（Identity-Aware Proxy）を有効化し、認証をインフラ層で担保する
- Cloud Runのingressを`internal`に設定し、VPC内からのみアクセス可能にする
- Cloud ArmorのWAFルールでレート制限を適用する

---

## 改善提案（コード例付き）

### 1. 認証ミドルウェアの追加（最優先）

```python
# app.py の先頭付近に追加
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader
import secrets

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str | None = Security(API_KEY_HEADER)):
    expected = os.environ.get("APP_API_KEY", "")
    if not expected:
        logger.warning("APP_API_KEY is not configured - rejecting all requests")
        raise HTTPException(status_code=500, detail="Server misconfigured")
    if not api_key or not secrets.compare_digest(api_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return api_key
```

### 2. SSRFプロテクション

```python
# shopee_core.py に追加
import ipaddress
import socket
from urllib.parse import urlparse

def validate_url_not_internal(url: str) -> bool:
    """URLが内部ネットワークを指していないことを確認"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        resolved = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except socket.gaierror:
        return False
    return True
```

### 3. Dockerfile の堅牢化

```dockerfile
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core curl && \
    rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos '' --uid 1001 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shopee_core.py app.py ./
COPY BGM/Vanilla.mp3 ./BGM/Vanilla.mp3

RUN mkdir -p /app/output && chown appuser:appuser /app/output

ENV OUTPUT_BASE=/app/output
ENV FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
ENV BGM_PATH=/app/BGM/Vanilla.mp3
ENV PORT=8080

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 4. 構造化ログの導入

```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": self.formatTime(record),
            "module": record.module,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.handlers = [handler]
```

---

## 次のPhaseへの引き継ぎ事項

### Phase 2（認証・認可実装）への引き継ぎ
- [ ] 認証方式の選定: Cloud IAP vs アプリケーション層APIキー vs OAuth2
- [ ] 認証実装後のCSRF保護追加
- [ ] セッション管理の設計（ステートレスJWTかサーバーサイドセッションか）
- [ ] 管理者/一般ユーザーのロール分離が必要かの検討

### Phase 3（堅牢化）への引き継ぎ
- [ ] レート制限の実装（Cloud Armor or アプリケーション層）
- [ ] SSRF対策の実装
- [ ] セキュリティヘッダーの追加
- [ ] 依存関係のバージョンロック（pip-compile）
- [ ] Dockerfileの非rootユーザー化
- [ ] 構造化ログの導入とCloud Monitoring連携

### Phase 4（運用監視）への引き継ぎ
- [ ] Cloud Logging/Monitoringのアラート設定
- [ ] APIクレジット消費の異常検知
- [ ] 定期的な依存関係脆弱性スキャン（Dependabot/Snyk）
- [ ] Webアプリケーションの定期ペネトレーションテスト

### コードアーキテクチャに関する注意
- `app.py`内にHTMLテンプレートが1178行インラインで記述されており、セキュリティレビューの困難さを著しく高めている。テンプレートの外部ファイル化（Jinja2等）を強く推奨する。
- `BATCH_STORE`がインメモリ辞書であり、Cloud Runのスケールアウト時にインスタンス間で状態が共有されない。Redis/Firestore等の外部ストアへの移行を推奨する。

### deploy.yml のセキュリティ評価
- GitHub Secretsの使用: 適切。GCPプロジェクトID、リージョン、Workload Identity Federation情報が全てSecretsから取得されている。
- Workload Identity Federation: `id-token: write`パーミッションでOIDCトークンベースの認証を使用しており、サービスアカウントキーのハードコードを回避している。これは良い設計。
- ただし、`gcloud run deploy`にシークレット環境変数（`RAINFOREST_API_KEY`等）の設定方法が記述されていない。これらが別途手動で設定されているか、Secret Managerから注入されているかを確認する必要がある。
