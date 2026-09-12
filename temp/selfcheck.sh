#!/bin/bash
# 스스로 검사 → 고치기 → 다시 검사, 세 판까지. 나아지지 않으면 멈춘다.
# ⚠️ GPU 는 gpu_wait.sh 로 자리가 날 때만 잡는다 (남의 작업도, 내 작업도 안 죽인다).
cd /app; LOG=/app/temp/selfcheck.log
A() { bash /app/temp/gpu_wait.sh 4000 7200 || return 1
      ROUND=$R python3 /app/finetune_data/_wordapp/audit_audio.py "$@" 2>&1 \
      | tr '\r' '\n' | grep -vi "warn\|iB/s\||%\|모델 올림\|^ *$"; }
F() { bash /app/temp/gpu_wait.sh 4300 7200 || return 1
      TRIES=8 python3 /app/finetune_data/_wordapp/fix_audit.py "$@" 2>&1 \
      | tr '\r' '\n' | grep -vi "warn\|iB/s\||%\|모델 올림\|^ *$" | tail -60; }
{
for R in 1 2 3; do
  echo "════════ 자가검사 $R 판  $(date +%m-%d\ %H:%M) ════════"
  for K in word ex; do for S in 여 남; do A $K $S; done; done
  echo "──── 판 $R 결과 ────"
  python3 - <<'PY'
import sqlite3
con=sqlite3.connect('/app/database/voices.db')
for k in ('word','ex'):
    for s in ('남','여'):
        t=con.execute("SELECT COUNT(*) FROM audio_audit WHERE kind=? AND sex=?",(k,s)).fetchone()[0]
        b=con.execute("SELECT COUNT(*) FROM audio_audit WHERE kind=? AND sex=? AND ok=0",(k,s)).fetchone()[0]
        if t: print(f'  {k} {s}: 흠 {b}/{t} = {b/t*100:.1f}%')
print('  까닭 상위:')
for r in con.execute("SELECT why,COUNT(*) c FROM audio_audit WHERE ok=0 GROUP BY why ORDER BY c DESC LIMIT 10"):
    print(f'    {r[1]:6d}  {r[0]}')
PY
  BAD=$(python3 -c "
import sqlite3;con=sqlite3.connect('/app/database/voices.db')
print(con.execute('SELECT COUNT(*) FROM audio_audit WHERE ok=0').fetchone()[0])")
  echo "  이번 판 흠 $BAD 개"
  [ "$BAD" -eq 0 ] && { echo '흠 없음 — 마침'; break; }
  for K in word ex; do for S in 여 남; do
    W=$([ "$S" = 여 ] && echo 리리 || echo 이한)
    F $K $S $W; F $K $S "원본:$W"
  done; done
done
echo "════════ 자가검사 끝 $(date +%m-%d\ %H:%M) ════════"
} >> $LOG 2>&1
