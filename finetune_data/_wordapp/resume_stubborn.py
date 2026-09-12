# -*- coding: utf-8 -*-
"""재시작으로 끊긴 자리에서 이어간다. 이미 고쳐진 것은 되짚어 보고 목록에서 뺀다."""
import os, sys, json, collections
os.chdir('/app'); sys.path.insert(0,'/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from zhjudge import judge
from faster_whisper import WhisperModel
P='finetune_data/_wordapp/audio_still_bad.json'
b=json.load(open(P,encoding='utf-8'))
m=WhisperModel("large-v3",device="cuda",device_index=0,compute_type="float16")
left=[]; done=0
for i,x in enumerate(b,1):
    if not os.path.exists(x['path']): continue
    s,_=m.transcribe(x['path'],language='zh',beam_size=5,best_of=5,temperature=[0.0,0.2],vad_filter=False)
    t=''.join(z.text for z in s).strip()
    if judge(x['ch'],t)=='ok': done+=1
    else: left.append(dict(x,heard=t,kind=judge(x['ch'],t)))
json.dump(left,open(P,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"이미 고쳐진 것 {done}개 확인 · 남은 것 {len(left)}개",flush=True)
