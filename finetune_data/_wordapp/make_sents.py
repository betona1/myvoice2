# -*- coding: utf-8 -*-
"""짧은 문장 여러 개 — 문장마다 받아쓰기·앞뒤 검사를 거쳐 통과한 것만 저장.
   쓰임: make_sents.py <성별> <화자> <모델> <온도> <키(new|clone)>"""
import os, sys, re, tempfile
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf, tts.zh_tts as z
SR = 24000
sex, who, MODEL, TEMP, KEY = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), sys.argv[5]
TRIES = int(os.environ.get('TRIES', '8'))
FALLBACK = None if MODEL.startswith('원본') else f'원본:{who}'
HAN = lambda s: ''.join(re.findall(r'[一-鿿]', s))
sents = [l.strip() for l in open('temp/voicesample/sents.txt', encoding='utf-8') if l.strip()]

def hear_words(w):
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        sf.write(f.name, w, SR); p = f.name
    try:
        segs, _ = z._get_asr().transcribe(p, language='zh', word_timestamps=True, beam_size=5, vad_filter=False)
        return [(z._norm(wd.word), wd.start, wd.end) for s in segs for wd in s.words]
    finally: os.unlink(p)

def end_dip(w, t1, back=0.06, ahead=0.35):
    import librosa
    env = librosa.feature.rms(y=w, frame_length=1024, hop_length=240)[0]
    env = np.convolve(env, np.ones(3) / 3, mode='same')
    i0, i1 = max(0, int((t1 - back) * 100)), min(len(env) - 1, int((t1 + ahead) * 100))
    ref = float(env[max(0, i0 - 15):i0 + 1].max()) + 1e-9
    for i in range(i0, i1):
        if env[i] < ref * 0.12: return i / 100 + 0.03
    return t1 + 0.06

OUT = 'outputs/chinese/voicepick'
MIN_PER_SYL = 0.17
MAX_PER_SYL = 0.40   # 이보다 느리면 「너무 느림」 (2026-09-10 17번)

def ends_clean(cut):
    """끝이 잠잠하게 끝나야 한다 — 끝 60ms 평균 세기가 봉우리의 8% 아래.
       (2026-09-10 남자 12번: 끝 120ms 가 17% — 소리 한가운데서 잘렸는데 검사를 다 통과했다)"""
    e = np.abs(cut); pk = float(e.max()) + 1e-9
    return float(e[-int(SR * 0.06):].mean()) < 0.08 * pk

def head_noisy(cut):
    """첫 80ms 가 잡음 덩어리(스펙트럼 평탄도 높음)면 앞에 숨·딸깍이 붙은 것 (2026-09-10 남자 10번)."""
    import librosa
    y = librosa.resample(cut[:int(SR * 0.08)], orig_sr=SR, target_sr=16000)
    if len(y) < 600: return False
    return float(np.mean(librosa.feature.spectral_flatness(y=y))) > 0.35

def trim_to_onset(cut):
    """첫 음절의 **진짜 시작**을 소리 세기로 찾아 그 앞을 잘라 낸다.
       Whisper 첫 낱말 시각이 이르게 찍히면 그 앞 군소리(숨·딸깍)가 낱말 안에 숨어 들어
       앞 군소리 검사를 빠져나간다 (2026-09-10 남자 10번 「这个」 앞). 첫 센 소리(봉우리의 40%)
       앞에서 40ms 넘게 잠잠한(5% 아래) 자리를 거꾸로 찾아 거기서 시작한다."""
    e = np.abs(cut); pk = float(e.max()) + 1e-9
    strong = np.where(e > 0.40 * pk)[0]
    if len(strong) == 0: return cut
    first = int(strong[0]); need = int(SR * 0.04); quiet = 0; cutpt = None
    for i in range(first, 0, -1):
        if e[i] < 0.05 * pk:
            quiet += 1
            if quiet >= need: cutpt = i + need; break
        else:
            quiet = 0
    if cutpt is None or cutpt < SR * 0.05: return cut
    if float(e[:cutpt].max()) < 0.12 * pk: return cut          # 앞이 원래 조용하면 그대로
    return cut[max(0, cutpt - int(SR * 0.02)):]

def gap_junk(cut, words):
    """낱말 **사이** 빈 틈(0.15초 넘음)에 소리가 있으면 군소리 (2026-09-10 10번 「这个」 뒤)."""
    pk = float(np.abs(cut).max()) + 1e-9
    for (t1, a1, b1), (t2, a2, b2) in zip(words, words[1:]):
        if a2 - b1 > 0.15:
            seg = np.abs(cut[int((b1 + 0.03) * SR):int((a2 - 0.03) * SR)])
            if len(seg) and float(seg.max()) > 0.15 * pk: return True
    return False
TAIL_MIN = 0.20      # 끝 0.25초 세기가 문장 평균의 1/5은 돼야 한다 (2026-09-10 「谢谢」 끝이 잘림)

def tail_weak(cut, words=None):
    rms_all = float(np.sqrt(np.mean(cut ** 2))) + 1e-9
    rms_tail = float(np.sqrt(np.mean(cut[-int(SR * 0.25):] ** 2)))
    if rms_tail < rms_all * TAIL_MIN: return True
    # 마지막 음절 자체 — Whisper 마지막 낱말 구간의 뒤 1/n 세기가 문장 평균의 30% 는 돼야 한다
    if words:
        txt, a, b = words[-1]; n = max(1, len(HAN(txt)))
        seg = cut[int(a * SR):int(min(b + 0.03, len(cut) / SR) * SR)]
        if len(seg) > SR * 0.06:
            last = seg[-max(int(len(seg) / n), int(SR * 0.10)):]
            if float(np.sqrt(np.mean(last ** 2))) < rms_all * 0.30: return True
        if b > len(cut) / SR + 0.02: return True            # 마지막 낱말이 잘림 안에서 끝나야
    return False

def tone_check(cut, s):
    """문장 안 **모든 음절**의 성조를 음높이 흐름으로 본다 (2026-09-10 「韩国人」 国 이 3성으로 들림).
       글자마다 성조(pypinyin)를 알고 있으니, Whisper 낱말 구간을 소리 골로 글자 수만큼 나눠
       음절마다 음높이 흐름(끝−처음)을 잰다: 2성은 올라야, 4성은 내려야, 1성은 떨어지면 안 되고,
       3성은 2성처럼 쭉 오르면 안 된다. 3성 연이음(很好의 很)·不·一·경성은 뺀다.
       나누기가 안 되는 낱말은 건너뛴다 (틀린 판정으로 멀쩡한 걸 버리지 않으려고)."""
    import librosa
    from pypinyin import pinyin, Style
    hz = HAN(s)
    tones = [x[0][-1:] for x in pinyin(hz, style=Style.TONE3, neutral_tone_with_five=True)]
    NEUTRAL2 = ['关系','谢谢','名字','什么','知道','东西','衣服','朋友','我们','你们','他们','妈妈','爸爸',
                '明白','觉得','先生','漂亮','认识','意思','告诉','时候','学生','喜欢','头发','桌子','椅子','孩子']
    for wd in NEUTRAL2:
        j = hz.find(wd)
        while j >= 0:
            tones[j + 1] = '5'; j = hz.find(wd, j + 1)
    skip = set()
    for i, c in enumerate(hz):
        if c in '不一': skip.add(i)
        if tones[i] == '3' and i + 1 < len(hz) and tones[i + 1] == '3': skip.add(i)     # 3성 연이음
    words = hear_words(cut); pos = 0
    for txt, a, b in words:
        chars = HAN(txt); n = len(chars)
        if n == 0: continue
        seg = cut[int(a * SR):int(min(b + 0.04, len(cut) / SR) * SR)]
        idx = list(range(pos, min(pos + n, len(hz)))); pos += n
        if ''.join(hz[i] for i in idx) != chars or len(seg) < SR * 0.10 * n: continue
        # 소리 골로 n 음절로
        env = librosa.feature.rms(y=seg, frame_length=1024, hop_length=240)[0]
        env = np.convolve(env, np.ones(3) / 3, mode='same'); top = env.max() + 1e-9
        bounds = [0]; i = 1
        while i < len(env) - 1:
            if env[i] < top * 0.5 and env[i] <= env[i-1] and env[i] <= env[i+1] and (i - bounds[-1]) > 5:
                bounds.append(i); i += 5
            i += 1
        bounds.append(len(env))
        if len(bounds) - 1 != n:
            L = len(seg); spans = [(int(L * k / n), int(L * (k + 1) / n)) for k in range(n)]   # 균등 나눔
        else:
            spans = [(bounds[k] * 240, bounds[k+1] * 240) for k in range(n)]
        for k, (p0, p1) in enumerate(spans):
            gi = idx[k]
            if gi in skip or p1 - p0 < SR * 0.10: continue
            y = librosa.resample(seg[p0:p1], orig_sr=SR, target_sr=16000)
            f0, _, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
            f = f0[~np.isnan(f0)]
            if len(f) < 5: continue
            # ⚠️ pyin 이 한 옥타브 튀는 틀(반/배)을 섞어 준다 → 중앙값에서 크게 벗어난 틀은 버린다.
            #    안 버리면 「你 +114%」「韩 −75%」 같은 헛된 값이 나와 멀쩡한 문장을 버린다 (2026-09-10).
            med = float(np.median(f)); f = f[(f > med * 0.7) & (f < med * 1.4)]
            if len(f) < 5: continue
            kk = max(2, len(f) * 3 // 10)
            rise = (float(np.median(f[-kk:])) - float(np.median(f[:kk]))) / float(np.median(f))
            if abs(rise) > 0.6: continue                     # 그래도 터무니없으면 재지 않은 것으로 친다
            imin = int(np.argmin(f)) / len(f); tn = tones[gi]
            if tn == '5':                                     # 경성: 짧고 골 없이
                avg = (len(cut) / SR) / max(1, len(hz))
                if n >= 4 and (p1 - p0) / SR > avg * 1.45: return False, f'{hz[gi]} 경성인데 김'
                if rise > 0.15 and imin < 0.4: return False, f'{hz[gi]} 경성인데 골(3성처럼)'
                continue
            # 4성은 분명히 내려야, 2성은 분명히 올라야 — "안 오르면 통과"로 두면 3성처럼 낮게 깔린
            # 소리(2026-09-10 「对」)가 빠져나간다
            if tn == '2' and rise < 0.03:  return False, f'{hz[gi]} 2성인데 {rise*100:+.0f}%'
            if tn == '4' and rise > -0.03: return False, f'{hz[gi]} 4성인데 {rise*100:+.0f}%'
            if tn == '1' and rise < -0.15: return False, f'{hz[gi]} 1성인데 {rise*100:+.0f}%'
            if tn == '3' and rise > 0.20 and imin < 0.35: return False, f'{hz[gi]} 3성인데 {rise*100:+.0f}% 오름'
    return True, ''

def final_tone3_ok(cut, s):
    """문장 끝 글자가 3성이면 — 낮게 깔렸다 살짝 올라와야지, 2성처럼 쭉 올라가면 안 된다
       (2026-09-10 「对不起」의 起 가 2성으로 들림). 끝 음절 음높이가 처음보다 15% 넘게 높고
       가장 낮은 데가 앞쪽(골 없음)이면 2성으로 보고 탈락."""
    import librosa
    from pypinyin import pinyin, Style
    last = HAN(s)[-1:]
    if not last: return True, ''
    tone = pinyin(last, style=Style.TONE3, neutral_tone_with_five=True)[0][0][-1:]
    if tone != '3': return True, ''
    words = hear_words(cut)
    if not words: return True, ''
    txt, a, b = words[-1]; n = len(HAN(txt))
    seg = cut[int(a * SR):int(min(b + 0.05, len(cut) / SR) * SR)]
    if n > 1 and len(seg) > 0:                       # 여러 글자 낱말이면 뒤 1/n 만
        seg = seg[-max(int(len(seg) / n), int(SR * 0.12)):]
    if len(seg) < SR * 0.10: return True, ''
    y = librosa.resample(seg, orig_sr=SR, target_sr=16000)
    f0, _, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
    f = f0[~np.isnan(f0)]
    if len(f) < 6: return True, ''
    k = max(2, len(f) // 4)
    rise = (float(np.mean(f[-k:])) - float(np.mean(f[:k]))) / float(np.mean(f))
    imin = int(np.argmin(f)) / len(f)
    if rise > 0.12 and imin < 0.45:
        return False, f'끝 {last} 3성인데 {rise*100:+.0f}% 오름(2성처럼)'
    return True, ''

def verify(cut, want):
    if len(cut) / SR < MIN_PER_SYL * len(want): return f'너무 빠름({len(cut)/SR/len(want):.2f}s/음절)'
    if len(cut) / SR > MAX_PER_SYL * len(want): return f'너무 느림({len(cut)/SR/len(want):.2f}s/음절)'
    if gap_junk(cut, hear_words(cut)): return '사이 군소리'
    e_ = np.abs(cut); pk = float(e_.max()) + 1e-9
    if float(e_[:int(SR * 0.03)].mean()) / pk > 0.08: return '앞이 붙음'
    if len(trim_to_onset(cut)) < len(cut) - SR * 0.03: return '앞 군소리(숨은 것)'
    if head_noisy(cut): return '앞 잡음(숨·딸깍)'
    if not ends_clean(cut): return '끝이 잘림(소리 중간)'
    if HAN(''.join(x[0] for x in hear_words(cut))) != want: return '받아쓰기'
    if z.lead_junk(cut): return '앞에 군소리'
    if tail_weak(cut, hear_words(cut)): return '끝이 약함(잘림)'
    ok, why = final_tone3_ok(cut, want)
    if not ok: return why
    ok, why = tone_check(cut, want)
    if not ok: return why
    return ''

for si, s in enumerate(sents):
    want = HAN(s); best = None; log = []
    fn = f'{OUT}/문장{si+1:02d}_{sex}_{KEY}.wav'
    if os.path.exists(fn):
        c, _ = sf.read(fn); c = np.asarray(c, dtype=np.float32); why = verify(c, want)
        if not why:
            print(f'  {si+1:02d} {s} = 그대로', flush=True); continue
        print(f'  {si+1:02d} {s} 저장본 탈락({why}) → 다시', flush=True)
    plan = [(MODEL, TEMP)] * TRIES + ([(FALLBACK, 0.65)] * TRIES if FALLBACK else [])
    for t, (mdl, temp) in enumerate(plan):
        if t == TRIES and best is not None: break
        if t == TRIES: log.append('→ 원본 대체'); z.unload()
        # ⚠️ 앞 문장이 원본으로 대체됐으면 원본 모델이 아직 올라가 있다 → 내리고 주 모델을 올린다.
        #    둘 다 두면 Whisper 까지 세 개가 되어 VRAM 이 넘친다 (2026-09-10 남자 판이 12번에서 죽음)
        if t == 0 and any(k != mdl for k in list(z._cache)): z.unload()
        # 앞 시도가 너무 빨랐으면 말 속도를 0.9 로 늦춰 본다
        spd = 0.85 if any('너무 빠름' in l for l in log) else (1.1 if any('너무 느림' in l for l in log) else 1.0)
        w = z.say(mdl, s, ref_dir=f'voice_samples/{who}', temperature=temp, max_seconds=8, speed=spd)
        words = hear_words(w)
        if not words: continue
        heard = HAN(''.join(x[0] for x in words))
        if not heard.startswith(want): log.append(f'{t+1}:「{heard[:10]}」'); continue
        acc = ''; end_t = None
        for txt, a, b in words:
            acc += HAN(txt)
            if len(acc) >= len(want): end_t = b; break
        start, _ = z._refine(w, words[0][1], end_t, None); end = end_dip(w, end_t)
        cut = trim_to_onset(w[int(start * SR):int(end * SR)])
        ext = 0
        while tail_weak(cut, hear_words(cut)) and ext < 2 and end + 0.25 <= len(w) / SR:
            end = end_dip(w, end + 0.22); ext += 1; cut = w[int(start * SR):int(end * SR)]
        while not ends_clean(cut) and ext < 3 and end + 0.2 <= len(w) / SR:
            end = end_dip(w, end + 0.15); ext += 1; cut = w[int(start * SR):int(end * SR)]
        if not ends_clean(cut): log.append(f'{t+1}: 끝이 잘림'); continue
        if head_noisy(cut): log.append(f'{t+1}: 앞 잡음'); continue
        if tail_weak(cut, hear_words(cut)): log.append(f'{t+1}: 끝이 약함'); continue
        e_ = np.abs(cut); pk = float(e_.max()) + 1e-9
        if float(e_[:int(SR * 0.03)].mean()) / pk > 0.08: log.append(f'{t+1}: 앞이 붙음'); continue
        h2 = HAN(''.join(x[0] for x in hear_words(cut)))
        if h2 != want: log.append(f'{t+1}: 자른 뒤「{h2[-6:]}」≠'); continue
        if z.lead_junk(cut): log.append(f'{t+1}: 앞에 군소리'); continue
        if len(cut) / SR < MIN_PER_SYL * len(want): log.append(f'{t+1}: 너무 빠름'); continue
        if len(cut) / SR > MAX_PER_SYL * len(want): log.append(f'{t+1}: 너무 느림'); continue
        if gap_junk(cut, hear_words(cut)): log.append(f'{t+1}: 사이 군소리'); continue
        ok3, why3 = final_tone3_ok(cut, s)
        if not ok3: log.append(f'{t+1}: {why3}'); continue
        okt, whyt = tone_check(cut, s)
        if not okt: log.append(f'{t+1}: {whyt}'); continue
        q, _ = z._quality(cut)
        if best is None or q > best[0]: best = (q, cut)
        if sum(1 for l in log if '통과' in l) >= 1: break
        log.append(f'{t+1}: 통과 q{q:.2f}')
    if best is None:
        if os.path.exists(fn): os.remove(fn); log.append('예전 파일 지움')
        print(f'  {si+1:02d} {s} ✗ ' + ' / '.join(log)[-110:], flush=True); continue
    sf.write(f'{OUT}/문장{si+1:02d}_{sex}_{KEY}.wav', best[1], SR)
    print(f'  {si+1:02d} {s} ✓ {len(best[1])/SR:.1f}s', flush=True)
