# -*- coding: utf-8 -*-
"""음성이 거친(쉰 소리) 것을 점수로 찾아낸다.
   받아적기는 「무슨 말인가」만 본다 — 목소리가 떨리는지 갈라지는지는 못 가린다.
   그래서 소리를 직접 잰다: 목청 울림 · 맑기 · 음높이 흔들림.
   ⚠️ 사람이 짚어 준 것들(妈妈·什么·很)이 모두 -1 아래였다. 그 언저리를 잣대로 삼는다."""
import os, re, sys, json, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, librosa
CJK=re.compile(r'[一-鿿]')
LV=int(sys.argv[1]) if len(sys.argv)>1 else 0     # 0=전체, 1~6=그 급수만
def score(p):
    try: y,sr=librosa.load(p, sr=16000)
    except Exception: return None
    if len(y) < sr*0.2: return None
    f0,vo,_=librosa.pyin(y, fmin=70, fmax=400, sr=sr, frame_length=1024)
    voiced=float(np.nanmean(vo)) if vo is not None else 0
    flat=float(np.mean(librosa.feature.spectral_flatness(y=y)))
    f=f0[~np.isnan(f0)]
    jit=float(np.mean(np.abs(np.diff(f)))/(np.mean(f)+1e-9)) if len(f)>4 else 1
    return voiced*2.0 - flat*6.0 - jit*3.0
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
q="SELECT chinese,pinyin,hsk,audio1,audio2 FROM words WHERE COALESCE(excluded,0)=0"
if LV: q+=f" AND hsk={LV}"
rows=con.execute(q).fetchall()
out=[]; n=0
for r in rows:
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        s=score(p); n+=1
        if s is not None:
            # ⚠️ 점수를 그냥 0 으로 자르면 안 된다 — 낱말이 길수록 점수가 낮게 나온다
            #    (음절이 바뀔 때마다 음높이가 흔들리는 것을 흠으로 세기 때문).
            #    그래서 글자 수를 함께 적어 두고, 같은 길이끼리 견준다.
            out.append([r['chinese'], r['pinyin'], c, round(s,2), r['hsk'] or 0,
                        len(CJK.findall(r['chinese'] or ''))])
    if n and n%400==0: print(f"   {n}개 잼…", flush=True)
# 같은 글자 수끼리 모아 가운뎃값을 내고, 거기서 크게 처지는 것만 고른다
import statistics, collections
byLen=collections.defaultdict(list)
for x in out: byLen[x[5]].append(x[3])
med={k: statistics.median(v) for k,v in byLen.items()}
sd ={k: (statistics.pstdev(v) or 1) for k,v in byLen.items()}
for x in out: x.append(round((x[3]-med[x[5]])/sd[x[5]], 2))    # 또래보다 얼마나 처지나
rough=[x for x in out if x[6] <= -1.5]
rough.sort(key=lambda x: x[6])
print(f"\n음성 {n}개")
print("  글자 수별 가운뎃값:", {k: round(v,2) for k,v in sorted(med.items())})
print(f"  또래보다 크게 처지는 것 {len(rough)}개")
for x in rough[:25]:
    print(f"  {x[0]:6s} ({x[1]:12s}) {'남' if x[2]=='audio1' else '여'}  점수 {x[3]:6.2f}  또래대비 {x[6]:5.2f}")
out=rough
json.dump(out, open('finetune_data/_wordapp/rough.json','w',encoding='utf-8'), ensure_ascii=False)
