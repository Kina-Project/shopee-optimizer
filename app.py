"""
Shopee商品ページ最適化ツール - Web API（Cloud Run用）
"""

import json
import os
import logging
import re
import shutil
import time
import uuid
import html
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from shopee_core import (
    EFFECT_PROMPTS,
    QuotaExhaustedError,
    append_video_generation_log,
    fetch_product_sheet_history,
    fetch_video_generation_history,
    get_folder_first_image_thumbnail,
    extract_asin,
    fetch_amazon_product,
    download_images,
    translate_product,
    translate_images,
    translate_image_text,
    has_japanese_text,
    generate_video,
    ensure_drive_folder,
    list_drive_folder_images,
    upload_images_to_drive,
    upload_translated_images_to_drive,
    upload_video_to_drive,
    upload_to_drive,
    upload_file_to_drive_folder,
    write_to_spreadsheet,
    get_config,
    search_google_images,
    download_supplemental_images,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OUTPUT_BASE = Path(os.environ.get("OUTPUT_BASE", str(Path(__file__).parent / "output")))
BATCH_STATE_DIR = OUTPUT_BASE / "_batches"
MAX_BATCH_SIZE = 10
EST_PER_PRODUCT_SEC = 165

BATCH_STORE = {}
BATCH_PAUSE_REQUESTS: dict[str, bool] = {}

STEP_NAMES = {
    1: "Amazon商品情報を取得",
    2: "画像ダウンロード",
    3: "英語翻訳 + 逆翻訳",
    4: "画像テキスト英語化",
    5: "動画生成",
    6: "Google Driveアップロード",
    7: "スプレッドシート書き込み",
}


def can_restart_from(product_state: dict, step: int) -> bool:
    """指定ステップから再開可能かを判定する。Step 3〜7のみ対応。"""
    if step < 3 or step > 7:
        return False
    asin = product_state.get("asin", "")
    if not asin:
        return False

    # Step 3以降: Step 1-2の成果物（product + image_paths）が必要
    product = product_state.get("product")
    image_paths = product_state.get("image_paths", [])
    if not product or not image_paths:
        return False
    # 画像ファイルが実在するか確認
    if not any(Path(p).exists() for p in image_paths):
        return False

    # Step 3: 翻訳からやるので product+images があれば OK
    if step == 3:
        return True
    # Step 4以降: 翻訳済み（title_en）が必要
    if not product.get("title_en"):
        return False
    if step == 4:
        return True

    # Step 5以降: title_en があれば OK（動画生成は翻訳データがあれば可能）
    if step <= 5:
        return True

    # Step 6: 動画ファイルが実在する必要がある
    if step == 6:
        videos = product_state.get("videos", [])
        return any(Path(v.get("video_path", "")).exists() for v in videos)

    # Step 7: Drive folder URL が必要
    if step == 7:
        return bool(product_state.get("drive_folder_url"))

    return False


def find_batch_for_asin(asin: str) -> tuple[str, dict] | tuple[None, None]:
    """BATCH_STOREからASINを含む最新バッチを検索して返す。"""
    best_batch_id = None
    best_product = None
    best_time = ""
    for bid, batch in BATCH_STORE.items():
        for p in batch.get("results", []):
            if p.get("asin") == asin:
                created = p.get("created_at", "") or batch.get("created_at", "")
                if created > best_time:
                    best_time = created
                    best_batch_id = bid
                    best_product = p
    if best_batch_id:
        return best_batch_id, best_product
    return None, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    BATCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    load_batch_store()
    logger.info("Shopee Optimizer API starting...")
    yield
    logger.info("Shopee Optimizer API shutting down...")


app = FastAPI(
    title="Shopee商品ページ最適化API",
    version="2.0.0",
    lifespan=lifespan,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


JST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def parse_drive_folder_id(folder_url: str) -> str:
    if not folder_url:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", folder_url)
    return m.group(1) if m else ""


def next_video_version(existing_videos: list[dict]) -> str:
    return f"v{len(existing_videos) + 1}"


def to_rel_video_url(asin: str, video_path: Path) -> str:
    rel = video_path.relative_to(OUTPUT_BASE / asin)
    return f"/files/{asin}/{rel.as_posix()}"


def to_rel_file_url(asin: str, file_path: str) -> str:
    p = Path(file_path)
    base = OUTPUT_BASE / asin
    try:
        rel = p.relative_to(base)
    except Exception:
        return ""
    return f"/files/{asin}/{rel.as_posix()}"


def rel_image_urls_from_paths(asin: str, paths: list[str]) -> list[str]:
    urls = []
    for p in paths:
        name = Path(p).name
        if name:
            urls.append(f"/files/{asin}/images/{name}")
    return urls


def resolve_selected_image_path(asin: str, selected_image_url: str, image_paths: list[str]) -> Path | None:
    if not image_paths:
        return None
    if not selected_image_url:
        return Path(image_paths[0])
    selected_name = Path(selected_image_url).name
    if not selected_name:
        return Path(image_paths[0])
    for p in image_paths:
        if Path(p).name == selected_name:
            return Path(p)
    return Path(image_paths[0])


def ensure_drive_parent_folder_config(config: dict):
    folder_id = (config.get("drive_parent_folder_id") or "").strip()
    if not folder_id:
        raise RuntimeError("DRIVE_PARENT_FOLDER_ID が未設定です。初期設定で保存先親フォルダIDを必ず指定してください。")
    # Drive認証の事前チェック（処理開始前に失敗を検知）
    from shopee_core import _get_drive_service
    drive = _get_drive_service(config)
    if not drive:
        raise RuntimeError(
            "Google Drive認証に失敗しました。環境変数を確認してください: "
            "DRIVE_REFRESH_TOKEN, DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET"
        )


def find_local_thumb_url(asin: str) -> str:
    images_dir = OUTPUT_BASE / asin / "images"
    if not images_dir.exists():
        return ""
    for p in sorted(images_dir.glob("*")):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            return f"/files/{asin}/images/{p.name}"
    return ""


def drive_thumb_url(file_id: str) -> str:
    if not file_id:
        return ""
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w400"


def write_step_checkpoint(
    url: str,
    product: dict,
    image_count: int,
    folder_url: str,
    config: dict,
    status: str,
) -> str:
    """各ステップ完了時の中間状態をシートへ保存する。失敗時はエラーメッセージを返す。"""
    try:
        ok = write_to_spreadsheet(
            url,
            product,
            image_count,
            folder_url,
            config,
            status=status,
        )
        if not ok:
            return "スプレッドシートへの中間保存に失敗しました（認証/設定を確認してください）"
        return ""
    except Exception as e:
        return str(e)


def parse_ts(value: str) -> datetime:
    if not value:
        return datetime.min
    s = value.strip()
    patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y-%m-%d",
    ]
    for p in patterns:
        try:
            return datetime.strptime(s, p)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min


def batch_state_path(batch_id: str) -> Path:
    return BATCH_STATE_DIR / f"{batch_id}.json"


def save_batch_state(batch_id: str):
    batch = BATCH_STORE.get(batch_id)
    if not batch:
        return
    path = batch_state_path(batch_id)
    path.write_text(
        json.dumps(batch, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_batch_or_none(batch_id: str):
    """BATCH_STOREから取得し、なければファイルから復元する。"""
    batch = BATCH_STORE.get(batch_id)
    if batch:
        return batch
    path = batch_state_path(batch_id)
    if path.exists():
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            BATCH_STORE[batch_id] = batch
            return batch
        except Exception:
            pass
    return None


def load_batch_store():
    BATCH_STORE.clear()
    if not BATCH_STATE_DIR.exists():
        return
    for p in sorted(BATCH_STATE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:200]:
        try:
            batch = json.loads(p.read_text(encoding="utf-8"))
            batch_id = batch.get("batch_id")
            if batch_id:
                BATCH_STORE[batch_id] = batch
        except Exception:
            continue


INDEX_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shopee商品ページ最適化ツール</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=Noto+Sans+JP:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --c-brand:#ee4d2d;
  --c-brand-dark:#d4391c;
  --c-brand-light:#ff8a65;
  --c-brand-glow:rgba(238,77,45,.18);
  --c-surface:#ffffff;
  --c-surface-raised:#ffffff;
  --c-bg:#f6f7fb;
  --c-bg-subtle:#eceef4;
  --c-text:#1a1d26;
  --c-text-secondary:#5f6577;
  --c-text-muted:#9298a8;
  --c-border:#e2e5ed;
  --c-border-light:#eff1f6;
  --c-success:#16a34a;
  --c-success-bg:#f0fdf4;
  --c-warn:#d97706;
  --c-warn-bg:#fffbeb;
  --c-error:#dc2626;
  --c-error-bg:#fef2f2;
  --c-terminal:#0f1117;
  --radius-sm:8px;
  --radius:12px;
  --radius-lg:16px;
  --radius-xl:20px;
  --shadow-sm:0 1px 3px rgba(0,0,0,.04),0 0 0 1px rgba(0,0,0,.02);
  --shadow:0 2px 12px rgba(0,0,0,.06),0 0 0 1px rgba(0,0,0,.03);
  --shadow-lg:0 8px 30px rgba(0,0,0,.08),0 0 0 1px rgba(0,0,0,.03);
  --shadow-brand:0 4px 14px rgba(238,77,45,.25);
  --font-display:'DM Sans','Noto Sans JP',sans-serif;
  --font-body:'Noto Sans JP','DM Sans',sans-serif;
  --font-mono:'JetBrains Mono',ui-monospace,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font-body);background:var(--c-bg);color:var(--c-text);-webkit-font-smoothing:antialiased;line-height:1.6}

/* === ヘッダー === */
.header{
  background:var(--c-terminal);
  position:relative;overflow:hidden;
  padding:40px 20px 36px;text-align:center;
}
.header::before{
  content:'';position:absolute;inset:0;
  background:
    radial-gradient(ellipse 600px 300px at 20% 80%, rgba(238,77,45,.15), transparent),
    radial-gradient(ellipse 500px 250px at 80% 20%, rgba(255,138,101,.1), transparent);
  pointer-events:none;
}
.header::after{
  content:'';position:absolute;inset:0;
  background:url("data:image/svg+xml,%3Csvg width='40' height='40' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 0h1v1H0z' fill='%23ffffff' fill-opacity='.02'/%3E%3C/svg%3E");
  pointer-events:none;
}
.header-inner{position:relative;z-index:1;max-width:980px;margin:0 auto}
.header h1{
  font-family:var(--font-display);font-size:28px;font-weight:700;
  color:#fff;letter-spacing:-.3px;
  animation:fadeInUp .6s ease-out;
}
.header .subtitle{
  color:rgba(255,255,255,.6);font-size:14px;margin-top:6px;font-weight:400;
  animation:fadeInUp .6s ease-out .1s both;
}
.header .brand-accent{color:var(--c-brand-light);font-weight:600}
.header-nav{margin-top:16px;animation:fadeInUp .6s ease-out .2s both}
.header-nav a{
  display:inline-flex;align-items:center;gap:6px;
  color:rgba(255,255,255,.5);text-decoration:none;font-size:13px;font-weight:500;
  padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.1);
  transition:all .25s;
}
.header-nav a:hover{color:#fff;border-color:rgba(255,255,255,.25);background:rgba(255,255,255,.05)}

@keyframes fadeInUp{
  from{opacity:0;transform:translateY(12px)}
  to{opacity:1;transform:translateY(0)}
}

/* === コンテナ === */
.container{max-width:980px;margin:28px auto;padding:0 20px}

/* === セクションラベル === */
.section-label{
  display:inline-flex;align-items:center;gap:8px;
  font-family:var(--font-display);font-size:13px;font-weight:600;
  text-transform:uppercase;letter-spacing:.8px;color:var(--c-text-muted);
  margin-bottom:14px;
}
.section-label .num{
  width:24px;height:24px;border-radius:50%;
  background:var(--c-brand);color:#fff;
  display:inline-flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:700;
}

/* === カード === */
.card{
  background:var(--c-surface);border-radius:var(--radius-lg);
  box-shadow:var(--shadow);padding:24px;margin-bottom:20px;
  border:1px solid var(--c-border-light);
  animation:cardIn .4s ease-out both;
}
.card:nth-child(2){animation-delay:.08s}
.card:nth-child(3){animation-delay:.16s}
@keyframes cardIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

/* === 入力フォーム === */
textarea{width:100%;min-height:120px;padding:12px 14px;border:1.5px solid var(--c-border);border-radius:var(--radius);font-size:14px;font-family:var(--font-body);transition:all .2s;background:var(--c-bg)}
textarea:focus{outline:none;border-color:var(--c-brand);box-shadow:0 0 0 3px var(--c-brand-glow);background:#fff}
.url-input{width:100%;padding:12px 16px;border:1.5px solid var(--c-border);border-radius:var(--radius);font-size:14px;font-family:var(--font-body);transition:all .2s;background:var(--c-bg)}
.url-input:focus{outline:none;border-color:var(--c-brand);box-shadow:0 0 0 3px var(--c-brand-glow);background:#fff}
.input-meta{display:flex;justify-content:space-between;align-items:center;margin-top:12px}

/* === ボタン === */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:10px 20px;
  background:var(--c-brand);color:#fff;border:none;
  border-radius:var(--radius-sm);cursor:pointer;
  font-family:var(--font-display);font-size:13px;font-weight:600;
  transition:all .2s;box-shadow:var(--shadow-brand);
  position:relative;overflow:hidden;
}
.btn::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,transparent 40%,rgba(255,255,255,.12) 50%,transparent 60%);
  transform:translateX(-100%);transition:transform .4s;
}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(238,77,45,.3)}
.btn:hover::after{transform:translateX(100%)}
.btn:active{transform:translateY(0);box-shadow:0 2px 8px rgba(238,77,45,.2)}
.btn:disabled{background:var(--c-bg-subtle);color:var(--c-text-muted);cursor:not-allowed;box-shadow:none;transform:none}
.btn:disabled::after{display:none}
.btn-ghost{background:transparent;color:var(--c-text-secondary);box-shadow:none;border:1.5px solid var(--c-border)}
.btn-ghost:hover{background:var(--c-bg);border-color:var(--c-text-muted);box-shadow:none;transform:none}
.btn-ghost::after{display:none}
.btn-sm{padding:7px 14px;font-size:12px;border-radius:6px}
.btn-spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}

.hidden{display:none}

/* === ログ === */
#log{
  max-height:260px;overflow:auto;
  background:var(--c-terminal);color:rgba(255,255,255,.7);
  padding:14px 16px;border-radius:var(--radius);
  font-family:var(--font-mono);font-size:12px;line-height:1.6;
  scrollbar-width:thin;margin-top:12px;
  border:1px solid rgba(255,255,255,.06);
}
.log-line{margin-bottom:2px;padding:2px 0}

/* === プロダクトカード === */
.product-card{
  border:1px solid var(--c-border);border-radius:var(--radius-lg);
  padding:20px;margin-bottom:16px;background:var(--c-surface);
  transition:all .25s;
}
.product-card:hover{border-color:var(--c-brand);box-shadow:0 0 0 3px var(--c-brand-glow)}
.product-top{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--c-border-light)}
.product-top strong{font-family:var(--font-display);font-size:15px;letter-spacing:-.2px}

/* === 動画 === */
video{
  width:300px;max-width:100%;border-radius:var(--radius);
  border:none;box-shadow:var(--shadow-lg);
  background:#000;
}

/* === フォーム要素 === */
select,input[type="text"]{
  padding:8px 12px;border:1.5px solid var(--c-border);
  border-radius:var(--radius-sm);font-size:13px;font-family:var(--font-body);
  transition:all .2s;background:var(--c-bg);
}
select:focus,input[type="text"]:focus{outline:none;border-color:var(--c-brand);box-shadow:0 0 0 3px var(--c-brand-glow);background:#fff}
.small{font-size:12px;color:var(--c-text-muted);line-height:1.5}
.badge{
  display:inline-flex;align-items:center;gap:4px;
  padding:4px 12px;border-radius:999px;
  background:var(--c-success-bg);color:var(--c-success);
  font-size:12px;font-weight:600;font-family:var(--font-display);
}
.badge::before{content:'';width:6px;height:6px;border-radius:50%;background:currentColor}

/* === タブUI === */
.tabs{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;margin-bottom:14px;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab-btn{
  border:1.5px solid var(--c-border);background:var(--c-surface);
  border-radius:var(--radius-sm);padding:8px 16px;
  font-family:var(--font-display);font-size:12px;font-weight:500;
  cursor:pointer;white-space:nowrap;transition:all .2s;
  color:var(--c-text-secondary);
}
.tab-btn:hover{background:var(--c-bg);border-color:var(--c-text-muted);color:var(--c-text)}
.tab-btn.active{
  background:var(--c-brand);color:#fff;border-color:var(--c-brand);
  box-shadow:var(--shadow-brand);
}
.tab-btn.done{border-color:var(--c-success);color:var(--c-success);background:var(--c-success-bg)}
.tab-btn.done:hover{background:#dcfce7}
.tab-btn.error{border-color:var(--c-error);color:var(--c-error);background:var(--c-error-bg)}

/* === プログレスパネル === */
.progress-panel{border:1px solid var(--c-border-light);border-radius:var(--radius);padding:16px;margin-bottom:12px;background:var(--c-bg)}
.steps{list-style:none;margin:12px 0 0 0;padding:0}
.steps li{
  display:flex;gap:12px;align-items:center;
  padding:10px 12px;margin-bottom:4px;
  border-radius:var(--radius-sm);font-size:13px;
  transition:background .2s;
}
.steps li:hover{background:rgba(0,0,0,.02)}

/* === ステップドット === */
.step-dot{
  width:18px;height:18px;border-radius:50%;display:inline-flex;
  align-items:center;justify-content:center;flex-shrink:0;
  transition:all .3s;position:relative;
}
.step-pending{background:var(--c-border);box-shadow:inset 0 1px 2px rgba(0,0,0,.06)}
.step-running{background:var(--c-warn);animation:pulseGlow 1.5s ease-in-out infinite}
.step-running::after{
  content:'';position:absolute;inset:-3px;border-radius:50%;
  border:2px solid var(--c-warn);opacity:0;
  animation:pulseRing 1.5s ease-out infinite;
}
.step-done{background:var(--c-success)}
.step-done::after{content:'';width:6px;height:3px;border-left:2px solid #fff;border-bottom:2px solid #fff;transform:rotate(-45deg) translateY(-1px)}
.step-warn{background:var(--c-warn)}
.step-skip{background:var(--c-text-muted)}
.step-error{background:var(--c-error)}

@keyframes pulseGlow{
  0%,100%{box-shadow:0 0 0 0 rgba(217,119,6,.4)}
  50%{box-shadow:0 0 0 8px rgba(217,119,6,0)}
}
@keyframes pulseRing{
  0%{transform:scale(.8);opacity:.6}
  100%{transform:scale(1.6);opacity:0}
}

.input-wrap{border:1px solid var(--c-border-light);border-radius:var(--radius);padding:14px;background:var(--c-bg)}

/* === レビュー画面 === */
.review-main{display:flex;gap:20px;align-items:flex-start;margin-top:14px}
.review-controls{flex:1;min-width:0}
.review-controls .control-row{
  display:flex;gap:8px;flex-wrap:wrap;align-items:center;
  padding:10px 0;border-bottom:1px solid var(--c-border-light);
}
.review-controls .control-row:last-child{border-bottom:none}
.control-label{
  font-family:var(--font-display);font-size:11px;font-weight:600;
  text-transform:uppercase;letter-spacing:.5px;color:var(--c-text-muted);
  min-width:60px;
}
.review-image-grid{
  margin-top:12px;
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(120px,1fr));
  gap:10px;
}
.review-image-item{
  border:1px solid var(--c-border);
  border-radius:10px;
  background:var(--c-bg);
  padding:8px;
}
.review-image-item img{
  width:100%;
  aspect-ratio:1/1;
  object-fit:cover;
  border-radius:8px;
  display:block;
}
.review-image-item.active{
  border-color:var(--c-brand);
  box-shadow:0 0 0 3px var(--c-brand-glow);
  background:#fff;
}
.review-product-panel{margin-top:8px}

/* === 画像不足警告 === */
.image-shortage-warn{
  background:var(--c-warn-bg);border:1px solid #fde68a;border-radius:var(--radius);
  padding:12px 16px;margin:10px 0;
  display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;
  font-size:13px;color:#92400e;
}
.image-shortage-warn::before{
  content:'';width:20px;height:20px;flex-shrink:0;
  background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke-width='2' stroke='%23d97706'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' d='M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z'/%3E%3C/svg%3E") no-repeat center/contain;
}

/* === 画像検索結果 === */
.image-search-results{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.image-search-item{
  display:flex;flex-direction:column;align-items:center;gap:4px;
  cursor:pointer;border:2px solid transparent;border-radius:var(--radius);
  padding:6px;transition:all .2s;background:var(--c-bg);
}
.image-search-item:hover{border-color:var(--c-brand-light);background:#fff}
.image-search-item input:checked+img{outline:3px solid var(--c-brand);outline-offset:2px;border-radius:var(--radius-sm)}
.image-search-item img{width:96px;height:96px;object-fit:cover;border-radius:var(--radius-sm)}
.image-search-item input[type="checkbox"]{margin:0;accent-color:var(--c-brand)}

/* === 確定ボタンエリア === */
.finalize-area{
  margin-top:20px;padding-top:20px;
  border-top:1px solid var(--c-border-light);
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;
}

/* === レスポンシブ === */
@media(max-width:680px){
  .header{padding:28px 16px}
  .header h1{font-size:22px}
  .container{margin:16px auto;padding:0 12px}
  .card{padding:16px;border-radius:var(--radius)}
  .review-main{flex-direction:column}
  video{width:100%}
  .image-shortage-warn{flex-direction:column;align-items:flex-start}
  .input-meta{flex-direction:column;gap:10px;align-items:stretch}
  .input-meta>div{justify-content:flex-end}
  .product-top{flex-direction:column;align-items:flex-start}
}
</style>
</head>
<body>
<div class="header">
  <div class="header-inner">
    <h1>Shopee <span class="brand-accent">Optimizer</span></h1>
    <p class="subtitle">Amazon商品情報の取得 / 翻訳 / AI動画生成 / Drive連携を一括処理</p>
    <div class="header-nav">
      <a href="/history">
        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>
        履歴を見る
      </a>
    </div>
  </div>
</div>
<div class="container">
  <div class="card">
    <div class="section-label"><span class="num">1</span>URL入力</div>
    <div id="inputTabs" class="tabs"></div>
    <div class="input-wrap">
      <input id="tabUrlInput" class="url-input" type="text" placeholder="https://www.amazon.co.jp/dp/XXXXXXXXXX" />
    </div>
    <div class="input-meta">
      <div id="count" class="small">0件入力済み</div>
      <div style="display:flex;gap:8px">
        <button id="addTabBtn" type="button" class="btn btn-ghost btn-sm">+ タブ追加</button>
        <button id="removeTabBtn" type="button" class="btn btn-ghost btn-sm">- タブ削除</button>
        <button id="runBtn" type="button" class="btn">一括実行</button>
      </div>
    </div>
  </div>

  <div class="card hidden" id="progressSec">
    <div class="section-label"><span class="num">2</span>処理進捗</div>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
      <div id="progressText" class="small" style="flex:1"></div>
      <button id="pauseBtn" type="button" class="btn btn-ghost btn-sm hidden" onclick="pauseBatch()" style="color:#dc2626;border-color:#dc2626">一時停止</button>
    </div>
    <div id="productTabs" class="tabs"></div>
    <div id="productPanel" class="progress-panel"></div>
    <div id="log"></div>
  </div>

  <div class="card hidden" id="reviewSec">
    <div class="section-label"><span class="num">3</span>動画レビュー</div>
    <div id="reviewTabs" class="tabs"></div>
    <div id="reviewPanel" class="review-product-panel"></div>
    <div class="finalize-area">
      <button class="btn" onclick="finalizeBatch()">全て確定してスプレッドシート反映</button>
      <div id="finalizeMsg" class="small"></div>
    </div>
  </div>
</div>

<script>
const STEP_EST={1:5,2:3,3:5,4:45,5:90,6:10,7:2};
const STEP_NAMES=['Amazon商品情報を取得','画像ダウンロード','英語翻訳 + 逆翻訳','画像テキスト英語化','動画生成','Google Driveアップロード','スプレッドシート書き込み'];
let currentBatchId='';
let reviewProducts=[];
let activeReviewProductIndex=0;
let startAt=0;
let progressProducts=[];
let activeProductIndex=0;
let inputTabsState=[{url:''}];
let activeInputTabIndex=0;
let lastStreamEventAt=0;
let streamWatchTimer=null;

function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML;}
function logLine(text){
  const log=document.getElementById('log');
  const div=document.createElement('div');
  div.className='log-line';
  div.innerHTML=esc(text);
  log.appendChild(div);
  log.scrollTop=log.scrollHeight;
}
function updateCount(){
  const n=inputTabsState.map(x=>(x.url||'').trim()).filter(Boolean).length;
  document.getElementById('count').textContent=`${n}件入力済み`;
}

function renderInputTabs(){
  const root=document.getElementById('inputTabs');
  root.innerHTML=inputTabsState.map((t,i)=>(
    `<button class="tab-btn ${i===activeInputTabIndex?'active':''}" onclick="setActiveInputTab(${i})">商品${i+1}${(t.url||'').trim()?'':'(未入力)'}</button>`
  )).join('');
  document.getElementById('tabUrlInput').value=inputTabsState[activeInputTabIndex]?.url || '';
  updateCount();
}

function setActiveInputTab(index){
  activeInputTabIndex=index;
  renderInputTabs();
}

function onInputUrlChange(){
  inputTabsState[activeInputTabIndex].url=document.getElementById('tabUrlInput').value;
  updateCount();
}

function syncActiveInputFromField(){
  const el=document.getElementById('tabUrlInput');
  if(!el)return;
  inputTabsState[activeInputTabIndex].url=el.value||'';
  updateCount();
}

function addInputTab(){
  if(inputTabsState.length>=10){alert('最大10タブです');return;}
  inputTabsState.push({url:''});
  activeInputTabIndex=inputTabsState.length-1;
  renderInputTabs();
}

function removeInputTab(){
  if(inputTabsState.length<=1){
    inputTabsState[0].url='';
    activeInputTabIndex=0;
    renderInputTabs();
    return;
  }
  inputTabsState.splice(activeInputTabIndex,1);
  if(activeInputTabIndex>=inputTabsState.length) activeInputTabIndex=inputTabsState.length-1;
  renderInputTabs();
}

function collectInputUrls(){
  syncActiveInputFromField();
  return inputTabsState.map(x=>(x.url||'').trim()).filter(Boolean);
}

function initFromQuery(){
  const params=new URLSearchParams(window.location.search);
  const resumeUrl=params.get('resume_url');
  if(resumeUrl){
    inputTabsState=[{url:resumeUrl}];
    activeInputTabIndex=0;
    renderInputTabs();
  }
}

document.getElementById('tabUrlInput').addEventListener('input',onInputUrlChange);
document.getElementById('tabUrlInput').addEventListener('change',syncActiveInputFromField);
document.getElementById('tabUrlInput').addEventListener('blur',syncActiveInputFromField);
document.getElementById('addTabBtn').addEventListener('click',addInputTab);
document.getElementById('removeTabBtn').addEventListener('click',removeInputTab);
document.getElementById('runBtn').addEventListener('click',runBatch);
renderInputTabs();
initFromQuery();
setTimeout(syncActiveInputFromField, 0);
setTimeout(syncActiveInputFromField, 300);

function makeProgressItem(url=''){
  return {
    url,
    asin:'',
    title:'',
    status:'waiting',
    steps:Array(7).fill('pending'),
    stepMessages:{},
    stepStartedAt:{},
    stepEst:{},
    batchStartedAt:null,
  };
}
let elapsedTimer=null;
function startElapsedTimer(){
  if(elapsedTimer)clearInterval(elapsedTimer);
  elapsedTimer=setInterval(()=>renderActiveProductPanel(),1000);
}
function stopElapsedTimer(){
  if(elapsedTimer){clearInterval(elapsedTimer);elapsedTimer=null;}
}
window.addEventListener('beforeunload',function(e){
  const btn=document.getElementById('runBtn');
  if(btn&&btn.disabled){
    e.preventDefault();
    e.returnValue='処理中です。ページを離れると進行中の処理が中断されます。';
  }
});

function initProgressTabs(urls){
  progressProducts = urls.map(u=>makeProgressItem(u));
  activeProductIndex = 0;
  renderProgressTabs();
  renderActiveProductPanel();
}

function getTabLabel(item, index){
  const base = item.asin || `商品${index+1}`;
  return item.title ? `${base}` : base;
}

function tabClass(item, index){
  let cls='tab-btn';
  if(index===activeProductIndex) cls+=' active';
  if(item.status==='done') cls+=' done';
  if(item.status==='error') cls+=' error';
  return cls;
}

function renderProgressTabs(){
  const root=document.getElementById('productTabs');
  root.innerHTML=progressProducts.map((p,i)=>(
    `<button class="${tabClass(p,i)}" onclick="setActiveProduct(${i})">${esc(getTabLabel(p,i))}</button>`
  )).join('');
}

function setActiveProduct(index){
  activeProductIndex=index;
  renderProgressTabs();
  renderActiveProductPanel();
}

function stepStatusText(status){
  if(status==='running') return '処理中';
  if(status==='done') return '完了';
  if(status==='warn') return '警告';
  if(status==='skip') return 'スキップ';
  if(status==='error') return 'エラー';
  return '待機';
}

function stepStatusStyle(status){
  if(status==='done') return 'color:var(--c-success);font-weight:500';
  if(status==='running') return 'color:var(--c-warn);font-weight:600';
  if(status==='error') return 'color:var(--c-error);font-weight:500';
  if(status==='warn') return 'color:var(--c-warn);font-weight:500';
  return 'color:var(--c-text-muted)';
}

function renderActiveProductPanel(){
  const root=document.getElementById('productPanel');
  const p=progressProducts[activeProductIndex];
  if(!p){
    root.innerHTML='<div class="small">まだ開始されていません</div>';
    return;
  }
  const doneCount=p.steps.filter(s=>s==='done').length;
  const progressPct=Math.round(doneCount/7*100);
  const header=`
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div><strong style="font-family:var(--font-display)">${esc(getTabLabel(p,activeProductIndex))}</strong> <span class="small">${esc(p.url||'')}</span></div>
      <span style="font-family:var(--font-display);font-size:13px;font-weight:600;color:var(--c-brand)">${progressPct}%</span>
    </div>
    <div style="height:3px;background:var(--c-border-light);border-radius:2px;margin-bottom:12px;overflow:hidden">
      <div style="height:100%;width:${progressPct}%;background:var(--c-brand);border-radius:2px;transition:width .4s ease"></div>
    </div>`;
  const steps=STEP_NAMES.map((name,idx)=>{
    const stepNum=idx+1;
    const s=p.steps[idx]||'pending';
    const msg=p.stepMessages[stepNum] ? `<span class="small" style="margin-left:4px">${esc(p.stepMessages[stepNum])}</span>` : '';
    let timeInfo='';
    if(s==='running' && p.stepStartedAt[stepNum]){
      const elapsed=Math.floor((Date.now()-p.stepStartedAt[stepNum])/1000);
      const min=Math.floor(elapsed/60);
      const sec=elapsed%60;
      const elapsedStr=min>0?`${min}分${sec}秒`:`${sec}秒`;
      const estStr=p.stepEst[stepNum]?` / 予測: ${p.stepEst[stepNum]}`:'';
      timeInfo=`<span class="small" style="margin-left:6px;color:var(--c-warn)">${elapsedStr}経過${estStr}</span>`;
    }
    return `<li><span class="step-dot step-${s}"></span><span style="${stepStatusStyle(s)}">Step${idx+1}</span> <span>${esc(name)}</span> <span style="${stepStatusStyle(s)}">${stepStatusText(s)}</span>${timeInfo}${msg}</li>`;
  }).join('');
  // 処理中の注意バナー
  const isRunning=p.steps.some(s=>s==='running');
  const banner=isRunning?'<div style="background:#fef3c7;color:#92400e;padding:8px 12px;border-radius:6px;font-size:12px;margin-bottom:8px;text-align:center">処理中です。ページを更新・移動しないでください。</div>':'';
  root.innerHTML=`${header}${banner}<ul class="steps">${steps}</ul>`;
}

async function pauseBatch(){
  if(!currentBatchId){alert('バッチIDが不明です');return}
  const btn=document.getElementById('pauseBtn');
  if(btn){btn.disabled=true;btn.textContent='停止要求中...';}
  try{
    const res=await fetch(`/api/batch/${encodeURIComponent(currentBatchId)}/pause`,{method:'POST'});
    if(!res.ok){const d=await res.json().catch(()=>({}));throw new Error(d.detail||`HTTP ${res.status}`);}
  }catch(e){
    alert('一時停止リクエスト失敗: '+e.message);
    if(btn){btn.disabled=false;btn.textContent='一時停止';}
  }
}

async function resumeBatch(batchId){
  if(!confirm('バッチ処理を再開しますか？')) return;
  const resumeBar=document.getElementById('batchResumeBar');
  if(resumeBar) resumeBar.remove();
  const btn=document.getElementById('runBtn');
  btn.disabled=true;
  btn.innerHTML='<span class="btn-spinner"></span>再開中...';

  // スキップ状態の商品をpendingに戻す
  for(let i=0;i<progressProducts.length;i++){
    if(progressProducts[i].status==='skipped'){
      progressProducts[i].status='pending';
      progressProducts[i].steps=progressProducts[i].steps.map(s=>s==='skip'?'pending':s);
    }
    // エラーで止まった商品もpendingに戻す
    if(progressProducts[i].status==='error'){
      progressProducts[i].status='pending';
      for(let j=0;j<progressProducts[i].steps.length;j++){
        if(progressProducts[i].steps[j]==='error') progressProducts[i].steps[j]='pending';
      }
    }
  }
  renderProgressTabs();
  renderActiveProductPanel();
  startElapsedTimer();
  document.getElementById('progressText').textContent='バッチ再開中...';
  const pauseBtn2=document.getElementById('pauseBtn');
  if(pauseBtn2){pauseBtn2.classList.remove('hidden');pauseBtn2.disabled=false;pauseBtn2.textContent='一時停止';}

  try{
    const res=await fetch(`/api/batch/${batchId}/resume`,{method:'POST'});
    const reader=res.body.getReader();
    const dec=new TextDecoder();
    let buf='';
    function read(){
      reader.read().then(({done,value})=>{
        if(done){
          stopElapsedTimer();
          btn.disabled=false;btn.textContent='一括処理開始';
          document.getElementById('progressText').textContent='再開処理完了';
          return;
        }
        buf+=dec.decode(value,{stream:true});
        const lines=buf.split('\\n');
        buf=lines.pop()||'';
        for(const line of lines){
          if(!line.startsWith('data: '))continue;
          try{
            const evt=JSON.parse(line.slice(6));
            lastStreamEventAt=Date.now();
            currentBatchId=evt.batch_id||currentBatchId;
            handleEvent(evt);
          }catch(e){}
        }
        read();
      });
    }
    read();
  }catch(e){
    alert('再開エラー: '+e.message);
    btn.disabled=false;btn.textContent='一括処理開始';
    stopElapsedTimer();
  }
}

async function runBatch(){
  const urls=collectInputUrls();
  if(!urls.length){alert('URLを入力してください');return}
  if(urls.length>10){alert('最大10件までです');return}

  startAt=Date.now();
  reviewProducts=[];
  activeReviewProductIndex=0;
  currentBatchId='';
  document.getElementById('reviewTabs').innerHTML='';
  document.getElementById('reviewPanel').innerHTML='';
  document.getElementById('finalizeMsg').textContent='';
  document.getElementById('progressSec').classList.remove('hidden');
  document.getElementById('reviewSec').classList.add('hidden');
  document.getElementById('log').innerHTML='';
  document.getElementById('progressText').textContent='ストリーム接続中...';
  initProgressTabs(urls);

  const btn=document.getElementById('runBtn');
  btn.disabled=true;
  btn.innerHTML='<span class="btn-spinner"></span>処理中...';
  const pauseBtn=document.getElementById('pauseBtn');
  if(pauseBtn){pauseBtn.classList.remove('hidden');pauseBtn.disabled=false;pauseBtn.textContent='一時停止';}
  lastStreamEventAt=Date.now();
  if(streamWatchTimer){clearInterval(streamWatchTimer);}
  streamWatchTimer=setInterval(()=>{
    if(!btn.disabled)return;
    const idle=Math.floor((Date.now()-lastStreamEventAt)/1000);
    if(idle>=15){
      document.getElementById('progressText').textContent=`処理中（更新待ち ${idle}秒）`;
    }
  },5000);

  try{
    const res=await fetch('/process-stream',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({urls})
    });
    const reader=res.body.getReader();
    const decoder=new TextDecoder();
    let buf='';

    while(true){
      const {done,value}=await reader.read();
      if(done)break;
      buf+=decoder.decode(value,{stream:true});
      const lines=buf.split('\\n');
      buf=lines.pop();
      for(const line of lines){
        if(!line.startsWith('data: '))continue;
        try{
          const evt=JSON.parse(line.slice(6));
          lastStreamEventAt=Date.now();
          handleEvent(evt);
        }catch(parseErr){
          console.warn('SSE parse error:',parseErr,line);
        }
      }
    }
  }catch(e){
    logLine('通信エラー: '+e.message);
  }finally{
    if(streamWatchTimer){clearInterval(streamWatchTimer);streamWatchTimer=null;}
    btn.disabled=false;
    btn.textContent='一括実行';
  }
}

function remainingText(sec){
  const m=Math.floor(sec/60);
  const s=sec%60;
  return `残り目安 ${m}分${s}秒`;
}

function handleEvent(evt){
  if(evt.batch_id)currentBatchId=evt.batch_id;
  if(typeof evt.remaining_sec==='number'){
    document.getElementById('progressText').textContent=remainingText(evt.remaining_sec);
  }

  if(evt.type==='product_start'){
    if(!progressProducts[evt.index]) progressProducts[evt.index]=makeProgressItem(evt.url||'');
    progressProducts[evt.index].url=evt.url||progressProducts[evt.index].url;
    progressProducts[evt.index].status='running';
    progressProducts[evt.index].batchStartedAt=Date.now();
    activeProductIndex=evt.index;
    startElapsedTimer();
    renderProgressTabs();
    renderActiveProductPanel();
    logLine(`商品 ${evt.index+1}/${evt.total_products} 開始: ${evt.url}`);
  }else if(evt.type==='step'){
    if(progressProducts[evt.index]){
      progressProducts[evt.index].steps[evt.step-1]='running';
      progressProducts[evt.index].stepMessages[evt.step]='';
      progressProducts[evt.index].stepStartedAt[evt.step]=Date.now();
      if(evt.est) progressProducts[evt.index].stepEst[evt.step]=evt.est;
      if(evt.step===1 && evt.name) progressProducts[evt.index].title=evt.name;
    }
    renderActiveProductPanel();
    logLine(`  [${evt.index+1}] Step${evt.step}: ${evt.name}`);
  }else if(evt.type==='step_done'){
    if(progressProducts[evt.index]){
      const current=progressProducts[evt.index].steps[evt.step-1];
      if(current!=='warn' && current!=='error'){
        progressProducts[evt.index].steps[evt.step-1]='done';
      }
      if(evt.step===1 && evt.data){
        progressProducts[evt.index].asin=evt.data.asin||progressProducts[evt.index].asin;
        progressProducts[evt.index].title=evt.data.title_ja||progressProducts[evt.index].title;
      }
    }
    renderProgressTabs();
    renderActiveProductPanel();
    logLine(`  [${evt.index+1}] Step${evt.step} 完了`);
  }else if(evt.type==='step_skip'){
    if(progressProducts[evt.index]){
      progressProducts[evt.index].steps[evt.step-1]='skip';
      progressProducts[evt.index].stepMessages[evt.step]=evt.reason||'';
    }
    renderActiveProductPanel();
    logLine(`  [${evt.index+1}] Step${evt.step} スキップ`);
  }else if(evt.type==='step_warn'){
    if(progressProducts[evt.index]){
      progressProducts[evt.index].steps[evt.step-1]='warn';
      progressProducts[evt.index].stepMessages[evt.step]=evt.message||'';
    }
    renderActiveProductPanel();
    logLine(`  [${evt.index+1}] 警告: ${evt.message}`);
  }else if(evt.type==='step_progress'){
    if(progressProducts[evt.index]){
      const c=evt.completed, t=evt.total_items;
      const remSec=Math.ceil(evt.est_remaining_sec||0);
      const remMin=Math.floor(remSec/60), remS=remSec%60;
      const remStr=remMin>0?`残り約${remMin}分${remS}秒`:`残り約${remS}秒`;
      progressProducts[evt.index].stepMessages[evt.step]=`${c}/${t}枚完了 (${remStr})`;
      progressProducts[evt.index].stepEst[evt.step]=`${c}/${t}枚`;
    }
    renderActiveProductPanel();
  }else if(evt.type==='quota_exhausted'){
    if(progressProducts[evt.index]){
      if(evt.step>=1 && evt.step<=7){
        progressProducts[evt.index].steps[evt.step-1]='error';
        progressProducts[evt.index].stepMessages[evt.step]=evt.message||'';
      }
      progressProducts[evt.index].status='error';
    }
    renderProgressTabs();
    renderActiveProductPanel();
    logLine(`  [${(evt.index??0)+1}] API残高不足: ${evt.message}`);
    alert(evt.message);
  }else if(evt.type==='batch_stopped'){
    stopElapsedTimer();
    const pb=document.getElementById('pauseBtn');
    if(pb) pb.classList.add('hidden');
    logLine(`バッチ停止: ${evt.message}`);
    for(let i=(evt.stopped_at_index||0)+1;i<progressProducts.length;i++){
      progressProducts[i].status='skipped';
    }
    renderProgressTabs();
    renderActiveProductPanel();
    // 再開ボタンを表示
    const stoppedBatchId=evt.batch_id||currentBatchId;
    if(stoppedBatchId){
      const resumeDiv=document.createElement('div');
      resumeDiv.id='batchResumeBar';
      resumeDiv.style.cssText='background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin:12px 0;text-align:center;';
      const isPaused=evt.message&&evt.message.includes('一時停止');
      resumeDiv.innerHTML=`
        <p style="margin:0 0 10px;color:#92400e;font-weight:600">${esc(evt.message)}</p>
        <button id="resumeBatchBtn" onclick="resumeBatch('${esc(stoppedBatchId)}')"
          style="background:var(--c-brand);color:#fff;border:none;padding:10px 28px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer">
          ${isPaused?'バッチ再開':'チャージ後にバッチ再開'}
        </button>
      `;
      const panel=document.getElementById('progressPanel');
      if(panel) panel.parentNode.insertBefore(resumeDiv,panel.nextSibling);
    }
    const btn=document.getElementById('runBtn');
    if(btn){btn.disabled=false;btn.textContent='一括処理開始';}
  }else if(evt.type==='error'){
    if(progressProducts[evt.index]){
      if(evt.step>=1 && evt.step<=7){
        progressProducts[evt.index].steps[evt.step-1]='error';
        progressProducts[evt.index].stepMessages[evt.step]=evt.message||'';
      }
      progressProducts[evt.index].status='error';
    }
    renderProgressTabs();
    renderActiveProductPanel();
    logLine(`  [${(evt.index??0)+1}] エラー: ${evt.message}`);
  }else if(evt.type==='product_done'){
    reviewProducts.push(evt.data);
    if(progressProducts[evt.index]){
      progressProducts[evt.index].status='done';
      progressProducts[evt.index].asin=evt.asin||progressProducts[evt.index].asin;
      progressProducts[evt.index].title=(evt.data&&evt.data.title_ja)||progressProducts[evt.index].title;
    }
    renderProgressTabs();
    renderActiveProductPanel();
    logLine(`商品 ${evt.index+1} 完了: ${evt.asin}`);
  }else if(evt.type==='all_done'){
    stopElapsedTimer();
    const pb2=document.getElementById('pauseBtn');
    if(pb2) pb2.classList.add('hidden');
    reviewProducts=evt.results||reviewProducts;
    activeReviewProductIndex=0;
    renderReview();
    document.getElementById('reviewSec').classList.remove('hidden');
    const sec=Math.floor((Date.now()-startAt)/1000);
    logLine(`全件完了 (${Math.floor(sec/60)}分${sec%60}秒)`);
  }
}

function findVideo(p, version){
  return (p.videos||[]).find(v=>v.version===version) || (p.videos||[])[0];
}

function getReviewImageUrls(p){
  if(!p) return [];
  const fromUrls = Array.isArray(p.image_urls) ? p.image_urls.filter(Boolean) : [];
  if(fromUrls.length) return fromUrls;
  const fromPaths = Array.isArray(p.image_paths) ? p.image_paths : [];
  return fromPaths
    .map(path=>{
      const name=(path||'').split('/').pop();
      return name ? `/files/${p.asin}/images/${name}` : '';
    })
    .filter(Boolean);
}

function driveImgFallback(img,asin,idx){
  const p=reviewProducts.find(r=>r.asin===asin);
  if(p && Array.isArray(p.drive_image_ids) && p.drive_image_ids[idx]){
    img.onerror=null;
    img.src='https://lh3.googleusercontent.com/d/'+p.drive_image_ids[idx]+'=w400';
  }
}
function driveVideoFallback(video,driveUrl){
  if(!driveUrl)return;
  // driveUrlからファイルIDを抽出してプロキシ経由で再生
  const m=driveUrl.match(/\/d\/([a-zA-Z0-9_-]+)/);
  if(m){
    const proxyUrl='/drive-video/'+m[1];
    video.onerror=function(){
      // プロキシも失敗したらDriveリンクにフォールバック
      const link=document.createElement('a');
      link.href=driveUrl;link.target='_blank';
      link.style.cssText='display:flex;width:300px;height:200px;align-items:center;justify-content:center;background:var(--c-bg);border-radius:var(--radius);color:var(--c-brand);font-size:13px;text-decoration:none;border:1px dashed var(--c-border-light)';
      link.textContent='Driveで動画を再生 →';
      video.parentNode.replaceChild(link,video);
    };
    video.src=proxyUrl;
    return;
  }
  const link=document.createElement('a');
  link.href=driveUrl;link.target='_blank';
  link.style.cssText='display:flex;width:300px;height:200px;align-items:center;justify-content:center;background:var(--c-bg);border-radius:var(--radius);color:var(--c-brand);font-size:13px;text-decoration:none;border:1px dashed var(--c-border-light)';
  link.textContent='Driveで動画を再生 →';
  video.parentNode.replaceChild(link,video);
}

function setActiveReviewProduct(index){
  activeReviewProductIndex=index;
  renderReview();
}

function renderReview(){
  const tabRoot=document.getElementById('reviewTabs');
  const panel=document.getElementById('reviewPanel');
  if(!reviewProducts.length){
    tabRoot.innerHTML='';
    panel.innerHTML='<div class="small">レビュー対象がありません</div>';
    return;
  }
  if(activeReviewProductIndex<0 || activeReviewProductIndex>=reviewProducts.length){
    activeReviewProductIndex=0;
  }

  tabRoot.innerHTML=reviewProducts.map((p,i)=>{
    const label=p.asin||`商品${i+1}`;
    return `<button class="tab-btn ${i===activeReviewProductIndex?'active':''}" onclick="setActiveReviewProduct(${i})">${esc(label)}</button>`;
  }).join('');

  const p=reviewProducts[activeReviewProductIndex];
  const selected=p.selected_version || (p.videos?.[0]?.version || '');
  const v=findVideo(p, selected);
  const imageUrls=getReviewImageUrls(p);
  const selectedImage=p.selected_image_url || v?.source_image_url || imageUrls[0] || '';
  p.selected_image_url=selectedImage;

  const shortageHtml = p.image_shortage ? `
    <div class="image-shortage-warn">
      <span>画像が${p.image_count||0}枚のみ（3枚未満）</span>
      <button class="btn btn-sm" onclick="showImageSearch('${p.asin}')">画像を検索して補完</button>
    </div>
    <div id="imgSearch_${p.asin}" class="hidden" style="padding:12px;background:var(--c-bg);border-radius:var(--radius);margin-bottom:10px">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <input id="imgQuery_${p.asin}" type="text" class="url-input" placeholder="ブランド名 + 商品名で検索" value="${esc((p.brand||'')+ ' ' +(p.title_ja||''))}" style="flex:1" />
        <button class="btn" onclick="searchImages('${p.asin}')">検索</button>
      </div>
      <div id="imgResults_${p.asin}" class="image-search-results"></div>
      <button class="btn btn-sm hidden" id="imgAddBtn_${p.asin}" onclick="addSelectedImages('${p.asin}')">選択した画像を取り込む</button>
    </div>` : '';

  const imgBadge = p.image_shortage
    ? `<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:var(--c-warn-bg);color:var(--c-warn);font-weight:600">${p.image_count||0}枚</span>`
    : `<span style="font-size:11px;padding:2px 8px;border-radius:999px;background:var(--c-bg-subtle);color:var(--c-text-muted);font-weight:500">${p.image_count||0}枚</span>`;

  const imageGridHtml = imageUrls.length ? `
    <div class="control-row" style="align-items:flex-start">
      <span class="control-label">画像選択</span>
      <div style="flex:1;min-width:0">
        <div class="small">再生成に使う画像を選択</div>
        <div class="review-image-grid">
          ${imageUrls.map((u,i)=>`
            <div class="review-image-item ${u===selectedImage?'active':''}">
              <img src="${esc(u)}" alt="image-${i+1}" loading="lazy" onerror="driveImgFallback(this,'${p.asin}',${i})" />
              <button class="btn btn-ghost btn-sm" style="margin-top:6px;width:100%" onclick="selectReviewImage('${p.asin}', ${i})">
                ${u===selectedImage?'選択中':'この画像で再度生成'}
              </button>
            </div>
          `).join('')}
        </div>
      </div>
    </div>` : `
    <div class="control-row">
      <span class="control-label">画像選択</span>
      <span class="small">画像がありません</span>
    </div>`;

  panel.innerHTML=`
    <div class="product-card">
      <div class="product-top">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <strong>${esc(p.asin)}</strong>
          <span class="small" style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.title_ja||'')}</span>
          ${imgBadge}
        </div>
        <div>${p.finalized?'<span class="badge">確定済み</span>':''}</div>
      </div>
      ${shortageHtml}
      <div class="review-main">
        ${v?.video_url?`<video controls src="${esc(v.video_url)}" onerror="driveVideoFallback(this,'${esc(v.drive_file_url||'')}')"></video>`:'<div style="width:300px;height:200px;display:flex;align-items:center;justify-content:center;background:var(--c-bg);border-radius:var(--radius);color:var(--c-text-muted);font-size:13px">動画なし</div>'}
        <div class="review-controls">
          <div class="control-row">
            <span class="control-label">現在</span>
            <span class="small">${esc(selected)} / ${esc(v?.effect||'-')} / ${esc(v?.model||'-')}</span>
          </div>
          <div class="control-row">
            <span class="control-label">演出</span>
            <select id="effect_${p.asin}">
              <option value="zoom">zoom</option>
              <option value="unbox">unbox</option>
              <option value="steam">steam</option>
              <option value="condensation">condensation</option>
              <option value="pickup">pickup</option>
            </select>
            <input id="memo_${p.asin}" type="text" placeholder="再生成メモ" style="min-width:160px;flex:1" />
          </div>
          <div class="control-row">
            <span class="control-label">追加指示</span>
            <input id="prompt_${p.asin}" type="text" placeholder="例: 商品ロゴを正面で見せる" style="min-width:220px;flex:1" />
            <button class="btn btn-sm" onclick="regenerate('${p.asin}')">再生成</button>
          </div>
          ${imageGridHtml}
          <div class="control-row">
            <span class="control-label">確定</span>
            <select id="version_${p.asin}" onchange="selectVersion('${p.asin}', this.value)">
              ${(p.videos||[]).map(x=>`<option value="${x.version}" ${x.version===selected?'selected':''}>${x.version} (${x.effect})</option>`).join('')}
            </select>
          </div>
          <div class="control-row">
            <span class="control-label">Drive</span>
            ${p.drive_folder_url
              ?`<a href="${esc(p.drive_folder_url)}" target="_blank" style="color:var(--c-brand);font-size:13px;text-decoration:none">フォルダを開く &rarr;</a>`
              :'<span class="small">未アップロード</span>'}
          </div>
        </div>
      </div>
    </div>
    <div style="margin-top:16px;padding:16px;background:var(--c-bg);border-radius:var(--radius);border:1px solid var(--c-border)">
      <div style="font-weight:700;font-size:14px;margin-bottom:12px">ステップから再開</div>
      <div id="rc-controls" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
        <select id="rc-step" style="padding:6px 10px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;background:#fff;min-width:220px">
          <option value="" disabled selected>読み込み中...</option>
        </select>
        <select id="rc-effect" style="padding:6px 10px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;background:#fff">
          <option value="zoom">zoom</option><option value="unbox">unbox</option><option value="steam">steam</option><option value="condensation">condensation</option><option value="pickup">pickup</option>
        </select>
        <button id="rc-btn" onclick="startRestartFromMain()" disabled
          style="padding:6px 16px;background:var(--c-brand);color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit">
          再開
        </button>
      </div>
      <div id="rc-progress" style="display:none">
        <div id="rc-steps-list" style="font-size:13px;line-height:2"></div>
      </div>
      <div id="rc-error" style="display:none;color:#dc2626;font-size:13px;margin-top:8px"></div>
    </div>
  `;
  loadRestartableStepsMain(p.asin);
}

function selectReviewImage(asin, imageIndex){
  const p=reviewProducts.find(x=>x.asin===asin);
  if(!p) return;
  const imageUrls=getReviewImageUrls(p);
  if(!imageUrls[imageIndex]) return;
  p.selected_image_url=imageUrls[imageIndex];
  renderReview();
}

function selectVersion(asin, version){
  const p=reviewProducts.find(x=>x.asin===asin);
  if(!p)return;
  p.selected_version=version;
  renderReview();
}

async function regenerate(asin){
  const effect=document.getElementById(`effect_${asin}`).value;
  const memo=document.getElementById(`memo_${asin}`).value;
  const promptExtra=(document.getElementById(`prompt_${asin}`)?.value||'').trim();
  const p=reviewProducts.find(x=>x.asin===asin);
  const selectedImageUrl=p?.selected_image_url || '';
  const res=await fetch('/regenerate-video',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({batch_id:currentBatchId, asin, effect, memo, selected_image_url:selectedImageUrl, prompt_extra:promptExtra})
  });
  const data=await res.json();
  if(!res.ok){alert(data.detail||'再生成に失敗しました');return}
  if(!p)return;
  p.videos=data.videos;
  p.selected_version=data.selected_version;
  p.selected_image_url=data.selected_image_url || p.selected_image_url;
  logLine(`[${asin}] 再生成完了: ${data.selected_version}`);
  renderReview();
}

function showImageSearch(asin){
  const el=document.getElementById(`imgSearch_${asin}`);
  if(el) el.classList.toggle('hidden');
}

async function searchImages(asin){
  const query=document.getElementById(`imgQuery_${asin}`).value.trim();
  if(!query){alert('検索キーワードを入力してください');return}
  const resultsEl=document.getElementById(`imgResults_${asin}`);
  resultsEl.innerHTML='<div class="small">検索中...</div>';
  try{
    const res=await fetch('/search-images',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({query})
    });
    const data=await res.json();
    if(!res.ok){resultsEl.innerHTML=`<div class="small">エラー: ${data.detail||'不明'}</div>`;return}
    const items=data.results||[];
    if(!items.length){resultsEl.innerHTML='<div class="small">結果なし</div>';return}
    resultsEl.innerHTML=items.map((img,i)=>`
      <label class="image-search-item">
        <input type="checkbox" value="${esc(img.url)}" data-asin="${asin}" />
        <img src="${esc(img.thumbnail||img.url)}" alt="${esc(img.title)}" />
        <span class="small">${img.width}x${img.height}</span>
      </label>
    `).join('');
    document.getElementById(`imgAddBtn_${asin}`).classList.remove('hidden');
  }catch(e){
    resultsEl.innerHTML=`<div class="small">通信エラー: ${e.message}</div>`;
  }
}

async function addSelectedImages(asin){
  const checks=document.querySelectorAll(`#imgResults_${asin} input[type="checkbox"]:checked`);
  const urls=Array.from(checks).map(c=>c.value);
  if(!urls.length){alert('画像を選択してください');return}
  try{
    const res=await fetch('/add-images',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({batch_id:currentBatchId, asin, image_urls:urls})
    });
    const data=await res.json();
    if(!res.ok){alert(data.detail||'取り込みに失敗しました');return}
    const p=reviewProducts.find(x=>x.asin===asin);
    if(p){
      p.image_count=data.total_image_count;
      p.image_shortage=data.image_shortage;
      p.image_urls=(p.image_urls||[]).concat(data.new_image_urls||[]);
      if(!p.selected_image_url && p.image_urls.length){
        p.selected_image_url=p.image_urls[0];
      }
    }
    logLine(`[${asin}] ${data.added_count}枚の画像を追加（合計${data.total_image_count}枚）`);
    renderReview();
  }catch(e){
    alert('通信エラー: '+e.message);
  }
}

async function finalizeBatch(){
  if(!currentBatchId){alert('batch_idがありません');return}
  const products=reviewProducts.map(p=>({asin:p.asin, selected_version:p.selected_version||p.videos?.[0]?.version||'v1'}));
  const res=await fetch('/finalize',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({batch_id:currentBatchId, products})
  });
  const data=await res.json();
  if(!res.ok){alert(data.detail||'確定処理に失敗しました');return}
  document.getElementById('finalizeMsg').textContent=`${data.finalized_count}件を確定しました`;
  reviewProducts=data.products || reviewProducts;
  renderReview();
}

async function loadRestartableStepsMain(asin){
  const sel=document.getElementById('rc-step');
  if(!sel||!currentBatchId||!asin) return;
  try{
    const res=await fetch('/api/restartable-steps/'+encodeURIComponent(currentBatchId)+'/'+encodeURIComponent(asin));
    const data=await res.json();
    sel.innerHTML='';
    let hasAvail=false;
    data.steps.forEach(s=>{
      const opt=document.createElement('option');
      opt.value=s.step;
      opt.textContent='Step '+s.step+': '+s.name;
      opt.disabled=!s.available;
      if(s.available&&!hasAvail){opt.selected=true;hasAvail=true;}
      sel.appendChild(opt);
    });
    if(!hasAvail){
      const opt=document.createElement('option');
      opt.value='';opt.textContent='再開可能なステップがありません（Step 1からやり直してください）';
      opt.disabled=true;opt.selected=true;
      sel.insertBefore(opt,sel.firstChild);
    }
    document.getElementById('rc-btn').disabled=!hasAvail;
  }catch(e){
    sel.innerHTML='<option disabled selected>読み込みエラー</option>';
  }
}

function startRestartFromMain(){
  const p=reviewProducts[activeReviewProductIndex];
  if(!p||!currentBatchId) return;
  const step=document.getElementById('rc-step').value;
  const effect=document.getElementById('rc-effect').value;
  if(!step) return;

  document.getElementById('rc-btn').disabled=true;
  document.getElementById('rc-btn').textContent='実行中...';
  document.getElementById('rc-progress').style.display='block';
  document.getElementById('rc-error').style.display='none';
  document.getElementById('rc-steps-list').innerHTML='';

  fetch('/restart-from-step',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({batch_id:currentBatchId,asin:p.asin,from_step:parseInt(step),effect:effect})
  }).then(response=>{
    const reader=response.body.getReader();
    const decoder=new TextDecoder();
    let buf='';
    function read(){
      reader.read().then(({done,value})=>{
        if(done) return;
        buf+=decoder.decode(value,{stream:true});
        const lines=buf.split('\\n');
        buf=lines.pop();
        lines.forEach(line=>{
          if(!line.startsWith('data: ')) return;
          try{
            const ev=JSON.parse(line.slice(6));
            handleRestartEventMain(ev);
          }catch(e){}
        });
        read();
      });
    }
    read();
  }).catch(err=>{
    document.getElementById('rc-error').style.display='block';
    document.getElementById('rc-error').textContent='エラー: '+err.message;
    document.getElementById('rc-btn').disabled=false;
    document.getElementById('rc-btn').textContent='再開';
  });
}

function handleRestartEventMain(ev){
  const list=document.getElementById('rc-steps-list');
  if(ev.type==='step_skip'){
    list.innerHTML+='<div style="color:#9298a8">Step '+ev.step+': '+(ev.name||STEP_NAMES[ev.step-1]||'')+' — <span style="font-style:italic">スキップ</span></div>';
  }else if(ev.type==='step'){
    list.innerHTML+='<div id="rc-row-'+ev.step+'">Step '+ev.step+': '+(ev.name||'')+' — <span style="color:#f59e0b;font-weight:600">実行中...</span></div>';
  }else if(ev.type==='step_done'){
    const row=document.getElementById('rc-row-'+ev.step);
    if(row) row.innerHTML=row.innerHTML.replace(/実行中\\.\\.\\./, '<span style="color:#16a34a;font-weight:600">完了</span>');
  }else if(ev.type==='step_warn'){
    list.innerHTML+='<div style="color:#f59e0b;font-size:12px;padding-left:16px">警告: '+(ev.message||'')+'</div>';
  }else if(ev.type==='step_progress'){
    const row=document.getElementById('rc-row-'+ev.step);
    if(row){
      const c=ev.completed, t=ev.total_items;
      const remSec=Math.ceil(ev.est_remaining_sec||0);
      const remMin=Math.floor(remSec/60), remS=remSec%60;
      const remStr=remMin>0?'残り約'+remMin+'分'+remS+'秒':'残り約'+remS+'秒';
      row.innerHTML='Step '+ev.step+': 画像テキスト英語化 — <span style="color:#f59e0b;font-weight:600">'+c+'/'+t+'枚完了 ('+remStr+')</span>';
    }
  }else if(ev.type==='quota_exhausted'){
    list.innerHTML+='<div style="color:#dc2626;font-weight:700;padding:8px;background:#fef2f2;border-radius:6px;margin:4px 0">API残高不足: '+(ev.message||'')+'</div>';
    document.getElementById('rc-btn').disabled=false;
    document.getElementById('rc-btn').textContent='再開';
    alert(ev.message);
  }else if(ev.type==='error'){
    list.innerHTML+='<div style="color:#dc2626;font-weight:600">Step '+(ev.step||'?')+': エラー — '+(ev.message||'')+'</div>';
    document.getElementById('rc-btn').disabled=false;
    document.getElementById('rc-btn').textContent='再開';
  }else if(ev.type==='all_done'){
    list.innerHTML+='<div style="color:#16a34a;font-weight:700;margin-top:8px">すべて完了しました。3秒後にリロードします...</div>';
    setTimeout(()=>location.reload(),3000);
  }
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/batches")
async def list_batches():
    items = []
    for batch in sorted(BATCH_STORE.values(), key=lambda x: x.get("created_at", ""), reverse=True):
        items.append({
            "batch_id": batch.get("batch_id", ""),
            "created_at": batch.get("created_at", ""),
            "product_count": len(batch.get("results", [])),
            "auto_finalize": batch.get("auto_finalize", False),
        })
    return {"items": items[:100]}


@app.get("/batch/{batch_id}")
async def get_batch(batch_id: str):
    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")
    return batch


@app.get("/history", response_class=HTMLResponse)
async def history_page():
    cfg = get_config()
    video_rows = fetch_video_generation_history(limit=1000, config=cfg)
    product_rows = fetch_product_sheet_history(limit=1000, config=cfg)

    combined = []
    for r in video_rows:
        combined.append({
            "source": "video",
            "asin": r.get("asin", ""),
            "timestamp": r.get("timestamp", ""),
            "url": r.get("product_url", ""),
            "title_ja": "",
            "drive_folder_url": r.get("drive_folder_url", ""),
            "thumb_url": r.get("thumb_url", ""),
        })
    for r in product_rows:
        combined.append({
            "source": "product",
            "asin": r.get("asin", ""),
            "timestamp": r.get("timestamp", ""),
            "url": r.get("url", ""),
            "title_ja": r.get("title_ja", ""),
            "drive_folder_url": r.get("drive_folder_url", ""),
            "thumb_url": "",
        })

    combined.sort(key=lambda x: parse_ts(x.get("timestamp", "")), reverse=True)
    latest_by_asin = {}
    for r in combined:
        asin = r.get("asin", "").strip()
        if asin and asin not in latest_by_asin:
            latest_by_asin[asin] = r

    rows = []
    for asin, r in latest_by_asin.items():
        candidate_thumb = r.get("thumb_url", "")
        if not candidate_thumb:
            candidate_thumb = find_local_thumb_url(asin)
        if not candidate_thumb and r.get("drive_folder_url", ""):
            try:
                candidate_thumb = get_folder_first_image_thumbnail(r.get("drive_folder_url", ""), config=cfg)
            except Exception:
                candidate_thumb = ""
        rows.append({
            "asin": asin,
            "created_at": r.get("timestamp", ""),
            "url": r.get("url", ""),
            "title": r.get("title_ja", ""),
            "drive_folder_url": r.get("drive_folder_url", ""),
            "thumb_url": candidate_thumb,
        })
        if not rows[-1]["thumb_url"]:
            try:
                rows[-1]["thumb_url"] = get_folder_first_image_thumbnail(rows[-1].get("drive_folder_url", ""), config=cfg)
            except Exception:
                rows[-1]["thumb_url"] = rows[-1]["thumb_url"] or find_local_thumb_url(asin)
    rows.sort(key=lambda x: parse_ts(x.get("created_at", "")), reverse=True)

    items_html = []
    for r in rows:
        detail_link = f"/history/asin/{r['asin']}"
        thumb = (
            f"<img src='{html.escape(r['thumb_url'])}' style='width:80px;height:80px;object-fit:contain;background:#fafbfc;border-radius:10px;border:1px solid #e8ebf0;padding:2px'/>"
            if r["thumb_url"] else
            "<div style='width:80px;height:80px;border-radius:10px;border:1.5px dashed #d0d5dd;display:flex;align-items:center;justify-content:center;font-size:11px;color:#9298a8;background:#fafbfc'>No Img</div>"
        )
        items_html.append(
            "<div class='history-item'>"
            f"{thumb}"
            "<div style='flex:1;min-width:0'>"
            f"<div style='font-size:11px;color:#9298a8;font-family:DM Sans,sans-serif;letter-spacing:.3px'>{html.escape(r['created_at'])}</div>"
            f"<div style='font-weight:700;margin:3px 0;font-size:15px;font-family:DM Sans,Noto Sans JP,sans-serif;letter-spacing:-.2px'>{html.escape(r['asin'])}</div>"
            f"<div style='font-size:13px;color:#5f6577;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{html.escape(r.get('title',''))}</div>"
            "<div style='display:flex;gap:12px;margin-top:6px;font-size:12px'>"
            f"<a href='{html.escape(detail_link)}' class='detail-link'>詳細 &rarr;</a>"
            f"{('<a href=\"/?resume_url='+quote(r.get('url',''))+'\" class=\"resume-link\">再開</a>') if r.get('url','') else ''}"
            f"{('<a href=\"'+html.escape(r.get('drive_folder_url',''))+'\" target=\"_blank\" class=\"drive-link\">Drive</a>') if r.get('drive_folder_url','') else ''}"
            "</div>"
            "</div></div>"
        )

    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>履歴 | Shopee Optimizer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Noto Sans JP','DM Sans',sans-serif;background:#f6f7fb;color:#1a1d26;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:980px;margin:24px auto;padding:0 20px}}
.head{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}}
.head h1{{font-family:'DM Sans','Noto Sans JP',sans-serif;font-size:24px;font-weight:700;letter-spacing:-.3px}}
.card{{background:#fff;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,.06),0 0 0 1px rgba(0,0,0,.03);padding:20px}}
a{{color:#ee4d2d;text-decoration:none;transition:color .2s}}
a:hover{{color:#d4391c}}
.history-item{{display:flex;gap:14px;padding:14px;border:1px solid #e8ebf0;border-radius:12px;margin-bottom:10px;transition:all .2s}}
.history-item:hover{{border-color:#ee4d2d;box-shadow:0 0 0 3px rgba(238,77,45,.08)}}
.detail-link{{font-weight:600;font-size:12px}}
.resume-link{{color:#2563eb;font-weight:600;font-size:12px}}
.resume-link:hover{{color:#1d4ed8}}
.drive-link{{color:#5f6577;font-size:12px}}
.drive-link:hover{{color:#ee4d2d}}
.back-link{{display:inline-flex;align-items:center;gap:6px;color:#5f6577;font-size:13px;font-weight:500;padding:6px 14px;border:1px solid #e2e5ed;border-radius:8px;transition:all .2s}}
.back-link:hover{{color:#1a1d26;border-color:#aaa}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>履歴</h1>
    <a href="/" class="back-link">&larr; 新規生成へ</a>
  </div>
  <div class="card">
    {''.join(items_html) if items_html else '<div style="font-size:13px;color:#9298a8;padding:20px;text-align:center">履歴がまだありません</div>'}
  </div>
</div>
</body>
</html>"""
    return page


@app.get("/history/asin/{asin}", response_class=HTMLResponse)
async def history_asin_page(asin: str):
    # バッチストアからASINを検索（再開カード表示用）
    found_batch_id, _found_product = find_batch_for_asin(asin)

    rows = [r for r in fetch_video_generation_history(limit=1000, config=get_config()) if r.get("asin", "") == asin]
    rows.sort(key=lambda x: parse_ts(x.get("timestamp", "")), reverse=True)
    from_product_sheet = False
    if not rows:
        product_rows = [r for r in fetch_product_sheet_history(limit=1000, config=get_config()) if r.get("asin", "") == asin]
        product_rows.sort(key=lambda x: parse_ts(x.get("timestamp", "")), reverse=True)
        if not product_rows:
            raise HTTPException(status_code=404, detail="履歴が見つかりません")
        from_product_sheet = True
        rows = [{
            "timestamp": r.get("timestamp", ""),
            "asin": r.get("asin", ""),
            "product_url": r.get("url", ""),
            "version": "",
            "effect": "",
            "model": "",
            "memo": "",
            "drive_folder_url": r.get("drive_folder_url", ""),
            "drive_file_url": "",
            "selected": "",
        } for r in product_rows]

    first = rows[0]
    images_html = []
    images_dir = OUTPUT_BASE / asin / "images"
    if images_dir.exists():
        for p in sorted(images_dir.glob("*")):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                images_html.append(f"<img src='/files/{html.escape(asin)}/images/{html.escape(p.name)}' style='width:150px;border-radius:10px;border:1px solid #e8ebf0;background:#fafbfc'/>")
    if not images_html:
        folder_id = parse_drive_folder_id(first.get("drive_folder_url", ""))
        if folder_id:
            drive_ids = list_drive_folder_images(folder_id, config=get_config())
            for did in drive_ids:
                images_html.append(f"<img src='https://lh3.googleusercontent.com/d/{html.escape(did)}=w400' style='width:150px;border-radius:10px;border:1px solid #e8ebf0;background:#fafbfc'/>")

    # ローカル動画ファイルを検索
    videos_dir = OUTPUT_BASE / asin / "videos"
    local_videos: dict[str, str] = {}  # version -> url
    if videos_dir.exists():
        for vp in sorted(videos_dir.glob("*.mp4")):
            ver = vp.stem  # e.g. "v1", "v2"
            local_videos[ver] = f"/files/{html.escape(asin)}/videos/{html.escape(vp.name)}"

    # DriveファイルURLからプロキシ再生用URLを生成
    def _drive_proxy_url(drive_url: str) -> str:
        """https://drive.google.com/file/d/{ID}/view → /drive-video/{ID}"""
        m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", drive_url)
        if m:
            return f"/drive-video/{m.group(1)}"
        return ""

    # 最新の動画URLを決定（ローカル優先 → Driveプロキシ）
    latest_video_url = ""
    for r in rows:
        ver = r.get("version", "")
        if ver in local_videos:
            latest_video_url = local_videos[ver]
            break
        if r.get("drive_file_url", ""):
            proxy = _drive_proxy_url(r["drive_file_url"])
            if proxy:
                latest_video_url = proxy
            break

    entries_html = []
    for r in rows:
        vid = r.get("drive_file_url", "")
        ver = r.get("version", "")
        selected_mark = "<span style='color:#16a34a;font-weight:600'>YES</span>" if r.get('selected','').upper()=='YES' else ''
        # 再生ボタン: ローカル → Driveプロキシ → Driveリンク
        play_html = ""
        if ver in local_videos:
            play_html = f"<button onclick=\"playVideo('{html.escape(local_videos[ver])}')\" style=\"background:#ee4d2d;color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:11px;font-weight:600;cursor:pointer\">&#9654; 再生</button>"
        elif vid:
            proxy = _drive_proxy_url(vid)
            if proxy:
                play_html = f"<button onclick=\"playVideo('{html.escape(proxy)}')\" style=\"background:#ee4d2d;color:#fff;border:none;border-radius:6px;padding:4px 10px;font-size:11px;font-weight:600;cursor:pointer\">&#9654; 再生</button>"
        if vid:
            play_html += f" <a href=\"{html.escape(vid)}\" target=\"_blank\" style=\"font-size:11px\">Drive &rarr;</a>"
        entries_html.append(
            "<tr>"
            f"<td>{html.escape(r.get('timestamp',''))}</td>"
            f"<td style='font-weight:600'>{html.escape(ver)}</td>"
            f"<td>{html.escape(r.get('effect',''))}</td>"
            f"<td>{html.escape(r.get('model',''))}</td>"
            f"<td>{html.escape(r.get('memo',''))}</td>"
            f"<td>{play_html}</td>"
            f"<td>{selected_mark}</td>"
            "</tr>"
        )

    page = f"""<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(asin)} | Shopee Optimizer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Noto Sans JP','DM Sans',sans-serif;background:#f6f7fb;color:#1a1d26;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:24px auto;padding:0 20px}}
.card{{background:#fff;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,.06),0 0 0 1px rgba(0,0,0,.03);padding:20px;margin-bottom:14px}}
.card h2{{font-family:'DM Sans','Noto Sans JP',sans-serif;font-size:15px;font-weight:700;margin:0 0 12px 0;color:#1a1d26;letter-spacing:-.2px}}
.grid{{display:flex;gap:10px;flex-wrap:wrap}}
table{{width:100%;border-collapse:collapse;font-size:12px;font-family:'DM Sans','Noto Sans JP',sans-serif}}
th{{background:#f6f7fb;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#5f6577;padding:10px 8px;text-align:left;border-bottom:2px solid #e2e5ed}}
td{{border-bottom:1px solid #eff1f6;padding:10px 8px;color:#5f6577}}
tr:hover td{{background:#fafbfc}}
a{{color:#ee4d2d;text-decoration:none;transition:color .2s}} a:hover{{color:#d4391c}}
.meta-row{{display:flex;gap:8px;align-items:baseline;padding:4px 0}}
.meta-label{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:#9298a8;min-width:80px}}
.meta-val{{font-size:14px;color:#5f6577;word-break:break-all}}
.back-link{{display:inline-flex;align-items:center;gap:6px;color:#5f6577;font-size:13px;font-weight:500;padding:6px 14px;border:1px solid #e2e5ed;border-radius:8px;transition:all .2s}}
.back-link:hover{{color:#1a1d26;border-color:#aaa}}
</style></head><body>
<div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px">
    <h1 style="font-family:DM Sans,Noto Sans JP,sans-serif;font-size:24px;font-weight:700;letter-spacing:-.3px">{html.escape(asin)}</h1>
    <a href="/history" class="back-link">&larr; 履歴一覧</a>
  </div>
  <div class="card">
    <div class="meta-row"><span class="meta-label">作業日</span><span class="meta-val">{html.escape(first.get('timestamp',''))}</span></div>
    <div class="meta-row"><span class="meta-label">URL</span><span class="meta-val">{html.escape(first.get('product_url',''))}</span></div>
    <div class="meta-row"><span class="meta-label">Drive</span><span class="meta-val"><a href="{html.escape(first.get('drive_folder_url',''))}" target="_blank">フォルダを開く &rarr;</a></span></div>
    <div class="meta-row"><span class="meta-label">再開</span><span class="meta-val">{('<a href=\"/?resume_url='+quote(first.get('product_url',''))+'\" style=\"color:#2563eb;font-weight:600\">このURLで再開 &rarr;</a>') if first.get('product_url','') else ''}</span></div>
  </div>
  <div class="card">
    <h2>商品画像</h2>
    <div class="grid">{''.join(images_html) if images_html else '<div style="font-size:12px;color:#9298a8">画像なし</div>'}</div>
  </div>
  <div class="card">
    <h2>動画プレビュー</h2>
    <div id="video-player-area" style="margin-bottom:16px">
      {f'<video id="main-video" src="{html.escape(latest_video_url)}" controls style="max-width:480px;width:100%;border-radius:12px;background:#000"></video>' if latest_video_url else '<div style="font-size:13px;color:#9298a8">動画なし</div>'}
    </div>
    {'<div style="margin-top:16px;padding:16px;background:#f6f7fb;border-radius:12px"><div style="font-weight:600;font-size:13px;margin-bottom:10px">動画を再生成</div><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><select id="regen-effect" style="padding:6px 10px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;background:#fff"><option value="zoom">zoom</option><option value="unbox">unbox</option><option value="steam">steam</option><option value="condensation">condensation</option><option value="pickup">pickup</option></select><input id="regen-memo" type="text" placeholder="メモ" style="padding:6px 10px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;min-width:120px"/><input id="regen-prompt" type="text" placeholder="追加指示（例: 商品を正面から）" style="padding:6px 10px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;flex:1;min-width:180px"/><button id="regen-btn" onclick="regenerateVideo()" style="padding:6px 16px;background:#ee4d2d;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;white-space:nowrap">再生成</button></div><div id="regen-status" style="display:none;margin-top:8px;font-size:12px;color:#5f6577"></div></div>' if found_batch_id else '<div style="margin-top:12px;font-size:12px;color:#9298a8">※ バッチデータが見つからないため再生成は利用できません。メインページから再処理してください。</div>'}
  </div>
  <div class="card">
    <h2>動画生成履歴</h2>
    <div style="font-size:12px;color:#9298a8;margin-bottom:10px">{'※ 動画生成シート未作成のため商品データシート履歴を表示中' if from_product_sheet else ''}</div>
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>日時</th><th>Version</th><th>Effect</th><th>Model</th><th>Memo</th><th>再生</th><th>確定</th></tr></thead>
      <tbody>{''.join(entries_html)}</tbody>
    </table>
    </div>
  </div>
  {_build_restart_card_html(found_batch_id, asin) if found_batch_id else ''}
</div>
<script>
function playVideo(url) {{
  const area = document.getElementById('video-player-area');
  let vid = document.getElementById('main-video');
  if (vid) {{
    vid.src = url;
    vid.play();
  }} else {{
    const v = document.createElement('video');
    v.id = 'main-video';
    v.src = url;
    v.controls = true;
    v.autoplay = true;
    v.style.cssText = 'max-width:480px;width:100%;border-radius:12px;background:#000';
    area.innerHTML = '';
    area.appendChild(v);
  }}
  area.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

async function regenerateVideo() {{
  const btn = document.getElementById('regen-btn');
  const status = document.getElementById('regen-status');
  const effect = document.getElementById('regen-effect').value;
  const memo = document.getElementById('regen-memo').value;
  const promptExtra = document.getElementById('regen-prompt').value;

  btn.disabled = true;
  btn.textContent = '生成中...';
  status.style.display = 'block';
  status.textContent = '動画を生成しています。30〜120秒かかる場合があります...';
  status.style.color = '#f59e0b';

  try {{
    const res = await fetch('/regenerate-video', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        batch_id: {json.dumps(found_batch_id or "")},
        asin: {json.dumps(asin)},
        effect: effect,
        memo: memo,
        prompt_extra: promptExtra
      }})
    }});
    const data = await res.json();
    if (!res.ok) {{
      status.textContent = 'エラー: ' + (data.detail || '再生成に失敗しました');
      status.style.color = '#dc2626';
      return;
    }}
    status.textContent = '再生成完了！3秒後にリロードします...';
    status.style.color = '#16a34a';
    setTimeout(() => location.reload(), 3000);
  }} catch (e) {{
    status.textContent = 'エラー: ' + e.message;
    status.style.color = '#dc2626';
  }} finally {{
    btn.disabled = false;
    btn.textContent = '再生成';
  }}
}}
</script>
</body></html>"""
    return page


def _restart_card_js(batch_id: str, asin: str) -> str:
    """再開カード用JavaScriptを生成する（ブレースエスケープの問題を回避）"""
    safe_bid = json.dumps(batch_id)
    safe_asin = json.dumps(asin)
    return (
        "<script>\n"
        "const BATCH_ID = " + safe_bid + ";\n"
        "const ASIN = " + safe_asin + ";\n"
        "\n"
        "async function loadRestartableSteps() {\n"
        "  try {\n"
        "    const res = await fetch('/api/restartable-steps/' + BATCH_ID + '/' + ASIN);\n"
        "    const data = await res.json();\n"
        "    const sel = document.getElementById('restart-step');\n"
        "    sel.innerHTML = '';\n"
        "    let hasAvailable = false;\n"
        "    data.steps.forEach(s => {\n"
        "      const opt = document.createElement('option');\n"
        "      opt.value = s.step;\n"
        "      opt.textContent = 'Step ' + s.step + ': ' + s.name;\n"
        "      opt.disabled = !s.available;\n"
        "      if (s.available && !hasAvailable) {\n"
        "        opt.selected = true;\n"
        "        hasAvailable = true;\n"
        "      }\n"
        "      sel.appendChild(opt);\n"
        "    });\n"
        "    if (!hasAvailable) {\n"
        "      const opt = document.createElement('option');\n"
        "      opt.value = '';\n"
        "      opt.textContent = '再開可能なステップがありません（Step 1からやり直してください）';\n"
        "      opt.disabled = true;\n"
        "      opt.selected = true;\n"
        "      sel.insertBefore(opt, sel.firstChild);\n"
        "    }\n"
        "    document.getElementById('restart-btn').disabled = !hasAvailable;\n"
        "  } catch (e) {\n"
        "    document.getElementById('restart-step').innerHTML = '<option disabled selected>読み込みエラー</option>';\n"
        "  }\n"
        "}\n"
        "\n"
        "function startRestart() {\n"
        "  const step = document.getElementById('restart-step').value;\n"
        "  const effect = document.getElementById('restart-effect').value;\n"
        "  if (!step) return;\n"
        "\n"
        "  document.getElementById('restart-btn').disabled = true;\n"
        "  document.getElementById('restart-btn').textContent = '実行中...';\n"
        "  document.getElementById('restart-progress').style.display = 'block';\n"
        "  document.getElementById('restart-error').style.display = 'none';\n"
        "  document.getElementById('restart-steps-list').innerHTML = '';\n"
        "\n"
        "  const stepNames = " + json.dumps(STEP_NAMES, ensure_ascii=False) + ";\n"
        "\n"
        "  fetch('/restart-from-step', {\n"
        "    method: 'POST',\n"
        "    headers: {'Content-Type': 'application/json'},\n"
        "    body: JSON.stringify({batch_id: BATCH_ID, asin: ASIN, from_step: parseInt(step), effect: effect})\n"
        "  }).then(response => {\n"
        "    const reader = response.body.getReader();\n"
        "    const decoder = new TextDecoder();\n"
        "    let buf = '';\n"
        "\n"
        "    function read() {\n"
        "      reader.read().then(({done, value}) => {\n"
        "        if (done) return;\n"
        "        buf += decoder.decode(value, {stream: true});\n"
        "        const lines = buf.split('\\n');\n"
        "        buf = lines.pop();\n"
        "        lines.forEach(line => {\n"
        "          if (!line.startsWith('data: ')) return;\n"
        "          try {\n"
        "            const ev = JSON.parse(line.slice(6));\n"
        "            handleRestartEvent(ev, stepNames);\n"
        "          } catch(e) {}\n"
        "        });\n"
        "        read();\n"
        "      });\n"
        "    }\n"
        "    read();\n"
        "  }).catch(err => {\n"
        "    document.getElementById('restart-error').style.display = 'block';\n"
        "    document.getElementById('restart-error').textContent = 'エラー: ' + err.message;\n"
        "    document.getElementById('restart-btn').disabled = false;\n"
        "    document.getElementById('restart-btn').textContent = '再開';\n"
        "  });\n"
        "}\n"
        "\n"
        "function handleRestartEvent(ev, stepNames) {\n"
        "  const list = document.getElementById('restart-steps-list');\n"
        "  if (ev.type === 'step_skip') {\n"
        "    list.innerHTML += '<div style=\"color:#9298a8\">Step ' + ev.step + ': ' + (ev.name || stepNames[ev.step] || '') + ' — <span style=\"font-style:italic\">スキップ</span></div>';\n"
        "  } else if (ev.type === 'step') {\n"
        "    list.innerHTML += '<div id=\"step-row-' + ev.step + '\">Step ' + ev.step + ': ' + (ev.name || '') + ' — <span style=\"color:#f59e0b;font-weight:600\">実行中...</span></div>';\n"
        "  } else if (ev.type === 'step_done') {\n"
        "    const row = document.getElementById('step-row-' + ev.step);\n"
        "    if (row) row.innerHTML = row.innerHTML.replace(/実行中\\.\\.\\./, '<span style=\"color:#16a34a;font-weight:600\">完了</span>');\n"
        "  } else if (ev.type === 'step_warn') {\n"
        "    list.innerHTML += '<div style=\"color:#f59e0b;font-size:12px;padding-left:16px\">警告: ' + (ev.message || '') + '</div>';\n"
        "  } else if (ev.type === 'step_progress') {\n"
        "    var row = document.getElementById('step-row-' + ev.step);\n"
        "    if (row) {\n"
        "      var c = ev.completed, t = ev.total_items;\n"
        "      var remSec = Math.ceil(ev.est_remaining_sec || 0);\n"
        "      var remMin = Math.floor(remSec / 60), remS = remSec % 60;\n"
        "      var remStr = remMin > 0 ? '残り約' + remMin + '分' + remS + '秒' : '残り約' + remS + '秒';\n"
        "      row.innerHTML = 'Step ' + ev.step + ': 画像テキスト英語化 — <span style=\"color:#f59e0b;font-weight:600\">' + c + '/' + t + '枚完了 (' + remStr + ')</span>';\n"
        "    }\n"
        "  } else if (ev.type === 'quota_exhausted') {\n"
        "    list.innerHTML += '<div style=\"color:#dc2626;font-weight:700;padding:8px;background:#fef2f2;border-radius:6px;margin:4px 0\">API残高不足: ' + (ev.message || '') + '</div>';\n"
        "    document.getElementById('restart-btn').disabled = false;\n"
        "    document.getElementById('restart-btn').textContent = '再開';\n"
        "    alert(ev.message);\n"
        "  } else if (ev.type === 'error') {\n"
        "    list.innerHTML += '<div style=\"color:#dc2626;font-weight:600\">Step ' + (ev.step || '?') + ': エラー — ' + (ev.message || '') + '</div>';\n"
        "    document.getElementById('restart-btn').disabled = false;\n"
        "    document.getElementById('restart-btn').textContent = '再開';\n"
        "  } else if (ev.type === 'all_done') {\n"
        "    list.innerHTML += '<div style=\"color:#16a34a;font-weight:700;margin-top:8px\">すべて完了しました。3秒後にリロードします...</div>';\n"
        "    setTimeout(() => location.reload(), 3000);\n"
        "  }\n"
        "}\n"
        "\n"
        "loadRestartableSteps();\n"
        "</script>"
    )


RESTART_CARD_HTML = """<div class="card" id="restart-card">
    <h2>ステップから再開</h2>
    <div id="restart-controls" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px">
      <select id="restart-step" style="padding:8px 12px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;background:#fff;min-width:220px">
        <option value="" disabled selected>読み込み中...</option>
      </select>
      <select id="restart-effect" style="padding:8px 12px;border:1px solid #e2e5ed;border-radius:8px;font-size:13px;font-family:inherit;background:#fff">
        <option value="zoom">zoom</option>
        <option value="unbox">unbox</option>
        <option value="steam">steam</option>
        <option value="condensation">condensation</option>
        <option value="pickup">pickup</option>
      </select>
      <button id="restart-btn" onclick="startRestart()" disabled
        style="padding:8px 20px;background:#ee4d2d;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:background .2s;font-family:inherit">
        再開
      </button>
    </div>
    <div id="restart-progress" style="display:none">
      <div id="restart-steps-list" style="font-size:13px;line-height:2"></div>
    </div>
    <div id="restart-error" style="display:none;color:#dc2626;font-size:13px;margin-top:8px"></div>
  </div>"""


def _build_restart_card_html(batch_id: str, asin: str) -> str:
    """再開カードのHTMLスニペットを生成する"""
    return RESTART_CARD_HTML + "\n" + _restart_card_js(batch_id, asin)


@app.get("/history/{batch_id}/{asin}", response_class=HTMLResponse)
async def history_detail_page(batch_id: str, asin: str):
    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")

    product = next((p for p in batch.get("results", []) if p.get("asin") == asin), None)
    if not product:
        raise HTTPException(status_code=404, detail="asinが見つかりません")

    images_html = []
    for p in product.get("image_paths", []):
        u = to_rel_file_url(asin, p)
        if u:
            images_html.append(f"<img src='{html.escape(u)}' style='width:150px;border-radius:10px;border:1px solid #e8ebf0;background:#fafbfc'/>")

    translated_html = []
    for p in product.get("translated_image_paths", []):
        u = to_rel_file_url(asin, p)
        if u:
            translated_html.append(f"<img src='{html.escape(u)}' style='width:150px;border-radius:10px;border:1px solid #e8ebf0;background:#fafbfc'/>")

    videos_html = []
    for v in product.get("videos", []):
        vu = v.get("video_url", "")
        if vu:
            video_block = f"<video controls src='{html.escape(vu)}' style='width:360px;max-width:100%;border-radius:12px;box-shadow:0 4px 16px rgba(0,0,0,.1)'></video>"
        else:
            video_block = '<div style="font-size:12px;color:#9298a8">動画なし</div>'
        videos_html.append(
            "<div style='padding:14px;border:1px solid #e8ebf0;border-radius:12px;margin-bottom:12px;transition:border-color .2s'>"
            f"<div style='font-size:11px;color:#9298a8;font-family:DM Sans,sans-serif'>{html.escape(v.get('created_at',''))}</div>"
            f"<div style='font-weight:700;font-family:DM Sans,Noto Sans JP,sans-serif;margin:4px 0 6px'>{html.escape(v.get('version',''))} / {html.escape(v.get('effect',''))} / {html.escape(v.get('model',''))}</div>"
            f"<div style='font-size:12px;color:#5f6577;margin-bottom:8px'>{html.escape(v.get('memo',''))}</div>"
            f"{video_block}"
            "</div>"
        )

    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(asin)} | Shopee Optimizer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Noto Sans JP','DM Sans',sans-serif;background:#f6f7fb;color:#1a1d26;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1080px;margin:24px auto;padding:0 20px}}
.card{{background:#fff;border-radius:16px;box-shadow:0 2px 12px rgba(0,0,0,.06),0 0 0 1px rgba(0,0,0,.03);padding:20px;margin-bottom:14px}}
.card h2{{font-family:'DM Sans','Noto Sans JP',sans-serif;font-size:15px;font-weight:700;margin:0 0 12px 0;letter-spacing:-.2px}}
.grid{{display:flex;gap:10px;flex-wrap:wrap}}
a{{color:#ee4d2d;text-decoration:none;transition:color .2s}} a:hover{{color:#d4391c}}
.meta-row{{display:flex;gap:8px;align-items:baseline;padding:4px 0}}
.meta-label{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;color:#9298a8;min-width:80px}}
.meta-val{{font-size:14px;color:#5f6577;word-break:break-all}}
.back-link{{display:inline-flex;align-items:center;gap:6px;color:#5f6577;font-size:13px;font-weight:500;padding:6px 14px;border:1px solid #e2e5ed;border-radius:8px;transition:all .2s}}
.back-link:hover{{color:#1a1d26;border-color:#aaa}}
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px">
    <h1 style="font-family:DM Sans,Noto Sans JP,sans-serif;font-size:24px;font-weight:700;letter-spacing:-.3px">{html.escape(asin)}</h1>
    <a href="/history" class="back-link">&larr; 履歴一覧</a>
  </div>
  <div class="card">
    <div class="meta-row"><span class="meta-label">作成日</span><span class="meta-val">{html.escape(product.get('created_at',''))}</span></div>
    <div class="meta-row"><span class="meta-label">URL</span><span class="meta-val">{html.escape(product.get('url',''))}</span></div>
    <div class="meta-row"><span class="meta-label">タイトル</span><span class="meta-val">{html.escape(product.get('title_ja',''))}</span></div>
    <div class="meta-row"><span class="meta-label">Drive</span><span class="meta-val"><a href="{html.escape(product.get('drive_folder_url',''))}" target="_blank">フォルダを開く &rarr;</a></span></div>
  </div>
  <div class="card">
    <h2>商品画像</h2>
    <div class="grid">{''.join(images_html) if images_html else '<div style="font-size:12px;color:#9298a8">なし</div>'}</div>
  </div>
  <div class="card">
    <h2>英語化画像</h2>
    <div class="grid">{''.join(translated_html) if translated_html else '<div style="font-size:12px;color:#9298a8">なし</div>'}</div>
  </div>
  <div class="card">
    <h2>動画履歴</h2>
    {''.join(videos_html) if videos_html else '<div style="font-size:12px;color:#9298a8">なし</div>'}
  </div>
  {_build_restart_card_html(batch_id, asin)}
</div>
</body>
</html>"""
    return page


# ─── ステップから再開 API ───────────────────────────────────────────

@app.get("/api/restartable-steps/{batch_id}/{asin}")
async def restartable_steps(batch_id: str, asin: str):
    """product_stateを検査し、各ステップの再開可否を返す。"""
    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")

    product = next((p for p in batch.get("results", []) if p.get("asin") == asin), None)
    if not product:
        raise HTTPException(status_code=404, detail="asinが見つかりません")

    steps = []
    for step_num in range(3, 8):
        steps.append({
            "step": step_num,
            "name": STEP_NAMES[step_num],
            "available": can_restart_from(product, step_num),
        })

    return JSONResponse({
        "batch_id": batch_id,
        "asin": asin,
        "steps": steps,
    })


@app.post("/restart-from-step")
async def restart_from_step(request: Request):
    """指定ステップ以降を再実行するSSEストリーミングエンドポイント。"""
    body = await request.body()
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="リクエストのJSON形式が不正です")

    batch_id = data.get("batch_id", "")
    asin = data.get("asin", "")
    try:
        from_step = int(data.get("from_step", 0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="from_stepは整数で指定してください")
    effect = data.get("effect", "zoom")

    if from_step < 3 or from_step > 7:
        raise HTTPException(status_code=400, detail="from_stepは3〜7の範囲で指定してください")

    if effect not in EFFECT_PROMPTS:
        raise HTTPException(status_code=400, detail="無効なeffectです")

    # Drive設定の事前チェック（Step 6を含む場合）
    if from_step <= 6:
        try:
            ensure_drive_parent_folder_config(get_config())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Google Drive設定エラー: {e}")

    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")

    product_state = next((p for p in batch.get("results", []) if p.get("asin") == asin), None)
    if not product_state:
        raise HTTPException(status_code=404, detail="asinが見つかりません")

    if not can_restart_from(product_state, from_step):
        raise HTTPException(status_code=400, detail=f"Step {from_step}から再開できません。必要なデータまたはファイルが不足しています。")

    def generate():
        yield ":" + (" " * 2048) + "\n\n"
        yield "retry: 3000\n\n"

        started = datetime.now()
        est_total = (8 - from_step) * 30  # 大まかな推定

        def emit(payload):
            elapsed = int((datetime.now() - started).total_seconds())
            payload["batch_id"] = batch_id
            payload["remaining_sec"] = max(0, est_total - elapsed)
            return sse_event(payload)

        # 既存データを復元
        product = dict(product_state.get("product", {}))
        image_paths = [str(p) for p in product_state.get("image_paths", [])]
        image_urls = list(product_state.get("image_urls", []))
        translated_image_paths = [str(p) for p in product_state.get("translated_image_paths", [])]
        url = product_state.get("url", "")
        config = get_config()
        output_dir = config["output_base"] / asin
        output_dir.mkdir(parents=True, exist_ok=True)
        videos_dir = output_dir / "videos"
        videos_dir.mkdir(exist_ok=True)

        openai_key = os.environ.get("OPENAI_API_KEY", "")

        # スキップされたステップを通知
        for s in range(1, from_step):
            yield emit({"type": "step_skip", "index": 0, "step": s, "name": STEP_NAMES[s], "reason": "既存データを使用"})

        total = 7

        # --- Step 3: 翻訳 ---
        if from_step <= 3:
            yield emit({"type": "step", "index": 0, "step": 3, "total": total, "name": "英語翻訳 + 逆翻訳", "est": "5秒"})
            try:
                product = translate_product(product)
            except Exception as e:
                logger.warning("Restart Step3 failed for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "error", "index": 0, "step": 3, "message": f"翻訳処理に失敗しました: {e}"})
                return
            yield emit({
                "type": "step_done", "index": 0, "step": 3,
                "data": {
                    "title_en": product.get("title_en", ""),
                    "title_reverse": product.get("title_reverse", ""),
                    "features_en": product.get("features_en", []),
                }
            })
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                len(image_paths),
                product_state.get("drive_folder_url", ""),
                config,
                "Step3完了: 英語翻訳 + 逆翻訳",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": 0, "step": 3, "message": f"シート中間記録エラー: {checkpoint_err}"})

        # --- Step 4: 画像テキスト英語化（1枚ずつ進捗報告） ---
        if from_step <= 4:
            if openai_key:
                total_images = len(image_paths)
                est_per_image = 45
                est = f"約{total_images * est_per_image}秒（{total_images}枚）"
                yield emit({"type": "step", "index": 0, "step": 4, "total": total, "name": "画像テキスト英語化", "est": est})

                en_dir = output_dir / "images_en"
                en_dir.mkdir(exist_ok=True)
                translated_image_paths = []
                consecutive_failures = 0
                max_consecutive_failures = 2
                step4_start = time.time()

                for img_i, img_path in enumerate(image_paths):
                    out_path = en_dir / f"{Path(img_path).stem}_en.png"
                    if not has_japanese_text(openai_key, str(img_path)):
                        logger.info("テキストなし、翻訳スキップ: %s", Path(img_path).name)
                        shutil.copy2(str(img_path), str(out_path))
                        translated_image_paths.append(str(out_path))
                        consecutive_failures = 0
                        elapsed = time.time() - step4_start
                        completed = img_i + 1
                        yield emit({"type": "step_progress", "index": 0, "step": 4, "completed": completed, "total_items": total_images, "elapsed_sec": round(elapsed, 1), "est_remaining_sec": 0})
                        continue
                    try:
                        success = translate_image_text(openai_key, str(img_path), str(out_path), product)
                    except QuotaExhaustedError as e:
                        yield emit({"type": "quota_exhausted", "index": 0, "step": 4, "service": e.service, "message": str(e)})
                        return
                    except Exception as e:
                        logger.warning("画像翻訳エラー (%s): %s", img_path, e)
                        success = False

                    if success:
                        translated_image_paths.append(str(out_path))
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            logger.warning("画像翻訳が%d回連続失敗。残り%d枚をスキップ", max_consecutive_failures, total_images - img_i - 1)
                            yield emit({"type": "step_warn", "index": 0, "step": 4, "message": f"画像翻訳が{max_consecutive_failures}回連続失敗。残り{total_images - img_i - 1}枚をスキップしました。"})
                            break

                    elapsed = time.time() - step4_start
                    completed = img_i + 1
                    if len(translated_image_paths) > 0:
                        avg_per_image = elapsed / completed
                        est_remaining = avg_per_image * (total_images - completed)
                    else:
                        est_remaining = est_per_image * (total_images - completed)
                    yield emit({
                        "type": "step_progress", "index": 0, "step": 4,
                        "completed": completed, "total_items": total_images,
                        "elapsed_sec": round(elapsed, 1),
                        "est_remaining_sec": round(max(0, est_remaining), 1),
                    })

                translated_image_urls = [f"/files/{asin}/images_en/{Path(p).name}" for p in translated_image_paths]
                yield emit({
                    "type": "step_done", "index": 0, "step": 4,
                    "data": {"translated_count": len(translated_image_paths), "translated_image_urls": translated_image_urls}
                })
                checkpoint_err = write_step_checkpoint(
                    url,
                    product,
                    len(image_paths),
                    product_state.get("drive_folder_url", ""),
                    config,
                    "Step4完了: 画像テキスト英語化",
                )
                if checkpoint_err:
                    yield emit({"type": "step_warn", "index": 0, "step": 4, "message": f"シート中間記録エラー: {checkpoint_err}"})
            else:
                yield emit({"type": "step_skip", "index": 0, "step": 4, "name": "画像テキスト英語化", "reason": "OpenAIキー未設定"})

        # --- Step 5: 動画生成 ---
        video_record = None
        if from_step <= 5:
            existing_videos = product_state.get("videos", [])
            version = next_video_version(existing_videos)
            model = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"]).get("model", "hailuo")
            video_path = videos_dir / f"{version}.mp4"

            yield emit({"type": "step", "index": 0, "step": 5, "total": total, "name": "動画生成", "est": "30〜120秒"})
            try:
                out = generate_video(image_paths, product, output_dir, config=config, effect=effect, output_path=video_path)
                video_path = Path(out) if out else None
            except QuotaExhaustedError as e:
                video_path = None
                yield emit({"type": "quota_exhausted", "index": 0, "step": 5, "service": e.service, "message": str(e)})
                return
            except Exception as e:
                video_path = None
                yield emit({"type": "step_warn", "index": 0, "step": 5, "message": f"動画生成失敗: {e}"})

            if video_path and video_path.exists():
                shutil.copy2(video_path, output_dir / "shopee_video.mp4")
                video_record = {
                    "version": version,
                    "effect": effect,
                    "model": model,
                    "memo": "再開による再生成",
                    "prompt_extra": "",
                    "created_at": now_iso(),
                    "video_path": str(video_path),
                    "video_url": to_rel_video_url(asin, video_path),
                    "drive_file_url": "",
                    "source_image_path": str(image_paths[0]) if image_paths else "",
                    "source_image_url": image_urls[0] if image_urls else "",
                }

            yield emit({
                "type": "step_done", "index": 0, "step": 5,
                "data": {"video_url": video_record["video_url"] if video_record else None}
            })
            step5_status = "Step5完了: 動画生成" if video_record else "Step5完了: 動画生成（未生成）"
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                len(image_paths),
                product_state.get("drive_folder_url", ""),
                config,
                step5_status,
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": 0, "step": 5, "message": f"シート中間記録エラー: {checkpoint_err}"})
        else:
            # Step 6以降から開始の場合、既存の最新動画を使う
            existing_videos = product_state.get("videos", [])
            if existing_videos:
                video_record = existing_videos[-1]

        # --- Step 6: Google Driveアップロード ---
        folder_url = product_state.get("drive_folder_url", "")
        folder_id = product_state.get("drive_folder_id", "")
        thumb_url = product_state.get("drive_thumb_url", "")
        if from_step <= 6:
            yield emit({"type": "step", "index": 0, "step": 6, "total": total, "name": "Google Driveアップロード", "est": "10秒"})
            vp = Path(video_record["video_path"]) if video_record and video_record.get("video_path") else None
            try:
                drive_meta = upload_to_drive(
                    asin, image_paths, translated_image_paths, vp, config, return_meta=True
                )
                folder_url = drive_meta.get("folder_url", "") or folder_url
                folder_id = drive_meta.get("folder_id", "") or parse_drive_folder_id(folder_url)
                if not folder_url and folder_id:
                    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
                image_items = drive_meta.get("image_items", [])
                if image_items:
                    thumb_url = drive_thumb_url(image_items[0].get("id", ""))
                if video_record:
                    video_item = drive_meta.get("video_item", {}) or {}
                    video_record["drive_file_url"] = video_item.get("webViewLink", "")
                    if not video_record["drive_file_url"] and vp:
                        raise RuntimeError("動画ファイルのDrive保存に失敗しました")
                if not folder_url:
                    raise RuntimeError("DriveフォルダURLが取得できません")
            except Exception as e:
                logger.warning("Restart Step6 Drive upload failed for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "error", "index": 0, "step": 6, "message": f"Google Driveアップロード失敗: {e}"})
                return

            yield emit({"type": "step_done", "index": 0, "step": 6, "data": {"drive_folder_url": folder_url}})
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                len(image_paths),
                folder_url,
                config,
                "Step6完了: Google Driveアップロード",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": 0, "step": 6, "message": f"シート中間記録エラー: {checkpoint_err}"})

            if video_record:
                try:
                    append_video_generation_log(
                        asin=asin,
                        product_url=url,
                        version=video_record.get("version", ""),
                        effect=video_record.get("effect", ""),
                        model=video_record.get("model", ""),
                        memo=video_record.get("memo", ""),
                        drive_folder_url=folder_url,
                        drive_file_url=video_record.get("drive_file_url", ""),
                        thumb_url=thumb_url,
                        is_selected=False,
                        config=config,
                    )
                except Exception:
                    pass

        # --- Step 7: スプレッドシート書き込み ---
        if from_step <= 7:
            yield emit({"type": "step", "index": 0, "step": 7, "total": total, "name": "スプレッドシート書き込み", "est": "2秒"})
            try:
                write_to_spreadsheet(url, product, len(image_paths), folder_url, config, status="完了")
                yield emit({"type": "step_done", "index": 0, "step": 7, "data": {"finalized": True}})
            except Exception as e:
                yield emit({"type": "step_warn", "index": 0, "step": 7, "message": f"スプレッドシートエラー: {e}"})
                yield emit({"type": "step_done", "index": 0, "step": 7, "data": {"finalized": False}})

        # product_state を更新して永続化
        product_state["product"] = product
        product_state["translated_image_paths"] = translated_image_paths
        product_state["drive_folder_url"] = folder_url
        product_state["drive_folder_id"] = folder_id
        product_state["drive_thumb_url"] = thumb_url
        if video_record and from_step <= 5:
            # 新しく生成した動画を追加
            if "videos" not in product_state:
                product_state["videos"] = []
            product_state["videos"].append(video_record)
            product_state["selected_version"] = video_record["version"]
        product_state["finalized"] = True
        product_state["finalized_at"] = now_iso()

        save_batch_state(batch_id)

        yield emit({"type": "all_done", "batch_id": batch_id, "asin": asin})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/files/{asin}/{path:path}")
async def serve_file(asin: str, path: str):
    if not re.match(r'^[A-Z0-9]{10}$', asin):
        raise HTTPException(status_code=400, detail="Invalid ASIN format")
    file_path = OUTPUT_BASE / asin / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not file_path.resolve().is_relative_to(OUTPUT_BASE.resolve()):
        raise HTTPException(status_code=403, detail="Forbidden")
    suffix = file_path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".mp4": "video/mp4", ".json": "application/json",
    }
    return FileResponse(file_path, media_type=media_types.get(suffix, "application/octet-stream"))


@app.get("/drive-video/{file_id}")
async def drive_video_proxy(file_id: str):
    """Google Driveの動画ファイルをプロキシしてインライン再生可能にする。"""
    if not re.match(r'^[a-zA-Z0-9_-]+$', file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID")
    try:
        from shopee_core import _get_drive_service
        config = get_config()
        drive = _get_drive_service(config)
        import io
        from googleapiclient.http import MediaIoBaseDownload
        request = drive.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="video/mp4",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        logger.warning("Drive video proxy failed for %s: %s", file_id, e)
        raise HTTPException(status_code=404, detail="動画を取得できません")


@app.post("/process-stream")
async def process_stream(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="リクエストのJSON形式が不正です")

    urls = data.get("urls") or []
    legacy_url = (data.get("url") or "").strip()
    if not urls and legacy_url:
        urls = [legacy_url]
    urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]

    rainforest_key = data.get("rainforest_key", "") or os.environ.get("RAINFOREST_API_KEY", "")
    openai_key = data.get("openai_key", "") or os.environ.get("OPENAI_API_KEY", "")
    skip_image_translate = data.get("skip_image_translate", False)
    auto_finalize = bool(data.get("auto_finalize", False)) or bool(legacy_url and not data.get("urls"))

    def generate():
        # Proxy/CDN buffering対策: 先に2KB超のコメントを流してSSEを即時開始させる
        yield ":" + (" " * 2048) + "\n\n"
        yield "retry: 3000\n\n"

        if not urls:
            yield sse_event({"type": "error", "step": 0, "message": "URLが必要です"})
            return
        if len(urls) > MAX_BATCH_SIZE:
            yield sse_event({"type": "error", "step": 0, "message": f"URLは最大{MAX_BATCH_SIZE}件までです"})
            return
        if not rainforest_key:
            yield sse_event({"type": "error", "step": 0, "message": "Rainforest APIキーが必要です"})
            return
        try:
            ensure_drive_parent_folder_config(get_config())
        except Exception as e:
            import logging as _log
            _log.getLogger("app").error("Drive config check failed: %s", e, exc_info=True)
            yield sse_event({"type": "error", "step": 0, "message": f"Google Drive設定エラー: {e}"})
            return

        batch_id = now_iso().replace(":", "").replace("-", "") + "_" + uuid.uuid4().hex[:8]
        started = datetime.now()
        est_total = len(urls) * EST_PER_PRODUCT_SEC
        results = []
        BATCH_STORE[batch_id] = {
            "batch_id": batch_id,
            "created_at": now_iso(),
            "urls": urls,
            "results": results,
            "auto_finalize": auto_finalize,
        }
        save_batch_state(batch_id)

        def emit(payload):
            elapsed = int((datetime.now() - started).total_seconds())
            payload["batch_id"] = batch_id
            payload["remaining_sec"] = max(0, est_total - elapsed)
            return sse_event(payload)

        for idx, url in enumerate(urls):
            # 一時停止チェック
            if BATCH_PAUSE_REQUESTS.pop(batch_id, False):
                remaining = len(urls) - idx
                yield emit({"type": "batch_stopped", "message": f"一時停止しました。残り{remaining}件の処理を中断しています。", "stopped_at_index": idx, "remaining_count": remaining})
                BATCH_STORE[batch_id]["stopped_reason"] = "user_paused"
                BATCH_STORE[batch_id]["stopped_at_index"] = idx
                BATCH_STORE[batch_id]["stopped_at_step"] = 1
                save_batch_state(batch_id)
                return

            yield emit({"type": "product_start", "index": idx, "total_products": len(urls), "url": url})

            config = get_config()
            output_base = config["output_base"]
            total = 7

            # Drive関連の変数を初期化
            folder_url = ""
            folder_id = ""
            drive_obj = None
            thumb_url = ""
            drive_image_ids = []

            # Step 1: Amazon商品情報を取得
            yield emit({"type": "step", "index": idx, "step": 1, "total": total, "name": "Amazon商品情報を取得", "est": "5秒"})
            try:
                asin = extract_asin(url)
                product = fetch_amazon_product(asin, rainforest_key)
            except QuotaExhaustedError as e:
                yield emit({"type": "quota_exhausted", "index": idx, "step": 1, "service": e.service, "message": str(e)})
                remaining = len(urls) - idx - 1
                if remaining > 0:
                    yield emit({"type": "batch_stopped", "message": f"API残高不足のため、残り{remaining}件の処理を中止しました。チャージ後に再開してください。", "stopped_at_index": idx, "remaining_count": remaining})
                BATCH_STORE[batch_id]["stopped_reason"] = f"{e.service}_quota_exhausted"
                BATCH_STORE[batch_id]["stopped_at_index"] = idx
                BATCH_STORE[batch_id]["stopped_at_step"] = 1
                save_batch_state(batch_id)
                return
            except ValueError as e:
                yield emit({"type": "error", "index": idx, "step": 1, "message": str(e)})
                continue
            except Exception as e:
                logger.warning("Step1 failed for %s: %s", url, e, exc_info=True)
                yield emit({"type": "error", "index": idx, "step": 1, "message": "商品情報の取得に失敗しました。URLを確認してください。"})
                continue

            output_dir = output_base / asin
            output_dir.mkdir(parents=True, exist_ok=True)
            images_dir = output_dir / "images"
            images_dir.mkdir(exist_ok=True)
            videos_dir = output_dir / "videos"
            videos_dir.mkdir(exist_ok=True)

            # Step 1完了後: Driveフォルダを事前作成
            try:
                drive_result = ensure_drive_folder(asin, config)
                folder_id = drive_result["folder_id"]
                folder_url = drive_result["folder_url"]
                drive_obj = drive_result["drive"]
                if not folder_url and folder_id:
                    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
            except Exception as e:
                logger.warning("Driveフォルダ作成失敗 for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "step_warn", "index": idx, "step": 1, "message": f"Driveフォルダ作成失敗（後続ステップで再試行します）: {e}"})

            yield emit({
                "type": "step_done", "index": idx, "step": 1,
                "data": {
                    "asin": asin,
                    "brand": product.get("brand", ""),
                    "title_ja": product.get("title_ja", ""),
                    "image_count_available": len(product.get("image_urls", [])),
                    "credits_remaining": product.get("credits_remaining", "?"),
                }
            })
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                0,
                folder_url,
                config,
                "Step1完了: Amazon商品情報を取得",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 1, "message": f"シート中間記録エラー: {checkpoint_err}"})

            # Step 2: 画像ダウンロード + 即座にDrive保存
            yield emit({"type": "step", "index": idx, "step": 2, "total": total, "name": "画像ダウンロード", "est": "3秒"})
            try:
                image_paths = download_images(product["image_urls"], images_dir)
            except Exception as e:
                logger.warning("Step2 failed for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "error", "index": idx, "step": 2, "message": "画像のダウンロードに失敗しました。"})
                continue

            image_urls = [f"/files/{asin}/images/{Path(p).name}" for p in image_paths]
            image_count = len(image_paths)
            image_shortage = image_count < 3

            # 元画像を即座にDriveへ保存
            if drive_obj and folder_id:
                try:
                    image_items = upload_images_to_drive(drive_obj, image_paths, folder_id)
                    if image_items:
                        thumb_url = drive_thumb_url(image_items[0].get("id", ""))
                        drive_image_ids = [item.get("id", "") for item in image_items if item.get("id")]
                    logger.info("Step2: 元画像%d枚をDriveに保存完了", len(image_paths))
                except Exception as e:
                    logger.warning("Step2 Drive保存失敗: %s", e)
                    yield emit({"type": "step_warn", "index": idx, "step": 2, "message": f"Drive保存失敗（画像はローカルに保持）: {e}"})

            step2_data = {
                "image_count": image_count,
                "image_urls": image_urls,
                "image_shortage": image_shortage,
            }
            yield emit({"type": "step_done", "index": idx, "step": 2, "data": step2_data})
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                "Step2完了: 画像ダウンロード",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 2, "message": f"シート中間記録エラー: {checkpoint_err}"})

            if image_shortage:
                yield emit({
                    "type": "step_warn", "index": idx, "step": 2,
                    "message": f"画像が{image_count}枚のみです（3枚未満）。レビュー画面から補完検索できます。"
                })

            # Step 3: 英語翻訳 + 逆翻訳
            yield emit({"type": "step", "index": idx, "step": 3, "total": total, "name": "英語翻訳 + 逆翻訳", "est": "5秒"})
            try:
                product = translate_product(product)
            except Exception as e:
                logger.warning("Step3 failed for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "error", "index": idx, "step": 3, "message": "翻訳処理に失敗しました。"})
                continue

            yield emit({
                "type": "step_done", "index": idx, "step": 3,
                "data": {
                    "title_en": product.get("title_en", ""),
                    "title_reverse": product.get("title_reverse", ""),
                    "features_en": product.get("features_en", []),
                }
            })
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                "Step3完了: 英語翻訳 + 逆翻訳",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 3, "message": f"シート中間記録エラー: {checkpoint_err}"})

            # Step 4: 画像テキスト英語化 + 即座にDrive保存（1枚ずつ進捗報告）
            translated_image_paths = []
            if openai_key and not skip_image_translate:
                total_images = len(image_paths)
                est_per_image = 45  # 秒/枚
                est = f"約{total_images * est_per_image}秒（{total_images}枚）"
                yield emit({"type": "step", "index": idx, "step": 4, "total": total, "name": "画像テキスト英語化", "est": est})

                en_dir = output_dir / "images_en"
                en_dir.mkdir(exist_ok=True)
                consecutive_failures = 0
                max_consecutive_failures = 2
                step4_start = time.time()

                for img_i, img_path in enumerate(image_paths):
                    out_path = en_dir / f"{img_path.stem}_en.png"
                    if not has_japanese_text(openai_key, str(img_path)):
                        logger.info("テキストなし、翻訳スキップ: %s", Path(img_path).name)
                        shutil.copy2(str(img_path), str(out_path))
                        translated_image_paths.append(out_path)
                        consecutive_failures = 0
                        elapsed = time.time() - step4_start
                        completed = img_i + 1
                        yield emit({"type": "step_progress", "index": idx, "step": 4, "completed": completed, "total_items": total_images, "elapsed_sec": round(elapsed, 1), "est_remaining_sec": 0})
                        continue
                    try:
                        success = translate_image_text(openai_key, str(img_path), str(out_path), product)
                    except QuotaExhaustedError as e:
                        yield emit({"type": "quota_exhausted", "index": idx, "step": 4, "service": e.service, "message": str(e)})
                        remaining = len(urls) - idx - 1
                        if remaining > 0:
                            yield emit({"type": "batch_stopped", "message": f"API残高不足のため、残り{remaining}件の処理を中止しました。チャージ後に再開してください。", "stopped_at_index": idx, "remaining_count": remaining})
                        BATCH_STORE[batch_id]["stopped_reason"] = f"{e.service}_quota_exhausted"
                        BATCH_STORE[batch_id]["stopped_at_index"] = idx
                        BATCH_STORE[batch_id]["stopped_at_step"] = 4
                        save_batch_state(batch_id)
                        return
                    except Exception as e:
                        logger.warning("画像翻訳エラー (%s): %s", img_path, e)
                        success = False

                    if success:
                        translated_image_paths.append(out_path)
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            logger.warning("画像翻訳が%d回連続失敗。残り%d枚をスキップ", max_consecutive_failures, total_images - img_i - 1)
                            yield emit({"type": "step_warn", "index": idx, "step": 4, "message": f"画像翻訳が{max_consecutive_failures}回連続失敗。残り{total_images - img_i - 1}枚をスキップしました。"})
                            break

                    # 1枚完了ごとに進捗報告
                    elapsed = time.time() - step4_start
                    completed = img_i + 1
                    if len(translated_image_paths) > 0:
                        avg_per_image = elapsed / completed
                        est_remaining = avg_per_image * (total_images - completed)
                    else:
                        est_remaining = est_per_image * (total_images - completed)
                    yield emit({
                        "type": "step_progress", "index": idx, "step": 4,
                        "completed": completed, "total_items": total_images,
                        "elapsed_sec": round(elapsed, 1),
                        "est_remaining_sec": round(max(0, est_remaining), 1),
                    })

                # 翻訳画像を即座にDriveへ保存
                if drive_obj and folder_id and translated_image_paths:
                    try:
                        upload_translated_images_to_drive(drive_obj, translated_image_paths, folder_id)
                        logger.info("Step4: 翻訳画像%d枚をDriveに保存完了", len(translated_image_paths))
                    except Exception as e:
                        logger.warning("Step4 Drive保存失敗: %s", e)
                        yield emit({"type": "step_warn", "index": idx, "step": 4, "message": f"翻訳画像のDrive保存失敗: {e}"})

                translated_image_urls = [f"/files/{asin}/images_en/{Path(p).name}" for p in translated_image_paths]
                yield emit({
                    "type": "step_done", "index": idx, "step": 4,
                    "data": {"translated_count": len(translated_image_paths), "translated_image_urls": translated_image_urls}
                })
                checkpoint_err = write_step_checkpoint(
                    url,
                    product,
                    image_count,
                    folder_url,
                    config,
                    "Step4完了: 画像テキスト英語化",
                )
                if checkpoint_err:
                    yield emit({"type": "step_warn", "index": idx, "step": 4, "message": f"シート中間記録エラー: {checkpoint_err}"})
            else:
                yield emit({"type": "step_skip", "index": idx, "step": 4, "name": "画像テキスト英語化", "reason": "OpenAIキー未設定またはスキップ指定"})

            # Step 5: 動画生成 + 即座にDrive保存
            effect = "zoom"
            model = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"]).get("model", "hailuo")
            version = "v1"
            video_path = videos_dir / f"{version}.mp4"

            yield emit({"type": "step", "index": idx, "step": 5, "total": total, "name": "動画生成", "est": "30〜120秒"})
            try:
                out = generate_video(image_paths, product, output_dir, config=config, effect=effect, output_path=video_path)
                video_path = Path(out) if out else None
                if video_path and video_path.exists():
                    shutil.copy2(video_path, output_dir / "shopee_video.mp4")
            except QuotaExhaustedError as e:
                video_path = None
                yield emit({"type": "quota_exhausted", "index": idx, "step": 5, "service": e.service, "message": str(e)})
                remaining = len(urls) - idx - 1
                if remaining > 0:
                    yield emit({"type": "batch_stopped", "message": f"API残高不足のため、残り{remaining}件の処理を中止しました。チャージ後に再開してください。", "stopped_at_index": idx, "remaining_count": remaining})
                BATCH_STORE[batch_id]["stopped_reason"] = f"{e.service}_quota_exhausted"
                BATCH_STORE[batch_id]["stopped_at_index"] = idx
                BATCH_STORE[batch_id]["stopped_at_step"] = 5
                save_batch_state(batch_id)
                return
            except Exception as e:
                video_path = None
                yield emit({"type": "step_warn", "index": idx, "step": 5, "message": f"動画生成失敗: {e}"})

            video_record = None
            if video_path and video_path.exists():
                video_record = {
                    "version": version,
                    "effect": effect,
                    "model": model,
                    "memo": "",
                    "prompt_extra": "",
                    "created_at": now_iso(),
                    "video_path": str(video_path),
                    "video_url": to_rel_video_url(asin, video_path),
                    "drive_file_url": "",
                    "source_image_path": str(image_paths[0]) if image_paths else "",
                    "source_image_url": image_urls[0] if image_urls else "",
                }

                # 動画を即座にDriveへ保存
                if drive_obj and folder_id:
                    try:
                        video_item = upload_video_to_drive(drive_obj, video_path, folder_id)
                        video_record["drive_file_url"] = video_item.get("webViewLink", "")
                        logger.info("Step5: 動画をDriveに保存完了")
                    except Exception as e:
                        logger.warning("Step5 動画Drive保存失敗: %s", e)
                        yield emit({"type": "step_warn", "index": idx, "step": 5, "message": f"動画のDrive保存失敗: {e}"})

            yield emit({
                "type": "step_done", "index": idx, "step": 5,
                "data": {"video_url": video_record["video_url"] if video_record else None}
            })
            step5_status = "Step5完了: 動画生成" if video_record else "Step5完了: 動画生成（未生成）"
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                step5_status,
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 5, "message": f"シート中間記録エラー: {checkpoint_err}"})

            # Step 6: Drive保存確認（逐次保存済みなので確認のみ）
            yield emit({"type": "step", "index": idx, "step": 6, "total": total, "name": "Google Drive保存確認", "est": "2秒"})
            if not folder_url:
                # Driveフォルダ作成に失敗していた場合はここで再試行
                try:
                    drive_result = ensure_drive_folder(asin, config)
                    folder_id = drive_result["folder_id"]
                    folder_url = drive_result["folder_url"]
                    drive_obj = drive_result["drive"]
                    if not folder_url and folder_id:
                        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
                    # まとめてアップロード（フォールバック）
                    upload_images_to_drive(drive_obj, image_paths, folder_id)
                    if translated_image_paths:
                        upload_translated_images_to_drive(drive_obj, translated_image_paths, folder_id)
                    if video_path and Path(video_path).exists():
                        video_item = upload_video_to_drive(drive_obj, video_path, folder_id)
                        if video_record:
                            video_record["drive_file_url"] = video_item.get("webViewLink", "")
                except Exception as e:
                    logger.warning("Step6 Drive fallback failed for %s: %s", asin, e, exc_info=True)
                    err_str = str(e)
                    if "認証" in err_str or "credential" in err_str.lower():
                        msg = "Google Drive認証に失敗しました。管理者に連絡してください。"
                    elif "権限" in err_str or "permission" in err_str.lower() or "403" in err_str:
                        msg = "Google Driveフォルダへの書き込み権限がありません。フォルダの共有設定を確認してください。"
                    else:
                        msg = "Google Driveへのアップロードに失敗しました。しばらくしてから再度お試しください。"
                    yield emit({"type": "error", "index": idx, "step": 6, "message": msg})
                    continue

            yield emit({"type": "step_done", "index": idx, "step": 6, "data": {"drive_folder_url": folder_url}})
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                "Step6完了: Google Drive保存確認",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 6, "message": f"シート中間記録エラー: {checkpoint_err}"})

            if video_record:
                try:
                    append_video_generation_log(
                        asin=asin,
                        product_url=url,
                        version=video_record["version"],
                        effect=video_record["effect"],
                        model=video_record["model"],
                        memo=video_record.get("memo", ""),
                        drive_folder_url=folder_url,
                        drive_file_url=video_record.get("drive_file_url", ""),
                        thumb_url=thumb_url,
                        is_selected=False,
                        config=config,
                    )
                except Exception:
                    pass

            # Step 7: スプレッドシート書き込み
            yield emit({"type": "step", "index": idx, "step": 7, "total": total, "name": "スプレッドシート書き込み", "est": "2秒"})
            try:
                write_to_spreadsheet(url, product, len(image_paths), folder_url, config, status="完了")
                yield emit({"type": "step_done", "index": idx, "step": 7, "data": {"finalized": auto_finalize}})
            except Exception as e:
                yield emit({"type": "step_warn", "index": idx, "step": 7, "message": f"スプレッドシートエラー: {e}"})
                yield emit({"type": "step_done", "index": idx, "step": 7, "data": {"finalized": False}})

            product_state = {
                "url": url,
                "asin": asin,
                "title_ja": product.get("title_ja", ""),
                "brand": product.get("brand", ""),
                "product": product,
                "image_paths": [str(p) for p in image_paths],
                "image_urls": image_urls,
                "translated_image_paths": [str(p) for p in translated_image_paths],
                "image_count": image_count,
                "image_shortage": image_shortage,
                "drive_folder_url": folder_url,
                "drive_folder_id": folder_id,
                "drive_thumb_url": thumb_url,
                "drive_image_ids": drive_image_ids,
                "videos": [video_record] if video_record else [],
                "selected_version": version if video_record else "",
                "selected_image_url": image_urls[0] if image_urls else "",
                "finalized": bool(auto_finalize),
                "finalized_at": now_iso() if auto_finalize else "",
                "created_at": now_iso(),
            }
            results.append(product_state)
            save_batch_state(batch_id)

            yield emit({"type": "product_done", "index": idx, "asin": asin, "data": product_state})

        BATCH_STORE[batch_id]["results"] = results
        BATCH_STORE[batch_id]["updated_at"] = now_iso()
        BATCH_PAUSE_REQUESTS.pop(batch_id, None)
        save_batch_state(batch_id)

        yield emit({"type": "all_done", "batch_id": batch_id, "results": results})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/batch/{batch_id}/pause")
async def pause_batch(batch_id: str):
    """処理中のバッチに一時停止リクエストを送る。現在の商品完了後に停止。"""
    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="バッチが見つかりません")
    BATCH_PAUSE_REQUESTS[batch_id] = True
    return {"status": "pause_requested"}


@app.post("/api/batch/{batch_id}/resume")
async def resume_batch(batch_id: str):
    """中断したバッチを再開する。stopped_at_indexの商品から処理を続行。"""
    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="バッチが見つかりません")
    if not batch.get("stopped_reason"):
        raise HTTPException(status_code=400, detail="このバッチは停止状態ではありません")

    urls = batch.get("urls", [])
    stopped_at_index = batch.get("stopped_at_index", 0)
    auto_finalize = batch.get("auto_finalize", False)
    existing_results = batch.get("results", [])

    if not urls:
        raise HTTPException(status_code=400, detail="再開対象のURLがありません")

    # 停止状態をクリア
    batch.pop("stopped_reason", None)
    batch.pop("stopped_at_index", None)
    batch.pop("stopped_at_step", None)
    save_batch_state(batch_id)

    remaining_urls = urls[stopped_at_index:]

    def generate():
        config = get_config()
        output_base = config["output_base"]
        openai_key = config.get("openai_key", "")
        rainforest_key = config.get("rainforest_key", "")
        skip_image_translate = config.get("skip_image_translate", False)
        started = datetime.now()
        est_total = len(remaining_urls) * EST_PER_PRODUCT_SEC
        results = list(existing_results)

        def emit(payload):
            elapsed = int((datetime.now() - started).total_seconds())
            payload["batch_id"] = batch_id
            payload["remaining_sec"] = max(0, est_total - elapsed)
            return sse_event(payload)

        for resume_i, url in enumerate(remaining_urls):
            idx = stopped_at_index + resume_i

            # 一時停止チェック
            if BATCH_PAUSE_REQUESTS.pop(batch_id, False):
                remaining = len(remaining_urls) - resume_i
                yield emit({"type": "batch_stopped", "message": f"一時停止しました。残り{remaining}件の処理を中断しています。", "stopped_at_index": idx, "remaining_count": remaining})
                BATCH_STORE[batch_id]["stopped_reason"] = "user_paused"
                BATCH_STORE[batch_id]["stopped_at_index"] = idx
                BATCH_STORE[batch_id]["stopped_at_step"] = 1
                save_batch_state(batch_id)
                return

            yield emit({"type": "product_start", "index": idx, "total_products": len(urls), "url": url})

            total = 7
            folder_url = ""
            folder_id = ""
            drive_obj = None
            thumb_url = ""
            drive_image_ids = []

            # Step 1: Amazon商品情報を取得
            yield emit({"type": "step", "index": idx, "step": 1, "total": total, "name": "Amazon商品情報を取得", "est": "5秒"})
            try:
                asin = extract_asin(url)
                product = fetch_amazon_product(asin, rainforest_key)
            except QuotaExhaustedError as e:
                yield emit({"type": "quota_exhausted", "index": idx, "step": 1, "service": e.service, "message": str(e)})
                remaining = len(urls) - idx - 1
                if remaining > 0:
                    yield emit({"type": "batch_stopped", "message": f"API残高不足のため、残り{remaining}件の処理を中止しました。チャージ後に再開してください。", "stopped_at_index": idx, "remaining_count": remaining})
                BATCH_STORE[batch_id]["stopped_reason"] = f"{e.service}_quota_exhausted"
                BATCH_STORE[batch_id]["stopped_at_index"] = idx
                BATCH_STORE[batch_id]["stopped_at_step"] = 1
                save_batch_state(batch_id)
                return
            except ValueError as e:
                yield emit({"type": "error", "index": idx, "step": 1, "message": str(e)})
                continue
            except Exception as e:
                logger.warning("Resume Step1 failed for %s: %s", url, e, exc_info=True)
                yield emit({"type": "error", "index": idx, "step": 1, "message": "商品情報の取得に失敗しました。"})
                continue

            output_dir = Path(output_base) / asin
            output_dir.mkdir(parents=True, exist_ok=True)
            images_dir = output_dir / "images"
            images_dir.mkdir(exist_ok=True)
            videos_dir = output_dir / "videos"
            videos_dir.mkdir(exist_ok=True)

            yield emit({
                "type": "step_done", "index": idx, "step": 1,
                "data": {
                    "asin": asin,
                    "title_ja": product.get("title_ja", ""),
                    "brand": product.get("brand", ""),
                    "price": product.get("price", ""),
                }
            })
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                0,
                folder_url,
                config,
                "Step1完了: Amazon商品情報を取得",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 1, "message": f"シート中間記録エラー: {checkpoint_err}"})

            # Driveフォルダ作成
            try:
                drive_result = ensure_drive_folder(asin, config)
                folder_id = drive_result["folder_id"]
                folder_url = drive_result["folder_url"]
                drive_obj = drive_result["drive"]
                if not folder_url and folder_id:
                    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
            except Exception as e:
                logger.warning("Resume Drive folder failed: %s", e)

            # Step 2: 商品画像取得
            yield emit({"type": "step", "index": idx, "step": 2, "total": total, "name": "商品画像取得", "est": "10秒"})
            try:
                image_paths = download_images(product["image_urls"], images_dir)
            except Exception as e:
                logger.warning("Resume Step2 failed for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "error", "index": idx, "step": 2, "message": "画像ダウンロードに失敗しました。"})
                continue

            image_urls = [f"/files/{asin}/images/{Path(p).name}" for p in image_paths]
            image_count = len(image_paths)
            image_shortage = image_count < 3

            if drive_obj and folder_id:
                try:
                    image_items = upload_images_to_drive(drive_obj, image_paths, folder_id)
                    if image_items:
                        thumb_url = drive_thumb_url(image_items[0].get("id", ""))
                        drive_image_ids = [item.get("id", "") for item in image_items if item.get("id")]
                    logger.info("Resume Step2: 元画像%d枚をDriveに保存完了", len(image_paths))
                except Exception as e:
                    logger.warning("Resume Step2 Drive保存失敗: %s", e)

            yield emit({"type": "step_done", "index": idx, "step": 2, "data": {"image_count": image_count, "image_urls": image_urls, "image_shortage": image_shortage}})
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                "Step2完了: 画像ダウンロード",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 2, "message": f"シート中間記録エラー: {checkpoint_err}"})

            if image_shortage:
                yield emit({"type": "step_warn", "index": idx, "step": 2, "message": f"画像が{image_count}枚のみです（3枚未満）。レビュー画面から補完検索できます。"})

            # Step 3: 英語翻訳 + 逆翻訳
            yield emit({"type": "step", "index": idx, "step": 3, "total": total, "name": "英語翻訳 + 逆翻訳", "est": "5秒"})
            try:
                product = translate_product(product)
            except Exception as e:
                logger.warning("Resume Step3 failed for %s: %s", asin, e, exc_info=True)
                yield emit({"type": "error", "index": idx, "step": 3, "message": "翻訳処理に失敗しました。"})
                continue

            yield emit({
                "type": "step_done", "index": idx, "step": 3,
                "data": {
                    "title_en": product.get("title_en", ""),
                    "title_reverse": product.get("title_reverse", ""),
                    "features_en": product.get("features_en", []),
                }
            })
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                "Step3完了: 英語翻訳 + 逆翻訳",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 3, "message": f"シート中間記録エラー: {checkpoint_err}"})

            # Step 4: 画像テキスト英語化
            translated_image_paths = []
            if openai_key and not skip_image_translate:
                total_images = len(image_paths)
                est_per_image = 45
                est = f"約{total_images * est_per_image}秒（{total_images}枚）"
                yield emit({"type": "step", "index": idx, "step": 4, "total": total, "name": "画像テキスト英語化", "est": est})

                en_dir = output_dir / "images_en"
                en_dir.mkdir(exist_ok=True)
                consecutive_failures = 0
                max_consecutive_failures = 2
                step4_start = time.time()

                for img_i, img_path in enumerate(image_paths):
                    out_path = en_dir / f"{img_path.stem}_en.png"
                    if not has_japanese_text(openai_key, str(img_path)):
                        logger.info("テキストなし、翻訳スキップ: %s", Path(img_path).name)
                        shutil.copy2(str(img_path), str(out_path))
                        translated_image_paths.append(out_path)
                        consecutive_failures = 0
                        elapsed = time.time() - step4_start
                        completed = img_i + 1
                        yield emit({"type": "step_progress", "index": idx, "step": 4, "completed": completed, "total_items": total_images, "elapsed_sec": round(elapsed, 1), "est_remaining_sec": 0})
                        continue
                    try:
                        success = translate_image_text(openai_key, str(img_path), str(out_path), product)
                    except QuotaExhaustedError as e:
                        yield emit({"type": "quota_exhausted", "index": idx, "step": 4, "service": e.service, "message": str(e)})
                        remaining = len(urls) - idx - 1
                        if remaining > 0:
                            yield emit({"type": "batch_stopped", "message": f"API残高不足のため、残り{remaining}件の処理を中止しました。チャージ後に再開してください。", "stopped_at_index": idx, "remaining_count": remaining})
                        BATCH_STORE[batch_id]["stopped_reason"] = f"{e.service}_quota_exhausted"
                        BATCH_STORE[batch_id]["stopped_at_index"] = idx
                        BATCH_STORE[batch_id]["stopped_at_step"] = 4
                        save_batch_state(batch_id)
                        return
                    except Exception as e:
                        logger.warning("画像翻訳エラー (%s): %s", img_path, e)
                        success = False

                    if success:
                        translated_image_paths.append(out_path)
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            yield emit({"type": "step_warn", "index": idx, "step": 4, "message": f"画像翻訳が{max_consecutive_failures}回連続失敗。残り{total_images - img_i - 1}枚をスキップしました。"})
                            break

                    elapsed = time.time() - step4_start
                    completed = img_i + 1
                    avg = elapsed / completed if translated_image_paths else est_per_image
                    est_remaining = avg * (total_images - completed)
                    yield emit({"type": "step_progress", "index": idx, "step": 4, "completed": completed, "total_items": total_images, "elapsed_sec": round(elapsed, 1), "est_remaining_sec": round(max(0, est_remaining), 1)})

                if drive_obj and folder_id and translated_image_paths:
                    try:
                        upload_translated_images_to_drive(drive_obj, translated_image_paths, folder_id)
                        logger.info("Resume Step4: 翻訳画像%d枚をDriveに保存完了", len(translated_image_paths))
                    except Exception as e:
                        logger.warning("Resume Step4 Drive保存失敗: %s", e)

                translated_image_urls = [f"/files/{asin}/images_en/{Path(p).name}" for p in translated_image_paths]
                yield emit({"type": "step_done", "index": idx, "step": 4, "data": {"translated_count": len(translated_image_paths), "translated_image_urls": translated_image_urls}})
                checkpoint_err = write_step_checkpoint(
                    url,
                    product,
                    image_count,
                    folder_url,
                    config,
                    "Step4完了: 画像テキスト英語化",
                )
                if checkpoint_err:
                    yield emit({"type": "step_warn", "index": idx, "step": 4, "message": f"シート中間記録エラー: {checkpoint_err}"})
            else:
                yield emit({"type": "step_skip", "index": idx, "step": 4, "name": "画像テキスト英語化", "reason": "OpenAIキー未設定またはスキップ指定"})

            # Step 5: 動画生成
            effect = "zoom"
            model = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"]).get("model", "hailuo")
            version = "v1"
            video_path = videos_dir / f"{version}.mp4"

            yield emit({"type": "step", "index": idx, "step": 5, "total": total, "name": "動画生成", "est": "30〜120秒"})
            try:
                out = generate_video(image_paths, product, output_dir, config=config, effect=effect, output_path=video_path)
                video_path = Path(out) if out else None
                if video_path and video_path.exists():
                    shutil.copy2(video_path, output_dir / "shopee_video.mp4")
            except QuotaExhaustedError as e:
                video_path = None
                yield emit({"type": "quota_exhausted", "index": idx, "step": 5, "service": e.service, "message": str(e)})
                remaining = len(urls) - idx - 1
                if remaining > 0:
                    yield emit({"type": "batch_stopped", "message": f"API残高不足のため、残り{remaining}件の処理を中止しました。チャージ後に再開してください。", "stopped_at_index": idx, "remaining_count": remaining})
                BATCH_STORE[batch_id]["stopped_reason"] = f"{e.service}_quota_exhausted"
                BATCH_STORE[batch_id]["stopped_at_index"] = idx
                BATCH_STORE[batch_id]["stopped_at_step"] = 5
                save_batch_state(batch_id)
                return
            except Exception as e:
                video_path = None
                yield emit({"type": "step_warn", "index": idx, "step": 5, "message": f"動画生成失敗: {e}"})

            video_record = None
            if video_path and video_path.exists():
                video_record = {
                    "version": version, "effect": effect, "model": model,
                    "memo": "", "prompt_extra": "", "created_at": now_iso(),
                    "video_path": str(video_path),
                    "video_url": f"/files/{asin}/videos/{video_path.name}",
                }

            if drive_obj and folder_id and video_path and video_path.exists():
                try:
                    video_item = upload_video_to_drive(drive_obj, video_path, folder_id)
                    if video_item and video_record:
                        video_record["drive_file_url"] = video_item.get("webViewLink", "")
                    logger.info("Resume Step5: 動画をDriveに保存完了")
                except Exception as e:
                    logger.warning("Resume Step5 Drive保存失敗: %s", e)
            yield emit({
                "type": "step_done", "index": idx, "step": 5,
                "data": {"video_url": video_record["video_url"] if video_record else None}
            })
            step5_status = "Step5完了: 動画生成" if video_record else "Step5完了: 動画生成（未生成）"
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                step5_status,
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 5, "message": f"シート中間記録エラー: {checkpoint_err}"})

            # Step 6: Drive確認
            yield emit({"type": "step", "index": idx, "step": 6, "total": total, "name": "Driveアップロード確認", "est": "2秒"})
            yield emit({"type": "step_done", "index": idx, "step": 6, "data": {"drive_folder_url": folder_url, "drive_thumb_url": thumb_url}})
            checkpoint_err = write_step_checkpoint(
                url,
                product,
                image_count,
                folder_url,
                config,
                "Step6完了: Driveアップロード確認",
            )
            if checkpoint_err:
                yield emit({"type": "step_warn", "index": idx, "step": 6, "message": f"シート中間記録エラー: {checkpoint_err}"})

            if video_record:
                try:
                    append_video_generation_log(
                        asin=asin, product_url=url, version=video_record["version"],
                        effect=video_record["effect"], model=video_record["model"],
                        memo="", drive_folder_url=folder_url,
                        drive_file_url=video_record.get("drive_file_url", ""),
                        thumb_url=thumb_url, is_selected=False, config=config,
                    )
                except Exception:
                    pass

            # Step 7: スプレッドシート書き込み
            yield emit({"type": "step", "index": idx, "step": 7, "total": total, "name": "スプレッドシート書き込み", "est": "2秒"})
            try:
                write_to_spreadsheet(url, product, len(image_paths), folder_url, config, status="完了")
                yield emit({"type": "step_done", "index": idx, "step": 7, "data": {"finalized": auto_finalize}})
            except Exception as e:
                yield emit({"type": "step_warn", "index": idx, "step": 7, "message": f"スプレッドシートエラー: {e}"})
                yield emit({"type": "step_done", "index": idx, "step": 7, "data": {"finalized": False}})

            product_state = {
                "url": url, "asin": asin,
                "title_ja": product.get("title_ja", ""),
                "brand": product.get("brand", ""),
                "product": product,
                "image_paths": [str(p) for p in image_paths],
                "image_urls": image_urls,
                "translated_image_paths": [str(p) for p in translated_image_paths],
                "image_count": image_count,
                "image_shortage": image_shortage,
                "drive_folder_url": folder_url,
                "drive_folder_id": folder_id,
                "drive_thumb_url": thumb_url,
                "drive_image_ids": drive_image_ids,
                "videos": [video_record] if video_record else [],
                "selected_version": version if video_record else "",
                "selected_image_url": image_urls[0] if image_urls else "",
                "finalized": bool(auto_finalize),
                "finalized_at": now_iso() if auto_finalize else "",
                "created_at": now_iso(),
            }
            results.append(product_state)
            save_batch_state(batch_id)
            yield emit({"type": "product_done", "index": idx, "asin": asin, "data": product_state})

        BATCH_STORE[batch_id]["results"] = results
        BATCH_STORE[batch_id]["updated_at"] = now_iso()
        BATCH_STORE[batch_id].pop("stopped_reason", None)
        BATCH_STORE[batch_id].pop("stopped_at_index", None)
        BATCH_STORE[batch_id].pop("stopped_at_step", None)
        BATCH_PAUSE_REQUESTS.pop(batch_id, None)
        save_batch_state(batch_id)
        yield emit({"type": "all_done", "batch_id": batch_id, "results": results})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/regenerate-video")
async def regenerate_video(request: Request):
    data = await request.json()
    batch_id = data.get("batch_id", "")
    asin = data.get("asin", "")
    effect = data.get("effect", "zoom")
    memo = data.get("memo", "")
    selected_image_url = data.get("selected_image_url", "")
    prompt_extra = data.get("prompt_extra", "")

    if effect not in EFFECT_PROMPTS:
        raise HTTPException(status_code=400, detail="無効なeffectです")

    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")

    product = next((p for p in batch["results"] if p.get("asin") == asin), None)
    if not product:
        raise HTTPException(status_code=404, detail="asinが見つかりません")

    config = get_config()
    try:
        ensure_drive_parent_folder_config(config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    output_dir = config["output_base"] / asin
    videos_dir = output_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    version = next_video_version(product.get("videos", []))
    model = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"]).get("model", "hailuo")
    output_path = videos_dir / f"{version}.mp4"
    product_image_paths = product.get("image_paths", [])
    source_image_path = resolve_selected_image_path(asin, selected_image_url, product_image_paths)
    if not source_image_path:
        raise HTTPException(status_code=400, detail="再生成に使える画像がありません")
    source_image_rel_url = f"/files/{asin}/images/{source_image_path.name}"

    try:
        out = generate_video(
            [Path(p) for p in product_image_paths],
            product.get("product", {}),
            output_dir,
            config=config,
            effect=effect,
            output_path=output_path,
            source_image_path=source_image_path,
            prompt_suffix=prompt_extra,
        )
        if out is None:
            raise HTTPException(status_code=500, detail="動画生成に失敗しました。画像を確認してください。")
        video_path = Path(out)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("regenerate_video failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="動画の再生成に失敗しました。しばらくしてから再度お試しください。")

    drive_file_url = ""
    folder_id = product.get("drive_folder_id", "")
    folder_url = product.get("drive_folder_url", "")
    if not folder_url and folder_id:
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    if folder_id:
        try:
            drive_file_url = upload_file_to_drive_folder(video_path, folder_id, config)
        except Exception as e:
            logger.warning("Drive upload failed for %s: %s", asin, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Google Driveへのアップロードに失敗しました。設定を確認してください。")
    else:
        try:
            drive_meta = upload_to_drive(asin, [], [], video_path, config, return_meta=True)
            folder_url = drive_meta.get("folder_url", "")
            folder_id = drive_meta.get("folder_id", "") or parse_drive_folder_id(folder_url)
            video_item = drive_meta.get("video_item", {}) or {}
            drive_file_url = video_item.get("webViewLink", "")
        except Exception as e:
            logger.warning("Drive upload failed for %s: %s", asin, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Google Driveへのアップロードに失敗しました。設定を確認してください。")
    if not folder_url and folder_id:
        folder_url = f"https://drive.google.com/drive/folders/{folder_id}"
    if not drive_file_url:
        raise HTTPException(status_code=500, detail="DriveファイルURLを取得できませんでした")
    if not folder_url:
        raise HTTPException(status_code=500, detail="DriveフォルダURLを取得できませんでした")
    product["drive_folder_id"] = folder_id
    product["drive_folder_url"] = folder_url

    video_record = {
        "version": version,
        "effect": effect,
        "model": model,
        "memo": memo,
        "prompt_extra": prompt_extra,
        "created_at": now_iso(),
        "video_path": str(video_path),
        "video_url": to_rel_video_url(asin, video_path),
        "drive_file_url": drive_file_url,
        "source_image_path": str(source_image_path),
        "source_image_url": source_image_rel_url,
    }

    product.setdefault("videos", []).append(video_record)
    product["selected_version"] = version
    product["selected_image_url"] = source_image_rel_url
    save_batch_state(batch_id)

    try:
        append_video_generation_log(
            asin=asin,
            product_url=product.get("url", ""),
            version=version,
            effect=effect,
            model=model,
            memo=memo,
            drive_folder_url=product.get("drive_folder_url", ""),
            drive_file_url=drive_file_url,
            thumb_url=product.get("drive_thumb_url", ""),
            is_selected=False,
            config=config,
        )
    except Exception:
        pass

    if video_path.exists():
        shutil.copy2(video_path, output_dir / "shopee_video.mp4")

    return JSONResponse({
        "ok": True,
        "asin": asin,
        "videos": product.get("videos", []),
        "selected_version": product.get("selected_version", version),
        "selected_image_url": product.get("selected_image_url", source_image_rel_url),
    })


@app.post("/finalize")
async def finalize(request: Request):
    data = await request.json()
    batch_id = data.get("batch_id", "")
    selections = data.get("products", [])

    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")

    selection_map = {p.get("asin"): p.get("selected_version") for p in selections if p.get("asin")}

    finalized_count = 0
    config = get_config()
    for product in batch["results"]:
        asin = product.get("asin", "")
        selected_version = selection_map.get(asin) or product.get("selected_version", "")
        if selected_version:
            product["selected_version"] = selected_version

        folder_url = product.get("drive_folder_url", "")
        if not folder_url and product.get("drive_folder_id"):
            folder_url = f"https://drive.google.com/drive/folders/{product.get('drive_folder_id')}"
            product["drive_folder_url"] = folder_url

        if product.get("finalized"):
            continue

        try:
            write_to_spreadsheet(
                product.get("url", ""),
                product.get("product", {}),
                len(product.get("image_paths", [])),
                folder_url,
                config,
                status="完了(確定)",
            )
            selected_video = next(
                (v for v in product.get("videos", []) if v.get("version") == product.get("selected_version")),
                {},
            )
            append_video_generation_log(
                asin=asin,
                product_url=product.get("url", ""),
                version=product.get("selected_version", ""),
                effect=selected_video.get("effect", ""),
                model=selected_video.get("model", ""),
                memo="finalize",
                drive_folder_url=folder_url,
                drive_file_url=selected_video.get("drive_file_url", ""),
                thumb_url=product.get("drive_thumb_url", ""),
                is_selected=True,
                config=config,
            )
            product["finalized"] = True
            product["finalized_at"] = now_iso()
            finalized_count += 1
        except Exception as e:
            logger.warning("finalize failed for %s: %s", asin, e)

    batch["updated_at"] = now_iso()
    save_batch_state(batch_id)

    return JSONResponse({
        "ok": True,
        "batch_id": batch_id,
        "finalized_count": finalized_count,
        "products": batch["results"],
    })


@app.post("/search-images")
async def api_search_images(request: Request):
    """Google画像検索で補完候補を返す"""
    data = await request.json()
    query = data.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="queryが必要です")

    results = search_google_images(query, num=10)
    return JSONResponse({"ok": True, "results": results})


@app.post("/add-images")
async def api_add_images(request: Request):
    """検索結果から選択された画像をダウンロードし、バッチのproduct_stateに追加"""
    data = await request.json()
    batch_id = data.get("batch_id", "")
    asin = data.get("asin", "")
    image_urls = data.get("image_urls", [])

    if not image_urls:
        raise HTTPException(status_code=400, detail="image_urlsが必要です")

    batch = get_batch_or_none(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_idが見つかりません")

    product = next((p for p in batch["results"] if p.get("asin") == asin), None)
    if not product:
        raise HTTPException(status_code=404, detail="asinが見つかりません")

    config = get_config()
    output_dir = config["output_base"] / asin
    existing_count = len(product.get("image_paths", []))

    new_paths = download_supplemental_images(image_urls, output_dir, start_index=existing_count)
    new_path_strs = [str(p) for p in new_paths]
    new_urls = [f"/files/{asin}/images/{Path(p).name}" for p in new_paths]

    product["image_paths"].extend(new_path_strs)
    product.setdefault("image_urls", [])
    product["image_urls"].extend(new_urls)
    product["image_count"] = len(product["image_paths"])
    product["image_shortage"] = product["image_count"] < 3
    if not product.get("selected_image_url") and product["image_urls"]:
        product["selected_image_url"] = product["image_urls"][0]
    save_batch_state(batch_id)

    return JSONResponse({
        "ok": True,
        "added_count": len(new_paths),
        "new_image_urls": new_urls,
        "total_image_count": product["image_count"],
        "image_shortage": product["image_shortage"],
    })


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
