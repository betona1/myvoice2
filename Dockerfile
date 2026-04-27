# NVIDIA CUDA 베이스 이미지 (Python 3.12 + CUDA 12.8)
FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip python3.12-dev \
    ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# python3 기본 설정
RUN ln -sf /usr/bin/python3.12 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.12 /usr/bin/python

WORKDIR /app

# 의존성 먼저 설치 (캐시 활용)
COPY requirements.txt .
RUN python3 -m pip install --break-system-packages --no-cache-dir \
    torch torchaudio --index-url https://download.pytorch.org/whl/cu128 && \
    python3 -m pip install --break-system-packages --no-cache-dir \
    -r requirements.txt && \
    python3 -m pip install --break-system-packages --no-cache-dir \
    coqui-tts[codec] yt-dlp

# 소스 코드 복사
COPY main.py .
COPY database/ database/
COPY tts/ tts/
COPY auth/ auth/
COPY library/ library/
COPY frontend/ frontend/
COPY .env .env

# 데이터 볼륨
VOLUME ["/app/voice_samples", "/app/outputs", "/app/database", "/app/finetune_data", "/app/projects", "/app/temp", "/app/diarize_temp"]

# 환경변수 기본값
ENV PORT=9090
ENV ENV=production
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE ${PORT}

CMD ["python3", "main.py"]
