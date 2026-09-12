# -*- coding: utf-8 -*-
"""앱의 모든 낱말을 새 방식(학습 모델 + 검사)으로 만든다.
   쓰임: gen_all_new.py <성별> <모델> <묶음번호> <묶음크기>
   ⚠️ 한 프로세스에 모델 하나 — 부르는 쪽이 [학습 모델 → 원본] 순으로 묶음마다 두 번 부른다.
   순서: 묶음(양사·접속사…) → HSK 1→6 → 그 밖. 앞부터 되니 평가를 먼저 시작할 수 있다.
   세 번째 길: 둘 다 실패한 한 글자는 audio_say(예: 一杯) 또는 양사면 「一」을 붙여 다시 시도."""
import os, sys, sqlite3, json
sys.path.insert(0, '/app'); os.chdir('/app')
import soundfile as sf, tts.zh_tts as z
sex, model, bno, bsz = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
who = model.split(':')[-1]
TRIES = int(os.environ.get('TRIES', '6'))
ORDER = """SELECT id, chinese, wordset, hsk, audio_say FROM words WHERE COALESCE(excluded,0)=0
           ORDER BY CASE WHEN wordset IS NOT NULL AND wordset<>'' THEN 0
                         WHEN hsk BETWEEN 1 AND 6 THEN hsk ELSE 9 END, id"""
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
rows = con.execute(ORDER).fetchall()[bno * bsz:(bno + 1) * bsz]
done = {r['word_id'] for r in con.execute("SELECT word_id FROM word_audio_new WHERE sex=? AND path IS NOT NULL", (sex,))}
todo = [r for r in rows if r['id'] not in done]
print(f"{sex} {model} 묶음{bno}: {len(todo)}/{len(rows)}개", flush=True)
OUT = 'outputs/chinese/new'
carrier_only = model.startswith('원본')            # 세 번째 길은 원본 단계에서만 (마지막)
for r in todo:
    ch = r['chinese']; texts = [ch]
    if carrier_only and len(ch) == 1:
        c = (r['audio_say'] or '').strip()
        if c and c != ch: texts.append(c)
        elif r['wordset'] == '양사': texts.append('一' + ch)
    got = None
    for t in texts:
        w, rep = z.say_word(model, t, ref_dir=f'voice_samples/{who}', tries=TRIES)
        if w is not None: got = (t, w, rep); break
    if got is None:
        con.execute("""INSERT INTO word_audio_new(word_id,sex,path,model,spoken,report)
                       VALUES(?,?,NULL,?,?,?) ON CONFLICT(word_id,sex) DO UPDATE SET
                       model=excluded.model, report=excluded.report""",
                    (r['id'], sex, model, ch, rep[-300:]))
        con.commit(); print(f"  ✗ {ch}", flush=True); continue
    t, w, rep = got
    p = f"{OUT}/{r['id']}_{sex}.wav"; sf.write(p, w, 24000)
    con.execute("""INSERT INTO word_audio_new(word_id,sex,path,model,spoken,dur,report)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(word_id,sex) DO UPDATE SET
                   path=excluded.path, model=excluded.model, spoken=excluded.spoken,
                   dur=excluded.dur, report=excluded.report, created_at=CURRENT_TIMESTAMP""",
                (r['id'], sex, p, model, t, round(len(w) / 24000, 2), rep[-300:]))
    con.commit()
    print(f"  ✓ {ch}{'' if t == ch else ' ←' + t} {len(w)/24000:.2f}s", flush=True)
con.close()
