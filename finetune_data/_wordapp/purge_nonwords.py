# -*- coding: utf-8 -*-
"""단어가 아닌 항목만 제거하고, 나머지는 병음 접두만 떼어 살린다.
발음 연습 교재는 두 단어를 나란히 적어 대비시키는데(妈/麻, 滑雪/滑冰) 그게 한 카드로 붙었다.
설명문 카드(一不 = 一의 성조변화 규칙)도 단어가 아니다.
반면 再来·挂电话·生活用品 같은 정상 표현은 병음 접두만 떼고 유지한다."""
import os, re, sqlite3, sys
os.chdir('/app')
DB='database/voices.db'

# 확실한 '설명 카드' 표지
EXPL=re.compile(r'(성조 변화|의 성조|운모|성모|경성 변화|발음이? 동일|언어유희|의문문|동사 형식|'
                r'단위 생략|수식 없는|될 수 없음|형용사 형용사|동사  동사|개사  목적어|의 줄임말|'
                r'입엄력|입말력|입욕력|입 마력|내 용 을 입|^연습|^예시|\s연습\s|\s예시\s)')

# CC-CEDICT 표제어 — 妈妈·谢谢·包包 같은 정상 중첩어를 지키기 위해 필요
_L=re.compile(r'^(\S+)\s+(\S+)\s+\[')
HEAD=set()
for _ln in open('database/cedict_full.txt', encoding='utf-8'):
    if _ln.startswith('#'): continue
    _m=_L.match(_ln)
    if _m: HEAD.add(_m.group(2))
PYPFX=re.compile(r'^[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]+(\s+[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]+)*\s*[—\-–]\s*')
# 뜻이 3칸 이상 공백으로 갈라짐 = 두 단어가 붙은 것
TWOGLOSS=re.compile(r'\S\s{3,}\S')

con=sqlite3.connect(DB, timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,meaning_ko FROM words").fetchall()

kill=[]; fixp=[]
for wid,ch,ko in rows:
    ko=(ko or '').strip()
    body=PYPFX.sub('', ko).strip()
    dead = bool(EXPL.search(ko))
    # 같은 글자 반복(从从/到到/吗吗) 또는 두 뜻이 크게 벌어짐
    if not dead and len(ch)>=2:
        if len(set(ch))==1 and ch not in HEAD: dead=True   # 从从·到到는 가짜, 妈妈·谢谢는 진짜
        elif len(ch)==2 and ch not in HEAD and TWOGLOSS.search(body): dead=True
        elif len(ch)>=4 and TWOGLOSS.search(body): dead=True
    if dead: kill.append((wid,ch,ko))
    elif body and body!=ko: fixp.append((body,wid))

print(f"  제거 {len(kill)}개 / 병음접두 정리 {len(fixp)}개")
print("  --- 제거 예시 ---")
for wid,ch,ko in kill[:18]: print(f"   {ch:12s} {ko[:44]}")
print("  --- 유지(정리) 예시 ---")
for body,wid in fixp[:10]:
    ch=next(c for i,c,k in rows if i==wid)
    print(f"   {ch:12s} → {body[:40]}")

if '--commit' in sys.argv:
    if fixp:
        con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", fixp); con.commit()
    if kill:
        ids=[k[0] for k in kill]
        q=','.join('?'*len(ids))
        con.execute(f"DELETE FROM word_progress WHERE word_id IN ({q})", ids)
        con.execute(f"DELETE FROM study_log WHERE word_id IN ({q})", ids)
        con.execute(f"DELETE FROM words WHERE id IN ({q})", ids)
        con.commit()
    r2=con.execute("SELECT id FROM words ORDER BY difficulty, freq DESC, chinese").fetchall()
    con.executemany("UPDATE words SET seq=? WHERE id=?", [(i+1, r[0]) for i,r in enumerate(r2)])
    con.commit()
    print(f"  ✅ 반영 완료 · 남은 단어 {len(r2)}개")
else:
    print("  [DRY RUN]")
con.close()
