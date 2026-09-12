# -*- coding: utf-8 -*-
"""중국어 카드(chinese_cards) 음성을 새 목소리로 만든다.
   쓰임: make_cards.py <성별> <모델> [묶음번호 묶음크기]
   ⚠️ 병음 낱글자 카드(b·p·m·f 등 한자 없음)는 건드리지 않는다 — 발음 연습용 소리다.
   길이로 갈라 만든다: 1~6자 낱말 · 7~60자 문장 · 60자 넘으면 문장부호로 쪼개 이어 붙임."""
import os, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf, tts.zh_tts as z

sex, model = sys.argv[1], sys.argv[2]
bno = int(sys.argv[3]) if len(sys.argv) > 3 else 0
bsz = int(sys.argv[4]) if len(sys.argv) > 4 else 100000
who = model.split(':')[-1]
TRIES = int(os.environ.get('TRIES', '8'))
OUT = 'outputs/chinese/new_card'; os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
con.execute("""CREATE TABLE IF NOT EXISTS card_audio_new (
  card_id INTEGER NOT NULL, sex TEXT NOT NULL, path TEXT, model TEXT, dur REAL, report TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(card_id, sex))""")
con.commit()

rows = [r for r in con.execute("""SELECT id, chinese FROM chinese_cards
                                  WHERE COALESCE(hidden,0)=0 ORDER BY id""")
        if z._HAN.search(r['chinese'] or '')]
rows = rows[bno * bsz:(bno + 1) * bsz]
done = {r['card_id'] for r in con.execute(
    "SELECT card_id FROM card_audio_new WHERE sex=? AND path IS NOT NULL", (sex,))}
todo = [r for r in rows if r['id'] not in done]
print(f'{sex} {model} 묶음{bno}: {len(todo)}/{len(rows)}개', flush=True)

for r in todo:
    w, rep = z.say_card(model, r['chinese'], tries=TRIES, ref_dir=f'voice_samples/{who}')
    if w is None:
        con.execute("""INSERT INTO card_audio_new(card_id,sex,path,model,report) VALUES(?,?,NULL,?,?)
                       ON CONFLICT(card_id,sex) DO UPDATE SET model=excluded.model, report=excluded.report""",
                    (r['id'], sex, model, (rep or '')[-300:]))
        con.commit(); print(f'  ✗ {r["chinese"][:16]}  {(rep or "")[-70:]}', flush=True); continue
    p = f"{OUT}/{r['id']}_{sex}.wav"; sf.write(p, w, 24000)
    con.execute("""INSERT INTO card_audio_new(card_id,sex,path,model,dur,report,created_at)
                   VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(card_id,sex) DO UPDATE SET
                   path=excluded.path, model=excluded.model, dur=excluded.dur,
                   report=excluded.report, created_at=CURRENT_TIMESTAMP""",
                (r['id'], sex, p, model, round(len(w) / 24000, 2), (rep or '')[-300:]))
    con.commit(); print(f'  ✓ {r["chinese"][:16]} {len(w)/24000:.2f}s', flush=True)
con.close()
