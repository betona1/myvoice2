# -*- coding: utf-8 -*-
"""확실한 결함부터 고치도록 대상을 추린다."""
import json, os, collections
os.chdir('/app')
b=json.load(open('finetune_data/_wordapp/audio_bad_multi.json',encoding='utf-8'))
g=json.load(open('finetune_data/_wordapp/audio_gap.json',encoding='utf-8'))   # 소리 끊김으로 잡은 것
by=collections.defaultdict(set)
for x in b: by[x['ch']].add(x['col'])
pick, why = [], collections.Counter()
seen=set()
def add(x, tag):
    k=(x['path'],)
    if k in seen: return
    seen.add(k); pick.append(x); why[tag]+=1
for x in b:
    if x['kind'] in ('extra','short','silent'): add(x, f"뚜렷한 결함({x['kind']})")
for x in b:
    if x['kind']=='wrong' and len(by[x['ch']])==1: add(x, "한쪽 목소리만 이상")
for x in g:
    add(dict(x, heard='', kind='extra'), "앞뒤에 딴소리 붙음")
json.dump(pick,open('finetune_data/_wordapp/audio_bad_multi.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"고칠 대상 {len(pick)}개")
for k,v in why.most_common(): print(f"   {k:<22}{v:>5}개")
