# -*- coding: utf-8 -*-
"""뜻이 중복되거나 잘못된 단어를 CEDICT 영어 정의로 재번역해 구분한다.
   예) 汉语 → 데이터엔 '중국인'(오역). 영어 'Chinese language' → '중국어'
   퀴즈에서 정답/오답 뜻이 겹치면 문제가 성립하지 않으므로 중복 해소가 핵심."""
import os, re, sqlite3, time, sys
os.chdir('/app')
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/fix_dup.log'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
NOTE=re.compile(r'(성조|운모|성모|사용한다|를 씀|의 변화|앞에서는|경성|음역|명칭|주의)')
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

def clean_en(en):
    en=re.sub(r'\([^)]*\)','',en or '')
    parts=[p.strip() for p in re.split(r'[;/]', en) if p.strip()]
    return '; '.join(parts[:2]) if parts else ''

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,pinyin,meaning_ko,meaning_en,level FROM words").fetchall()

from collections import defaultdict
grp=defaultdict(list)
for wid,ch,py,ko,en,lv in rows:
    if (ko or '').strip(): grp[(ko.strip(), lv, len(ch))].append((wid,ch,en))

targets=[]
for k,v in grp.items():
    if len(v)>1: targets += v          # 뜻 중복
for wid,ch,py,ko,en,lv in rows:        # 설명 조각/빈 뜻
    if not (ko or '').strip() or NOTE.search(ko or ''):
        if not any(t[0]==wid for t in targets): targets.append((wid,ch,en))

log(f"수정 대상 {len(targets)}개 (뜻중복 + 설명조각)")
ok=fail=0; upd=[]
for i,(wid,ch,en) in enumerate(targets,1):
    src=clean_en(en); new=''
    try:
        if src:
            # 영어 정의를 번역하면 동음이의가 갈린다
            new=(ts.translate_text(src, translator='google', from_language='en', to_language='ko') or '').strip()
        if not new:
            r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google', from_language='zh', to_language='ko')
            m=QUOTE.match((r or '').strip()); new=m.group(1).strip() if m else ''
    except Exception:
        fail+=1
    if new and new!=ch:
        for a,b in RULES:
            x=re.sub(a,b,new)
            if x!=new: new=x; break
        upd.append((new,wid)); ok+=1
    if i%50==0:
        if upd: con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit(); upd=[]
        log(f"  {i}/{len(targets)} 갱신{ok} 실패{fail}")
    time.sleep(0.35)
if upd: con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit()
log(f"완료: 갱신 {ok} / 실패 {fail}")

# 중복 잔여 확인
g2=defaultdict(int)
for ko,lv,L in con.execute("SELECT meaning_ko,level,length(chinese) FROM words WHERE meaning_ko!=''"):
    g2[(ko,lv,L)]+=1
log(f"남은 중복 그룹 {sum(1 for v in g2.values() if v>1)}개")
con.close()
