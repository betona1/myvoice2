# -*- coding: utf-8 -*-
"""끝내 안 되는 낱글자: 두 음절 낱말의 **앞자리**에 두어 읽히고 뒤를 떼어 낸다.
   XTTS 는 글자를 홀로 두면 성조를 뭉개지만(页 yè→耶 yé), 낱말 안에서는 제대로 읽는다.
   앞자리에 두는 게 요점이다 — 앞에 아무것도 없으니 '나시에'처럼 딴 글자가 섞일 수 없다.
   뒷자리 음절은 3성을 피했다(3성+3성은 앞 글자가 2성으로 바뀌는 변조가 일어난다).
   자르는 자리는 받아적기가 알려 준 언저리에서 '소리의 골짜기'를 찾아 정하고,
   자른 뒤 다시 들어 그 음절 하나로 들리는지 확인한다. 0.42초 미만은 버린다."""
import os, re, sys, glob, json, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from pypinyin import pinyin as _py, Style
PHASE=sys.argv[1]; SR=24000; NTRY=6; MIN_D=0.42
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
TMP='outputs/chinese/_carrier'; os.makedirs(TMP, exist_ok=True)
CJK=re.compile(r'[一-鿿]')
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]

# 글자 → 그 글자가 앞에 오는 두 음절 낱말 (뒷자리는 3성을 피한다)
CAR = {
 '些':['些微','些许'], '匹':['匹配','匹夫'], '只':['只身','只言'],
 '场':['场地','场面'], '处':['处所','处方'], '张':['张开','张贴'],
 '把':['把握','把关'], '支':['支持','支付'], '朵':['朵云','朵朵'],
 '条':['条件','条约'], '杯':['杯子','杯盘'], '枝':['枝叶','枝头'],
 '架':['架子','架空'], '盘':['盘子','盘算'], '碗':['碗筷','碗柜'],
 '种':['种子','种类'], '趟':['趟数','趟马'], '页':['页面','页数'],
}
con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
left=json.load(open('finetune_data/_wordapp/homo_left.json',encoding='utf-8'))
need={}
for x in left: need.setdefault(x.split('/')[0], set()).add('audio'+x.split('/')[1])

if PHASE=='make':
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    for ch, cols in sorted(need.items()):
        r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        cars=CAR.get(ch)
        if not (r and cars): print(f"  {ch}: 실을 낱말 없음"); continue
        for col in sorted(cols):
            for wi,word in enumerate(cars):
                for k in range(NTRY):
                    aw=np.array(eng.tts.tts(text=word, speaker_wav=REF[col], language='zh-cn'),
                                dtype=np.float32)
                    a=np.abs(aw); v=a>0.012
                    if not v.any(): continue
                    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
                    seg=aw[max(0,i0-int(SR*0.03)):min(len(aw), i1+int(SR*0.05))]
                    if len(seg) >= SR*0.4:
                        sf.write(f"{TMP}/{r['id']}_{col}_{wi}_{k}.wav", seg, SR)
        print(f"  {ch} ← {' '.join(cars)}", flush=True)
    print("\n실어 읽히기 끝")
else:
    from faster_whisper import WhisperModel
    import opencc
    t2s=opencc.OpenCC('t2s')
    asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
    def hear(p, marks=False):
        s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                           temperature=[0.0,0.2], vad_filter=False, word_timestamps=marks)
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
    ok, still = [], []
    for ch, cols in sorted(need.items()):
        r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
        if not r: continue
        want=syl(ch)
        for col in sorted(cols):
            got=None
            for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
                txt, tl = hear(p, marks=True)
                if len(tl) < 2 or syl(txt[0]) != want: continue   # 앞자리가 그 소리로 읽혔나
                w,sr=sf.read(p)
                if w.ndim>1: w=w.mean(axis=1)
                b=tl[1][1]                                        # 둘째 음절이 시작하는 언저리
                a=np.abs(w); n=max(1,int(sr*0.012))
                k=np.convolve(a, np.ones(n)/n, 'same')
                lo=max(0,int((b-0.09)*sr)); hi=min(len(k), int((b+0.09)*sr))
                ci = lo+int(np.argmin(k[lo:hi])) if hi-lo>8 else int(b*sr)
                seg=np.asarray(w[:ci], dtype=np.float32).copy()
                if len(seg)/sr < MIN_D: continue
                f=min(int(sr*0.05), len(seg)//4)
                if f>1: seg[-f:]*=np.cos(np.linspace(0,np.pi/2,f))**2
                t2=f"{TMP}/_chk.wav"; sf.write(t2, seg, sr)
                if syl(hear(t2)) != want: continue                # 떼어 낸 뒤 다시 확인
                got=(seg, sr, txt, len(seg)/sr); break
            if got:
                seg,sr,txt,d=got
                sf.write(r[col], seg, sr)
                ok.append(f"{ch}/{col[-1]}")
                print(f"  ✓ {ch} {col} ← '{txt}' 에서 앞음절 {d:.2f}s", flush=True)
            else:
                still.append(f"{ch}/{col[-1]}")
    con.commit()
    json.dump(still, open('finetune_data/_wordapp/homo_left.json','w',encoding='utf-8'), ensure_ascii=False)
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    print(f"\n실어 읽혀 떼어 낸 것 {len(ok)}개 · 아직 안 되는 것 {len(still)}개")
    if still: print("  " + ' '.join(still))
