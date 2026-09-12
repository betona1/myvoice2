# -*- coding: utf-8 -*-
"""숫자+양사 표현은 규칙으로 생성한다.
구글은 五分을 '5점', 二号를 'èr hào — 일' 처럼 엉뚱하게 옮긴다.
중국어 수사는 규칙이 명확하므로 직접 만드는 편이 정확하다."""
import os, re, sqlite3
os.chdir('/app')
DB='database/voices.db'

NUM={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
     '半':None}
UNIT={'点':'시','分':'분','秒':'초','号':'일','日':'일','月':'월','年':'년','岁':'살',
      '个':'개','次':'번','块':'위안','天':'일','口':'명','本':'권','张':'장','件':'벌',
      '杯':'잔','碗':'그릇','位':'분','名':'명','层':'층','遍':'번','件':'개','只':'마리',
      '条':'개','双':'켤레','件':'벌','班':'반','课':'과','页':'쪽','元':'위안','角':'자오'}
# 사람 단위는 '명'
UNIT['个人']='명'
UNIT['个月']='개월'
UNIT['个星期']='주'
UNIT['个小时']='시간'
UNIT['百']='백'
UNIT['千']='천'

def cn2num(s):
    """한자 수사 → 아라비아 숫자 (1~99)"""
    if not s: return None
    if s.isdigit(): return int(s)
    if '十' in s:
        a,_,b = s.partition('十')
        t = (NUM.get(a,1) if a else 1)*10
        if b: t += NUM.get(b,0) or 0
        return t
    if len(s)==1: return NUM.get(s)
    v=0
    for ch in s:
        n=NUM.get(ch)
        if n is None: return None
        v=v*10+n
    return v

PAT=re.compile(r'^([零〇一二三四五六七八九十两0-9]{1,3})(个月|个星期|个小时|点半|点|分钟|分|秒|号|日|月|年|岁|个人|个|次|块|天|口|本|张|件|杯|碗|位|名|层|遍|只|条|双|页|元|角|百|千)$')

con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,meaning_ko FROM words").fetchall()
upd=[]
for wid,ch,ko in rows:
    m=PAT.match(ch)
    if not m: continue
    n=cn2num(m.group(1)); u=m.group(2)
    if n is None: continue
    if u=='点半': new=f'{n}시 반'
    elif u=='分钟': new=f'{n}분간'
    else:
        k=UNIT.get(u)
        if not k: continue
        new=f'{n}{k}'
    if new!=(ko or '').strip(): upd.append((new,wid,ch,ko))
print(f"  규칙으로 교정: {len(upd)}개")
for new,wid,ch,ko in upd[:20]:
    print(f"   {ch:8s} {str(ko)[:22]:24s} → {new}")
if upd:
    con.executemany("UPDATE words SET meaning_ko=? WHERE id=?", [(a,b) for a,b,_,_ in upd])
    con.commit()
    print("  ✅ 반영 완료")
con.close()
