# -*- coding: utf-8 -*-
import os, re, sqlite3, sys
os.chdir('/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from curated456 import CUR
from pypinyin import pinyin as _py, Style
DB='database/voices.db'; CIR='①②③④⑤⑥⑦⑧'
con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
nm=np=miss=0; changed=[]
for ch,py,ko in CUR:
    r=con.execute("SELECT id,pinyin,meaning_ko,senses_ko FROM words WHERE chinese=? AND hsk BETWEEN 4 AND 6",(ch,)).fetchall()
    if not r: miss+=1; continue
    for wid,opy,oko,sen in r:
        if (opy or '').strip()!=py:
            con.execute("UPDATE words SET pinyin=?,tones=? WHERE id=?",
                        (py,' '.join(p[0] for p in _py(ch,style=Style.TONE3)),wid)); np+=1
        if (oko or '').strip()!=ko:
            parts=[p.strip() for p in re.split(r'[①②③④⑤⑥⑦⑧]\s*',sen or '') if p.strip()]
            parts=[p for p in parts if p!=ko and p!=(oko or '').strip()]
            parts=[ko]+([oko.strip()] if oko and oko.strip() else [])+parts
            ns=' '.join(f'{CIR[j]} {p}' for j,p in enumerate(parts[:6])) if len(parts)>1 else ko
            con.execute("UPDATE words SET meaning_ko=?,senses_ko=? WHERE id=?",(ko,ns,wid)); nm+=1
            changed.append(f"   {ch} {py}  '{str(oko)[:20]}' → '{ko}'")
con.commit(); con.close()
print(f"큐레이션 적용: 뜻 {nm}개 / 병음 {np}개 / DB에 없음 {miss}개")
for c in changed[:40]: print(c)
