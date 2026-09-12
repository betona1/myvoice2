"""양사·의성의태 낱말 음성을 '성조까지' 맞는지 본다.
   zhjudge 는 배움에 쓰기엔 너그럽다 — 张(zhāng)을 掌(zhǎng)으로,
   한 글자를 더 긴 낱말(张唐)로 스냅해 통과시킨다. 발음 학습에는 그러면 안 된다."""
import sqlite3, os, sys, json, re
sys.path.insert(0,'/app'); os.chdir('/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
from faster_whisper import WhisperModel
from pypinyin import pinyin as _py, Style
import opencc
t2s=opencc.OpenCC('t2s')
asr=WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")
def hear(p):
    s,_=asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                       temperature=[0.0,0.2], vad_filter=False)
    return ''.join(z.text for z in s).strip()
CJK=re.compile(r'[一-鿿]')
def han(x): return ''.join(CJK.findall(t2s.convert(x or '')))
def syl(x): return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
rows=con.execute("""SELECT id,chinese,pinyin,wordset,audio1,audio2 FROM words
                    WHERE wordset IN ('양사','의성의태') AND COALESCE(excluded,0)=0
                    ORDER BY wordset,chinese""").fetchall()
bad=[]
for r in rows:
    want=syl(r['chinese'])
    for c in ('audio1','audio2'):
        p=r[c]
        if not (p and os.path.exists(p)):
            bad.append([r['chinese'],r['wordset'],c,'없음','']); continue
        h=hear(p); g=han(h)
        got=syl(g) if g else []
        if got!=want:
            why='길이' if len(got)!=len(want) else '성조/음'
            bad.append([r['chinese'],r['wordset'],c,h,why])
            print(f"  ✗ {r['chinese']}({r['wordset']}) {c} → '{h}'  {'/'.join(want)} ≠ {'/'.join(got)}", flush=True)
json.dump(bad, open('finetune_data/_wordapp/strict_bad.json','w',encoding='utf-8'),
          ensure_ascii=False, indent=1)
print(f"\n낱말 {len(rows)}개 / 파일 {len(rows)*2}개 중 어긋난 것 {len(bad)}개")
