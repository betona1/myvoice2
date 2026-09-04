# -*- coding: utf-8 -*-
"""낱글자 음성 전수 검사 (고친 판).
   받아적기는 홀로 떨어진 한 음절을 못 가린다 — 빈칸이거나 '请不吝点赞订阅' 같은 헛것이 나온다.
   문맥에서 제대로 받아적히던 소리도 잘라 놓으면 빈칸이 됐다(직접 확인).
   그래서 못 가릴 때는 **뒤에 이미 검증된 소리를 붙여** 문장처럼 만들어 다시 묻는다.
   그러면 앞머리를 제대로 받아적는다."""
import os, re, sys, sqlite3, json
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel
from pypinyin import pinyin as _py, Style
import opencc
t2s=opencc.OpenCC('t2s'); CJK=re.compile(r'[一-鿿]')
def han(x): return ''.join(CJK.findall(t2s.convert(x or '')))
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
def hear(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                       temperature=[0.0,0.2], vad_filter=False)
    return han(''.join(z.text for z in s))

con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
# 꼬리로 쓸 소리 — 이미 검증된 예문
TAILZ='一张纸'
tr=con.execute("SELECT audio1 FROM word_examples WHERE chinese=?", (TAILZ,)).fetchone()
tail, tsr = sf.read(tr['audio1'])
if tail.ndim>1: tail=tail.mean(axis=1)

def hear_ctx(path):
    """홀로는 못 알아들으니, 뒤에 아는 소리를 붙여 문장처럼 만들어 묻는다."""
    w, sr = sf.read(path)
    if w.ndim>1: w=w.mean(axis=1)
    if sr != tsr: return None
    joined=np.concatenate([np.asarray(w,dtype=np.float32),
                           np.zeros(int(sr*0.28),dtype=np.float32),
                           np.asarray(tail,dtype=np.float32)])
    p2='/tmp/_ctx.wav'; sf.write(p2, joined, sr)
    h=hear(p2)
    k=h.rfind(TAILZ)
    return h[:k] if k>0 else (h[:-len(TAILZ)] if h.endswith(TAILZ) else None)

bad=[]; n=0; rescued=0
for r in con.execute("""SELECT chinese,pinyin,audio1,audio2,audio_say FROM words
                        WHERE wordset IN ('양사','의성의태') AND COALESCE(excluded,0)=0
                        ORDER BY chinese"""):
    ch=r['chinese']
    if len(CJK.findall(ch))!=1: continue
    # 홀로 못 읽히는 양사는 「一杯」처럼 수사를 붙여 들려준다 — 그때는 그 낱말이 정답이다
    # audio_say 는 낱말 단위라, 한쪽 목소리만 「一杯」로 바꾼 경우가 있다.
    # 낱글자로 들리든 「一杯」로 들리든 옳은 소리이므로 둘 다 받는다.
    say = (r['audio_say'] or '').strip()
    # 수사는 무엇을 붙였든(一/三/两/这/几) 그 양사를 옳게 읽은 것이다 —
    # audio_say 를 낱말 하나에만 적어 둬서 목소리마다 다를 수 있다.
    wants = [syl(ch)] + [syl(n+ch) for n in '一三两这几四五']
    tgt  = (say + '/' if say else '') + ch
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): continue
        n+=1
        d=len(sf.read(p)[0])/sf.read(p)[1]
        h=hear(p)
        if syl(h) in wants: continue
        h2=hear_ctx(p)                         # 홀로는 못 가린다 → 꼬리를 붙여 다시
        if h2 is not None and syl(h2) in wants:
            rescued+=1; continue
        bad.append([ch,c,tgt,h,h2 or '',round(d,2)])
        print(f"  ✗ {ch} {c} [{tgt}]  홀로:'{h}'  꼬리붙여:'{h2 or ''}'  {d:.2f}s", flush=True)
print(f"\n낱글자 음성 {n}개 · 꼬리를 붙여 되살린 것 {rescued}개 · 그래도 어긋난 것 {len(bad)}개")
json.dump(bad, open('finetune_data/_wordapp/audit_bad.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=1)
