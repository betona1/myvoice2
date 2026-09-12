# -*- coding: utf-8 -*-
"""senses_ko 는 영어 사전 뜻을 항목별로 기계 번역한 것이라
   ① 같은 말의 되풀이(我们 → 우리/우리를/우리 스스로/우리의)
   ② 영어 다의어를 엉뚱하게 고른 것(firm → 단단한, also pr. → 또한 홍보)
   이 섞여 있다. 이 둘을 걷어내고 서로 다른 뜻만 남긴다.
   사람이 손질한 것(예문 — 이나 ※ 가 있는 것)은 건드리지 않는다."""
import os, re, sys, sqlite3
os.chdir('/app')
DRY = '--commit' not in sys.argv

MARK = '①②③④⑤⑥⑦⑧⑨'
def split(s):
    s = s or ''
    if '①' not in s: return [s.strip()] if s.strip() else []
    return [x.strip(' ,;') for x in re.split(f'[{MARK}]', s) if x.strip(' ,;')]

def stem(t):
    """조사·어미를 떨어내 같은 말인지 견줄 수 있게 만든다."""
    t = re.sub(r'\([^)]*\)', '', t)            # 괄호 설명 제거
    t = re.sub(r'[^가-힣0-9]', '', t)
    for suf in ('하다','되다','스럽다','롭다','시키다','이다','습니다','합니다','세요','어요','아요',
                '다','은','는','이','가','을','를','의','에','게','고','서','한','함','음','기',
                '로','와','과','들','째','번'):
        if len(t) >= len(suf) + 1 and t.endswith(suf):
            t = t[:-len(suf)]; break
    return t

# 영어 사전에서 그대로 흘러든 찌꺼기 — 뜻이 아니다
NOISE = [
    r'^또한\s*(홍보|발음|pr)', r'^\(?기사\)?$', r'^\(?기타\)?$', r'^등$', r'^및$',
    r'^\d+$', r'^[a-zA-Z]+$', r'^약어', r'^줄임말$', r'^축약', r'^참조$', r'^동일$',
    r'^\(?의성어\)?$', r'^\(?구어\)?$', r'^\(?문어\)?$',
    r'\bsb\b', r'\bsth\b', r'비트$', r'^\(?그림\)?$',      # sb/sth·least bit 따위 영어 찌꺼기
]
NOISE = [re.compile(p) for p in NOISE]

# 영어 다의어를 반대쪽으로 잘못 고른 자리 — (버릴 말, 영어에 이게 있을 때만)
WRONG = [
    ('단단한','firm'), ('법인','corporation'), ('소설','novel'), ('현재의','present'),
    ('성냥','match'), ('법원','court'), ('사원','temple'), ('봄철','spring'),
    ('용수철','spring'), ('가을','fall'), ('이자','interest'), ('박람회','fair'),
    ('오른쪽','right'), ('계급','class'), ('물체','object'), ('아파트','flat'),
    ('평평한','flat'), ('빛','light'), ('가벼운','light'), ('친절한','kind'),
]

def clean(word, senses, en):
    it = split(senses)
    if len(it) < 2: return senses
    en = (en or '').lower()
    out, seen = [], []
    for t in it:
        if any(p.match(t) for p in NOISE): continue
        # 영어 다의어 오역: 첫 뜻이 아니면서, 그 영단어가 실제로 사전에 있는 경우만 버린다
        if out and any(t.strip(' .') == k and e in en for k, e in WRONG): continue
        s = stem(t)
        if not s: continue
        # 앞의 뜻과 같은 말이면 버린다 (한쪽이 다른 쪽에 들어가는 경우 포함)
        # 한쪽이 다른 쪽의 앞부분이면 같은 말로 본다 (둘 / 둘2, 알 / 알고, 많 / 많)
        if any(s == p or s.startswith(p) or p.startswith(s) for p in seen): continue
        seen.append(s); out.append(t)
        if len(out) >= 3: break                # 복습 카드에 3개면 넉넉하다
    if not out: return senses
    if len(out) == 1: return out[0]
    return ' '.join(f'{MARK[i]} {t}' for i, t in enumerate(out))

con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
rows = con.execute("SELECT id,chinese,meaning_ko,senses_ko,meaning_en FROM words WHERE COALESCE(excluded,0)=0").fetchall()
chg = []
for r in rows:
    s = r['senses_ko'] or ''
    if '—' in s or '※' in s: continue          # 사람이 손질한 것은 그대로 둔다
    n = clean(r['chinese'], s, r['meaning_en'])
    if n != s: chg.append((r['id'], r['chinese'], s, n))
print(f"{'미리보기' if DRY else '적용'}: {len(chg)}개 정리")
for i, ch, a, b in chg[:25]:
    print(f"  {ch:<7}{a[:56]}")
    print(f"  {'':<7}→ {b[:56]}")
if not DRY:
    for i, ch, a, b in chg: con.execute("UPDATE words SET senses_ko=? WHERE id=?", (b, i))
    con.commit(); print(f"\n{len(chg)}개 반영")
con.close()
