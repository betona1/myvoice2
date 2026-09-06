# -*- coding: utf-8 -*-
"""교안에서 「지문 + A~D 선택지」 독해 문제를 통째로 뽑는다.
   교안이 지문과 선택지를 다른 쪽에 나눠 실어, 카드로 들어올 때 서로 떨어져
   선택지만 있는 카드가 「문제가 없는」 꼴로 남았다.
   ⚠️ 교안에 정답이 적혀 있지 않다 — 정답 없이 문제만 붙인다."""
import json, re, subprocess, collections
PDF='temp/신HSK쓰기독해/S02926.pdf'
SUBJ='신HSK쓰기독해'
CJK=re.compile(r'[一-鿿]')
n=int(subprocess.run(['pdfinfo',PDF],capture_output=True,text=True).stdout.split('Pages:')[1].split()[0])
sec=[s for s in json.load(open('temp/textbook_sections.json',encoding='utf-8'))
     if s['subject']==SUBJ]
def where(p0):
    for s in sec:
        if s['start_page'] <= p0 <= s['end_page']: return s['week'], s['class_num']
    return 0,0
PAGE={}
def text(p):
    if p not in PAGE:
        PAGE[p]=subprocess.run(['pdftotext','-layout','-f',str(p),'-l',str(p),PDF,'-'],
                               capture_output=True,text=True).stdout
    return PAGE[p]
OPT=re.compile(r'^\s*([ABCD])\s+(.{2,60}?)\s*$')
NUM=re.compile(r'^\s*(\d{2})\s*[．.]\s*(.*)$')
SKIP=re.compile(r'서울디지털대학교|들어가기|학습하기|정리하기|학습개요|차시예고')
def opts(p):
    d={}
    for l in text(p).split('\n'):
        m=OPT.match(l.rstrip())
        if m and CJK.search(m.group(2)): d.setdefault(m.group(1), m.group(2).strip())
    return d if len(d)==4 else None
def passage(p):
    """번호로 시작하는 지문을 모은다 (선택지 줄과 머리글은 뺀다)."""
    lines=[l.rstrip() for l in text(p).split('\n')]
    buf=[]; on=False
    for l in lines:
        if SKIP.search(l): continue
        if OPT.match(l) and CJK.search(l): continue
        m=NUM.match(l)
        if m and CJK.search(m.group(2)):
            on=True; buf=[m.group(2).strip()]; num=m.group(1); continue
        if on:
            t=l.strip()
            if not t or re.fullmatch(r'[\d/\s]+', t): continue
            if CJK.search(t): buf.append(t)
    return (num, ''.join(buf)) if on and buf else None

out=[]; seen=set()
for p in range(1,n+1):
    o=opts(p)
    if not o: continue
    # 지문은 같은 쪽이나 앞 두 쪽에 있다
    ps=None
    for q in (p, p-1, p-2):
        if q<1: continue
        ps=passage(q)
        if ps and len(ps[1])>=25: break
        ps=None
    if not ps: continue
    key=tuple(o[k] for k in 'ABCD')
    if key in seen: continue
    seen.add(key)
    w,c=where(p-1)
    out.append({'subject':SUBJ,'week':w,'class_num':c,'page':p,
                'no':ps[0],'passage':ps[1],'options':[o[k] for k in 'ABCD']})
print(f"지문+선택지 문제 {len(out)}개")
print(collections.Counter((x['week'],x['class_num']) for x in out))
for x in out[:3]:
    print(f"\n  {x['week']}주{x['class_num']}교시 {x['page']}쪽 · {x['no']}번")
    print(f"    지문: {x['passage'][:70]}…")
    for k,v in zip('ABCD', x['options']): print(f"      {k} {v}")
json.dump(out, open('finetune_data/_wordapp/reading_items.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=1)
