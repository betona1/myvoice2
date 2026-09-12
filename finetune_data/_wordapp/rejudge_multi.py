# -*- coding: utf-8 -*-
"""받아적힌 글을 다시 따져 본다. Whisper 가 흔히 헷갈리는 소리
   (n/ng, 권설음 zh·ch·sh, n/l, 얼화 儿 생략)는 같은 것으로 친다."""
import json, re, os, collections
os.chdir('/app')
from pypinyin import pinyin as _py, Style
M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
   'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'u','ǘ':'u','ǚ':'u','ǜ':'u','ü':'u'}
def sy(s): return [re.sub(r'[^a-z]','',''.join(M.get(c,c) for c in x[0].lower())) for x in _py(s,style=Style.NORMAL)]
def loose(s):
    s = s.replace('zh','z').replace('ch','c').replace('sh','s')
    if s.endswith('ng'): s = s[:-1]
    if s[:1] == 'l': s = 'n'+s[1:]
    return s
def same(a, b): return loose(a) == loose(b)

def erless(s): return re.sub(r'[儿兒]$','',s) or s
def judge(word, heard):
    w, t = erless(word), erless(heard)
    if not t: return 'silent'
    if t == w: return 'ok'
    a, b = sy(w), sy(t)
    if len(a) == len(b):
        return 'ok' if all(same(x,y) for x,y in zip(a,b)) else 'wrong'
    # 길이가 다르면: 단어가 통째로 들어 있는지 본다
    if len(b) > len(a):
        for i in range(len(b)-len(a)+1):
            if all(same(x,y) for x,y in zip(a, b[i:i+len(a)])): return 'extra'
        return 'wrong'
    return 'short'          # 일부만 들림

p='finetune_data/_wordapp/audio_bad_multi.json'
b=json.load(open(p,encoding='utf-8'))
out=collections.defaultdict(list)
for x in b:
    out[judge(x['ch'], x['heard'])].append(x)
print("다시 따진 결과:", {k:len(v) for k,v in sorted(out.items())})
real=[dict(x, kind=k) for k,v in out.items() if k!='ok' for x in v]
json.dump(real, open(p,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"\n실제 손볼 것 {len(real)}개 (통과로 돌린 것 {len(out['ok'])}개)")
for k in ('extra','short','silent','wrong'):
    v=out[k]
    if v: print(f"\n[{k}] {len(v)}개 — " + '  '.join(f"{x['ch']}→{x['heard'][:8] or '무음'}" for x in v[:12]))
