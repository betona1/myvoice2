# -*- coding: utf-8 -*-
"""낱글자 음성 한둘을 골라 고친다.
   소리가 같은 다른 글자로도 읽혀 보고, 받아적기가 **그 소리 한 음절**로 들은 것만 쓴다.
   쓰기:  python3 fix_one.py make 拣:2 捡:1
          python3 fix_one.py pick 拣:2 捡:1"""
import os, re, sys, glob, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from pypinyin import pinyin as _py, Style
PHASE=sys.argv[1]; JOBS=[a.split(':') for a in sys.argv[2:]]
SR=24000; TMP='outputs/chinese/_one'; os.makedirs(TMP, exist_ok=True)
REF={'1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     '2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
CJK=re.compile(r'[一-鿿]')
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row

def homos(ch):
    want=syl(ch)[0]
    known={r[0] for r in con.execute("SELECT chinese FROM words WHERE length(chinese)=1")}
    out=[]
    for ln in open('database/cedict_full.txt', encoding='utf-8'):
        if ln.startswith('#'): continue
        try: sp=ln.split(' ',2)[1]; py=ln.split('[',1)[1].split(']',1)[0]
        except Exception: continue
        if len(sp)!=1 or not CJK.match(sp): continue
        if re.sub(r'\s','',py.lower())==want.replace(' ','') or syl(sp)[0]==want:
            if sp not in out: out.append(sp)
    out.sort(key=lambda c: (c!=ch, c not in known))
    return out[:10]

if PHASE=='make':
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    for ch, col in JOBS:
        r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        cands=homos(ch)
        print(f"  {ch} ← {' '.join(cands)}", flush=True)
        for ti,t in enumerate(cands):
            for k in range(5):
                aw=np.array(eng.tts.tts(text=t, speaker_wav=REF[col], language='zh-cn'), dtype=np.float32)
                a=np.abs(aw); v=a>0.012
                if not v.any(): continue
                i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
                seg=aw[max(0,i0-int(SR*0.03)):min(len(aw), i1+int(SR*0.07))]
                if len(seg)>=SR*0.3: sf.write(f"{TMP}/{r['id']}_{col}_{ti}_{k}.wav", seg, SR)
    print("만들기 끝")
else:
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                           temperature=[0.0,0.2], vad_filter=False)
        return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
    for ch, col in JOBS:
        r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        want=syl(ch); got=None
        for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
            h=hear(p)
            if len(h)!=1 or syl(h)!=want: continue    # 한 음절로 또렷이 들린 것만
            d=len(sf.read(p)[0])/sf.read(p)[1]
            if 0.4<=d<=1.3 and (got is None or d>got[1]): got=(p,d,h)
        if got:
            p,d,h=got
            seg,sr=sf.read(p)
            f=min(int(sr*0.04), len(seg)//5)
            if f>1: seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
            sf.write(r['audio'+col], seg, sr)
            print(f"  ✓ {ch} audio{col} ← '{h}' {d:.2f}s")
        else:
            print(f"  ✗ {ch} audio{col} — 쓸 만한 후보 없음 (원본 유지)")
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
