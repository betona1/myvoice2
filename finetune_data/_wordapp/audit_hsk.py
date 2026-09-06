# -*- coding: utf-8 -*-
"""HSK 낱말 음성 전수검사.
   ⚠️ 받아적기는 홀로 떨어진 한 음절을 못 가린다 — 문맥에서 제대로 받아적히던 소리도
      잘라 놓으면 빈칸이나 헛것('请不吝点赞订阅')이 된다. 직접 확인했다.
      그래서 못 알아들으면 **뒤에 이미 검증된 소리를 붙여** 문장처럼 만들어 다시 묻는다.
   글자가 그대로 들리면 통과. 다르면 병음(성조까지)이 같은지 본다 — 동음이자는 괜찮다.
   중간에 끊겨도 이어서 할 수 있게 한 급수를 마칠 때마다 적어 둔다."""
import os, re, sys, json, sqlite3, time
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel
from pypinyin import pinyin as _py, Style
import opencc
t2s=opencc.OpenCC('t2s'); CJK=re.compile(r'[一-鿿]')
def han(x): return ''.join(CJK.findall(t2s.convert(x or '')))
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
LEVELS=[int(a) for a in sys.argv[1:]] or [1,2,3,4,5,6]
OUT='/app/finetune_data/_wordapp/audit_hsk.json'
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
def hear(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                       temperature=[0.0,0.2], vad_filter=False)
    return han(''.join(z.text for z in s))

con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
TAILZ='一张纸'                                   # 꼬리로 붙일, 이미 검증된 소리
tr=con.execute("SELECT audio1 FROM word_examples WHERE chinese=?", (TAILZ,)).fetchone()
tail,tsr=sf.read(tr['audio1'])
if tail.ndim>1: tail=tail.mean(axis=1)
def hear_ctx(path):
    w,sr=sf.read(path)
    if w.ndim>1: w=w.mean(axis=1)
    if sr!=tsr: return None
    j=np.concatenate([np.asarray(w,dtype=np.float32),
                      np.zeros(int(sr*0.28),dtype=np.float32),
                      np.asarray(tail,dtype=np.float32)])
    p2='/tmp/_hctx.wav'; sf.write(p2,j,sr)
    h=hear(p2); k=h.rfind(TAILZ)
    return h[:k] if k>0 else (h[:-len(TAILZ)] if h.endswith(TAILZ) else None)

res=json.load(open(OUT,encoding='utf-8')) if os.path.exists(OUT) else {"bad":[], "done":[], "checked":0}
t0=time.time()
for lv in LEVELS:
    if lv in res["done"]:
        print(f"HSK {lv}급 — 이미 마침", flush=True); continue
    rows=con.execute("""SELECT id,chinese,pinyin,audio1,audio2,audio_say FROM words
                        WHERE hsk=? AND COALESCE(excluded,0)=0 ORDER BY seq,id""",(lv,)).fetchall()
    n=bad=0
    for i,r in enumerate(rows,1):
        ch=r['chinese']; say=(r['audio_say'] or '').strip()
        tgt=say or ch
        want=syl(tgt)
        for c in ('audio1','audio2'):
            p=r[c]
            if not (p and os.path.exists(p)): 
                res["bad"].append([lv,ch,c,tgt,'(파일없음)',0]); bad+=1; continue
            n+=1
            h=hear(p)
            if h==tgt or syl(h)==want: continue        # 글자가 같거나, 소리가 같으면 통과
            h2=hear_ctx(p)                             # 홀로 못 가릴 때는 꼬리를 붙여 다시
            if h2 is not None and (h2==tgt or syl(h2)==want): continue
            d=len(sf.read(p)[0])/sf.read(p)[1]
            res["bad"].append([lv,ch,c,tgt,h or (h2 or ''),round(d,2)]); bad+=1
        if i%100==0:
            el=time.time()-t0
            print(f"   HSK{lv} {i}/{len(rows)}  어긋남 {bad}  ({el/60:.0f}분)", flush=True)
            res["checked"]=n; json.dump(res, open(OUT,'w',encoding='utf-8'), ensure_ascii=False)
    res["done"].append(lv); res["checked"]=res.get("checked",0)+n
    json.dump(res, open(OUT,'w',encoding='utf-8'), ensure_ascii=False)
    print(f"■ HSK {lv}급: 낱말 {len(rows)} · 음성 {n} · 어긋남 {bad} "
          f"({bad/max(1,n)*100:.1f}%)", flush=True)
tot=len(res["bad"])
print(f"\n검사한 음성 {res['checked']}개 · 어긋난 것 {tot}개  ({(time.time()-t0)/60:.0f}분)")
