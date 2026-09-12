# -*- coding: utf-8 -*-
"""pypinyin 이 잘못 읽는 글자를, 소리가 같고 제대로 읽히는 다른 글자로 강제해 음성을 만든다.
   10GB 카드라 XTTS 와 Whisper 를 함께 못 올린다 → make / verify 두 단계.
   ⚠️ 통과한 후보가 없으면 **원본을 그대로 둔다** (나쁜 것으로 덮어쓰지 않는다)."""
import os, sys, glob, json, shutil, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
sys.path.insert(0, '/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge

PHASE = sys.argv[1] if len(sys.argv) > 1 else 'make'
N = int(os.environ.get('N_CAND', '12'))
SR = 24000
REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
       'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP = 'outputs/chinese/_forced'
os.makedirs(TMP, exist_ok=True)
JOBS = json.load(open('finetune_data/_wordapp/forced_jobs.json', encoding='utf-8'))

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

if PHASE == 'make':
    from tts.engine import get_engine
    eng = get_engine()
    for f in glob.glob(f'{TMP}/*.wav'):
        os.remove(f)
    for word, spoken in JOBS:
        r = con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",
                        (word,)).fetchone()
        if not r:
            print(f"  {word}: DB에 없음"); continue
        for col in ('audio1', 'audio2'):
            for k in range(N):
                aw = np.array(eng.tts.tts(text=spoken, speaker_wav=REF[col], language='zh-cn'),
                              dtype=np.float32)
                t = trim(aw)
                if t is not None and len(t) >= SR * 0.2:
                    sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", t, SR)
        print(f"  {word} ← '{spoken}' 후보 {N}×2개", flush=True)
    print("\n생성 끝")
else:
    from faster_whisper import WhisperModel
    asr = WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s, _ = asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                              temperature=[0.0, 0.2], vad_filter=False)
        return ''.join(z.text for z in s).strip()
    ok = keep = 0
    for word, spoken in JOBS:
        r = con.execute("""SELECT id,audio1,audio2 FROM words
                           WHERE chinese=? AND COALESCE(excluded,0)=0""", (word,)).fetchone()
        if not r:
            continue
        for col in ('audio1', 'audio2'):
            cur = r[col]
            if cur and os.path.exists(cur) and judge(word, hear(cur)) == 'ok':
                keep += 1; continue
            got = None
            for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
                h = hear(p)
                if judge(word, h) == 'ok':
                    got = (p, h); break
            if got:
                shutil.copy2(got[0], cur)
                print(f"   {word} {col}: 통과 '{got[1][:12]}'", flush=True)
                ok += 1
            else:
                print(f"   {word} {col}: 통과한 후보 없음 — 원본 유지", flush=True)
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'):
        os.remove(f)
    print(f"\n새로 채택 {ok}개 · 이미 좋아서 그대로 둔 것 {keep}개")
con.close()
