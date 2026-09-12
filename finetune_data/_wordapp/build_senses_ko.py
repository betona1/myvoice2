# -*- coding: utf-8 -*-
"""다의어 한국어 뜻 생성. CC-CEDICT의 여러 뜻을 한 번에 번역해 ①②③ 으로 정리한다.
  师傅  master; qualified worker; respectful form of address for older men
    → ① 사부 ② 숙련공 ③ 기사님(연장자 남성에 대한 정중한 호칭)
meaning_ko = 대표 뜻(간단 표시용), senses_ko = 전체 뜻(사전식)"""
import os, re, sqlite3, time
os.chdir('/app')
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/senses.log'
CIR='①②③④⑤⑥⑦⑧'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

def norm(t):
    t=(t or '').strip(' .,;')
    for a,b in RULES:
        x=re.sub(a,b,t)
        if x!=t: return x
    return t

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("""SELECT id,chinese,meaning_ko,meaning_en FROM words
                    WHERE (senses_ko IS NULL OR senses_ko='') ORDER BY seq""").fetchall()
log(f"대상 {len(rows)}개")

ok=fail=0; buf=[]
for i,(wid,ch,ko,en) in enumerate(rows,1):
    parts=[p.strip() for p in (en or '').split(';') if p.strip()][:6]
    ks=[]
    if parts:
        src='; '.join(parts)
        for a in range(3):
            try:
                t=ts.translate_text(src, translator='google', from_language='en', to_language='ko') or ''
                ks=[norm(x) for x in t.split(';') if x.strip()]
                break
            except Exception:
                if a==2: fail+=1
                else: time.sleep(2.0*(a+1))
        time.sleep(0.35)
    if not ks:
        # 영어 뜻이 없으면 기존 한국어를 대표 뜻으로
        ks=[norm(ko)] if (ko or '').strip() else []
    if not ks: continue
    # 중복 제거
    seen=[]; 
    for x in ks:
        if x and x not in seen: seen.append(x)
    ks=seen[:6]
    sen=' '.join(f'{CIR[j]} {x}' for j,x in enumerate(ks)) if len(ks)>1 else ks[0]
    buf.append((sen, ks[0], wid)); ok+=1
    if len(buf)>=50:
        con.executemany("UPDATE words SET senses_ko=?, meaning_ko=? WHERE id=?", buf); con.commit(); buf=[]
        log(f"  {i}/{len(rows)} 완료{ok} 실패{fail}")
if buf: con.executemany("UPDATE words SET senses_ko=?, meaning_ko=? WHERE id=?", buf); con.commit()
log(f"완료: {ok} / 실패 {fail}")
for w in ['东西','意思','师傅','主人','司机','老板']:
    r=con.execute("SELECT chinese,meaning_ko,senses_ko FROM words WHERE chinese=?",(w,)).fetchone()
    if r: log(f"   {r[0]:6s} 대표:{str(r[1])[:12]:14s} 전체:{str(r[2])[:70]}")
con.close()
