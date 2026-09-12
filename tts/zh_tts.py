# -*- coding: utf-8 -*-
"""파인튜닝한 중국어 XTTS 모델로 소리를 만든다.
   기본 엔진(tts/engine.py)은 원본 XTTS 에 참조 음성만 물리는 구조라 학습한 모델을 못 쓴다.
   여기서는 학습 결과(model.pth/config.json/vocab.json)를 직접 올려 쓴다.
   ⚠️ 합성할 때의 언어 이름은 'zh-cn' — 학습할 때 쓰는 'zh' 와 다르다.
   ⚠️ 10GB 카드 한 장이라 한 번에 한 모델만 올린다."""
import os, glob
import numpy as np, torch

SR = 24000
_cache = {}

BASE_DIR = '/root/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2'

def _model_dir(name):
    """'원본:이한' 처럼 쓰면 학습 안 한 원본 XTTS 를 올린다 (목소리는 참조로 본뜬다)."""
    if name.startswith('원본'):
        return BASE_DIR
    return os.path.join('finetune_data', f'{name}_zh_model')

def load(name, ref_dir=None):
    """name: '이한' | '리리'. ref_dir 를 주면 그 안의 wav 들로 목소리 결을 잡는다."""
    if name in _cache:
        return _cache[name]
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    d = _model_dir(name)
    cfg = XttsConfig(); cfg.load_json(f'{d}/config.json')
    model = Xtts.init_from_config(cfg)
    model.load_checkpoint(cfg, checkpoint_path=f'{d}/model.pth',
                          vocab_path=f'{d}/vocab.json', eval=True, strict=False)
    if torch.cuda.is_available():
        model.cuda()
    if ref_dir is None and name.startswith('원본:'):
        ref_dir = os.path.join('voice_samples', name.split(':', 1)[1])
    refs = sorted(glob.glob(f'{ref_dir}/*.wav')) if ref_dir else []
    if not refs:
        refs = [f'{d}/reference.wav'] if os.path.exists(f'{d}/reference.wav') else []
    gpt_lat, spk_emb = model.get_conditioning_latents(
        audio_path=refs, gpt_cond_len=cfg.gpt_cond_len,
        max_ref_length=cfg.max_ref_len, sound_norm_refs=cfg.sound_norm_refs)
    _cache[name] = (model, cfg, gpt_lat, spk_emb)
    print(f"[중국어TTS] {name} 모델 올림 (참조 {len(refs)}개)")
    return _cache[name]

def say(name, text, temperature=0.70, speed=1.0, ref_dir=None, max_seconds=None):
    """max_seconds: 생성 길이 상한. 낱말은 3초면 넉넉하다 — 학습 모델이 헛소리를
       길게 낼수록 VRAM 도 크게 먹으니 낱말에는 꼭 건다. (토큰 하나 ≈ 46ms)"""
    model, cfg, gpt_lat, spk_emb = load(name, ref_dir)
    keep = model.gpt.max_gen_mel_tokens
    if max_seconds:
        model.gpt.max_gen_mel_tokens = int(max_seconds * 21.5) + 4
    try:
        return _say(model, cfg, gpt_lat, spk_emb, text, temperature, speed)
    finally:
        model.gpt.max_gen_mel_tokens = keep

def _say(model, cfg, gpt_lat, spk_emb, text, temperature, speed):
    out = model.inference(text, 'zh-cn', gpt_lat, spk_emb,
                          temperature=temperature, speed=speed,
                          length_penalty=cfg.length_penalty,
                          repetition_penalty=cfg.repetition_penalty,
                          top_k=cfg.top_k, top_p=cfg.top_p, enable_text_splitting=False)
    return np.asarray(out['wav'], dtype=np.float32)

def unload(name=None):
    """다음 모델을 올리기 전에 VRAM 을 비운다."""
    for k in ([name] if name else list(_cache)):
        _cache.pop(k, None)
    import gc; gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ──────────────────────────────────────────────────────────────────────
# 낱말까지만 잘라 내기
#   파인튜닝 모델은 목소리는 좋은데 낱말을 말한 뒤 멈추지 못하고 헛소리를 잇는다
#   (녹음 2분으로 학습한 탓, 2026-09-08). 다행히 **앞부분은 맞게 말한다**.
#   Whisper 로 글자마다 시각을 받아, 시킨 낱말의 마지막 글자 끝에서 끊는다.
#   헛소리가 붙어 있어야 Whisper 가 짧은 낱말도 잘 알아듣는다 — 홀로 있는
#   한 음절은 못 알아듣는다는 게 이미 확인된 바라, 자르기 전 소리로 판정한다.
# ──────────────────────────────────────────────────────────────────────
import re as _re
_asr = None
_HAN = _re.compile(r'[一-鿿]')

def _get_asr():
    global _asr
    if _asr is None:
        from faster_whisper import WhisperModel
        # int8_float16 — VRAM 3GB → 1.5GB. 앱 엔진(2GB)·모델(2GB)과 함께 올려야 해서.
        _asr = WhisperModel('large-v3', device='cuda', compute_type='int8_float16')
    return _asr

_DIG = str.maketrans('0123456789０１２３４５６７８９', '零一二三四五六七八九零一二三四五六七八九')

def _norm(s):
    """번체·이체를 간체로 맞춘다 (Whisper 가 번체로 적을 때가 있다). 숫자는 그대로 둔다 —
       긴 글 쪽(make_passage)은 「10」을 「十」으로도 읽어야 해서 아라비아 숫자가 필요하다."""
    try:
        from opencc import OpenCC
        return OpenCC('t2s').convert(s)
    except Exception:
        return s

def _normd(s):
    """낱말 검사용 — 간체 + 아라비아 숫자를 한 자리씩 한자로.
       ⚠️ Whisper 가 「六六七九」를 「6679」로 적으면 _HAN 이 숫자를 통째로 버려 낱말을 못 찾는다."""
    return _norm(s.translate(_DIG))

def _py(s):
    from pypinyin import lazy_pinyin
    return lazy_pinyin(s)          # 성조 뺀 병음 — Whisper 가 동음이자를 적는 일이 잦다

def _find(want, heard):
    """낱말이 들린 글월의 맨 앞(한 글자 뒤까지 허용)에 있는지 — 한자로, 안 되면 병음으로."""
    for i in (0, 1):
        if heard[i:i + len(want)] == want:
            return i
    pw = _py(want)
    for i in (0, 1):
        if _py(heard[i:i + len(want)]) == pw:
            return i
    return -1

def say_clean(name, text, tries=5, pad=0.08, ref_dir=None, temperature=0.70, speed=1.0):
    """낱말을 말한 뒤 붙는 헛소리를 잘라 낸 소리를 돌려준다.
       돌려주는 값: (wav, 들린 글월) — 못 찾으면 (None, 마지막 들린 글월)"""
    want = ''.join(_HAN.findall(text))
    if not want:
        return say(name, text, ref_dir=ref_dir), ''
    asr = _get_asr()
    import tempfile, soundfile as sf, os as _os
    last = ''
    for _ in range(tries):
        w = say(name, text, temperature=temperature, ref_dir=ref_dir, max_seconds=3.0, speed=speed)
        torch.cuda.empty_cache()
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            sf.write(f.name, w, SR); p = f.name
        try:
            segs, _ = asr.transcribe(p, language='zh', word_timestamps=True,
                                     beam_size=5, vad_filter=False)
            chars = [(c, wd.start, wd.end)
                     for s in segs for wd in s.words for c in _HAN.findall(_normd(wd.word))]
        finally:
            _os.unlink(p)
        heard = ''.join(c for c, _, _ in chars); last = heard
        i, n = _find(want, heard), len(want)
        if i < 0 and want.endswith('儿') and len(want) > 1:
            # 얼화(儿化)는 Whisper 가 儿 을 빼먹기 일쑤 — 앞글자까지 찾고 끝을 조금 더 준다
            i, n = _find(want[:-1], heard), len(want) - 1
            pad += 0.12
        if i < 0:
            continue
        # ⚠️ Whisper 의 글자 시각은 실제 소리보다 **시작은 이르고 끝은 이르다**.
        #    그대로 믿고 자르면 뒷글자가 잘리고 앞에 군소리가 붙는다 (2026-09-09 평가에서 확인).
        #    그래서 시각은 어림으로만 쓰고, 실제 자르는 자리는 소리 세기로 잡는다.
        nxt = chars[i + n][1] if i + n < len(chars) else None
        start, end = _refine(w, chars[i][1], chars[i + n - 1][2], nxt)
        cut = w[int(start * SR):int(end * SR)]
        # 끝을 조금 다듬는다 — 마지막 글자 뒤 첫 잠잠한 데까지만
        e = np.abs(cut); thr = 0.012
        j = len(cut) - 1
        while j > 0 and e[j] < thr: j -= 1
        cut = cut[:min(len(cut), j + int(SR * 0.06))]
        if len(cut) >= SR * 0.18:
            return cut.astype(np.float32), heard
    return None, last


# ──────────────────────────────────────────────────────────────────────
# 낱말 합성 — 자른 뒤 검사해 통과한 것만 쓴다
#   say_clean 은 길이만 보고 넘겨서 「很」이 두 번 들리거나(헌헌) 앞뒤에
#   군소리·잡음이 붙은 것을 못 걸렀다 (2026-09-08 사용자 지적).
#   여기서는 후보를 여러 개 만들고 하나하나 다음을 검사한다.
#     ① 문맥 받아쓰기 — 뒤에 아는 소리를 붙여 문장처럼 만들어 묻는다.
#        홀로 있는 한 음절은 Whisper 가 못 알아듣는다(확인된 바).
#        앞머리가 시킨 낱말과 정확히 같아야 한다 (「很很」이면 탈락)
#     ② 음절 수 — 소리 세기 봉우리 수가 글자 수와 같아야 한다 (비언어 군소리 걸러냄)
#     ③ 음절당 길이 0.14~0.65초
#     ④ 앞머리 80ms 가 잡음 덩어리가 아닐 것 (스펙트럼 평탄도)
#   통과한 후보 중 매끄러움 점수가 가장 높은 것을 고른다.
# ──────────────────────────────────────────────────────────────────────
_ctx = None
HEADZ, TAILZ = '这本杂志', '一张纸'

def _get_ctx():
    """문맥 받아쓰기에 붙일 머리·꼬리 — 이미 검증된 예문 소리.
       ⚠️ 꼬리만 붙이면 「张」이 꼬리 「一张纸」에 묻히고, 「二」처럼 홀로 앞에 선
       음절은 Whisper 가 떨어뜨린다. 앞뒤로 붙여 가운데만 읽게 한다."""
    global _ctx
    if _ctx is None:
        import sqlite3, soundfile as sf
        con = sqlite3.connect('database/voices.db'); out = []
        for z in (HEADZ, TAILZ):
            r = con.execute("SELECT audio1 FROM word_examples WHERE chinese=?", (z,)).fetchone()
            w, sr = sf.read(r[0])
            if w.ndim > 1: w = w.mean(axis=1)
            out.append(np.asarray(w, dtype=np.float32))
        con.close(); _ctx = tuple(out)
    return _ctx

def _hear_ctx(cut):
    """머리 + 잘라 낸 소리 + 꼬리를 받아쓰고, 가운데만 돌려준다."""
    import tempfile, soundfile as sf, os as _os
    head, tail = _get_ctx()
    gap = np.zeros(int(SR * 0.30), np.float32)
    joined = np.concatenate([head, gap, cut, gap, tail])
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        sf.write(f.name, joined, SR); p = f.name
    try:
        segs, _ = _get_asr().transcribe(p, language='zh', beam_size=5, best_of=5,
                                        temperature=[0.0, 0.2], vad_filter=False)
    finally:
        _os.unlink(p)
    h = ''.join(_HAN.findall(_normd(''.join(s.text for s in segs))))
    a = h.find(HEADZ); b = h.rfind(TAILZ)
    if a < 0 or b < 0 or b < a + len(HEADZ):
        return None
    return h[a + len(HEADZ):b]

def _refine(w, t0, t1, t_next, thr=0.010):
    """시작: t0 앞뒤 [-0.06, +0.14] 창에서 가장 잠잠한 자리.
       끝: t1 부터 앞으로 나가며 소리가 50ms 넘게 잠잠해지는 자리 (최대 +0.45초,
           다음 글자가 있으면 그 시작 직전까지)."""
    e = np.abs(w); n = len(w)
    # 시작 — t0+0.10 에서 거꾸로 t0-0.35 까지 훑어 **마지막 잠잠한 자리**(50ms 연속 < thr)를 찾는다.
    #        없으면 (앞 군소리가 낱말에 붙어 있으면) t0 를 그대로 두고, 부르는 쪽이 앞 세기로 걸러낸다.
    need = int(SR * 0.05)
    hi, lo = min(n, int((t0 + 0.10) * SR)), max(0, int((t0 - 0.35) * SR))
    start = max(t0 - 0.03, 0); p = hi; quiet = 0
    while p > lo:
        if e[p] < thr:
            quiet += 1
            if quiet >= need: start = (p + need) / SR - 0.03; break
        else:
            quiet = 0
        p -= 1
    # 끝
    lim = t1 + 0.45
    if t_next is not None: lim = min(lim, t_next - 0.02)
    p = int(t1 * SR); q = min(n, int(lim * SR)); quiet = 0; end = lim
    need = int(SR * 0.05)
    while p < q:
        if e[p] < thr:
            quiet += 1
            if quiet >= need: end = (p - need) / SR + 0.04; break
        else:
            quiet = 0
        p += 1
    return max(start - 0.02, 0), min(end + 0.0, n / SR)

def _tight(w, thr=0.010, pad_s=0.035):
    """앞뒤 잠잠한 데를 바짝 걷어낸다."""
    e = np.abs(w); idx = np.where(e > thr)[0]
    if len(idx) == 0: return w
    pad = int(SR * pad_s)
    return w[max(0, idx[0] - pad): min(len(w), idx[-1] + pad)]

def _syllables(w):
    """소리 세기 봉우리 수 — 음절 수 어림."""
    import librosa
    rms = librosa.feature.rms(y=w, frame_length=1024, hop_length=240)[0]   # 10ms
    rms = np.convolve(rms, np.ones(5) / 5, mode='same')
    top = rms.max()
    if top <= 0: return 0
    r = rms / top
    peaks, i = 0, 1
    while i < len(r) - 1:
        if r[i] >= 0.35 and r[i] >= r[i-1] and r[i] >= r[i+1]:
            # 이 봉우리 뒤에 골(봉우리의 55% 아래)이 와야 다음 봉우리를 센다
            j = i + 1
            while j < len(r) and r[j] > r[i] * 0.55: j += 1
            peaks += 1; i = j
        else:
            i += 1
    return peaks

def _quality(w):
    """매끄러움 — 목청 울림 많고, 잡음 적고, 떨림 적을수록 높다."""
    import librosa
    y = librosa.resample(w, orig_sr=SR, target_sr=16000)
    f0, vo, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
    voiced = float(np.nanmean(vo)) if vo is not None else 0
    flat = float(np.mean(librosa.feature.spectral_flatness(y=y)))
    f = f0[~np.isnan(f0)]
    jit = float(np.mean(np.abs(np.diff(f))) / (np.mean(f) + 1e-9)) if len(f) > 2 else 1.0
    head = y[:int(16000 * 0.08)]
    head_flat = float(np.mean(librosa.feature.spectral_flatness(y=head))) if len(head) > 512 else 0
    return voiced * 2 - flat * 6 - jit * 3, head_flat

def lead_junk(cut, thr_ratio=0.15):
    """첫 낱말이 시작되기 **앞에** 들리는 소리가 있는가.
       첫 30ms 세기 검사는 살살 시작하는 군소리를 놓치고, 받아쓰기는 말 아닌 소리를 무시한다
       (2026-09-10 「这个」 앞 군소리 지적). Whisper 가 잡은 첫 낱말 시작 앞 구간의 세기를 본다."""
    import tempfile, soundfile as sf, os as _os
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        sf.write(f.name, cut, SR); p = f.name
    try:
        segs, _ = _get_asr().transcribe(p, language='zh', word_timestamps=True, beam_size=5, vad_filter=False)
        words = [wd for s in segs for wd in s.words]
    finally:
        _os.unlink(p)
    if not words: return False
    t0 = words[0].start
    if t0 < 0.10: return False
    pre = np.abs(cut[:int((t0 - 0.03) * SR)])
    return len(pre) > 0 and float(pre.max()) > thr_ratio * (float(np.abs(cut).max()) + 1e-9)

# ──────────────────────────────────────────────────────────────────────
# 낱말 추가 검사 (2026-09-11 평가 코멘트에서: 너무 빠름 14 · 뒷글자 잘림 8 · 목소리 떨림 8 · 발음/성조 이상 10+)
# ──────────────────────────────────────────────────────────────────────
def _ends_clean(cut):
    e = np.abs(cut); pk = float(e.max()) + 1e-9
    return float(e[-int(SR * 0.06):].mean()) < 0.08 * pk

def _head_noisy(cut):
    import librosa
    y = librosa.resample(cut[:int(SR * 0.08)], orig_sr=SR, target_sr=16000)
    if len(y) < 600: return False
    return float(np.mean(librosa.feature.spectral_flatness(y=y))) > 0.35

def _trim_onset(cut):
    """첫 센 소리 앞의 잠잠한 자리에서 시작 — 앞에 숨은 군소리(「把」→「이바」)를 떼어 낸다."""
    e = np.abs(cut); pk = float(e.max()) + 1e-9
    strong = np.where(e > 0.40 * pk)[0]
    if len(strong) == 0: return cut
    first = int(strong[0]); need = int(SR * 0.04); quiet = 0; cutpt = None
    for i in range(first, 0, -1):
        if e[i] < 0.05 * pk:
            quiet += 1
            if quiet >= need: cutpt = i + need; break
        else: quiet = 0
    if cutpt is None or cutpt < SR * 0.05: return cut
    if float(e[:cutpt].max()) < 0.12 * pk: return cut
    return cut[max(0, cutpt - int(SR * 0.02)):]

def _f0(seg):
    import librosa
    y = librosa.resample(seg, orig_sr=SR, target_sr=16000)
    f0, _, _ = librosa.pyin(y, fmin=70, fmax=500, sr=16000, frame_length=1024)
    f = f0[~np.isnan(f0)]
    if len(f) < 5: return None
    med = float(np.median(f)); f = f[(f > med * 0.7) & (f < med * 1.4)]      # 옥타브 튐 걷어냄
    return f if len(f) >= 5 else None

def _jitter(cut):
    """목소리 떨림 — 이웃 틀 음높이 차의 상대값 평균."""
    f = _f0(cut)
    if f is None: return 0.0
    return float(np.mean(np.abs(np.diff(f))) / (np.mean(f) + 1e-9))

_NEUTRAL2 = ['关系','谢谢','名字','什么','知道','东西','衣服','朋友','我们','你们','他们','妈妈','爸爸','明白',
             '觉得','先生','漂亮','认识','意思','告诉','时候','学生','喜欢','头发','桌子','椅子','孩子','马虎','清楚','便宜']

def _tone_ok(cut, text):
    """음절마다 성조 흐름: 2성↑ 4성↓ 1성 안 떨어짐 3성 쭉 오르면 안 됨 경성 짧고 골 없이.
       한 글자는 통째로, 여러 글자는 소리 골로 나눠(못 나누면 균등) 잰다. 3성 연이음·不·一은 뺀다."""
    import librosa
    from pypinyin import pinyin, Style
    hz = ''.join(_HAN.findall(text)); n = len(hz)
    if n == 0: return True, ''
    tones = [x[0][-1:] for x in pinyin(hz, style=Style.TONE3, neutral_tone_with_five=True)]
    for wd in _NEUTRAL2:
        j = hz.find(wd)
        while j >= 0: tones[j + 1] = '5'; j = hz.find(wd, j + 1)
    skip = {i for i, c in enumerate(hz) if c in '不一'}
    skip |= {i for i in range(n - 1) if tones[i] == '3' and tones[i + 1] == '3'}
    if n == 1: spans = [(0, len(cut))]
    else:
        env = librosa.feature.rms(y=cut, frame_length=1024, hop_length=240)[0]
        env = np.convolve(env, np.ones(3) / 3, mode='same'); top = env.max() + 1e-9
        bounds = [0]; i = 1
        while i < len(env) - 1:
            if env[i] < top * 0.5 and env[i] <= env[i-1] and env[i] <= env[i+1] and (i - bounds[-1]) > 5:
                bounds.append(i); i += 5
            i += 1
        bounds.append(len(env))
        if len(bounds) - 1 == n: spans = [(bounds[k] * 240, bounds[k+1] * 240) for k in range(n)]
        else:
            L = len(cut); spans = [(int(L * k / n), int(L * (k + 1) / n)) for k in range(n)]
    avg = (len(cut) / SR) / n
    for k, (p0, p1) in enumerate(spans):
        if k in skip or p1 - p0 < SR * 0.10: continue
        f = _f0(cut[p0:p1])
        if f is None: continue
        kk = max(2, len(f) * 3 // 10)
        rise = (float(np.median(f[-kk:])) - float(np.median(f[:kk]))) / float(np.median(f))
        if abs(rise) > 0.6: continue
        imin = int(np.argmin(f)) / len(f); tn = tones[k]
        if tn == '5':
            if n >= 4 and (p1 - p0) / SR > avg * 1.45: return False, f'{hz[k]} 경성인데 김'
            if rise > 0.15 and imin < 0.4: return False, f'{hz[k]} 경성인데 골'
            continue
        if tn == '2' and rise < 0.03:  return False, f'{hz[k]} 2성인데 {rise*100:+.0f}%'
        if tn == '4' and rise > -0.03: return False, f'{hz[k]} 4성인데 {rise*100:+.0f}%'
        if tn == '1' and rise < -0.15: return False, f'{hz[k]} 1성인데 {rise*100:+.0f}%'
        if tn == '3' and rise > 0.12 and imin < 0.45: return False, f'{hz[k]} 3성인데 {rise*100:+.0f}% 오름'
    return True, ''

def word_extra_ok(cut, text):
    """낱말 추가 검사 한 벌. (통과 여부, 까닭, 다듬은 소리)"""
    n = max(1, len(_HAN.findall(text)))
    cut = _trim_onset(cut)
    dur = len(cut) / SR
    if dur < (0.30 if n == 1 else 0.26 * n): return False, '너무 빠름', cut
    if not _ends_clean(cut): return False, '끝이 잘림', cut
    if _head_noisy(cut): return False, '앞 잡음', cut
    if _jitter(cut) > 0.045: return False, '목소리 떨림', cut
    ok, why = _tone_ok(cut, text)
    if not ok: return False, why, cut
    return True, '', cut

def say_word(name, text, tries=8, ref_dir=None, temperature=0.60, verbose=False):
    """낱말 하나를 깔끔하게. 돌려주는 값: (wav, 보고) — 실패면 (None, 보고)"""
    want = ''.join(_HAN.findall(text)); n_syl = len(want)
    if want.endswith('儿') and n_syl > 1: n_syl -= 1      # 얼화는 한 음절
    passed, log = [], []
    # ⚠️ 한 프로세스에 모델을 둘 올리면 VRAM 이 넘친다 (앱 엔진 2GB + 모델 2GB×2 + Whisper 3GB).
    #    원본 모델로 다시 시도하는 건 부르는 쪽이 모델을 바꿔 가며 두 단계로 한다.
    who = name
    for t in range(tries):
        spd = 0.85 if any('너무 빠름' in l for l in log) else 1.0
        w, heard = say_clean(who, text, tries=1, ref_dir=ref_dir, temperature=temperature, speed=spd)
        if w is None:
            log.append(f'{t+1}: 낱말 못 찾음「{heard[:10]}」'); continue
        w = _tight(w)
        dur = len(w) / SR
        e_ = np.abs(w); pk = float(e_.max()) + 1e-9
        head_ratio = float(e_[:int(SR * 0.03)].mean()) / pk
        if head_ratio > 0.08:                 # 첫 30ms 가 이미 큰 소리 = 앞에 뭔가 붙어 있다
            log.append(f'{t+1}: 앞이 붙음 {head_ratio:.2f}'); continue
        if lead_junk(w):
            log.append(f'{t+1}: 앞에 군소리'); continue
        top = 0.95 if n_syl == 1 else 0.65 * n_syl + 0.15     # 한 음절(특히 3성)은 길어도 된다
        low = 0.22 if n_syl == 1 else 0.24 * n_syl          # 두 음절 0.48초, 세 음절 0.72초는 돼야 한다
        if not (low <= dur <= top):
            log.append(f'{t+1}: 길이 {dur:.2f}s 안 맞음'); continue
        syl = _syllables(w)
        # 한 음절은 엄격(「很很」을 거르는 잣대). 둘 이상은 경성(么·了)이 앞 음절에 묻히니 ±1
        if abs(syl - n_syl) > (0 if n_syl == 1 else 1):
            log.append(f'{t+1}: 음절 {syl}≠{n_syl}'); continue
        h = _hear_ctx(w)
        if h is None:
            log.append(f'{t+1}: 문맥 받아쓰기 실패'); continue
        ok = (h == want) or (_py(h) == _py(want)) or \
             (want.endswith('儿') and (h == want[:-1] or _py(h) == _py(want[:-1])))
        if not ok:
            log.append(f'{t+1}: 들린「{h}」≠「{want}」'); continue
        okx, whyx, w = word_extra_ok(w, text)
        if not okx:
            log.append(f'{t+1}: {whyx}'); continue
        q, head_flat = _quality(w)
        if head_flat > 0.45:
            log.append(f'{t+1}: 앞머리 잡음 {head_flat:.2f}'); continue
        passed.append((q, w)); log.append(f'{t+1}{"원" if who.startswith("원본") else ""}: 통과 (매끄러움 {q:.2f}, {dur:.2f}s)')
        if len(passed) >= 3: break          # 셋 모이면 그중 고른다
    if not passed:
        return None, ' / '.join(log)
    passed.sort(key=lambda x: -x[0])
    return passed[0][1], ' / '.join(log)


def say_sentence(name, text, tries=8, ref_dir=None, temperature=None, verbose=False):
    """예문·문장 하나를 검사 통과할 때까지 만든다 — make_sents.py 의 검사 한 벌을 그대로 쓴다.
       낱말(say_word)과 달리 문장은 낱말 사이 군소리·끝 약함까지 본다."""
    import importlib.util, sys as _sys, os as _os
    g = globals().setdefault('_SENT_NS', {})
    if not g:
        src = open('/app/finetune_data/_wordapp/make_sents.py', encoding='utf-8').read()
        head = src[:src.index('for si, s in enumerate(sents):')]
        who = (ref_dir or '').rsplit('/', 1)[-1] or '리리'
        bak = _sys.argv[:]; _sys.argv = ['x', '여', who, name, '0.5', 'new']
        exec(compile(head, 'h', 'exec'), g); _sys.argv = bak
    HAN = g['HAN']; hear_words = g['hear_words']; end_dip = g['end_dip']
    trim_to_onset = g['trim_to_onset']; verify = g['verify']
    who = (ref_dir or '').rsplit('/', 1)[-1]
    temp = temperature if temperature is not None else (0.45 if who == '리리' else 0.62)
    want = HAN(text); log = []
    if not want: return None, '한자 없음'
    for t in range(tries):
        spd = 0.85 if any('너무 빠름' in l for l in log) else (1.1 if any('너무 느림' in l for l in log) else 1.0)
        w = say(name, text, temperature=temp, ref_dir=ref_dir,
                max_seconds=min(28, 3 + 0.45 * len(want)), speed=spd)
        words = hear_words(w)
        if not words: log.append(f'{t+1}: 안 들림'); continue
        heard = HAN(''.join(x[0] for x in words))
        if not heard.startswith(want): log.append(f'{t+1}:「{heard[:8]}」'); continue
        acc = ''; end_t = None
        for txt, a, b in words:
            acc += HAN(txt)
            if len(acc) >= len(want): end_t = b; break
        st, _ = _refine(w, words[0][1], end_t, None); en = end_dip(w, end_t)
        cut = trim_to_onset(w[int(st * SR):int(en * SR)])
        why = verify(cut, want)
        if why: log.append(f'{t+1}: {why}'); continue
        return cut, ' / '.join(log)
    return None, ' / '.join(log)


SHORT_EX = 6      # 이 글자 수까지는 낱말로 본다

def say_example(name, text, tries=8, ref_dir=None):
    """예문 하나. ⚠️ 예문은 대부분 「一张纸」같은 3~5글자 구절이라 **낱말 검사**가 맞다.
       문장 검사를 씌우면 「앞이 붙음」이 무더기로 난다 (2026-09-12: 1144개 중 700개가 그랬다).
       6글자를 넘는 것만 문장으로 본다."""
    n = len(_HAN.findall(text))
    if n <= SHORT_EX:
        return say_word(name, text, tries=tries, ref_dir=ref_dir)
    return say_sentence(name, text, tries=tries, ref_dir=ref_dir)

def check_example(cut, text):
    """예문 검사 — 위와 같은 기준으로 갈라 본다. 돌려주는 값: 까닭('' 이면 괜찮음)"""
    n = len(_HAN.findall(text))
    want = ''.join(_HAN.findall(text))
    if n <= SHORT_EX:
        ok, why, _ = word_extra_ok(cut, text)
        if not ok: return why
        h = _hear_ctx(cut)
        if h is None: return '문맥 받아쓰기 실패'
        if not (h == want or _py(h) == _py(want)): return f'들린「{h[:8]}」≠'
        return ''
    g = globals().setdefault('_SENT_NS', {})
    if not g:
        import sys as _sys
        src = open('/app/finetune_data/_wordapp/make_sents.py', encoding='utf-8').read()
        head = src[:src.index('for si, s in enumerate(sents):')]
        bak = _sys.argv[:]; _sys.argv = ['x', '여', '리리', '리리', '0.5', 'new']
        exec(compile(head, 'h', 'exec'), g); _sys.argv = bak
    return g['verify'](cut, want)


def say_long(name, text, tries=6, ref_dir=None, gap=0.35):
    """60자 넘는 긴 글 — 문장부호로 쪼개 하나씩 만들고 이어 붙인다.
       ⚠️ XTTS 는 중국어 한 번에 82자까지라 통째로는 못 읽는다.
       한 토막이라도 끝내 실패하면 통째로 버린다 (반쪽짜리 소리를 남기지 않는다)."""
    import re as _re
    parts = [p for p in _re.findall(r'[^。！？；]*[。！？；]|[^。！？；]+$', text) if _HAN.search(p)]
    out = []
    for p in parts:
        if len(_HAN.findall(p)) > 55:                     # 그래도 길면 쉼표로 한 번 더
            sub = [q for q in _re.findall(r'[^，、]*[，、]|[^，、]+$', p) if _HAN.search(q)]
        else:
            sub = [p]
        for q in sub:
            w, rep = say_sentence(name, q, tries=tries, ref_dir=ref_dir)
            if w is None: return None, f'토막 실패「{q[:12]}」 {rep[-60:]}'
            out += [w, np.zeros(int(SR * gap), np.float32)]
    if not out: return None, '토막 없음'
    return np.concatenate(out[:-1]), ''

_PY_LAT = _re.compile(r'[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü\uac00-\ud7a3]+')

def clean_card_text(text):
    """읽을 글월만 남긴다 — 병음·한글은 걷어낸다.
       ⚠️ 「妈mā 麻má 马mǎ 骂mà」 같은 성조 연습 카드를 그대로 읽히면 병음까지 소리 내려 한다.
          숫자와 중국어 문장부호는 남긴다 (2600多平方米 처럼 뜻이 있다)."""
    t = _PY_LAT.sub(' ', text)
    t = _re.sub(r'[ \t]+', ' ', t).strip(' .．·-—')
    return t if _HAN.search(t) else text

def say_card(name, text, tries=8, ref_dir=None):
    """카드 하나 — 길이에 따라 낱말·문장·긴 글로 갈라 만든다."""
    text = clean_card_text(text)
    n = len(_HAN.findall(text))
    if n == 0: return None, '한자 없음'
    if n <= SHORT_EX: return say_word(name, text, tries=tries, ref_dir=ref_dir)
    if n <= 60: return say_sentence(name, text, tries=tries, ref_dir=ref_dir)
    return say_long(name, text, tries=max(4, tries // 2), ref_dir=ref_dir)

def check_card(cut, text):
    """카드 검사 — 같은 갈래로. 긴 글은 토막마다 못 보므로 받아쓰기와 앞뒤만 본다."""
    text = clean_card_text(text)
    n = len(_HAN.findall(text))
    if n == 0: return ''
    if n <= 60: return check_example(cut, text)
    want = ''.join(_HAN.findall(text))
    e = np.abs(cut); pk = float(e.max()) + 1e-9
    if float(e[:int(SR * 0.03)].mean()) / pk > 0.10: return '앞이 붙음'
    if float(e[-int(SR * 0.06):].mean()) > 0.10 * pk: return '끝이 잘림'
    if len(cut) / SR < 0.16 * n: return '너무 빠름'
    return ''
