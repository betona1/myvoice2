"""낱글자 음성의 앞뒤 잠잠한 구간을 잰다.
   「쉬었다가 소리가 나고 숨넘어가는」 느낌은 앞에 빈 구간이 길거나
   뒤가 갑자기 끊길 때 난다."""
import os, re, sqlite3
os.chdir('/app')
import numpy as np, soundfile as sf
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
def edges(p, thr=0.012):
    w,sr=sf.read(p)
    if w.ndim>1: w=w.mean(axis=1)
    a=np.abs(w); v=a>thr
    if not v.any(): return None
    i0=int(np.nonzero(v)[0][0]); i1=int(np.nonzero(v)[0][-1])
    return len(w)/sr, i0/sr, (len(w)-i1)/sr, float(a[i1-int(sr*0.02):i1+1].mean()/(a[v].mean()+1e-9))
rows=[r for r in con.execute("""SELECT chinese,pinyin,audio1,audio2 FROM words
                                WHERE COALESCE(excluded,0)=0""")
      if len(CJK.findall(r['chinese'] or ''))==1]
bad=[]
for r in rows:
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        e=edges(p)
        if not e: continue
        d, lead, tail, endlvl = e
        if lead>0.12 or tail<0.02 or endlvl>0.6:
            bad.append((r['chinese'], r['pinyin'], c[-1], round(d,2), round(lead,2), round(tail,2), round(endlvl,2)))
print(f"낱글자 음성 {sum(1 for r in rows for c in ('audio1','audio2') if r[c])}개 중 손볼 것 {len(bad)}개")
print("  글자  병음      성별 길이  앞빈틈 뒤빈틈 끝세기")
for x in bad[:24]:
    print(f"  {x[0]:4s} {x[1]:9s} {x[2]}  {x[3]:5.2f} {x[4]:6.2f} {x[5]:6.2f} {x[6]:6.2f}")
for x in bad:
    if x[0]=='很': print(f"\n  很: 길이 {x[3]}초 · 앞빈틈 {x[4]}초 · 뒤빈틈 {x[5]}초 · 끝세기 {x[6]}")
