# -*- coding: utf-8 -*-
"""들린 소리가 그 단어로 볼 수 있는지 따진다.
   Whisper 가 흔히 달리 적는 것들(숫자, 번체, 로마자, 얼화 생략,
   n/ng·권설음·n/l 흔들림)은 같은 것으로 친다."""
import re
from pypinyin import pinyin as _py, Style

_M = {'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
      'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'u','ǘ':'u','ǚ':'u','ǜ':'u','ü':'u'}
def syls(s):
    return [re.sub(r'[^a-z]', '', ''.join(_M.get(c, c) for c in x[0].lower()))
            for x in _py(s, style=Style.NORMAL)]
def loose(s):
    s = s.replace('zh','z').replace('ch','c').replace('sh','s')
    if s.endswith('ng'): s = s[:-1]
    if s[:1] == 'l': s = 'n' + s[1:]
    return s
def same(a, b): return bool(a) and loose(a) == loose(b)

DIG = '零一二三四五六七八九'
def _pos(n):
    if n < 10:  return DIG[n]
    if n < 20:  return '十' + (DIG[n%10] if n%10 else '')
    if n < 100: return DIG[n//10] + '十' + (DIG[n%10] if n%10 else '')
    if n < 1000:
        r, m = DIG[n//100] + '百', n % 100
        return r if not m else r + ('零' + DIG[m] if m < 10 else _pos(m))
    return None
# 영어 알파벳 이름의 소리 — 踢 을 "T" 로 적는 경우
LET = {'a':'ei','b':'bi','c':'xi','d':'di','e':'yi','f':'ef','g':'ji','h':'eichi','i':'ai','j':'jei',
       'k':'kei','l':'el','m':'em','n':'en','o':'ou','p':'pi','q':'kiu','r':'ar','s':'es','t':'ti',
       'u':'you','v':'wei','w':'dabliu','x':'eks','y':'wai','z':'zi'}

_T2S = None
def t2s(t):
    """번체로 받아적힌 것을 간체로 되돌린다 (音樂 → 音乐)."""
    global _T2S
    if _T2S is None:
        _T2S = {}
        try:
            for line in open('database/cedict_full.txt', encoding='utf-8', errors='ignore'):
                if line.startswith('#'): continue
                a = line.split(' ', 2)
                if len(a) >= 2 and len(a[0]) == 1 and len(a[1]) == 1 and a[0] != a[1]:
                    _T2S.setdefault(a[0], a[1])
        except Exception:
            pass
    return ''.join(_T2S.get(c, c) for c in t)

def cands(text):
    """들린 글에서 비교할 만한 후보들을 만든다. 한자는 그대로, 숫자는 두 가지 읽기로."""
    parts = re.findall(r'[0-9]+|[一-鿿]', text or '')
    outs = ['']
    for p in parts:
        if p.isdigit():
            v = [''.join(DIG[int(c)] for c in p)]
            q = _pos(int(p)) if int(p) < 1000 else None
            if q and q not in v: v.append(q)
            # 250 을 '二百五' 라 읽듯, 끝자리 十/百 은 흔히 생략한다
            v += [x[:-1] for x in list(v) if x.endswith(('十','百')) and len(x) > 2]
            # 2 는 二 로도 两 으로도 읽는다 (2点 = 两点)
            v += [x.replace('二', '两') for x in list(v) if '二' in x]
        else:
            v = [p] if p == t2s(p) else [p, t2s(p)]
        outs = [a + b for a in outs for b in v][:8]
    return [o for o in outs if o]

def roman(text):
    """한자 없이 로마자로만 적힌 경우의 소리 후보들.
       'T' 처럼 알파벳 이름으로 읽은 것, 'DR' 처럼 낱자를 이어 읽은 것도 함께 본다."""
    t = re.sub(r'[^A-Za-z]', '', text or '').lower()
    if not t: return []
    out = [t]
    if len(t) == 1 and t in LET: out.append(LET[t])
    if 2 <= len(t) <= 4 and all(c in LET for c in t):
        out.append(''.join(LET[c] for c in t))
    return out

def _erless(s): return re.sub(r'[儿兒]$', '', s) or s
def _erless_heard(word, s):
    """얼화는 而·兒 로도 적힌다 — 원 단어가 얼화일 때만 떼어 낸다."""
    if re.search(r'[儿兒]$', word): return re.sub(r'[儿兒而]$', '', s) or s
    return _erless(s)
_RANK = {'ok':0, 'extra':1, 'short':2, 'wrong':3, 'silent':4}

def _cmp(w, t):
    a, b = syls(w), syls(t)
    if len(a) == len(b):
        return 'ok' if all(same(x, y) for x, y in zip(a, b)) else 'wrong'
    if len(b) > len(a):
        for i in range(len(b) - len(a) + 1):
            if all(same(x, y) for x, y in zip(a, b[i:i+len(a)])): return 'extra'
        return 'wrong'
    return 'short'

def _lev(a, b):
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j-1] + 1, prev + (x != y))
    return d[-1]

def _snap_ok(word, heard):
    """한 글자는 받아적기가 더 긴 낱말로 스냅한다(幅→服务, 支→之).
       글자 수가 하나뿐인데 들린 말의 첫 음절이 같으면 제대로 읽은 것으로 본다."""
    if len(_erless(word)) != 1:
        return False
    t = ''.join(cands(heard)[:1]) or ''
    if not t:
        return False
    a, b = syls(word), syls(t)
    return bool(a and b and same(a[0], b[0]))

def judge(word, heard):
    """'ok' | 'extra'(군더더기) | 'short'(덜 들림) | 'wrong' | 'silent'"""
    # ⚠️ 목표 쪽도 문장부호를 걷어내야 한다. 안 그러면 「哎呀，我忘了」 처럼
    #    쉼표가 든 예문에서 음절 수가 어긋나 멀쩡한 음성이 틀린 것으로 잡힌다.
    word = ''.join(cands(word)[:1]) or word
    w = _erless(word)
    cs = cands(heard)
    if not cs:
        # 한자가 없이 로마자로만 적힌 경우 — 병음과 견준다
        wants = {''.join(loose(s) for s in syls(x)) for x in (w, word)}
        for r in roman(heard):
            got = loose(r)
            for want in wants:
                if got == want or (len(want) >= 4 and _lev(want, got) <= 1): return 'ok'
        return 'silent'
    v = min((_cmp(w, _erless_heard(word, c)) for c in cs), key=lambda k: _RANK[k])
    if v != 'ok':
        # 문장 가운데 얼화도 받아적기에서 자주 빠진다 (一点儿水 → 一点水)
        nw = w.replace('儿', '').replace('兒', '')
        if nw and nw != w:
            v2 = min((_cmp(nw, c.replace('儿', '').replace('兒', '')) for c in cs),
                     key=lambda k: _RANK[k])
            if v2 == 'ok':
                return 'ok'
    return 'ok' if (v != 'ok' and _snap_ok(word, heard)) else v
