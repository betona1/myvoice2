# -*- coding: utf-8 -*-
"""words 에 교재 단원(주차·교시)을 붙인다.
   단어 카드와 한자가 정확히 같은 것 우선, 없으면 그 과목 본문에 처음 등장하는 단원을 쓴다."""
import os, sys, sqlite3
os.chdir('/app')
APPLY = '--apply' in sys.argv
con = sqlite3.connect('database/voices.db', timeout=60); con.execute('PRAGMA busy_timeout=60000')
cols = [r[1] for r in con.execute('PRAGMA table_info(words)')]
for c in ('unit_week', 'unit_class'):
    if c not in cols:
        con.execute(f'ALTER TABLE words ADD COLUMN {c} INTEGER'); print(f'{c} 컬럼 추가')
con.commit()

words = con.execute("SELECT id,chinese,subject FROM words WHERE COALESCE(excluded,0)=0").fetchall()
subs = sorted({w[2] for w in words if w[2]})
cards = {}
for s in subs:
    cards[s] = con.execute(
        "SELECT week,class_num,chinese,group_name FROM chinese_cards WHERE subject=? ORDER BY week,class_num",
        (s,)).fetchall()

hit_exact = hit_in = miss = 0
plan = []
for wid, zh, subj in words:
    rows = cards.get(subj) or []
    pos = None
    for w, c, t, g in rows:                       # ① 단어 카드와 정확히 일치
        if g == '단어' and t == zh:
            pos = (w, c); break
    if pos is None:
        for w, c, t, g in rows:                   # ② 본문 등에 처음 등장하는 단원
            if t and zh in t:
                pos = (w, c); break
        if pos: hit_in += 1
        else: miss += 1
    else:
        hit_exact += 1
    if pos: plan.append((pos[0], pos[1], wid))

print(f"단어카드 일치 {hit_exact} / 본문 등장 {hit_in} / 못 찾음 {miss}  (총 {len(words)})")
if APPLY:
    con.executemany("UPDATE words SET unit_week=?, unit_class=? WHERE id=?", plan)
    con.commit()
    print(f"단원 부여 {len(plan)}건")
    for r in con.execute("""SELECT subject, COUNT(*), SUM(unit_week IS NOT NULL)
                            FROM words WHERE COALESCE(excluded,0)=0 GROUP BY subject ORDER BY 2 DESC"""):
        print(f"   {r[0] or '(없음)':<20} {r[1]:>5}개 중 단원 있음 {r[2]}")
else:
    print("※ 드라이런 — --apply 로 반영")
con.close()
