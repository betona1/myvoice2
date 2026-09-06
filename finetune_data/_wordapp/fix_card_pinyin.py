# -*- coding: utf-8 -*-
"""카드의 병음 칸이 깨진 것을 고친다.
   2주2교시에서 「같은 구절이 앞에 두 번 덧붙은」 병음이 나왔다 — 한자 16자에 병음 33개.
   짝짓기로는 손쓸 수 없으니 병음을 다시 만든다.
   ⚠️ 새로 만든 것이 더 나을 때만 바꾼다. 옛것은 pinyin_old 에 남긴다.
   숫자는 자료의 관행대로 읽기를 붙이지 않는다(晚上9点 → wǎn shàng diǎn)."""
import os, re, sys, sqlite3, unicodedata
sys.path.insert(0,'/app'); os.chdir('/app')
from pypinyin import pinyin as _py, Style
HAN=lambda c: '一'<=c<='鿿'
PUN=re.compile(r'^[，。！？、：；“”‘’（）()《》〈〉…—·\-~～＋+／/【】\[\]{},.!?;:\'"]+$')
def flat(s):
    return ''.join(c for c in unicodedata.normalize('NFD', (s or '').lower())
                   if not unicodedata.combining(c) and c.isalpha())
CED={}
for ln in open('database/cedict_full.txt', encoding='utf-8'):
    if ln.startswith('#'): continue
    try: sp=ln.split(' ',2)[1]; py=ln.split('[',1)[1].split(']',1)[0]
    except Exception: continue
    if len(sp)==1: CED.setdefault(sp,set()).add(re.sub(r'[0-9\s]','',py.lower()))
def readings(c):
    return {flat(x[0]) for x in _py(c, style=Style.TONE, heteronym=True)} | CED.get(c,set())

def align_match(chars, pys):
    out=['']*len(chars); ti=0; i=0
    norm=lambda t: re.sub(r'\s+','',str(t or ''))
    while i<len(chars):
        if HAN(chars[i]):
            while ti<len(pys) and PUN.match(pys[ti]): ti+=1
            out[i]=pys[ti] if ti<len(pys) else ''; ti+=1; i+=1
        else:
            j=i
            while j<len(chars) and not HAN(chars[j]): j+=1
            run=norm(''.join(chars[i:j]))
            if run:
                k=ti; eat=''
                while k<len(pys) and len(eat)<len(run) and run.startswith(eat+norm(pys[k])):
                    eat+=norm(pys[k]); k+=1
                if eat: ti=k
            i=j
    return out
def align_hanonly(chars, pys):
    toks=[t for t in pys if not PUN.match(t)]
    out=['']*len(chars); ti=0
    for i,c in enumerate(chars):
        if HAN(c): out[i]=toks[ti] if ti<len(toks) else ''; ti+=1
    return out
def score(chars, out):
    hit=seen=0
    for i,c in enumerate(chars):
        if not HAN(c): continue
        g=flat(out[i])
        if not g: continue
        seen+=1; hit += (g in readings(c))
    return hit/seen if seen else 0.0
def best(chars, pys):
    return max((align_match(chars,pys), align_hanonly(chars,pys)), key=lambda o: score(chars,o))

def make(text):
    """자료의 관행대로 다시 만든다 — 한자는 읽기, 부호는 그대로, 숫자·영문은 건너뛴다."""
    out=[]
    for seg in _py(text, style=Style.TONE, errors=lambda x: [[None]]*len(x)):
        pass
    chars=list(text)
    pys=_py(text, style=Style.TONE, errors=lambda x: ['\0']*len(x))
    k=0
    for ch in chars:
        if HAN(ch):
            v=pys[k][0] if k<len(pys) else ''; k+=1
            out.append(v)
        else:
            if k<len(pys) and pys[k][0]=='\0': k+=1
            if PUN.match(ch): out.append(ch)
    return ' '.join(t for t in out if t)

con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute('PRAGMA table_info(chinese_cards)')]
if 'pinyin_old' not in cols:
    con.execute('ALTER TABLE chinese_cards ADD COLUMN pinyin_old TEXT'); con.commit()
    print('pinyin_old 칸 추가')
DRY = '--apply' not in sys.argv
rows=con.execute('SELECT id,chinese,pinyin,week,class_num FROM chinese_cards').fetchall()
bad=[]; fixed=0; kept=0
for r in rows:
    ch=r['chinese'] or ''; py=(r['pinyin'] or '').split()
    if not ch or not py: continue
    chars=list(ch)
    if not any(HAN(c) for c in chars): continue
    s0=score(chars, best(chars,py))
    if s0 >= 0.92: continue
    # 「不 bù→bú(+4성)」 같은 변조 설명 카드는 일부러 그렇게 적은 것이니 두어야 한다
    old=' '.join(py)
    if '→' in old and len([c for c in chars if HAN(c)]) <= 4:
        kept+=1; continue
    bad.append(r)
    new=make(ch)
    s1=score(chars, best(chars,new.split()))
    if s1 > s0 + 0.02:
        if not DRY:
            con.execute('UPDATE chinese_cards SET pinyin_old=COALESCE(pinyin_old,pinyin), pinyin=? WHERE id=?',
                        (new, r['id']))
        fixed+=1
        if fixed<=6:
            print(f"  {r['week']}주{r['class_num']}교시 [{r['id']}]  {s0:.0%} → {s1:.0%}")
            print(f"    {ch[:34]}")
            print(f"    옛: {' '.join(py)[:70]}")
            print(f"    새: {new[:70]}")
    else:
        kept+=1
if not DRY: con.commit()
print(f"\n짝이 안 맞는 카드 {len(bad)}장 · 고칠 수 있는 것 {fixed}장 · 그대로 둘 것 {kept}장")
print('실제로 바꾸려면 --apply' if DRY else '바꿨습니다 (옛 값은 pinyin_old 에)')
