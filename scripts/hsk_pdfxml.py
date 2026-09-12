"""교안 PDF를 '쪽 → 줄 → 글도막' 꼴로 읽어 온다.

pdftohtml -xml 은 글자마다 위치·글꼴·색을 알려 준다.
쪽 그림(파워포인트 슬라이드)에서 지문·물음·보기를 갈라내려면 이 위치 정보가 있어야 한다.
(pdftotext 만으로는 '65．筷子是…' 첫 줄만 남고 뒤가 잘리는 일이 생겼다)
"""
import re
import subprocess
from functools import lru_cache

_FS = re.compile(r'<fontspec id="(\d+)" size="([\d.]+)" family="([^"]*)" color="(#[0-9a-fA-F]{6})"')
_TX = re.compile(r'<text top="(-?\d+)" left="(-?\d+)" width="(-?\d+)" height="(-?\d+)" font="(\d+)">(.*?)</text>', re.S)
_TAG = re.compile(r'<[^>]+>')
_ENT = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'", '&#39;': "'"}


def _unesc(s: str) -> str:
    s = _TAG.sub('', s)
    for k, v in _ENT.items():
        s = s.replace(k, v)
    return s


@lru_cache(maxsize=4)
def load_pages(pdf: str) -> dict:
    """{쪽번호(1부터): [도막…]}. 도막 = dict(top,left,w,h,size,color,text)"""
    xml = subprocess.run(['pdftohtml', '-xml', '-i', '-stdout', pdf],
                         capture_output=True, text=True, errors='replace').stdout
    fonts, out = {}, {}
    for chunk in re.split(r'(?=<page number=")', xml)[1:]:
        m = re.match(r'<page number="(\d+)"', chunk)
        if not m:
            continue
        pno = int(m.group(1))
        for f in _FS.finditer(chunk):
            fonts[f.group(1)] = (float(f.group(2)), f.group(3), f.group(4))
        runs = []
        for t in _TX.finditer(chunk):
            txt = _unesc(t.group(6)).strip()
            if not txt:
                continue
            size, fam, color = fonts.get(t.group(5), (0.0, '', '#000000'))
            runs.append({'top': int(t.group(1)), 'left': int(t.group(2)),
                         'w': int(t.group(3)), 'h': int(t.group(4)),
                         'size': size, 'family': fam, 'color': color, 'text': txt})
        out[pno] = runs
    return out


def lines(runs, tol: int = 14):
    """같은 높이의 도막을 한 줄로 묶는다. 줄은 위→아래, 도막은 왼→오른쪽."""
    rows = []
    for r in sorted(runs, key=lambda x: (x['top'], x['left'])):
        for row in rows:
            if abs(row[0]['top'] - r['top']) <= tol:
                row.append(r)
                break
        else:
            rows.append([r])
    for row in rows:
        row.sort(key=lambda x: x['left'])
    rows.sort(key=lambda x: x[0]['top'])
    return rows


def row_text(row) -> str:
    """한 줄의 글. 도막 사이가 크게 벌어지면 칸을 하나 둔다(보기 두 개가 한 줄일 때)."""
    out = ''
    prev = None
    for r in row:
        if prev is not None and r['left'] - (prev['left'] + prev['w']) > 18:
            out += ' '
        out += r['text']
        prev = r
    return out
