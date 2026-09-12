# -*- coding: utf-8 -*-
"""기준 목소리 세 가지로 같은 낱말을 만들어 견줘 듣는다.
   A = 지금 쓰는 남성  B = 새 녹음 중 가장 깨끗한 한 토막  C = 새 녹음 다섯 개를 함께"""
import os, sys, glob
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
SR=24000
REFS={'A':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
      'B':'voice_samples/male_ref_new.wav',
      'C':sorted(glob.glob('voice_samples/male_new/s*.wav'))}
WORDS=['材料','张','那些','很','一点儿','个','二','哪','妈妈','什么','会',
       '我们明天去图书馆看书。','这个问题有点儿难，但是我会努力。']
OUT='outputs/chinese/_reftest'
os.makedirs(OUT, exist_ok=True)
for f in glob.glob(f'{OUT}/*.wav'): os.remove(f)

def trim(w, thr=0.012, gap_s=0.30, pad_s=0.06):
    e=np.abs(w); v=e>thr
    if not v.any(): return w
    gap=int(SR*gap_s); segs=[]; st=None; run=0
    for i,x in enumerate(v):
        if x: st=i if st is None else st; run=0
        elif st is not None:
            run+=1
            if run>=gap: segs.append((st,i-run+1)); st=None; run=0
    if st is not None: segs.append((st,len(w)))
    if not segs: return w
    a,b=segs[0][0],segs[-1][1]; pad=int(SR*pad_s)
    return w[max(0,a-pad):min(len(w),b+pad)]

from tts.engine import get_engine
eng=get_engine()
for i,txt in enumerate(WORDS):
    for k,ref in REFS.items():
        aw=np.array(eng.tts.tts(text=txt, speaker_wav=ref, language='zh-cn'), dtype=np.float32)
        sf.write(f'{OUT}/{i:02d}{k}.wav', trim(aw), SR)
    print(f"  {txt}", flush=True)
print("끝")
