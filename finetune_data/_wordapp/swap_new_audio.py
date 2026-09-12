# -*- coding: utf-8 -*-
"""새로 만든 낱말 음성을 앱이 쓰는 자리(words.audio1/audio2)로 바꾼다.
   ⚠️ 되돌릴 수 있게 지금 경로를 audio1_v1/audio2_v1 에 보관한다 (한 번만 채운다).
   쓰임: swap_new_audio.py [apply|revert|status]"""
import os, sys, sqlite3
os.chdir('/app')
mode = sys.argv[1] if len(sys.argv) > 1 else 'status'
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
cols = {r[1] for r in con.execute('PRAGMA table_info(words)')}
for c in ('audio1_v1', 'audio2_v1'):
    if c not in cols: con.execute(f'ALTER TABLE words ADD COLUMN {c} TEXT')
con.commit()

def status():
    n1 = con.execute("SELECT COUNT(*) FROM words WHERE audio1_v1 IS NOT NULL").fetchone()[0]
    sw = con.execute("SELECT COUNT(*) FROM words WHERE audio1 LIKE 'outputs/chinese/new/%'").fetchone()[0]
    sw2 = con.execute("SELECT COUNT(*) FROM words WHERE audio2 LIKE 'outputs/chinese/new/%'").fetchone()[0]
    av = con.execute("SELECT sex, COUNT(*) FROM word_audio_new WHERE path IS NOT NULL GROUP BY sex").fetchall()
    print(f'보관된 옛 경로 {n1}개 · 새 음성으로 바뀐 것 남 {sw} · 여 {sw2} · 만들어진 새 음성 {[tuple(r) for r in av]}')

if mode == 'apply':
    rows = con.execute("""SELECT n.word_id, n.sex, n.path FROM word_audio_new n
                          WHERE n.path IS NOT NULL""").fetchall()
    done = miss = 0
    for r in rows:
        if not os.path.exists(r['path']): miss += 1; continue
        col = 'audio1' if r['sex'] == '남' else 'audio2'
        con.execute(f"UPDATE words SET {col}_v1 = COALESCE({col}_v1, {col}), {col} = ? WHERE id = ?",
                    (r['path'], r['word_id']))
        done += 1
    con.commit(); print(f'바꿈 {done}개 · 파일 없어 건너뜀 {miss}개'); status()
elif mode == 'revert':
    con.execute("UPDATE words SET audio1 = audio1_v1 WHERE audio1_v1 IS NOT NULL")
    con.execute("UPDATE words SET audio2 = audio2_v1 WHERE audio2_v1 IS NOT NULL")
    con.commit(); print('되돌림'); status()
else:
    status()
con.close()
