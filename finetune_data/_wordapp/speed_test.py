# -*- coding: utf-8 -*-
"""남성 음성 속도를 견줘 듣기 위한 표본을 만든다.
   같은 낱말을 1.0 / 0.95 / 0.90 / 0.85 로 만들어 나란히 둔다."""
import os, sys
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from tts.engine import get_engine
SR=24000
REF='voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav'
OUT='outputs/chinese/_speed'; os.makedirs(OUT, exist_ok=True)
WORDS=['什么','很','妈妈','材料','学校','这个','谢谢']
SPEEDS=[1.0, 0.95, 0.90, 0.85]
eng=get_engine()
def trim(w, thr=0.012, pad=0.06):
    a=np.abs(w); v=a>thr
    if not v.any(): return w
    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
    p=int(SR*pad)
    return w[max(0,i0-p):min(len(w), i1+p)]
for w in WORDS:
    for sp in SPEEDS:
        try:
            aw=np.array(eng.tts.tts(text=w, speaker_wav=REF, language='zh-cn', speed=sp),
                        dtype=np.float32)
        except TypeError:
            aw=np.array(eng.tts.tts(text=w, speaker_wav=REF, language='zh-cn'), dtype=np.float32)
            print('  ⚠️ speed 를 안 받는다 — 뒤에서 늘려야 한다'); sp=None
        t=trim(aw)
        name=f"{w}_{str(sp).replace('.','')}.wav"
        sf.write(os.path.join(OUT,name), t, SR)
        print(f"  {w} 속도 {sp}: {len(t)/SR:.2f}초", flush=True)
print('끝')
