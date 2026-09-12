# -*- coding: utf-8 -*-
"""한 글자 TTS가 왜 헛소리를 붙이는지 — 입력 형태를 바꿔가며 길이를 잰다.
   한 음절은 0.5~0.9초가 정상. 2초를 넘으면 뭔가 덧붙은 것."""
import sys, os, re, time
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf
import main as M
from tts.engine import get_engine

REF = {'남': 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
       '여': 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
CH = ['都', '喝', '好', '矮', '四', '号']
OUT = '/tmp/probe'
os.makedirs(OUT, exist_ok=True)
eng = get_engine()

def synth(text, ref):
    return np.array(eng.tts.tts(text=text, speaker_wav=ref, language='zh-cn'), dtype=np.float32)

def trim(w, sr=24000, thr=0.012, gap_s=0.22, pad_s=0.05):
    """무음으로 끊어지는 구간들 중 '에너지가 가장 큰 덩어리'만 남긴다.
    XTTS 는 한 글자를 주면 진짜 음절 앞뒤로 잡음·군소리를 붙인다.
    앞에서부터 자르면(첫 공백 기준) 앞의 잡음만 남는 경우가 있어 이렇게 고른다."""
    e = np.abs(w)
    voiced = e > thr
    if not voiced.any(): return w
    gap = int(sr * gap_s)
    segs, start, run = [], None, 0
    for i, v in enumerate(voiced):
        if v:
            if start is None: start = i
            run = 0
        else:
            if start is not None:
                run += 1
                if run >= gap:
                    segs.append((start, i - run + 1)); start = None; run = 0
    if start is not None: segs.append((start, len(w)))
    if not segs: return w
    a, b = max(segs, key=lambda s: float(np.sum(e[s[0]:s[1]] ** 2)))
    pad = int(sr * pad_s)
    return w[max(0, a - pad):min(len(w), b + pad)]

print(f"{'글자':^4} {'목소리':^5} {'그대로':>8} {'뒤에。':>8} {'반복':>8} │ {'그대로+정리':>11}")
for ch in CH:
    for vn, ref in REF.items():
        ds = []
        raws = {}
        for label, txt in (('plain', ch), ('dot', ch + '。'), ('rep', f'{ch}，{ch}')):
            w = synth(txt, ref); raws[label] = w
            ds.append(len(w) / 24000)
        t = trim(raws['plain'])
        sf.write(f'{OUT}/{ch}_{vn}_plain.wav', raws['plain'], 24000)
        sf.write(f'{OUT}/{ch}_{vn}_trim.wav', t, 24000)
        print(f"{ch:^4} {vn:^5} {ds[0]:>7.2f}s {ds[1]:>7.2f}s {ds[2]:>7.2f}s │ {len(t)/24000:>10.2f}s")
