# -*- coding: utf-8 -*-
"""쉰 소리로 들리는 낱말을 여러 벌 만들어 **가장 매끄러운 것**을 고른다.

   받아적기는 「무슨 말인가」만 가린다 — 목소리가 거친지는 못 본다.
   그래서 소리 자체를 잰다.
     ① 목청이 울리는 비율(voiced)   높을수록 또렷하다
     ② 스펙트럼 평탄도(flatness)     낮을수록 맑다 (높으면 바람소리·잡음에 가깝다)
     ③ 음높이 흔들림(jitter)         낮을수록 안정적이다
   10GB 카드라 XTTS 와 Whisper 를 함께 못 올린다 → make / pick 두 판.
   쓰기:  python3 remake_smooth.py make 妈妈:2 ...
          python3 remake_smooth.py pick 妈妈:2 ..."""
import os, re, sys, glob, sqlite3, shutil
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
PHASE=sys.argv[1]; JOBS=[a.split(':') for a in sys.argv[2:]]
SR=24000; N=int(os.environ.get('NCAND','10'))
TMP='outputs/chinese/_smooth'; os.makedirs(TMP, exist_ok=True)
REF={'1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     '2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row

def trim(w, thr=0.012, pad=0.05):
    a=np.abs(w); v=a>thr
    if not v.any(): return None
    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
    p=int(SR*pad)
    return w[max(0,i0-p):min(len(w), i1+p)]

if PHASE=='make':
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    for ch, col in JOBS:
        r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: print(f"  {ch}: 없음"); continue
        for k in range(N):
            aw=np.array(eng.tts.tts(text=ch, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
            t=trim(aw)
            if t is not None and len(t)>=SR*0.3:
                sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", t, SR)
        print(f"  {ch} {N}벌 만듦", flush=True)
    print("만들기 끝")
else:
    import librosa
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, temperature=[0.0], vad_filter=False)
        return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
    def smooth(p):
        """맑기 점수 — 클수록 매끄럽다."""
        y,sr=librosa.load(p, sr=16000)
        if len(y) < sr*0.2: return None
        f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=sr, frame_length=1024)
        voiced=float(np.nanmean(vo)) if vo is not None else 0
        flat=float(np.mean(librosa.feature.spectral_flatness(y=y)))
        f=f0[~np.isnan(f0)]
        jit=float(np.mean(np.abs(np.diff(f)))/ (np.mean(f)+1e-9)) if len(f)>4 else 1
        return voiced*2.0 - flat*6.0 - jit*3.0, voiced, flat, jit
    from pypinyin import pinyin as _py, Style
    def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
    for ch, col in JOBS:
        r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        want=syl(ch)
        cur=r['audio'+col]
        base=smooth(cur) if cur and os.path.exists(cur) else None
        best=None
        for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
            h=hear(p)
            # 소리가 같으면 글자가 달라도 좋다 (哪 를 那 로 받아적는 일이 잦다)
            if h!=ch and syl(h)!=want: continue
            s=smooth(p)
            if not s: continue
            if best is None or s[0]>best[0][0]: best=(s,p)
        if not best:
            print(f"  ✗ {ch} audio{col}: 쓸 만한 후보 없음"); continue
        (sc,vo,fl,ji), p = best
        if base and sc <= base[0]:
            print(f"  = {ch} audio{col}: 지금 것이 더 낫다 (지금 {base[0]:.2f} · 새것 {sc:.2f})"); continue
        if not os.path.exists(cur+'.orig'): shutil.copy2(cur, cur+'.orig')
        d,sr2=sf.read(p); sf.write(cur, d, sr2)
        print(f"  ✓ {ch} audio{col}: {base[0]:.2f} → {sc:.2f}  (울림 {vo:.2f} 맑기 {fl:.3f} 흔들림 {ji:.3f})")
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
