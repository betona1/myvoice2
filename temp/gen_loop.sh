#!/bin/bash
# 컨테이너 안에서 스스로 도는 묶음 고리 — 바깥 세션이 끊겨도 이어진다.
cd /app
BS=200; N=43; START=${1:-20}
LOG=/app/temp/gen_loop.log
# 먼저 도는 묶음이 있으면 끝나기를 기다린다 (GPU 한 장)
while pgrep -f "gen_all_new.py" >/dev/null; do sleep 20; done
F() { python3 /app/finetune_data/_wordapp/gen_all_new.py "$@" 2>&1 | tr '\r' '\n' | grep -vi "warn\|iB/s\||%\|모델 올림\|^ *$"; }
for b in $(seq $START $((N-1))); do
  echo "════ 묶음 $b / $((N-1))  $(date +%m-%d\ %H:%M) ════"
  F 여 리리 $b $BS;  F 여 원본:리리 $b $BS
  F 남 이한 $b $BS;  F 남 원본:이한 $b $BS
done >> $LOG 2>&1
echo "════ 전부 끝 $(date +%m-%d\ %H:%M) ════" >> $LOG
