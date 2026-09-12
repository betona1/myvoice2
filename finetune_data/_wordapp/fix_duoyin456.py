# -*- coding: utf-8 -*-
"""HSK 4~6급 단일 글자 병음을 pypinyin 상용독음과 대조. 뜻과 어긋난 다음자를 잡는다."""
import os,sqlite3,sys
os.chdir('/app')
from pypinyin import pinyin as _py, Style
APPLY='--apply' in sys.argv
# 뜻이 비상용 독음을 요구하는 예외 (건드리지 않음)
KEEP={'系':'jì','和':'hé','都':'dōu','长':'cháng','行':'xíng','重':'zhòng','空':'kōng',
      '着':'zhe','了':'le','的':'de','地':'de','得':'de','差':'chà','조':'x'}
con=sqlite3.connect('database/voices.db',timeout=60); con.execute('PRAGMA busy_timeout=60000')
rows=con.execute("SELECT id,chinese,pinyin,meaning_ko FROM words WHERE hsk BETWEEN 4 AND 6 AND length(chinese)=1").fetchall()
mis=[]
for wid,ch,py,ko in rows:
    want=_py(ch,style=Style.TONE)[0][0]
    if (py or '').strip().lower()!=want.lower() and ch not in KEEP:
        mis.append((wid,ch,py,want,ko))
print(f"상용독음 불일치 {len(mis)}개 / 단일글자 {len(rows)}개")
for _,ch,o,w,ko in mis: print(f"   {ch}  {o} → {w}   ({ko[:22]})")
if APPLY:
    for wid,ch,o,w,ko in mis:
        con.execute("UPDATE words SET pinyin=?,tones=? WHERE id=?",
                    (w,' '.join(p[0] for p in _py(ch,style=Style.TONE3)),wid))
    con.commit(); print(f"\n적용 {len(mis)}개")
con.close()
