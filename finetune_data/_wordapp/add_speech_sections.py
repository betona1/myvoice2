# -*- coding: utf-8 -*-
"""성공하는스피치커뮤니케이션 교안을 주차·교시로 나눠 textbook_sections.json 에 넣는다.
   이 교안은 짜임이 곧아서, 「주표지 → 그다음 쪽부터 그 교시」 로 끊으면 된다.
   ⚠️ 교시 범위를 '그 교시를 언급한 쪽의 min/max' 로 잡으면 안 된다 —
      주표지에 다음 차시 예고가 함께 적혀 있어 범위가 뒤엉킨다."""
import json, re, subprocess, sys
from pathlib import Path
PDF='temp/speech/S03754-01-04.pdf'
SUBJ='성공하는스피치커뮤니케이션'
n=int(subprocess.run(['pdfinfo',PDF],capture_output=True,text=True).stdout
      .split('Pages:')[1].split()[0])
starts=[]
for p in range(1,n+1):
    t=subprocess.run(['pdftotext','-layout','-f',str(p),'-l',str(p),PDF,'-'],
                     capture_output=True,text=True).stdout
    head=' '.join(t.split())
    if '학습목표' not in t: continue
    m=re.search(r'(\d)\s*교시\s*[.．]\s*([^\n▪]{2,60}?)\s*학습목표', head)
    if not m: continue
    cls=int(m.group(1)); title=m.group(2).strip()
    # 주차는 바로 앞 주표지에서 읽는다
    prev=subprocess.run(['pdftotext','-layout','-f',str(p-1),'-l',str(p-1),PDF,'-'],
                        capture_output=True,text=True).stdout if p>1 else ''
    wm=re.search(r'(\d+)\s*주[.．]', ' '.join(prev.split()))
    starts.append({'page0': p-2, 'week': int(wm.group(1)) if wm else 0,
                   'class_num': cls, 'title': title})
# 끝쪽 = 다음 시작의 주표지 앞까지
secs=[]
for i,s in enumerate(starts):
    end = (starts[i+1]['page0'] - 1) if i+1 < len(starts) else (n-1)
    secs.append({'subject': SUBJ, 'pdf': PDF, 'week': s['week'],
                 'class_num': s['class_num'], 'title': s['title'],
                 'start_page': s['page0'], 'end_page': end})
f=Path('temp/textbook_sections.json')
all_=json.load(open(f,encoding='utf-8'))
before=len(all_)
all_=[x for x in all_ if x['subject']!=SUBJ]
all_+=secs
json.dump(all_, open(f,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"{SUBJ} 구간 {len(secs)}개 넣음 (전체 {before} → {len(all_)})")
for s in secs:
    print(f"  {s['week']}주 {s['class_num']}교시  {s['start_page']+1}~{s['end_page']+1}쪽  {s['title'][:34]}")
