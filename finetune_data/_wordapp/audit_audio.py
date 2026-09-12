# -*- coding: utf-8 -*-
"""음성 전수 검사 — 지금 앱이 쓰는 소리를 하나하나 검사해 흠을 기록한다.
   쓰임: audit_audio.py <word|ex> <남|여> [묶음번호 묶음크기]
   검사 항목 (낱말): 앞 군소리·앞 잡음·끝 잘림·속도·목소리 떨림·음절 성조·문맥 받아쓰기
          (예문): 위에 더해 낱말 사이 군소리·끝 약함
   ⚠️ 검사 도구가 틀릴 수 있다 — 이 파일은 **고치지 않고 기록만** 한다.
      고치는 것은 fix_audit.py 가 하고, 흠 비율이 너무 높으면 멈추고 사람에게 묻는다."""
import os, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf
import tts.zh_tts as z

kind, sex = sys.argv[1], sys.argv[2]
bno = int(sys.argv[3]) if len(sys.argv) > 3 else 0
bsz = int(sys.argv[4]) if len(sys.argv) > 4 else 100000
ROUND = int(os.environ.get('ROUND', '1'))
col = 'audio1' if sex == '남' else 'audio2'

# 예문은 make_sents 의 검사 한 벌을 그대로 쓴다
_ex_verify = None
if kind == 'ex':
    src = open('/app/finetune_data/_wordapp/make_sents.py', encoding='utf-8').read()
    head = src[:src.index('for si, s in enumerate(sents):')]
    _argv = sys.argv[:]; sys.argv = ['x', sex, '리리' if sex == '여' else '이한', '리리', '0.5', 'new']
    ns = {}; exec(compile(head, 'h', 'exec'), ns); sys.argv = _argv
    _ex_verify = ns['verify']

con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
con.execute("""CREATE TABLE IF NOT EXISTS audio_audit (
  kind TEXT NOT NULL, item_id INTEGER NOT NULL, sex TEXT NOT NULL,
  ok INTEGER, why TEXT, round INTEGER, path TEXT,
  checked_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(kind,item_id,sex))""")
con.commit()

if kind == 'word':
    rows = con.execute(f"""SELECT id, chinese AS text, {col} AS path FROM words
                           WHERE COALESCE(excluded,0)=0 ORDER BY id""").fetchall()
else:
    rows = con.execute(f"""SELECT id, chinese AS text, {col} AS path FROM word_examples
                           ORDER BY word_id, seq""").fetchall()
rows = rows[bno * bsz:(bno + 1) * bsz]
done = {r['item_id'] for r in con.execute(
    "SELECT item_id FROM audio_audit WHERE kind=? AND sex=? AND round>=?", (kind, sex, ROUND))}
todo = [r for r in rows if r['id'] not in done]
print(f'{kind} {sex} 묶음{bno}: 검사할 {len(todo)}/{len(rows)}개', flush=True)

HAN = lambda s: ''.join(z._HAN.findall(s or ''))
ok_n = bad_n = 0
for r in todo:
    p = r['path']; why = ''
    if not p or not os.path.exists(p):
        why = '파일 없음'
    else:
        try:
            w, sr = sf.read(p); w = np.asarray(w, dtype=np.float32)
            if w.ndim > 1: w = w.mean(axis=1)
            want = HAN(r['text'])
            if not want:
                why = ''
            elif kind == 'ex':
                why = z.check_example(w, r['text'])
            else:
                good, w2, _ = (lambda t: (t[0], t[2], t[1]))(z.word_extra_ok(w, r['text']))
                why = '' if good else z.word_extra_ok(w, r['text'])[1]
                if not why:
                    h = z._hear_ctx(w)
                    if h is None: why = '문맥 받아쓰기 실패'
                    elif not (h == want or z._py(h) == z._py(want)
                              or (want.endswith('儿') and h == want[:-1])):
                        why = f'들린「{h[:8]}」≠'
        except Exception as e:
            why = f'검사 오류 {type(e).__name__}'
    con.execute("""INSERT INTO audio_audit(kind,item_id,sex,ok,why,round,path,checked_at)
                   VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(kind,item_id,sex)
                   DO UPDATE SET ok=excluded.ok, why=excluded.why, round=excluded.round,
                                 path=excluded.path, checked_at=CURRENT_TIMESTAMP""",
                (kind, r['id'], sex, 0 if why else 1, why, ROUND, p))
    if why: bad_n += 1
    else: ok_n += 1
    if (ok_n + bad_n) % 100 == 0:
        con.commit(); print(f'   {ok_n+bad_n}/{len(todo)} · 흠 {bad_n}', flush=True)
con.commit()
print(f'{kind} {sex} 묶음{bno} 끝 — 괜찮음 {ok_n} · 흠 {bad_n}', flush=True)
con.close()
