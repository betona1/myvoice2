# -*- coding: utf-8 -*-
"""낱글자 음성에서 **목청이 울리는 구간만** 남긴다.

   「마하혀~」「한숨 쉬듯」으로 들리는 것은 뒤에 숨소리가 붙어서다.
   숨소리는 목청이 울리지 않는다(무성음) — 그래서 소리 세기로는 못 가리고,
   앞서 만든 '가운데 끊김' 잣대로도 안 걸린다(빈틈 없이 이어 붙어 있다).
   목청 울림(voiced)을 보고 그 구간만 남기면 깨끗해진다.
   ⚠️ 무성 자음(p·t·k·s·x…)이 앞머리에 있으니 앞쪽은 넉넉히 남긴다.
   ⚠️ 원본은 .orig 로 남긴다."""
import os, re, sys, json, shutil, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf, librosa
CJK=re.compile(r'[一-鿿]')
ONLY=set(sys.argv[1:])                       # 글자를 주면 그것만
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
rows=[r for r in con.execute("""SELECT chinese,pinyin,audio1,audio2 FROM words
                                WHERE COALESCE(excluded,0)=0""")
      if len(CJK.findall(r['chinese'] or ''))==1 and (not ONLY or r['chinese'] in ONLY)]
fixed=skip=0
for r in rows:
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        w,sr0=sf.read(p)
        if w.ndim>1: w=w.mean(axis=1)
        dur=len(w)/sr0
        if dur < 0.25: continue
        y=librosa.resample(w.astype(np.float32), orig_sr=sr0, target_sr=16000)
        try:
            f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024)
        except Exception:
            skip+=1; continue
        if vo is None: skip+=1; continue
        on=np.where(vo>0.5)[0]
        if len(on)<4: skip+=1; continue
        hop=1024/4/16000                      # pyin 기본 hop 은 frame_length//4 다
                                              # (512 로 잡았다가 구간이 두 배로 나와 아무것도 못 잘랐다)
        a=max(0.0, on[0]*hop - 0.09)          # 앞은 넉넉히 (무성 자음이 있다)
        b=min(dur, on[-1]*hop + 0.06)         # 뒤는 조금만 (숨소리를 끊는다)
        if b-a < 0.20 or (dur-(b-a)) < 0.08:  # 잘라 낼 게 없으면 둔다
            skip+=1; continue
        seg=np.asarray(w[int(a*sr0):int(b*sr0)], dtype=np.float32).copy()
        f=min(int(sr0*0.03), len(seg)//6)
        if f>1:
            seg[:f]*=np.linspace(0,1,f)
            seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
        if not os.path.exists(p+'.orig'): shutil.copy2(p, p+'.orig')
        sf.write(p, seg, sr0)
        fixed+=1
        if ONLY: print(f"  ✓ {r['chinese']} {c}: {dur:.2f}초 → {(b-a):.2f}초")
print(f"\n숨소리를 걷어낸 것 {fixed}개 · 그대로 둔 것 {skip}개")
