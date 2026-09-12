# -*- coding: utf-8 -*-
"""표본 5개마다 가장 깨끗한 8초를 뽑아 두 번째 기준 후보를 만든다.
   XTTS 는 speaker_wav 에 여러 파일을 줄 수 있어, 서로 다른 문장을 섞으면
   한 토막만 줄 때보다 목소리가 덜 흔들린다."""
import os, glob
os.chdir('/app')
import numpy as np, soundfile as sf, librosa
WIN=8.0
os.makedirs('voice_samples/male_new', exist_ok=True)
for p in sorted(glob.glob('temp/voicesample/s*.wav')):
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    dur=len(w)/sr
    if dur<WIN: continue
    y=librosa.resample(w.astype(np.float32), orig_sr=sr, target_sr=16000)
    f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024)
    hop=1024/4/16000; n=int(WIN*sr); best=None
    for st in np.arange(0, dur-WIN, 0.5):
        a=int(st*sr); seg=w[a:a+n]
        quiet=float(np.mean(np.abs(seg)<0.008))
        v=vo[int(st/hop):int((st+WIN)/hop)]
        voiced=float(np.nanmean(v)) if len(v) else 0
        sc=voiced*2-quiet*2
        if best is None or sc>best[0]: best=(sc,st)
    st=best[1]; seg=w[int(st*sr):int((st+WIN)*sr)]
    seg=seg/(np.max(np.abs(seg))+1e-9)*0.9
    out=f"voice_samples/male_new/{os.path.basename(p)}"
    sf.write(out, seg, sr)
    print(f"  {out}  ({st:.1f}초부터 {WIN:.0f}초)")
