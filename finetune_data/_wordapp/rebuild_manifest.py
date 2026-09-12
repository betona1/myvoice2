# -*- coding: utf-8 -*-
"""voicepick 폴더를 훑어 manifest.json 을 다시 쓴다. 순서: 처음 15개 → 150개 → 긴 글."""
import os, json
os.chdir('/app'); N = 'outputs/chinese/voicepick'
W = ['哪','哪儿','张','些','那些','很','个','二','会','点','材料','妈妈','什么','一点儿','图书馆']
W += [l.strip() for l in open('temp/voicesample/w150.txt', encoding='utf-8') if l.strip()]
SENTS = [l.strip() for l in open('temp/voicesample/sents.txt', encoding='utf-8') if l.strip()]
W = [f'문장{i+1:02d}' for i in range(len(SENTS))] + ['긴글', '긴글·차분', '긴글·원본'] + W + ['我们明天去图书馆看书。','这个问题有点儿难，但是我会努力。']
man = []
for t in W:
    e = {'word': t, 'files': {}}
    if t.startswith('문장'):
        e['text'] = SENTS[int(t[2:]) - 1]; e['label'] = f'짧은 문장 {int(t[2:])}'
    if t.startswith('긴글'):
        e['label'] = {'긴글': '긴 글 — 학습 모델', '긴글·차분': '긴 글 — 학습 모델, 더 차분하게',
                      '긴글·원본': '긴 글 — 원본 모델에 목소리만 본뜸'}[t]
        e['text'] = ('现在是二零二六年九月八号，星期二，上午十点半。我住在北京市朝阳区，电话号码是一三八，六六七九，四五二零。'
                     '我今年三十五岁，在一家公司工作了七年。每天早上六点四十起床，七点二十出门，八点到公司。'
                     '下班以后有时候去跑步，跑三公里大概要二十五分钟。')
    for sex in ('남', '여'):
        for k in ('old', 'clone', 'new', 'new2', 'new3'):
            fn = f'{t}_{sex}_{k}.wav'
            if os.path.exists(f'{N}/{fn}'):
                e['files'][f'{sex}_{k}'] = fn
                e.setdefault('mtime', {})[f'{sex}_{k}'] = int(os.path.getmtime(f'{N}/{fn}'))
    if e['files']: man.append(e)
json.dump(man, open(f'{N}/manifest.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'목록 {len(man)}항목')
