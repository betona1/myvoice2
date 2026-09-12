# -*- coding: utf-8 -*-
"""번체가 섞인 단어를 간체로 통일한다. 著名 처럼 간체로도 쓰는 글자는
   opencc 가 알아서 그대로 두므로 글자 단위로 바꾸지 않는다.
   발음은 그대로이므로 병음·음성 파일은 건드리지 않는다."""
import os, sqlite3
os.chdir('/app')
from opencc import OpenCC
cc = OpenCC('t2s')
con = sqlite3.connect('database/voices.db', timeout=60)
con.execute('PRAGMA busy_timeout=60000'); con.row_factory = sqlite3.Row
rows = con.execute("SELECT id,chinese,meaning_ko,subject FROM words WHERE COALESCE(excluded,0)=0").fetchall()
chg, dup, skip = [], [], []
for r in rows:
    s = cc.convert(r['chinese'])
    if s == r['chinese']: continue
    other = con.execute("SELECT id FROM words WHERE chinese=? AND id<>?", (s, r['id'])).fetchone()
    if other:
        dup.append((r['id'], r['chinese'], s, r['meaning_ko'], other['id']))
    else:
        chg.append((r['id'], r['chinese'], s, r['meaning_ko'], r['subject']))
print(f"간체로 바꿀 것 {len(chg)}개 · 이미 같은 간체 항목이 있는 것 {len(dup)}개")
for i, a, b, mk, sub in chg[:25]:
    print(f"   {a:<9}→ {b:<9}{(mk or '')[:20]:<22}{sub or ''}")
if len(chg) > 25: print(f"   … 외 {len(chg)-25}개")
print()
for i, a, b, mk, oid in dup:
    print(f"   {a:<9}→ {b:<9}이미 있음(id={oid}) → 번체본 제외 처리")
for i, a, b, mk, sub in chg: con.execute("UPDATE words SET chinese=? WHERE id=?", (b, i))
for i, a, b, mk, oid in dup: con.execute("UPDATE words SET excluded=1 WHERE id=?", (i,))
con.commit()
left = con.execute("SELECT COUNT(*) FROM words WHERE COALESCE(excluded,0)=0").fetchone()[0]
print(f"\n바꾼 것 {len(chg)}개 / 제외한 중복 {len(dup)}개 · 학습 대상 {left}개")
con.close()
