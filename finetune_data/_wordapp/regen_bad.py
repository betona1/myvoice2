# -*- coding: utf-8 -*-
"""1초를 넘는(잡음이 섞인) 한 글자 음성만 다시 만든다.
   잡음 구간이 진짜 음절보다 커서 앞선 정리에서 잘못 골린 것들이다.
   여러 번 생성해 '한 음절 길이(0.35~0.95초)에 가장 가까운' 결과를 고른다."""
import os, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf
import main as M
from tts.engine import get_engine

REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
       'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
LO, HI, SR = 0.35, 0.95, 24000

def trim(w, sr=SR, thr=0.012, gap_s=0.22, pad_s=0.05):
    e = np.abs(w); voiced = e > thr
    if not voiced.any(): return None
    gap = int(sr * gap_s); segs, start, run = [], None, 0
    for i, v in enumerate(voiced):
        if v:
            if start is None: start = i
            run = 0
        else:
            if start is not None:
                run += 1
                if run >= gap: segs.append((start, i - run + 1)); start = None; run = 0
    if start is not None: segs.append((start, len(w)))
    if not segs: return None
    a, b = max(segs, key=lambda s: float(np.sum(e[s[0]:s[1]] ** 2)))
    pad = int(sr * pad_s)
    return w[max(0, a - pad):min(len(w), b + pad)]

con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True)
rows = con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                      WHERE length(chinese)=1 AND COALESCE(excluded,0)=0""").fetchall()
con.close()
targets = []
for wid, ch, a1, a2 in rows:
    for col, p in (('audio1', a1), ('audio2', a2)):
        if p and os.path.exists(p) and sf.info(p).duration > 1.0:
            targets.append((wid, ch, col, p))
print(f"다시 만들 음성 {len(targets)}개\n")

eng = get_engine()
fixed = give = 0
for wid, ch, col, p in targets:
    before = sf.info(p).duration
    best, bd = None, 9e9
    for attempt in range(4):                     # 여러 번 뽑아 가장 그럴듯한 것을 고른다
        w = np.array(eng.tts.tts(text=ch, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
        t = trim(w)
        if t is None: continue
        d = len(t) / SR
        score = 0 if LO <= d <= HI else min(abs(d - LO), abs(d - HI))
        if score < bd: bd, best = score, t
        if score == 0: break
    if best is None:
        give += 1; print(f"  {ch} {col}  실패 — 원본 유지"); continue
    sf.write(p, best, SR)
    fixed += 1
    print(f"  {ch} {col}  {before:.2f}s → {len(best)/SR:.2f}s")
print(f"\n교체 {fixed}개 / 실패 {give}개")
