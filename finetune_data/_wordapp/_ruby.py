"""화면이 쓰는 짝짓기(alignPinyin)를 그대로 옮겨, 글자마다 붙은 병음이 맞는지 본다.
   맞는지는 pypinyin 의 그 글자 읽기(다음자면 여러 개) 안에 드는지로 가린다."""
import sqlite3, re, sys
sys.path.insert(0,'/app')
from pypinyin import pinyin as _py, Style
HAN=lambda c: '一'<=c<='鿿'
def strip_tone(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', s.lower())
                   if not unicodedata.combining(c) and c.isalpha())
PUN=re.compile(r'^[，。！？、：；“”‘’（）()《》〈〉…—·\-~～＋+／/【】\[\]{},.!?;:\'"]+$')
def align_hanonly(chars, pys):
    toks=[t for t in pys if not PUN.match(t)]
    out=['']*len(chars); ti=0
    for i,c in enumerate(chars):
        if HAN(c):
            out[i]=toks[ti] if ti<len(toks) else ''; ti+=1
    return out
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
                k=ti; eaten=''
                while k<len(pys) and len(eaten)<len(run) and run.startswith(eaten+norm(pys[k])):
                    eaten+=norm(pys[k]); k+=1
                if eaten: ti=k
            i=j
    return out
def align(chars, pys):
    out=['']*len(chars); ti=0; i=0
    while i < len(chars):
        if HAN(chars[i]):
            out[i]=pys[ti] if ti < len(pys) else ''; ti+=1; i+=1
        else:
            j=i; vis=False
            while j < len(chars) and not HAN(chars[j]):
                if not chars[j].isspace(): vis=True
                j+=1
            if vis: ti+=1
            i=j
    return out
# 다음자를 놓치지 않도록 사전 읽기를 CC-CEDICT 까지 넓힌다
#   (pypinyin 만 보면 乐 을 lè 로만 알아 音乐 의 yuè 를 틀렸다고 센다)
CEDICT={}
for ln in open('/app/database/cedict_full.txt', encoding='utf-8'):
    if ln.startswith('#'): continue
    try:
        sp=ln.split(' ',2)[1]; py=ln.split('[',1)[1].split(']',1)[0]
    except Exception: continue
    if len(sp)!=1: continue
    CEDICT.setdefault(sp,set()).add(re.sub(r'[0-9\s]','',py.lower()))
def readings(c):
    r={strip_tone(x[0]) for x in _py(c, style=Style.TONE, heteronym=True)}
    return r | CEDICT.get(c,set())

con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
W=int(sys.argv[1]) if len(sys.argv)>1 else 0
C=int(sys.argv[2]) if len(sys.argv)>2 else 0
q='SELECT id,chinese,pinyin,week,class_num FROM chinese_cards'
a=[]
if W: q+=' WHERE week=? AND class_num=?'; a=[W,C]
tot=badc=0; cards=set(); ex=[]
for r in con.execute(q,a):
    ch=r['chinese'] or ''; py=(r['pinyin'] or '').split()
    if not ch or not py: continue
    chars=list(ch)
    A=align(chars,py); B=align_hanonly(chars,py); M=align_match(chars,py)
    def score(o):
        hit=seen=0
        for i,c in enumerate(chars):
            if not HAN(c): continue
            want=readings(c)
            g=strip_tone(o[i] or '')
            if g: seen+=1; hit += (g in want)
        return hit/seen if seen else 0
    got=max((M,B,A), key=score)
    for i,c in enumerate(chars):
        if not HAN(c): continue
        tot+=1
        want=readings(c)
        g=strip_tone(got[i] or '')
        if g and g not in want:
            badc+=1; cards.add(r['id'])
            if len(ex)<12: ex.append((r['week'],r['class_num'],ch[:22],c,got[i],sorted(want)[:3]))
print(f'검사한 글자 {tot}개 · 어긋난 글자 {badc}개 ({badc/max(1,tot)*100:.1f}%) · 카드 {len(cards)}장')
for w,cn,s,c,g,want in ex:
    print(f'  {w}주{cn}교시  {s}')
    print(f'     {c} 에 붙은 병음 「{g}」 · 사전 읽기 {want}')
