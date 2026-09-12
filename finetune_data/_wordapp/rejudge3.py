# -*- coding: utf-8 -*-
import json, os, sys, collections
os.chdir('/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from zhjudge import judge
P='finetune_data/_wordapp/audio_bad_multi.json'
b=json.load(open(P,encoding='utf-8'))
keep=[]; freed=[]
for x in b:
    v=judge(x['ch'],x['heard'])
    (freed if v=='ok' else keep).append(dict(x,kind=v))
print(f"보강한 판정으로 {len(freed)}개가 더 통과 → 남은 것 {len(keep)}개")
print("  풀린 보기:", '  '.join(f"{x['ch']}→{x['heard'][:8]}" for x in freed[:14]))
print("  유형:", dict(collections.Counter(x['kind'] for x in keep)))
json.dump(keep,open(P,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
# 두 목소리 중 한쪽만 걸린 단어 = 그 음성만 이상할 가능성이 높다
by=collections.defaultdict(set)
for x in keep: by[x['ch']].add(x['col'])
one=[x for x in keep if len(by[x['ch']])==1]
both=[x for x in keep if len(by[x['ch']])==2]
print(f"\n  한쪽 목소리만 걸림 {len(one)}개  ← 그 음성 자체가 이상할 가능성 높음")
print(f"  두 목소리 다 걸림  {len(both)}개  ← 받아적기가 헷갈린 것일 가능성 높음")
json.dump(one,open('finetune_data/_wordapp/audio_fix_one.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
json.dump(both,open('finetune_data/_wordapp/audio_fix_both.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
