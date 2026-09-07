# -*- coding: utf-8 -*-
"""또래보다 거친 음성을 한꺼번에 다시 만든다.

   받아적기는 「무슨 말인가」만 본다 — 쉰 소리·떨림은 못 가린다.
   그래서 소리를 재서(목청 울림·맑기·흔들림) 또래(같은 글자 수)보다 처지는 것만 골랐다.
   ⚠️ 새것이 지금 것보다 나을 때만 바꾼다. 원본은 .orig 로 남긴다.
   ⚠️ 빠르기는 1.0 그대로 만든다 — 화면에서 듣는 사람이 조절하므로 파일에 굳히지 않는다.
   10GB 카드라 XTTS 와 Whisper 를 함께 못 올린다 → make / pick 두 판.
"""
import os, re, sys, glob, json, shutil, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
PHASE=sys.argv[1]
LIMIT=int(sys.argv[2]) if len(sys.argv)>2 else 0
N=int(os.environ.get('NCAND','6'))
SR=24000
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP='outputs/chinese/_batch'; os.makedirs(TMP, exist_ok=True)
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
rough=json.load(open('/app/finetune_data/_wordapp/rough.json',encoding='utf-8'))
if LIMIT: rough=rough[:LIMIT]

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
    for i,x in enumerate(rough,1):
        ch, col = x[0], x[2]
        r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        for k in range(N):
            aw=np.array(eng.tts.tts(text=ch, speaker_wav=REF[col], language='zh-cn'),
                        dtype=np.float32)
            t=trim(aw)
            if t is not None and len(t)>=SR*0.25:
                sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", t, SR)
        if i%25==0: print(f"   {i}/{len(rough)} 만드는 중", flush=True)
    print(f"\n{len(rough)}개 후보 생성 끝")
else:
    import librosa
    from faster_whisper import WhisperModel
    from pypinyin import pinyin as _py, Style
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, temperature=[0.0], vad_filter=False)
        return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
    def smooth(p):
        try: y,sr=librosa.load(p, sr=16000)
        except Exception: return None
        if len(y)<sr*0.2: return None
        f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=sr, frame_length=1024)
        voiced=float(np.nanmean(vo)) if vo is not None else 0
        flat=float(np.mean(librosa.feature.spectral_flatness(y=y)))
        f=f0[~np.isnan(f0)]
        jit=float(np.mean(np.abs(np.diff(f)))/(np.mean(f)+1e-9)) if len(f)>4 else 1
        return voiced*2.0 - flat*6.0 - jit*3.0
    ok=keep=0
    for i,x in enumerate(rough,1):
        ch, col = x[0], x[2]
        r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        cur=r[col]
        base=smooth(cur) if cur and os.path.exists(cur) else -99
        want=syl(ch); best=None
        for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
            h=hear(p)
            if h!=ch and syl(h)!=want: continue       # 말이 맞아야 한다
            s=smooth(p)
            if s is None: continue
            if best is None or s>best[0]: best=(s,p)
        if best and base is not None and best[0] > base + 0.3:
            if not os.path.exists(cur+'.orig'): shutil.copy2(cur, cur+'.orig')
            d,sr2=sf.read(best[1]); sf.write(cur, d, sr2); ok+=1
        else:
            keep+=1
        if i%25==0: print(f"   {i}/{len(rough)}  바꾼 것 {ok} · 그대로 {keep}", flush=True)
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    print(f"\n바꾼 것 {ok}개 · 지금 것이 나아 둔 것 {keep}개")
