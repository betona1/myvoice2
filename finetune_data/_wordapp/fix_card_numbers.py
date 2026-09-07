# -*- coding: utf-8 -*-
"""카드 해석에 남아 있는 숫자 오류를 고친다.
   독해 문제(reading_items)는 고쳤는데 원본 카드(chinese_cards)에 같은 해석이 남아 있었다.
   숫자만 바꿔 끼운다 — 나머지 문장은 그대로 둔다."""
import re, sqlite3, os
os.chdir('/app')
SUB = [
  # (어느 글에서, 바꿀 것, 바뀔 것)
  ('鲨鱼', r'약\s*100\s*종', '약 370여 종'),
  ('哈尔滨', r'2000\s*년부터', '1963년부터'),
  ('哈尔滨', r'2006\s*년에는', '1985년에는'),
  ('哈尔滨', r'그 이후로 매달[,，]?\s*매일이', '그 뒤로 해마다 1월 5일이'),
  ('哈尔滨', r'그 이후로 매년\s*매일', '그 뒤로 해마다 1월 5일'),
]
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute('PRAGMA table_info(chinese_cards)')]
n=0
for r in con.execute('SELECT id,chinese,meaning_ko FROM chinese_cards WHERE meaning_ko IS NOT NULL').fetchall():
    ko=r['meaning_ko']; new=ko
    for key, pat, rep in SUB:
        if key in (r['chinese'] or ''):
            new=re.sub(pat, rep, new)
    if new!=ko:
        con.execute("""UPDATE chinese_cards SET meaning_ko_old=COALESCE(meaning_ko_old,meaning_ko),
                       meaning_ko=? WHERE id=?""", (new, r['id']))
        print(f"  [{r['id']}] {r['chinese'][:20]}…")
        n+=1
con.commit()
print(f"\n고친 카드 {n}장 (옛 해석은 meaning_ko_old 에)")
