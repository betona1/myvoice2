# -*- coding: utf-8 -*-
"""글자 → 그 글자가 든 단어 목록(word_index.json)과 낱글자 사전(char_dict.json)을
   현재 DB로 다시 만든다. 학습에서 뺀 항목(excluded)은 넣지 않는다.
   순서는 쉬운 것부터(seq) — 이어보기가 곧 학습 순서가 되도록."""
import os, json, sqlite3, collections
os.chdir('/app')
OUT = 'outputs/chinese'
CAP = 60                       # 한 글자당 최대 개수 (너무 많으면 목록이 못 쓰게 된다)
con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("""SELECT chinese,pinyin,meaning_ko,subject,seq,hsk
                      FROM words
                      WHERE COALESCE(excluded,0)=0 AND pinyin!='' AND meaning_ko!=''
                      ORDER BY COALESCE(seq,999999)""").fetchall()
con.close()

def han(s): return [c for c in s if '一' <= c <= '鿿']

idx = collections.defaultdict(list)
for r in rows:
    w = r['chinese']
    if len(w) > 6: continue                     # 문장 같은 긴 항목은 목록에 넣지 않는다
    for ch in set(han(w)):
        if ch == w: continue                    # 자기 자신은 뺀다
        idx[ch].append(([w, r['pinyin'], r['meaning_ko'], r['subject'] or ''],
                        r['hsk'] or 0, len(w), r['seq'] or 999999))

# 앞에 오는 것부터, 그 안에서는 HSK 급수 낮은 것부터.
# seq(난이도)는 성어를 쉽다고 보는 등 뒤엉켜 있어 보조 기준으로만 쓴다.
out = {}
for ch, lst in idx.items():
    lst.sort(key=lambda t: (0 if t[0][0].startswith(ch) else 1,
                            t[1] if t[1] > 0 else 9,   # 급수 없는 것은 뒤로
                            t[2], t[3]))
    out[ch] = [t[0] for t in lst[:CAP]]

cd = {}
for r in rows:
    if len(r['chinese']) == 1:
        cd[r['chinese']] = {'p': r['pinyin'], 'k': r['meaning_ko']}

for name, data in (('word_index.json', out),):     # char_dict 는 문장 속 글자까지 담고 있어 건드리지 않는다
    p = os.path.join(OUT, name)
    if os.path.exists(p): os.replace(p, p + '.bak')
    json.dump(data, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f"  {name}: {len(data)}자 · {os.path.getsize(p)//1024}KB")
big = sum(1 for v in out.values() if len(v) >= CAP)
print(f"\n{CAP}개로 잘린 글자 {big}자")
for ch in ('以', '所', '子'):
    v = out.get(ch, [])
    print(f"  {ch}: {len(v)}개  " + '  '.join(x[0] for x in v[:14]))
