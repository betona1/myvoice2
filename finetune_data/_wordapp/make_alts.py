# -*- coding: utf-8 -*-
"""검사는 통과하는데 귀로는 이상한 문장 — 통과하는 다른 후보를 2개 더 만들어 골라 듣게 한다.
   쓰임: make_alts.py <성별> <화자> <모델> <온도> 번호 번호 …"""
import os, sys, re, importlib.util
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf
# make_sents 의 검사 함수들을 그대로 쓴다 (모듈처럼 불러오되 본문은 안 돌게 argv 를 흉내낸다)
sys.argv_backup = sys.argv[:]
sex, who, MODEL, TEMP = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]); ids = [int(x) for x in sys.argv[5:]]
src = open('/app/finetune_data/_wordapp/make_sents.py', encoding='utf-8').read()
head = src[:src.index('for si, s in enumerate(sents):')]
sys.argv = ['x', sex, who, MODEL, str(TEMP), 'new']
ns = {}; exec(compile(head, 'make_sents_head', 'exec'), ns)
z = ns['z']; SR = ns['SR']; HAN = ns['HAN']; hear_words = ns['hear_words']; end_dip = ns['end_dip']
trim_to_onset = ns['trim_to_onset']; verify = ns['verify']; sents = ns['sents']; OUT = ns['OUT']
TRIES = int(os.environ.get('TRIES', '14'))
for si in ids:
    s = sents[si - 1]; want = HAN(s); got = []
    for t in range(TRIES):
        if len(got) >= 2: break
        w = z.say(MODEL, s, ref_dir=f'voice_samples/{who}', temperature=TEMP, max_seconds=8)
        words = hear_words(w)
        if not words or not HAN(''.join(x[0] for x in words)).startswith(want): continue
        acc = ''; end_t = None
        for txt, a, b in words:
            acc += HAN(txt)
            if len(acc) >= len(want): end_t = b; break
        start, _ = z._refine(w, words[0][1], end_t, None); end = end_dip(w, end_t)
        cut = trim_to_onset(w[int(start * SR):int(end * SR)])
        why = verify(cut, want)
        if why: continue
        got.append(cut)
    for k, c in enumerate(got):
        sf.write(f'{OUT}/문장{si:02d}_{sex}_new{k+2}.wav', c, SR)
    print(f'  {si:02d} {s} 대안 {len(got)}개', flush=True)
