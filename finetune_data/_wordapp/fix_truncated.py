# -*- coding: utf-8 -*-
"""뜻이 수식어만 남아 잘린 단어를 다시 번역한다.
  很高兴 → '매우'  (X)   실제로는 '매우 기쁘다'
합성 표현은 CC-CEDICT 표제어가 없어 영어가 부정확하므로 **중국어를 캐리어 문장으로** 번역한다."""
import os, re, sqlite3, time, sys
os.chdir('/app')
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/trunc.log'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
# 이것만 남아 있으면 잘린 것
MOD={'매우','아주','너무','정말','진짜','조금','약간','많이','잘','더','가장','제일',
     '또','다시','함께','같이','바로','이미','아직','곧','막','점점','서로','모두','다','좀','꽤','상당히'}
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'이에요$','이다'),(r'예요$','이다'),
       (r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,meaning_ko FROM words WHERE meaning_ko!=''").fetchall()
# 수식어 단독 + 한자 3자 이상 = 뒷말이 잘림 (非常처럼 2자짜리는 '매우'가 정답이라 제외)
targets=[(i,c,k) for i,c,k in rows if k.strip() in MOD and len(c)>=3]
log(f"대상 {len(targets)}개")
ok=fail=0; upd=[]
for n,(wid,ch,ko) in enumerate(targets,1):
    new=''
    for a in range(3):
        try:
            r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google',
                                from_language='zh', to_language='ko')
            m=QUOTE.match((r or '').strip()); new=m.group(1).strip() if m else ''
            if new==ch: new=''
            break
        except Exception:
            if a==2: fail+=1
            else: time.sleep(2.0*(a+1))
    if new and new not in MOD:
        for a,b in RULES:
            x=re.sub(a,b,new)
            if x!=new: new=x; break
        upd.append((new,wid)); ok+=1
        if n<=20: log(f"   {ch:10s} {ko:6s} → {new}")
    time.sleep(0.35)
    if n%50==0:
        if upd: con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit(); upd=[]
        log(f"  {n}/{len(targets)} 갱신{ok} 실패{fail}")
if upd: con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit()
log(f"완료: 갱신 {ok} / 실패 {fail}")
con.close()
