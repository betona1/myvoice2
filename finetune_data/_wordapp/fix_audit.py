# -*- coding: utf-8 -*-
"""검사에서 흠으로 잡힌 소리를 다시 만든다.
   쓰임: fix_audit.py <word|ex> <남|여> <모델>
   ⚠️ 흠 비율이 너무 높으면(기본 40%) **아무것도 안 고치고 멈춘다** — 그런 경우는
      소리가 아니라 검사 도구가 틀렸을 가능성이 크기 때문이다 (이 프로젝트에서 여러 번 그랬다).
   다시 만든 것은 그 자리(words/word_examples 의 audio 칸)에 바로 걸고, 검사 기록을 지워 다음 판에서 다시 본다."""
import os, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf
import tts.zh_tts as z

kind, sex, model = sys.argv[1], sys.argv[2], sys.argv[3]
who = model.split(':')[-1]
col = 'audio1' if sex == '남' else 'audio2'
TRIES = int(os.environ.get('TRIES', '8'))
MAXBAD = float(os.environ.get('MAXBAD', '0.40'))
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row

tot = con.execute("SELECT COUNT(*) FROM audio_audit WHERE kind=? AND sex=?", (kind, sex)).fetchone()[0]
bad = con.execute("SELECT COUNT(*) FROM audio_audit WHERE kind=? AND sex=? AND ok=0", (kind, sex)).fetchone()[0]
if tot and bad / tot > MAXBAD:
    print(f'!! {kind} {sex}: 흠 {bad}/{tot} = {bad/tot*100:.0f}% — 너무 많아 멈춥니다.')
    print('   소리보다 검사 잣대를 먼저 의심해야 합니다. 까닭별 수:')
    for r in con.execute("""SELECT why, COUNT(*) c FROM audio_audit WHERE kind=? AND sex=? AND ok=0
                            GROUP BY why ORDER BY c DESC LIMIT 12""", (kind, sex)):
        print(f'     {r["c"]:6d}  {r["why"]}')
    sys.exit(2)

if kind == 'word':
    rows = con.execute("""SELECT a.item_id id, w.chinese text, w.audio_say, w.wordset, a.why
                          FROM audio_audit a JOIN words w ON w.id=a.item_id
                          WHERE a.kind='word' AND a.sex=? AND a.ok=0 ORDER BY a.item_id""", (sex,)).fetchall()
else:
    rows = con.execute("""SELECT a.item_id id, e.chinese text, NULL audio_say, NULL wordset, a.why
                          FROM audio_audit a JOIN word_examples e ON e.id=a.item_id
                          WHERE a.kind='ex' AND a.sex=? AND a.ok=0 ORDER BY a.item_id""", (sex,)).fetchall()
print(f'{kind} {sex} {model}: 다시 만들 {len(rows)}개 (흠 비율 {bad}/{tot})', flush=True)

OUT = 'outputs/chinese/new' if kind == 'word' else 'outputs/chinese/new_ex'
os.makedirs(OUT, exist_ok=True)
TABLE = 'words' if kind == 'word' else 'word_examples'
fixed = left = 0
for r in rows:
    texts = [r['text']]
    if kind == 'word' and model.startswith('원본') and len(r['text']) == 1:
        c = (r['audio_say'] or '').strip()
        if c and c != r['text']: texts.append(c)
        elif r['wordset'] == '양사': texts.append('一' + r['text'])
    got = None
    for t in texts:
        if kind == 'word':
            w, rep = z.say_word(model, t, ref_dir=f'voice_samples/{who}', tries=TRIES)
        else:
            w, rep = z.say_example(model, t, ref_dir=f'voice_samples/{who}', tries=TRIES)
        if w is not None: got = (t, w); break
    if got is None:
        left += 1; print(f'  ✗ {r["text"][:14]}  ({r["why"]})', flush=True); continue
    t, w = got
    p = f"{OUT}/{r['id']}_{sex}.wav"; sf.write(p, w, 24000)
    con.execute(f"UPDATE {TABLE} SET {col}=? WHERE id=?", (p, r['id']))
    con.execute("DELETE FROM audio_audit WHERE kind=? AND item_id=? AND sex=?", (kind, r['id'], sex))
    con.commit(); fixed += 1
    print(f'  ✓ {r["text"][:14]}{"" if t == r["text"] else " ←" + t} {len(w)/24000:.2f}s', flush=True)
print(f'{kind} {sex} {model}: 고침 {fixed} · 남음 {left}', flush=True)
con.close()
