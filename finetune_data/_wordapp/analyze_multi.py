# -*- coding: utf-8 -*-
import json, re, os, sys, collections
os.chdir('/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from zhjudge import syls, loose
b=json.load(open('finetune_data/_wordapp/audio_bad_multi.json',encoding='utf-8'))
def lev(a,b):
    d=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        p,d[0]=d[0],i
        for j,y in enumerate(b,1):
            p,d[j]=d[j],min(d[j]+1,d[j-1]+1,p+(x!=y))
    return d[-1]
cat=collections.Counter(); ex={}
for x in b:
    h=x['heard'] or ''
    hz=re.sub(r'[^一-鿿]','',h)
    tgt=''.join(loose(s) for s in syls(x['ch']))
    if not h: k='아무것도 안 들림'
    elif not hz: k='로마자로만 적힘'
    else:
        got=''.join(loose(s) for s in syls(hz))
        d=lev(tgt,got)
        k = '거의 같음(1자 차이)' if d<=1 else ('비슷함(2자 차이)' if d==2 else '전혀 다름')
    cat[k]+=1; ex.setdefault(k,[]).append(f"{x['ch']}→{h[:12] or '무음'}")
for k,n in cat.most_common():
    print(f"  {k:<16} {n:>5}개   " + '  '.join(ex[k][:6]))
