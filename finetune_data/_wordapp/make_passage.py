# -*- coding: utf-8 -*-
"""긴 글 — 문장마다 만들어 **받아쓰기로 맞춰 본 것만** 잇는다.
   ⚠️ 학습 모델은 문장 끝에서도 다음 글자를 덧붙일 때가 있다 (「…四五二零。」뒤에 「我」).
      그래서 문장마다 ① 받아쓰기가 글월과 같아야 하고 ② 마지막 글자 끝에서 소리 기준으로 자른다.
   숫자: Whisper 가 「138」「2026」처럼 아라비아 숫자로 적으니 한 자리씩 한자로 되돌려 맞춘다.
   통과한 후보가 여럿이면 매끄러움 점수가 높은 것을 고른다."""
import os, sys, re, tempfile
sys.path.insert(0, '/app'); os.chdir('/app')
import numpy as np, soundfile as sf, tts.zh_tts as z
TXT = ('现在是二零二六年九月八号，星期二，上午十点半。'
       # 전화번호는 자리마다 「、」로 끊어 읽힌다 — 「一三八」을 한 호흡에 뭉치면 어색하다 (2026-09-09 지적)
       '我住在北京市朝阳区。电话号码是一、三、八，六、六、七、九，四、五、二、零。'
       '我今年三十五岁，在一家公司工作了七年。'
       '每天早上六点四十起床，七点二十出门，八点到公司。'
       '下班以后有时候去跑步，跑三公里大概要二十五分钟。')
SR = 24000
sex, who = sys.argv[1], sys.argv[2]
MODEL  = sys.argv[3] if len(sys.argv) > 3 else who          # '원본:리리' 처럼 원본 모델도 됨
TEMP   = float(sys.argv[4]) if len(sys.argv) > 4 else 0.62
SUFFIX = sys.argv[5] if len(sys.argv) > 5 else ''            # 파일 이름에 붙일 꼬리
TRIES = int(os.environ.get('TRIES', '6'))
HAN = lambda s: ''.join(re.findall(r'[一-鿿]', s))
DIG = str.maketrans('0123456789', '零一二三四五六七八九')

def cn_num(n):
    """0~99 를 중국어로 (10 → 十, 25 → 二十五, 40 → 四十)."""
    n = int(n)
    if n < 10: return '零一二三四五六七八九'[n]
    t, o = divmod(n, 10)
    return ('' if t == 1 else '零一二三四五六七八九'[t]) + '十' + ('零一二三四五六七八九'[o] if o else '')

FULL = str.maketrans('０１２３４５６７８９', '0123456789')

def variants(heard):
    """아라비아 숫자를 한자로 — 한 자리씩 / 수 읽기 두 가지 모두 시도.
       ⚠️ Whisper 가 전각 숫자(２０２６)로 적을 때가 있어 먼저 반각으로 맞춘다 —
          안 맞추면 HAN() 이 숫자를 통째로 버려 「月号星期二上午点」처럼 보여 멀쩡한 후보가 탈락한다."""
    heard = heard.translate(FULL)
    a = re.sub(r'\d+', lambda m: m.group().translate(DIG), heard)
    b = re.sub(r'\d+', lambda m: cn_num(m.group()) if int(m.group()) < 100 else m.group().translate(DIG), heard)
    return {HAN(a), HAN(b)}

def end_dip(w, t1, back=0.06, ahead=0.35):
    """마지막 글자 끝(t1) 언저리에서 소리 봉우리가 꺼지는 **첫 골**을 찾아 거기서 자른다.
       50ms 잠잠해질 때까지 기다리면 뒤에 붙은 군소리(「야이」)가 딸려 들어온다."""
    import librosa
    env = librosa.feature.rms(y=w, frame_length=1024, hop_length=240)[0]      # 10ms
    env = np.convolve(env, np.ones(3) / 3, mode='same')
    i0, i1 = max(0, int((t1 - back) * 100)), min(len(env) - 1, int((t1 + ahead) * 100))
    ref = float(env[max(0, i0 - 15):i0 + 1].max()) + 1e-9                   # 마지막 음절 세기
    for i in range(i0, i1):
        if env[i] < ref * 0.12:                                               # 12% 아래로 꺼지면 골
            return i / 100 + 0.03
    return t1 + 0.06

CN2D = str.maketrans('零一二三四五六七八九', '0123456789')

def digits_of(txt):
    """글월에서 숫자 열만 뽑는다 — 한자 숫자든 아라비아 숫자든 0~9 로."""
    t = txt.translate(FULL).translate(CN2D)
    t = re.sub(r'(?<=[\d、，, ])[以幺么](?=[\d、，, ])|(?<=是)[以幺么]', '1', t)   # 一 → 以/幺 로 적힘
    return ''.join(re.findall(r'\d', t))

def match(want_s, heard):
    """숫자가 셋 넘게 이어지는 글월(전화번호)은 Whisper 가 「13866794520」처럼 뭉뚱그려 적으니
       **숫자 열이 같고 앞머리 한자가 같으면** 통과. 그 밖은 글자 단위로 같아야 한다."""
    want = HAN(want_s)
    # ⚠️ 「、」로 끊어 읽는 전화번호에만 건다. 「二零二六年」 같은 해 숫자에도 걸리게 두면
    #    「星期二」의 二까지 세고 "다 맞았다"며 「上午十点半」 앞에서 끊어 버린다 (2026-09-09 실제로 그랬다).
    if re.search(r'(?:[零一二三四五六七八九]、){2,}', want_s):
        head = re.split(r'[零一二三四五六七八九]', want)[0]
        hh = HAN(heard.translate(FULL))
        return hh.startswith(head) and digits_of(heard) == digits_of(want_s)
    return want in variants(heard)

def phone_ok(cut, words):
    """전화번호 문장은 받아쓰기만으론 모자란다 (2026-09-09 지적):
       ① 「零」이 안 들려도 Whisper 가 앞 숫자로 미루어 0 을 채워 넣는다 → 음절 수로 확인
       ② 「七」이 4성으로 떨어져도 받아쓰기는 모른다 → 그 구간 음높이 기울기로 확인 (1성 = 높고 평평)"""
    import librosa
    n_syl = z._syllables(cut)
    if n_syl < 13:                                   # 봉우리 세기는 六六처럼 붙은 음절을 합쳐 세므로 13까지 봐준다
        return False, f'음절 {n_syl}<13'
    # 마지막 숫자 「零」 — 끝 0.5초에 목청 울림이 뚜렷하고, 끝 0.3초 세기가 문장 전체 세기의 1/4은 돼야 한다
    #   (Whisper 는 零이 안 들려도 앞 숫자로 미루어 0 을 채워 넣는다 — 2026-09-09 실제로 그랬다)
    tail = cut[-int(SR * 0.5):]
    y = librosa.resample(tail, orig_sr=SR, target_sr=16000)
    _, vo, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
    rms_all = float(np.sqrt(np.mean(cut ** 2))) + 1e-9
    rms_tail = float(np.sqrt(np.mean(cut[-int(SR * 0.3):] ** 2)))
    if float(np.nanmean(vo)) < 0.35 or rms_tail < rms_all * 0.25:
        return False, f'끝 零 약함(울림 {float(np.nanmean(vo)):.2f}, 세기 {rms_tail/rms_all:.2f})'
    # 1성 숫자(一·三·八·七) — 높고 평평해야 한다. Whisper 가 「138」을 한 덩어리로 적으니
    #   그 구간을 소리 골로 음절로 나눠, 1성 자리마다 ① 기울기가 떨어지지 않고 ② 높이가 문장 윗자락에 있는지 본다.
    #   (2026-09-09: 七이 4성처럼, 一三八이 이상하다는 지적)
    FIRST = set('1387')
    y_all = librosa.resample(cut, orig_sr=SR, target_sr=16000)
    f0_all, _, _ = librosa.pyin(y_all, fmin=70, fmax=500, sr=16000, frame_length=1024)
    fa = f0_all[~np.isnan(f0_all)]
    hi_ref = float(np.percentile(fa, 75)) if len(fa) > 10 else None
    hop = 256 / 16000                                                   # pyin 틀 간격
    for txt, a, b in words:
        ds = digits_of(txt)
        if not ds or not (set(ds) & FIRST): continue
        seg = cut[int(a * SR):int(min(b + 0.04, len(cut) / SR) * SR)]
        if len(seg) < SR * 0.10: continue
        # 구간을 소리 골로 음절로 나눈다 (기대 음절 수 = 숫자 수)
        env = librosa.feature.rms(y=seg, frame_length=1024, hop_length=240)[0]
        env = np.convolve(env, np.ones(3) / 3, mode='same'); top = env.max() + 1e-9
        bounds = [0]; i = 1
        while i < len(env) - 1:
            if env[i] < top * 0.30 and env[i] <= env[i-1] and env[i] <= env[i+1] and (i - bounds[-1]) > 8:
                bounds.append(i); i += 8
            i += 1
        bounds.append(len(env))
        spans = [(bounds[k] * 240, bounds[k+1] * 240) for k in range(len(bounds) - 1)]
        if len(spans) != len(ds):                                        # 못 나눴으면 덩어리째 본다
            spans = [(0, len(seg))]; ds_chk = [ds]
        else:
            ds_chk = list(ds)
        for (p0, p1), d in zip(spans, ds_chk):
            if not (set(d) & FIRST): continue
            y = librosa.resample(seg[p0:p1], orig_sr=SR, target_sr=16000)
            if len(y) < 1600: continue
            f0, _, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
            f = f0[~np.isnan(f0)]
            if len(f) < 5: continue
            k = max(2, len(f) * 2 // 5)
            slope = (float(np.mean(f[-k:])) - float(np.mean(f[:k]))) / float(np.mean(f))
            if slope < -0.10:
                return False, f'{d} 음높이 {slope*100:+.0f}% 떨어짐(1성 아님)'
    return True, ''

CACHE = 'outputs/chinese/voicepick/_sent'
os.makedirs(CACHE, exist_ok=True)

def hear_words(w):
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        sf.write(f.name, w, SR); p = f.name
    try:
        segs, _ = z._get_asr().transcribe(p, language='zh', word_timestamps=True, beam_size=5, vad_filter=False)
        words = [(z._norm(wd.word), wd.start, wd.end) for s in segs for wd in s.words]
    finally:
        os.unlink(p)
    return words

sents = re.findall(r'[^。]*。', TXT)
parts = []
FALLBACK = None if MODEL.startswith('원본') else f'원본:{who}'
import hashlib
def is_phone(s): return bool(re.search(r'(?:[零一二三四五六七八九]、){2,}', s))

D2CN = dict(zip('0123456789', '零一二三四五六七八九'))
TONE1 = set('一三八七')

def digit_tone_ok(w):
    """홀로 잘라 낸 1성 숫자 — 음높이가 떨어지면 안 된다."""
    import librosa
    y = librosa.resample(w, orig_sr=SR, target_sr=16000)
    f0, _, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
    f = f0[~np.isnan(f0)]
    if len(f) < 5: return True, 0.0
    k = max(2, len(f) * 2 // 5)
    slope = (float(np.mean(f[-k:])) - float(np.mean(f[:k]))) / float(np.mean(f))
    return slope >= -0.10, slope

def build_phone(s):
    """「电话号码是」는 문장으로, 숫자는 **낱말 하나씩** 검사해 만들어 잇는다.
       문장째 읽히면 모델이 숫자 열에서 성조를 흘리고(七→4성, 一三八 이상) 零을 삼킨다 (2026-09-09).
       낱말 만들기(say_word)는 앞뒤 자르기·문맥 받아쓰기·음절 수 검사를 이미 거친다."""
    head_txt = s.split('是')[0] + '是'
    hw = None
    for t in range(TRIES):
        w = z.say(MODEL, head_txt + '。', ref_dir=f'voice_samples/{who}', temperature=TEMP, max_seconds=6)
        ws = hear_words(w); h = ''.join(x[0] for x in ws)
        if HAN(head_txt) not in HAN(h): continue
        acc = ''; end_t = None
        for txt, a, b in ws:
            acc += txt
            if HAN(head_txt) in HAN(acc): end_t = b; break
        st, _ = z._refine(w, ws[0][1], end_t, None); en = end_dip(w, end_t)
        hw = w[int(st * SR):int(en * SR)]
        if float(np.abs(hw[:int(SR*0.03)]).mean()) / (float(np.abs(hw).max()) + 1e-9) > 0.08: hw = None; continue
        break
    if hw is None: return None, '앞머리 실패'
    parts = [hw]
    groups = re.findall(r'[零一二三四五六七八九、]+', s.split('是', 1)[1])
    log = []

    def split_syl(w, n):
        """묶음 소리를 소리 골로 n 음절로 나눈다. 못 나누면 None."""
        import librosa
        env = librosa.feature.rms(y=w, frame_length=1024, hop_length=240)[0]
        env = np.convolve(env, np.ones(3) / 3, mode='same'); top = env.max() + 1e-9
        bounds = [0]; i = 1
        while i < len(env) - 1:
            if env[i] < top * 0.55 and env[i] <= env[i-1] and env[i] <= env[i+1] and (i - bounds[-1]) > 5:
                bounds.append(i); i += 5
            i += 1
        bounds.append(len(env))
        if len(bounds) - 1 != n: return None
        return [w[bounds[k] * 240: bounds[k+1] * 240] for k in range(n)]

    # ⚠️ 한 글자(一 등)는 낱말 만들기가 자주 실패한다 → 「一三八」처럼 **묶음째** 낱말로 만든다.
    #    여러 음절 낱말은 잘 나온다. 묶음을 소리 골로 음절로 나눠 1성 자리마다 기울기를 본다.
    for gi, g in enumerate(groups):
        parts.append(np.zeros(int(SR * 0.28), np.float32))          # 쉼표 자리
        digits = g.replace('、', ''); got = None
        for mdl in ([MODEL] + ([FALLBACK] if FALLBACK else [])):
            for t in range(16):
                wd, rep = z.say_word(mdl, digits, ref_dir=f'voice_samples/{who}', tries=1, temperature=0.55)
                if wd is None: continue
                syls = split_syl(wd, len(digits))
                if syls is None: log.append(f'{digits} 못나눔'); continue
                bad = None
                for d, sw in zip(digits, syls):
                    if d in TONE1 and len(sw) >= SR * 0.10:
                        ok, sl = digit_tone_ok(sw)
                        if not ok: bad = f'{d} {sl*100:+.0f}%'; break
                if bad: log.append(bad); continue
                got = wd; break
            if got is not None: break
        if got is None: return None, f'묶음 {digits} 실패 ({" ".join(log[-5:])})'
        parts += [got]
    out = np.concatenate(parts)
    # 마지막 확인 — 숫자 열이 맞는지
    h = ''.join(x[0] for x in hear_words(out))
    if digits_of(h) != digits_of(s): return None, f'이어 붙인 뒤 숫자 열「{digits_of(h)}」≠'
    return out, ' '.join(log)

for si, s in enumerate(sents):
    want = HAN(s); best = None; log = []
    ck = f"{CACHE}/{sex}{SUFFIX}_{si}_{hashlib.md5(s.encode()).hexdigest()[:8]}.wav"
    if os.path.exists(ck):                              # 전에 통과한 문장 — 지금 잣대로 다시 봐서 되면 그대로
        c, _ = sf.read(ck); c = np.asarray(c, dtype=np.float32)
        wc = hear_words(c); h2 = ''.join(x[0] for x in wc)
        ok = (want in variants(h2) or match(s, h2))
        why = ''
        if ok and is_phone(s): ok, why = False, '숫자 하나씩 다시'
        if ok:
            print(f'  문장{si+1} = 저장본 그대로 {len(c)/SR:.1f}s', flush=True)
            parts += [c, np.zeros(int(SR * 0.45), np.float32)]; continue
        log.append(f'저장본 탈락({why or "받아쓰기"})')
    if is_phone(s):
        out, why = build_phone(s)
        if out is None:
            print(f'  문장{si+1} ✗ 전화번호: {why}'); sys.exit(1)
        print(f'  문장{si+1} ✓ {len(out)/SR:.1f}s  숫자 하나씩 {why}', flush=True)
        sf.write(ck, out, SR); parts += [out, np.zeros(int(SR * 0.45), np.float32)]; continue
    plan = [(MODEL, TEMP)] * TRIES + ([(FALLBACK, 0.65)] * TRIES if FALLBACK else [])
    for t, (mdl, temp) in enumerate(plan):
        if t == TRIES and best is not None: break            # 주 모델에서 통과했으면 대체는 안 쓴다
        if t == TRIES: log.append('→ 원본 모델로 대체'); z.unload()
        w = z.say(mdl, s, ref_dir=f'voice_samples/{who}', temperature=temp, max_seconds=12)
        words = hear_words(w)
        heard = ''.join(x[0] for x in words)
        ok = match(s, heard) or any(v.startswith(want) for v in variants(heard))
        if not ok:
            log.append(f'{t+1}: 「{HAN(heard)[:22]}…」'); continue
        # 글월 마지막 글자가 끝나는 낱말을 찾아 거기서 자른다
        # ⚠️ 글자 수로 세면 「10」이 「一零」(2자)로도 「十」(1자)로도 읽혀 한 낱말 일찍 끊긴다.
        #    글월과 **정확히 같아지는** 자리에서 끊고, 없으면 글월을 품게 되는 첫 자리.
        acc = ''; end_t = None
        for txt, a, b in words:
            acc += txt
            vs = variants(acc)
            if want in vs or match(s, acc): end_t = b; break
            if any(v.startswith(want) for v in vs): end_t = b; break
        if end_t is None: end_t = words[-1][2]
        start, _ = z._refine(w, words[0][1], end_t, None)                  # 첫 낱말 앞의 잠잠한 자리
        end = end_dip(w, end_t)
        cut = w[int(start * SR):int(end * SR)]
        e_ = np.abs(cut); pk = float(e_.max()) + 1e-9
        if float(e_[:int(SR * 0.03)].mean()) / pk > 0.08:
            log.append(f'{t+1}: 앞이 붙음'); continue
        # 자른 것을 다시 받아써서 글월과 **정확히** 같아야 한다 (앞만 맞는 걸로는 부족)
        # ⚠️ 자리마다 끊어 읽는 숫자는 사이 쉼을 "끝"으로 잘못 보고 마지막 零 앞에서 끊길 수 있다
        #    → 재검사가 끝을 놓치면 0.25초씩 두 번까지 늘려 다시 본다
        h2 = ''.join(x[0] for x in hear_words(cut)); ok2 = want in variants(h2) or match(s, h2)
        ext = 0
        while not ok2 and ext < 2 and end + 0.25 <= len(w) / SR:
            end = end_dip(w, end + 0.22); ext += 1
            cut = w[int(start * SR):int(end * SR)]
            h2 = ''.join(x[0] for x in hear_words(cut)); ok2 = want in variants(h2) or match(s, h2)
        if not ok2:
            log.append(f'{t+1}: 자른 뒤「{h2[-10:]}」≠'); continue
        if is_phone(s):
            okp, why = phone_ok(cut, hear_words(cut))
            if not okp:
                log.append(f'{t+1}: {why}'); continue
        q, _ = z._quality(cut)
        log.append(f'{t+1}: 통과 {len(cut)/SR:.1f}s q{q:.2f}')
        if best is None or q > best[0]: best = (q, cut)
        if len([l for l in log if '통과' in l]) >= 2: break
    if best is None:
        print(f'  문장{si+1} ✗ ' + ' / '.join(log)); sys.exit(1)
    print(f'  문장{si+1} ✓ {len(best[1])/SR:.1f}s  ' + ' / '.join(log), flush=True)
    sf.write(ck, best[1], SR)
    parts += [best[1], np.zeros(int(SR * 0.45), np.float32)]
out = np.concatenate(parts)
sf.write(f'outputs/chinese/voicepick/긴글{SUFFIX}_{sex}_new.wav', out, SR)
print(f'{who} 긴 글 {len(out)/SR:.1f}초')
