# -*- coding: utf-8 -*-
"""끝내 안 고쳐지는 것들. XTTS 는 짧은 말 뒤에 소리를 흘리는 버릇이 있어서,
   문장부호를 붙여 말끝을 맺게 하거나 여러 번 더 시도해 본다."""
import os, sys, json, collections
os.chdir('/app'); sys.path.insert(0,'/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge
from faster_whisper import WhisperModel
from tts.engine import get_engine
SR=24000
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TAIL=['','。','，','！','？','、']          # 말끝을 맺게 하는 부호들
def trim(w, sr=SR, thr=0.012, gap_s=0.28, pad_s=0.06):
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

b=json.load(open('finetune_data/_wordapp/audio_still_bad.json',encoding='utf-8'))
print(f"끈질기게 다시 볼 것 {len(b)}개",flush=True)
m=WhisperModel("large-v3",device="cuda",device_index=0,compute_type="float16")
eng=get_engine()
def hear(p):
    s,_=m.transcribe(p,language='zh',beam_size=5,best_of=5,temperature=[0.0,0.2],vad_filter=False)
    return ''.join(z.text for z in s).strip()
fixed=[]; left=[]
for i,x in enumerate(b,1):
    w,p,col=x['ch'],x['path'],x['col']
    got=None
    for tail in TAIL:
        for _ in range(3):
            try:
                aw=np.array(eng.tts.tts(text=w+tail,speaker_wav=REF[col],language='zh-cn'),dtype=np.float32)
            except Exception:
                continue
            t=trim(aw)
            if t is None or len(t)<SR*0.25: continue
            sf.write('/tmp/_s.wav',t,SR)
            if judge(w,hear('/tmp/_s.wav'))=='ok': got=t; break
        if got is not None: break
    if got is None: left.append(x)
    else: sf.write(p,got,SR); fixed.append(x['ch'])
    if i%40==0: print(f"  {i}/{len(b)} · 고침 {len(fixed)}개",flush=True)
json.dump(left,open('finetune_data/_wordapp/audio_still_bad.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n추가로 고친 것 {len(fixed)}개 / 끝내 못 고친 것 {len(left)}개")
print("  못 고친 것:", '  '.join(f"{x['ch']}({x['col'][-1]})" for x in left[:40]))
