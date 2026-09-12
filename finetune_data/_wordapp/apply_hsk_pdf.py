# -*- coding: utf-8 -*-
"""교수님 HSK 4·5급 단어 PDF 를 DB 에 반영한다.
   ① hsk_course 열 — 이 과목에서 4급/5급으로 가르치는 단어 표시 (기존 hsk 는 건드리지 않는다)
   ② 빠진 단어 추가 (병음·뜻은 PDF 값을 쓴다)"""
import os, re, json, sqlite3
os.chdir('/app')
from pypinyin import pinyin as _py, Style
d = json.load(open('finetune_data/_hsk_media/hsk_pdf_words.json', encoding='utf-8'))
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
cols = [r[1] for r in con.execute('PRAGMA table_info(words)')]
if 'hsk_course' not in cols:
    con.execute('ALTER TABLE words ADD COLUMN hsk_course INTEGER')
    print('hsk_course 열 추가')
con.execute('UPDATE words SET hsk_course=NULL')

def clean_ko(s, lv):
    s = re.sub(r'\s+', ' ', s or '').strip()
    if lv == '4':                      # [명]북방/북쪽  →  북방/북쪽 (명)
        m = re.match(r'\[([^\]]+)\]\s*(.*)', s)
        if m: s = f"{m.group(2).strip()} ({m.group(1)})"
    else:                              # 5급은 문장형 — 마침표를 다듬는다
        s = re.sub(r'\s*\.\s*$', '', s).replace('. ', ', ')
    return s.strip(' ,./')

seq0 = con.execute("SELECT COALESCE(MAX(seq),0) FROM words").fetchone()[0]
added, marked = [], 0
for lv in ('4', '5'):
    for ch, v in d[lv].items():
        r = con.execute("SELECT id,excluded FROM words WHERE chinese=?", (ch,)).fetchone()
        ko = clean_ko(v['ko'], lv)
        if r:
            con.execute("UPDATE words SET hsk_course=? WHERE id=?", (int(lv), r['id']))
            marked += 1
            if r['excluded']:          # 교재에 실린 단어면 학습에서 빼지 않는다
                con.execute("UPDATE words SET excluded=0 WHERE id=?", (r['id'],))
            continue
        seq0 += 1
        tones = ' '.join(x[0] for x in _py(ch, style=Style.TONE3))
        pin   = ' '.join(x[0] for x in _py(ch, style=Style.TONE))
        con.execute("""INSERT INTO words(chinese,pinyin,tones,meaning_ko,senses_ko,hsk,hsk_course,
                                         category,subject,seq,level,difficulty,excluded)
                       VALUES(?,?,?,?,?,?,?,'단어','HSK어휘',?,?,?,0)""",
                    (ch, pin, tones, ko, ko, int(lv), int(lv), seq0, int(lv), float(lv)*10))
        added.append((lv, ch, pin, ko))
con.commit()
print(f"교재 급수 표시 {marked}개 · 새로 넣은 단어 {len(added)}개")
for lv, ch, pin, ko in added: print(f"   {lv}급 {ch:<6}{pin:<16}{ko[:34]}")
for lv in (4, 5):
    n = con.execute("SELECT COUNT(*) FROM words WHERE hsk_course=? AND COALESCE(excluded,0)=0", (lv,)).fetchone()[0]
    print(f"\nHSK{lv}급 학습 대상 {n}개")
con.close()
