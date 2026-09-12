# -*- coding: utf-8 -*-
"""HSK 수입 어휘의 다음자 독음/뜻 교정.
HSK 원본은 forms 배열의 첫 항목을 썼는데 그게 흔한 독음이 아니다.
  读 → dòu(콤마)  X   실제 상용은 dú(읽다)
pypinyin이 주는 상용 독음과 일치하는 form을 골라 병음·영어뜻을 다시 잡고,
한국어 대표 뜻은 캐리어 문장 번역으로 실제 쓰임에 맞춘다."""
import os, re, json, sqlite3, time, sys
os.chdir('/app')
from pypinyin import pinyin as _py, Style
import translators as ts
DB='database/voices.db'
LOG='/app/finetune_data/_wordapp/fix_reading.log'
CIR='①②③④⑤⑥⑦⑧'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
ONLYLAT=re.compile(r"^[A-Za-z0-9 ,.\-'()/;]+$")
DROP=re.compile(r'^(CL:|variant of|old variant|see |see also|abbr\. for|surname |Taiwan pr\.|erhua variant)', re.I)
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

def norm(t):
    t=re.sub(r'\s{2,}',' ',(t or '').strip(' .,;'))
    for a,b in RULES:
        x=re.sub(a,b,t)
        if x!=t: return x
    return t

def key(p):   # 성조 무시 비교
    M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
       'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'v','ǘ':'v','ǚ':'v','ǜ':'v','ü':'v'}
    return ''.join(M.get(c,c) for c in (p or '').lower() if c.isalpha() or c in M)

src={}
for x in json.load(open('database/hsk/complete_hsk.json',encoding='utf-8')):
    src[x['simplified']]=x

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,pinyin,meaning_ko,meaning_en FROM words WHERE subject='HSK어휘'").fetchall()
log(f"검사 {len(rows)}개")

need=[]
for wid,ch,py,ko,en in rows:
    x=src.get(ch)
    if not x or len(x.get('forms',[]))<2: continue
    want=' '.join(p[0] for p in _py(ch, style=Style.TONE))      # 상용 독음
    cur=key(py)
    if key(want)==cur: continue                                  # 이미 맞음
    best=None
    for f in x['forms']:
        if key(f['transcriptions']['pinyin'])==key(want): best=f; break
    if best: need.append((wid,ch,py,best))
log(f"독음 교정 대상 {len(need)}개")

ok=0
for i,(wid,ch,oldpy,f) in enumerate(need,1):
    newpy=f['transcriptions']['pinyin']
    mean=[m.strip() for m in f['meanings'] if m.strip() and not DROP.match(m.strip())][:6]
    en='; '.join(mean)
    ks=[]
    if en:
        for a in range(3):
            try:
                t=ts.translate_text('; '.join(mean[:4]), translator='google', from_language='en', to_language='ko') or ''
                ks=[norm(z) for z in t.split(';') if z.strip()]
                break
            except Exception:
                if a<2: time.sleep(2.0*(a+1))
        time.sleep(0.3)
    # 실제 쓰임 기준 대표 뜻
    prim=''
    for a in range(2):
        try:
            r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google', from_language='zh', to_language='ko')
            m=QUOTE.match((r or '').strip()); prim=m.group(1).strip() if m else ''
            if prim and ONLYLAT.match(prim):
                prim=ts.translate_text(prim,'en','ko') if False else (ts.translate_text(prim, translator='google', from_language='en', to_language='ko') or '')
            break
        except Exception:
            if a<1: time.sleep(2.0)
    time.sleep(0.3)
    prim=norm(prim)
    seen=[]
    for z in ([prim] if prim else [])+ks:
        if z and z not in seen: seen.append(z)
    if not seen: continue
    sen=' '.join(f'{CIR[j]} {z}' for j,z in enumerate(seen[:6])) if len(seen)>1 else seen[0]
    tones=' '.join(p[0] for p in _py(ch, style=Style.TONE3))
    con.execute("UPDATE words SET pinyin=?, tones=?, meaning_ko=?, meaning_en=?, senses_ko=? WHERE id=?",
                (' '.join(p[0] for p in _py(ch, style=Style.TONE)), tones, seen[0], en, sen, wid))
    ok+=1
    if ok<=15: log(f"   {ch}  {oldpy} → {newpy}  '{seen[0]}'")
    if i%30==0: con.commit(); log(f"  {i}/{len(need)} 교정{ok}")
con.commit()
log(f"완료: {ok}개 교정")
con.close()
