# -*- coding: utf-8 -*-
"""세 번째 후보: 원본 XTTS + 새 녹음을 참조로 (학습 없이 목소리만 본뜨기).
   파인튜닝한 모델이 짧은 낱말에서 말을 못 멈추는 문제가 있어 견줄 거리를 하나 더 둔다."""
import os, sys, glob
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
SR=24000
WORDS=['哪','哪儿','张','些','那些','很','个','二','会','点',
       '材料','妈妈','什么','一点儿','图书馆',
       '我们明天去图书馆看书。','这个问题有点儿难，但是我会努力。']
OUT='outputs/chinese/_fttest'
def trim(w, thr=0.012, gap_s=0.35, pad_s=0.06):
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
for sex,who in (('남','이한'),('여','리리')):
    refs=sorted(glob.glob(f'voice_samples/{who}/*.wav'))
    for i,t in enumerate(WORDS):
        w=np.array(eng.tts.tts(text=t, speaker_wav=refs, language='zh-cn'), dtype=np.float32)
        sf.write(f'{OUT}/{i:02d}_{sex}_clone.wav', trim(w), SR)
    print(f'{who}({sex}) 복제 {len(WORDS)}개 생성', flush=True)
print('끝')
