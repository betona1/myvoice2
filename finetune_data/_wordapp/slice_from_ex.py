# -*- coding: utf-8 -*-
"""낱글자 양사의 성조가 틀린 음성을, 검증된 예문에서 그 글자만 오려 내 갈아 끼운다.
   XTTS 는 글자를 홀로 읽으면 성조를 자주 놓친다(张 zhāng→掌 zhǎng, 页 yè→耶 yé).
   예문(一张纸) 안에서는 제대로 읽으므로, 거기서 잘라 오는 편이 확실하다.
   ⚠️ 3성 글자는 뒤에 3성이 오면 2성으로 바뀐다(변조) → 그런 예문은 쓰지 않는다.
   Whisper 만 올리므로 XTTS 와 메모리를 다투지 않는다."""
import os, sys, json, sqlite3, re
sys.path.insert(0,'/app'); os.chdir('/app'); sys.path.insert(0,'/app/finetune_data/_wordapp')
import numpy as np, soundfile as sf
from faster_whisper import WhisperModel
from pypinyin import pinyin as _py, Style
import opencc
t2s = opencc.OpenCC('t2s')
CJK = re.compile(r'[一-鿿]')
OUT = 'outputs/chinese/words'

def han(x):  return ''.join(CJK.findall(t2s.convert(x or '')))
def syl(x):  return [a[0] for a in _py(x, style=Style.TONE3, neutral_tone_with_five=True)]

asr = WhisperModel("large-v3", device="cuda", device_index=0, compute_type="float16")

def listen(p, words=False):
    s,_ = asr.transcribe(p, language='zh', beam_size=5, best_of=5,
                         temperature=[0.0,0.2], vad_filter=False, word_timestamps=words)
    segs = list(s)
    if not words:
        return ''.join(z.text for z in segs).strip()
    out = []
    for z in segs:
        for w in (z.words or []):
            for ch in han(w.word):           # 한 덩어리 안의 글자를 시간으로 고르게 편다
                out.append([ch, w.start, w.end, len(han(w.word))])
    # 덩어리 안에서 글자마다 제 몫의 구간을 준다
    fixed, i = [], 0
    while i < len(out):
        n = out[i][3]; grp = out[i:i+n]
        if not grp: break
        a, b = grp[0][1], grp[0][2]; step = (b-a)/max(1,n)
        for k,(ch,_,_,_) in enumerate(grp):
            fixed.append((ch, a+step*k, a+step*(k+1)))
        i += n
    return fixed

def cut(path, a, b, lpad=0.0, rpad=0.06, fade=0.012):
    """앞뒤로 똑같이 여유를 주면 앞 글자가 딸려 들어온다(一片, 两颗).
       왼쪽은 정확히 그 글자가 시작하는 데서 끊고, 오른쪽만 여운을 조금 남긴다."""
    w, sr = sf.read(path)
    if w.ndim > 1: w = w.mean(axis=1)
    i = max(0, int((a-lpad)*sr)); j = min(len(w), int((b+rpad)*sr))
    if j-i < sr*0.12: return None, None
    seg = np.asarray(w[i:j], dtype=np.float32).copy()
    f = min(int(sr*fade), len(seg)//4)
    if f > 1:                                  # 잘린 자리에서 '딱' 소리가 나지 않게
        seg[:f]  *= np.linspace(0,1,f)
        seg[-f:] *= np.linspace(1,0,f)
    return seg, sr

con = sqlite3.connect('/app/database/voices.db', timeout=60); con.row_factory = sqlite3.Row
bad = json.load(open('finetune_data/_wordapp/strict_bad.json', encoding='utf-8'))
need = {}
for ch, ws, col, heard, why in bad:
    need.setdefault(ch, set()).add(col)
print(f"고칠 낱말 {len(need)}개 / 파일 {sum(len(v) for v in need.values())}개\n", flush=True)

fixed, giveup, dbg = [], [], []
for ch, cols in sorted(need.items()):
    r = con.execute("""SELECT id,chinese,audio1,audio2 FROM words
                       WHERE chinese=? AND COALESCE(excluded,0)=0""", (ch,)).fetchone()
    if not r: continue
    exs = con.execute("""SELECT chinese,audio1,audio2 FROM word_examples
                         WHERE word_id=? ORDER BY seq""", (r['id'],)).fetchall()
    want = syl(ch)
    for col in sorted(cols):
        got = None
        why = []
        for e in exs:
            src = e[col] or e['audio1'] or e['audio2']
            if not (src and os.path.exists(src)): why.append('파일없음'); continue
            txt = han(e['chinese'])
            k = txt.find(ch[0])
            if k < 0 or txt[k:k+len(ch)] != ch: why.append('예문에글자없음'); continue
            # 뒤 글자가 3성이면 변조가 일어난다 — 3성 낱말은 그런 예문을 건너뛴다
            nxt = txt[k+len(ch):k+len(ch)+1]
            if want[-1].endswith('3') and nxt and syl(nxt)[0].endswith('3'): why.append('변조'); continue
            tl = listen(src, words=True)
            s = ''.join(x[0] for x in tl)
            j = s.find(ch)
            if j < 0 or j+len(ch) > len(tl): why.append(f'받아적기다름({s})'); continue
            # 오른쪽 여운은 다음 글자가 시작하기 전까지만 (뒷글자를 물지 않게)
            nend = tl[j+len(ch)][1] if j+len(ch) < len(tl) else None
            e_ = tl[j+len(ch)-1][2]
            rp = min(0.06, max(0.0, nend-e_)) if nend is not None else 0.09
            seg, sr = cut(src, tl[j][1], e_, rpad=rp)
            if seg is None: why.append('너무짧음'); continue
            tmp = f"/tmp/_sl_{r['id']}_{col}.wav"
            sf.write(tmp, seg, sr)
            # 예문은 이미 검증된 음성이다 → 성조는 문맥에서 이미 맞다.
            # 여기서 볼 것은 '엉뚱한 데를 잘랐는가' 뿐이므로 성조를 뺀 음절만 견준다.
            # (조각 하나를 다시 받아적게 하면 성조를 또 놓쳐서 멀쩡한 것도 버려진다)
            dur = len(seg)/sr
            lo_d, hi_d = 0.13*len(ch), 0.52*len(ch)
            rms = float(np.sqrt(np.mean(seg**2)))
            if lo_d <= dur <= hi_d and rms > 0.01:
                got = (tmp, e['chinese'], dur); break
            why.append(f"{e['chinese']}:{dur:.2f}s/rms{rms:.3f}")
        if got:
            dst = r[col] or f"{OUT}/{col}_{r['id']}_slice.wav"
            d, sr2 = sf.read(got[0]); sf.write(dst, d, sr2)
            con.execute(f"UPDATE words SET {col}=? WHERE id=?", (dst, r['id']))
            fixed.append(f"{ch}/{col[-1]}←{got[1]}")
            print(f"  ✓ {ch} {col} ← {got[1]} ({got[2]:.2f}s)", flush=True)
        else:
            giveup.append(f"{ch}/{col[-1]}")
            print(f"  ✗ {ch} {col}: {' | '.join(why) or '예문없음'}", flush=True)
con.commit()
print(f"\n오려 붙인 것 {len(fixed)}개 · 못 고친 것 {len(giveup)}개")
if giveup: print("  남은 것: " + ' '.join(giveup))
if dbg: print("\n[안 맞은 조각] " + '  '.join(dbg[:60]))
json.dump(giveup, open('finetune_data/_wordapp/slice_left.json','w',encoding='utf-8'),
          ensure_ascii=False)
con.close()
