# -*- coding: utf-8 -*-
"""CC-CEDICT 의 CL: 표시에서 '무엇을 어느 양사로 세는가'를 뽑는다.
     上衣 /jacket/CL:件[jian4]/      → 옷은 件
   지금 양사 학습은 「양사 → 예문」 한 방향뿐이라, 반대로 묻는 문제를 만들려면 이 짝이 필요하다.
     「上衣(옷) 를 세는 양사는?  ① 件 ② 张 ③ 条 ④ 把」
   교재에 있는 낱말을 앞에 둔다 — 모르는 낱말로 물으면 문제가 아니라 수수께끼가 된다."""
import os, re, sqlite3, json
os.chdir('/app')
CJK=re.compile(r'^[一-鿿]+$')
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
con.execute("""CREATE TABLE IF NOT EXISTS classifier_nouns (
    classifier TEXT NOT NULL,      -- 양사 (간체)
    noun       TEXT NOT NULL,      -- 그 양사로 세는 낱말
    pinyin     TEXT,
    meaning_ko TEXT,               -- 교재에 있으면 한글 뜻
    meaning_en TEXT,
    hsk        INTEGER DEFAULT 0,
    known      INTEGER DEFAULT 0,  -- 교재(words)에 있는 낱말인가
    PRIMARY KEY (classifier, noun))""")
con.execute("CREATE INDEX IF NOT EXISTS idx_cln ON classifier_nouns(classifier, known DESC, hsk)")

known={}
for r in con.execute("""SELECT chinese,pinyin,meaning_ko,hsk FROM words
                        WHERE COALESCE(excluded,0)=0"""):
    known[r['chinese']]=(r['pinyin'], r['meaning_ko'], r['hsk'] or 0)

def simp(tok):
    """個|个 → 个 ,  件 → 件"""
    return tok.split('|')[-1]

n=0; seen=set()
for ln in open('database/cedict_full.txt', encoding='utf-8'):
    if ln.startswith('#') or 'CL:' not in ln: continue
    try:
        head, rest = ln.split(' [',1)
        trad, sp = head.split(' ',1)
        py = rest.split(']',1)[0]
        body = rest.split(']',1)[1]
    except Exception:
        continue
    if not CJK.match(sp) or len(sp) < 2: continue          # 낱글자는 문제로 삼기 어렵다
    cls=[]
    for seg in re.findall(r'CL:([^/]+)', body):
        for tok in seg.split(','):
            tok=tok.strip()
            m=re.match(r'([一-鿿|]+)\[', tok)
            if m: cls.append(simp(m.group(1)))
    if not cls: continue
    en='; '.join([p for p in body.split('/') if p and not p.startswith('CL:')][:2])[:120]
    k=known.get(sp)
    for c in cls:
        if (c,sp) in seen: continue
        seen.add((c,sp))
        con.execute("""INSERT OR REPLACE INTO classifier_nouns
                       (classifier,noun,pinyin,meaning_ko,meaning_en,hsk,known)
                       VALUES(?,?,?,?,?,?,?)""",
                    (c, sp, py, (k[1] if k else '') or '', en, (k[2] if k else 0), 1 if k else 0))
        n+=1
con.commit()

ours={r[0] for r in con.execute("SELECT chinese FROM words WHERE wordset='양사' AND COALESCE(excluded,0)=0")}
tot=con.execute("SELECT COUNT(*) FROM classifier_nouns").fetchone()[0]
kn =con.execute("SELECT COUNT(*) FROM classifier_nouns WHERE known=1").fetchone()[0]
print(f"양사-낱말 짝 {tot}개 (그중 교재에 있는 낱말 {kn}개)")
print(f"\n우리 양사 {len(ours)}개 가운데 짝이 있는 것:")
rows=con.execute("""SELECT classifier, COUNT(*) n, SUM(known) k FROM classifier_nouns
                    GROUP BY classifier ORDER BY k DESC, n DESC""").fetchall()
hit=[r for r in rows if r['classifier'] in ours]
print(f"  {len(hit)}개")
for r in hit[:18]:
    ex=[x[0] for x in con.execute("""SELECT noun FROM classifier_nouns
                                     WHERE classifier=? ORDER BY known DESC, hsk LIMIT 4""",(r['classifier'],))]
    print(f"   {r['classifier']}  전체 {r['n']:3d}개 · 교재 {r['k'] or 0:3d}개   {' '.join(ex)}")
