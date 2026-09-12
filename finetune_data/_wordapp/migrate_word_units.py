# -*- coding: utf-8 -*-
"""한 단어가 여러 교재에 나올 수 있도록 단어–교재 연결을 따로 뗀다.
   words.subject 는 대표 교재로 그대로 두고, 실제 목록은 word_units 로 본다."""
import os, sqlite3
os.chdir('/app')
from opencc import OpenCC
cc = OpenCC('t2s')
c = sqlite3.connect('database/voices.db', timeout=60); c.row_factory = sqlite3.Row
c.execute("PRAGMA busy_timeout=60000")
c.execute("""CREATE TABLE IF NOT EXISTS word_units (
    word_id    INTEGER NOT NULL,
    subject    TEXT    NOT NULL,
    unit_week  INTEGER,
    unit_class INTEGER,
    FOREIGN KEY(word_id) REFERENCES words(id))""")
c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_wu_uniq
             ON word_units(word_id, subject, IFNULL(unit_week,-1), IFNULL(unit_class,-1))""")
c.execute("CREATE INDEX IF NOT EXISTS idx_wu_subject ON word_units(subject, unit_week, unit_class)")
c.execute("CREATE INDEX IF NOT EXISTS idx_wu_word    ON word_units(word_id)")

def add(wid, sub, w, cl):
    try:
        c.execute("INSERT INTO word_units(word_id,subject,unit_week,unit_class) VALUES(?,?,?,?)",
                  (wid, sub, w, cl)); return 1
    except sqlite3.IntegrityError:
        return 0

# 1) 지금 쓰이는 단어들의 교재 정보를 그대로 옮긴다
base = 0
for r in c.execute("SELECT id,subject,unit_week,unit_class FROM words "
                   "WHERE COALESCE(excluded,0)=0 AND subject IS NOT NULL").fetchall():
    base += add(r['id'], r['subject'], r['unit_week'], r['unit_class'])

# 2) 간체 통일로 제외된 중복본이 갖고 있던 교재 정보를 되살린다
back = []
for r in c.execute("SELECT id,chinese,subject,unit_week,unit_class FROM words "
                   "WHERE excluded=1 AND subject IS NOT NULL").fetchall():
    s = cc.convert(r['chinese'])
    if s == r['chinese']: continue
    o = c.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0", (s,)).fetchone()
    if not o: continue
    if add(o['id'], r['subject'], r['unit_week'], r['unit_class']):
        back.append((s, r['subject'], r['unit_week'], r['unit_class']))
c.commit()
print(f"옮긴 연결 {base}건 · 되살린 연결 {len(back)}건")
for s, sub, w, cl in back: print(f"   {s:<8}{sub}({w}주 {cl}교시)")
n = c.execute("SELECT COUNT(*) FROM word_units").fetchone()[0]
multi = c.execute("SELECT COUNT(*) FROM (SELECT word_id FROM word_units GROUP BY word_id HAVING COUNT(*)>1)").fetchone()[0]
print(f"\nword_units {n}건 · 두 교재 이상에 걸친 단어 {multi}개")
print("교재별:", dict(c.execute("SELECT subject,COUNT(*) FROM word_units GROUP BY subject").fetchall()))
c.close()
