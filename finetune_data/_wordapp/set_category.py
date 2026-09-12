# -*- coding: utf-8 -*-
"""words.category 부여 — 단어 / 구문 / 성어.
   기준: 사전 등재 표제어=단어, 사전이 관용구로 표시=성어, 사전에 없는 다글자=구문."""
import os, re, sys, sqlite3
os.chdir('/app')
APPLY = '--apply' in sys.argv

CE = {}
for line in open('database/cedict_full.txt', encoding='utf-8'):
    if line.startswith('#'): continue
    p = line.split(' ', 2)
    if len(p) >= 2:
        en = p[2].split('/', 1)[1] if '/' in p[2] else ''
        CE.setdefault(p[0], en); CE.setdefault(p[1], en)

IDIOM = re.compile(r'\(idiom|idiom\)|成语')
KO    = re.compile(r'\(관용')
COMP  = re.compile(r'(出来|进来|过来|回来|上来|下来|起来|出去|进去|过去|回去|上去|下去'
                   r'|不了|不起|不着|不下|不到|不动|不住|不开|不完|得了|得起)$')
# 나열·반복 (연습용 찌꺼기) — 단, 아래 KEEP 은 실제 단어이므로 살린다
KEEP = {'一声声调','二声声调','三声声调','四声声调','世界地图','哗啦哗啦',
        '前鼻韵母','后鼻韵母','舌尖中音','舌尖前音','舌尖后音','语气助词','汉语拼音','人民币元'}
REP   = lambda s: len(s) == 4 and s[:2] == s[2:]
ENUM2 = lambda s: len(s) == 4 and s not in CE and s[:2] in CE and s[2:] in CE and s[:2] != s[2:]

con = sqlite3.connect('database/voices.db', timeout=60); con.execute('PRAGMA busy_timeout=60000')
if 'category' not in [r[1] for r in con.execute('PRAGMA table_info(words)')]:
    con.execute("ALTER TABLE words ADD COLUMN category TEXT DEFAULT '단어'"); con.commit()
    print("category 컬럼 추가")

rows = con.execute("SELECT id,chinese,meaning_ko,meaning_en FROM words WHERE COALESCE(excluded,0)=0").fetchall()
cnt = {'단어':0,'구문':0,'성어':0}; junk = []
for wid, ch, ko, en in rows:
    g = CE.get(ch, '')
    if IDIOM.search(g or '') or IDIOM.search(en or '') or KO.search(ko or ''):
        cat = '성어'
    elif ch not in KEEP and len(ch) >= 3 and ch not in CE and (REP(ch) or ENUM2(ch)):
        junk.append((wid, ch, ko)); continue
    elif len(ch) >= 3 and (COMP.search(ch) or ch not in CE):
        cat = '구문'
    else:
        cat = '단어'
    cnt[cat] += 1
    if APPLY: con.execute("UPDATE words SET category=? WHERE id=?", (cat, wid))
if APPLY:
    con.executemany("UPDATE words SET excluded=1 WHERE id=?", [(i,) for i,_,_ in junk])
    con.commit()
print(f"단어 {cnt['단어']} / 구문 {cnt['구문']} / 성어 {cnt['성어']} / 추가 제외 {len(junk)}"
      + ("" if APPLY else "  ※드라이런"))
for _, ch, ko in junk: print(f"   제외 {ch:<7} {ko[:28]}")
con.close()
