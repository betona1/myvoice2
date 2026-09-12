# -*- coding: utf-8 -*-
"""평가에서 「나쁨」이거나 「보통+코멘트」인 낱말을 강화된 검사로 다시 만든다.
   쓰임: regen_rated.py <성별> <모델>   — 학습 모델 먼저, 남은 것은 원본으로 (부르는 쪽이 두 번)
   다시 만든 낱말은 점수를 비워 「미평가」에 다시 뜨게 하고 코멘트는 남긴다."""
import os, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import soundfile as sf, tts.zh_tts as z
sex, model = sys.argv[1], sys.argv[2]; who = model.split(':')[-1]
TRIES = int(os.environ.get('TRIES', '10'))
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
rows = con.execute("""SELECT w.id, w.chinese, w.audio_say, w.wordset, e.comment FROM voice_eval e JOIN words w ON w.id=e.word_id
                      WHERE e.sex=? AND (e.rating=1 OR (e.rating=2 AND COALESCE(e.comment,'')<>''))
                        AND NOT EXISTS (SELECT 1 FROM word_audio_new n WHERE n.word_id=w.id AND n.sex=e.sex AND n.model=? AND n.created_at > e.updated_at)
                      ORDER BY e.rating, w.id""", (sex, model)).fetchall()
print(f'{sex} {model}: 다시 만들 {len(rows)}개', flush=True)
for r in rows:
    ch = r['chinese']; texts = [ch]
    if model.startswith('원본') and len(ch) == 1:
        c = (r['audio_say'] or '').strip()
        if c and c != ch: texts.append(c)
        elif r['wordset'] == '양사': texts.append('一' + ch)
    got = None
    for t in texts:
        w, rep = z.say_word(model, t, ref_dir=f'voice_samples/{who}', tries=TRIES)
        if w is not None: got = (t, w, rep); break
    if got is None:
        print(f'  ✗ {ch}  {rep[-90:]}', flush=True); continue
    t, w, rep = got
    p = f"outputs/chinese/new/{r['id']}_{sex}.wav"; sf.write(p, w, 24000)
    con.execute("""INSERT INTO word_audio_new(word_id,sex,path,model,spoken,dur,report,created_at)
                   VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(word_id,sex) DO UPDATE SET path=excluded.path,
                   model=excluded.model, spoken=excluded.spoken, dur=excluded.dur, report=excluded.report, created_at=CURRENT_TIMESTAMP""",
                (r['id'], sex, p, model, t, round(len(w)/24000, 2), rep[-300:]))
    con.execute("UPDATE voice_eval SET rating=NULL WHERE word_id=? AND sex=?", (r['id'], sex))
    con.commit(); print(f'  ✓ {ch}{"" if t == ch else " ←" + t} {len(w)/24000:.2f}s', flush=True)
con.close()
