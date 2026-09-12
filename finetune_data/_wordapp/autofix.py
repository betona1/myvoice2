# -*- coding: utf-8 -*-
"""자동 교정 가능한 문제를 일괄 처리한다.
 A) 영어잔존   : 한국어에 남은 영어를 EN→KO 번역
 B) 사전표기잔존: [병음]·변형·참조 제거 후 영어 정의로 재번역
 C) 뜻중복반복 : '또는 또는' 처럼 반복된 말 정리
 D) 뜻잘림     : 수식어만 남은 것 재번역
 E) 뜻너무짧음 : 영어 정의로 보강
 F) 뜻겹침     : 전체뜻(①②)이 있으면 서로 다른 대표뜻으로 분리"""
import os, re, sqlite3, time, sys
os.chdir('/app')
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/autofix.log'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
LATIN=re.compile(r'(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])')
ONLYLAT=re.compile(r'^[A-Za-z0-9 ,.\-\'()/;]+$')
NOTA=re.compile(r'(\[|\]|\||변형|variant|CL:|abbr|참조)')
MOD={'매우','아주','너무','정말','진짜','조금','약간','많이','잘','더','가장','제일','또','다시',
     '함께','같이','바로','이미','아직','곧','막','점점','서로','모두','다','좀','꽤','상당히'}
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]
KEEP={'TV','SDU','BS','km','kg','cm','mm','PC','DVD','CD','AI','IT'}

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

def norm(t):
    t=re.sub(r'\s{2,}',' ',(t or '').strip(' .,;'))
    for a,b in RULES:
        x=re.sub(a,b,t)
        if x!=t: return x
    return t

def dedup(t):
    """'또는 또는', '수백만, 수백만' 같은 반복 제거"""
    t=re.sub(r'\b(.{2,}?)(\s*[,、]?\s*\1)+\b', r'\1', t or '')
    return re.sub(r'\s{2,}',' ',t).strip(' ,')

def clean_en(en):
    en=re.sub(r'\([^)]*\)','',en or '')
    ps=[p.strip() for p in re.split(r'[;/]', en) if p.strip()]
    return '; '.join(ps[:3])

def tr(src, frm, to):
    for a in range(3):
        try: return (ts.translate_text(src, translator='google', from_language=frm, to_language=to) or '').strip()
        except Exception:
            if a<2: time.sleep(2.0*(a+1))
    return ''

def carrier(ch):
    r=tr(f'「{ch}」的意思是什么？','zh','ko')
    m=QUOTE.match(r); v=m.group(1).strip() if m else ''
    if v and ONLYLAT.match(v): v=tr(v,'en','ko')      # 영어 새어나오면 한 번 더
    return '' if v==ch else v

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,meaning_ko,meaning_en,senses_ko,freq FROM words").fetchall()

from collections import defaultdict
grp=defaultdict(list)
for r in rows:
    k=(r[2] or '').strip()
    if k: grp[k].append(r)

upd=[]; n=dict(en=0,nota=0,rep=0,trunc=0,short=0,dup=0)
todo=[]
for wid,ch,ko,en,sen,freq in rows:
    ko=(ko or '').strip(); why=None
    lat=[x for x in LATIN.findall(ko) if x not in KEEP]
    if not ko: why='trunc'
    elif lat: why='en'
    elif NOTA.search(ko): why='nota'
    elif re.search(r'(.{2,})\s*\1', ko): why='rep'
    elif ko in MOD and len(ch)>=3: why='trunc'
    elif len(ch)>=3 and len(ko)<=2: why='short'
    elif len(grp.get(ko,[]))>1 and sen and '②' in sen: why='dup'
    if why: todo.append((wid,ch,ko,en,sen,why))
log(f"자동교정 대상 {len(todo)}개")

for i,(wid,ch,ko,en,sen,why) in enumerate(todo,1):
    new=''
    if why=='rep':
        new=dedup(ko)
    elif why=='dup':
        # 전체뜻에서 겹치지 않는 뜻을 대표로 승격
        parts=[p.strip() for p in re.split(r'[①②③④⑤⑥⑦⑧]\s*', sen) if p.strip()]
        for p in parts:
            if len(grp.get(p,[]))<=1 and p!=ko: new=p; break
    elif why=='en':
        src=clean_en(en) or ko
        new=tr(src,'en','ko') if src else ''
        if not new or LATIN.search(new): new=carrier(ch)
        time.sleep(0.3)
    else:
        src=clean_en(en)
        new=tr(src,'en','ko') if src else ''
        if not new or NOTA.search(new) or LATIN.search(new): new=carrier(ch)
        time.sleep(0.3)
    new=dedup(norm(new))
    if new and new!=ko and not NOTA.search(new):
        upd.append((new,wid)); n[why]+=1
    if i%50==0:
        if upd: con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit(); upd=[]
        log(f"  {i}/{len(todo)}  " + ' '.join(f'{k}={v}' for k,v in n.items() if v))
if upd: con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", upd); con.commit()
log("완료: " + ' '.join(f'{k}={v}' for k,v in n.items()))
con.close()
