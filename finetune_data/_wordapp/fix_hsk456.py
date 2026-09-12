# -*- coding: utf-8 -*-
"""HSK 4~6급 정비.
 A) 병음 대문자(고유명사 오인) → 상용 독음 소문자
 B) 단일 글자 뜻 재선정 — 캐리어 문장 번역. 단, 결과가 병음의 한글 음역이면 버린다(分→'펜' 방지)
 C) 영어 잔존 제거"""
import os, re, sqlite3, time, sys
os.chdir('/app')
from pypinyin import pinyin as _py, Style
import translators as ts
DB='database/voices.db'; CIR='①②③④⑤⑥⑦⑧'
LOG='/app/finetune_data/_wordapp/fix456.log'
QUOTE=re.compile(r'^\s*["“「‘\']([^"”」’\']{1,60})["”」’\']')
ONLYLAT=re.compile(r"^[A-Za-z0-9 ,.\-'()/;]+$")
LATIN=re.compile(r'(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])')
KEEP={'TV','SDU','BS','km','kg','cm','mm','PC','DVD','CD','AI','IT'}
RULES=[(r'해요$','하다'),(r'했어요?$','하다'),(r'돼요$','되다'),(r'이에요$','이다'),
       (r'예요$','이다'),(r'있어요$','있다'),(r'없어요$','없다'),(r'었어요$','다')]

CHO=['g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h']
JUNG=['a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i']
JONG=['','g','k','ks','n','nj','nh','d','l','lg','lm','lb','ls','lt','lp','lh','m','b','ps','s','ss','ng','j','ch','k','t','p','h']
def rom(s):
    o=[]
    for c in s:
        v=ord(c)
        if 0xAC00<=v<=0xD7A3:
            i=v-0xAC00; o.append(CHO[i//588]+JUNG[(i%588)//28]+JONG[i%28])
        elif c.isalpha(): o.append(c.lower())
    return ''.join(o)
def st(p):
    M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
       'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'v','ǘ':'v','ǚ':'v','ǜ':'v','ü':'v'}
    return ''.join(M.get(c,c) for c in (p or '').lower() if c.isalpha() or c in M)
def lev(a,b):
    if a==b: return 0
    if not a or not b: return max(len(a),len(b))
    prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        cur=[i]
        for j,y in enumerate(b,1): cur.append(min(prev[j]+1,cur[j-1]+1,prev[j-1]+(x!=y)))
        prev=cur
    return prev[-1]

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

# ── A) 병음 대문자 정정 ──
n=0
for wid,ch,py in con.execute("SELECT id,chinese,pinyin FROM words WHERE hsk BETWEEN 4 AND 6").fetchall():
    if not py or not py[:1].isupper(): continue
    want=' '.join(p[0] for p in _py(ch, style=Style.TONE))
    tones=' '.join(p[0] for p in _py(ch, style=Style.TONE3))
    con.execute("UPDATE words SET pinyin=?, tones=? WHERE id=?", (want, tones, wid)); n+=1
con.commit(); log(f"A) 병음 대문자 정정 {n}개")

# ── B) 단일 글자 뜻 재선정 ──
rows=con.execute("""SELECT id,chinese,pinyin,meaning_ko,senses_ko FROM words
                    WHERE hsk BETWEEN 4 AND 6 AND length(chinese)=1 ORDER BY hsk,freq DESC""").fetchall()
log(f"B) 단일 글자 {len(rows)}개")
ok=rej=same=0
for i,(wid,ch,py,ko,sen) in enumerate(rows,1):
    got=''
    for a in range(3):
        try:
            r=ts.translate_text(f'「{ch}」的意思是什么？', translator='google', from_language='zh', to_language='ko')
            m=QUOTE.match((r or '').strip()); got=m.group(1).strip() if m else ''
            if got and ONLYLAT.match(got):
                got=(ts.translate_text(got, translator='google', from_language='en', to_language='ko') or '').strip()
            break
        except Exception:
            if a<2: time.sleep(2.0*(a+1))
    time.sleep(0.35)
    got=norm(got)
    # 안전장치: 병음의 한글 음역이면 버린다 (分→'펜' 유형)
    if got and len(got)<=6 and lev(rom(got), st(py)) <= max(1, len(st(py))//3):
        rej+=1; continue
    if not got or got==ch or LATIN.search(got): rej+=1; continue
    if got==(ko or '').strip(): same+=1; continue
    parts=[p.strip() for p in re.split(r'[①②③④⑤⑥⑦⑧]\s*', sen or '') if p.strip()]
    if got in parts: parts.remove(got)
    parts=[got]+parts
    ns=' '.join(f'{CIR[j]} {p}' for j,p in enumerate(parts[:6])) if len(parts)>1 else got
    con.execute("UPDATE words SET meaning_ko=?, senses_ko=? WHERE id=?", (got, ns, wid)); ok+=1
    if ok<=15: log(f"   {ch}  '{str(ko)[:14]}' → '{got}'")
    if i%50==0: con.commit(); log(f"  {i}/{len(rows)} 변경{ok} 유지{same} 기각{rej}")
con.commit(); log(f"B) 완료 변경 {ok} / 유지 {same} / 기각 {rej}")

# ── C) 영어 잔존 ──
rows=con.execute("SELECT id,chinese,meaning_ko,meaning_en FROM words WHERE hsk BETWEEN 4 AND 6").fetchall()
tg=[r for r in rows if [x for x in LATIN.findall(r[2] or '') if x not in KEEP]]
log(f"C) 영어 잔존 {len(tg)}개")
c3=0
for wid,ch,ko,en in tg:
    src='; '.join([p.strip() for p in re.split(r'[;/]', re.sub(r'\([^)]*\)','',en or '')) if p.strip()][:3])
    new=''
    if src:
        try: new=(ts.translate_text(src, translator='google', from_language='en', to_language='ko') or '').strip()
        except Exception: pass
        time.sleep(0.3)
    new=norm(new)
    if new and not LATIN.search(new):
        con.execute("UPDATE words SET meaning_ko=? WHERE id=?", (new, wid)); c3+=1
con.commit(); log(f"C) 완료 {c3}개")
con.close()
