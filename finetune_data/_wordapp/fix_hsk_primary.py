# -*- coding: utf-8 -*-
"""HSK 저급수 어휘의 '대표 뜻'을 실제 쓰임 기준으로 재선정.
사전은 뜻을 빈도순으로 주지 않는다.
  热 /to warm up; to heat up; hot (of weather)/ → 첫 뜻 '워밍업하다'는 어색, 실제는 '덥다'
  字 /letter; symbol; character/ → '편지'가 아니라 '글자'
캐리어 문장(「X」的意思是什么？) 번역을 대표로 올리고 나머지는 뒤에 남긴다."""
import os, re, sqlite3, time, sys
os.chdir('/app')
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/fix_primary_hsk.log'
CIR='①②③④⑤⑥⑦⑧'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
ONLYLAT=re.compile(r"^[A-Za-z0-9 ,.\-'()/;]+$")
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]
MAXLV=int(os.environ.get('MAXLV','3'))

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

def norm(t):
    t=re.sub(r'\s{2,}',' ',(t or '').strip(' .,;'))
    for a,b in RULES:
        x=re.sub(a,b,t)
        if x!=t: return x
    return t

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("""SELECT id,chinese,meaning_ko,senses_ko FROM words
                    WHERE hsk>0 AND hsk<=? ORDER BY hsk, freq DESC""",(MAXLV,)).fetchall()
log(f"대상 HSK 1~{MAXLV}급 {len(rows)}개")

ok=same=fail=0
for i,(wid,ch,ko,sen) in enumerate(rows,1):
    got=''
    for a in range(3):
        try:
            r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google', from_language='zh', to_language='ko')
            m=QUOTE.match((r or '').strip()); got=m.group(1).strip() if m else ''
            if got and ONLYLAT.match(got):
                got=(ts.translate_text(got, translator='google', from_language='en', to_language='ko') or '').strip()
            if got==ch: got=''
            break
        except Exception:
            if a==2: fail+=1
            else: time.sleep(2.0*(a+1))
    time.sleep(0.35)
    got=norm(got)
    if not got: continue
    if got==(ko or '').strip(): same+=1; continue
    parts=[p.strip() for p in re.split(r'[①②③④⑤⑥⑦⑧]\s*', sen or '') if p.strip()]
    if got in parts: parts.remove(got)
    parts=[got]+parts
    newsen=' '.join(f'{CIR[j]} {p}' for j,p in enumerate(parts[:6])) if len(parts)>1 else parts[0]
    con.execute("UPDATE words SET meaning_ko=?, senses_ko=? WHERE id=?", (got, newsen, wid))
    ok+=1
    if ok<=18: log(f"   {ch:6s} '{str(ko)[:16]}' → '{got}'")
    if i%50==0: con.commit(); log(f"  {i}/{len(rows)} 변경{ok} 유지{same} 실패{fail}")
con.commit()
log(f"완료: 변경 {ok} / 유지 {same} / 실패 {fail}")
con.close()
