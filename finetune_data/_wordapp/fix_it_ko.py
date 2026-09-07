# -*- coding: utf-8 -*-
"""컴퓨터 쪽 뜻으로 잘못 잡힌 낱말을 바로잡는다.
   桌面 은 본디 '탁자 윗면'인데 옮기는 쪽이 컴퓨터 '데스크탑'을 골랐다 —
   교재 문장은 「젓가락으로 그릇·접시·탁자 위를 두드리지 말라」이다.
   두 뜻을 다 쓰는 낱말은 본디 뜻을 앞에 둔다."""
import sqlite3, os
os.chdir('/app')
FIX = {
 '桌面':   ('탁자 윗면, 책상 위 (컴퓨터 바탕화면)', '桌面上 처럼 쓰이면 탁자 위'),
 '桌面上': ('탁자 위에, 책상 위에', ''),
 '上映':   ('(영화를) 상영하다, 개봉하다', '스크린에 → 잘못 옮겨짐'),
 '收视率': ('시청률', '등급 → 잘못 옮겨짐'),
 '枢纽':   ('요충지, 중심 (교통·물류의)', ''),
}
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute('PRAGMA table_info(words)')]
if 'meaning_ko_old' not in cols:
    con.execute('ALTER TABLE words ADD COLUMN meaning_ko_old TEXT'); con.commit()
n=0
for ch,(ko,why) in FIX.items():
    r=con.execute("SELECT id,meaning_ko FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: print(f"  {ch}: 없음"); continue
    if (r['meaning_ko'] or '')==ko: print(f"  {ch}: 이미 맞음"); continue
    con.execute("""UPDATE words SET meaning_ko_old=COALESCE(meaning_ko_old,meaning_ko),
                   meaning_ko=? WHERE id=?""", (ko, r['id']))
    print(f"  {ch}: 「{r['meaning_ko']}」 → 「{ko}」" + (f"   ({why})" if why else "")); n+=1
con.commit()
print(f"\n고친 낱말 {n}개 (옛 뜻은 meaning_ko_old 에)")
