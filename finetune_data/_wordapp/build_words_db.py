# -*- coding: utf-8 -*-
"""단어 학습 시스템 DB 구축.
chinese_cards에 흩어진 단어를 words 마스터로 모으고 난이도/학습순서를 계산한다.
난이도 = 교재레벨*10 + 글자수*2 - log(빈도)*3  (낮을수록 쉬움)"""
import os, re, math, sqlite3
os.chdir('/app')
DB='database/voices.db'
JP={'기초여행일본어','초급일본어문형연습','50음도'}
LV={'기초중국어':1,'중국어발음연습':1,'발음연습':1,'기초중국어2':2,'생활중국어':3,
    '중국어단어장':3,'중국인의생활과문화':4,'신HSK쓰기독해':5}

con=sqlite3.connect(DB, timeout=60); con.execute('PRAGMA busy_timeout=60000')
cur=con.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS words (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chinese TEXT UNIQUE NOT NULL,
  pinyin TEXT DEFAULT '', tones TEXT DEFAULT '',
  meaning_ko TEXT DEFAULT '', meaning_en TEXT DEFAULT '',
  audio1 TEXT DEFAULT '', audio2 TEXT DEFAULT '',
  level INTEGER DEFAULT 3, freq INTEGER DEFAULT 0,
  difficulty REAL DEFAULT 0, seq INTEGER DEFAULT 0,
  subject TEXT DEFAULT '', created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_words_seq ON words(seq);
CREATE INDEX IF NOT EXISTS idx_words_level ON words(level);
CREATE TABLE IF NOT EXISTS word_progress (
  user_id INTEGER NOT NULL, word_id INTEGER NOT NULL,
  box INTEGER DEFAULT 0, due_at TEXT, ease REAL DEFAULT 2.5, interval_days REAL DEFAULT 0,
  seen INTEGER DEFAULT 0, correct INTEGER DEFAULT 0, wrong INTEGER DEFAULT 0,
  streak INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0,
  starred INTEGER DEFAULT 0, last_at TEXT,
  PRIMARY KEY(user_id, word_id)
);
CREATE INDEX IF NOT EXISTS idx_wp_due ON word_progress(user_id, due_at);
CREATE TABLE IF NOT EXISTS study_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, word_id INTEGER, mode TEXT, result INTEGER,
  ms INTEGER DEFAULT 0, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sl_user ON study_log(user_id, created_at);
""")
con.commit()

rows=cur.execute("""SELECT chinese,pinyin,tones,meaning_ko,meaning_en,subject,
                    ref_audio_path,ref_audio_path_2 FROM chinese_cards""").fetchall()
corpus=''.join((r[0] or '') for r in rows if r[5] not in JP)

best={}
for ch,py,tn,ko,en,subj,a1,a2 in rows:
    if subj in JP or not ch or not re.fullmatch('[一-鿿]{1,6}', ch): continue
    lv=LV.get(subj,3)
    score=(lv, 0 if (ko and py and a1 and a2) else 1)
    if ch not in best or score < best[ch][0]:
        best[ch]=(score, py or '', tn or '', (ko or '').strip(), (en or '').strip(),
                  a1 or '', a2 or '', lv, subj)

items=[]
for ch,(sc,py,tn,ko,en,a1,a2,lv,subj) in best.items():
    f=corpus.count(ch)
    d=lv*10 + len(ch)*2 - math.log1p(f)*3
    items.append([ch,py,tn,ko,en,a1,a2,lv,f,round(d,3),subj])
items.sort(key=lambda x:(x[9], -x[8], x[0]))
for i,it in enumerate(items,1): it.append(i)

cur.execute("DELETE FROM words")
cur.executemany("""INSERT INTO words
 (chinese,pinyin,tones,meaning_ko,meaning_en,audio1,audio2,level,freq,difficulty,subject,seq,created_at)
 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""", items)
con.commit()

n=cur.execute("SELECT COUNT(*) FROM words").fetchone()[0]
full=cur.execute("SELECT COUNT(*) FROM words WHERE pinyin!='' AND meaning_ko!='' AND audio1!='' AND audio2!=''").fetchone()[0]
print(f"  words {n}개 / 완비 {full}개")
print("  레벨별:", dict(cur.execute("SELECT level,COUNT(*) FROM words GROUP BY level").fetchall()))
print()
print("  쉬운 순서 1~15:")
for r in cur.execute("SELECT seq,chinese,pinyin,meaning_ko,level,freq FROM words ORDER BY seq LIMIT 15"):
    print(f"    {r[0]:>4} {r[1]:6s} {r[2]:14s} {str(r[3])[:14]:16s} lv{r[4]} freq{r[5]}")
print("  어려운 쪽 5:")
for r in cur.execute("SELECT seq,chinese,pinyin,meaning_ko,level,freq FROM words ORDER BY seq DESC LIMIT 5"):
    print(f"    {r[0]:>4} {r[1]:6s} {r[2]:14s} {str(r[3])[:14]:16s} lv{r[4]} freq{r[5]}")
con.close()
