# -*- coding: utf-8 -*-
import os, re, hashlib, numpy as np, soundfile as sf
os.chdir('/app'); SR=24000
TXT=('现在是二零二六年九月八号，星期二，上午十点半。我住在北京市朝阳区。电话号码是一、三、八，六、六、七、九，四、五、二、零。'
     '我今年三十五岁，在一家公司工作了七年。每天早上六点四十起床，七点二十出门，八点到公司。下班以后有时候去跑步，跑三公里大概要二十五分钟。')
sents=re.findall(r'[^。]*。', TXT); C='outputs/chinese/voicepick/_sent'; os.makedirs(C, exist_ok=True)
for fn in os.listdir('outputs/chinese/voicepick'):
    m=re.match(r'^긴글(·차분|·원본)?_(남|여)_new\.wav$', fn)
    if not m: continue
    suf, sex = m.group(1) or '', m.group(2)
    w,_=sf.read(f'outputs/chinese/voicepick/{fn}'); w=np.asarray(w,dtype=np.float32)
    z=(np.abs(w)==0).astype(np.int8); segs=[]; st=None; run=0
    for i,v in enumerate(z):
        if v: run+=1
        else:
            if run>=int(SR*0.4) and st is not None: segs.append((st,i-run)); st=i
            elif st is None: st=i
            run=0
    if st is not None and len(w)-st>SR*0.3: segs.append((st,len(w)))
    if len(segs)!=len(sents): print(f'  {fn}: 조각 {len(segs)}≠{len(sents)} — 건너뜀'); continue
    for si,(a,b) in enumerate(segs):
        ck=f"{C}/{sex}{suf}_{si}_{hashlib.md5(sents[si].encode()).hexdigest()[:8]}.wav"
        sf.write(ck, w[a:b], SR)
    print(f'  {fn}: 문장 {len(segs)}개 저장')
