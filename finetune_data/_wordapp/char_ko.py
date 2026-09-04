# -*- coding: utf-8 -*-
"""예문 글자 가운데 교재에 없어 한글 뜻이 없는 것을 채운다.
   쓰기 공부에서 글자마다 음과 뜻을 보여 주는데, 없으면 영어가 그대로 나온다(专 → specialized).
   낱글자는 영어 뜻을 옮기는 편이 안전하다 —
   중국어를 바로 던지면 엉뚱하게 옮겨지는 일이 잦다(石头→결석).
   결과는 char_ko 표에 담는다."""
import os, re, sys, json, time, sqlite3
os.chdir('/app')
import translators as ts
from pypinyin import pinyin as _py, Style
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('database/voices.db', timeout=60); con.execute('PRAGMA busy_timeout=60000')
con.execute("""CREATE TABLE IF NOT EXISTS char_ko (
    char TEXT PRIMARY KEY, pinyin TEXT, ko TEXT, en TEXT)""")

# 사전에서 낱글자 뜻을 뽑는다.
# ⚠️ 첫 항목이 「surname Ye」인 글자가 수두룩하다 — 그대로 쓰면 业이 '성 예'가 된다.
#    성씨·이체자·참조 같은 껍데기 뜻은 걷어내고, 알맹이가 가장 많은 항목을 고른다.
SKIP=re.compile(r'^(surname\b|variant of|old variant of|Japanese variant|see |used in |'
                r'abbr\. for a |CL:|erhua variant)', re.I)
def useful(line):
    parts=[p.strip() for p in line.split('/')[1:-1] if p.strip()]
    return [p for p in parts if not SKIP.match(p)]
GLOSS={}
for ln in open('database/cedict_full.txt', encoding='utf-8'):
    if ln.startswith('#'): continue
    try:
        simp=ln.split(' ',2)[1]
        if len(simp)!=1: continue
    except Exception: continue
    us=useful(ln)
    if not us: continue
    if simp not in GLOSS or len(us) > len(GLOSS[simp]):   # 뜻이 가장 풍부한 항목
        GLOSS[simp]=us

need=json.load(open('finetune_data/_wordapp/char_need_ko.json', encoding='utf-8'))
done={r[0] for r in con.execute("SELECT char FROM char_ko WHERE COALESCE(ko,'')<>''")}
todo=[c for c in need if c not in done]
print(f"채울 글자 {len(todo)}자", flush=True)

def short(t):
    t=(t or '').strip().strip('.,;')
    t=re.sub(r'\s*\(.*?\)\s*','',t)             # 괄호 설명은 덜어 낸다
    return t[:40]

ok=fail=0
for i,ch in enumerate(todo,1):
    us=GLOSS.get(ch)
    if not us:
        fail+=1; continue
    parts=us[:3]
    en='; '.join(us[:6])
    try:
        ko=ts.translate_text('; '.join(parts), translator='google', from_language='en', to_language='ko')
    except Exception as e:
        try:
            time.sleep(1.5)
            ko=ts.translate_text('; '.join(parts), translator='bing', from_language='en', to_language='ko')
        except Exception:
            fail+=1; continue
    py=_py(ch, style=Style.TONE)[0][0]
    con.execute("INSERT OR REPLACE INTO char_ko(char,pinyin,ko,en) VALUES(?,?,?,?)",
                (ch, py, short(ko), en[:120]))
    ok+=1
    if i%25==0: con.commit(); print(f"   {i}/{len(todo)}  {ch} {py} {short(ko)}", flush=True)
con.commit()
print(f"\n채운 것 {ok}자 · 못 채운 것 {fail}자")
for r in con.execute("SELECT char,pinyin,ko FROM char_ko ORDER BY char LIMIT 12"):
    print(f"  {r[0]} {r[1]} — {r[2]}")
