# -*- coding: utf-8 -*-
"""음성이 '덜 끝난 채' 잘렸는지 본다.
   길이만 봐서는 모자란다 — 끝이 뚝 끊긴 소리는 마지막 순간에도 여전히 크다.
   자연스레 끝난 소리는 꼬리가 잦아든다. 그래서 두 가지를 함께 본다.
     ① 글자 수에 견줘 너무 짧은가 (한 음절 0.22초 잡음)
     ② 마지막 30ms 가 아직 큰가 (평균의 절반 넘게) → 잘린 것"""
import os, re, sqlite3, sys
os.chdir('/app')
import numpy as np, soundfile as sf
CJK=re.compile(r'[一-鿿]')
PER = 0.22                       # 한 음절에 넉넉잡아 필요한 길이

def look(path):
    w, sr = sf.read(path)
    if w.ndim > 1: w = w.mean(axis=1)
    if len(w) < sr*0.02: return None
    d = len(w)/sr
    a = np.abs(w)
    tail = a[-int(sr*0.03):]
    body = a[a > a.max()*0.05]                    # 잠잠한 데를 뺀 알맹이
    ratio = float(tail.mean() / (body.mean() + 1e-9)) if len(body) else 0.0
    return d, ratio

con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
short, cut = [], []
rows=con.execute("""SELECT chinese,wordset,audio1,audio2 FROM words
                    WHERE wordset IN ('양사','접속사','의성의태') AND COALESCE(excluded,0)=0
                    ORDER BY wordset,chinese""").fetchall()
for r in rows:
    n = len(CJK.findall(r['chinese'])) or 1
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        v=look(p)
        if not v: continue
        d, ratio = v
        if d < PER*n*0.8:  short.append((r['chinese'], r['wordset'], c, round(d,2), n))
        elif ratio > 0.5:  cut.append((r['chinese'], r['wordset'], c, round(d,2), round(ratio,2)))
print(f"낱말 {len(rows)}개 검사")
print(f"\n■ 글자 수에 견줘 짧은 것 {len(short)}개")
for ch,ws,c,d,n in short: print(f"   {ch} ({ws}) {c} {d}s / {n}자")
print(f"\n■ 끝이 뚝 끊긴 것 {len(cut)}개")
for ch,ws,c,d,rt in cut: print(f"   {ch} ({ws}) {c} {d}s 끝세기 {rt}")

es=con.execute("SELECT e.chinese,e.audio1,e.audio2 FROM word_examples e").fetchall()
ec=[]
for e in es:
    n=len(CJK.findall(e['chinese'])) or 1
    for c in ('audio1','audio2'):
        p=e[c]
        if not (p and os.path.exists(p)): continue
        v=look(p)
        if not v: continue
        d, ratio = v
        if d < PER*n*0.6 or ratio > 0.5: ec.append((e['chinese'], c, round(d,2), round(ratio,2), n))
print(f"\n■ 예문 {len(es)}개 중 수상한 것 {len(ec)}개")
for zh,c,d,rt,n in ec[:40]: print(f"   {zh} {c} {d}s/{n}자 끝세기 {rt}")
