# -*- coding: utf-8 -*-
"""접속사 음성을 고친다.
   ⚠️ 까닭: 「先…再…」처럼 가운뎃점이 든 표기를 그대로 읽히면 XTTS 가 거기서 말을 끊어
      뒷말이 빠진다. 읽을 한자만 뽑아 「先，再」로 들려준다 (쉼표라 사이가 자연스럽다).
   읽은 글자는 audio_say 에 남긴다. 10GB 카드라 make / verify 두 단계."""
import os, re, sys, glob, shutil, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
sys.path.insert(0,'/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from zhjudge import judge

PHASE = sys.argv[1] if len(sys.argv)>1 else 'make'
N  = int(os.environ.get('N_CAND','5'))
SR = 24000
REF={'audio1':'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav',
     'audio2':'voice_samples/2a75fe0f_모바일도서검색기수민.wav'}
OUT='outputs/chinese/words'; TMP='outputs/chinese/_conj'
os.makedirs(TMP, exist_ok=True)
HAN=re.compile(r'[一-鿿]+')

def say_of(ch):
    """「不但…还（也）…」→「不但，还」 — 괄호 안 딸림말은 뺀다."""
    s=re.sub(r'（[^）]*）|\([^)]*\)','',ch)
    return '，'.join(HAN.findall(s))

def trim(w, thr=0.012, gap_s=0.45, pad_s=0.06):
    """gap 0.45초 — 쉼표 사이 쉼이 0.3초쯤이라, 그보다 커야 반쪽이 안 잘린다."""
    e=np.abs(w); v=e>thr
    if not v.any(): return None
    gap=int(SR*gap_s); segs=[]; st=None; run=0
    for i,x in enumerate(v):
        if x: st=i if st is None else st; run=0
        elif st is not None:
            run+=1
            if run>=gap: segs.append((st,i-run+1)); st=None; run=0
    if st is not None: segs.append((st,len(w)))
    if not segs: return None
    a,b=segs[0][0],segs[-1][1]; pad=int(SR*pad_s)     # 첫 소리~끝 소리 통째로
    return w[max(0,a-pad):min(len(w),b+pad)]

con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
rows=[r for r in con.execute(
    "SELECT id,chinese,audio1,audio2 FROM words WHERE wordset='접속사' AND COALESCE(excluded,0)=0")
    if '…' in r['chinese'] or '，' in r['chinese']]

if PHASE=='make':
    from tts.engine import get_engine
    eng=get_engine()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    print(f"가운뎃점 든 접속사 {len(rows)}개", flush=True)
    for r in rows:
        say=say_of(r['chinese'])
        if not say: continue
        for col in ('audio1','audio2'):
            for k in range(N):
                aw=np.array(eng.tts.tts(text=say, speaker_wav=REF[col], language='zh-cn'),
                            dtype=np.float32)
                t=trim(aw)
                if t is not None and len(t)>=SR*0.25:
                    sf.write(f"{TMP}/{r['id']}_{col}_{k}.wav", t, SR)
        print(f"   {r['chinese']} ← 「{say}」", flush=True)
    print("\n생성 끝 — 이어서 verify")
else:
    from faster_whisper import WhisperModel
    asr=WhisperModel("large-v3", device="cuda", compute_type="float16")
    def hear(p):
        s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                           temperature=[0.0,0.2], vad_filter=False)
        return ''.join(z.text for z in s).strip()
    fixed=keep=0; still=[]
    for r in rows:
        say=say_of(r['chinese'])
        want=say.replace('，','')
        if not want: continue
        for col in ('audio1','audio2'):
            cur=r[col]
            if cur and os.path.exists(cur) and judge(want, hear(cur))=='ok':
                keep+=1; continue
            got=None
            for p in sorted(glob.glob(f"{TMP}/{r['id']}_{col}_*.wav")):
                if judge(want, hear(p))=='ok': got=p; break
            if got:
                dst=cur if cur else f"{OUT}/{col}_{r['id']}_conj.wav"
                shutil.copy2(got, dst)
                con.execute(f"UPDATE words SET {col}=?, audio_say=? WHERE id=?", (dst, say, r['id']))
                fixed+=1
            else:
                still.append(f"{r['chinese']}({col[-1]})")
    con.commit()
    for f in glob.glob(f'{TMP}/*.wav'): os.remove(f)
    print(f"\n고친 음성 {fixed}개 · 이미 좋던 것 {keep}개 · 아직 안 되는 것 {len(still)}개")
    if still: print('  '+'  '.join(still))
con.close()
