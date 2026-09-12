# -*- coding: utf-8 -*-
import os,re,sys,sqlite3
os.chdir('/app')
off=int(sys.argv[1]); lim=int(sys.argv[2])
c=sqlite3.connect('file:database/voices.db?mode=ro',uri=True)
rows=c.execute("""SELECT id,hsk,chinese,pinyin,meaning_ko,meaning_en FROM words
                  WHERE hsk BETWEEN 4 AND 6 ORDER BY hsk,freq DESC,id LIMIT ? OFFSET ?""",(lim,off)).fetchall()
for wid,lv,ch,py,ko,en in rows:
    en=re.sub(r'\s+',' ',(en or '')).strip()[:78]
    print(f"{wid}\t{lv}\t{ch}\t{py}\t{ko}\t{en}")
