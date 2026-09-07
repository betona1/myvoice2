# -*- coding: utf-8 -*-
"""낱글자 음성 가운데가 끊긴 것을 찾는다.
   한 음절은 쉬지 않는다 — 소리 한가운데 0.15초 넘는 빈틈이 있으면 잘못 만들어진 것이다.
   (很 남성이 1.61초에 가운데 0.8초가 비어 「쉬었다 나오는」 소리였다)
   함께 잰다: 너무 긺(한 음절에 1.2초 넘음), 잡음 낌새(소리가 고르지 않음)."""
import os, re, json, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
def look(p, thr=0.012):
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    a=np.abs(w); v=a>thr
    if not v.any(): return None
    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
    gap=0; run=0
    for x in v[i0:i1]:
        if not x: run+=1; gap=max(gap,run)
        else: run=0
    body=a[i0:i1+1]
    # 잡음 낌새 — 소리가 고르게 이어지지 않고 잘게 흩어져 있으면 지지직거린다
    zc=float(np.mean(np.abs(np.diff(np.sign(w[i0:i1+1])))>0)) if i1>i0 else 0
    return (i1-i0)/sr, gap/sr, zc, len(w)/sr
rows=[r for r in con.execute("""SELECT chinese,pinyin,audio1,audio2 FROM words
                                WHERE COALESCE(excluded,0)=0""")
      if len(CJK.findall(r['chinese'] or ''))==1]
bad=[]; n=0
for r in rows:
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        v=look(p)
        if not v: continue
        n+=1
        span, gap, zc, dur = v
        why=[]
        if gap>0.15: why.append(f'가운데끊김 {gap:.2f}s')
        if span>1.2: why.append(f'너무긺 {span:.2f}s')
        if zc>0.35:  why.append(f'잡음낌새 {zc:.2f}')
        if why: bad.append([r['chinese'], r['pinyin'], c, round(dur,2), ' · '.join(why)])
print(f"낱글자 음성 {n}개 중 손볼 것 {len(bad)}개\n")
for ch,py,c,d,why in bad[:40]:
    print(f"  {ch} ({py}) {c[-1]}  {d:.2f}초  — {why}")
json.dump(bad, open('finetune_data/_wordapp/gap_bad.json','w',encoding='utf-8'), ensure_ascii=False)
