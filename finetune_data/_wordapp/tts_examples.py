# -*- coding: utf-8 -*-
"""예문(word_examples)의 2인 음성을 만든다.
   ⚠️ 10GB 카드라 XTTS 와 Whisper 를 함께 못 올린다 → make / verify 두 단계.
   예문은 여러 글자라 한 글자보다 잘 나오지만, 뒤에 군더더기가 붙는 일이 있어 잘라 낸다."""
import os, sys, glob, shutil, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
sys.path.insert(0, '/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge

PHASE = sys.argv[1] if len(sys.argv) > 1 else 'make'
N = int(os.environ.get('N_CAND', '3'))
SR = 24000
REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
       'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
OUT = 'outputs/chinese/examples'
TMP = 'outputs/chinese/_ex'
os.makedirs(OUT, exist_ok=True); os.makedirs(TMP, exist_ok=True)

def trim(w, sr=SR, thr=0.012, gap_s=0.30, pad_s=0.06, whole=False):
    """앞뒤 잠잠한 데를 걷어낸다.
       ⚠️ whole=True 면 '가장 센 덩어리'를 고르지 않고 **첫 소리~끝 소리**를 통째로 남긴다.
          쉼표가 든 문장은 중간에 쉼이 있어, 센 덩어리만 고르면 반쪽이 잘려 나간다."""
    e = np.abs(w); v = e > thr
    if not v.any():
        return None
    gap = int(sr * gap_s); segs, st, run = [], None, 0
    for i, x in enumerate(v):
        if x:
            if st is None:
                st = i
            run = 0
        else:
            if st is not None:
                run += 1
                if run >= gap:
                    segs.append((st, i - run + 1)); st = None; run = 0
    if st is not None:
        segs.append((st, len(w)))
    if not segs:
        return None
    if whole:
        a, b = segs[0][0], segs[-1][1]
    else:
        a, b = max(segs, key=lambda s: float(np.sum(e[s[0]:s[1]] ** 2)))
    pad = int(sr * pad_s)
    return w[max(0, a - pad):min(len(w), b + pad)]

con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
rows = con.execute("""SELECT e.id,e.chinese FROM word_examples e
                      JOIN words w ON w.id=e.word_id
                      WHERE (e.audio1 IS NULL OR e.audio1='' OR e.audio2 IS NULL OR e.audio2='')
                      ORDER BY e.word_id,e.seq""").fetchall()
print(f"음성이 없는 예문 {len(rows)}개", flush=True)

if PHASE == 'make':
    if not rows:
        raise SystemExit
    from tts.engine import get_engine
    eng = get_engine()
    for f in glob.glob(f'{TMP}/*.wav'):
        os.remove(f)
    done = 0
    for r in rows:
        for col in ('audio1', 'audio2'):
            for k in range(N):
                aw = np.array(eng.tts.tts(text=r['chinese'], speaker_wav=REF[col], language='zh-cn'),
                              dtype=np.float32)
                t = trim(aw, whole=True)      # 문장은 통째로 남긴다
                if t is not None and len(t) >= SR * 0.3:
                    sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", t, SR)
        done += 1
        if done % 20 == 0:
            print(f"   {done}/{len(rows)} 생성", flush=True)
    print(f"\n{done}개 생성 — 이어서 verify")
else:
    from faster_whisper import WhisperModel
    asr = WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s, _ = asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                              temperature=[0.0, 0.2], vad_filter=False)
        return ''.join(z.text for z in s).strip()
    ok = weak = 0
    todo = con.execute("""SELECT id,chinese FROM word_examples
                          WHERE audio1 IS NULL OR audio1='' OR audio2 IS NULL OR audio2=''""").fetchall()
    for r in todo:
        got = {}
        for col in ('audio1', 'audio2'):
            cands = sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav"))
            if not cands:
                continue
            pick = next((p for p in cands if judge(r['chinese'], hear(p)) == 'ok'), cands[0])
            dst = f"{OUT}/{col}_{r['id']}.wav"
            shutil.copy2(pick, dst); got[col] = dst
        if len(got) == 2:
            con.execute("UPDATE word_examples SET audio1=?, audio2=? WHERE id=?",
                        (got['audio1'], got['audio2'], r['id']))
            ok += 1
        else:
            weak += 1
        if (ok + weak) % 30 == 0:
            print(f"   {ok+weak}/{len(todo)} 확정", flush=True)
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'):
        os.remove(f)
    print(f"\n음성 붙인 예문 {ok}개 · 못 만든 것 {weak}개")
con.close()
