# -*- coding: utf-8 -*-
"""접속사 음성에서 뒷말이 빠진 것을 고친다.
   「不但…而且…」를 그대로 읽히면 XTTS 가 앞 도막만 읽고 만다(…를 못 다룬다).
   그래서 「…」를 쉼표로 바꿔 읽힌다 — 不但，而且 처럼.
   받아적기로 **두 도막이 다 들리는지** 보고 그때만 바꾼다. make / verify 두 판."""
import os, re, sys, glob, shutil, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
PHASE=sys.argv[1]; SR=24000; N=int(os.environ.get('NCAND','5'))
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP='outputs/chinese/_conj'; os.makedirs(TMP, exist_ok=True)
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
rows=[r for r in con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                                WHERE wordset='접속사' AND COALESCE(excluded,0)=0""")
      if '…' in r['chinese']]
def speak(ch):
    """「…」를 쉼표로 — 그래야 두 도막을 다 읽는다"""
    t=re.sub(r'…+', '，', ch).strip('，')
    return re.sub(r'[（）()]', '', t)
def parts(ch):
    return [p for p in re.split(r'…+', re.sub(r'[（）()]','',ch)) if CJK.search(p)]
if PHASE=='make':
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    for i,r in enumerate(rows,1):
        t=speak(r['chinese'])
        for col in ('audio1','audio2'):
            for k in range(N):
                aw=np.array(eng.tts.tts(text=t, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
                a=np.abs(aw); v=a>0.012
                if not v.any(): continue
                i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
                seg=aw[max(0,i0-int(SR*0.03)):min(len(aw), i1+int(SR*0.07))]
                if len(seg)>=SR*0.4: sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", seg, SR)
        if i%10==0: print(f"   {i}/{len(rows)}", flush=True)
    print(f"\n접속사 {len(rows)}개 후보 생성 끝")
else:
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, temperature=[0.0,0.2], vad_filter=False)
        return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
    ok=keep=0
    for r in rows:
        want=parts(r['chinese'])
        for col in ('audio1','audio2'):
            cur=r[col]
            if not (cur and os.path.exists(cur)): continue
            got=None
            for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
                h=hear(p)
                if all(w in h for w in want):        # 도막이 다 들려야 한다
                    d=len(sf.read(p)[0])/sf.read(p)[1]
                    if got is None or d>got[1]: got=(p,d)
            if got:
                if not os.path.exists(cur+'.orig'): shutil.copy2(cur, cur+'.orig')
                d0,sr0=sf.read(got[0]); sf.write(cur, d0, sr0); ok+=1
            else: keep+=1
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    print(f"\n두 도막이 다 들리게 바꾼 것 {ok}개 · 못 고친 것 {keep}개")
