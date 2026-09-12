# -*- coding: utf-8 -*-
"""걸린 것만 더 꼼꼼한 설정으로 다시 받아적어 본다. 음성 파일은 건드리지 않는다."""
import os, sys, json, collections
os.chdir('/app'); sys.path.insert(0,'/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from zhjudge import judge
from faster_whisper import WhisperModel
P='finetune_data/_wordapp/audio_bad_multi.json'
b=json.load(open(P,encoding='utf-8'))
m=WhisperModel("large-v3",device="cuda",device_index=0,compute_type="float16")
keep=[]; freed=0
for i,x in enumerate(b,1):
    if not os.path.exists(x['path']): continue
    segs,_=m.transcribe(x['path'],language='zh',beam_size=5,best_of=5,
                        temperature=[0.0,0.2,0.4],vad_filter=False)
    t=''.join(s.text for s in segs).strip()
    v=judge(x['ch'],t)
    if v=='ok': freed+=1
    else: keep.append(dict(x,heard=t,kind=v))
    if i%300==0: print(f"  {i}/{len(b)} · 통과로 풀린 것 {freed}개",flush=True)
json.dump(keep,open(P,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n다시 들어 보니 {freed}개는 제대로 읽은 것이었고, {len(keep)}개가 남았습니다.")
print("  유형:",dict(collections.Counter(x['kind'] for x in keep)))
for x in keep[:25]: print(f"   [{x['kind']:<6}] {x['ch']:<6}{x['col']:<7} {x['heard'][:24]!r}")
