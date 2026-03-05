FROM python:3.12-slim

# ffmpeg インストール
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存関係インストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコード
COPY shopee_core.py app.py ./

# BGMファイル
COPY BGM/Vanilla.mp3 ./BGM/Vanilla.mp3

# 出力ディレクトリ
RUN mkdir -p /app/output

ENV OUTPUT_BASE=/app/output
ENV FONT_PATH=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
ENV BGM_PATH=/app/BGM/Vanilla.mp3
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
