# -*- coding: utf-8 -*-
"""XTTS 가 단독 낱말에서 h·b 초성을 흘리는 것들(火药→我要, 胡同→無痛).
   문장 안에 넣어 읽히면 제대로 발음하므로, 받아적기의 낱말 시각을 따라
   그 부분만 오려 낸다."""
import os, sys, json
os.chdir('/app'); sys.path.insert(0,'/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge
from faster_whisper import WhisperModel
from tts.engine import get_engine
SR=24000
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
CARRIER=["这个词是{}。","请念一下{}。","我们学{}这个词。","他说的是{}。"]
m=WhisperModel("large-v3",device="cuda",device_index=0,compute_type="float16")
eng=get_engine()

def hear(p, words=False):
    s,_=m.transcribe(p,language='zh',beam_size=5,best_of=5,temperature=[0.0,0.2],
                     vad_filter=False,word_timestamps=words)
    s=list(s)
    return s if words else ''.join(z.text for z in s).strip()

def slice_word(segs, w, wav):
    """받아적은 낱말들 가운데 목표 단어에 해당하는 구간을 찾아 오려 낸다."""
    ws=[x for z in segs for x in (z.words or [])]
    if not ws: return None
    txt=''.join(x.word for x in ws)
    # 목표 글자들이 이어서 나오는 자리를 찾는다
    idx=[]; pos=0
    for i,x in enumerate(ws):
        idx += [i]*len(x.word); pos+=len(x.word)
    for tgt in (w, w[0]+w[-1]):
        j=txt.find(w)
        if j<0: continue
        a,b=idx[j], idx[min(j+len(w)-1, len(idx)-1)]
        st,en=ws[a].start, ws[b].end
        if en-st < 0.15: continue
        s0=max(0,int((st-0.06)*SR)); s1=min(len(wav),int((en+0.10)*SR))
        return wav[s0:s1]
    return None

b=json.load(open('finetune_data/_wordapp/audio_still_bad.json',encoding='utf-8'))
print(f"문장 안에 넣어 읽혀 볼 것 {len(b)}개",flush=True)
fixed=[]; left=[]
for i,x in enumerate(b,1):
    w,p,col=x['ch'],x['path'],x['col']
    got=None
    for c in CARRIER:
        for _ in range(2):
            try:
                aw=np.array(eng.tts.tts(text=c.format(w),speaker_wav=REF[col],language='zh-cn'),dtype=np.float32)
            except Exception:
                continue
            sf.write('/tmp/_c.wav',aw,SR)
            cut=slice_word(hear('/tmp/_c.wav',words=True), w, aw)
            if cut is None or len(cut)<SR*0.2: continue
            sf.write('/tmp/_c2.wav',cut,SR)
            if judge(w,hear('/tmp/_c2.wav'))=='ok': got=cut; break
        if got is not None: break
    if got is None: left.append(x)
    else: sf.write(p,got,SR); fixed.append(f"{w}({col[-1]})")
    if i%10==0: print(f"  {i}/{len(b)} · 고침 {len(fixed)}개",flush=True)
json.dump(left,open('finetune_data/_wordapp/audio_still_bad.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n문장에 넣어 고친 것 {len(fixed)}개 / 여전히 못 고친 것 {len(left)}개")
print("  고침:", '  '.join(fixed))
print("  남음:", '  '.join(f"{x['ch']}({x['col'][-1]})" for x in left))
