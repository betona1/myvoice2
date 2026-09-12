# -*- coding: utf-8 -*-
"""중국어 녹음을 XTTS 학습 자료로 자른다 — 대본에 맞춰 정렬하는 방식.

여기까지 오게 된 까닭(다시 밟지 말 것):
 ① 코퀴가 딸려 주는 자르개는 낱말이 `.` `!` `?` 로 끝날 때만 자른다.
    Whisper 의 중국어 전사에는 이 부호가 안 붙어 한 조각도 안 나온다.
 ② 그래서 쉼·길이로 잘랐더니 「到市」「场买菜」처럼 낱말 한가운데가 갈렸다.
    쉬지 않고 이어 읽은 녹음이라 자를 자리가 없어 길이로 억지로 끊긴 탓이다.
 ③ 게다가 전사에 부호가 없어 **부호 없이 끝나는 글월만** 학습시키게 된다.
    원본 XTTS 는 「。가 나오면 말을 끝낸다」고 배운 모델이라, 이렇게 학습하면
    낱말 하나를 시켜도 말을 안 멈추고 딴 문장을 지어낸다 (실제로 그랬다).

그래서 **읽으신 대본**을 기준으로 삼는다. 대본에는 문장 경계와 부호가 온전하다.
Whisper 로 글자마다 시각을 받아 대본과 맞춰(difflib) 문장의 시작·끝 시각을 얻는다.
길이는 두 갈래로 만든다 — 문장(2~7초)과 절 단위 짧은 토막(0.8~3초)."""
import os, re, sys, csv, glob, random, difflib
sys.path.insert(0, '/app'); os.chdir('/app')
import torch, torchaudio
from faster_whisper import WhisperModel

HAN = re.compile(r'[一-鿿]')
SCRIPT = 'temp/voicesample/녹음대본.txt'
LONG_MIN, LONG_MAX = 1.5, 7.0
SHORT_MIN, SHORT_MAX = 0.8, 3.0
PAD, EVAL_PCT = 0.12, 0.12


def load_scripts(path=SCRIPT):
    """대본을 편(篇)마다 나눠 문장 목록으로 만든다."""
    txt = open(path, encoding='utf-8').read()
    parts, cur = [], []
    for line in txt.splitlines():
        if re.match(r'^【\d】', line):
            if cur: parts.append(''.join(cur))
            cur = []
        elif HAN.search(line) and not line.startswith('='):
            cur.append(line.strip())
    if cur: parts.append(''.join(cur))
    out = []
    for p in parts:
        sents = [s for s in re.findall(r'[^。！？]*[。！？]', p) if HAN.search(s)]
        out.append(sents)
    return out


def clauses(sent):
    """문장을 절로 쪼갠다 — 쉼표 자리에서. 부호는 살려 둔다."""
    return [c for c in re.findall(r'[^，、；：]*[，、；：]?', sent) if HAN.search(c)]


def align(chars, script_sents):
    """Whisper 글자열과 대본을 맞춰, 문장마다 (시작 글자번호, 끝 글자번호)를 준다."""
    hay = ''.join(c for c, _, _ in chars)
    flat, spans = '', []
    for s in script_sents:
        body = ''.join(HAN.findall(s))
        spans.append((len(flat), len(flat) + len(body), s))
        flat += body
    sm = difflib.SequenceMatcher(None, flat, hay, autojunk=False)
    m = {}                                    # 대본 글자번호 → 녹음 글자번호
    for a, b, n in sm.get_matching_blocks():
        for i in range(n):
            m[a + i] = b + i
    out = []
    for a, b, s in spans:
        hit = [m[i] for i in range(a, b) if i in m]
        if len(hit) >= max(3, (b - a) * 0.5):   # 절반 넘게 맞아야 믿는다
            out.append((min(hit), max(hit), s))
    return out


def build(audio_files, out_dir, speaker):
    os.makedirs(f'{out_dir}/wavs', exist_ok=True)
    scripts = load_scripts()
    asr = WhisperModel('large-v3', device='cuda' if torch.cuda.is_available() else 'cpu',
                       compute_type='float16')
    rows, total = [], 0.0
    for path in audio_files:
        wav, sr = torchaudio.load(path)
        if wav.size(0) != 1: wav = wav.mean(dim=0, keepdim=True)
        wav = wav.squeeze(); dur_all = wav.size(-1) / sr; total += dur_all
        segs, _ = asr.transcribe(path, word_timestamps=True, language='zh',
                                 beam_size=5, vad_filter=False)
        chars = [(ch, w.start, w.end)
                 for s in segs for w in s.words for ch in HAN.findall(w.word)]
        if not chars:
            print(f"  {os.path.basename(path)}: 알아들은 게 없음"); continue

        # 어느 편을 읽은 녹음인지 고른다
        hay = ''.join(c for c, _, _ in chars)
        best = max(range(len(scripts)), key=lambda i: difflib.SequenceMatcher(
            None, ''.join(HAN.findall(''.join(scripts[i]))), hay, autojunk=False).ratio())
        sents = scripts[best]
        placed = align(chars, sents)
        base = os.path.splitext(os.path.basename(path))[0]
        nl = ns = 0

        def cut(i0, i1, text, tag):
            a = max(chars[i0][1] - PAD, 0)
            b = min(chars[i1][2] + PAD, dur_all)
            if b - a < 0.5: return False
            name = f'wavs/{base}_{tag}.wav'
            torchaudio.save(f'{out_dir}/{name}',
                            wav[int(a * sr):int(b * sr)].unsqueeze(0), sr)
            rows.append((name, text, speaker)); return True

        for i0, i1, sent in placed:
            if LONG_MIN <= chars[i1][2] - chars[i0][1] <= LONG_MAX:
                if cut(i0, i1, sent, f'L{nl:03d}'): nl += 1
            # 절 단위 짧은 토막
            k = i0
            for cl in clauses(sent):
                n = len(HAN.findall(cl))
                if n == 0: continue
                j = min(k + n - 1, i1)
                if SHORT_MIN <= chars[j][2] - chars[k][1] <= SHORT_MAX:
                    if cut(k, j, cl, f'S{ns:03d}'): ns += 1
                k = j + 1
                if k > i1: break
        print(f"  {os.path.basename(path)} ← 대본 {best+1}편 · 문장 {nl} · 절 {ns}", flush=True)

    del asr
    if torch.cuda.is_available(): torch.cuda.empty_cache()
    random.seed(0); random.shuffle(rows)
    n_ev = max(2, int(len(rows) * EVAL_PCT))
    ev, tr = sorted(rows[:n_ev]), sorted(rows[n_ev:])
    for fn, data in (('metadata_train.csv', tr), ('metadata_eval.csv', ev)):
        with open(f'{out_dir}/{fn}', 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f, delimiter='|', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
            w.writerow(['audio_file', 'text', 'speaker_name']); w.writerows(data)
    print(f"\n{speaker}: 조각 {len(rows)}개 (학습 {len(tr)} · 평가 {len(ev)}) · 원본 {total:.1f}초")


if __name__ == '__main__':
    build(sorted(glob.glob(f'{sys.argv[1]}/*.wav')), sys.argv[2], sys.argv[3])
