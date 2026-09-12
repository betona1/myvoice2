# -*- coding: utf-8 -*-
"""비HSK 항목 중 '단어가 아닌 것'만 좁게 분류.
   판정 기준은 3가지뿐 — 문장 / 나열 / 조각. 사전 미등재 자체는 근거로 쓰지 않는다."""
import os, re, sys, json, sqlite3
os.chdir('/app')
APPLY = '--apply' in sys.argv

CE = set()
for line in open('database/cedict_full.txt', encoding='utf-8'):
    if line.startswith('#'): continue
    p = line.split(' ', 2)
    if len(p) >= 2: CE.add(p[0]); CE.add(p[1])

PRON  = '你我他她它咱您'
PART  = '吗呢吧啊呀哇嘛'
# ① 문장: 대명사 주어가 있거나, 의문/감탄 어기조사로 끝나거나, 한국어 뜻이 완결된 문장
SUBJ  = re.compile(f'^[{PRON}]')
ENDP  = re.compile(f'[{PART}]$')
KOSENT= re.compile(r'(니까\??$|세요\.?$|습니다\.?$|해요\.?$|어요\.?$|아요\.?$|예요\.?$|이에요\.?$|군요\.?$|하니\??$|나요\??$|십니까)')
KOQ   = re.compile(r'\?$')
# ② 나열: 같은 부류를 죽 늘어놓은 것 (연습용 목록)
ENUMD = re.compile(r'^[一二三四五六七八九十百千万亿零两]{3,}$')
ENUM2 = re.compile(f'^[{PRON}]{{2,}}$')
DIRE  = re.compile(r'^(上|下|进|出|回|过|起)(去|来)(上|下|进|出|回|过|起)(去|来)')
def is_enum(ch):
    if ENUMD.match(ch) or ENUM2.match(ch) or DIRE.match(ch): return True
    if len(ch) >= 4:            # 낱글자들이 각각 사전에 있고 합쳐진 건 사전에 없음 = 나열
        if ch not in CE and all(c in CE for c in ch) and len(set(ch)) == len(ch):
            return True
    return False
# ③ 조각: 뜻이 잘렸거나 의미가 성립 안 하는 토막
FRAG  = re.compile(r'(^|\s)(매우|너무|정말)\s{2,}|\s{2,}')
BADKO = re.compile(r'^(위치|성 나|그럼 원하다|그거 알아요)$')

con = sqlite3.connect('database/voices.db', timeout=60); con.execute('PRAGMA busy_timeout=60000')
cols = [r[1] for r in con.execute('PRAGMA table_info(words)')]
if 'excluded' not in cols:
    con.execute('ALTER TABLE words ADD COLUMN excluded INTEGER DEFAULT 0'); con.commit()
con.execute('UPDATE words SET excluded=0')   # 매 실행마다 초기화 (재현 가능)
con.commit()

# 오탐 방지 화이트리스트 — 실제 용어·복합명사
KEEP = {'前鼻韵母','后鼻韵母','舌尖中音','舌面前音','世界地图','以茶代酒','看你说的','普通话'}
rows = con.execute("SELECT id,chinese,pinyin,meaning_ko FROM words WHERE (hsk IS NULL OR hsk=0)").fetchall()
B = {}
def put(k, r): B.setdefault(k, []).append(r)

for wid, ch, py, ko in rows:
    ch = (ch or '').strip(); ko = (ko or '').strip()
    if ch in KEEP:
        continue
    if ch in CE and len(ch) <= 3:      # 짧고 사전 등재면 무조건 유지
        continue
    if is_enum(ch):                    put('② 나열 (연습용 목록)', (wid,ch,py,ko)); continue
    if SUBJ.match(ch) and len(ch) >= 3: put('① 문장 (주어 있음)', (wid,ch,py,ko)); continue
    if ENDP.search(ch) and len(ch) >= 3 and ch not in CE:
                                       put('① 문장 (어기조사 종결)', (wid,ch,py,ko)); continue
    if ch not in CE and (KOSENT.search(ko) or KOQ.search(ko)):
                                       put('① 문장 (뜻이 완결 문장)', (wid,ch,py,ko)); continue
    if ch not in CE and len(ch) >= 5:  put('① 문장 (5자 이상 어구)', (wid,ch,py,ko)); continue
    if ch not in CE and (FRAG.search(ko) or BADKO.match(ko) or not ko):
                                       put('③ 조각 (뜻 불성립)', (wid,ch,py,ko)); continue

tot = 0
for k in sorted(B):
    g = B[k]; tot += len(g)
    print(f"■ {k}: {len(g)}개")
    for wid,ch,py,ko in g[:10]: print(f"   {ch:<10} {py[:20]:<21} {ko[:28]}")
    print()
print(f"제외 대상 합계 {tot}개  /  유지 {len(rows)-tot}개  (비HSK {len(rows)})")
json.dump({str(w):[c,p,k] for g in B.values() for w,c,p,k in g},
          open('finetune_data/_wordapp/junk_candidates.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=1)
if APPLY:
    ids=[w for g in B.values() for w,_,_,_ in g]
    con.executemany("UPDATE words SET excluded=1 WHERE id=?", [(i,) for i in ids]); con.commit()
    print(f"\nexcluded=1 표시 {len(ids)}개 — 삭제 아님, UPDATE words SET excluded=0 으로 즉시 복구")
con.close()
