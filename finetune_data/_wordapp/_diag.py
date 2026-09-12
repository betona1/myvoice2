import os, sys, re, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf, librosa
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
for ch in sys.argv[1:]:
    r=con.execute("SELECT chinese,pinyin,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: continue
    print(f"\n{r['chinese']} ({r['pinyin']})")
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        w,sr0=sf.read(p)
        if w.ndim>1: w=w.mean(axis=1)
        y=librosa.resample(w.astype(np.float32), orig_sr=sr0, target_sr=16000)
        f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024)
        hop=512/16000
        on=np.where(vo>0.5)[0] if vo is not None else []
        a=np.abs(w); v=a>0.012
        i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
        print(f"  {c}: 파일 {len(w)/sr0:.2f}초 · 소리구간 {i0/sr0:.2f}~{i1/sr0:.2f}"
              f" · 울림구간 {(on[0]*hop if len(on) else -1):.2f}~{(on[-1]*hop if len(on) else -1):.2f}"
              f" · 울림비율 {float(np.nanmean(vo)) if vo is not None else 0:.2f}")
