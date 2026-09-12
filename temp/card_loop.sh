#!/bin/bash
# 카드 음성 — 300개씩 [여 학습→여 원본→남 학습→남 원본]. GPU 자리 날 때만.
cd /app; LOG=/app/temp/card_loop.log
F() { bash /app/temp/gpu_wait.sh 4300 3600 || return 1
      python3 /app/finetune_data/_wordapp/make_cards.py "$@" 2>&1 \
      | tr '\r' '\n' | grep -vi "warn\|iB/s\||%\|모델 올림\|^ *$"; }
{
for b in $(seq 0 38); do
  echo "════ 카드 묶음 $b / 38  $(date +%m-%d\ %H:%M) ════"
  F 여 리리 $b 300;  F 여 원본:리리 $b 300
  F 남 이한 $b 300;  F 남 원본:이한 $b 300
done
echo "════ 카드 끝 $(date +%m-%d\ %H:%M) ════"
} >> $LOG 2>&1
