# -*- coding: utf-8 -*-
"""꾸러미마다 '언제 것인가'를 적어 둔다.
   자료는 계속 고쳐진다(HSK 음성은 아직 전수검사 전이다).
   앱이 구운 채로 들고 있으면 못 고치므로, 판을 붙여 바뀐 것만 다시 받게 한다.
     data_version  전체 판 (하나라도 고치면 올라간다)
     lesson.rev    그 하루치의 sha256 앞 12자 — 이것만 견주면 된다
     lesson.built  만든 때
   앱은 rev 가 다를 때만 다시 받으면 된다."""
import os, json, time
BASE='/app/finetune_data/_export'
p=os.path.join(BASE,'index.json')
idx=json.load(open(p,encoding='utf-8'))
idx['data_version']=time.strftime('%Y%m%d-%H%M')
idx['verified']={"양사":"낱글자 음성 전수검사 통과",
                 "hsk1":"미검사","hsk2":"미검사","hsk3":"미검사",
                 "hsk4":"미검사","hsk5":"미검사","hsk6":"미검사"}
n=0
for c in idx['courses']:
    newest=0
    for l in c['lessons']:
        f=os.path.join(BASE,l['file'])
        if os.path.exists(f):
            l['rev']=l['sha256'][:12]
            l['built']=time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(f)))
            newest=max(newest, os.path.getmtime(f)); n+=1
    c['built']=time.strftime('%Y-%m-%d', time.localtime(newest)) if newest else ''
    c['verified']=idx['verified'].get(c['id'],'미검사')
json.dump(idx, open(p,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"판 표시 완료 — data_version {idx['data_version']} · 하루치 {n}개")
for c in idx['courses']:
    print(f"  {c['label']:8s} {c['days']:3d}일 · {c['verified']}")
