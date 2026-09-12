"""블로그 글 꾸러미 만들기 — 단어 3개씩 한 편.

    python3 scripts/blog_build.py --set 동사
    python3 scripts/blog_build.py --set 동사 --only 2-6      # 2~6일차만
    python3 scripts/blog_build.py --all

한 편마다 폴더 하나가 생기고 그 안에 이런 것들이 담긴다.
    제목.txt          블로그 제목
    본문.html         스마트에디터에 붙여넣을 본문 (그림 자리는 [[...]] 로 표시)
    본문.txt          글자만 있는 판
    대표이미지.png     상단 대표 이미지 1장
    획순_<낱말>.gif    낱말마다 획순 따라쓰기 그림
    meta.json         제목·쇼츠주소·낱말·태그 — 자동 발행이 읽는다
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hanzi_gif import word_gif, _font                              # noqa: E402
from PIL import Image, ImageDraw                                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "database" / "voices.db"
OUT = ROOT / "temp" / "blog" / "out"
SHORTS = ROOT / "temp" / "blog" / "shorts.json"

APP_URL = "https://vaveling.app/"
CHANNEL = "https://www.youtube.com/@VAVELINGAPP"
SETS = ("동사", "형용사", "대명사", "양사", "접속사", "의성의태")

# 갈래마다 제목·안내문에 쓰는 말
LABEL = {"동사": ("동사", "기초 동사"), "형용사": ("형용사", "기초 형용사"),
         "대명사": ("인칭대명사", "인칭·지시 대명사"), "양사": ("양사", "양사"),
         "접속사": ("접속사", "접속사"), "의성의태": ("의성어·의태어", "의성어·의태어")}

CIRCLED = "①②③④⑤⑥"
# ①…㊿ — 51일차부터는 그냥 숫자로 적는다
_CIRC50 = ("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
           "㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟"
           "㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿")


def day_mark(n: int) -> str:
    return _CIRC50[n - 1] if 1 <= n <= len(_CIRC50) else f"{n}일차"
ERHUA_HEAD = set("这那哪点会玩事孩")


# ── 예문 다듬기 ─────────────────────────────────────────────────────────────
def _is_question(ko: str, cn: str) -> bool:
    return ko.strip().endswith("?") or cn.endswith("吗") or cn.endswith("呢")


def fix_cn(cn: str, q: bool) -> str:
    cn = cn.strip().rstrip("。？！,，")
    return cn + ("？" if q else "。")


def fix_py(cn: str, py: str, q: bool) -> str:
    """병음 다듬기 — 첫 글자 대문자, 끝에 마침표, 그리고 儿화 붙이기.

    자료에는 '这儿' 의 병음이 'zhè ér' 로 따로 적혀 있다. 실제로는 'zhèr' 로 소리 나므로
    한자와 병음을 한 칸씩 맞춰 보고 儿 앞 글자에 붙여 준다."""
    toks = py.strip().split()
    chars = list(cn)
    if len(toks) == len(chars):
        merged = []
        for i, (ch, tk) in enumerate(zip(chars, toks)):
            if ch == "儿" and merged and i > 0 and chars[i - 1] in ERHUA_HEAD:
                merged[-1] = merged[-1] + "r"
            else:
                merged.append(tk)
        toks = merged
    out = " ".join(toks).strip().rstrip(".?!")
    if out:
        out = out[0].upper() + out[1:]
    return out + ("?" if q else ".")


# ── 자료 읽기 ───────────────────────────────────────────────────────────────
def fix_ko(ko: str) -> str:
    ko = ko.strip()
    return ko if (not ko or ko[-1] in ".?!。？！…") else ko + "."


def load_words(wordset: str):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("""SELECT id, chinese, pinyin, meaning_ko, hsk, hsk_new, seq
                        FROM words WHERE wordset=? ORDER BY seq, id""", (wordset,)).fetchall()
    out = []
    for r in rows:
        ex = c.execute("""SELECT chinese, pinyin, meaning_ko FROM word_examples
                          WHERE word_id=? ORDER BY seq LIMIT 2""", (r["id"],)).fetchall()
        exs = []
        for e in ex:
            q = _is_question(e["meaning_ko"] or "", e["chinese"] or "")
            exs.append({"cn": fix_cn(e["chinese"], q),
                        "py": fix_py(e["chinese"], e["pinyin"] or "", q),
                        "ko": fix_ko(e["meaning_ko"] or "")})
        out.append({"id": r["id"], "cn": r["chinese"], "py": r["pinyin"] or "",
                    "ko": (r["meaning_ko"] or "").strip(),
                    "hsk": r["hsk"] or r["hsk_new"] or 0, "ex": exs})
    c.close()
    return out


def load_shorts():
    """{(갈래, 시작번호): 영상ID}"""
    m = {}
    if not SHORTS.exists():
        return m
    for s in json.loads(SHORTS.read_text()):
        m[(s["kind"], s["start"])] = s["vid"]
    # 낱말 하나만 담긴 쇼츠는 제목에 '(37/37)' 처럼 적혀 목록에서 빠진다 — 따로 넣어 둔다
    for extra in (("대명사", 37, "iFadV6l2NtQ"), ("양사", 73, "yYv42qL56G4")):
        m.setdefault((extra[0], extra[1]), extra[2])
    return m


# ── 대표 이미지 ─────────────────────────────────────────────────────────────
BG, INK, SUB, ACC = (255, 255, 255), (32, 34, 44), (122, 126, 140), (58, 96, 232)


def cover(words, wordset, day, out: Path, size=(1200, 630)):
    W, H = size
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 0, W, 10], fill=ACC)                       # 위쪽 띠
    kind = LABEL[wordset][0]

    dr.text((60, 58), f"HSK 중국어 단어 공부 · {day}일차", font=_font(34), fill=SUB)
    dr.text((60, 106), f"오늘의 {kind} {len(words)}개 — 뜻·병음·예문·획순",
            font=_font(42), fill=INK)

    # 낱말 칸 — 가로로 나눠 놓는다
    top, bot = 210, H - 96
    colw = (W - 120) // len(words)
    for i, w in enumerate(words):
        x = 60 + i * colw
        if i:
            dr.line([(x - 6, top + 16), (x - 6, bot - 16)], fill=(231, 232, 238), width=2)
        dr.text((x + 14, top), w["cn"], font=_font(96), fill=INK)
        dr.text((x + 14, top + 122), w["py"], font=_font(34), fill=ACC)
        ko = w["ko"]
        if len(ko) > 16:
            ko = ko[:15] + "…"
        dr.text((x + 14, top + 172), ko, font=_font(32), fill=SUB)
        if w["hsk"]:
            dr.text((x + 14, top + 224), f"HSK {w['hsk']}", font=_font(26), fill=(150, 154, 168))

    dr.line([(60, bot), (W - 60, bot)], fill=(231, 232, 238), width=2)
    dr.text((60, bot + 24), "VAVELING · 중국어 한자 획순 학습", font=_font(30), fill=SUB)
    tw = dr.textlength("vaveling.app", font=_font(30))
    dr.text((W - 60 - tw, bot + 24), "vaveling.app", font=_font(30), fill=ACC)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out


# ── 글 짜기 ─────────────────────────────────────────────────────────────────
def tags(wordset):
    base = ["#HSK중국어", "#HSK단어", "#중국어단어", "#중국어공부", "#기초중국어",
            "#한자공부", "#한자획순", "#중국어발음", "#중국어예문", "#하루3단어",
            "#베이블링", "#VAVELING"]
    base.insert(5, "#중국어" + LABEL[wordset][0])
    return base


def make_text(words, wordset, day, total, start, end, short_id):
    kind, kind_long = LABEL[wordset]
    dots = "·".join(w["cn"] for w in words)
    title = (f"HSK 중국어 단어 공부 {day_mark(day)} {dots}"
             f"｜{kind} 발음·한자 획순·예문")
    head = (f"HSK 중국어 단어 공부 {day}일차｜{kind_long} {dots}\n\n"
            f"중국어 {kind_long} {len(words)}개를 뜻·병음·발음·예문과 움직이는 한자 획순으로 "
            f"익혀요. 유튜브 쇼츠로 듣고 VAVELING에서 게임처럼 복습해 보세요.\n")

    blocks = [head]
    rng = f"({start}~{end}/{total})" if start != end else f"({start}/{total})"
    if short_id:
        blocks.append("🎬 유튜브 쇼츠로 학습하기\n"
                      f"{dots} — 발음·획순·예문 {rng}\n"
                      f"https://www.youtube.com/shorts/{short_id}\n")
    else:
        blocks.append("🎬 유튜브 영상으로 학습하기\n"
                      f"[[쇼츠주소]]   ← {dots} {rng} 쇼츠를 올린 뒤 주소를 넣어 주세요\n")

    for i, w in enumerate(words):
        lv = f" (HSK {w['hsk']})" if w["hsk"] else ""
        lines = [f"{CIRCLED[i]} {w['cn']} {w['py']} — {w['ko']}{lv}"]
        for e in w["ex"]:
            lines += [e["cn"], e["py"], e["ko"]]
        blocks.append("\n".join(lines) + "\n")

    blocks.append("✍️ 중국어 한자 획순 따라 쓰기\n"
                  f"아래 움직이는 획순을 보면서 {dots}의 글자를 공책에 세 번씩 써 보세요.\n"
                  + "\n".join(f"[[획순_{w['cn']}.gif]]" for w in words) + "\n")

    blocks.append("✅ 오늘의 HSK 단어 10초 복습\n"
                  + "\n".join(f"{w['cn']} — {w['ko']}" for w in words)
                  + "\n듣기 → 따라 읽기 → 한자 획순 쓰기 → 중국어 예문 말하기 순서로 "
                    "복습하면 오래 기억할 수 있어요.\n")

    blocks.append("🎮 VAVELING 앱에서 중국어 게임으로 복습하기\n"
                  f"설치 없이 바로 학습: {APP_URL}\n유튜브 채널: {CHANNEL}\n")
    blocks.append(" ".join(tags(wordset)))
    return title, "\n".join(blocks)


def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def make_html(title, body, words):
    """스마트에디터에 붙여넣을 본문. 그림은 붙여넣기로 안 들어가므로 자리만 표시한다."""
    out = [f"<h2>{esc(title)}</h2>", "<p>[[대표이미지]]</p>"]
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        lines = para.split("\n")
        if lines[0].startswith(("🎬", "✍️", "✅", "🎮")):
            out.append(f"<h3>{esc(lines[0])}</h3>")
            lines = lines[1:]
        for ln in lines:
            if ln.startswith("[["):
                out.append(f"<p><strong>{esc(ln)}</strong></p>")
            elif ln.startswith("http"):
                out.append(f'<p><a href="{ln}">{esc(ln)}</a></p>')
            elif re.match(r'^[①②③④⑤⑥]', ln):
                out.append(f"<p><strong>{esc(ln)}</strong></p>")
            elif ln.startswith("#"):
                out.append(f"<p>{esc(ln)}</p>")
            else:
                out.append(f"<p>{esc(ln)}</p>")
    return "<div>\n" + "\n".join(out) + "\n</div>\n"


# ── 한 편 만들기 ────────────────────────────────────────────────────────────
def build_one(wordset, day, words, total, short_id, gif=True):
    start = (day - 1) * 3 + 1
    end = start + len(words) - 1
    title, body = make_text(words, wordset, day, total, start, end, short_id)
    slug = "-".join(w["cn"] for w in words)
    d = OUT / wordset / f"{day:03d}_{slug}"
    d.mkdir(parents=True, exist_ok=True)

    (d / "제목.txt").write_text(title + "\n", encoding="utf-8")
    (d / "본문.txt").write_text(body, encoding="utf-8")
    (d / "본문.html").write_text(make_html(title, body, words), encoding="utf-8")
    cover(words, wordset, day, d / "대표이미지.png")

    gifs, miss = [], []
    for w in words:
        p = d / f"획순_{w['cn']}.gif"
        if gif and (not p.exists() or p.stat().st_size == 0):
            r = word_gif(w["cn"], p)
            if not r.get("ok"):
                miss.append(r)
                continue
        if p.exists():
            gifs.append(p.name)

    meta = {"wordset": wordset, "day": day, "range": [start, end], "total": total,
            "title": title, "shorts_id": short_id,
            "shorts_url": f"https://www.youtube.com/shorts/{short_id}" if short_id else "",
            "words": [{"cn": w["cn"], "py": w["py"], "ko": w["ko"], "hsk": w["hsk"]}
                      for w in words],
            "cover": "대표이미지.png", "gifs": gifs, "tags": tags(wordset),
            "missing_stroke_data": miss}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    return d, meta


def build(wordset, only=None, gif=True):
    ws = load_words(wordset)
    if not ws:
        print(f"[!] {wordset} 낱말이 없습니다")
        return []
    shorts = load_shorts()
    groups = [ws[i:i + 3] for i in range(0, len(ws), 3)]
    made = []
    for gi, g in enumerate(groups, 1):
        if only and gi not in only:
            continue
        sid = shorts.get((wordset, (gi - 1) * 3 + 1), "")
        d, meta = build_one(wordset, gi, g, len(ws), sid, gif=gif)
        flag = "" if sid else "  ← 쇼츠 주소 없음"
        bad = meta["missing_stroke_data"]
        if bad:
            flag += "  ⚠ 획순 자료 없는 글자: " + ", ".join(
                "".join(x.get("missing", [])) for x in bad)
        print(f"  {wordset} {gi:>3}일차  {'·'.join(w['cn'] for w in g):<14} {flag}")
        made.append(meta)
    return made


def write_index(all_meta):
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# 블로그 글 꾸러미 목록", "",
             f"모두 {len(all_meta)}편. 폴더마다 제목·본문·대표이미지·획순GIF가 들어 있습니다.", ""]
    for ws in SETS:
        sub = [m for m in all_meta if m["wordset"] == ws]
        if not sub:
            continue
        got = sum(1 for m in sub if m["shorts_id"])
        lines += [f"## {LABEL[ws][0]} — {len(sub)}편 (쇼츠 주소 있음 {got}편)", "",
                  "| 일차 | 낱말 | 번호 | 쇼츠 | 폴더 |", "|---|---|---|---|---|"]
        for m in sub:
            slug = "-".join(w["cn"] for w in m["words"])
            url = m["shorts_url"] or "**없음**"
            lines.append(f"| {m['day']} | {' · '.join(w['cn'] for w in m['words'])} | "
                         f"{m['range'][0]}~{m['range'][1]}/{m['total']} | {url} | "
                         f"`{ws}/{m['day']:03d}_{slug}` |")
        lines.append("")
    (OUT / "목록.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "목록.json").write_text(json.dumps(all_meta, ensure_ascii=False, indent=1),
                                   encoding="utf-8")


def parse_only(s):
    out = set()
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out |= set(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out or None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="wordset", choices=SETS)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", default="", help="예: 2-6 또는 1,3,5")
    ap.add_argument("--no-gif", action="store_true", help="획순 GIF는 건너뛰기 (빠르게 점검)")
    a = ap.parse_args()
    todo = list(SETS) if a.all else ([a.wordset] if a.wordset else [])
    if not todo:
        ap.error("--set 또는 --all 을 주세요")
    allm = []
    for ws in todo:
        print(f"\n▶ {ws}")
        allm += build(ws, parse_only(a.only), gif=not a.no_gif)
    write_index(allm)
    print(f"\n✅ {len(allm)}편 — {OUT}")
