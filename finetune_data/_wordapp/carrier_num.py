# -*- coding: utf-8 -*-
"""XTTS 가 끝내 홀로 못 읽는 양사는 「一杯」처럼 수사를 붙여 들려준다.
   ① 두 음절이면 XTTS 도 성조를 제대로 내고, ② 받아적기도 제 구실을 한다
      (홀로 떨어진 한 음절은 빈칸이나 헛것으로 나와 가릴 수가 없다 — 직접 확인했다).
   ③ 양사는 본디 「一杯」「三张」처럼 수사와 함께 쓰이니 배우기에도 이 편이 낫다.
   화면에 보이는 글자는 그대로 두고 소리만 바꾼다."""
import os, re, sys, glob, json, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
PHASE=sys.argv[1]; SR=24000; NTRY=8
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP='outputs/chinese/_num'; os.makedirs(TMP, exist_ok=True)
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
ALT = len(sys.argv) > 2 and sys.argv[2] == 'alt'
if ALT:
    left=json.load(open('finetune_data/_wordapp/homo_left.json',encoding='utf-8'))
    need={}
    for x in left: need.setdefault(x.split('/')[0], set()).add('audio'+x.split('/')[1])
else:
    bad=json.load(open('finetune_data/_wordapp/audit_bad.json',encoding='utf-8'))
    need={}
    for b in bad: need.setdefault(b[0], set()).add(b[1])
from pypinyin import pinyin as _py, Style as _St
def nums(ch):
    """붙일 수사 — 3성 양사에 两(3성)을 붙이면 앞이 2성으로 바뀌는 변조가 난다."""
    t=_py(ch, style=_St.TONE3, neutral_tone_with_five=True)[0][0]
    base=['三','两','这'] if not t.endswith('3') else ['三','这','几']
    return (['一']+base) if not ALT else (base + ['两','一','四','五'])

if PHASE=='make':
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    for ch, cols in sorted(need.items()):
        r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        cars=nums(ch)
        for col in sorted(cols):
            for ni,nu in enumerate(cars):
                for k in range(NTRY):
                    aw=np.array(eng.tts.tts(text=nu+ch, speaker_wav=REF[col], language='zh-cn'),
                                dtype=np.float32)
                    a=np.abs(aw); v=a>0.012
                    if not v.any(): continue
                    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
                    seg=aw[max(0,i0-int(SR*0.03)):min(len(aw), i1+int(SR*0.06))]
                    if len(seg) >= SR*0.35:
                        sf.write(f"{TMP}/{r['id']}_{col}_{ni}_{k}.wav", seg, SR)
        print(f"  {ch} ← {' '.join(n+ch for n in cars)}", flush=True)
    print("\n수사 붙여 만들기 끝")
else:
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                           temperature=[0.0,0.2], vad_filter=False)
        return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
    ok, still = [], []
    for ch, cols in sorted(need.items()):
        r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        wants=[n+ch for n in nums(ch)]
        for col in sorted(cols):
            got=None
            for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
                h=hear(p)
                if h not in wants: continue                # 「一杯」「三杯」로 또렷이 들린 것만
                d=len(sf.read(p)[0])/sf.read(p)[1]
                if 0.4 <= d <= 1.8 and (got is None or d>got[1]): got=(p,d,h)
            if got:
                p,d,h=got
                seg,sr=sf.read(p)
                f=min(int(sr*0.04), len(seg)//5)
                if f>1: seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
                sf.write(r[col], seg, sr)
                con.execute("UPDATE words SET audio_say=? WHERE id=?", (h, r['id']))
                ok.append(f"{ch}/{col[-1]}")
                print(f"  ✓ {ch} {col} ← '{h}' {d:.2f}s", flush=True)
            else:
                still.append(f"{ch}/{col[-1]}")
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    json.dump(still, open('finetune_data/_wordapp/homo_left.json','w',encoding='utf-8'), ensure_ascii=False)
    print(f"\n수사를 붙여 채운 것 {len(ok)}개 · 아직 안 되는 것 {len(still)}개")
    if still: print("  " + ' '.join(still))
