# -*- coding: utf-8 -*-
"""고빈도 단어의 '대표 뜻'을 실제 쓰임에 맞게 재선정.
CC-CEDICT는 뜻을 빈도순으로 주지 않는다.
  告诉 /to press charges/to file a complaint/to tell/to inform/
  → 사전 첫 뜻은 '고소하다'지만 실제로는 '말하다'가 압도적
캐리어 문장 번역(실제 쓰임 반영)을 대표 뜻으로 올리고, 전체 뜻은 그대로 둔다."""
import os, re, sqlite3, time, sys
os.chdir('/app')
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/primary.log'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
MINFREQ=int(os.environ.get('MINFREQ','8'))

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("""SELECT id,chinese,meaning_ko,senses_ko,freq FROM words
                    WHERE freq>=? AND senses_ko!='' AND senses_ko LIKE '%②%'
                    ORDER BY freq DESC""",(MINFREQ,)).fetchall()
log(f"대상 {len(rows)}개 (빈도 {MINFREQ}회 이상 다의어)")

ok=same=fail=0; upd=[]
for i,(wid,ch,ko,sen,freq) in enumerate(rows,1):
    got=''
    for a in range(3):
        try:
            r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google',
                                from_language='zh', to_language='ko')
            m=QUOTE.match((r or '').strip()); got=m.group(1).strip() if m else ''
            if got==ch: got=''
            break
        except Exception:
            if a==2: fail+=1
            else: time.sleep(2.0*(a+1))
    time.sleep(0.35)
    if not got: continue
    if got==(ko or '').strip(): same+=1; continue
    # 전체 뜻 목록에 이미 있으면 그 뜻을 대표로 승격, 없으면 맨 앞에 추가
    parts=re.split(r'[①②③④⑤⑥⑦⑧]\s*', sen)
    parts=[p.strip() for p in parts if p.strip()]
    if got in parts: parts.remove(got)
    parts=[got]+parts
    CIR='①②③④⑤⑥⑦⑧'
    newsen=' '.join(f'{CIR[j]} {p}' for j,p in enumerate(parts[:6]))
    upd.append((got, newsen, wid)); ok+=1
    if ok<=15: log(f"   {ch:8s}(빈도{freq}) {str(ko)[:14]:16s} → {got}")
    if len(upd)>=40:
        con.executemany("UPDATE words SET meaning_ko=?, senses_ko=? WHERE id=?", upd); con.commit(); upd=[]
        log(f"  {i}/{len(rows)} 변경{ok} 유지{same} 실패{fail}")
if upd: con.executemany("UPDATE words SET meaning_ko=?, senses_ko=? WHERE id=?", upd); con.commit()
log(f"완료: 변경 {ok} / 유지 {same} / 실패 {fail}")
con.close()
