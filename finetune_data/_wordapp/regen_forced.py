# -*- coding: utf-8 -*-
"""XTTS 는 한자를 pypinyin 방식으로 읽으므로, 다음자를 잘못 읽는 단어는
   같은 소리의 다른 글자로 바꿔 넣어 소리를 강제한 뒤 원래 자리에 저장한다.
   예) 系领带 → pypinyin 이 xì 로 읽는다. 记领带 로 넣어 jì 소리를 얻는다."""
import os, sys, json, sqlite3
os.chdir('/app'); sys.path.insert(0,'/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from tts.engine import get_engine
from faster_whisper import WhisperModel
SR=24000
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
# (DB 한자, XTTS 에 넣을 글자) — 소리는 같고 pypinyin 이 제대로 읽는 것으로 고른다
JOBS = json.load(open('finetune_data/_wordapp/forced_jobs.json', encoding='utf-8'))

def trim(w, sr=SR, thr=0.012, gap_s=0.30, pad_s=0.06):
    e=np.abs(w); v=e>thr
    if not v.any(): return None
    gap=int(sr*gap_s); segs,st,run=[],None,0
    for i,x in enumerate(v):
        if x:
            if st is None: st=i
            run=0
        else:
            if st is not None:
                run+=1
                if run>=gap: segs.append((st,i-run+1)); st=None; run=0
    if st is not None: segs.append((st,len(w)))
    if not segs: return None
    a,b=max(segs,key=lambda s:float(np.sum(e[s[0]:s[1]]**2)))
    pad=int(sr*pad_s)
    return w[max(0,a-pad):min(len(w),b+pad)]

con=sqlite3.connect('file:database/voices.db?mode=ro',uri=True); con.row_factory=sqlite3.Row
eng=get_engine()
m=WhisperModel('large-v3',device='cuda',device_index=0,compute_type='float16')
def hear(p):
    s,_=m.transcribe(p,language='zh',beam_size=5,best_of=5,temperature=[0.0,0.2],vad_filter=False)
    return ''.join(z.text for z in s).strip()

for word, spoken in JOBS:
    r=con.execute("SELECT audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(word,)).fetchone()
    if not r: print(f"  {word}: DB에 없음"); continue
    print(f"\n{word}  ← '{spoken}' 로 소리를 만든다")
    for col in ('audio1','audio2'):
        p=r[col]
        if not p: print(f"   {col}: 경로 없음"); continue
        best=None
        for _ in range(4):
            aw=np.array(eng.tts.tts(text=spoken, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
            t=trim(aw)
            if t is None or len(t)<SR*0.35: continue
            sf.write('/tmp/_f.wav',t,SR)
            h=hear('/tmp/_f.wav')
            if len(h)>=2: best=(t,h); break
            if best is None: best=(t,h)
        if best is None: print(f"   {col}: 만들기 실패"); continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        sf.write(p,best[0],SR)
        print(f"   {col}: {len(best[0])/SR:.2f}초 · 들린 소리 {best[1][:18]!r} → {p}")
con.close()
