# -*- coding: utf-8 -*-
"""words 테이블 뜻 정리.
- "xǐhuan — 좋아하다" 처럼 앞에 병음이 붙은 것 제거
- 발음/문법 설명 조각("성조 의 변화")은 재번역"""
import os, re, sqlite3, time
os.chdir('/app')
import translators as ts
DB='database/voices.db'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,40})["”」’\']')
PYPFX=re.compile(r'^[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]+\s*[—\-–]\s*')
NOTE=re.compile(r'(성조|양사|사용한다|를 씀|의 변화|앞에서는|경성|운모|성모)')
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,meaning_ko FROM words").fetchall()
pfx=note=0; upd=[]
for wid,ch,ko in rows:
    ko=(ko or '').strip(); new=ko
    n2=PYPFX.sub('', new)
    if n2!=new: new=n2.strip(); pfx+=1
    # 설명 조각이거나 비었으면 재번역
    if not new or NOTE.search(new) or len(new)<2:
        for a in range(3):
            try:
                r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google', from_language='zh', to_language='ko')
                m=QUOTE.match((r or '').strip()); t=m.group(1).strip() if m else ''
                if t and t!=ch: new=t; note+=1
                break
            except Exception:
                if a<2: time.sleep(2.0*(a+1))
        time.sleep(0.35)
    for a,b in RULES:
        x=re.sub(a,b,new)
        if x!=new: new=x; break
    if new!=ko: upd.append((new,wid))
if upd:
    con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit()
print(f"  병음접두 제거 {pfx} / 설명조각 재번역 {note} / 총 {len(upd)}개 수정")
for r in con.execute("SELECT seq,chinese,pinyin,meaning_ko FROM words ORDER BY seq LIMIT 12"):
    print(f"    {r[0]:>3} {r[1]:6s} {r[2]:12s} {r[3]}")
con.close()
