# -*- coding: utf-8 -*-
"""소리를 제대로 내는 긴 말을 만든 뒤, 앞부분만 오려내 짧은 단어의 음성으로 쓴다.
   不着 는 zháo 소리를 낼 동음자가 없어서 不着急 을 만들고 急 을 떼어 낸다."""
import os, sys, json
os.chdir('/app'); sys.path.insert(0,'/app')
import numpy as np, soundfile as sf
from tts.engine import get_engine
from faster_whisper import WhisperModel
import sqlite3
SR=24000
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
JOBS=json.load(open('finetune_data/_wordapp/slice_jobs.json',encoding='utf-8'))  # [단어, 만들 말, 남길 음절수]
m=WhisperModel('large-v3',device='cuda',device_index=0,compute_type='float16')
eng=get_engine()
con=sqlite3.connect('file:database/voices.db?mode=ro',uri=True); con.row_factory=sqlite3.Row

def words_of(path):
    segs,_=m.transcribe(path,language='zh',beam_size=5,vad_filter=False,word_timestamps=True)
    return [w for s in segs for w in (s.words or [])]

for word, spoken, keep in JOBS:
    r=con.execute("SELECT audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(word,)).fetchone()
    if not r: print(f"  {word}: DB에 없음"); continue
    print(f"\n{word}  ← '{spoken}' 을 만들어 앞 {keep}자만 남긴다")
    for col in ('audio1','audio2'):
        p=r[col]
        if not p: continue
        got=None
        for _ in range(5):
            aw=np.array(eng.tts.tts(text=spoken, speaker_wav=REF[col], language='zh-cn'),dtype=np.float32)
            sf.write('/tmp/_h.wav',aw,SR)
            ws=words_of('/tmp/_h.wav')
            txt=''.join(w.word for w in ws)
            han=[c for c in txt if '一'<=c<='鿿']
            if len(han)<keep+1: continue          # 뒤에 뗄 게 있어야 한다
            # keep 번째 글자가 끝나는 시각까지 자른다
            n=0; end=None
            for w in ws:
                for ch in w.word:
                    if '一'<=ch<='鿿':
                        n+=1
                        if n==keep: end=w.end
                if end: break
            if not end: continue
            cut=aw[:min(len(aw),int((end+0.14)*SR))]
            if len(cut)<SR*0.5: continue
            sf.write('/tmp/_h2.wav',cut,SR)
            s,_=m.transcribe('/tmp/_h2.wav',language='zh',beam_size=5,vad_filter=False)
            heard=''.join(z.text for z in s).strip()
            got=(cut,heard,txt); break
        if got is None: print(f"   {col}: 실패"); continue
        sf.write(p,got[0],SR)
        print(f"   {col}: {len(got[0])/SR:.2f}초 · 전체 {got[2][:10]!r} → 잘라낸 뒤 {got[1][:12]!r}")
con.close()
