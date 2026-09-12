# -*- coding: utf-8 -*-
"""빠진 HSK 2.0 어휘를 words에 추가.
병음·영어뜻·빈도는 HSK 데이터에서 가져오고, 한국어 뜻만 번역한다.
subject='HSK어휘', level은 HSK 급수(5·6급은 5로 묶음)"""
import os, re, json, glob, sqlite3, time, sys, math
os.chdir('/app')
import translators as ts
DB='database/voices.db'; BASE='database/hsk'
LOG='/app/finetune_data/_wordapp/import_hsk.log'
CIR='①②③④⑤⑥⑦⑧'
DROP=re.compile(r'^(CL:|variant of|old variant|see |see also|abbr\. for|surname |Taiwan pr\.|erhua variant|used in|also written)', re.I)
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

# HSK 데이터 적재
info={}
for x in json.load(open(f'{BASE}/complete_hsk.json',encoding='utf-8')):
    w=x['simplified']; lv=None
    for l in x.get('level',[]):
        if l.startswith('old-'):
            n=int(l.split('-')[1])
            lv=n if lv is None else min(lv,n)
    if lv is None: continue
    f=(x.get('forms') or [{}])[0]
    py=(f.get('transcriptions') or {}).get('pinyin','')
    mean=[m.strip() for m in (f.get('meanings') or []) if m.strip() and not DROP.match(m.strip())]
    info[w]={'lv':lv,'py':py,'en':'; '.join(mean[:6]),'freq':x.get('frequency',0)}
for f in sorted(glob.glob(f'{BASE}/clem109/l*.json')):
    n=int(os.path.basename(f)[1])
    for w in json.load(open(f,encoding='utf-8')):
        h=w['hanzi']
        e=info.setdefault(h, {'lv':n,'py':w.get('pinyin',''),'en':'','freq':0})
        if n<e['lv']: e['lv']=n
        if not e['py']: e['py']=w.get('pinyin','')
        if not e['en']: e['en']='; '.join((w.get('translations') or [])[:6])

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
have={r[0] for r in con.execute("SELECT chinese FROM words")}
known={r[0]:r[1] for r in con.execute("SELECT chinese,meaning_ko FROM words WHERE meaning_ko!=''")}
miss=[(w,d) for w,d in info.items() if w not in have]
log(f"추가 대상 {len(miss)}개")

rows=[]; ok=fail=0
for i,(w,d) in enumerate(sorted(miss, key=lambda x:(x[1]['lv'], -x[1]['freq'])),1):
    en=d['en']; ks=[]
    if en:
        for a in range(3):
            try:
                t=ts.translate_text('; '.join(en.split('; ')[:4]), translator='google',
                                    from_language='en', to_language='ko') or ''
                ks=[norm(x) for x in t.split(';') if x.strip()]
                break
            except Exception:
                if a==2: fail+=1
                else: time.sleep(2.0*(a+1))
        time.sleep(0.35)
    seen=[]
    for x in ks:
        if x and x not in seen: seen.append(x)
    ks=seen[:6]
    if not ks: continue
    sen=' '.join(f'{CIR[j]} {x}' for j,x in enumerate(ks)) if len(ks)>1 else ks[0]
    lv=min(5, d['lv'])                       # 학습용 레벨(1~5)
    diff=lv*10 + len(w)*2 - math.log1p(d['freq'] or 1)*0.5
    rows.append((1, w, d['py'], '', ks[0], en, 'textbook', 'HSK어휘', lv, d['freq'],
                 round(diff,3), f"HSK {d['lv']}급", sen, d['lv'], 0))
    ok+=1
    if len(rows)>=100:
        con.executemany("""INSERT OR IGNORE INTO words
          (user_id_dummy,chinese,pinyin,tones,meaning_ko,meaning_en,category,subject,level,freq,difficulty,seq_label,senses_ko,hsk,hsk_new)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows) if False else None
        # 실제 컬럼에 맞춰 삽입
        con.executemany("""INSERT OR IGNORE INTO words
          (chinese,pinyin,tones,meaning_ko,meaning_en,level,freq,difficulty,subject,senses_ko,hsk,hsk_new,seq,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,datetime('now'))""",
          [(r[1],r[2],r[3],r[4],r[5],r[8],r[9],r[10],r[7],r[12],r[13],r[14]) for r in rows])
        con.commit(); rows=[]
        log(f"  {i}/{len(miss)} 삽입{ok} 실패{fail}")
if rows:
    con.executemany("""INSERT OR IGNORE INTO words
      (chinese,pinyin,tones,meaning_ko,meaning_en,level,freq,difficulty,subject,senses_ko,hsk,hsk_new,seq,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,datetime('now'))""",
      [(r[1],r[2],r[3],r[4],r[5],r[8],r[9],r[10],r[7],r[12],r[13],r[14]) for r in rows])
    con.commit()

# 성조 채우고 seq 재부여
from pypinyin import pinyin as _py, Style
fix=[]
for wid,ch,tn in con.execute("SELECT id,chinese,tones FROM words WHERE tones='' OR tones IS NULL"):
    fix.append((' '.join(x[0] for x in _py(ch,style=Style.TONE3)), wid))
if fix: con.executemany("UPDATE words SET tones=? WHERE id=?", fix); con.commit()
r2=con.execute("SELECT id FROM words ORDER BY difficulty, freq DESC, chinese").fetchall()
con.executemany("UPDATE words SET seq=? WHERE id=?", [(i+1,r[0]) for i,r in enumerate(r2)])
con.commit()
log(f"완료: 추가 {ok} / 실패 {fail} / 전체 {len(r2)}개")
con.close()
