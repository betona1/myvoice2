# -*- coding: utf-8 -*-
"""고친 것들이 실제로 제대로 들리는지 되짚어 본다."""
import os, sys, json, collections
os.chdir('/app'); sys.path.insert(0,'/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from zhjudge import judge
from faster_whisper import WhisperModel
b=json.load(open('finetune_data/_wordapp/audio_bad_multi.json',encoding='utf-8'))
m=WhisperModel("large-v3",device="cuda",device_index=0,compute_type="float16")
still=[]; ok=0
for i,x in enumerate(b,1):
    if not os.path.exists(x['path']): continue
    s,_=m.transcribe(x['path'],language='zh',beam_size=5,best_of=5,temperature=[0.0,0.2],vad_filter=False)
    t=''.join(z.text for z in s).strip(); v=judge(x['ch'],t)
    if v=='ok': ok+=1
    else: still.append(dict(x,heard=t,kind=v))
    if i%400==0: print(f"  {i}/{len(b)} · 통과 {ok}개",flush=True)
json.dump(still,open('finetune_data/_wordapp/audio_still_bad.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n고친 대상 {len(b)}개 중 통과 {ok}개 / 아직 이상 {len(still)}개")
print("  유형:",dict(collections.Counter(x['kind'] for x in still)))
for x in still[:20]: print(f"   [{x['kind']:<6}] {x['ch']:<6}{x['col']:<7} {x['heard'][:24]!r}")
