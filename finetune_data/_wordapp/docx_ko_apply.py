# -*- coding: utf-8 -*-
"""교수님 자료의 해석을 본문 카드에 반영한다.
   ⚠️ 자료는 문단 단위, 카드는 여러 문단이 합쳐진 것이라 한 문단만 넣으면 내용이 깎인다.
      → 카드를 이루는 문단을 순서대로 모아, 카드 한자의 90% 이상을 덮을 때만 바꾼다."""
import re, os, sys, glob, zipfile, json, sqlite3
os.chdir('/app')
DRY = '--commit' not in sys.argv
HAN = r'[一-鿿]'
PY  = r'[a-zA-Zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]'

def docx_paras(path):
    x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8', 'ignore')
    out = []
    for para in re.findall(r'<w:p[ >].*?</w:p>', x, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>(.*?)</w:t>', para, re.S))
        t = t.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        if t.strip():
            out.append(t.strip())
    return out

def strip_py(t):
    t = re.sub('(' + PY + '+)(?=' + HAN + ')', '', t)
    t = re.sub(PY + '+', '', t)
    return t

def han_only(t):
    return re.sub(r'[^一-鿿]', '', t or '')

def is_ko(t):
    k = len(re.findall(r'[가-힣]', t)); h = len(re.findall(HAN, t))
    return k >= 8 and k > h * 2

# 자료에서 (한자본문, 해석) 짝을 파일 순서대로 모은다
seq = []
for f in sorted(glob.glob('finetune_data/_hsk_media/*.docx')):
    ps = docx_paras(f)
    for i, p in enumerate(ps):
        if is_ko(p) or len(re.findall(HAN, p)) < 6:
            continue
        # 한 중국어 문단에 한국어 해석이 여러 문단으로 이어지기도 한다 → 이어지는 것을 모두 모은다
        ko = []
        j = i + 1
        while j < len(ps) and is_ko(ps[j]):
            ko.append(ps[j].strip()); j += 1
        if ko:
            seq.append({'zh': han_only(strip_py(p)), 'ko': ' '.join(ko), 'f': os.path.basename(f)})

con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row
cards = con.execute("""SELECT id,week,class_num,group_name,chinese,meaning_ko
                       FROM chinese_cards WHERE subject='신HSK쓰기독해'
                         AND group_name IN ('본문','문제풀이')""").fetchall()

plan = []
for c in cards:
    ct = han_only(c['chinese'])
    if len(ct) < 20:
        continue
    # 이 카드 안에 통째로 들어가는 자료 문단을 순서대로 모은다
    parts, pos = [], 0
    for s in seq:
        if len(s['zh']) < 6:
            continue
        j = ct.find(s['zh'], pos)
        if j >= 0:
            parts.append((j, s)); pos = j + len(s['zh'])
    if not parts:
        continue
    cover = sum(len(s['zh']) for _, s in parts)
    ratio = cover / len(ct)
    if ratio < 0.9:
        continue
    ko = ' '.join(s['ko'] for _, s in parts)
    if ko.strip() == (c['meaning_ko'] or '').strip():
        continue
    plan.append({'id': c['id'], 'week': c['week'], 'cls': c['class_num'],
                 'g': c['group_name'], 'zh': c['chinese'][:40],
                 'old': (c['meaning_ko'] or ''), 'new': ko,
                 'cover': round(ratio, 3), 'n': len(parts)})

print(f"{'미리보기' if DRY else '적용'} — 자료가 카드 전체를 덮는 것 {len(plan)}장\n")
for p in plan:
    print(f"  {p['week']}주{p['cls']}교시 [{p['g']}] id={p['id']}  덮음 {int(p['cover']*100)}% ({p['n']}문단)")
    print(f"    본문 : {p['zh']}")
    print(f"    지금 : {p['old'][:76]}")
    print(f"    교안 : {p['new'][:76]}")
if not DRY:
    for p in plan:
        con.execute("UPDATE chinese_cards SET meaning_ko=? WHERE id=?", (p['new'], p['id']))
    con.commit()
    print(f"\n{len(plan)}장 반영")
json.dump(plan, open('finetune_data/_hsk_media/docx_ko_plan.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
con.close()
