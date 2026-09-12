# -*- coding: utf-8 -*-
"""녹음한 표본에서 **가장 깨끗한 구간**을 골라 기준 목소리로 삼는다.
   XTTS 는 6~15초짜리 한 토막이면 넉넉하다. 길수록 좋은 게 아니라
   잡음·끊김 없이 고르게 말한 데를 고르는 게 중요하다.
   잣대: 소리가 이어지고(무음 적고), 세기가 고르고, 목청이 잘 울리는 구간."""
import os, glob, sys
os.chdir('/app')
import numpy as np, soundfile as sf, librosa
D='temp/voicesample'
WIN=12.0        # 고를 길이(초)
best_all=[]
for p in sorted(glob.glob(f'{D}/s*.wav')):
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    dur=len(w)/sr
    y=librosa.resample(w.astype(np.float32), orig_sr=sr, target_sr=16000)
    f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=16000, frame_length=1024)
    hop=1024/4/16000
    print(f"{os.path.basename(p)}  {dur:.1f}초  울림 {float(np.nanmean(vo)):.2f}", flush=True)
    step=0.5; best=None
    n=int(WIN*sr)
    if len(w) < n: continue
    for st in np.arange(0, dur-WIN, step):
        a=int(st*sr); b=a+n
        seg=w[a:b]
        e=np.abs(seg)
        quiet=float(np.mean(e<0.008))                 # 무음 비율 — 낮을수록 좋다
        fa=int(st/hop); fb=int((st+WIN)/hop)
        v=vo[fa:fb] if vo is not None else None
        voiced=float(np.nanmean(v)) if v is not None and len(v) else 0
        rms=float(np.sqrt(np.mean(seg**2)))
        even=1.0-float(np.std([np.sqrt(np.mean(seg[i:i+sr//2]**2)) for i in range(0,n-sr//2,sr//2)])/(rms+1e-9))
        score=voiced*2.0 - quiet*2.0 + even*1.0
        if best is None or score>best[0]: best=(score, st, quiet, voiced, even)
    if best:
        sc, st, q, v, ev = best
        print(f"   가장 좋은 구간 {st:.1f}~{st+WIN:.1f}초  점수 {sc:.2f}"
              f" (무음 {q:.2f} 울림 {v:.2f} 고름 {ev:.2f})", flush=True)
        best_all.append((sc, p, st))
best_all.sort(reverse=True)
if best_all:
    sc, p, st = best_all[0]
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    seg=w[int(st*sr):int((st+WIN)*sr)]
    seg=seg/ (np.max(np.abs(seg))+1e-9) * 0.9        # 크기를 고르게
    out='voice_samples/male_ref_new.wav'
    sf.write(out, seg, sr)
    print(f"\n골랐습니다: {os.path.basename(p)} 의 {st:.1f}초부터 {WIN:.0f}초 → {out}")
