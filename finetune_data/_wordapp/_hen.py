import os, sys, re, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel
import opencc
t2s=opencc.OpenCC('t2s'); CJK=re.compile(r'[一-鿿]')
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
def hear(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                       temperature=[0.0,0.2], vad_filter=False)
    return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
for ch in ('很','非常','太'):
    r=con.execute("SELECT chinese,pinyin,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: continue
    print(f"\n{r['chinese']} ({r['pinyin']})")
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): print(f"  {c}: 없음"); continue
        w,sr=sf.read(p)
        if w.ndim>1: w=w.mean(axis=1)
        a=np.abs(w); v=a>0.012
        i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
        # 소리가 난 구간 안에서 잠깐 끊긴 데가 있나 (숨 넘어가는 느낌의 정체)
        gaps=[]
        run=0
        for x in v[i0:i1]:
            if not x: run+=1
            else:
                if run> sr*0.05: gaps.append(round(run/sr,2))
                run=0
        print(f"  {c}: {len(w)/sr:.2f}초 · 앞빈틈 {i0/sr:.2f} · 뒤빈틈 {(len(w)-i1)/sr:.2f}"
              f" · 중간끊김 {gaps} · 들린것 '{hear(p)}'")
