# -*- coding: utf-8 -*-
"""지정한 낱말을 새 자르기로 다시 만들어 DB 에 넣고, 그 낱말의 평가는 비워 다시 듣게 한다 (코멘트는 남김).
   쓰임: regen_words.py <성별> <모델> 낱말1 낱말2 …"""
import os, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import soundfile as sf, tts.zh_tts as z
sex, model = sys.argv[1], sys.argv[2]; words = sys.argv[3:]; who = model.split(':')[-1]
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
for ch in words:
    r = con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0", (ch,)).fetchone()
    if not r: print(f'  {ch}: 없음'); continue
    w, rep = z.say_word(model, ch, ref_dir=f'voice_samples/{who}', tries=6)
    if w is None: print(f'  ✗ {ch}  {rep[-100:]}', flush=True); continue
    p = f"outputs/chinese/new/{r['id']}_{sex}.wav"; sf.write(p, w, 24000)
    con.execute("""INSERT INTO word_audio_new(word_id,sex,path,model,spoken,dur,report)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(word_id,sex) DO UPDATE SET path=excluded.path,
                   model=excluded.model, spoken=excluded.spoken, dur=excluded.dur, report=excluded.report,
                   created_at=CURRENT_TIMESTAMP""", (r['id'], sex, p, model, ch, round(len(w)/24000, 2), rep[-300:]))
    con.execute("UPDATE voice_eval SET rating=NULL WHERE word_id=? AND sex=?", (r['id'], sex))
    con.commit(); print(f'  ✓ {ch} {len(w)/24000:.2f}s', flush=True)
