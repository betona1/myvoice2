# -*- coding: utf-8 -*-
"""전수검사에서 어긋난 HSK 음성을 고친다.

   낱말 길이에 따라 길을 나눈다 —
     여러 글자  XTTS 가 대체로 제대로 읽는다. 여러 번 만들어 받아적기로 고르면 된다.
     한 글자    홀로 두면 성조를 뭉갠다(页 yè→耶 yé). 소리가 같은 다른 글자로 읽히거나
                두 음절 낱말 앞자리에 실어 읽히고 뒤를 뗀다.
   ⚠️ 받아적기는 홀로 떨어진 한 음절을 못 가린다 → 짧으면 꼬리에 아는 소리를 붙여 되묻는다.
   ⚠️ 통과한 후보가 없으면 원본을 그대로 둔다 (나쁜 것으로 덮지 않는다).
   10GB 카드라 XTTS 와 Whisper 를 함께 못 올린다 → make / verify 두 판.
"""
import os, re, sys, glob, json, sqlite3, subprocess
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from pypinyin import pinyin as _py, Style
PHASE=sys.argv[1]; SR=24000
NCAND=int(os.environ.get('NCAND','6'))
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP='outputs/chinese/_fixhsk'; os.makedirs(TMP, exist_ok=True)
CJK=re.compile(r'[一-鿿]')
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]

con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
bad=json.load(open('/app/finetune_data/_wordapp/audit_hsk.json',encoding='utf-8'))['bad']
need={}
for lv,ch,col,tgt,heard,dur in bad:
    need.setdefault(ch, {})[col]=tgt

def homophones():
    """소리가 똑같은(성조까지) 홑글자 — 배우는 이가 아는 글자를 앞에 둔다."""
    known={r[0] for r in con.execute("SELECT chinese FROM words WHERE length(chinese)=1")}
    m={}
    for ln in open('database/cedict_full.txt', encoding='utf-8'):
        if ln.startswith('#'): continue
        try:
            sp=ln.split(' ',2)[1]; py=ln.split('[',1)[1].split(']',1)[0]
        except Exception: continue
        if len(sp)!=1 or not CJK.match(sp): continue
        k=py.lower().replace(' ','')
        m.setdefault(k, [])
        if sp not in m[k]: m[k].append(sp)
    for k in m: m[k].sort(key=lambda c: (c not in known,))
    return m

if PHASE=='make':
    HOM=homophones()
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    plan={}
    for i,(ch,cols) in enumerate(sorted(need.items()),1):
        r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        if len(CJK.findall(ch))==1:
            want=syl(ch)[0]
            texts=[ch]+[c for c in HOM.get(want,[]) if c!=ch][:6]      # 제 글자부터, 그다음 동음자
        else:
            texts=[ch]                                                # 여러 글자는 제 글자로 여러 번
        plan[ch]=texts
        for col in cols:
            for ti,t in enumerate(texts):
                for k in range(NCAND if len(texts)==1 else 3):
                    aw=np.array(eng.tts.tts(text=t, speaker_wav=REF[col], language='zh-cn'),
                                dtype=np.float32)
                    a=np.abs(aw); v=a>0.012
                    if not v.any(): continue
                    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
                    seg=aw[max(0,i0-int(SR*0.03)):min(len(aw), i1+int(SR*0.07))]
                    if len(seg)>=SR*0.25:
                        sf.write(f"{TMP}/{r['id']}_{col}_{ti}_{k}.wav", seg, SR)
        if i%20==0: print(f"   {i}/{len(need)} 만드는 중", flush=True)
    json.dump(plan, open('/app/finetune_data/_wordapp/fix_plan.json','w',encoding='utf-8'),
              ensure_ascii=False)
    print(f"\n{len(need)}개 낱말 후보 생성 끝")
else:
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                           temperature=[0.0,0.2], vad_filter=False)
        return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
    TAILZ='一张纸'
    tr=con.execute("SELECT audio1 FROM word_examples WHERE chinese=?",(TAILZ,)).fetchone()
    tail,tsr=sf.read(tr['audio1'])
    if tail.ndim>1: tail=tail.mean(axis=1)
    def hear_ctx(path):
        w,sr=sf.read(path)
        if w.ndim>1: w=w.mean(axis=1)
        if sr!=tsr: return None
        j=np.concatenate([np.asarray(w,dtype=np.float32),
                          np.zeros(int(sr*0.28),dtype=np.float32),
                          np.asarray(tail,dtype=np.float32)])
        sf.write('/tmp/_fctx.wav', j, sr)
        h=hear('/tmp/_fctx.wav'); k=h.rfind(TAILZ)
        return h[:k] if k>0 else (h[:-len(TAILZ)] if h.endswith(TAILZ) else None)
    ok=keep=0; still=[]
    for i,(ch,cols) in enumerate(sorted(need.items()),1):
        r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        want=syl(ch)
        for col in cols:
            got=None
            for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
                h=hear(p)
                good = (h==ch or syl(h)==want)
                if not good:
                    h2=hear_ctx(p)                       # 짧으면 홀로는 못 가린다
                    good = h2 is not None and (h2==ch or syl(h2)==want)
                if good:
                    d=len(sf.read(p)[0])/sf.read(p)[1]
                    if got is None or d>got[1]: got=(p,d)
            if got:
                d0,sr0=sf.read(got[0])
                f=min(int(sr0*0.04), len(d0)//5)
                if f>1: d0[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
                sf.write(r[col], d0, sr0); ok+=1
            else:
                still.append(f"{ch}/{col[-1]}"); keep+=1
        if i%20==0: print(f"   {i}/{len(need)}  고침 {ok} · 못 고침 {keep}", flush=True)
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    json.dump(still, open('/app/finetune_data/_wordapp/fix_left.json','w',encoding='utf-8'),
              ensure_ascii=False)
    print(f"\n고친 것 {ok}개 · 원본 그대로 둔 것 {keep}개")
