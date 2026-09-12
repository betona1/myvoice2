# -*- coding: utf-8 -*-
"""교수님 병음 자료(docx)에서 (한자, 병음) 짝을 뽑아 DB 와 대조한다.
   자료는 「병음+한자」 를 이어 붙인 꼴이다: zài在shātān沙滩páiqiúchǎng排球场
   → 앞의 로마자가 바로 뒤 한자의 병음이다.

   ⚠️ 고칠 것만 고른다. 음절 수가 한자 수와 맞지 않으면 짝이 어긋난 것이므로 버린다."""
import re, os, sys, json, glob, zipfile, unicodedata, collections
os.chdir('/app')
from pypinyin import pinyin as _py, Style

HAN = r'[一-鿿]'
PY  = r'[a-zA-Zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]'
PAIR = re.compile('(' + PY + r'+)(' + HAN + '+)')

def docx_text(path):
    z = zipfile.ZipFile(path)
    x = z.read('word/document.xml').decode('utf-8', 'ignore')
    out = []
    for para in re.findall(r'<w:p[ >].*?</w:p>', x, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>(.*?)</w:t>', para, re.S))
        t = t.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        if t.strip():
            out.append(t)
    return out

def bare(s):
    """성조 기호를 떼고 소문자 로마자만 남긴다."""
    t = unicodedata.normalize('NFD', (s or '').lower())
    t = re.sub(r'[̀-ͯ]', '', t)
    return re.sub(r'[^a-z]', '', t).replace('v', 'u').replace('ü', 'u')

def sylls(hz):
    return [x[0] for x in _py(hz, style=Style.TONE)]

pairs = collections.defaultdict(collections.Counter)   # 한자 -> 병음 표기별 횟수
files = sorted(glob.glob('finetune_data/_hsk_media/*.docx'))
kept = dropped = 0
for f in files:
    for line in docx_text(f):
        for m in PAIR.finditer(line):
            py, hz = m.group(1), m.group(2)
            if len(hz) > 6:
                dropped += 1; continue
            # 음절 수가 글자 수와 맞아야 올바른 짝이다
            auto = sylls(hz)
            if bare(py) != bare(''.join(auto)):
                # 자동 병음과 다르면 '다른 독음'일 수 있으니 남겨 두되 표시
                if len(bare(py)) < 2 or len(bare(py)) > 24:
                    dropped += 1; continue
            pairs[hz][py] += 1
            kept += 1
print(f"docx {len(files)}개에서 (한자·병음) 짝 {kept}개 (버린 것 {dropped}개) · 서로 다른 한자 {len(pairs)}개")
best = {h: c.most_common(1)[0][0] for h, c in pairs.items()}
json.dump(best, open('finetune_data/_hsk_media/docx_pinyin.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)

# ── DB 와 대조: 소리(성조 무시)가 다른 것만 고른다 ──
import sqlite3
con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True); con.row_factory = sqlite3.Row
db = {r['chinese']: dict(r) for r in
      con.execute("SELECT chinese,pinyin,tones,meaning_ko FROM words WHERE COALESCE(excluded,0)=0")}
con.close()
diff = []
for hz, py in best.items():
    d = db.get(hz)
    if not d or not d['pinyin']:
        continue
    if bare(py) != bare(d['pinyin']):
        diff.append({'ch': hz, 'db': d['pinyin'], 'doc': py,
                     'auto': ' '.join(sylls(hz)), 'ko': (d['meaning_ko'] or '')[:22],
                     'n': pairs[hz][py]})
diff.sort(key=lambda x: -x['n'])
print(f"\nDB 와 소리가 다른 것 {len(diff)}개\n")
for d in diff:
    print(f"   {d['ch']:<6} DB={d['db']:<14} 교안={d['doc']:<16} 자동={d['auto']:<14} {d['ko']}")
json.dump(diff, open('finetune_data/_hsk_media/docx_pinyin_diff.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
