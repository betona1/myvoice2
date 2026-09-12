# -*- coding: utf-8 -*-
"""교수님 병음 자료(docx)의 한국어 해석을 본문 카드와 대조한다.
   자료는 「병음 섞인 중국어 문단 → 바로 다음 문단에 한국어 해석」 꼴이다.
   병음을 걷어내면 순수 한자 본문이 나오므로, 그걸로 카드를 찾는다."""
import re, os, glob, zipfile, json, sqlite3
os.chdir('/app')
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
    """병음을 떼고 한자·문장부호만 남긴다."""
    t = re.sub('(' + PY + '+)(?=' + HAN + ')', '', t)     # 한자 앞의 병음
    t = re.sub(PY + '+', '', t)                           # 남은 로마자
    return re.sub(r'\s+', '', t)

def is_ko(t):
    k = len(re.findall(r'[가-힣]', t)); h = len(re.findall(HAN, t))
    return k >= 10 and k > h * 2

con = sqlite3.connect('file:database/voices.db?mode=ro', uri=True); con.row_factory = sqlite3.Row
cards = con.execute("""SELECT id,week,class_num,group_name,chinese,meaning_ko
                       FROM chinese_cards WHERE subject='신HSK쓰기독해'""").fetchall()
con.close()

def find_card(zh):
    """한자 본문으로 카드를 찾는다. 앞 20자가 겹치면 같은 지문으로 본다."""
    key = re.sub(r'[^一-鿿]', '', zh)[:20]
    if len(key) < 10: return None
    best = None
    for c in cards:
        t = re.sub(r'[^一-鿿]', '', c['chinese'] or '')
        if key and key in t:
            if best is None or len(t) < len(re.sub(r'[^一-鿿]', '', best['chinese'])):
                best = c
    return best

found, miss = [], []
for f in sorted(glob.glob('finetune_data/_hsk_media/*.docx')):
    ps = docx_paras(f)
    for i, p in enumerate(ps):
        if is_ko(p) or len(re.findall(HAN, p)) < 12:
            continue
        ko = ps[i+1] if i+1 < len(ps) and is_ko(ps[i+1]) else None
        if not ko: continue
        zh = strip_py(p)
        c = find_card(zh)
        (found if c else miss).append({'file': os.path.basename(f), 'zh': zh, 'ko': ko,
                                       'id': c['id'] if c else None,
                                       'week': c['week'] if c else None,
                                       'cls': c['class_num'] if c else None,
                                       'cur': (c['meaning_ko'] or '') if c else ''})
print(f"해석이 딸린 지문 {len(found)+len(miss)}개 · 카드를 찾은 것 {len(found)}개 · 못 찾은 것 {len(miss)}개\n")
for d in found:
    same = d['cur'].strip() == d['ko'].strip()
    print(f"  {d['week']}주{d['cls']}교시 id={d['id']}  {'(같음)' if same else ''}")
    print(f"    본문 : {d['zh'][:52]}")
    print(f"    지금 : {d['cur'][:70]}")
    print(f"    교안 : {d['ko'][:70]}")
json.dump({'found': found, 'miss': miss},
          open('finetune_data/_hsk_media/docx_ko.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
