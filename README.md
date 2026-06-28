# myvoice2

`myvoice` 운영본([myvoice.901planner.cloud](https://myvoice.901planner.cloud))과 **완전히 분리된 비교/실험용 인스턴스**. 동일 코드베이스에서 fork했지만 별도 디렉터리·DB·시크릿·포트로 가동되어 운영본에 영향 없이 모델/데이터 실험을 할 수 있습니다.

## 🎯 목적

- 같은 입력 텍스트를 **여러 모델로 합성**해 음성 품질 비교 (XTTS v2 zero-shot vs fine-tuned vs F5-TTS 등)
- 운영본에 영향 없이 fine-tune/모델 교체/스키마 변경 등 자유롭게 실험
- 운영서버의 3× RTX 3080 GPU를 역할별로 분배해서 추론·STT·학습이 동시 가능

## 🏗 아키텍처 차이점

### myvoice (운영본, GPU서버)
- 도메인 + Cloudflare + nginx reverse proxy
- Google OAuth + 이메일 로그인
- 단일 GPU 추론

### myvoice2 (이 프로젝트, 운영서버)
- LAN 직접 (`https://YOUR_SERVER:9092`, 자체서명 HTTPS)
- 이메일 로그인만 (OAuth 비활성)
- **GPU 역할 분리**: GPU 0 추론 / GPU 1 STT(Whisper large-v3) / GPU 2 학습·F5-TTS
- **학습 서브프로세스 격리**: `tts/train_subprocess.py`가 `CUDA_VISIBLE_DEVICES`로 GPU 분리 실행
- 비교 실험용 별도 venv (`./voice_samples/f5tts_venv/`)

## ⚡ 빠른 시작

```bash
cd ~/myvoice2

# 환경 설정 (.env 채우기 — 아래 참고)
cp .env.example .env  # (예시 — 운영 .env는 git에 안 올라감)
# 편집: SUPER_ADMIN_PASSWORD, JWT_SECRET_KEY, BASE_URL, PORT 등

# 컨테이너 가동 (이미지는 myvoice 빌드본 재사용)
sudo docker compose up -d

# 헬스체크
curl -ksS https://YOUR_SERVER:9092/

# 첫 로그인 → admin@myvoice2.local + .env에 설정한 비번
```

자세한 운영/개발/트러블슈팅은 **[MANUAL.md](MANUAL.md)** 참고.

## 📁 주요 디렉터리

```
myvoice2/
├── main.py                  # FastAPI 진입점
├── docker-compose.yml       # 컨테이너 정의 (port 9092, GPU all)
├── tts/
│   ├── engine.py            # XTTS v2 추론 래퍼
│   ├── finetuner.py         # 학습 파이프라인 (서브프로세스 격리)
│   └── train_subprocess.py  # GPU 격리 학습 entrypoint
├── auth/                    # 로그인/세션
├── library/                 # 다국어 학습 카드 데이터
└── frontend/                # Vanilla JS UI
```

## 🔬 모델 비교 워크플로

```bash
# 1. 같은 인물 ref wav 12초 트리밍
ffmpeg -i ref.wav -t 12 -ac 1 -ar 24000 ref12s.wav

# 2. F5-TTS zero-shot
. ./voice_samples/f5tts_venv/bin/activate
CUDA_VISIBLE_DEVICES=2 f5-tts_infer-cli \
  --ref_audio ref12s.wav --gen_text "..." \
  --output_file out_f5.wav

# 3. XTTS v2 fine-tune (10/30 epoch 등 비교)
curl -ksS -b $COOKIE -X POST .../api/finetune/start \
  -d "voice_ids=...&model_name=noh_v1&epochs=10&batch_size=2"

# 4. 학습된 모델로 합성
curl -ksS -b $COOKIE -X POST .../api/tts/generate-finetuned \
  --data-urlencode "model_dir=/app/finetune_data/noh_v1_model" \
  --data-urlencode "text=..."
```

## 🛡 보안 / git ignore

다음은 절대 커밋되지 않도록 `.gitignore`에 명시됨:
- `.env` (시크릿)
- `*.pem`, `certs/` (인증서)
- `database/voices.db` (사용자 데이터)
- `voice_samples/`, `outputs/`, `finetune_data/`, `temp/` (대용량 / 사용자 콘텐츠)

## 📜 라이선스

운영본 `myvoice` 라이선스(MIT) 그대로.

## 🔗 관련 저장소

- **운영본**: [betona1/voicetoTTS](https://github.com/betona1/voicetoTTS) (이 fork의 origin)
- **이 저장소**: 운영서버 격리 인스턴스, 비교/실험용
