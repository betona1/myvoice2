# -*- coding: utf-8 -*-
"""voicepick 의 「다」 낱말 전부를 앞뒤 문맥 받아쓰기로 다시 걸러, 못 넘긴 건 .bad 로 뺀다."""
import os, sys, re, glob
sys.path.insert(0, '/app'); os.chdir('/app')
import soundfile as sf, numpy as np, tts.zh_tts as z
N = 'outputs/chinese/voicepick'; HAN = lambda s: ''.join(re.findall(r'[一-鿿]', s))
bad = []; n = 0
for fn in sorted(glob.glob(f'{N}/*_new.wav')):
    b = os.path.basename(fn); t = b.rsplit('_', 2)[0]
    want = HAN(t)
    if len(want) > 6 or not want: continue           # 문장·긴 글은 건너뜀
    n += 1
    w, _ = sf.read(fn); w = np.asarray(w, dtype=np.float32); h = z._hear_ctx(w)
    ok = h is not None and (h == want or z._py(h) == z._py(want) or (want.endswith('儿') and h == want[:-1]))
    if not ok:
        bad.append(b); os.rename(fn, fn + '.bad'); print(f'  ✗ {b} 들린「{h}」', flush=True)
print(f'\n검사 {n}개 · 뺀 것 {len(bad)}개')
