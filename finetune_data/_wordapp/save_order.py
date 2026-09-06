# -*- coding: utf-8 -*-
"""문장 순서 문제를 writing_puzzles 에 넣는다.
   기존 퍼즐(어순 배열)과 짜임이 같아 한 표에 담되, kind 로 갈라 둔다.
     word  : 낱말을 늘어놓아 한 문장 만들기 (짝수 주)
     order : 세 도막을 차례대로 놓기 (홀수 주 2교시)"""
import json, sqlite3
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute('PRAGMA table_info(writing_puzzles)')]
if 'kind' not in cols:
    con.execute("ALTER TABLE writing_puzzles ADD COLUMN kind TEXT DEFAULT 'word'")
    con.execute("UPDATE writing_puzzles SET kind='word' WHERE kind IS NULL")
    con.commit(); print('kind 칸 추가')
data=json.load(open('finetune_data/_wordapp/order_puzzles.json',encoding='utf-8'))
con.execute("DELETE FROM writing_puzzles WHERE kind='order'")
n=0
for i,o in enumerate(data):
    frags=o['frags']; ans=o['answer']
    # ⚠️ tokens 는 **정답 차례**로 담아야 한다 — 화면에서 섞어 보여 주고 이 차례와 견준다
    tokens=[frags[k] for k in ans]
    answer=''.join(tokens)
    con.execute("""INSERT INTO writing_puzzles
                   (subject,week,class_num,sort_order,level,page,answer,tokens,meaning_ko,kind)
                   VALUES(?,?,?,?,?,?,?,?,?,'order')""",
                (o['subject'], o['week'], o['class_num'], i, 4, o['page'],
                 answer, json.dumps(tokens, ensure_ascii=False),
                 '순서: ' + ' → '.join(ans)))
    n+=1
con.commit()
print(f"문장 순서 문제 {n}개 넣음")
for r in con.execute("""SELECT week,class_num,COUNT(*) c FROM writing_puzzles
                        GROUP BY kind,week,class_num ORDER BY week,class_num"""):
    pass
import collections
rows=con.execute("SELECT kind,week,class_num FROM writing_puzzles").fetchall()
print(collections.Counter((r['kind'],r['week']) for r in rows))
