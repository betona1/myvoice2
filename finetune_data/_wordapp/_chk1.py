import os, re, sys, sqlite3
sys.path.insert(0,'/app'); os.chdir('/app')
import soundfile as sf
from faster_whisper import WhisperModel
import opencc
t2s=opencc.OpenCC('t2s'); CJK=re.compile(r'[一-鿿]')
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
def hear(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                       temperature=[0.0,0.2], vad_filter=False)
    return ''.join(CJK.findall(t2s.convert(''.join(z.text for z in s))))
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
for r in con.execute("""SELECT chinese,pinyin,hsk,audio1,audio2 FROM words
                        WHERE COALESCE(excluded,0)=0 AND pinyin LIKE 'cái liào%'
                           OR chinese IN ('材料','才料')"""):
    line=f"  {r['chinese']} ({r['pinyin']}) HSK{r['hsk'] or 0}"
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)): line+=f"  {c[-1]}:없음"; continue
        w,sr=sf.read(p); h=hear(p)
        line+=f"  {c[-1]}:'{h}'({len(w)/sr:.2f}s){'' if h==r['chinese'] else ' ←'}"
    print(line)
