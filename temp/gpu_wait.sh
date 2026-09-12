#!/bin/bash
# GPU 자리가 날 때까지 기다린다. 쓰임: gpu_wait.sh <필요MiB> [최대대기초]
# ⚠️ 내 작업끼리도, ollama 같은 남의 작업과도 안 부딪히게 — 죽이지 않고 기다린다.
NEED=${1:-6000}; MAX=${2:-7200}; T=0
while [ $T -lt $MAX ]; do
  FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
  MINE=$(pgrep -cf "gen_all_new.py|make_examples.py|make_sents.py|make_alts.py|regen_rated.py" || true)
  if [ "$FREE" -ge "$NEED" ] && [ "${MINE:-0}" -le 1 ]; then exit 0; fi
  sleep 20; T=$((T+20))
done
echo "[gpu_wait] ${MAX}초 기다렸지만 자리가 안 남 (빈자리 ${FREE}MiB)" >&2
exit 1
