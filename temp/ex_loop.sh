#!/bin/bash
# 예문 음성 — 짧은 구절은 낱말 검사, 6글자 넘는 것만 문장 검사 (say_example)
cd /app; LOG=/app/temp/ex_loop.log
F() { bash /app/temp/gpu_wait.sh 4300 3600 || return 1
      python3 /app/finetune_data/_wordapp/make_examples.py "$@" 2>&1 \
      | tr '\r' '\n' | grep -vi "warn\|iB/s\||%\|모델 올림\|^ *$"; }
{
for b in $(seq 0 11); do
  echo "════ 예문 묶음 $b / 11  $(date +%m-%d\ %H:%M) ════"
  F 여 리리 $b 100;  F 여 원본:리리 $b 100
  F 남 이한 $b 100;  F 남 원본:이한 $b 100
done
echo "════ 예문 끝 $(date +%m-%d\ %H:%M) ════"
} >> $LOG 2>&1
