# -*- coding: utf-8 -*-
"""XTTS 는 pypinyin 방식으로 한자를 읽는다. 사전의 올바른 독음과 어긋나는 단어는
   음성도 잘못 났을 가능성이 높다. 그 목록을 뽑는다."""
import os, re, sqlite3, json
os.chdir('/app')
from pypinyin import pinyin as py, Style
ced={}
for line in open('database/cedict_full.txt',encoding='utf-8',errors='ignore'):
    if line.startswith('#'): continue
    m=re.match(r'^(\S+) (\S+) \[([^\]]+)\]', line)
    if m: ced.setdefault(m.group(2), set()).add(m.group(3).lower().replace(' ',''))
def flat(t):
    t=re.sub(r'[^a-z0-9:]','',(t or '').lower())
    return t.replace('u:','v').replace('r5','')
def bare(t): return re.sub(r'\d','',flat(t))
con=sqlite3.connect('file:database/voices.db?mode=ro',uri=True); con.row_factory=sqlite3.Row
rows=con.execute("""SELECT chinese,tones,meaning_ko,hsk,audio1,audio2 FROM words
                    WHERE COALESCE(excluded,0)=0 AND length(chinese) BETWEEN 1 AND 4""").fetchall()
con.close()
out=[]
for r in rows:
    w=r['chinese']
    if not all('一'<=c<='鿿' for c in w): continue
    cd=ced.get(w)
    if not cd: continue
    guess=''.join(x[0] for x in py(w, style=Style.TONE3, heteronym=False))
    g=bare(guess)
    if any(bare(x)==g for x in cd): continue          # pypinyin 이 사전 독음 중 하나와 맞으면 통과
    out.append({'chinese':w,'pypinyin':guess,'cedict':sorted(cd)[:2],
                'db':r['tones'],'ko':r['meaning_ko'],'hsk':r['hsk']})
print(f"pypinyin 이 사전과 다르게 읽는 단어 {len(out)}개  ← 음성이 잘못 났을 수 있다\n")
for o in out:
    print(f"   {o['chinese']:<6}pypinyin={o['pypinyin']:<16}사전={'/'.join(o['cedict']):<18}HSK{o['hsk'] or '-'} {(o['ko'] or '')[:22]}")
json.dump(out, open('finetune_data/_wordapp/g2p_bad.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
