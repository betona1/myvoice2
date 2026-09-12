# -*- coding: utf-8 -*-
"""예문(word_examples) 음성을 새 목소리로 만든다.
   짧은 문장에서 다듬은 검사 한 벌을 그대로 쓴다 — make_sents.py 의 함수들을 불러 온다.
     앞 군소리·앞 잡음·낱말 사이 군소리·끝 잘림·끝 약함·속도·음절 성조·받아쓰기
   쓰임: make_examples.py <성별> <모델> [묶음번호 묶음크기]
   ⚠️ 한 프로세스에 모델 하나. 부르는 쪽이 [학습 모델 → 원본] 순으로 두 번 부른다."""
import os, sys, re, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf

sex, model = sys.argv[1], sys.argv[2]
bno = int(sys.argv[3]) if len(sys.argv) > 3 else 0
bsz = int(sys.argv[4]) if len(sys.argv) > 4 else 100000
who = model.split(':')[-1]
TRIES = int(os.environ.get('TRIES', '8'))

# make_sents.py 의 검사 함수들을 그대로 가져온다 (본문은 안 돌게 머리만 실행)
src = open('/app/finetune_data/_wordapp/make_sents.py', encoding='utf-8').read()
head = src[:src.index('for si, s in enumerate(sents):')]
_argv = sys.argv[:]; sys.argv = ['x', sex, who, model, '0.62', 'new']
ns = {}; exec(compile(head, 'make_sents_head', 'exec'), ns); sys.argv = _argv
z = ns['z']; SR = ns['SR']; HAN = ns['HAN']; hear_words = ns['hear_words']
end_dip = ns['end_dip']; trim_to_onset = ns['trim_to_onset']; verify = ns['verify']
TEMP = 0.45 if sex == '여' else 0.62          # 여자는 차분하게 (긴 글에서 그쪽이 나았다)

OUT = 'outputs/chinese/new_ex'; os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
con.execute("""CREATE TABLE IF NOT EXISTS example_audio_new (
  ex_id INTEGER NOT NULL, sex TEXT NOT NULL, path TEXT, model TEXT, dur REAL, report TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(ex_id, sex))""")
con.commit()

rows = con.execute("""SELECT e.id, e.chinese FROM word_examples e ORDER BY e.word_id, e.seq""").fetchall()
rows = rows[bno * bsz:(bno + 1) * bsz]
done = {r['ex_id'] for r in con.execute("SELECT ex_id FROM example_audio_new WHERE sex=? AND path IS NOT NULL", (sex,))}
todo = [r for r in rows if r['id'] not in done]
print(f'{sex} {model}: {len(todo)}/{len(rows)}개', flush=True)

for r in todo:
    s = r['chinese']; want = HAN(s)
    if not want: continue
    w, rep = z.say_example(model, s, tries=TRIES, ref_dir=f'voice_samples/{who}')
    best = (0.0, w) if w is not None else None
    log = [rep or '']
    if best is None:
        con.execute("""INSERT INTO example_audio_new(ex_id,sex,path,model,report) VALUES(?,?,NULL,?,?)
                       ON CONFLICT(ex_id,sex) DO UPDATE SET model=excluded.model, report=excluded.report""",
                    (r['id'], sex, model, ' / '.join(log)[-300:]))
        con.commit(); print(f'  ✗ {s}  ' + ' / '.join(log)[-90:], flush=True); continue
    p = f"{OUT}/{r['id']}_{sex}.wav"; sf.write(p, best[1], SR)
    con.execute("""INSERT INTO example_audio_new(ex_id,sex,path,model,dur,report,created_at)
                   VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(ex_id,sex) DO UPDATE SET
                   path=excluded.path, model=excluded.model, dur=excluded.dur, report=excluded.report,
                   created_at=CURRENT_TIMESTAMP""",
                (r['id'], sex, p, model, round(len(best[1]) / SR, 2), ' / '.join(log)[-300:]))
    con.commit(); print(f'  ✓ {s} {len(best[1])/SR:.2f}s', flush=True)
con.close()
