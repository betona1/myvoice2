# -*- coding: utf-8 -*-
"""「Taiwan pr.」(대만 발음)을 「대만 홍보」로 옮겨 놓은 것을 걷어낸다.
   pr. 은 pronunciation — 뜻이 아니라 발음 안내다. 낱글자 뜻에는 필요 없다.
   걷어내고 나서 뜻이 비면 사전 영어에서 다시 추린다."""
import re, sqlite3, os
os.chdir('/app')
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
PR=re.compile(r'[,，]?\s*(?:대만|또한|또)?\s*홍보[.．]?\s*\[[^\]]*\]')
BRACK=re.compile(r'\s*\[[^\]]*\]')
HAND={'圾':'쓰레기 (垃圾)','垃':'쓰레기 (垃圾)','槟':'빈랑 (槟榔) · 檳城(페낭)',
      '璃':'유리 (玻璃)','哟':'어! 야! (놀람·감탄)'}
n=0
for r in con.execute('SELECT char,ko,en FROM char_ko').fetchall():
    ko=r['ko'] or ''
    new=BRACK.sub('', PR.sub('', ko)).strip(' ,，.·')
    if r['char'] in HAND: new=HAND[r['char']]
    elif not new:                                   # 발음 안내뿐이었다면 영어에서 다시 추린다
        en=(r['en'] or '').split(';')
        cand=[x.strip() for x in en if x.strip() and 'pr.' not in x and 'Taiwan' not in x]
        new=cand[0][:24] if cand else ''
    if new and new!=ko:
        con.execute('UPDATE char_ko SET ko=? WHERE char=?', (new, r['char'])); n+=1
con.commit()
print(f"발음 안내를 걷어낸 글자 {n}자")
for ch in '哟明危圾坊垃寂暂朴槟汐液璃识谊貌礼':
    r=con.execute('SELECT pinyin,ko FROM char_ko WHERE char=?',(ch,)).fetchone()
    if r: print(f"  {ch} {r['pinyin'] or ''} — {r['ko']}")
BAD=re.compile(r'오류|습니다|입니다|[A-Za-z]{3,}|\[')
left=[r['char'] for r in con.execute('SELECT char,ko FROM char_ko') if BAD.search(r['ko'] or '')]
print(f"\n아직 어그러진 것 {len(left)}자" + (f": {''.join(left)}" if left else ''))
