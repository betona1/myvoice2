# -*- coding: utf-8 -*-
"""senses_ko 의 자동생성 찌꺼기 항목만 골라 버린다.
   ①②③ 로 쪼개어 '오염된 뜻'만 제거하고 깨끗한 것은 남긴다."""
import os, re, sys, sqlite3
os.chdir('/app')
APPLY = '--apply' in sys.argv
CIR = '①②③④⑤⑥⑦⑧'
BAD = re.compile(r'(CL:|\bsth\b|\bsb\b|\bSB\b|\bSTH\b|\(onom\.\)|\(coll\.\)|\(Tw\)|구어체 홍보'
                 r'|참조$|^약어|[A-Za-z]{4,}|[一-鿿]{2,}\|)')
con = sqlite3.connect('database/voices.db', timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows = con.execute("SELECT id,chinese,meaning_ko,senses_ko FROM words WHERE senses_ko IS NOT NULL AND senses_ko!=''").fetchall()
n = emptied = 0
for wid, ch, ko, sen in rows:
    if not BAD.search(sen): continue
    parts = [p.strip() for p in re.split(r'[①②③④⑤⑥⑦⑧]\s*', sen) if p.strip()]
    keep = [p for p in parts if not BAD.search(p)]
    if not keep:                       # 전부 오염 → 대표뜻만 남긴다
        new = (ko or '').strip(); emptied += 1
    elif len(keep) == 1:
        new = keep[0]
    else:
        new = ' '.join(f'{CIR[i]} {p}' for i, p in enumerate(keep[:6]))
    if new and new != sen:
        if APPLY: con.execute("UPDATE words SET senses_ko=? WHERE id=?", (new, wid))
        n += 1
        if n <= 10: print(f"   {ch:<8} {sen[:44]}\n            → {new[:52]}")
if APPLY: con.commit()
print(f"\n정리 {n}건 (전부 오염되어 대표뜻만 남긴 것 {emptied}건)" + ("" if APPLY else "  ※드라이런"))
con.close()
