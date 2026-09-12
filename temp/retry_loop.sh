#!/bin/bash
# 못 만든 낱말만 다시 — 시도 12번. 한 프로세스에 모델 하나씩.
cd /app; LOG=/app/temp/retry_loop.log
bash /app/temp/gpu_wait.sh 4300 7200 || exit 1
F() { bash /app/temp/gpu_wait.sh 4300 3600 || return 1; python3 /app/finetune_data/_wordapp/gen_all_new.py "$@" 2>&1 | tr '\r' '\n' | grep -vi "warn\|iB/s\||%\|모델 올림\|^ *$"; }
{
for b in $(seq 0 42); do
  echo "════ 재시도 묶음 $b  $(date +%m-%d\ %H:%M) ════"
  F 여 리리 $b 200;  F 여 원본:리리 $b 200
  F 남 이한 $b 200;  F 남 원본:이한 $b 200
done
echo "════ 재시도 끝 $(date +%m-%d\ %H:%M) ════"
} >> $LOG 2>&1
