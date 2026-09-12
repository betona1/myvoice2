# -*- coding: utf-8 -*-
"""'뜻'이 사실은 병음을 한글로 음역한 것인지 탐지.
  分 fēn → '펜'(pen),  本 běn → '벤'(ben)
한글을 로마자로 근사 변환해 병음과 비교한다. 편집거리가 가깝고 뜻이 짧으면 오역."""
import os, re, sqlite3, sys, unicodedata
os.chdir('/app')
DB='database/voices.db'

CHO=['g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h']
JUNG=['a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i']
JONG=['','g','k','ks','n','nj','nh','d','l','lg','lm','lb','ls','lt','lp','lh','m','b','ps','s','ss','ng','j','ch','k','t','p','h']

def rom(s):
    out=[]
    for c in s:
        o=ord(c)
        if 0xAC00<=o<=0xD7A3:
            i=o-0xAC00
            out.append(CHO[i//588]+JUNG[(i%588)//28]+JONG[i%28])
        elif c.isalpha(): out.append(c.lower())
    return ''.join(out)

def strip_tone(p):
    M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
       'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'v','ǘ':'v','ǚ':'v','ǜ':'v','ü':'v'}
    return ''.join(M.get(c,c) for c in (p or '').lower() if c.isalpha() or c in M)

def lev(a,b):
    if a==b: return 0
    if not a or not b: return max(len(a),len(b))
    prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        cur=[i]
        for j,y in enumerate(b,1):
            cur.append(min(prev[j]+1, cur[j-1]+1, prev[j-1]+(x!=y)))
        prev=cur
    return prev[-1]

con=sqlite3.connect('file:'+DB+'?mode=ro',uri=True)
rows=con.execute("SELECT id,chinese,pinyin,meaning_ko,meaning_en,hsk FROM words WHERE meaning_ko!=''").fetchall()
hits=[]
for wid,ch,py,ko,en,hsk in rows:
    k=ko.strip()
    if len(k)>6 or not k: continue                 # 긴 뜻은 음역일 리 없다
    r=rom(k); p=strip_tone(py)
    if not r or not p: continue
    d=lev(r,p)
    if d<=max(1, len(p)//4):                        # 병음과 거의 같음 = 음역
        hits.append((wid,ch,py,ko,r,p,d,hsk,en))
print(f"  음역 의심 {len(hits)}개")
print(f"  {'한자':6s} {'병음':12s} {'뜻':10s} {'로마자':10s} {'거리':4s} 급수")
for h in hits[:40]:
    print(f"   {h[1]:6s} {h[2]:12s} {h[3]:10s} {h[4]:10s} {h[6]:<4} HSK{h[7]}")
import json
json.dump([{'id':h[0],'chinese':h[1],'pinyin':h[2],'meaning_ko':h[3],'en':h[8],'hsk':h[7]} for h in hits],
          open('/app/finetune_data/_wordapp/translit_hits.json','w'), ensure_ascii=False)
con.close()
