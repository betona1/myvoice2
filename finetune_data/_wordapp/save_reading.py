# -*- coding: utf-8 -*-
"""지문+선택지 문제를 담고, 흩어져 있던 원본 카드는 화면에서 숨긴다.
   ⚠️ 카드를 지우지 않는다 — hidden 표시만 해 되돌릴 수 있게 한다."""
import json, re, sqlite3
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
con.execute("""CREATE TABLE IF NOT EXISTS reading_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT, week INTEGER, class_num INTEGER, page INTEGER,
    no TEXT, passage TEXT, options TEXT, meaning_ko TEXT)""")
cols=[r[1] for r in con.execute('PRAGMA table_info(chinese_cards)')]
if 'hidden' not in cols:
    con.execute('ALTER TABLE chinese_cards ADD COLUMN hidden INTEGER DEFAULT 0'); con.commit()
    print('hidden 칸 추가')

data=json.load(open('finetune_data/_wordapp/reading_items.json',encoding='utf-8'))
con.execute("DELETE FROM reading_items")
for x in data:
    con.execute("""INSERT INTO reading_items(subject,week,class_num,page,no,passage,options,meaning_ko)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (x['subject'], x['week'], x['class_num'], x['page'], x['no'],
                 x['passage'], json.dumps(x['options'], ensure_ascii=False), ''))
print(f"지문+선택지 {len(data)}개 담음")

# 선택지만 있는 카드(=이제 문제 안에 들어간 것)를 숨긴다
opts={''.join(o) for x in data for o in [x['options']]}
hid=0
for r in con.execute("""SELECT id,chinese FROM chinese_cards
                        WHERE subject='신HSK쓰기독해' AND group_name='문제풀이'
                        AND COALESCE(hidden,0)=0""").fetchall():
    t=re.sub(r'\s','', r['chinese'] or '')
    m=re.match(r'^A(.+?)B(.+?)C(.+?)D(.+)$', t)
    if not m: continue
    if ''.join(m.groups()) in opts:
        con.execute("UPDATE chinese_cards SET hidden=1 WHERE id=?", (r['id'],)); hid+=1
con.commit()
print(f"선택지만 있던 카드 {hid}장 숨김 (지우지 않음 — hidden=1)")
