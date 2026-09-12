# -*- coding: utf-8 -*-
"""음성이 없는 단어의 2인 음성을 만든다.
   한 글자는 XTTS 가 앞뒤로 잡음을 붙이므로, 만들고 → 잘라내고 →
   Whisper 로 되받아 적어 그 글자로 들릴 때만 채택한다."""
import os, re, sys, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
sys.path.insert(0, '/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge

PHASE = sys.argv[1] if len(sys.argv) > 1 else 'make'
# ⚠️ 10GB 카드라 XTTS 와 Whisper 를 함께 올리면 메모리가 모자란다 → 단계를 나눈다

SR = 24000
REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
       'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
OUT = 'outputs/chinese/words'
os.makedirs(OUT, exist_ok=True)

def trim(w, sr=SR, thr=0.012, gap_s=0.22, pad_s=0.05):
    """소리가 가장 센 덩어리만 남긴다 (앞뒤 잡음 제거)."""
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
rows = con.execute("""SELECT id,chinese FROM words
                      WHERE COALESCE(excluded,0)=0
                        AND (audio1 IS NULL OR audio1='' OR audio2 IS NULL OR audio2='')
                      ORDER BY seq""").fetchall()
print(f"음성이 없는 단어 {len(rows)}개", flush=True)
if PHASE == 'make' and not rows:
    raise SystemExit

if PHASE == 'make':
    # ① XTTS 로만 만든다 (후보를 여러 개 저장해 둔다)
    from tts.engine import get_engine
    eng = get_engine()
    ok = 0
    for r in rows:
        ch = r['chinese']
        paths = {}
        for col in ('audio1', 'audio2'):
            cands = []
            for k in range(4):
                aw = np.array(eng.tts.tts(text=ch, speaker_wav=REF[col], language='zh-cn'),
                              dtype=np.float32)
                t = trim(aw)
                if t is not None and len(t) >= SR * 0.2:
                    p = f"{OUT}/_cand_{r['id']}_{col}_{k}.wav"
                    sf.write(p, t, SR); cands.append(p)
            if cands:
                paths[col] = cands[0]
        if len(paths) == 2:
            con.execute("UPDATE words SET audio1=?, audio2=? WHERE id=?",
                        (paths['audio1'], paths['audio2'], r['id']))
            ok += 1
            print(f"   {ch} 후보 생성", flush=True)
    con.commit()
    print(f"\n{ok}개 생성 — 이어서 `verify` 로 골라내세요")
else:
    # ② Whisper 로 후보 가운데 제대로 들리는 것을 고른다
    import glob, shutil
    from faster_whisper import WhisperModel
    asr = WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s, _ = asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                              temperature=[0.0, 0.2], vad_filter=False)
        return ''.join(z.text for z in s).strip()
    picked = 0
    for r in con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                            WHERE audio1 LIKE '%_cand_%'""").fetchall():
        ch = r['chinese']; final = {}
        for col in ('audio1', 'audio2'):
            cands = sorted(glob.glob(f"{OUT}/_cand_{r['id']}_{col}_*.wav"))
            if not cands:
                continue
            best = cands[0]
            for p in cands:
                if judge(ch, hear(p)) == 'ok':
                    best = p; break
            dst = f"{OUT}/{col}_{r['id']}_{abs(hash(ch)) % 0xffffff:06x}.wav"
            shutil.copy2(best, dst); final[col] = dst
        if len(final) == 2:
            con.execute("UPDATE words SET audio1=?, audio2=? WHERE id=?",
                        (final['audio1'], final['audio2'], r['id']))
            picked += 1
            print(f"   {ch} 확정", flush=True)
    con.commit()
    for f in glob.glob(f"{OUT}/_cand_*.wav"):
        os.remove(f)
    print(f"\n{picked}개 확정 · 후보 파일 정리 완료")
con.close()
