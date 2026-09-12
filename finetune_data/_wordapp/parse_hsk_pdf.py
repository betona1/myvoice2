# -*- coding: utf-8 -*-
"""교수님이 주신 HSK 4·5급 단어 PDF 에서 한자·병음·뜻을 뽑는다.

   ⚠️ 두 가지 함정
   1) 한 쪽이 **두 열**이다. 열을 먼저 갈라야 한다.
   2) 칸이 좁아 병음·뜻이 **다음 줄로 넘어간다** (参观 cān / guān).
      이어지는 줄을 '같은 열'의 앞 항목에 붙여야 한다.
"""
import re, json, subprocess, os
os.chdir('/home/joacham/projects/myvoice2/finetune_data/_hsk_media')
HAN = r'[一-鿿]'
PY_ = r'[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]'

def txt(p):
    return subprocess.run(['pdftotext', '-layout', p, '-'],
                          capture_output=True, text=True).stdout

def split_cols(t, guess):
    """줄마다 왼쪽/오른쪽 열로 가른다. 열 경계는 두 번째 항목이 시작하는 자리."""
    left, right = [], []
    for l in t.split('\n'):
        if not l.rstrip():
            continue
        starts = [m.start() for m in re.finditer(r'(?<!\S)\d{1,4}\s+' + HAN, l)]
        cut = next((s for s in starts if s >= guess - 12), None)
        if cut is None:
            # 항목이 하나뿐이거나 이어지는 줄 — 경계 근처의 큰 공백에서 가른다
            m = re.search(r'\s{4,}', l[guess - 12:]) if len(l) > guess - 12 else None
            cut = (guess - 12 + m.end()) if m else len(l)
        left.append(l[:cut].rstrip())
        right.append(l[cut:].rstrip())
    return left, right

def join_wrapped(lines):
    """번호로 시작하는 항목이 없는 줄은 앞 항목의 이어짐이다."""
    out = []
    for l in lines:
        if not l.strip():
            continue
        if re.match(r'\s*\d{1,4}\s+' + HAN, l):
            out.append(l)
        elif out:
            out[-1] = out[-1] + ' ' + l.strip()
    return out

def read_entry(cell, mode):
    m = re.match(r'\s*(\d{1,4})\s+(' + HAN + r'{1,6})\s+(.*)', cell, re.S)
    if not m:
        return None
    no, ch = int(m.group(1)), m.group(2)
    rest = re.sub(r'\s+', ' ', m.group(3)).strip()
    # 한자도 다음 줄로 넘어간다 (差不 + 多, 开玩 + 笑).
    # ⚠️ 뜻 안의 한자(5급은 한자 주석이 있다)까지 붙이면 안 되므로,
    #    '병음이 시작되기 전'에 나온 한자만 붙인다.
    if mode == 5:
        # 5급 뜻에는 한자 주석이 있다(구두점(句讀點)) → 병음 [ ] 앞의 한자만 붙인다
        m2 = re.search(r'\[', rest)
        head = rest[:m2.start()] if m2 else ''
        more = re.findall(HAN, head)
        if more:
            ch += ''.join(more)
            rest = rest[m2.start():] if m2 else rest
    else:
        # 4급 뜻은 한글뿐이라, 흩어진 한자는 모두 단어의 이어짐이다
        more = re.findall(HAN, rest)
        if more:
            ch += ''.join(more)
            rest = re.sub(HAN, '', rest).strip()
    if mode == 5:
        mm = re.match(r'\[([^\]]+)\]\s*(.*)', rest)
        if not mm:
            return None
        py, ko = mm.group(1).strip(), mm.group(2).strip()
    else:
        mm = re.match(r'((?:' + PY_ + r'+ ?)+)\s*(.*)', rest)
        if not mm:
            return None
        py, ko = mm.group(1).strip(), mm.group(2).strip()
        # 뜻 뒤에 이어붙은 병음 꼬리를 떼어 병음에 돌려준다 ([명]창문 guān hu)
        tail = re.search(r'\s((?:' + PY_ + r'+ ?){1,3})$', ko)
        if tail and not re.search(r'[가-힣]', tail.group(1)):
            py = (py + ' ' + tail.group(1)).strip()
            ko = ko[:tail.start()].strip()
    return (ch, {'no': no, 'pinyin': re.sub(r'\s+', ' ', py), 'ko': ko}) if ko else None

# 쪽 머리글·구역 표시는 항목이 아니다
JUNK = re.compile(r'编号|汉字|拼音|注解|词汇')
def parse(path, mode, guess):
    got = {}
    for col in split_cols(txt(path), guess):
        for line in join_wrapped(col):
            if JUNK.search(line):
                line = JUNK.sub(' ', line)
            # 홀로 선 알파벳 구역 표시(A B C …)를 지운다
            line = re.sub(r'(?<=\s)[A-Z](?=\s|$)', ' ', line)
            e = read_entry(line, mode)
            if not e:
                continue
            ch, v = e
            # 쪽 바닥글 '第 N 页' 의 第 가 단어 끝에 붙어 온다
            if len(ch) > 1 and ch.endswith('第'):
                ch = ch[:-1]
            if re.fullmatch(HAN + r'{1,6}', ch):
                got[ch] = v
    return got

d4 = parse('HSK4단어.pdf', 4, 36)
d5 = parse('HSK5급 단어.pdf', 5, 45)
print(f"4급 {len(d4)}개 (문서상 1200) · 5급 {len(d5)}개 (문서상 1300)\n")
for k in ('参观', '窗户', '聪明', '差不多', '乒乓球', '自行车', '信用卡', '开玩笑', '按时'):
    v = d4.get(k)
    print(f"   4급 {k:<6}{(v['pinyin'] if v else '(없음)'):<16}{(v['ko'][:34] if v else '')}")
print()
for k in ('哎', '办理', '不得了', '冰激凌'):
    v = d5.get(k)
    print(f"   5급 {k:<6}{(v['pinyin'] if v else '(없음)'):<16}{(v['ko'][:34] if v else '')}")
json.dump({'4': d4, '5': d5}, open('hsk_pdf_words.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
