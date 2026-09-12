# -*- coding: utf-8 -*-
"""CC-CEDICT 원본(다의어 포함)으로 words.meaning_en 재구축.
기존 cedict_en.json은 **첫 번째 뜻만** 담은 축약본이라 '东西=east and west'처럼 핵심 뜻이 빠졌다.
원본: 東西 东西 [dong1 xi5] /thing/stuff/person/CL:個|个[ge4]/"""
import os, re, sqlite3, json
os.chdir('/app')
SRC='database/cedict_full.txt'
DB='database/voices.db'
LINE=re.compile(r'^(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+/(.+)/\s*$')
# 사전 표기·메타 정보는 뜻이 아니다
DROP=re.compile(r'^(CL:|variant of|old variant|see |see also|abbr\. for|surname |Taiwan pr\.|erhua variant|used in|also written)', re.I)

senses={}   # 간체자 → [뜻...]
for ln in open(SRC, encoding='utf-8'):
    if ln.startswith('#'): continue
    m=LINE.match(ln.rstrip('\n'))
    if not m: continue
    simp=m.group(2); defs=m.group(4).split('/')
    out=[]
    for d in defs:
        d=d.strip()
        if not d or DROP.match(d): continue
        d=re.sub(r'\[[^\]]*\]','',d)          # [pinyin] 제거
        d=re.sub(r'\s*\|\s*\S+','',d)         # 번체|간체 표기 제거
        d=re.sub(r'\s{2,}',' ',d).strip(' ,;')
        if d and d not in out: out.append(d)
    if out:
        senses.setdefault(simp, [])
        for d in out:
            if d not in senses[simp]: senses[simp].append(d)

print(f"  CC-CEDICT 표제어 {len(senses)}개")
con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
try:
    con.execute("ALTER TABLE words ADD COLUMN senses_ko TEXT DEFAULT ''"); con.commit()
    print("  senses_ko 컬럼 생성")
except Exception: pass

rows=con.execute("SELECT id,chinese,meaning_en FROM words").fetchall()
upd=[]; hit=0
for wid,ch,en in rows:
    lst=senses.get(ch)
    if not lst: continue
    new='; '.join(lst[:6])       # 최대 6개 뜻
    if new and new!=(en or ''): upd.append((new,wid)); hit+=1
if upd:
    con.executemany("UPDATE words SET meaning_en=? WHERE id=?", upd); con.commit()
print(f"  영어 뜻 보강: {hit}개")
for w in ['东西','意思','师傅','老板','主人','司机']:
    r=con.execute("SELECT chinese,meaning_ko,meaning_en FROM words WHERE chinese=?",(w,)).fetchone()
    if r: print(f"   {r[0]:6s} 한:{str(r[1])[:16]:18s} 영:{str(r[2])[:64]}")
n=con.execute("SELECT COUNT(*) FROM words WHERE meaning_en!=''").fetchone()[0]
print(f"  영어 뜻 보유: {n}/{len(rows)}")
con.close()
