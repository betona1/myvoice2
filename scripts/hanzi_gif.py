"""한자 획순 애니메이션 GIF 만들기.

브라우저에서는 hanzi-writer 가 획순을 그려 준다. 블로그에 넣을 그림은 서버에서 만들어야 해서,
같은 자료(hanzi-writer-data)를 받아 PIL 로 한 장씩 그린 뒤 GIF 로 엮는다.

자료의 좌표계 — 글자는 1024 폭 상자에 담기고, y 축이 위로 자란다(밑선 y=0, 위 y≈900).
화면 좌표로는 y' = 900 - y 로 뒤집는다.

한 획을 그리는 방법은 hanzi-writer 와 같다.
  ① 획의 테두리(닫힌 도형)를 칠해 둔다
  ② 획의 중심선(median)을 굵은 선으로 '지금까지 그린 길이'만큼 그려 덮개(mask)를 만든다
  ③ 덮개로 ①을 오려 내면 붓이 지나간 만큼만 보인다
"""
import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

DATA_DIR = Path(__file__).resolve().parent.parent / "temp" / "hanzi_data"
CDN = "https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1/{}.json"

BOX = 1024.0          # 자료 상자 크기
Y_TOP = 900.0         # 위쪽 기준선 (y' = Y_TOP - y)
PEN = 168             # 붓 굵기 (1024 기준)

_CMD = re.compile(r'([MLQCZmlqcz])([^MLQCZmlqcz]*)')
_NUM = re.compile(r'-?\d*\.?\d+')


# ── 자료 받아 두기 ──────────────────────────────────────────────────────────
def char_data(ch: str) -> dict | None:
    """글자 하나의 획 자료. 한 번 받아 temp/hanzi_data 에 담아 둔다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / f"{ord(ch):05x}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    url = CDN.format(urllib.parse.quote(ch))
    try:
        raw = urllib.request.urlopen(url, timeout=30).read().decode()
        d = json.loads(raw)
    except Exception:
        return None
    if not d.get("strokes"):
        return None
    f.write_text(json.dumps(d, ensure_ascii=False))
    return d


# ── 획 테두리 길 읽기 ───────────────────────────────────────────────────────
def _flat_quad(p0, p1, p2, n=10):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _flat_cubic(p0, p1, p2, p3, n=14):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
    return out


def stroke_polys(path: str):
    """SVG 길(path)을 닫힌 다각형 목록으로. 곡선은 잔 선분으로 펴서 쓴다."""
    polys, cur, start = [], [], None
    pt = (0.0, 0.0)
    for cmd, args in _CMD.findall(path):
        nums = [float(x) for x in _NUM.findall(args)]
        up = cmd.upper()
        rel = cmd.islower()

        def abspt(x, y):
            return (pt[0] + x, pt[1] + y) if rel else (x, y)

        if up == 'M':
            if len(cur) >= 3:
                polys.append(cur)
            pt = abspt(nums[0], nums[1])
            cur, start = [pt], pt
            for i in range(2, len(nums) - 1, 2):      # M 뒤에 좌표가 더 오면 L 로 본다
                pt = abspt(nums[i], nums[i + 1])
                cur.append(pt)
        elif up == 'L':
            for i in range(0, len(nums) - 1, 2):
                pt = abspt(nums[i], nums[i + 1])
                cur.append(pt)
        elif up == 'Q':
            for i in range(0, len(nums) - 3, 4):
                c = abspt(nums[i], nums[i + 1])
                e = abspt(nums[i + 2], nums[i + 3])
                cur += _flat_quad(pt, c, e)
                pt = e
        elif up == 'C':
            for i in range(0, len(nums) - 5, 6):
                c1 = abspt(nums[i], nums[i + 1])
                c2 = abspt(nums[i + 2], nums[i + 3])
                e = abspt(nums[i + 4], nums[i + 5])
                cur += _flat_cubic(pt, c1, c2, e)
                pt = e
        elif up == 'Z':
            if len(cur) >= 3:
                polys.append(cur)
            cur = [start] if start else []
            pt = start or pt
    if len(cur) >= 3:
        polys.append(cur)
    return polys


# ── 한 글자 그리기 ──────────────────────────────────────────────────────────
class CharPen:
    """글자 한 자를 획 하나씩 얹어 가며 그린다."""

    def __init__(self, ch, size, ink=(45, 45, 55), guide=(226, 226, 235)):
        self.ch, self.size, self.ink, self.guide = ch, size, ink, guide
        d = char_data(ch)
        self.ok = d is not None
        if not self.ok:
            self.strokes, self.medians = [], []
            return
        k = size / BOX
        self.strokes = [[[(x * k, (Y_TOP - y) * k) for (x, y) in poly]
                         for poly in stroke_polys(s)] for s in d["strokes"]]
        self.medians = [[(x * k, (Y_TOP - y) * k) for (x, y) in m] for m in d["medians"]]
        self.pen = max(6, int(PEN * k))

    @property
    def n(self):
        return len(self.strokes)

    def _fill(self, img, i, color):
        dr = ImageDraw.Draw(img)
        for poly in self.strokes[i]:
            dr.polygon(poly, fill=color)

    def _mask(self, i, frac):
        """중심선을 frac 만큼 따라간 덮개."""
        m = Image.new("L", (self.size, self.size), 0)
        dr = ImageDraw.Draw(m)
        pts = self.medians[i]
        if len(pts) < 2:
            pts = pts * 2
        segs = [math.dist(pts[j], pts[j + 1]) for j in range(len(pts) - 1)]
        total = sum(segs) or 1.0
        want = total * frac
        path, run = [pts[0]], 0.0
        for j, L in enumerate(segs):
            if run + L <= want:
                path.append(pts[j + 1]); run += L
            else:
                t = max(0.0, (want - run) / L)
                path.append((pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
                             pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t))
                break
        r = self.pen / 2
        if len(path) >= 2:
            dr.line(path, fill=255, width=self.pen, joint="curve")
        for p in path:                                 # 둥근 붓끝
            dr.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=255)
        return m

    def base(self, bg=(255, 255, 255)):
        """밑그림 — 옅은 글자 윤곽과 열십자 안내선."""
        img = Image.new("RGB", (self.size, self.size), bg)
        dr = ImageDraw.Draw(img)
        s, h = self.size, self.size // 2
        dr.rectangle([0, 0, s - 1, s - 1], outline=(233, 233, 240))
        for a in range(0, s, 16):                       # 점선 안내선
            dr.point((a, h), fill=(214, 214, 224))
            dr.point((h, a), fill=(214, 214, 224))
        for i in range(self.n):
            self._fill(img, i, self.guide)
        return img

    def frame(self, done, frac, bg=(255, 255, 255)):
        """획 done 개는 다 그려진 채, 그다음 획을 frac 만큼 그린 한 장."""
        img = self.base(bg)
        for i in range(min(done, self.n)):
            self._fill(img, i, self.ink)
        if done < self.n and frac > 0:
            lay = Image.new("RGB", (self.size, self.size), bg)
            self._fill(lay, done, self.ink)
            img.paste(lay, (0, 0), self._mask(done, frac))
        return img


# ── 낱말 GIF ────────────────────────────────────────────────────────────────
def word_gif(word: str, out: Path, size=210, pad=14, per_stroke=3,
             ms=70, hold_ms=1400, label=True):
    """낱말 하나의 획순 GIF. 글자를 나란히 놓고 앞 글자부터 차례로 그린다."""
    chars = [c for c in word if '一' <= c <= '鿿']
    pens = [CharPen(c, size) for c in chars]
    if not pens or not all(p.ok for p in pens):
        missing = [c for c, p in zip(chars, pens) if not p.ok]
        return {"ok": False, "missing": missing, "word": word}

    lab_h = 30 if label else 0
    W = pad + sum(size + pad for _ in pens)
    H = pad + size + pad + lab_h
    xs = [pad + i * (size + pad) for i in range(len(pens))]

    def compose(states):
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        for x, pen, (done, frac) in zip(xs, pens, states):
            canvas.paste(pen.frame(done, frac), (x, pad))
        if label:
            dr = ImageDraw.Draw(canvas)
            total = sum(p.n for p in pens)
            drawn = sum(min(d, p.n) for p, (d, _) in zip(pens, states))
            dr.text((pad, pad + size + 8), f"{word}   {drawn}/{total}",
                    fill=(120, 120, 134), font=_font(18))
        return canvas

    frames = [compose([(0, 0.0)] * len(pens))]
    for ci, pen in enumerate(pens):
        for si in range(pen.n):
            for f in range(1, per_stroke + 1):
                st = [(pens[j].n, 0.0) if j < ci else (0, 0.0) for j in range(len(pens))]
                st[ci] = (si, f / per_stroke)
                frames.append(compose(st))
    frames.append(compose([(p.n, 0.0) for p in pens]))

    dur = [ms] * (len(frames) - 1) + [hold_ms]
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:], loop=0,
                   duration=dur, optimize=True, disposal=2)
    return {"ok": True, "word": word, "chars": len(pens),
            "strokes": sum(p.n for p in pens), "frames": len(frames),
            "kb": round(out.stat().st_size / 1024, 1)}


_FONTS = {}
_FONT_PATHS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
               "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _font(px):
    if px in _FONTS:
        return _FONTS[px]
    from PIL import ImageFont
    for p in _FONT_PATHS:
        if Path(p).exists():
            try:
                _FONTS[px] = ImageFont.truetype(p, px)
                return _FONTS[px]
            except Exception:
                continue
    _FONTS[px] = ImageFont.load_default()
    return _FONTS[px]


if __name__ == "__main__":
    import sys
    for w in (sys.argv[1:] or ["喜欢"]):
        print(word_gif(w, Path("temp/blog/_test") / f"획순_{w}.gif"))
