"""
Shopee商品ページ最適化ツール - コアロジック
CLI (shopee_tool.py) と Web API (app.py) から共通で使用
"""

import os
import re
import json
import base64
import logging
logger = logging.getLogger(__name__)
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

import time
import requests
from deep_translator import GoogleTranslator

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class QuotaExhaustedError(Exception):
    """API残高不足・クォータ超過時に発生する例外"""
    def __init__(self, service: str, message: str = ""):
        self.service = service
        super().__init__(message or f"{service} の残高が不足しています。チャージしてください。")

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import gspread
    from google.oauth2.service_account import Credentials as SACredentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False


# === 設定（環境変数でオーバーライド可能） ===
def get_config():
    """環境変数から設定を読み込み"""
    return {
        "output_base": Path(os.environ.get("OUTPUT_BASE", str(Path(__file__).parent / "output"))),
        "bgm_path": Path(os.environ.get("BGM_PATH", str(Path(__file__).parent / "BGM" / "Vanilla.mp3"))),
        "font_path": os.environ.get("FONT_PATH", ""),
        "gcp_key_path": Path(os.environ.get(
            "GCP_KEY_PATH",
            str(Path.home() / ".config" / "gcloud" / "keys" / "mcp-sheets-key.json"),
        )),
        "spreadsheet_id": os.environ.get("SPREADSHEET_ID", ""),
        "drive_parent_folder_id": os.environ.get(
            "DRIVE_PARENT_FOLDER_ID", ""
        ),
        "drive_refresh_token": os.environ.get("DRIVE_REFRESH_TOKEN", ""),
        "drive_client_id": os.environ.get("DRIVE_CLIENT_ID", ""),
        "drive_client_secret": os.environ.get("DRIVE_CLIENT_SECRET", ""),
    }


RAINFOREST_API_URL = "https://api.rainforestapi.com/request"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

# 動画パラメータ
VIDEO_W = 1080
VIDEO_H = 1920
FPS = 30
CLIP_DUR = 4
FRAMES = CLIP_DUR * FPS

EFFECT_PROMPTS = {
    "zoom": {
        "prompt": (
            "Camera slowly and smoothly dollies forward toward the product. "
            "No rotation, no lateral movement, purely forward zoom. "
            "The product stays centered and unchanged throughout. "
            "Smooth cinematic dolly-in motion with studio lighting."
        ),
        "negative": (
            "text change, letters morphing, garbled text, illegible text, "
            "rotation, spinning, turning, lateral movement, panning, "
            "fast movement, blurry, distorted, warped, deformed, glitch, artifacts"
        ),
        "model": "hailuo",
    },
    "unbox": {
        "prompt": (
            "A hand gently reaches in and slowly pulls the product out of the packaging. "
            "Clean white background, soft studio lighting, smooth movement."
        ),
        "negative": "",
        "model": "hailuo",
    },
    "steam": {
        "prompt": (
            "Static camera, no movement. Wisps of warm steam rise gently from the product. "
            "Warm golden light slowly intensifies. Cozy and inviting atmosphere."
        ),
        "negative": "",
        "model": "hailuo",
    },
    "condensation": {
        "prompt": (
            "Static camera, no movement. Condensation gathers on the glass surface. "
            "A single water droplet slowly forms and drips downward. "
            "Cool refreshing atmosphere and natural lighting."
        ),
        "negative": (
            "text change, letters morphing, garbled text, illegible text, "
            "fast movement, blurry, distorted, warped, deformed, glitch, artifacts, "
            "multiple droplets, heavy rain, pouring water"
        ),
        "model": "kling",
    },
    "pickup": {
        "prompt": (
            "A hand gently reaches in and slowly picks up the product. "
            "The product is lifted smoothly upward with soft natural lighting."
        ),
        "negative": "",
        "model": "hailuo",
    },
}

FAL_MODELS = {
    "hailuo": {
        "model_id": "fal-ai/minimax/hailuo-2.3/standard/image-to-video",
        "params": {"duration": "10"},
        "supports_negative": False,
    },
    "kling": {
        "model_id": "fal-ai/kling-video/v2.1/standard/image-to-video",
        "params": {"duration": "10", "aspect_ratio": "9:16"},
        "supports_negative": True,
    },
}


def _resolve_font(config):
    """フォントパスを解決"""
    if config["font_path"]:
        return config["font_path"]
    # macOS
    mac_font = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    if Path(mac_font).exists():
        return mac_font
    # Linux (Docker)
    linux_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if Path(linux_font).exists():
        return linux_font
    return "Arial"


# ============================================================
# 1. Amazon 商品情報取得
# ============================================================

def extract_asin(url):
    """URLからASINを抽出"""
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"ASINが見つかりません: {url}")


def fetch_amazon_product(asin, api_key):
    """Rainforest APIでAmazon商品情報を取得"""
    params = {
        "api_key": api_key,
        "type": "product",
        "amazon_domain": "amazon.co.jp",
        "asin": asin,
    }
    resp = requests.get(RAINFOREST_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    product = data.get("product", {})
    title_ja = product.get("title", "")
    brand = product.get("brand", "")

    features = []
    for bullet in product.get("feature_bullets", []):
        if isinstance(bullet, str):
            features.append(bullet)
        elif isinstance(bullet, dict):
            features.append(bullet.get("text", ""))

    description_ja = product.get("description", "")

    main_img = product.get("main_image", {}).get("link", "")
    image_urls = []
    for img in product.get("images", []):
        url = img.get("link", "")
        if url:
            image_urls.append(url)
    if not image_urls and main_img:
        image_urls.append(main_img)
    elif main_img and main_img in image_urls:
        image_urls.remove(main_img)
        image_urls.insert(0, main_img)
    elif main_img:
        image_urls.insert(0, main_img)

    credits_remaining = data.get("request_info", {}).get("credits_remaining", "?")

    # クレジット残が数値で0以下の場合は警告
    try:
        if int(credits_remaining) <= 0:
            raise QuotaExhaustedError("Rainforest", "Rainforest APIのクレジットが残っていません。プランのアップグレードが必要です。")
    except (ValueError, TypeError):
        pass  # "?" などの場合は無視

    return {
        "asin": asin,
        "title_ja": title_ja,
        "brand": brand,
        "features": features,
        "description_ja": description_ja,
        "image_urls": image_urls,
        "credits_remaining": credits_remaining,
    }


# ============================================================
# 2. 画像ダウンロード
# ============================================================

def _ensure_min_size(filepath, min_size=800):
    """画像が min_size×min_size 未満の場合、短辺基準でリサイズ（アスペクト比維持）。
    Pillow がなければスキップ。"""
    if not HAS_PIL:
        return
    try:
        with Image.open(filepath) as img:
            w, h = img.size
            if w >= min_size and h >= min_size:
                return
            scale = max(min_size / w, min_size / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            resized.save(filepath)
    except Exception as e:
        logger.warning("画像リサイズ失敗（スキップ）: %s - %s", filepath, e)


def download_images(image_urls, output_dir):
    """画像をダウンロード + 最低800px保証"""
    paths = []
    for i, url in enumerate(image_urls):
        ext = "png" if ".png" in url.lower() else "jpg"
        filepath = output_dir / f"{i+1:02d}.{ext}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
        _ensure_min_size(filepath)
        paths.append(filepath)
    return paths


# ============================================================
# 2b. Google画像検索（画像不足時の補完用）
# ============================================================

def search_google_images(query, num=10):
    """Google Custom Search APIで商品画像を検索"""
    api_key = os.environ.get("GOOGLE_CSE_API_KEY", "").strip()
    cx = os.environ.get("GOOGLE_CSE_CX", "").strip()
    if not api_key or not cx:
        return []

    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "searchType": "image",
        "num": min(num, 10),
        "imgSize": "large",
        "safe": "active",
    }
    try:
        resp = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = []
    for item in data.get("items", []):
        results.append({
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "thumbnail": item.get("image", {}).get("thumbnailLink", ""),
            "context_url": item.get("image", {}).get("contextLink", ""),
            "width": item.get("image", {}).get("width", 0),
            "height": item.get("image", {}).get("height", 0),
        })
    return results


def download_supplemental_images(image_urls, output_dir, start_index=0):
    """追加画像をダウンロード + 最低800px保証。既存のimagesフォルダに追記"""
    images_dir = Path(output_dir) / "images"
    images_dir.mkdir(exist_ok=True)
    paths = []
    for i, url in enumerate(image_urls):
        idx = start_index + i + 1
        ext = "png" if ".png" in url.lower() else "jpg"
        filepath = images_dir / f"{idx:02d}_sup.{ext}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)
            _ensure_min_size(filepath)
            paths.append(filepath)
        except Exception:
            continue
    return paths


# ============================================================
# 3. テキスト翻訳
# ============================================================

def translate_text(text_ja, max_chunk=4500):
    """日本語→英語翻訳（長文はチャンク分割、失敗時はフォールバック）"""
    if not text_ja:
        return ""
    try:
        translator = GoogleTranslator(source="ja", target="en")
        # deep-translator は約5000文字が上限なので分割
        if len(text_ja) <= max_chunk:
            return translator.translate(text_ja) or ""
        chunks = [text_ja[i:i + max_chunk] for i in range(0, len(text_ja), max_chunk)]
        return " ".join(translator.translate(c) or "" for c in chunks)
    except Exception as e:
        logger.warning("翻訳失敗（フォールバック: 原文返却）: %s", e)
        return text_ja


def _reverse_translate(text_en):
    """英語→日本語の逆翻訳（失敗時は空文字）"""
    if not text_en:
        return ""
    try:
        return GoogleTranslator(source="en", target="ja").translate(text_en) or ""
    except Exception as e:
        logger.warning("逆翻訳失敗: %s", e)
        return ""


def _shorten_title(title_en, brand="", max_len=100):
    """英語タイトルを max_len 文字以内に短縮する。
    1) OpenAI で要約  2) 失敗時は単純トリム"""
    if len(title_en) <= max_len:
        return title_en
    if HAS_OPENAI and os.environ.get("OPENAI_API_KEY"):
        try:
            client = OpenAI()
            prompt = (
                f"Shorten the following product title to {max_len} characters or less. "
                "Keep the brand name at the beginning and preserve key product information. "
                "Return ONLY the shortened title, nothing else.\n\n"
                f"Title: {title_en}"
            )
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            shortened = resp.choices[0].message.content.strip().strip('"')
            if 0 < len(shortened) <= max_len:
                return shortened
        except Exception as e:
            logger.warning("タイトル短縮失敗（フォールバック: 単純トリム）: %s", e)
    # フォールバック: 単純トリム
    return title_en[:max_len].rsplit(" ", 1)[0] if " " in title_en[:max_len] else title_en[:max_len]


def translate_product(product):
    """商品情報を英語翻訳 + 逆翻訳 + タイトル100文字制御"""
    title_en = translate_text(product["title_ja"])
    title_en = _shorten_title(title_en, brand=product.get("brand", ""))

    features_en = []
    for f in product["features"][:5]:
        features_en.append(translate_text(f))

    description_en = ""
    if product.get("description_ja"):
        description_en = translate_text(product["description_ja"][:500])

    title_reverse = _reverse_translate(title_en)

    return {
        **product,
        "title_en": title_en,
        "title_reverse": title_reverse,
        "features_en": features_en,
        "description_en": description_en,
    }


# ============================================================
# 4. 画像テキスト英語化（OpenAI）
# ============================================================

def has_japanese_text(openai_key, image_path):
    """画像に日本語テキストが含まれるかgpt-4o-miniで判定。コスト: ~$0.01/枚"""
    if not HAS_OPENAI:
        return True  # 判定できない場合は翻訳する

    client = OpenAI(api_key=openai_key)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    base64_image = base64.b64encode(image_bytes).decode("utf-8")

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "low"}},
                    {"type": "text", "text": "Does this image contain Japanese text (hiragana, katakana, or kanji)? Reply ONLY 'yes' or 'no'."},
                ],
            }],
            max_tokens=5,
            temperature=0,
        )
        answer = resp.choices[0].message.content.strip().lower()
        has_text = answer.startswith("yes")
        logger.info("日本語テキスト判定 (%s): %s", Path(image_path).name, "あり" if has_text else "なし")
        return has_text
    except Exception as e:
        logger.warning("日本語テキスト判定失敗 (%s): %s — 翻訳対象とします", Path(image_path).name, e)
        return True  # 判定失敗時は安全側（翻訳する）


def translate_image_text(openai_key, image_path, output_path, product):
    """画像内の日本語テキストを英語に置換"""
    if not HAS_OPENAI:
        return False

    client = OpenAI(api_key=openai_key)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    brand = product.get("brand", "")
    title_en = product.get("title_en", "")

    prompt = (
        "Edit this product image: Replace ALL Japanese text with the equivalent English translation. "
        "IMPORTANT: The output must be a square image (1:1 ratio). "
        "Fit the ENTIRE original image content into the square without cropping anything. "
        "If the original is not square, scale it down and add a clean white background to fill the remaining space. "
        "Do NOT cut off any part of the product or text. "
        "Keep the exact same layout, fonts style, colors, and design. "
        "Only change the text language from Japanese to English. "
        f"The product brand is '{brand}'. English product name: '{title_en}'. "
        "Translate all visible Japanese text naturally into English."
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.responses.create(
                model="gpt-4o",
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{base64_image}"},
                        {"type": "input_text", "text": prompt},
                    ],
                }],
                tools=[{"type": "image_generation", "size": "1024x1024", "quality": "high"}],
            )
            break  # 成功したらループを抜ける
        except Exception as e:
            err_str = str(e).lower()
            # quota超過（残高不足）は即座に停止
            if "insufficient_quota" in err_str or "billing" in err_str:
                raise QuotaExhaustedError("OpenAI", "OpenAI APIの残高が不足しています。チャージしてください。") from e
            # 一時的なレートリミットはリトライ
            if ("rate_limit" in err_str or "429" in err_str) and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1秒, 2秒, 4秒
                logger.info(f"OpenAI レートリミット、{wait}秒後にリトライ ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            logger.warning(f"画像翻訳APIエラー ({image_path}): {e}")
            return False
    else:
        logger.warning(f"画像翻訳リトライ上限到達 ({image_path})")
        return False

    for output in response.output:
        if output.type == "image_generation_call":
            image_data = base64.b64decode(output.result)
            with open(output_path, "wb") as f:
                f.write(image_data)
            return True

    return False


def translate_images(openai_key, image_paths, output_dir, product):
    """全画像のテキストを英語化。QuotaExhaustedErrorは呼び出し元へ伝播する。"""
    en_dir = output_dir / "images_en"
    en_dir.mkdir(exist_ok=True)

    translated_paths = []
    consecutive_failures = 0
    max_consecutive_failures = 2

    for i, img_path in enumerate(image_paths):
        out_path = en_dir / f"{img_path.stem}_en.png"
        # QuotaExhaustedErrorはここで捕捉せず呼び出し元へ伝播
        success = translate_image_text(openai_key, str(img_path), str(out_path), product)
        if success:
            translated_paths.append(out_path)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                logger.warning(f"画像翻訳が{max_consecutive_failures}回連続失敗。残り{len(image_paths) - i - 1}枚をスキップします。")
                break

    return translated_paths


# ============================================================
# 5. 動画生成
# ============================================================

def _merge_bgm(video_path, output_path, bgm_path):
    """動画にBGMを合成する共通関数"""
    bgm_path = Path(bgm_path)
    video_path = Path(video_path)
    output_path = Path(output_path)

    if not bgm_path.exists():
        if video_path != output_path:
            shutil.copy2(video_path, output_path)
        return output_path

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, timeout=15
    )
    try:
        vid_duration = float(probe.stdout.strip())
    except (ValueError, AttributeError):
        vid_duration = 10.0
    fade_out_start = max(0, vid_duration - 2)

    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_path), "-i", str(bgm_path),
        "-filter_complex",
        f"[1:a]atrim=start=0:end={vid_duration},"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out_start}:d=2,"
        f"volume=0.4[bgm]",
        "-map", "0:v", "-map", "[bgm]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(output_path)
    ], capture_output=True, timeout=60)

    return output_path


def generate_video_kenburns(image_paths, product, output_dir, config=None, output_path=None):
    """Ken Burns + テロップ + BGM動画を生成"""
    if config is None:
        config = get_config()

    tmp = Path("/tmp") / f"shopee_gen_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # フォント
    font_src = _resolve_font(config)
    font_path = tmp / "font.ttf"
    if Path(font_src).exists():
        shutil.copy(font_src, font_path)
    else:
        font_path = Path(font_src)

    images = image_paths[:5]
    if not images:
        return None

    # テロップ生成
    brand = product.get("brand", "")
    title_en = product.get("title_en", "Product")
    features_en = product.get("features_en", [])

    telops = []
    telops.append(brand if brand else title_en.split(",")[0].split("-")[0].strip()[:30])
    telops.append(title_en[:40] if len(title_en) > 40 else title_en)
    for f in features_en[:2]:
        telops.append(f[:35] if len(f) > 35 else f)
    telops.append("Shop Now")
    while len(telops) < len(images):
        telops.append("Premium Quality")

    clip_paths = []
    for i, img_path in enumerate(images):
        telop_file = tmp / f"t{i}.txt"
        telop_file.write_text(telops[i], encoding="utf-8")

        frame_path = tmp / f"f{i}.png"
        clip_path = tmp / f"c{i}.mp4"

        subprocess.run([
            "ffmpeg", "-y", "-i", str(img_path), "-i", str(img_path),
            "-filter_complex",
            f"[0:v]scale=1296:2304:force_original_aspect_ratio=increase,crop=1296:2304,boxblur=40:10[bg];"
            f"[1:v]scale=1069:1901:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2",
            "-frames:v", "1", str(frame_path)
        ], capture_output=True, timeout=30)

        zoom_expr = (
            f"z='1+0.15*(1-cos(on*PI/{FRAMES}))/2'" if i % 2 == 0
            else f"z='1.15-0.15*(1-cos(on*PI/{FRAMES}))/2'"
        )
        slide_x = (
            r"x='if(lt(t\,1)\,w-(w/2+tw/2)*sin(t*PI/2)\,"
            r"if(lt(t\,3)\,(w-tw)/2\,"
            r"(w-tw)/2-(w/2+tw/2)*(1-cos((t-3)*PI/2))))'"
        )
        vf = (
            f"zoompan={zoom_expr}:x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
            f":d={FRAMES}:s={VIDEO_W}x{VIDEO_H}:fps={FPS},"
            f"drawtext=textfile={telop_file}:fontfile={font_path}"
            f":fontsize=52:fontcolor=white:{slide_x}:y=h-200"
            f":box=1:boxcolor=black@0.6:boxborderw=20"
        )

        subprocess.run([
            "ffmpeg", "-y", "-loop", "1", "-i", str(frame_path),
            "-vf", vf,
            "-t", str(CLIP_DUR), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", str(FPS), str(clip_path)
        ], capture_output=True, timeout=60)

        clip_paths.append(clip_path)

    # クロスフェード結合
    num_clips = len(clip_paths)
    if num_clips == 1:
        video_path = clip_paths[0]
    else:
        inputs = []
        for cp in clip_paths:
            inputs.extend(["-i", str(cp)])

        filter_parts = []
        prev = "[0:v]"
        for j in range(1, num_clips):
            offset = CLIP_DUR * j - 0.5 * j
            out_label = f"[v{j}]" if j < num_clips - 1 else "[vout]"
            cur = f"[{j}:v]"
            filter_parts.append(
                f"{prev}{cur}xfade=transition=fade:duration=0.5:offset={offset}{out_label}"
            )
            prev = out_label

        video_no_audio = tmp / "video.mp4"
        subprocess.run(
            ["ffmpeg", "-y"] + inputs + [
                "-filter_complex", ";".join(filter_parts),
                "-map", "[vout]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(video_no_audio)
            ],
            capture_output=True, timeout=60
        )
        video_path = video_no_audio

    final_path = Path(output_path) if output_path else (output_dir / "shopee_video.mp4")
    _merge_bgm(video_path, final_path, config["bgm_path"])

    shutil.rmtree(tmp, ignore_errors=True)
    return final_path


def generate_video_ai(image_path, prompt, model="hailuo", negative_prompt="", duration=None):
    """fal.aiでI2V動画を生成し、動画URLを返す"""
    try:
        import fal_client
    except ImportError as e:
        raise RuntimeError("fal-client が未インストールです") from e

    if model not in FAL_MODELS:
        raise ValueError(f"未対応モデルです: {model}")

    model_config = FAL_MODELS[model]
    image_url = fal_client.upload_file(str(image_path))

    arguments = {
        "prompt": prompt,
        "image_url": image_url,
        **model_config["params"],
    }
    # duration が明示指定された場合はモデルデフォルトを上書き
    if duration is not None:
        arguments["duration"] = duration

    if model_config["supports_negative"] and negative_prompt:
        arguments["negative_prompt"] = negative_prompt

    try:
        result = fal_client.subscribe(
            model_config["model_id"],
            arguments=arguments,
            with_logs=True,
        )
    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in ("insufficient", "balance", "credit", "quota", "payment", "billing")):
            raise QuotaExhaustedError("fal.ai", "fal.ai の残高が不足しています。チャージしてください。") from e
        raise

    video_url = result.get("video", {}).get("url")
    if not video_url:
        raise RuntimeError(f"動画URLが取得できません: {result}")
    return video_url


def generate_video(
    image_paths,
    product,
    output_dir,
    config=None,
    effect="zoom",
    output_path=None,
    source_image_path=None,
    prompt_suffix="",
    duration=None,
):
    """AI動画生成。FAL_KEY未設定時はKen Burnsへフォールバック"""
    if not image_paths:
        return None
    if config is None:
        config = get_config()

    target_path = Path(output_path) if output_path else (Path(output_dir) / "shopee_video.mp4")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fal_key = os.environ.get("FAL_KEY", "").strip()
    if not fal_key:
        return generate_video_kenburns(image_paths, product, output_dir, config, output_path=target_path)
    effect_config = EFFECT_PROMPTS.get(effect, EFFECT_PROMPTS["zoom"])

    source_path = Path(source_image_path) if source_image_path else Path(image_paths[0])
    if not source_path.exists():
        source_path = Path(image_paths[0])

    prompt = effect_config["prompt"]
    if prompt_suffix:
        prompt = f"{prompt}\nAdditional instruction: {prompt_suffix}"

    video_url = generate_video_ai(
        image_path=str(source_path),
        prompt=prompt,
        model=effect_config["model"],
        negative_prompt=effect_config.get("negative", ""),
        duration=duration,
    )
    resp = requests.get(video_url, timeout=120)
    resp.raise_for_status()

    # BGM合成: AI動画を一時ファイルに保存→BGM合成→最終パスへ
    raw_path = target_path.with_suffix(".raw.mp4")
    raw_path.write_bytes(resp.content)
    _merge_bgm(raw_path, target_path, config["bgm_path"])
    raw_path.unlink(missing_ok=True)

    return target_path


# ============================================================
# 6. Google Drive アップロード
# ============================================================

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_credentials(config):
    """Drive API用の認証情報を取得（3段階フォールバック）"""
    errors = []

    # 方法1: OAuth2 リフレッシュトークン（Cloud Run用）
    if config.get("drive_refresh_token"):
        try:
            from google.oauth2.credentials import Credentials as OAuthCreds
            creds = OAuthCreds(
                token=None,
                refresh_token=config["drive_refresh_token"],
                client_id=config["drive_client_id"],
                client_secret=config["drive_client_secret"],
                token_uri="https://oauth2.googleapis.com/token",
                scopes=DRIVE_SCOPES,
            )
            # トークンリフレッシュを事前テスト
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            logger.info("Drive認証: OAuth2リフレッシュトークンで成功")
            return creds
        except Exception as e:
            errors.append(f"OAuth2リフレッシュトークン: {e}")
            logger.warning("Drive認証方法1(OAuth2)失敗: %s", e)

    # 方法2: Application Default Credentials
    try:
        import google.auth
        creds, _ = google.auth.default(scopes=DRIVE_SCOPES)
        logger.info("Drive認証: ADCで成功")
        return creds
    except Exception as e:
        errors.append(f"ADC: {e}")
        logger.debug("Drive認証方法2(ADC)失敗: %s", e)

    # 方法3: gcloud CLIのアクセストークン（ローカル開発用）
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True, timeout=10
        ).strip()
        if token:
            from google.oauth2.credentials import Credentials as OAuthCreds
            logger.info("Drive認証: gcloud CLIで成功")
            return OAuthCreds(token=token)
    except Exception as e:
        errors.append(f"gcloud CLI: {e}")
        logger.debug("Drive認証方法3(gcloud)失敗: %s", e)

    logger.error("Drive認証: 全方法失敗 - %s", "; ".join(errors))
    return None


def _get_drive_service(config):
    from googleapiclient.discovery import build
    creds = _get_drive_credentials(config)
    if not creds:
        return None
    return build("drive", "v3", credentials=creds)


def _drive_api_retry(func, max_retries=3):
    """Google Drive APIコールをリトライ付きで実行（429/500/503対応）"""
    import time
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            error_str = str(e)
            resp = getattr(e, 'resp', None)
            status = getattr(e, 'status_code', None) or (resp.get('status', 0) if isinstance(resp, dict) else getattr(resp, 'status', 0) if resp else 0)
            retryable = (
                int(status) in (429, 500, 503)
                or 'timeout' in error_str.lower()
                or 'rate limit' in error_str.lower()
            )
            if retryable and attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning("Drive API リトライ %d/%d (%s秒後): %s", attempt + 1, max_retries, wait, e)
                time.sleep(wait)
            else:
                raise


def _upload_file_to_drive(drive, file_path, parent_id):
    from googleapiclient.http import MediaFileUpload
    name = Path(file_path).name
    suffix = Path(file_path).suffix.lower()
    mime = (
        "image/png" if suffix == ".png"
        else "image/jpeg" if suffix in (".jpg", ".jpeg")
        else "application/json" if suffix == ".json"
        else "video/mp4"
    )
    file_size = Path(file_path).stat().st_size
    # 5MB以上のファイルはresumableアップロード（動画向け）
    resumable = file_size > 5 * 1024 * 1024
    media = MediaFileUpload(str(file_path), mimetype=mime, resumable=resumable)
    def _do_upload():
        return drive.files().create(
            body={"name": name, "parents": [parent_id]},
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()
    return _drive_api_retry(_do_upload)


def _escape_drive_query(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_existing_folder(drive, parent_id, folder_name):
    q = (
        f"mimeType='application/vnd.google-apps.folder' and trashed=false and "
        f"name='{_escape_drive_query(folder_name)}' and '{_escape_drive_query(parent_id)}' in parents"
    )
    def _do_list():
        return drive.files().list(
            q=q,
            pageSize=1,
            orderBy="modifiedTime desc",
            fields="files(id, webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    res = _drive_api_retry(_do_list)
    files = res.get("files", [])
    return files[0] if files else None


def list_drive_folder_images(folder_id, config=None):
    """Driveフォルダ内の画像ファイルID一覧を取得。サムネイル表示用。"""
    if not folder_id:
        return []
    if config is None:
        config = get_config()
    drive = _get_drive_service(config)
    if not drive:
        return []
    q = (
        f"'{_escape_drive_query(folder_id)}' in parents and trashed=false and "
        f"(mimeType='image/jpeg' or mimeType='image/png')"
    )
    def _do_list():
        return drive.files().list(
            q=q,
            pageSize=50,
            orderBy="name",
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
    try:
        res = _drive_api_retry(_do_list)
        return [f.get("id", "") for f in res.get("files", []) if f.get("id")]
    except Exception as e:
        logger.warning("Driveフォルダ画像一覧取得失敗: %s", e)
        return []


def ensure_drive_folder(asin, config=None):
    """ASINフォルダをDriveに作成（既存なら再利用）。folder_id, folder_url, driveオブジェクトを返す。"""
    if config is None:
        config = get_config()

    parent_id = (config.get("drive_parent_folder_id") or "").strip()
    if not parent_id:
        raise RuntimeError("DRIVE_PARENT_FOLDER_ID が未設定です。保存先親フォルダIDを必ず指定してください。")

    drive = _get_drive_service(config)
    if not drive:
        raise RuntimeError(
            "Google Drive認証に失敗しました。以下を確認してください:\n"
            "- DRIVE_REFRESH_TOKEN / DRIVE_CLIENT_ID / DRIVE_CLIENT_SECRET が正しく設定されているか\n"
            "- または gcloud auth login が実行済みか（ローカル開発時）"
        )

    existing = _find_existing_folder(drive, parent_id, asin)
    if existing:
        folder_id = existing["id"]
        folder_url = existing.get("webViewLink", "")
    else:
        folder_meta = {
            "name": asin,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        def _do_create_folder():
            return drive.files().create(
                body=folder_meta,
                fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute()
        try:
            folder = _drive_api_retry(_do_create_folder)
        except Exception as e:
            raise RuntimeError(
                f"Driveフォルダ作成に失敗しました（親フォルダID: {parent_id}）。"
                f"親フォルダへの書き込み権限を確認してください: {e}"
            )
        folder_id = folder["id"]
        folder_url = folder["webViewLink"]

    return {"folder_id": folder_id, "folder_url": folder_url, "drive": drive}


def upload_images_to_drive(drive, image_paths, folder_id):
    """元画像をDriveフォルダへアップロード。アップロード結果リストを返す。"""
    image_items = []
    for img in image_paths:
        image_items.append(_upload_file_to_drive(drive, img, folder_id))
    return image_items


def upload_translated_images_to_drive(drive, translated_paths, folder_id):
    """翻訳画像をDriveのimages_enサブフォルダへアップロード。"""
    if not translated_paths:
        return
    en_folder_meta = {
        "name": "images_en",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [folder_id],
    }
    def _do_create_en_folder():
        return drive.files().create(body=en_folder_meta, fields="id", supportsAllDrives=True).execute()
    en_folder = _drive_api_retry(_do_create_en_folder)
    for img in translated_paths:
        _upload_file_to_drive(drive, img, en_folder["id"])


def upload_video_to_drive(drive, video_path, folder_id):
    """動画をDriveフォルダへアップロード。アップロード結果を返す。"""
    if not video_path or not Path(video_path).exists():
        return {}
    return _upload_file_to_drive(drive, video_path, folder_id)


def upload_to_drive(asin, image_paths, translated_paths, video_path, config=None, return_meta=False):
    """画像・動画をGoogle DriveにASIN別フォルダでアップロード（後方互換用）"""
    result = ensure_drive_folder(asin, config)
    folder_id = result["folder_id"]
    folder_url = result["folder_url"]
    drive = result["drive"]

    image_items = upload_images_to_drive(drive, image_paths, folder_id)
    upload_translated_images_to_drive(drive, translated_paths, folder_id)
    video_item = upload_video_to_drive(drive, video_path, folder_id)

    if return_meta:
        return {
            "folder_url": folder_url,
            "folder_id": folder_id,
            "image_items": image_items,
            "video_item": video_item,
        }
    return folder_url


def upload_file_to_drive_folder(file_path, folder_id, config=None):
    """既存Driveフォルダへ単一ファイルをアップロード"""
    if config is None:
        config = get_config()
    drive = _get_drive_service(config)
    if not drive:
        raise RuntimeError(
            "Google Drive認証に失敗しました。"
            "DRIVE_REFRESH_TOKEN等の環境変数を確認してください。"
        )
    if not folder_id:
        raise RuntimeError("DriveフォルダIDが空です。")
    try:
        uploaded = _upload_file_to_drive(drive, file_path, folder_id)
    except Exception as e:
        raise RuntimeError(
            f"ファイルのDriveアップロードに失敗しました（フォルダID: {folder_id}）。"
            f"フォルダへの書き込み権限を確認してください: {e}"
        )
    return uploaded.get("webViewLink", "")


def get_folder_first_image_thumbnail(folder_url, config=None):
    """DriveフォルダURLから先頭画像のサムネURLを返す"""
    if not folder_url:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", folder_url)
    if not m:
        return ""
    folder_id = m.group(1)

    if config is None:
        config = get_config()
    drive = _get_drive_service(config)
    if not drive:
        return ""

    q = f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'"
    res = drive.files().list(
        q=q,
        pageSize=200,
        fields="files(id,mimeType,name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    if not files:
        return ""

    def score(name):
        n = (name or "").lower()
        # 優先: 01.* -> 先頭番号付き画像 -> その他
        m = re.match(r"^(\d+)\.(jpg|jpeg|png)$", n)
        if m:
            num = int(m.group(1))
            return (0, num)
        m2 = re.match(r"^(\d+)", n)
        if m2:
            return (1, int(m2.group(1)))
        # 翻訳画像っぽい名前は後ろに回す
        if "_en" in n or "thumb" in n:
            return (3, 999999)
        return (2, 999999)

    best = sorted(files, key=lambda f: score(f.get("name", "")))[0]
    file_id = best.get("id", "")
    if not file_id:
        return ""
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w400"


# ============================================================
# 7. スプレッドシート書き込み
# ============================================================

def write_to_spreadsheet(url, product, image_count, image_folder_url="", config=None):
    """商品データをスプレッドシートに書き込み"""
    if config is None:
        config = get_config()

    if not HAS_GSPREAD:
        return False

    gcp_key_path = config["gcp_key_path"]
    if not gcp_key_path.exists():
        return False

    creds = SACredentials.from_service_account_file(
        str(gcp_key_path),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(config["spreadsheet_id"])

    ws = ss.worksheet("商品データ")
    existing = ws.col_values(3)
    asin = product["asin"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    row_idx = None
    for i, val in enumerate(existing):
        if val == asin:
            row_idx = i + 1
            break

    features_ja = "\n".join(product.get("features", [])[:5])
    features_en = "\n".join(product.get("features_en", [])[:5])

    row_data = [
        "",
        url,
        asin,
        product.get("brand", ""),
        product.get("title_ja", ""),
        product.get("title_en", ""),
        product.get("title_reverse", ""),
        features_ja,
        features_en,
        product.get("description_en", ""),
        image_folder_url,
        str(image_count),
        "完了",
        now,
    ]

    if row_idx:
        ws.update(values=[row_data], range_name=f"A{row_idx}:N{row_idx}")
    else:
        next_row = len(existing) + 1
        row_data[0] = str(next_row - 1)
        ws.update(values=[row_data], range_name=f"A{next_row}:N{next_row}")

    try:
        log_ws = ss.worksheet("ログ")
        log_ws.insert_row(
            [now, "shopee_tool", "成功", f"ASIN:{asin} 処理完了（画像{image_count}枚）"],
            index=2,
        )
    except Exception:
        pass

    return True


def append_video_generation_log(
    asin,
    product_url,
    version,
    effect,
    model,
    memo="",
    drive_folder_url="",
    drive_file_url="",
    thumb_url="",
    is_selected=False,
    config=None,
):
    """動画生成履歴を「動画生成」シートへ追記"""
    if config is None:
        config = get_config()
    if not HAS_GSPREAD:
        return False

    gcp_key_path = config["gcp_key_path"]
    if not gcp_key_path.exists():
        return False

    creds = SACredentials.from_service_account_file(
        str(gcp_key_path),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(config["spreadsheet_id"])

    try:
        ws = ss.worksheet("動画生成")
    except Exception:
        ws = ss.add_worksheet(title="動画生成", rows=1000, cols=20)
        ws.append_row([
            "日時", "ASIN", "商品URL", "バージョン", "演出", "モデル",
            "メモ", "Driveフォルダ", "Driveファイル", "確定", "サムネイルURL",
        ])

    ws.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        asin,
        product_url,
        version,
        effect,
        model,
        memo,
        drive_folder_url,
        drive_file_url,
        "YES" if is_selected else "",
        thumb_url,
    ])
    return True


def fetch_video_generation_history(limit=300, config=None):
    """「動画生成」シートの履歴を新しい順で取得"""
    if config is None:
        config = get_config()
    if not HAS_GSPREAD:
        return []

    gcp_key_path = config["gcp_key_path"]
    if not gcp_key_path.exists():
        return []

    creds = SACredentials.from_service_account_file(
        str(gcp_key_path),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(config["spreadsheet_id"])

    try:
        ws = ss.worksheet("動画生成")
    except Exception:
        return []

    values = ws.get_all_values()
    if len(values) <= 1:
        return []

    rows = []
    for r in values[1:]:
        rows.append({
            "timestamp": r[0] if len(r) > 0 else "",
            "asin": r[1] if len(r) > 1 else "",
            "product_url": r[2] if len(r) > 2 else "",
            "version": r[3] if len(r) > 3 else "",
            "effect": r[4] if len(r) > 4 else "",
            "model": r[5] if len(r) > 5 else "",
            "memo": r[6] if len(r) > 6 else "",
            "drive_folder_url": r[7] if len(r) > 7 else "",
            "drive_file_url": r[8] if len(r) > 8 else "",
            "selected": r[9] if len(r) > 9 else "",
            "thumb_url": r[10] if len(r) > 10 else "",
        })

    rows.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return rows[:limit]


def fetch_product_sheet_history(limit=300, config=None):
    """「商品データ」シートの履歴を新しい順で取得"""
    if config is None:
        config = get_config()
    if not HAS_GSPREAD:
        return []

    gcp_key_path = config["gcp_key_path"]
    if not gcp_key_path.exists():
        return []

    creds = SACredentials.from_service_account_file(
        str(gcp_key_path),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(config["spreadsheet_id"])
    ws = ss.worksheet("商品データ")
    values = ws.get_all_values()
    if len(values) <= 1:
        return []

    rows = []
    for r in values[1:]:
        rows.append({
            "url": r[1] if len(r) > 1 else "",
            "asin": r[2] if len(r) > 2 else "",
            "brand": r[3] if len(r) > 3 else "",
            "title_ja": r[4] if len(r) > 4 else "",
            "drive_folder_url": r[10] if len(r) > 10 else "",
            "image_count": r[11] if len(r) > 11 else "",
            "status": r[12] if len(r) > 12 else "",
            "timestamp": r[13] if len(r) > 13 else "",
        })

    rows.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return rows[:limit]


# ============================================================
# パイプライン統合
# ============================================================

def process_product(url, rainforest_key, openai_key="", skip_image_translate=False, config=None, log_fn=print):
    """フルパイプライン実行。結果を辞書で返す。"""
    if config is None:
        config = get_config()

    output_base = config["output_base"]
    asin = extract_asin(url)

    output_dir = output_base / asin
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    log_fn(f"[1/7] Amazon商品情報を取得 ({asin})")
    product = fetch_amazon_product(asin, rainforest_key)
    log_fn(f"  タイトル: {product['title_ja'][:50]}...")
    log_fn(f"  画像: {len(product['image_urls'])}枚, クレジット残: {product['credits_remaining']}")

    log_fn("[2/7] 画像ダウンロード")
    image_paths = download_images(product["image_urls"], images_dir)
    log_fn(f"  {len(image_paths)}枚ダウンロード完了")

    log_fn("[3/7] 英語翻訳 + 逆翻訳")
    product = translate_product(product)
    log_fn(f"  EN: {product['title_en'][:60]}...")

    translated_image_paths = []
    if openai_key and not skip_image_translate:
        log_fn("[4/7] 画像テキスト英語化（OpenAI GPT-4o）")
        translated_image_paths = translate_images(openai_key, image_paths, output_dir, product)
        log_fn(f"  {len(translated_image_paths)}/{len(image_paths)}枚完了")
    else:
        log_fn("[4/7] 画像テキスト英語化: スキップ")

    log_fn("[5/7] 動画生成")
    video_path = generate_video(image_paths, product, output_dir, config)
    log_fn(f"  動画: {video_path}")

    folder_url = ""
    log_fn("[6/7] Google Driveアップロード")
    try:
        folder_url = upload_to_drive(
            asin, image_paths, translated_image_paths, video_path, config
        )
        log_fn(f"  Drive: {folder_url}")
    except Exception as e:
        log_fn(f"  Driveエラー: {e}")

    log_fn("[7/7] スプレッドシート書き込み")
    try:
        write_to_spreadsheet(url, product, len(image_paths), folder_url, config)
        log_fn("  スプレッドシート記録完了")
    except Exception as e:
        log_fn(f"  スプレッドシートエラー: {e}")

    return {
        "asin": asin,
        "brand": product.get("brand", ""),
        "title_ja": product["title_ja"],
        "title_en": product["title_en"],
        "title_reverse": product["title_reverse"],
        "features_en": product.get("features_en", []),
        "image_count": len(image_paths),
        "translated_image_count": len(translated_image_paths),
        "video_path": str(video_path) if video_path else None,
        "drive_folder_url": folder_url,
        "output_dir": str(output_dir),
        "amazon_image_urls": product.get("image_urls", []),
        "image_paths": [str(p) for p in image_paths],
        "translated_image_paths": [str(p) for p in translated_image_paths],
    }
