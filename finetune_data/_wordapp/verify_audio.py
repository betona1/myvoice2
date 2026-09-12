# -*- coding: utf-8 -*-
"""한 글자 음성이 '실제로 그 글자로 들리는지' Whisper 로 되받아 적어 검증한다.
   잘라내기는 덧붙은 소리만 없앨 뿐, 아예 다른 소리를 낸 경우(见 → '하이네후아')는 못 고친다.
   그런 건 다시 만들되 매번 검증해서 통과한 것만 채택한다."""
import os, re, sys, json, sqlite3
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from pypinyin import pinyin as _py, Style

PHASE = sys.argv[1] if len(sys.argv) > 1 else 'check'
OUT = 'finetune_data/_wordapp/audio_bad.json'
SR = 24000
M = {'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
     'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'u','ǘ':'u','ǚ':'u','ǜ':'u','ü':'u'}
def flat(p): return re.sub(r'[^a-z]', '', ''.join(M.get(c, c) for c in (p or '').lower()))
def syl(ch):  return flat(_py(ch, style=Style.NORMAL)[0][0])

def load_asr():
    from faster_whisper import WhisperModel
    return WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")

def hear(model, path):
    segs, _ = model.transcribe(path, language='zh', beam_size=1, vad_filter=False)
    return ''.join(s.text for s in segs).strip()

# Whisper 는 숫자를 아라비아 숫자로 적는다 — 一 을 "1" 로 적었다면 제대로 읽은 것이다
NUM = {'0':'零','1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七','8':'八','9':'九',
       '10':'十','100':'百','1000':'千'}
def ok(ch, text):
    """글자가 그대로 들리거나, 음절(성조 무시)이 같으면 통과."""
    if not text: return False
    if ch in text: return True
    d = re.sub(r'[^0-9]', '', text)
    if d and NUM.get(d) == ch: return True
    if d and len(d) == 1 and NUM.get(d) and syl(NUM[d]) == syl(ch): return True
    t = re.sub(r'[^一-鿿]', '', text)
    if not t or len(t) > 3: return False
    want = syl(ch)
    return any(syl(c) == want for c in t)

con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True)
rows = con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                      WHERE length(chinese)=1 AND COALESCE(excluded,0)=0 ORDER BY seq""").fetchall()
con.close()

if PHASE == 'check':
    asr = load_asr()
    bad = []
    n = 0
    for wid, ch, a1, a2 in rows:
        for col, p in (('audio1', a1), ('audio2', a2)):
            if not p or not os.path.exists(p): continue
            n += 1
            t = hear(asr, p)
            if not ok(ch, t):
                bad.append({'id': wid, 'ch': ch, 'col': col, 'path': p, 'heard': t})
            if n % 200 == 0: print(f"  {n}개 확인 · 이상 {len(bad)}개", flush=True)
    json.dump(bad, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"\n검증 {n}개 / 잘못 들리는 것 {len(bad)}개 → {OUT}")
    for b in bad[:30]:
        print(f"   {b['ch']} {b['col']:<7} 들린 소리: {b['heard'][:22]!r}")

elif PHASE == 'fix':
    from tts.engine import get_engine
    REF = {'audio1': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
           'audio2': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
    def trim(w, sr=SR, thr=0.012, gap_s=0.22, pad_s=0.05):
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
        a, b = max(segs, key=lambda s2: float(np.sum(e[s2[0]:s2[1]]**2)))
        pad = int(sr*pad_s)
        return w[max(0,a-pad):min(len(w),b+pad)]

    bad = json.load(open(OUT, encoding='utf-8'))
    print(f"다시 만들 음성 {len(bad)}개")
    asr = load_asr(); eng = get_engine()
    fixed = give = 0
    for i, b in enumerate(bad, 1):
        ch, p, col = b['ch'], b['path'], b['col']
        best = None
        for a in range(6):                       # 검증에 통과할 때까지 최대 6번
            w = np.array(eng.tts.tts(text=ch, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
            t = trim(w)
            if t is None or len(t) < SR*0.2: continue
            tmp = '/tmp/_try.wav'; sf.write(tmp, t, SR)
            heard = hear(asr, tmp)
            if ok(ch, heard):
                best = t; break
            if best is None: best = t
        if best is None:
            give += 1; continue
        sf.write(p, best, SR); fixed += 1
        if i % 50 == 0: print(f"  {i}/{len(bad)} 처리", flush=True)
    print(f"\n교체 {fixed}개 / 실패 {give}개")
