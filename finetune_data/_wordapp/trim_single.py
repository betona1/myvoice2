# -*- coding: utf-8 -*-
"""한 글자 단어 음성에서 덧붙은 군소리를 잘라낸다.
   XTTS 는 글자 하나만 주면 진짜 음절 앞뒤로 잡음을 붙인다(都 → '두' + '씨얼').
   에너지가 가장 큰 덩어리만 남기면 한 음절(0.4~0.7초)로 정리된다.
   원본은 outputs/chinese/words/_orig/ 로 백업하고 제자리에서 덮어쓴다."""
import os, sys, shutil, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf
APPLY = '--apply' in sys.argv
BK = 'outputs/chinese/words/_orig'
os.makedirs(BK, exist_ok=True)

def trim(w, sr, thr=0.012, gap_s=0.22, pad_s=0.05):
    e = np.abs(w)
    voiced = e > thr
    if not voiced.any(): return None
    gap = int(sr * gap_s)
    segs, start, run = [], None, 0
    for i, v in enumerate(voiced):
        if v:
            if start is None: start = i
            run = 0
        else:
            if start is not None:
                run += 1
                if run >= gap:
                    segs.append((start, i - run + 1)); start = None; run = 0
    if start is not None: segs.append((start, len(w)))
    if not segs: return None
    a, b = max(segs, key=lambda s: float(np.sum(e[s[0]:s[1]] ** 2)))
    pad = int(sr * pad_s)
    return w[max(0, a - pad):min(len(w), b + pad)]

con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True)
rows = con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                      WHERE length(chinese)=1 AND COALESCE(excluded,0)=0""").fetchall()
con.close()
print(f"한 글자 단어 {len(rows)}개 · 음성 {len(rows)*2}개\n")

ok = skip = bad = 0
befores, afters = [], []
for wid, ch, a1, a2 in rows:
    for p in (a1, a2):
        if not p or not os.path.exists(p): skip += 1; continue
        try:
            w, sr = sf.read(p, dtype='float32')
        except Exception:
            bad += 1; continue
        if w.ndim > 1: w = w[:, 0]
        d0 = len(w) / sr
        t = trim(w, sr)
        if t is None or len(t) < sr * 0.15:      # 너무 짧게 잘리면 건드리지 않는다
            skip += 1; continue
        d1 = len(t) / sr
        if d1 >= d0 - 0.05:                      # 이미 깔끔하면 그대로
            skip += 1; continue
        befores.append(d0); afters.append(d1)
        if APPLY:
            b = os.path.join(BK, os.path.basename(p))
            if not os.path.exists(b): shutil.copy2(p, b)
            sf.write(p, t, sr)
        ok += 1
import statistics as st
print(f"정리 대상 {ok}개 / 그대로 둠 {skip}개 / 오류 {bad}개")
if befores:
    print(f"평균 길이  {st.mean(befores):.2f}초 → {st.mean(afters):.2f}초")
    print(f"중앙값     {st.median(befores):.2f}초 → {st.median(afters):.2f}초")
print("반영됨" if APPLY else "※ 드라이런 — --apply 로 반영")
