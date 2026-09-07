# -*- coding: utf-8 -*-
"""해석에서 숫자가 어긋난 곳을 찾는다.
   ⚠️ 十分(매우)·百货(백화점)처럼 숫자가 아닌 것을 세면 헛걸림투성이가 된다.
      그래서 **뒤에 단위가 붙은 수**만 본다 — 370多种·1963年·1月5日 같은 것.
   기계 번역이 숫자를 슬쩍 바꾸는 일이 있고, 독해에서는 그게 정답을 가른다."""
import json, re, sqlite3, os, sys
os.chdir('/app')
DIG={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
UNIT='年|月|日|号|种|岁|个|多|余|次|倍|世纪|米|公里|元|块|人|名|分钟|小时|天|周|%|％'
def cn2int(s):
    if s.isdigit(): return int(s)
    v=0; cur=0
    for c in s:
        if c in DIG: cur=DIG[c]
        elif c=='十': v+= (cur or 1)*10; cur=0
        elif c=='百': v+= (cur or 1)*100; cur=0
        elif c=='千': v+= (cur or 1)*1000; cur=0
        elif c=='万': v=(v+cur)*10000; cur=0
        else: return None
    return v+cur
NUM=re.compile(r'(\d+|[零〇一二两三四五六七八九十百千万]+)\s*(?:' + UNIT + r')')
KNUM=re.compile(r'(\d+)\s*(?:년|월|일|종|살|개|여|번|배|세기|미터|킬로|원|명|사람|분|시간|일|주|%|퍼센트)')
def cn_nums(t):
    out=set()
    for m in NUM.finditer(t or ''):
        v=cn2int(m.group(1))
        if v is not None and v>=2: out.add(v)
    return out
def ko_nums(t):
    return {int(m.group(1)) for m in KNUM.finditer(t or '') if int(m.group(1))>=2}
def check(cn, ko, where, rows):
    a, b = cn_nums(cn), ko_nums(ko)
    if not a: return
    miss=a-b                                   # 원문에 있는데 해석에 없는 수
    extra={x for x in b-a if x>=10}            # 해석에만 있는 큰 수 (바꿔치기 낌새)
    if miss or extra:
        rows.append((where, sorted(miss), sorted(extra), (cn or '')[:44], (ko or '')[:70]))
con=sqlite3.connect('database/voices.db'); con.row_factory=sqlite3.Row
rows=[]
for r in con.execute('SELECT id,week,page,passage,meaning_ko,options,options_ko FROM reading_items'):
    check(r['passage'], r['meaning_ko'] or '', f"독해 {r['week']}주 {r['page']}쪽 지문", rows)
    ops=json.loads(r['options']); ok=json.loads(r['options_ko'] or '[]')
    for j,(o,k) in enumerate(zip(ops, ok)):
        check(o, k, f"독해 {r['week']}주 {r['page']}쪽 {'ABCD'[j]}", rows)
for r in con.execute("""SELECT chinese,meaning_ko,subject,week,class_num FROM chinese_cards
                        WHERE length(chinese)>=10 AND COALESCE(hidden,0)=0"""):
    check(r['chinese'], r['meaning_ko'] or '', f"{r['subject']} {r['week']}주{r['class_num']}교시", rows)
print(f"숫자가 어긋나 보이는 곳 {len(rows)}군데\n")
for w,a,b,cn,ko in rows:
    tag='바뀜' if b else '빠짐'
    print(f"  [{tag}] {w}  원문 {a}" + (f" · 해석에만 {b}" if b else ""))
    print(f"     中 {cn}")
    print(f"     韓 {ko}")
