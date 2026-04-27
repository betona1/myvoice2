# myvoice2 운영/개발 매뉴얼

88서버에서 돌아가는 격리 인스턴스. 80서버 운영본 `myvoice`(`https://myvoice.901planner.cloud`)와 코드/DB/시크릿 모두 분리.

---

## 1. 빠른 사실 정리

| 항목 | 값 |
|---|---|
| 디렉터리 | `/home/joacham/projects/myvoice2/` |
| 컨테이너 | `myvoice2-prod` |
| 이미지 | `myvoice-myvoice-prod:latest` (재사용, 코드/.env는 볼륨 마운트) |
| 접근 URL | `https://192.168.219.88:9092` (LAN, 자체서명 인증서) |
| 관리자 | `admin@myvoice2.local` / 비번은 `.env`의 `SUPER_ADMIN_PASSWORD` |
| GPU | 0 = XTTS 추론, 1 = Whisper, 2 = 학습/F5-TTS |

브라우저 첫 접속 시 "안전하지 않음" 경고 → "고급 → 계속 진행"으로 통과시키면 secure context 인정 → 마이크 권한 정상 발급.

---

## 2. 디렉터리 구조

```
/home/joacham/projects/myvoice2/
├── main.py                  # FastAPI 진입점 (~127KB)
├── docker-compose.yml       # 컨테이너 정의
├── Dockerfile               # 이미지 빌드 (재사용 시 안 쓰임)
├── .env                     # 시크릿 (git ignore) — 변경 후 컨테이너 재시작 필요
├── certs/cert.pem,key.pem   # 자체서명 HTTPS 인증서 (git ignore)
├── requirements.txt         # Python 의존성
├── auth/                    # 로그인/인증 라우트
├── tts/                     # XTTS 래퍼 + finetuner + train_subprocess
│   ├── engine.py
│   ├── finetuner.py         # 학습 파이프라인 (subprocess 격리)
│   ├── srt_generator.py
│   └── train_subprocess.py  # GPU 2 격리 entrypoint
├── library/                 # 다국어 학습 카드 데이터
├── frontend/                # Vanilla JS UI
├── database/
│   ├── db.py
│   ├── voices.db            # SQLite (git ignore)
│   ├── cedict_en.json       # 중영사전
│   └── pinyin_to_hanzi.json
├── voice_samples/           # 업로드/임포트된 ref wav (git ignore)
├── outputs/                 # 합성 결과 wav (git ignore)
├── finetune_data/           # 학습 산출물 (git ignore)
└── temp/, diarize_temp/, projects/   # 임시 작업 (git ignore)
```

---

## 3. 운영 명령어

### 컨테이너
```bash
cd /home/joacham/projects/myvoice2

# 시작 (이미 빌드된 이미지 재사용)
sudo docker compose up -d

# 코드 변경만 반영 (main.py 등 수정 후)
sudo docker restart myvoice2-prod

# 정지
sudo docker compose down

# 로그 (실시간)
sudo docker logs -f myvoice2-prod

# 컨테이너 안에서 셸
sudo docker exec -it myvoice2-prod bash
```

### .env 변경 후
.env는 ro 볼륨 마운트라 **컨테이너 재시작** 필요 (`docker restart`). 비밀번호/포트/JWT 변경 시 반영.

### 포트 변경
`docker-compose.yml`의 `9092:9092` 양쪽 + `.env`의 `PORT=9092` + (HTTPS 인증서 SAN 영향 없음). 변경 후 `docker compose up -d` (recreate).

---

## 4. 사용자/관리자

- 슈퍼 관리자는 첫 부팅 때 `.env`의 `SUPER_ADMIN_EMAIL/PASSWORD`로 자동 생성됨 (`database/db.py:300`).
- 추가 사용자는 `/register` 엔드포인트로 자기등록 가능 (이메일 인증 → SMTP 사용; .env에 SMTP 설정됨).
- Google OAuth는 `.env`에서 `GOOGLE_*` 주석 처리해 비활성. 로그인 페이지에 버튼은 보이지만 클릭 시 동작 안 함.

### 비번 분실 시
```bash
sudo docker exec myvoice2-prod python3 - <<'PY'
import asyncio, sqlite3
from auth.security import hash_password
c = sqlite3.connect('/app/database/voices.db')
c.execute("UPDATE users SET password_hash = ? WHERE email = ?",
          (hash_password("새비번"), "admin@myvoice2.local"))
c.commit()
PY
```

---

## 5. Voice 등록 / 합성

### 일반 합성 (XTTS v2 zero-shot)
1. 웹 UI 접속 → 로그인
2. 목소리 등록 (업로드 또는 녹음 ≥ 30초)
3. 텍스트 입력 → 합성
4. 결과 wav는 `outputs/<날짜>/`에 저장됨

### Fine-tune (특정 인물 모델 학습)
필요 조건: 같은 인물 ref wav 합쳐서 **2분 이상** 권장 (코드 30초 이하 reject).

#### API 호출 (curl 예시)
```bash
COOKIE=/tmp/myvoice2.cookie
# 로그인 (한 번)
curl -ksS -c $COOKIE -X POST https://192.168.219.88:9092/api/auth/login \
  -d "email=admin@myvoice2.local" -d "password=$PW"

# 학습 시작
curl -ksS -b $COOKIE -X POST https://192.168.219.88:9092/api/finetune/start \
  -d "voice_ids=3,4,5,6,9,10,11,12" \
  -d "model_name=noh_v1" \
  -d "epochs=10" -d "batch_size=2"

# 진행 상태 폴링
curl -ksS -b $COOKIE https://192.168.219.88:9092/api/finetune/status

# 모델 목록
curl -ksS -b $COOKIE https://192.168.219.88:9092/api/finetune/models

# 학습된 모델로 합성
curl -ksS -b $COOKIE -X POST https://192.168.219.88:9092/api/tts/generate-finetuned \
  --data-urlencode "model_dir=/app/finetune_data/noh_v1_model" \
  --data-urlencode "text=합성할 문장"
```

#### ⚠️ `model_name`은 반드시 ASCII로
컨테이너 LANG=None 환경 때문에 한글 model_name은 디렉터리명이 더블 UTF-8 인코딩으로 저장돼 API 매칭이 깨짐. 영문 권장.

#### 학습 시간 (RTX 3080, batch=2)
- 8개 ref (≈3.7분 분량) × 10 epoch ≈ 8~12분
- 30 epoch ≈ 25~35분
- 대용량 데이터일수록 epoch당 시간 ↑

### F5-TTS 비교 (zero-shot)
별도 venv에서 호스트 직접 실행:
```bash
. /home/joacham/projects/voice_compare/f5tts_venv/bin/activate
CUDA_VISIBLE_DEVICES=2 f5-tts_infer-cli \
  --ref_audio /home/joacham/projects/voice_compare/refs/노무현_ref12s.wav \
  --gen_text "합성할 텍스트" \
  --output_dir /home/joacham/projects/voice_compare/f5tts_outputs/ \
  --output_file 출력.wav \
  --device cuda
```

ref wav는 12초 mono 24kHz로 미리 ffmpeg 트리밍할 것.

---

## 6. GPU 분배 정책

| GPU | 용도 | 메모리 |
|---|---|---|
| **GPU 0** (09:00.0) | XTTS v2 추론 엔진 (서비스 startup에 자동 로드) | ~2 GiB |
| **GPU 1** (0A:00.0) | Faster Whisper `large-v3` (lazy load on 첫 STT 호출) | ~3-4 GiB |
| **GPU 2** (0F:00.0) | XTTS fine-tune 트레이너 + F5-TTS 추론 | ~6 GiB peak |

### 주의: GPU 0 학습 금지
같은 프로세스에서 GPU 0의 추론 엔진(2 GiB) + 학습용 모델(7+ GiB)이 동시에 올라가면 10 GiB 한도 초과 → CUDA OOM 크래시. `tts/finetuner.py`는 학습을 **subprocess.run**으로 띄우고 `CUDA_VISIBLE_DEVICES=2`(env: `FINETUNE_GPU`)를 부모에서 자식으로 전달해 격리.

### 다른 GPU에서 학습하려면
`.env`에 `FINETUNE_GPU=1` 추가 후 컨테이너 재시작 — 단 GPU 1은 Whisper와 충돌 가능.

### PCIe x1 라이저 제약
3장 모두 Gen3 x1 (~1 GB/s/GPU). 추론은 무관하지만 분산 학습은 비현실적. 현재 학습은 단일 GPU에서만 함.

---

## 7. 의존성 (requirements.txt)

핵심:
- `torch 2.10+cu128` + `torchaudio` (Dockerfile에서 별도 설치)
- `coqui-tts[codec]` 0.27.5 (XTTS v2 + 트레이너)
- `faster-whisper` (large-v3, GPU)
- `aiosqlite`, `fastapi`, `uvicorn`
- `pypinyin`, `opencc`, `pykakasi`, `cutlet`, `fugashi`, `unidic-lite` (한중일 NLP)
- `parselmouth`, `librosa`, `soundfile`, `pydub`
- `pandas` (← coqui 트레이너 의존성, 이미지에 누락돼 추가됨)

추가 패키지 필요 시 `requirements.txt`에 적고 이미지 재빌드(`docker compose build`).

---

## 8. 트러블슈팅

### 마이크 안 됨
브라우저는 secure context(HTTPS, localhost만)에서만 마이크 허용. `https://192.168.219.88:9092`로 접속하고 자체서명 인증서 경고 통과.

### CUDA out of memory (학습 중)
- 트레이너가 GPU 0에 떴는지 확인 — `FINETUNE_GPU=2` 인지 점검
- GPU 2에 다른 작업(F5-TTS 등) 있는지 `nvidia-smi`로 확인 후 종료
- `batch_size=1`까지 낮춰보기 (속도 ↓)

### 학습 모델이 API에 안 보임 / "찾을 수 없습니다"
- `finetune_data/<name>_model/meta.json` 존재 확인
- 디렉터리 이름이 한글이면 ASCII로 `os.rename` (한글이면 더블 인코딩 이슈)

### "No module named 'pandas'" 등 의존성 에러
컨테이너 내부에서 즉시: `sudo docker exec myvoice2-prod pip install --break-system-packages <pkg>` (휘발성).
영속화: `requirements.txt`에 추가 + `docker compose build` 재실행.

### `/api/auth/login` 422
JSON 말고 form-encoded로 보내야 함 — `-d "email=...&password=..."` (curl 기본).

### 컨테이너 재시작 후 학습된 pandas/패키지가 사라짐
이건 `docker compose down/up` 같은 컨테이너 재생성 때만. `docker restart` 는 컨테이너 인스턴스 보존이라 안 사라짐. 영속화는 requirements.txt + 이미지 재빌드.

### Whisper 첫 호출이 느림
`large-v3` 모델 (~3 GB) GPU 1에 첫 로드 5-15초. lru_cache로 1회만 로드되니 두 번째 호출부터는 즉시.

### 포트 9092 바뀜
바뀌지 않음. `.env`/`docker-compose.yml` 둘 다 9092 고정. 코드 수정/재시작은 포트 영향 없음.

---

## 9. 공통 디렉터리 (88서버 전체)

| 경로 | 내용 |
|---|---|
| `/home/joacham/projects/myvoice2/` | 이 프로젝트 |
| `/home/joacham/projects/voice_compare/` | F5-TTS venv + 비교 출력물 |
| `/home/joacham/projects/AISERVER/myvoice/` | 80서버에서 가져온 운영본 카피 (참조용, 가동 안 함) |
| `/home/joacham/projects/.env` | 88서버 공용 자격증명 (sudo 비번 등) |

---

## 10. 비교 실험 워크플로 (학습 회차별)

```
같은 텍스트 합성 → 3가지 모델 결과를 voice_compare/ 아래 모음:
  • F5-TTS zero-shot         → voice_compare/f5tts_outputs/
  • XTTS v2 + 10 epoch       → voice_compare/xtts_outputs/노무현_xtts_v1_10ep.wav
  • XTTS v2 + 30 epoch       → voice_compare/xtts_outputs/노무현_xtts_v2_30ep.wav

scp로 가져가기:
  scp joacham@192.168.219.88:'/home/joacham/projects/voice_compare/*/노무현*.wav' .
```

---

## 11. 80서버 운영본과의 관계

`myvoice2`는 운영본을 절대 건드리지 않음. 비교 데이터(voices, ref wav)만 80서버에서 80→88로 일방향 가져옴.

운영본 자체를 88서버로 컷오버하려면 별도 작업 필요 — `myvoice_production_routing.md` 메모리 참고 (80서버 nginx upstream 한 줄 변경).
