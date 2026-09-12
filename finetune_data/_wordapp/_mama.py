import os, sys, re, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel
import opencc
t2s=opencc.OpenCC('t2s'); CJK=re.compile(r'[一-鿿]')
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
def hear(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, temperature=[0.0], vad_filter=False)
    return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
for ch in ('妈妈','爸爸','妈'):
    r=con.execute("SELECT chinese,pinyin,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: continue
    print(f"{r['chinese']} ({r['pinyin']})")
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): print(f"  {c}: 없음"); continue
        w,sr=sf.read(p)
        if w.ndim>1: w=w.mean(axis=1)
        a=np.abs(w); v=a>0.012
        i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
        body=w[i0:i1+1]
        # 쉰 소리 낌새 — 소리가 고르지 않고 잘게 흔들리면 거칠게 들린다
        zc=float(np.mean(np.abs(np.diff(np.sign(body)))>0))
        rms=float(np.sqrt(np.mean(body**2)))
        print(f"  {c}: {len(w)/sr:.2f}초 · 세기 {rms:.3f} · 거칠기 {zc:.3f} · 들린것 '{hear(p)}'")
