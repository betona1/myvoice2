# -*- coding: utf-8 -*-
"""교안에서 「문장 순서 맞추기」 문제를 정답까지 뽑아낸다.
   4급 독해 2부분 꼴 —  A/B/C 세 도막을 보여 주고, 다음 줄에 정답 차례가 적혀 있다.
       A 可是一直都占线 / B 我给马经理打了好几次电话了 / C 也不知道他到底是怎么回事
       A          B  A  C          ← 이 줄이 정답
   카드에는 도막만 들어와 있어 「문제가 없다」고 보인다. 그래서 문제와 정답을 따로 담는다."""
import json, re, subprocess, sqlite3, sys
PDF='temp/신HSK쓰기독해/S02926.pdf'
SUBJ='신HSK쓰기독해'
CJK=re.compile(r'[一-鿿]')
n=int(subprocess.run(['pdfinfo',PDF],capture_output=True,text=True).stdout.split('Pages:')[1].split()[0])
sec=json.load(open('temp/textbook_sections.json',encoding='utf-8'))
sec=[s for s in sec if s['subject']==SUBJ]
def where(p0):
    for s in sec:
        if s['start_page'] <= p0 <= s['end_page']: return s['week'], s['class_num']
    return 0,0
FRAG=re.compile(r'^\s*([ABC])\s+(.{2,60}?)\s*$')
ANS =re.compile(r'^\s*([ABC])\s+([ABC])\s+([ABC])\s*$')
PAGE={}
def text(p):
    if p not in PAGE:
        PAGE[p]=subprocess.run(['pdftotext','-layout','-f',str(p),'-l',str(p),PDF,'-'],
                               capture_output=True,text=True).stdout
    return PAGE[p]
out=[]; used=set()
for p in range(1,n+1):
    frag={}
    for l in text(p).split('\n'):
        m=FRAG.match(l.rstrip())
        if m and CJK.search(m.group(2)): frag.setdefault(m.group(1), m.group(2).strip())
    if len(frag)!=3: continue
    key=tuple(frag[k] for k in 'ABC')
    if key in used: continue
    # 정답 줄은 같은 쪽이나 바로 다음 두 쪽에 있다 (문제와 정답을 나눠 실은 곳이 있다)
    ans=None
    for q in range(p, min(n, p+2)+1):
        for l in text(q).split('\n'):
            a=ANS.match(l.rstrip())
            if a and len(set(a.groups()))==3: ans=list(a.groups()); break
        if ans: break
    if ans:
        used.add(key)
        w,c = where(p-1)
        out.append({'subject':SUBJ,'week':w,'class_num':c,'page':p,
                    'frags':{k:frag[k] for k in 'ABC'},'answer':ans})
print(f"문장 순서 문제 {len(out)}개")
import collections
print(collections.Counter((o['week'],o['class_num']) for o in out))
for o in out[:4]:
    print(f"  {o['week']}주{o['class_num']}교시 {o['page']}쪽  정답 {' '.join(o['answer'])}")
    for k in 'ABC': print(f"     {k} {o['frags'][k]}")
json.dump(out, open('finetune_data/_wordapp/order_puzzles.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=1)
