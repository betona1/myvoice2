# -*- coding: utf-8 -*-
"""검색용 병음 키 생성.
   py_plain : 무성조·무공백 (wo3 men → women, nv3 → nu)   ← 'hao' 로 'hǎo' 를 찾기 위함
   py_num   : 성조숫자·무공백 (wo3 men → wo3men)          ← 'hao3' 처럼 성조까지 지정할 때
   ü 는 tones 에 v 로 들어있다. 사용자는 u/v/ü 아무거나 치므로 u 로 통일한다."""
import os, re, sqlite3
os.chdir('/app')
con = sqlite3.connect('database/voices.db', timeout=60); con.execute('PRAGMA busy_timeout=60000')
cols = [r[1] for r in con.execute('PRAGMA table_info(words)')]
for col in ('py_plain', 'py_num'):
    if col not in cols:
        con.execute(f'ALTER TABLE words ADD COLUMN {col} TEXT'); print(f'{col} 컬럼 추가')
con.commit()

def keys(tones: str):
    t = (tones or '').lower()
    t = re.sub(r"[\s'\-·]", '', t)          # 공백·어깻점 제거
    num = re.sub(r'[^a-z0-9]', '', t)       # 성조숫자 유지
    plain = re.sub(r'\d', '', num).replace('v', 'u')
    return plain, num

n = 0
for wid, tones in con.execute('SELECT id,tones FROM words').fetchall():
    p, q = keys(tones)
    con.execute('UPDATE words SET py_plain=?, py_num=? WHERE id=?', (p, q, wid)); n += 1
con.execute('CREATE INDEX IF NOT EXISTS idx_words_pyplain ON words(py_plain)')
con.execute('CREATE INDEX IF NOT EXISTS idx_words_pynum   ON words(py_num)')
con.execute('CREATE INDEX IF NOT EXISTS idx_words_chinese ON words(chinese)')
con.commit()
print(f'검색키 생성 {n}건')
for r in con.execute("SELECT chinese,pinyin,py_plain,py_num FROM words WHERE chinese IN ('我们','女朋友','红绿灯','好','汉语') "):
    print('  ', r)
con.close()
