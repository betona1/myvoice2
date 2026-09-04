# -*- coding: utf-8 -*-
"""낱글자 음성을 '통째로' 만든다 — 잘라 붙이지 않는다.
   예문에서 오려 내면 앞뒤 소리가 섞여 '나시에'처럼 들리고 혀짧은 소리가 난다.
   XTTS 가 글자를 홀로 읽을 때 성조를 놓치는 게 본디 문제이므로,
   **소리가 똑같고 제대로 읽히는 다른 글자**로 읽혀서 그 소리를 쓴다(些 ← 歇).
   10GB 카드라 XTTS 와 Whisper 를 같이 못 올린다 → make / pick 두 판."""
import os, re, sys, glob, json, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from pypinyin import pinyin as _py, Style
PHASE=sys.argv[1]; SR=24000
ROUND2 = len(sys.argv) > 2 and sys.argv[2] == 'left'   # 남은 것만, 후보를 더 넓게
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP='outputs/chinese/_homo'; os.makedirs(TMP, exist_ok=True)
CJK=re.compile(r'[一-鿿]')
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]

con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
targets=[r for r in con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                                   WHERE wordset IN ('양사','의성의태') AND COALESCE(excluded,0)=0
                                   ORDER BY chinese""")
         if len(CJK.findall(r['chinese']))==1]
LEFT=None
if ROUND2:
    try: LEFT={x.split('/')[0] for x in json.load(open('finetune_data/_wordapp/homo_left.json',encoding='utf-8'))}
    except Exception: LEFT=None
    if LEFT: targets=[t for t in targets if t['chinese'] in LEFT]
NCAND = 14 if ROUND2 else 4        # 동음자 몇 개까지 볼까
NTRY  = 5  if ROUND2 else 3        # 한 글자에 몇 번씩 만들까

def homophones():
    """CC-CEDICT 에서 소리가 똑같은(성조까지) 홑글자를 모은다.
       배우는 이가 아는 글자일수록 XTTS 도 또박또박 읽는 편이라, 교재 낱말을 앞에 둔다."""
    known={r[0] for r in con.execute("SELECT chinese FROM words WHERE length(chinese)=1")}
    m={}
    for ln in open('database/cedict_full.txt', encoding='utf-8'):
        if ln.startswith('#'): continue
        try: simp = ln.split(' ',2)[1]; py = ln.split('[',1)[1].split(']',1)[0]
        except Exception: continue
        if len(simp)!=1 or not CJK.match(simp): continue
        k=py.lower().replace(' ','')
        m.setdefault(k, [])
        if simp not in m[k]: m[k].append(simp)
    for k in m: m[k].sort(key=lambda c: (c not in known,))
    return m

if PHASE=='make':
    HOM=homophones()
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    plan={}
    for t in targets:
        ch=t['chinese']; want=syl(ch)[0]
        cands=[ch] + [c for c in HOM.get(want, []) if c != ch][:NCAND]  # 제 글자부터, 그다음 동음자
        plan[ch]=cands
        for col in ('audio1','audio2'):
            for ci,c in enumerate(cands):
                for k in range(NTRY):
                    aw=np.array(eng.tts.tts(text=c, speaker_wav=REF[col], language='zh-cn'),
                                dtype=np.float32)
                    a=np.abs(aw); v=a>0.012
                    if not v.any(): continue
                    i0,i1=int(np.nonzero(v)[0][0]), int(np.nonzero(v)[0][-1])
                    seg=aw[max(0,i0-int(SR*0.03)):min(len(aw), i1+int(SR*0.08))]
                    if len(seg) >= SR*0.25:
                        sf.write(f"{TMP}/{t['id']}_{col}_{ci}_{k}.wav", seg, SR)
        print(f"  {ch} ← {' '.join(cands)}", flush=True)
    json.dump(plan, open('finetune_data/_wordapp/homo_plan.json','w',encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f"\n{len(targets)}개 후보 생성 끝")
else:
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p, marks=False):
        s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                           temperature=[0.0,0.2], vad_filter=False,
                           word_timestamps=marks)
        segs=list(s)
        txt=''.join(CJK.findall(t2s.convert(''.join(z.text for z in segs))))
        if not marks: return txt
        out=[]
        for z in segs:
            for w in (z.words or []):
                cs=''.join(CJK.findall(t2s.convert(w.word)))
                if not cs: continue
                st=(w.end-w.start)/len(cs)
                for i,c in enumerate(cs): out.append((c, w.start+st*i, w.start+st*(i+1)))
        return txt, out
    ok, left = [], []
    only=None
    if ROUND2:
        try: only=set(json.load(open('finetune_data/_wordapp/homo_left.json',encoding='utf-8')))
        except Exception: only=None
    for t in targets:
        ch=t['chinese']; want=syl(ch)
        for col in ('audio1','audio2'):
            if only is not None and f"{ch}/{col[-1]}" not in only: continue
            best=None
            for p in sorted(glob.glob(f"{TMP}/{t['id']}_{col}_*.wav")):
                h, tl = hear(p, marks=True)
                if not h: continue
                cutat=None
                if syl(h)!=want:
                    # '张卡' 처럼 뒤에 군더더기가 붙었으면 그 자리에서 끊는다
                    if len(h)>1 and syl(h[0])==want and len(tl)>1:
                        cutat=tl[1][1]
                    else:
                        continue
                seg0,sr0=sf.read(p)
                if cutat is not None:
                    seg0=seg0[:min(len(seg0), int((cutat+0.03)*sr0))]
                    if len(seg0)/sr0 < 0.32: continue
                d=len(seg0)/sr0
                if 0.35 <= d <= 1.6 and (best is None or d>best[0]):
                    best=(d,p,h,seg0,sr0)
            if best:
                d,p,h,seg,sr=best
                f=min(int(sr*0.05), len(seg)//4)
                if f>1: seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
                sf.write(t[col], seg, sr)
                ok.append(f"{ch}/{col[-1]}")
                print(f"  ✓ {ch} {col} ← '{h}' {d:.2f}s", flush=True)
            else:
                left.append(f"{ch}/{col[-1]}")
    con.commit()
    json.dump(left, open('finetune_data/_wordapp/homo_left.json','w',encoding='utf-8'),
              ensure_ascii=False)
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    print(f"\n통째로 만든 것 {len(ok)}개 · 못 한 것 {len(left)}개")
    if left: print("  " + ' '.join(left))
