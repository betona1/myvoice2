# -*- coding: utf-8 -*-
"""너무 짧게 잘린 낱글자 음성을 넉넉하게 다시 뜬다.
   받아적기가 알려 주는 글자 끝시각은 실제보다 이르다 — 그대로 끊으면 소리가 덜 끝난
   채로 잘려 '발음하다 마는' 소리가 된다(些 0.14초).
   ① 예문 여럿 중 그 글자가 가장 길게 발음된 것을 고르고
   ② 앞뒤로 조금 넉넉히 잡되 뒤는 다음 글자 첫머리를 살짝만 물게 한다."""
import os, sys, sqlite3, re, json
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel
from pypinyin import pinyin as _py, Style
import opencc
t2s=opencc.OpenCC('t2s'); CJK=re.compile(r'[一-鿿]')
MIN_D, WANT_D = 0.20, 0.26
def han(x): return ''.join(CJK.findall(t2s.convert(x or '')))
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")

def marks(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, temperature=[0.0],
                       vad_filter=False, word_timestamps=True)
    out=[]
    for z in s:
        for w in (z.words or []):
            cs=han(w.word)
            if not cs: continue
            st=(w.end-w.start)/len(cs)
            for i,c in enumerate(cs): out.append((c, w.start+st*i, w.start+st*(i+1)))
    return out

con=sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory=sqlite3.Row
short=[]
for r in con.execute("""SELECT chinese,audio1,audio2 FROM words
                        WHERE wordset IN ('양사','의성의태') AND COALESCE(excluded,0)=0"""):
    for c in ('audio1','audio2'):
        p=r[c]
        if p and os.path.exists(p) and len(CJK.findall(r['chinese'])) <= 3:
            short.append((r['chinese'], c))
need={}
for ch,c in short: need.setdefault(ch,set()).add(c)
print(f"다시 뜰 낱말 {len(need)}개 / 파일 {len(short)}개\n", flush=True)

fixed, left = [], []
for ch, cols in sorted(need.items()):
    r=con.execute("SELECT id,audio1,audio2 FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    exs=con.execute("SELECT chinese,audio1,audio2 FROM word_examples WHERE word_id=? ORDER BY seq",(r['id'],)).fetchall()
    want=syl(ch)
    for col in sorted(cols):
        best=None
        for e in exs:
            src=e[col] or e['audio1'] or e['audio2']
            if not (src and os.path.exists(src)): continue
            txt=han(e['chinese']); k=txt.find(ch)
            if k<0: continue
            nx=txt[k+len(ch):k+len(ch)+1]
            if want[-1].endswith('3') and nx and syl(nx)[0].endswith('3'): continue   # 변조 피하기
            tl=marks(src); s=''.join(x[0] for x in tl); j=s.find(ch)
            if j < 0 and len(s) == len(txt):
                j = k          # 받아적기가 글자를 다르게 적었어도(唉→哎) 글자 수가 같으면 자리로 맞춘다
            if j<0 or j+len(ch)>len(tl): continue
            a, b = tl[j][1], tl[j+len(ch)-1][2]
            span = b-a
            if best is None or span > best[0]: best=(span, src, a, b, tl, j, e['chinese'])
        if not best: left.append(f"{ch}/{col[-1]}"); continue
        span, src, a, b, tl, j, exz = best
        w,sr=sf.read(src)
        if w.ndim>1: w=w.mean(axis=1)
        nxt = tl[j+len(ch)][1] if j+len(ch) < len(tl) else None
        lp = 0.025                                   # 앞: 첫소리 터짐을 놓치지 않게
        # 뒤: 소리의 여운까지 담되, 다음 글자를 물지 않는다.
        #     억지로 늘리면 다음 음절 첫머리가 딸려 와 '뚝' 끊긴 소리가 된다.
        hard = (nxt + 0.03) if nxt is not None else (b + 0.14)
        i0=max(0,int((a-lp)*sr)); i1=min(len(w), int(min(b+0.12, hard)*sr))
        if (i1-i0)/sr < WANT_D:
            i1=min(len(w), int(hard*sr), i0+int(WANT_D*sr))
        seg=np.asarray(w[i0:i1],dtype=np.float32).copy()
        d=len(seg)/sr
        if d < MIN_D: left.append(f"{ch}/{col[-1]}({d:.2f}s)"); continue
        # 소리가 한창일 때 끊기면 '뚝' 하고 잘린 느낌이 난다 → 꼬리를 길게 재운다
        fi=min(int(sr*0.015), len(seg)//5)
        fo=min(int(sr*0.07),  len(seg)//3)
        if fi>1: seg[:fi]*=np.linspace(0,1,fi)
        if fo>1: seg[-fo:]*=np.cos(np.linspace(0,np.pi/2,fo))**2
        sf.write(r[col], seg, sr)
        fixed.append(f"{ch}/{col[-1]}")
        print(f"  ✓ {ch} {col} ← {exz}  {d:.2f}s (전 {span:.2f}s)", flush=True)
con.commit()
print(f"\n다시 뜬 것 {len(fixed)}개 · 못 한 것 {len(left)}개")
if left: print("  " + ' '.join(left))
