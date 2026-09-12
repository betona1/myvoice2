# -*- coding: utf-8 -*-
"""여러 글자로 된 단어 음성 검증. 争夺 처럼 뒤에 엉뚱한 소리가 붙은 것,
   아예 다른 말이 들리는 것을 갈라내고 다시 만든다."""
import os, sys, json, sqlite3, collections
sys.path.insert(0, '/app'); os.chdir('/app')
sys.path.insert(0, '/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge

PHASE = sys.argv[1] if len(sys.argv) > 1 else 'check'
OUT = 'finetune_data/_wordapp/audio_bad_multi.json'
SR = 24000

def load_asr():
    from faster_whisper import WhisperModel
    return WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")

def hear(model, path):
    segs, _ = model.transcribe(path, language='zh', beam_size=1, vad_filter=False)
    return ''.join(s.text for s in segs).strip()

def rows():
    con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True)
    r = con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                       WHERE length(chinese) BETWEEN 2 AND 6 AND COALESCE(excluded,0)=0
                       ORDER BY seq""").fetchall()
    con.close(); return r

if PHASE == 'check':
    asr = load_asr(); bad = []; n = 0
    for wid, w, a1, a2 in rows():
        for col, p in (('audio1', a1), ('audio2', a2)):
            if not p or not os.path.exists(p): continue
            n += 1
            t = hear(asr, p); v = judge(w, t)
            if v != 'ok':
                bad.append({'id': wid, 'ch': w, 'col': col, 'path': p, 'heard': t, 'kind': v})
            if n % 1000 == 0: print(f"  {n}개 확인 · 이상 {len(bad)}개", flush=True)
    json.dump(bad, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"\n검증 {n}개 / 이상 {len(bad)}개 → {OUT}")
    print("  유형:", dict(collections.Counter(b['kind'] for b in bad)))
    for b in bad[:30]:
        print(f"   [{b['kind']:<6}] {b['ch']:<6}{b['col']:<7} 들린 소리: {b['heard'][:26]!r}")

elif PHASE == 'fix':
    from tts.engine import get_engine
    REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
           'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
    def trim(w, sr=SR, thr=0.012, gap_s=0.30, pad_s=0.06):
        """한 단어는 음절이 붙어 나오므로, 0.3초 넘게 벌어진 뒤의 소리는 군더더기로 본다."""
        e = np.abs(w); v = e > thr
        if not v.any(): return None
        gap = int(sr*gap_s); segs, st, run = [], None, 0
        for i, x in enumerate(v):
            if x:
                if st is None: st = i
                run = 0
            else:
                if st is not None:
                    run += 1
                    if run >= gap: segs.append((st, i-run+1)); st = None; run = 0
        if st is not None: segs.append((st, len(w)))
        if not segs: return None
        a, b = max(segs, key=lambda s: float(np.sum(e[s[0]:s[1]]**2)))
        pad = int(sr*pad_s)
        return w[max(0, a-pad):min(len(w), b+pad)]

    bad = json.load(open(OUT, encoding='utf-8'))
    print(f"다시 만들 음성 {len(bad)}개", flush=True)
    asr = load_asr(); eng = get_engine()
    cut = new = give = 0
    for i, b in enumerate(bad, 1):
        w, p, col = b['ch'], b['path'], b['col']
        done = False
        # 1) 원본을 잘라내는 것만으로 되는지 먼저 본다 (목소리 결이 그대로 남는다)
        try:
            cur, _ = sf.read(p, dtype='float32')
            if cur.ndim > 1: cur = cur.mean(1)
            t = trim(cur)
            if t is not None and len(t) >= SR*0.25 and len(t) < len(cur):
                sf.write('/tmp/_m0.wav', t, SR)
                if judge(w, hear(asr, '/tmp/_m0.wav')) == 'ok':
                    sf.write(p, t, SR); cut += 1; done = True
        except Exception:
            pass
        if not done:
            # 2) 안 되면 통과할 때까지 최대 5번 새로 만든다
            best = None
            for _ in range(5):
                aw = np.array(eng.tts.tts(text=w, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
                t = trim(aw)
                if t is None or len(t) < SR*0.25: continue
                sf.write('/tmp/_m1.wav', t, SR)
                if judge(w, hear(asr, '/tmp/_m1.wav')) == 'ok':
                    best = (t, True); break
                if best is None: best = (t, False)
            # 통과한 것만 덮어쓴다. 못 고쳤으면 원본을 그대로 둔다
            if best is not None and best[1]:
                sf.write(p, best[0], SR); new += 1
            else:
                give += 1
        if i % 100 == 0: print(f"  {i}/{len(bad)} 처리 (잘라냄 {cut} · 새로 {new} · 실패 {give})", flush=True)
    print(f"\n잘라내 해결 {cut}개 / 새로 만들어 해결 {new}개 / 끝내 못 고침 {give}개")
