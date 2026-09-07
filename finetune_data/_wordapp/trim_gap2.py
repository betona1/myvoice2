# -*- coding: utf-8 -*-
"""남은 것들은 **첫 덩어리가 군더더기**이고 진짜 소리가 뒤에 있다.
   (앞 판에서 '첫 덩어리만 남기기'로 240개를 고쳤지만, 이런 것들은 그 잣대로는 못 고친다)
   그래서 덩어리 가운데 **가장 센 것**을 고른다 — 사람이 읽은 음절이 제일 크다."""
import os, re, json, shutil, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf
def chunks(w, sr, thr=0.012, gap_s=0.15):
    a=np.abs(w); v=a>thr
    out=[]; st=None; run=0
    for i,x in enumerate(v):
        if x:
            if st is None: st=i
            run=0
        elif st is not None:
            run+=1
            if run>=gap_s*sr: out.append((st, i-run+1)); st=None; run=0
    if st is not None: out.append((st, len(w)))
    return out
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
bad=json.load(open('finetune_data/_wordapp/gap_bad2.json',encoding='utf-8'))
fixed=skip=0
for ch, py, col, dur, why in bad:
    r=con.execute("SELECT audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: continue
    p=r[col]
    if not (p and os.path.exists(p)): continue
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    cs=[c for c in chunks(w,sr) if (c[1]-c[0])/sr>=0.18]
    if not cs: skip+=1; continue
    a,b=max(cs, key=lambda c: float(np.sum(np.abs(w[c[0]:c[1]])**2)))   # 가장 센 덩어리
    seg=np.asarray(w[max(0,a-int(sr*0.02)):min(len(w), b+int(sr*0.05))], dtype=np.float32).copy()
    if len(seg)/sr < 0.2: skip+=1; continue
    f=min(int(sr*0.04), len(seg)//5)
    if f>1: seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
    if not os.path.exists(p+'.orig'): shutil.copy2(p, p+'.orig')
    sf.write(p, seg, sr); fixed+=1
print(f"가장 센 덩어리만 남긴 것 {fixed}개 · 그대로 둔 것 {skip}개")
