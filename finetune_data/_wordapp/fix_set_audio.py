# -*- coding: utf-8 -*-
"""묶음(양사·접속사·의성의태) 단어의 음성을 다듬는다.
   10GB 카드라 XTTS 와 Whisper 를 함께 못 올린다 → 단계를 나눈다.
     make   : 후보를 넉넉히 만든다
     verify : Whisper 로 제대로 들리는 것을 고른다 (한 글자는 잘 헷갈리므로 후보가 많아야 한다)
   판정은 zhjudge — 로마자·번체·숫자 표기 흔들림을 같은 소리로 친다."""
import os, re, sys, glob, shutil, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
sys.path.insert(0, '/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge

PHASE = sys.argv[1] if len(sys.argv) > 1 else 'make'
SET   = sys.argv[2] if len(sys.argv) > 2 else '양사'
N     = int(os.environ.get('N_CAND', '8'))
SR    = 24000
REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
       'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
OUT = 'outputs/chinese/words'
TMP = 'outputs/chinese/_retry'
os.makedirs(TMP, exist_ok=True)

def trim(w, sr=SR, thr=0.012, gap_s=0.22, pad_s=0.05):
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
    a, b = max(segs, key=lambda s: float(np.sum(e[s[0]:s[1]] ** 2)))
    pad = int(sr * pad_s)
    return w[max(0, a - pad):min(len(w), b + pad)]

con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
BAD = 'finetune_data/_wordapp/set_audio_bad.txt'

if PHASE == 'make':
    # 다시 만들 대상 — verify 가 남긴 목록이 있으면 그것만, 없으면 묶음 전체
    if os.path.exists(BAD):
        want = [l.strip() for l in open(BAD, encoding='utf-8') if l.strip()]
        rows = [r for r in con.execute(
            "SELECT id,chinese FROM words WHERE wordset=? AND COALESCE(excluded,0)=0", (SET,))
            if r['chinese'] in want]
    else:
        rows = con.execute("SELECT id,chinese FROM words WHERE wordset=? AND COALESCE(excluded,0)=0",
                           (SET,)).fetchall()
    from tts.engine import get_engine
    eng = get_engine()
    print(f"{SET} — 다시 만들 {len(rows)}개 (한 목소리에 {N}개씩)", flush=True)
    for f in glob.glob(f'{TMP}/*.wav'):
        os.remove(f)
    for r in rows:
        for col in ('audio1', 'audio2'):
            for k in range(N):
                aw = np.array(eng.tts.tts(text=r['chinese'], speaker_wav=REF[col], language='zh-cn'),
                              dtype=np.float32)
                t = trim(aw)
                if t is not None and len(t) >= SR * 0.2:
                    sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", t, SR)
        print(f"   {r['chinese']} 후보 생성", flush=True)
    print("\n생성 끝 — 이어서 verify")
else:
    from faster_whisper import WhisperModel
    asr = WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s, _ = asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                              temperature=[0.0, 0.2], vad_filter=False)
        return ''.join(z.text for z in s).strip()
    rows = con.execute("SELECT id,chinese,audio1,audio2 FROM words WHERE wordset=? AND COALESCE(excluded,0)=0",
                       (SET,)).fetchall()
    fixed, still = 0, []
    for r in rows:
        ch = r['chinese']
        for col in ('audio1', 'audio2'):
            cur = r[col]
            # 지금 것이 이미 잘 들리면 둔다
            if cur and os.path.exists(cur) and judge(ch, hear(cur)) == 'ok':
                continue
            cands = sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav"))
            got = next((p for p in cands if judge(ch, hear(p)) == 'ok'), None)
            if got:
                dst = cur if cur else f"{OUT}/{col}_{r['id']}_{abs(hash(ch)) % 0xffffff:06x}.wav"
                shutil.copy2(got, dst)
                con.execute(f"UPDATE words SET {col}=? WHERE id=?", (dst, r['id']))
                fixed += 1
            else:
                still.append(f"{ch}({col[-1]})")
    con.commit()
    open(BAD, 'w', encoding='utf-8').write(
        '\n'.join(sorted({s.split('(')[0] for s in still})))
    print(f"\n{SET} — 고친 음성 {fixed}개 · 아직 안 되는 것 {len(still)}개")
    print('  ' + '  '.join(still))
con.close()
