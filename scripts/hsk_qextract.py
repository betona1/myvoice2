"""신HSK 교안 슬라이드에서 '문제'를 통째로 되살린다.

여태 교안은 낱말 단위로 쪼개져 chinese_cards 에 들어갔다.
그래서 지문·물음·보기가 서로 다른 카드로 흩어지고, 슬라이드가 여러 쪽에 걸친 문제는
첫 줄만 남아 잘려 보였다(65．筷子是中餐最主要的进餐用具，在使用上也有很多讲究。 로 끝나 버린 것).

여기서는 슬라이드 한 장을 '토막(block)'으로 읽고, 같은 문항 번호끼리 이어 붙여
지문·물음·보기·정답이 한 덩어리인 문제를 만든다.

문제 갈래
  blank  빈칸 채우기 (독해 1부분) — 보기 낱말은 슬라이드 그림에 있어 따로 넣어 준다
  order  순서 맞추기 (4급 독해 2부분) — 교안에 정답이 적혀 있다
  choice 알맞은 답 고르기 (5급 독해 2부분, 4·5급 독해 3부분)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hsk_pdfxml import load_pages, lines, row_text   # noqa: E402

HAN = r'一-鿿'
HANGUL = re.compile(r'[가-힣]')
CJK = re.compile(f'[{HAN}]')
QNUM = re.compile(r'^\s*(\d{1,3}(?:\s*[-~－]\s*\d{1,3})?)\s*[．.、]\s*(.*)$')
ANSROW = re.compile(r'^\s*([A-F])(?:\s+([A-F]))+\s*$')
OPTHEAD = re.compile(f'^\\s*([A-F])\\s*[.、．]?\\s*[{HAN}]')
BLANK = re.compile(r'[（(]\s*[)）]')


def parse_options(text):
    """'A能送弟弟新车 B坐上保罗的新车' → [('A','能送弟弟新车'),('B','坐上保罗的新车')].

    보기는 반드시 A→B→C… 차례로 나온다. 그 차례대로만 글자를 찾으면
    지문 속에 우연히 든 알파벳에 속지 않는다."""
    spans = []
    pos = 0
    for letter in 'ABCDEF':
        m = re.compile(f'{letter}\\s*[.、．]?\\s*(?=[{HAN}“”"‘’])').search(text, pos)
        if not m:
            break
        spans.append((letter, m.start(), m.end()))
        pos = m.end()
    out = []
    for i, (letter, _, end) in enumerate(spans):
        stop = spans[i + 1][1] if i + 1 < len(spans) else len(text)
        body = text[end:stop].strip(' 　')
        if body:
            out.append((letter, body))
    return out if len(out) >= 2 else []


def _clean(t):
    return re.sub(r'\s+', ' ', t).strip()


def _tight(t):
    """한자 사이에 낀 빈칸을 없앤다. 교안 줄바꿈이 '批评 时' 처럼 틈을 남긴다.
    교안 글꼴 탓에 말줄임표가 '„„' 로 깨져 나오는 것도 여기서 되돌린다."""
    t = _clean(t).replace('„„', '……').replace('„', '…')
    for _ in range(3):
        t = re.sub(f'(?<=[{HAN}，。、？！：；“”‘’（）])\\s+(?=[{HAN}，。、？！：；“”‘’（）])', '', t)
    return t


def read_slide(runs):
    """슬라이드 한 장 → {title, subtitle, markers, blocks}"""
    title, subtitle, markers, body = '', '', [], []
    for row in lines(runs):
        keep = []
        for r in row:
            if r['top'] < 60 or r['top'] > 770 or r['color'] == '#a6a6a6':
                continue                                   # 머리말·꼬리말
            if r['left'] > 880 and r['top'] > 730 and len(r['text']) <= 4:
                continue    # 오른쪽 아래 쪽번호. 글꼴이 빠진 곳에서는 '140' 이 'S' 로
                            # 깨져 나오기도 해서 숫자인지 따지지 않고 자리로만 가른다
            if r['size'] >= 40 and r['color'] == '#ffffff' and len(r['text']) <= 2:
                markers.append({'top': r['top'], 'letter': r['text']})
                continue
            keep.append(r)
        if not keep:
            continue
        txt = _clean(row_text(keep))
        if not txt:
            continue
        top = keep[0]['top']
        if top < 130 and (keep[0]['color'] == '#083988' or keep[0]['size'] >= 33):
            if not title:
                title = txt
                continue
        if top < 200 and HANGUL.search(txt) and not CJK.search(txt):
            if not subtitle:
                subtitle = txt
            continue
        if HANGUL.search(txt) and not CJK.search(txt):
            continue                                       # 한글 설명줄은 문제가 아니다
        body.append({'top': top, 'text': txt})

    blocks, cur = [], None

    def flush():
        nonlocal cur
        if cur and (cur['passage'] or cur['options'] or cur['stem']):
            blocks.append(cur)
        cur = None

    for row in body:
        txt = row['text']
        if ANSROW.match(txt) and len(txt.replace(' ', '')) <= 6:
            if cur is None:
                cur = {'qno': '', 'passage': [], 'stem': '', 'options': [], 'answer': ''}
            cur['answer'] = txt.replace(' ', '')
            continue
        m = QNUM.match(txt)
        if m:
            flush()
            cur = {'qno': re.sub(r'\s', '', m.group(1)).replace('~', '-').replace('－', '-'),
                   'passage': [], 'stem': '', 'options': [], 'answer': ''}
            rest = m.group(2).strip()
            if rest:
                if rest.startswith('★'):
                    cur['stem'] = rest.lstrip('★ ')
                elif OPTHEAD.match(rest):
                    cur['options'].append(rest)
                else:
                    cur['passage'].append(rest)
            continue
        if cur is None:
            cur = {'qno': '', 'passage': [], 'stem': '', 'options': [], 'answer': ''}
        if txt.startswith('★'):
            cur['stem'] = txt.lstrip('★ ').strip()
        elif OPTHEAD.match(txt):
            cur['options'].append(txt)
        else:
            cur['passage'].append(txt)
    flush()

    for b in blocks:
        b['passage'] = _tight(''.join(b['passage']))
        b['stem'] = _tight(b['stem'])
        b['options'] = [(k, _tight(v)) for k, v in parse_options(' '.join(b['options']))]
        # ★ 표 없이 '82．男孩儿的希望是什么？' 처럼 번호 뒤에 바로 물음이 오는 슬라이드가 있다.
        # 보기가 딸린 짧은 물음꼴 한 줄은 지문이 아니라 물음으로 본다.
        if b['options'] and not b['stem'] and b['passage'] and len(b['passage']) <= 45 \
                and re.search(r'[？：?:]$', b['passage']):
            b['stem'], b['passage'] = b['passage'], ''
    return {'title': title, 'subtitle': subtitle, 'markers': markers,
            'letters': [m['letter'] for m in markers], 'rows': body, 'blocks': blocks}


def _turns(slide):
    """빈칸 슬라이드의 대화 차례. 왼쪽 동그라미(A/B)마다 한 마디씩 묶는다."""
    marks = sorted([m for m in slide['markers'] if m['letter'] in 'ABCDEF'],
                   key=lambda m: m['top'])
    turns = []
    for row in slide['rows']:
        who = ''
        for m in marks:
            if m['top'] <= row['top'] + 40:
                who = m['letter']
        if turns and turns[-1]['who'] == who:
            turns[-1]['text'] += _tight(row['text'])
        else:
            turns.append({'who': who, 'text': _tight(row['text'])})
    return turns


def _rng(qno):
    m = re.match(r'^(\d+)-(\d+)$', qno or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def extract_section(pdf, start_page, end_page):
    """교안 한 교시(0부터 세는 쪽 범위) → 문제 목록"""
    pages = load_pages(pdf)
    slides = []
    for idx in range(start_page, end_page + 1):
        runs = pages.get(idx + 1)
        if not runs:
            continue
        s = read_slide(runs)
        s['page'] = idx + 1
        s['page_idx'] = idx
        slides.append(s)

    qs = {}          # 낱 문항  qno → 문제
    ranges = {}      # 'a-b'   → 묶음 지문
    order_n = 0
    blanks = []

    def put(qno, page, patch):
        q = qs.setdefault(qno, {'qno': qno, 'page': page, 'qtype': 'choice',
                                'passage': '', 'stem': '', 'options': [], 'answer': '',
                                'answer_src': ''})
        for k, v in patch.items():
            if not v:
                continue
            if k == 'passage':
                if len(v) > len(q['passage']):
                    q['passage'] = v
            elif not q.get(k):
                q[k] = v
        return q

    for s in slides:
        lvl = 5 if '5급' in s['title'] else 4
        for b in s['blocks']:
            rng = _rng(b['qno'])
            # ① 순서 맞추기 — 문항 번호가 없고 보기 3개 + 정답 줄
            if not b['qno'] and b['options'] and not b['passage'] and not b['stem'] \
                    and ('Q' in s['letters'] or b['answer']):
                order_n += 1
                qs[f'order{order_n}'] = {
                    'qno': str(order_n), 'page': s['page'], 'qtype': 'order', 'level': lvl,
                    'passage': '', 'stem': s['subtitle'] or '문장의 순서를 바르게 맞추세요',
                    'options': b['options'], 'answer': b['answer'],
                    'answer_src': 'textbook' if b['answer'] else '', 'title': s['title']}
                continue
            # ② 빈칸 채우기 — 괄호가 뚫린 대화·문장
            if not b['qno'] and not b['options'] and BLANK.search(b['passage']):
                blanks.append({'page': s['page'], 'title': s['title'],
                               'turns': _turns(s), 'text': b['passage']})
                continue
            if not b['qno']:
                # 번호 없이 지문만 이어지는 슬라이드 — 바로 앞 문항에 붙인다
                if blanks and BLANK.search(b['passage']):
                    blanks[-1]['text'] += b['passage']
                continue
            if rng:
                key = b['qno']
                slot = ranges.setdefault(key, {'passage': '', 'subs': [], 'page': s['page'],
                                               'level': lvl, 'title': s['title']})
                if len(b['passage']) > len(slot['passage']):
                    slot['passage'] = b['passage']
                if b['options']:
                    slot['subs'].append({'stem': b['stem'], 'options': b['options'],
                                         'page': s['page']})
                continue
            put(b['qno'], s['page'], {'passage': b['passage'], 'stem': b['stem'],
                                      'options': b['options'], 'level': lvl,
                                      'title': s['title'],
                                      'answer': b['answer'],
                                      'answer_src': 'textbook' if b['answer'] else ''})

    # 묶음 문항(80-81, 82-86)을 낱 문항으로 편다
    for key, slot in ranges.items():
        a, z = _rng(key)
        if slot['subs']:
            for i, sub in enumerate(slot['subs']):
                qno = str(a + i)
                if qno in qs and qs[qno].get('options'):
                    qs[qno]['passage'] = qs[qno]['passage'] or slot['passage']
                    qs[qno]['group'] = key
                    continue
                qs[qno] = {'qno': qno, 'page': sub['page'], 'qtype': 'choice',
                           'level': slot['level'], 'passage': slot['passage'],
                           'stem': sub['stem'], 'options': sub['options'],
                           'answer': '', 'answer_src': '', 'group': key,
                           'title': slot['title']}
        else:
            # 지문만 있는 묶음 — 뒤에 따로 실린 낱 문항들이 이 지문을 쓴다
            for n in range(a, z + 1):
                q = qs.get(str(n))
                if q and not q['passage']:
                    q['passage'] = slot['passage']
                    q['group'] = key

    out = [q for q in qs.values() if q.get('options')]
    for q in out:
        q.setdefault('level', 4)
        q.setdefault('group', '')
    out.sort(key=lambda q: (q['page'], int(re.sub(r'\D', '', q['qno']) or 0)))
    return {'questions': out, 'blanks': blanks, 'slides': slides}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', default='temp/신HSK쓰기독해/S02926.pdf')
    ap.add_argument('--start', type=int, required=True)
    ap.add_argument('--end', type=int, required=True)
    a = ap.parse_args()
    r = extract_section(a.pdf, a.start, a.end)
    print(json.dumps({'questions': r['questions'], 'blanks': r['blanks']},
                     ensure_ascii=False, indent=1))
