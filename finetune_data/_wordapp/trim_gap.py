# -*- coding: utf-8 -*-
"""가운데가 끊긴 낱글자 음성에서 **첫 소리 덩어리만** 남긴다.
   XTTS 는 글자를 홀로 읽을 때 소리를 내고 한참 쉬었다가 군더더기를 덧붙이는 버릇이 있다
   (一 이 1.72초인데 가운데 1.17초가 비어 있었다). 뒤쪽이 잡음처럼 들린다.
   ⚠️ 첫 덩어리가 너무 짧으면(0.18초 미만) 건드리지 않는다 — 그건 다른 문제다.
   ⚠️ 원본은 .orig 로 남긴다."""
import os, re, json, shutil, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf
CJK=re.compile(r'[一-鿿]')
MINLEN=0.18
def first_chunk(w, sr, thr=0.012, gap_s=0.15, pad=0.05):
    a=np.abs(w); v=a>thr
    if not v.any(): return None
    gap=int(sr*gap_s); st=None; run=0
    for i,x in enumerate(v):
        if x:
            if st is None: st=i
            run=0
        elif st is not None:
            run+=1
            if run>=gap:
                return st, i-run+1
    return (st, len(w)) if st is not None else None
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
bad=json.load(open('finetune_data/_wordapp/gap_bad.json',encoding='utf-8'))
todo={(b[0], b[2]) for b in bad if '가운데끊김' in b[4]}
fixed=skip=0
for ch, col in sorted(todo):
    r=con.execute("SELECT audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: continue
    p=r[col]
    if not (p and os.path.exists(p)): continue
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    e=first_chunk(w, sr)
    if not e: skip+=1; continue
    a,b=e
    if (b-a)/sr < MINLEN: skip+=1; continue
    pad=int(sr*0.05)
    seg=np.asarray(w[max(0,a-int(sr*0.02)):min(len(w), b+pad)], dtype=np.float32).copy()
    f=min(int(sr*0.04), len(seg)//5)
    if f>1: seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
    if not os.path.exists(p+'.orig'): shutil.copy2(p, p+'.orig')
    sf.write(p, seg, sr)
    fixed+=1
print(f"뒤 군더더기를 잘라 낸 것 {fixed}개 · 그대로 둔 것 {skip}개")
