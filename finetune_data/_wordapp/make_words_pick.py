# -*- coding: utf-8 -*-
"""견주기 페이지 낱말을 검사 통과한 것만으로 만든다.
   쓰임: make_words_pick.py <성별> <모델> <낱말파일>
   ⚠️ 한 프로세스에 모델 하나만 — 둘을 번갈아 올리면 VRAM 이 조각나 Whisper 가 넘친다.
   실패한 낱말은 <낱말파일>.left 에 남겨 다음 단계(원본 모델)가 이어받는다."""
import os, sys, json
sys.path.insert(0, '/app'); os.chdir('/app')
import soundfile as sf, tts.zh_tts as z
sex, model, wf = sys.argv[1], sys.argv[2], sys.argv[3]
who = model.split(':')[-1]
words = [l.strip() for l in open(wf, encoding='utf-8') if l.strip()]
OUT = 'outputs/chinese/voicepick'
rp = f'{OUT}/report.json'
report = json.load(open(rp, encoding='utf-8')) if os.path.exists(rp) else {}
left = []
def _flush():
    open(wf + '.left', 'w', encoding='utf-8').write('\n'.join(left + words[words.index(t)+1:]))
for t in words:
    _flush()
    w, rep = z.say_word(model, t, ref_dir=f'voice_samples/{who}', tries=int(os.environ.get("TRIES","8")))
    if w is None:
        left.append(t); report[f'{t}_{sex}'] = f'{model}: ✗ {rep}'
        print(f'  {sex} {t:6s} {model:8s} ✗  {rep[-120:]}', flush=True); continue
    sf.write(f'{OUT}/{t}_{sex}_new.wav', w, 24000)
    report[f'{t}_{sex}'] = f'{model}: ✓ {len(w)/24000:.2f}s | {rep}'
    print(f'  {sex} {t:6s} {model:8s} ✓ {len(w)/24000:.2f}s', flush=True)
open(wf + '.left', 'w', encoding='utf-8').write('\n'.join(left))     # 끝까지 왔으면 실패분만
json.dump(report, open(rp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
