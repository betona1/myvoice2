# -*- coding: utf-8 -*-
"""파인튜닝한 모델과 지금 쓰는 음성을 같은 낱말로 견준다.
   ⚠️ 10GB 카드라 모델을 하나씩 올렸다 내린다."""
import os, sys, glob
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
sys.path.insert(0,'/app/tts')
import tts.zh_tts as z
SR=24000
WORDS=['哪','哪儿','张','些','那些','很','个','二','会','点',
       '材料','妈妈','什么','一点儿','图书馆',
       '我们明天去图书馆看书。','这个问题有点儿难，但是我会努力。']
OLD={'남':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     '여':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
NEW={'남':'이한','여':'리리'}
OUT='outputs/chinese/_fttest'; os.makedirs(OUT, exist_ok=True)
for f in glob.glob(f'{OUT}/*.wav'): os.remove(f)

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

# 1) 새로 학습한 모델
for sex,name in NEW.items():
    d=f'finetune_data/{name}_zh_model'
    if not os.path.exists(f'{d}/model.pth'):
        print(f'{name}: 학습된 모델이 없다 — 건너뜀'); continue
    for i,t in enumerate(WORDS):
        w=z.say(name, t, ref_dir=f'voice_samples/{name}')
        sf.write(f'{OUT}/{i:02d}_{sex}_new.wav', trim(w), SR)
    print(f'{name}({sex}) 새 모델 {len(WORDS)}개 생성', flush=True)
    z.unload(name)

# 2) 지금 쓰는 음성 (원본 XTTS + 참조)
from tts.engine import get_engine
eng=get_engine()
for sex,ref in OLD.items():
    for i,t in enumerate(WORDS):
        w=np.array(eng.tts.tts(text=t, speaker_wav=ref, language='zh-cn'), dtype=np.float32)
        sf.write(f'{OUT}/{i:02d}_{sex}_old.wav', trim(w), SR)
    print(f'{sex} 지금 음성 {len(WORDS)}개 생성', flush=True)
print('끝')
