# NVIDIA CUDA 베이스 이미지 (Python 3.12 + CUDA 12.8)
FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip python3.12-dev \
    ffmpeg libsndfile1 \
    poppler-utils \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    && rm -rf /var/lib/apt/lists/*
# poppler-utils: pdf2image가 교안 뷰어(/api/chinese/textbook/image)에서 사용.
# 빠지면 PDFInfoNotInstalledError로 모든 과목 교안이 500.
# tesseract-ocr(+chi_sim): 단어앱 카메라 검색(/api/words/ocr).
# 빠지면 촬영 검색이 500. chi_tra는 번체 교재 사진 대비.

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
