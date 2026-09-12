#!/usr/bin/env bash
# 재부팅 후 GPU/서비스 상태 확인 (2026-08-28, 3080 3장 → 1장 축소 대응)
set -u

echo "=== 1. NVIDIA 드라이버 정합성 ==="
if nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv; then
    echo "[OK] 드라이버 정상"
else
    echo "[FAIL] 여전히 불일치 — 'sudo apt install --reinstall nvidia-dkms-595-open' 후 재부팅 검토"
    exit 1
fi

echo
echo "커널 모듈: $(awk '{print $8}' /proc/driver/nvidia/version)"
echo "유저 라이브러리: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader)"

echo
echo "=== 2. GPU 개수 확인 (1이어야 정상) ==="
COUNT=$(nvidia-smi --list-gpus | wc -l)
echo "감지된 GPU: ${COUNT}장"
[ "$COUNT" -eq 1 ] || echo "[주의] .env의 GPU_COUNT/STT_GPU/FINETUNE_GPU 재검토 필요"

echo
echo "=== 3. 컨테이너 상태 ==="
sudo docker ps -a --format 'table {{.Names}}\t{{.Status}}'

echo
echo "=== 4. myvoice2 기동 ==="
echo "  cd /home/joacham/projects/myvoice2 && sudo docker compose up -d"
echo "  sudo docker logs -f myvoice2-prod   # [DEVICE] TTS 엔진 디바이스: cuda 확인"
echo "  curl -ksS https://192.168.219.88:9092/ -o /dev/null -w '%{http_code}\n'"
