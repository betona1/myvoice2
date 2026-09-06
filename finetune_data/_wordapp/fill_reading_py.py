# -*- coding: utf-8 -*-
"""독해 문제에 병음을 붙인다.
   화면의 buildRuby 가 「한자 + 병음」을 받아 루비를 얹고, 낱말을 누르면 단어 모달이 뜬다.
   그 짜임을 그대로 쓰려면 병음이 있어야 한다.
   ⚠️ 문장부호도 토큰으로 넣는다 — 짝짓기가 그 편을 좋아한다(숫자는 넣지 않는다)."""
import json, re, os, sqlite3
os.chdir('/app')
from pypinyin import pinyin as _py, Style
HAN=lambda c: '一'<=c<='鿿'
PUN=set('，。！？、：；“”‘’（）()《》〈〉…—·【】')
def make(text):
    out=[]; k=0
    pys=_py(text, style=Style.TONE, errors=lambda x: ['\0']*len(x))
    for ch in text:
        if HAN(ch):
            out.append(pys[k][0] if k<len(pys) else ''); k+=1
        else:
            if k<len(pys) and pys[k][0]=='\0': k+=1
            if ch in PUN: out.append(ch)
    return ' '.join(t for t in out if t)
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute('PRAGMA table_info(reading_items)')]
for c in ('passage_py','options_py'):
    if c not in cols:
        con.execute(f'ALTER TABLE reading_items ADD COLUMN {c} TEXT')
con.commit()
n=0
for r in con.execute('SELECT id,passage,options FROM reading_items').fetchall():
    ops=json.loads(r['options'])
    con.execute("UPDATE reading_items SET passage_py=?, options_py=? WHERE id=?",
                (make(r['passage']), json.dumps([make(o) for o in ops], ensure_ascii=False), r['id']))
    n+=1
con.commit()
print(f"병음 붙인 문제 {n}개")
r=con.execute('SELECT passage,passage_py,options,options_py FROM reading_items LIMIT 1').fetchone()
print('  보기:', r['passage'][:26])
print('       ', r['passage_py'][:60])
print('  선택지:', json.loads(r['options'])[0])
print('        ', json.loads(r['options_py'])[0])
