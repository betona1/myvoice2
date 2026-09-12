# -*- coding: utf-8 -*-
"""HSK 공식 급수를 words에 매핑.
출처 2종 교차검증 완료 (4,991개 동일):
  - drkameleon/complete-hsk-vocabulary (구 HSK 2.0 old-1~6 + 신 HSK 3.0)
  - clem109/hsk-vocabulary (구 HSK 2.0)
words.hsk = 1~6 (구 HSK 2.0 급수), words.hsk_new = 신 HSK 3.0 급수"""
import os, json, sqlite3, glob
os.chdir('/app')
DB='database/voices.db'
BASE='database/hsk'

old={}; new={}
for x in json.load(open(f'{BASE}/complete_hsk.json',encoding='utf-8')):
    w=x['simplified']
    for lv in x.get('level',[]):
        if lv.startswith('old-'):
            n=int(lv.split('-')[1])
            if w not in old or n<old[w]: old[w]=n
        elif lv.startswith('new-'):
            n=int(lv.split('-')[1])
            if w not in new or n<new[w]: new[w]=n
# 두 번째 소스로 보강
for f in sorted(glob.glob(f'{BASE}/clem109/l*.json')):
    n=int(os.path.basename(f)[1])
    for w in json.load(open(f,encoding='utf-8')):
        h=w['hanzi']
        if h not in old or n<old[h]: old[h]=n
print(f"  HSK 2.0 어휘 {len(old)}개 / HSK 3.0 {len(new)}개")

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
for col in ('hsk','hsk_new'):
    try: con.execute(f"ALTER TABLE words ADD COLUMN {col} INTEGER DEFAULT 0"); con.commit()
    except Exception: pass

rows=con.execute("SELECT id,chinese FROM words").fetchall()
upd=[(old.get(ch,0), new.get(ch,0), wid) for wid,ch in rows]
con.executemany("UPDATE words SET hsk=?, hsk_new=? WHERE id=?", upd)
con.commit()

print()
print("  우리 단어의 HSK 2.0 급수 분포:")
tot=0
for r in con.execute("SELECT hsk,COUNT(*) FROM words GROUP BY hsk ORDER BY hsk"):
    lab='HSK 밖' if r[0]==0 else f'{r[0]}급'
    print(f"   {lab:8s} {r[1]:>5}")
    if r[0]: tot+=r[1]
n=con.execute("SELECT COUNT(*) FROM words").fetchone()[0]
print(f"   → 전체 {n}개 중 HSK 어휘 {tot}개 ({100*tot//n}%)")

print()
print("  HSK 급수별 보유율 (공식 목록 대비):")
from collections import Counter
offi=Counter(old.values())
have=Counter()
for lv, in con.execute("SELECT hsk FROM words WHERE hsk>0"): have[lv]+=1
miss_all=0
for lv in range(1,7):
    o=offi.get(lv,0); h=have.get(lv,0)
    miss_all += o-h
    bar='▓'*int(20*h/max(o,1))+'░'*(20-int(20*h/max(o,1)))
    print(f"   {lv}급  {bar} {h:>4}/{o:<4} ({100*h//max(o,1)}%)")
print(f"   → 없는 HSK 단어 {miss_all}개")
con.close()
