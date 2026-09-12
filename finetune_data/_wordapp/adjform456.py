# -*- coding: utf-8 -*-
"""관형형 종결 → 기본형. 안전한 접미 규칙만 적용."""
import os,re,sqlite3,sys
os.chdir('/app')
DB='database/voices.db'; CIR='①②③④⑤⑥⑦⑧'
APPLY = '--apply' in sys.argv
R=[(r'적인$','적이다'),(r'([가-힣])한$',r'\1하다'),(r'([가-힣])운$',None),
   (r'([가-힣])난$',r'\1나다'),(r'([가-힣])진$',r'\1지다'),(r'([가-힣])쁜$',r'\1쁘다'),
   (r'([가-힣])른$',r'\1르다'),(r'([가-힣])은$',r'\1다'),(r'([가-힣])싼$',r'\1싸다'),
   (r'([가-힣])운$',None)]
BAD={'인','한','은','운','난','진','른'}   # 한 글자짜리는 건드리지 않음
def conv(t):
    t=(t or '').strip()
    if len(t)<3 or t in BAD: return None
    if t.endswith('적인'): return t[:-2]+'적이다'
    m=re.search(r'([가-힣])운$',t)
    if m:  # ㅂ불규칙: 아름다운→아름답다, 무거운→무겁다
        b=t[:-1]; c=ord(b[-1])
        if 0xAC00<=c<=0xD7A3 and (c-0xAC00)%28==0:
            return b[:-1]+chr(c+17)+'다'
        return None
    for pat,rep in R:
        if rep is None: continue
        n=re.sub(pat,rep,t)
        if n!=t: return n
    return None
con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,pinyin,meaning_ko,senses_ko FROM words WHERE hsk BETWEEN 4 AND 6").fetchall()
hits=[]
for wid,ch,py,ko,sen in rows:
    n=conv(ko)
    if n: hits.append((wid,ch,py,ko,n,sen))
print(f"관형형 종결 대상 {len(hits)}개 / 4398")
for _,ch,py,o,n,_ in hits[:30]: print(f"   {ch:<6} {o}  →  {n}")
if APPLY:
    for wid,ch,py,o,n,sen in hits:
        parts=[x.strip() for x in re.split(r'[①②③④⑤⑥⑦⑧]\s*',sen or '') if x.strip()]
        parts=[n]+[x for x in parts if x!=o and x!=n]
        ns=' '.join(f'{CIR[j]} {x}' for j,x in enumerate(parts[:6])) if len(parts)>1 else n
        con.execute("UPDATE words SET meaning_ko=?,senses_ko=? WHERE id=?",(n,ns,wid))
    con.commit(); print(f"\n적용 완료 {len(hits)}개")
con.close()
