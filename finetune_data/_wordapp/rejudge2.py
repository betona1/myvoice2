# -*- coding: utf-8 -*-
"""숫자 표기(60 → 六十/六零)와 얼화·권설음 흔들림을 감안해 다시 판정한다."""
import json, re, os, collections, soundfile as sf
os.chdir('/app')
from pypinyin import pinyin as _py, Style
M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
   'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'u','ǘ':'u','ǚ':'u','ǜ':'u','ü':'u'}
def sy(s): return [re.sub(r'[^a-z]','',''.join(M.get(c,c) for c in x[0].lower())) for x in _py(s,style=Style.NORMAL)]
def loose(s):
    s=s.replace('zh','z').replace('ch','c').replace('sh','s')
    if s.endswith('ng'): s=s[:-1]
    if s[:1]=='l': s='n'+s[1:]
    return s
def same(a,b): return loose(a)==loose(b)

DIG='零一二三四五六七八九'
def pos(n):
    """60 → 六十, 18 → 十八 (중국식 자릿수 읽기)"""
    if n<10: return DIG[n]
    if n<20: return '十'+(DIG[n%10] if n%10 else '')
    if n<100: return DIG[n//10]+'十'+(DIG[n%10] if n%10 else '')
    if n<1000:
        r=DIG[n//100]+'百'; m=n%100
        return r if not m else r+('零'+DIG[m] if m<10 else pos(m))
    return None
def cands(text):
    """들린 글에서 한자만 남기되, 아라비아 숫자는 두 가지 읽기를 모두 후보로 둔다."""
    parts=re.findall(r'[0-9]+|[一-鿿]', text or '')
    outs=['']
    for p in parts:
        if p.isdigit():
            v=[''.join(DIG[int(c)] for c in p)]
            q=pos(int(p)) if len(p)<=4 and int(p)<1000 else None
            if q and q not in v: v.append(q)
        else: v=[p]
        outs=[a+b for a in outs for b in v][:8]
    return [o for o in outs if o]

def erless(s): return re.sub(r'[儿兒]$','',s) or s
def cmp1(w,t):
    a,b=sy(w),sy(t)
    if len(a)==len(b): return 'ok' if all(same(x,y) for x,y in zip(a,b)) else 'wrong'
    if len(b)>len(a):
        for i in range(len(b)-len(a)+1):
            if all(same(x,y) for x,y in zip(a,b[i:i+len(a)])): return 'extra'
        return 'wrong'
    return 'short'
RANK={'ok':0,'extra':1,'short':2,'wrong':3}
def judge(word,heard):
    cs=cands(heard)
    if not cs: return 'silent'
    w=erless(word)
    return min((cmp1(w,erless(c)) for c in cs), key=lambda k:RANK[k])

p='finetune_data/_wordapp/audio_bad_multi.json'
b=json.load(open(p,encoding='utf-8'))
out=collections.defaultdict(list)
for x in b: out[judge(x['ch'],x['heard'])].append(x)
print("다시 따진 결과:", {k:len(v) for k,v in sorted(out.items())})
# 무음으로 나온 것은 파일 길이를 재서 진짜 빈 것인지 본다
for x in out['silent']:
    try:
        d,sr=sf.read(x['path']); x['dur']=round(len(d)/sr,2)
    except Exception: x['dur']=-1
short_files=[x for x in out['silent'] if 0<=x['dur']<0.45]
print(f"  무음 판정 {len(out['silent'])}개 중 파일이 실제로 아주 짧은 것 {len(short_files)}개")
real=[dict(x,kind=k) for k,v in out.items() if k!='ok' for x in v]
json.dump(real,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n실제 손볼 것 {len(real)}개 (이번에 통과로 돌린 것 {len(out['ok'])}개)")
for k in ('extra','short','silent','wrong'):
    v=out[k]
    if v: print(f"\n[{k}] {len(v)}개 — "+'  '.join(f"{x['ch']}→{x['heard'][:9] or '무음'}" for x in v[:14]))
