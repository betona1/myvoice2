# -*- coding: utf-8 -*-
"""옮긴 뜻을 다듬는다 — 중복(바지; 바지), 영어 잔재(in; 에), 말투(집중했습니다),
   글자 수로 잘려 말이 끊긴 끝(밖으)을 손본다. 뜻 사이는 쉼표로 끊는다."""
import os, re, sqlite3
os.chdir('/app')
END=[(r'했습니다$','하다'),(r'합니다$','하다'),(r'입니다$','이다'),(r'됩니다$','되다'),
     (r'해요$','하다'),(r'하다\.$','하다'),(r'있습니다$','있다'),(r'없습니다$','없다')]
ASCII=re.compile(r'^[\x00-\x7f]+$')
def tidy(t):
    parts=re.split(r'[;,·]\s*', (t or ''))
    out=[]
    for p in parts:
        p=p.strip().strip('.')
        if not p or ASCII.match(p): continue          # 안 옮겨진 영어는 버린다
        for a,b in END:
            p2=re.sub(a,b,p)
            if p2!=p: p=p2; break
        if p not in out: out.append(p)                # 같은 뜻 되풀이 제거
    keep=[]; n=0
    for p in out:
        if keep and n+len(p)+2 > 28: break            # 뜻 하나를 통째로만 담는다
        keep.append(p); n+=len(p)+2
        if len(keep)>=3: break
    return ', '.join(keep)
con=sqlite3.connect('database/voices.db', timeout=60)
rows=con.execute("SELECT char,ko FROM char_ko").fetchall()
n=0
for ch,ko in rows:
    k=tidy(ko)
    if k and k!=ko:
        con.execute("UPDATE char_ko SET ko=? WHERE char=?", (k, ch)); n+=1
con.commit()
print(f"다듬은 것 {n}자 / 모두 {len(rows)}자\n")
for ch in '专业羊京裤么习于事乎球材选':
    r=con.execute("SELECT pinyin,ko FROM char_ko WHERE char=?", (ch,)).fetchone()
    print(f"  {ch} {r[0]} — {r[1]}")
empty=[r[0] for r in con.execute("SELECT char FROM char_ko WHERE COALESCE(ko,'')=''")]
if empty: print("\n뜻이 비게 된 글자:", ''.join(empty))
