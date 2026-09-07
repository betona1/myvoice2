# -*- coding: utf-8 -*-
"""대명사·동사를 묶음으로 표시한다.
   낱말·뜻·병음·음성은 이미 다 있다 — wordset 만 달면 /flash·게임·쓰기연습지·앱 꾸러미에
   한꺼번에 들어간다.
   ⚠️ 동사는 2,670개라 다 넣으면 첫걸음에 짓눌린다. HSK 1~3급(223개)부터."""
import json, os, sqlite3, sys
os.chdir('/app')
PRON = ['我','你','您','他','她','它','我们','你们','他们','她们','它们','咱们',
        '这','那','哪','这儿','那儿','哪儿','这里','那里','哪里','这个','那个','哪个',
        '这些','那些','哪些','谁','什么','怎么','怎样','为什么','多少','几',
        '自己','别人','大家','每']
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row

# ① 대명사
n=0
for w in PRON:
    r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(w,)).fetchone()
    if not r: continue
    con.execute("UPDATE words SET wordset='대명사' WHERE id=?", (r['id'],)); n+=1
print(f"대명사 {n}개 표시")

# ② 동사 — 품사 자료(complete-hsk-vocabulary, MIT)에서 가린다
pos={}
try:
    for x in json.load(open('temp/hsk_pos.json',encoding='utf-8')):
        pos.setdefault(x['s'], set()).update(x.get('p') or [])
except Exception:
    print("  ⚠️ temp/hsk_pos.json 이 없다 — 동사는 건너뛴다"); pos=None
if pos:
    # ⚠️ 품사 자료가 형용사·명사까지 'v' 로 달아 둔 것이 있다
    #    (猫 고양이 · 热 덥다 · 白 희다 · 题 문제 가 동사로 들어왔다).
    #    그래서 'v' 가 있어도 형용사(a)·명사(n)가 함께 달렸으면 뺀다.
    V={'v','vd','vi','vt','vx','vl','vg'}          # vn(동명사)은 뺀다 — 명사 쪽이 세다
    NOT={'a','ad','an','n','nz','nr','ns','nt','m','q','r','d','p','c','u','e','y','o','w'}
    LV=int(sys.argv[1]) if len(sys.argv)>1 else 3      # HSK 몇 급까지
    m=0
    for r in con.execute("""SELECT id,chinese,hsk FROM words
                            WHERE COALESCE(excluded,0)=0 AND hsk BETWEEN 1 AND ?
                              AND COALESCE(wordset,'')=''""", (LV,)):
        tags=pos.get(r['chinese'], set())
        if (V & tags) and not (NOT & tags):
            con.execute("UPDATE words SET wordset='동사' WHERE id=?", (r['id'],)); m+=1
    print(f"동사 {m}개 표시 (HSK 1~{LV}급)")
# ③ 형용사 — 형용사로만 쓰이는 것만 (동사·명사 겸용은 뺀다)
if pos:
    A={'a','ad','an','ag'}
    NOTA={'n','nz','nr','ns','nt','v','vn','m','q','r','d','p','c','u','w','t'}
    LVA=int(sys.argv[2]) if len(sys.argv)>2 else 4
    k=0
    for r in con.execute("""SELECT id,chinese FROM words
                            WHERE COALESCE(excluded,0)=0 AND hsk BETWEEN 1 AND ?
                              AND COALESCE(wordset,'')=''""", (LVA,)):
        tags=pos.get(r['chinese'], set())
        if (A & tags) and not (NOTA & tags):
            con.execute("UPDATE words SET wordset='형용사' WHERE id=?", (r['id'],)); k+=1
    print(f"형용사 {k}개 표시 (HSK 1~{LVA}급)")
con.commit()
import collections
print()
print("묶음별 낱말 수:")
for r in con.execute("""SELECT wordset, COUNT(*) n FROM words
                        WHERE COALESCE(wordset,'')<>'' AND COALESCE(excluded,0)=0
                        GROUP BY wordset ORDER BY n DESC"""):
    print(f"  {r['wordset']:8s} {r['n']:4d}개")
