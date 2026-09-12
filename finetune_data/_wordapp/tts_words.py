# -*- coding: utf-8 -*-
"""words 테이블 전용 TTS 배치 (audio1=김준용 / audio2=수민).
기존 tts_batch_hsk.py는 chinese_cards 기준이라 words에만 있는 신규 HSK 어휘를 못 잡는다."""
import sys, os, re, time, uuid, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import numpy as np, soundfile as sf
import main as M
from tts.engine import get_engine

DB='database/voices.db'; OUT='outputs/chinese/words'
LOG='/app/finetune_data/_wordapp/tts_words.log'
VOICES=[('김준용','voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav','audio1'),
        ('수민','voice_samples/2a75fe0f_모바일도서검색기수민.wav','audio2')]

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    open(LOG,'a',encoding='utf-8').write(line+'\n')

def gen(engine, text, ref, path):
    t=M.preprocess_tts_text(text,'zh-cn')
    if len(t)>15:
        chunks=[c.strip() for c in re.split(r'[。，！？、；]', t) if c.strip()]
        if len(chunks)>1:
            wavs=[]
            for c in chunks:
                wavs.append(np.array(engine.tts.tts(text=c, speaker_wav=ref, language='zh-cn'),dtype=np.float32))
                wavs.append(np.zeros(int(24000*0.12),dtype=np.float32))
            sf.write(path, np.concatenate(wavs), 24000); return
    sf.write(path, np.array(engine.tts.tts(text=t, speaker_wav=ref, language='zh-cn'),dtype=np.float32), 24000)

os.makedirs(OUT, exist_ok=True)
con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
engine=None
for vname, ref, col in VOICES:
    rows=con.execute(f"SELECT id,chinese FROM words WHERE ({col} IS NULL OR {col}='') ORDER BY seq").fetchall()
    log(f"── {vname}({col}): 대상 {len(rows)}건")
    if not rows: continue
    if engine is None:
        log("엔진 로딩..."); engine=get_engine(); log("엔진 준비 완료")
    ok=fail=0; t0=time.time()
    for i,(wid,ch) in enumerate(rows,1):
        try:
            p=f"{OUT}/{col}_{wid}_{uuid.uuid4().hex[:6]}.wav"
            gen(engine, ch, ref, p)
            con.execute(f"UPDATE words SET {col}=? WHERE id=?", (p,wid)); con.commit(); ok+=1
        except Exception as e:
            fail+=1; log(f"  실패 id={wid} {ch[:10]} — {type(e).__name__}: {str(e)[:60]}")
        if i%50==0:
            el=time.time()-t0
            log(f"  {i}/{len(rows)} 성공{ok} 실패{fail} ETA {(len(rows)-i)*el/i/60:.0f}분")
    log(f"── {vname} 완료: 성공 {ok} / 실패 {fail}")
log("전체 완료")
con.close()
