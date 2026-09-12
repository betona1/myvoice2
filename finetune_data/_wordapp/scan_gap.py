# -*- coding: utf-8 -*-
"""争夺 처럼 뒤(또는 앞)에 군더더기 소리가 붙은 음성을 소리 크기만으로 빠르게 골라낸다.
   한 단어는 음절이 붙어 나므로, 0.3초 넘게 끊겼다가 다시 소리가 나면 군더더기로 본다."""
import os, sys, json, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf
THR, GAP, MINSEG = 0.012, 0.30, 0.12
con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True)
rows = con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                      WHERE length(chinese)>=2 AND COALESCE(excluded,0)=0 ORDER BY seq""").fetchall()
con.close()
def segs(w, sr):
    e = np.abs(w); v = e > THR
    if not v.any(): return []
    gap = int(sr*GAP); out, st, run = [], None, 0
    for i, x in enumerate(v):
        if x:
            if st is None: st = i
            run = 0
        else:
            if st is not None:
                run += 1
                if run >= gap: out.append((st, i-run+1)); st = None; run = 0
    if st is not None: out.append((st, len(w)))
    return out
sus, n, err = [], 0, 0
for wid, ch, a1, a2 in rows:
    for col, p in (('audio1', a1), ('audio2', a2)):
        if not p or not os.path.exists(p): continue
        n += 1
        try:
            w, sr = sf.read(p, dtype='float32')
            if w.ndim > 1: w = w.mean(1)
        except Exception:
            err += 1; continue
        s = segs(w, sr)
        if len(s) < 2: continue
        big = max(s, key=lambda t: float(np.sum(np.abs(w[t[0]:t[1]])**2)))
        extra = [t for t in s if t is not big and (t[1]-t[0]) >= sr*MINSEG]
        if extra:
            sus.append({'id': wid, 'ch': ch, 'col': col, 'path': p,
                        'segs': len(s), 'extra': len(extra),
                        'where': 'before' if extra[0][0] < big[0] else 'after'})
        if n % 3000 == 0: print(f"  {n}개 훑음 · 의심 {len(sus)}개", flush=True)
out = 'finetune_data/_wordapp/audio_gap.json'
json.dump(sus, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"\n{n}개 중 군더더기 의심 {len(sus)}개 (읽기 실패 {err}개) → {out}")
import collections
print("  위치:", dict(collections.Counter(x['where'] for x in sus)))
print("  보기:", ' '.join(x['ch'] for x in sus[:25]))
