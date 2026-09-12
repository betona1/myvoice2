# -*- coding: utf-8 -*-
"""JSON {"id": "새 뜻"} 을 읽어 words.meaning_ko / senses_ko 갱신."""
import os,re,sys,json,sqlite3
os.chdir('/app')
CIR='①②③④⑤⑥⑦⑧'
d=json.load(open(sys.argv[1],encoding='utf-8'))
con=sqlite3.connect('database/voices.db',timeout=60); con.execute('PRAGMA busy_timeout=60000')
n=skip=0
for k,v in d.items():
    py=None
    if isinstance(v,list): ko,py = v[0].strip(), (v[1].strip() if len(v)>1 else None)
    else: ko=v.strip()
    r=con.execute("SELECT meaning_ko,senses_ko FROM words WHERE id=?",(int(k),)).fetchone()
    if not r: skip+=1; continue
    oko,sen=r
    if py:
        from pypinyin import pinyin as _py, Style
        ch=con.execute("SELECT chinese FROM words WHERE id=?",(int(k),)).fetchone()[0]
        con.execute("UPDATE words SET pinyin=?,tones=? WHERE id=?",
                    (py,' '.join(x[0] for x in _py(ch,style=Style.TONE3)),int(k)))
    if (oko or '').strip()==ko: skip+=1; continue
    p=[x.strip() for x in re.split(r'[①②③④⑤⑥⑦⑧]\s*',sen or '') if x.strip()]
    p=[ko]+[x for x in p if x!=ko and x!=(oko or '').strip()]
    ns=' '.join(f'{CIR[j]} {x}' for j,x in enumerate(p[:6])) if len(p)>1 else ko
    con.execute("UPDATE words SET meaning_ko=?,senses_ko=? WHERE id=?",(ko,ns,int(k))); n+=1
con.commit(); con.close()
print(f"적용 {n}개 / 무변경 {skip}개")
