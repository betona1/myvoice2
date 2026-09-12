# -*- coding: utf-8 -*-
import os,re,sqlite3
os.chdir('/app')
DB='database/voices.db'
CHO=['g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h']
JUNG=['a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i']
JONG=['','g','k','ks','n','nj','nh','d','l','lg','lm','lb','ls','lt','lp','lh','m','b','ps','s','ss','ng','j','ch','k','t','p','h']
def rom(s):
    o=[]
    for c in s:
        v=ord(c)
        if 0xAC00<=v<=0xD7A3:
            i=v-0xAC00; o.append(CHO[i//588]+JUNG[(i%588)//28]+JONG[i%28])
    return ''.join(o)
M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i','ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'v','ǘ':'v','ǚ':'v','ǜ':'v','ü':'v'}
def st(p): return ''.join(M.get(c,c) for c in (p or '').lower() if c.isalpha() or c in M)
def lev(a,b):
    if a==b: return 0
    if not a or not b: return max(len(a),len(b))
    pv=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        cu=[i]
        for j,y in enumerate(b,1): cu.append(min(pv[j]+1,cu[j-1]+1,pv[j-1]+(x!=y)))
        pv=cu
    return pv[-1]
LAT=re.compile(r'(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])')
KEEP={'TV','SDU','PC','DVD','CD','AI','IT','km','kg','cm','mm'}
IMP=re.compile(r'(해요$|했어요|예요$|이에요$|주세요$|하세요$|합니다$|입니다$|그만해$|안아줘$)')
con=sqlite3.connect(DB)
rows=con.execute("SELECT id,chinese,pinyin,meaning_ko FROM words WHERE hsk BETWEEN 4 AND 6").fetchall()
cap=[r for r in rows if r[2] and r[2][:1].isupper()]
eng=[r for r in rows if [x for x in LAT.findall(r[3] or '') if x not in KEEP]]
imp=[r for r in rows if IMP.search((r[3] or '').strip())]
# 음역 의심: 한자 1글자 & 뜻이 짧고 로마자화가 병음과 유사
tr=[r for r in rows if len(r[1])==1 and r[3] and len(r[3])<=5
    and lev(rom(r[3]), st(r[2])) <= max(1,len(st(r[2]))//3)]
adj=[r for r in rows if re.search(r'(은|는|한|인|던|운|을)$',(r[3] or '').strip()) and len(r[1])==1]
print(f"HSK 4~6급 {len(rows)}개")
for nm,g in [('병음 대문자',cap),('영어 잔존',eng),('명령·회화체',imp),('병음 음역 의심',tr),('관형형 종결',adj)]:
    print(f"\n■ {nm}: {len(g)}개")
    for r in g[:20]: print(f"   {r[1]} {r[2]} → {r[3]}")
con.close()
