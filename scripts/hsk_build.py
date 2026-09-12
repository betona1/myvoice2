"""교안 → 문제 꾸러미(hsk_questions) 만들기.

    docker exec myvoice2-prod python3 /app/scripts/hsk_build.py --week 3

하는 일
  1) 교안 슬라이드에서 문제를 통째로 뽑는다            (hsk_qextract)
  2) 빈칸 문제의 '주어지는 단어'는 슬라이드 그림에 있어 VLM 으로 읽는다
  3) 교안에 정답이 없는 객관식은 로컬 LLM 이 풀어 준다 (answer_src='ai' 로 구분)
  4) 지문·물음·보기의 한국어 뜻과 병음, 문법 짚을 거리를 함께 담는다
"""
import argparse
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hsk_qextract import extract_section                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("MYVOICE_DB", str(ROOT / "database" / "voices.db"))
SECTIONS = ROOT / "temp" / "textbook_sections.json"
OLLAMA = os.environ.get("OLLAMA_URL", "http://172.17.0.1:11434")
TXT_MODEL = os.environ.get("HSK_TXT_MODEL", "qwen2.5vl:7b")
VLM_MODEL = os.environ.get("HSK_VLM_MODEL", "qwen2.5vl:7b")

DDL = """
CREATE TABLE IF NOT EXISTS hsk_questions(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  subject     TEXT NOT NULL,
  week        INTEGER NOT NULL,
  class_num   INTEGER NOT NULL,
  page        INTEGER,            -- 교안 원본 쪽 (1부터)
  qno         TEXT,               -- 교안에 적힌 문항 번호
  grp         TEXT,               -- 지문을 함께 쓰는 묶음 ('80-81')
  qtype       TEXT,               -- blank | order | choice
  level       INTEGER,            -- 4 | 5
  passage     TEXT, passage_ko TEXT, passage_py TEXT,
  stem        TEXT, stem_ko TEXT, stem_py TEXT,
  turns       TEXT,               -- 빈칸형 대화 [{who,text}]
  options     TEXT, options_ko TEXT, options_py TEXT,
  answer      TEXT,
  answer_src  TEXT,               -- textbook | ai | manual
  explain_ko  TEXT,
  grammar     TEXT,               -- [{cn,ko}]
  sort_order  INTEGER,
  updated_at  TEXT,
  UNIQUE(subject, week, class_num, qtype, qno)
);
CREATE INDEX IF NOT EXISTS idx_hskq_unit ON hsk_questions(subject, week, class_num);
"""


# ── 로컬 LLM ────────────────────────────────────────────────────────────────
def _ollama(prompt, images=None, model=None, want_json=True, timeout=600, num_predict=600):
    """로컬 LLM 한 번 부르기.

    num_predict 를 반드시 걸어 둔다 — 작은 모델은 JSON 을 끝맺지 못하고
    같은 말을 끝없이 이어 붙이는 일이 있다(5천 토큰 넘게 돌던 적이 있다)."""
    body = {"model": model or TXT_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": num_predict}}
    if want_json:
        body["format"] = "json"
    if images:
        body["images"] = images
    req = urllib.request.Request(f"{OLLAMA}/api/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "")


def _jload(text, default=None):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'[\[{].*[\]}]', text or '', re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return default


# ── 빈칸 문제의 '주어지는 단어' 읽기 ────────────────────────────────────────
def ocr_word_bank(pdf, page):
    """슬라이드 위쪽 그림에 실린 보기 낱말을 읽어 [('A','危险'), …] 로 돌려준다."""
    from PIL import Image
    tmp = Path("/tmp/hskbank")
    tmp.mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-r", "150", "-f", str(page), "-l", str(page),
                    "-png", pdf, str(tmp / "p")], capture_output=True)
    pngs = sorted(tmp.glob("p*.png"))
    if not pngs:
        return []
    im = Image.open(pngs[-1])
    w, h = im.size
    crop = im.crop((0, int(h * 175 / 810), w, int(h * 460 / 810)))   # 제목 아래 ~ 대화 위
    buf = tmp / "bank.png"
    crop.save(buf)
    for png in pngs:
        png.unlink()
    b64 = base64.b64encode(buf.read_bytes()).decode()
    out = _ollama(
        '这是HSK阅读“选词填空”的备选词图。只输出JSON数组，'
        '按顺序列出每个字母和它后面的词，例如 ["A 危险","B 毕业"]。不要输出别的内容。',
        images=[b64], model=VLM_MODEL, timeout=300)
    got = _jload(out, [])
    # format=json 을 켜 두면 모델이 ["A 危险",…] 대신 {"A":"危险",…} 로 돌려주기도 한다
    if isinstance(got, dict):
        letters = {k.strip().upper(): v for k, v in got.items()
                   if re.fullmatch(r'[A-Fa-f]', str(k).strip())}
        if letters:
            return [(k, str(letters[k]).strip()) for k in sorted(letters)]
        got = next((v for v in got.values() if isinstance(v, list)), [])
    bank = []
    for item in got or []:
        m = re.match(r'\s*([A-F])[\s.、．:：]*(.+)$', str(item))
        if m:
            bank.append((m.group(1), m.group(2).strip()))
    return bank


# ── 정답·해석 ───────────────────────────────────────────────────────────────
def solve_choice(passage, stem, options):
    lines = "\n".join(f"{k} {v}" for k, v in options)
    p = f"""你是HSK阅读老师。请阅读短文，选出唯一正确的答案。

【短文】
{passage or '(무)'}

【问题】{stem or '根据这段话，下列哪项正确？'}
{lines}

只输出JSON：{{"answer":"A","why":"选它的理由，用韩语一两句"}}"""
    d = _jload(_ollama(p, num_predict=300), {}) or {}
    a = str(d.get("answer", "")).strip().upper()[:1]
    return (a if a in [k for k, _ in options] else ""), str(d.get("why", "")).strip()


def solve_blank_set(items, bank):
    """빈칸 여럿을 한꺼번에 짝지어 준다.

    보기 낱말은 한 번씩만 쓰이므로, 문제를 하나씩 따로 물으면
    앞에서 잘못 고른 낱말 때문에 뒤가 줄줄이 밀린다(3주 1교시에서 실제로 그랬다).
    전체를 한 판으로 놓고 짝지어야 서로 어긋나지 않는다."""
    lines = "\n".join(f"{k} {v}" for k, v in bank)
    body = "\n\n".join(
        f"({i + 1})\n" + "\n".join(f"{t['who']}: {t['text']}" for t in it)
        for i, it in enumerate(items))
    p = f"""这是HSK4级阅读第一部分“选词填空”。备选词每个只能用一次，请给每道题配一个词。

【备选】
{lines}

【题目】
{body}

只输出JSON，不要解释：{{"answers":["字母","字母"]}}  （共{len(items)}个，按题号顺序）"""
    d = _jload(_ollama(p, timeout=600, num_predict=120), {}) or {}
    got = d.get("answers") if isinstance(d, dict) else None
    if isinstance(got, dict):
        got = [got[k] for k in sorted(got)]
    letters = [str(x).strip().upper()[:1] for x in (got or [])]
    valid = {k for k, _ in bank}
    return [(letters[i] if i < len(letters) and letters[i] in valid else "")
            for i in range(len(items))]


def solve_blank(turns, bank, used):
    """빈칸에 들어갈 낱말 고르기. 이미 쓴 낱말은 빼고 물어본다(보기는 한 번씩 쓴다)."""
    left = [(k, v) for k, v in bank if k not in used] or bank
    body = "\n".join(f"{t['who']}: {t['text']}" for t in turns)
    lines = "\n".join(f"{k} {v}" for k, v in left)
    p = f"""这是HSK4级阅读第一部分“选词填空”。括号（）里应该填哪个词？

【对话】
{body}

【备选】
{lines}

只输出JSON：{{"answer":"C","why":"왜 그 낱말인지 한국어 한 문장"}}"""
    d = _jload(_ollama(p, num_predict=300), {}) or {}
    a = str(d.get("answer", "")).strip().upper()[:1]
    return (a if a in [k for k, _ in bank] else ""), str(d.get("why", "")).strip()


_HAN_RE = re.compile(r'[\u4e00-\u9fff]')


def _clean_ko(t):
    """앞머리에 붙은 'A.' 같은 표시를 뗀다 — 화면에서 글자는 따로 보여 준다.

    모델이 {"ko": "...", "zh": "..."} 같은 덩이로 돌려주는 일도 있어 한국어 쪽만 꺼낸다."""
    if isinstance(t, dict):
        t = t.get("ko") or t.get("korean") or t.get("ko_kr") or ""
    # 'A 危险' 처럼 글자 뒤에 구분 표시나 빈칸이 있을 때만 뗀다.
    # 그냥 '[A-F]' 하나를 무조건 떼면 'A는 항상 통화 중' 이 '는 항상 통화 중' 이 된다.
    t = re.sub(r'^\s*(?:보기\s*)?[A-F](?:\s*[.)．、:：]\s*|\s+)', '', str(t or '').strip())
    return t.strip()


def _untranslated(t):
    """한자가 3할을 넘으면 옮기다 만 것으로 본다."""
    t = str(t or '')
    body = re.sub(r'\s', '', t)
    return not body or len(_HAN_RE.findall(body)) > len(body) * 0.3


def _split_ko(text, n):
    """보기 번역이 한 덩이로 붙어 나올 때 n 조각으로 가른다."""
    t = str(text or '')
    for parts in (re.split(r'[\n;·]+', t),
                  re.split(r'(?=(?:^|\s)[A-F]\s*[.)．、:：])', t)):
        got = [_clean_ko(x) for x in parts if x and x.strip()]
        if len(got) == n:
            return got
    return []


def blank_why(turns, bank, letter):
    """고른 낱말이 왜 맞는지 한 문장으로."""
    word = dict(bank).get(letter, "")
    body = "\n".join(f"{t['who']}: {t['text']}" for t in turns)
    p = f"""아래 대화의 괄호에 '{word}'({letter})가 들어간다. 왜 그런지 한국어 한 문장으로 쓰라.

{body}

JSON만 출력: {{"why":"한 문장"}}"""
    d = _jload(_ollama(p, num_predict=150), {}) or {}
    return str(d.get("why", "")).strip()


_PSG_MEMO, _STEM_MEMO, _OPT_MEMO = {}, {}, {}


def _ask_ko(label, text, cap, tries):
    """중국어 한 토막을 한국어로. 옮기다 만 것 같으면 다시 묻는다."""
    prompt = f"""아래 중국어 {label}을 자연스러운 한국어로 옮겨라.
한국어로만 쓰고, 중국어를 그대로 두지 않는다.

{text}

JSON만 출력: {{"ko":""}}"""
    best = ""
    for _ in range(tries):
        d = _jload(_ollama(prompt, num_predict=cap), {}) or {}
        v = _clean_ko(d.get("ko") if isinstance(d, dict) else "")
        if len(v) > len(best):
            best = v
        if v and not _untranslated(v):
            return v
    return best


def _tr_passage(passage):
    """지문 번역. 80-81, 82-86 처럼 지문을 함께 쓰는 문항은 한 번만 묻는다
    (문항마다 되물으면 427자 지문을 다섯 번 옮기게 된다)."""
    if not passage:
        return ""
    if passage not in _PSG_MEMO:
        long = len(passage) > 200
        _PSG_MEMO[passage] = _ask_ko("지문", passage,
                                     cap=min(2000, len(passage) * 3 + 300),
                                     tries=1 if long else 3)
    return _PSG_MEMO[passage]


def _tr_stem(stem):
    if not stem:
        return ""
    if stem not in _STEM_MEMO:
        _STEM_MEMO[stem] = _ask_ko("물음", stem, cap=300, tries=2)
    return _STEM_MEMO[stem]


def _translate_options(options):
    """보기만 따로 옮긴다. 조각 수가 맞고 한국어인지 확인한다."""
    if not options:
        return []
    key = tuple(v for _, v in options)
    if key in _OPT_MEMO:
        return _OPT_MEMO[key]
    # 보기 앞에 A·B·C 를 붙여 물으면 그 글자까지 문장으로 옮겨 'A는 …' 이 되어 버린다.
    # 그래서 번호만 달아 보낸다.
    lines = "\n".join(f"{i + 1}) {v}" for i, (_, v) in enumerate(options))
    prompt = f"""아래 중국어 문장 {len(options)}개를 한국어로 옮겨라.

규칙
- 한국어로만 쓴다. 중국어를 그대로 두지 않는다.
- 번호 순서대로 배열 원소 {len(options)}개로 만든다.
- 번호나 알파벳을 번역문 앞에 붙이지 않는다.

{lines}

JSON만 출력: {{"options_ko":[]}}"""
    src = [v.replace(" ", "") for _, v in options]
    best = []
    for _ in range(3):
        d = _jload(_ollama(prompt, num_predict=600), {}) or {}
        oks = d.get("options_ko") if isinstance(d, dict) else None
        if isinstance(oks, str):
            oks = _split_ko(oks, len(options))
        elif isinstance(oks, list):
            oks = [_clean_ko(x) for x in oks]
            if len(oks) == 1 and len(options) > 1:
                oks = _split_ko(oks[0], len(options))
        else:
            oks = []
        copied = sum(1 for i, x in enumerate(oks)
                     if i < len(src) and x.replace(" ", "") == src[i])
        if len(oks) > len(best):
            best = oks
        if len(oks) == len(options) and not copied \
                and not any(_untranslated(x) for x in oks):
            best = oks
            break
    _OPT_MEMO[key] = best
    return best


def translate(passage, stem, options):
    """지문·물음·보기의 한국어 뜻.

    작은 모델은 한 번에 다 물으면 답이 길어져 JSON 을 끝맺지 못하고 통째로 날아간다
    (80번 지문 해석이 그렇게 비어 있었다). 그래서 셋으로 나눠 묻고, 각각 담아 둔다."""
    return _tr_passage(passage), _tr_stem(stem), _translate_options(options)


_GRAM_MEMO = {}


def grammar_points(text):
    """문장 속 문법·짝꿍 표현 짚어 주기.

    82-86 처럼 지문 하나에 물음이 여럿 달린 문제는 같은 지문을 되묻게 된다 — 한 번만 묻고 담아 둔다."""
    if not text:
        return []
    if text in _GRAM_MEMO:
        return _GRAM_MEMO[text]
    p = f"""다음 중국어 문장에서 HSK 학습자가 짚어야 할 문법·짝꿍표현을 최대 3개 고르라.

{text}

JSON만 출력: {{"points":[{{"cn":"被","ko":"피동문 — 'A被B+동사'로 'A가 B에게 ~당하다'"}}]}}"""
    d = _jload(_ollama(p, num_predict=500), {}) or {}
    out = []
    for x in (d.get("points") or [])[:3]:
        cn, ko = str(x.get("cn", "")).strip(), str(x.get("ko", "")).strip()
        if cn and ko:
            out.append({"cn": cn, "ko": ko})
    _GRAM_MEMO[text] = out
    return out


_BLANK = re.compile(r'^(.*?)[（(]\s*[)）](.*)$', re.S)


def _split_blank(text):
    """괄호를 사이에 두고 앞뒤로 가른다. 괄호가 없으면 앞쪽에 통째로 둔다."""
    m = _BLANK.match(text or "")
    if not m:
        return {"pre": text or "", "pre_py": pinyin_of(text), "post": "", "post_py": "",
                "has_blank": False}
    pre, post = m.group(1), m.group(2)
    return {"pre": pre, "pre_py": pinyin_of(pre), "post": post, "post_py": pinyin_of(post),
            "has_blank": True}


def pinyin_of(text):
    if not text:
        return ""
    try:
        from pypinyin import pinyin, Style
        return " ".join(x[0] for x in pinyin(text, style=Style.TONE))
    except Exception:
        return ""


# ── 담기 ────────────────────────────────────────────────────────────────────
def save(rows):
    c = sqlite3.connect(DB)
    c.executescript(DDL)
    have = {r[1] for r in c.execute("PRAGMA table_info(hsk_questions)")}
    for col in ("stem_py",):                       # 먼저 만든 표에 칸을 보탠다
        if col not in have:
            c.execute(f"ALTER TABLE hsk_questions ADD COLUMN {col} TEXT")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        c.execute("""INSERT INTO hsk_questions
            (subject,week,class_num,page,qno,grp,qtype,level,passage,passage_ko,passage_py,
             stem,stem_ko,stem_py,turns,options,options_ko,options_py,answer,answer_src,
             explain_ko,grammar,sort_order,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(subject,week,class_num,qtype,qno) DO UPDATE SET
             page=excluded.page, grp=excluded.grp, level=excluded.level,
             passage=excluded.passage, passage_ko=excluded.passage_ko,
             passage_py=excluded.passage_py, stem=excluded.stem, stem_ko=excluded.stem_ko,
             stem_py=excluded.stem_py,
             turns=excluded.turns, options=excluded.options, options_ko=excluded.options_ko,
             options_py=excluded.options_py,
             answer=CASE WHEN hsk_questions.answer_src='manual' THEN hsk_questions.answer
                         ELSE excluded.answer END,
             answer_src=CASE WHEN hsk_questions.answer_src='manual' THEN 'manual'
                             ELSE excluded.answer_src END,
             explain_ko=excluded.explain_ko, grammar=excluded.grammar,
             sort_order=excluded.sort_order, updated_at=excluded.updated_at""",
            (r["subject"], r["week"], r["class_num"], r["page"], r["qno"], r["grp"],
             r["qtype"], r["level"], r["passage"], r["passage_ko"], r["passage_py"],
             r["stem"], r["stem_ko"], r["stem_py"], r["turns"], r["options"], r["options_ko"],
             r["options_py"], r["answer"], r["answer_src"], r["explain_ko"],
             r["grammar"], r["sort_order"], now))
    c.commit()
    c.close()


_NOISE = re.compile(r'[^\u4e00-\u9fff]')


def prune_cards(subject, week, cls, rows):
    """되살린 문제와 겹치는 낡은 '문제풀이' 카드를 접는다.

    교안을 낱말 단위로 뽑던 시절, 한 문제가 카드 여러 장으로 흩어지면서
    '65．筷子是中餐最主要的进餐用具，在使用上也有很多讲究。' 처럼 첫 줄만 남은 카드가 생겼다.
    이제 문제는 문제 갈래에서 온전히 보여 주므로, 그 조각들은 보이지 않게 한다.
    (지우지 않고 hidden=1 로만 둔다 — 되돌릴 수 있어야 한다)"""
    whole = []
    for r in rows:
        if r["class_num"] != cls:
            continue
        body = (r["passage"] or "") + (r["stem"] or "") + " ".join(json.loads(r["options"] or "[]"))
        w = _NOISE.sub("", body)
        if len(w) >= 8:
            whole.append(w)
    if not whole:
        return 0
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    hit = []
    try:
        for r in c.execute("""SELECT id, chinese FROM chinese_cards
                              WHERE subject=? AND week=? AND class_num=? AND group_name='문제풀이'
                                AND COALESCE(hidden,0)=0""", (subject, week, cls)):
            w = _NOISE.sub("", r["chinese"] or "")
            if len(w) >= 6 and any(w in t for t in whole):
                hit.append(r["id"])
        if hit:
            c.executemany("UPDATE chinese_cards SET hidden=1 WHERE id=?", [(i,) for i in hit])
            c.commit()
    except sqlite3.Error as e:          # 카드 표가 없는 DB(시험용)에서도 뽑기는 이어져야 한다
        print(f"   [알림] 낡은 카드 정리 건너뜀: {e}")
    finally:
        c.close()
    return len(hit)


def refresh_text(subject, week, classes):
    """뽑기 규칙을 고친 뒤, 이미 담긴 문제의 '글'만 다시 채운다.

    정답·해석·해설·문법은 그대로 두므로 LLM 을 다시 부르지 않는다.
    (교안 쪽번호가 'S' 로 깨져 지문에 섞여 든 것을 걷어내려고 만들었다)"""
    secs = [x for x in json.loads(SECTIONS.read_text())
            if x["subject"] == subject and x["week"] == week
            and (not classes or x["class_num"] in classes)]
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    n = 0
    for sec in sorted(secs, key=lambda x: x["class_num"]):
        pdf = str(ROOT / sec["pdf"]) if not sec["pdf"].startswith("/") else sec["pdf"]
        r = extract_section(pdf, sec["start_page"], sec["end_page"])
        cls = sec["class_num"]
        fresh = {}
        order = 0
        for b in r["blanks"]:
            order += 1
            turns = [dict(t, py=pinyin_of(t["text"]), **_split_blank(t["text"]))
                     for t in b["turns"]]
            joined = " ".join(t["text"] for t in turns)
            fresh[("blank", str(order))] = {
                "page": b["page"], "passage": joined, "passage_py": pinyin_of(joined),
                "stem": "", "stem_py": "",
                "turns": json.dumps(turns, ensure_ascii=False)}
        for q in r["questions"]:
            order += 1
            opts = [v for _, v in q["options"]]
            fresh[(q["qtype"], q["qno"])] = {
                "page": q["page"], "passage": q["passage"],
                "passage_py": pinyin_of(q["passage"]),
                "stem": q["stem"], "stem_py": pinyin_of(q["stem"]), "turns": "",
                "options": json.dumps(opts, ensure_ascii=False),
                "options_py": json.dumps([pinyin_of(v) for v in opts], ensure_ascii=False),
                "grp": q.get("group", "")}
        for row in c.execute("""SELECT id, qtype, qno FROM hsk_questions
                                WHERE subject=? AND week=? AND class_num=?""",
                             (subject, week, cls)).fetchall():
            f = fresh.get((row["qtype"], row["qno"]))
            if not f:
                print(f"   [알림] {cls}교시 {row['qtype']} {row['qno']}번은 교안에서 못 찾음")
                continue
            cols = ", ".join(f"{k}=?" for k in f)
            c.execute(f"UPDATE hsk_questions SET {cols}, updated_at=datetime('now') WHERE id=?",
                      list(f.values()) + [row["id"]])
            n += 1
        print(f"▶ {week}주 {cls}교시 — 글 {len(fresh)}건 중 {n}건 갱신")
    c.commit()
    c.close()
    return n


def build(subject, week, classes, use_llm=True):
    secs = [s for s in json.loads(SECTIONS.read_text())
            if s["subject"] == subject and s["week"] == week
            and (not classes or s["class_num"] in classes)]
    if not secs:
        print(f"[!] {subject} {week}주차 교안 구간을 찾지 못했습니다")
        return []
    allrows = []
    for sec in sorted(secs, key=lambda s: s["class_num"]):
        pdf = str(ROOT / sec["pdf"]) if not sec["pdf"].startswith("/") else sec["pdf"]
        r = extract_section(pdf, sec["start_page"], sec["end_page"])
        cls = sec["class_num"]
        print(f"\n▶ {week}주 {cls}교시 «{sec.get('title','')}» "
              f"— 문제 {len(r['questions'])}, 빈칸 {len(r['blanks'])}")

        bank = []
        if r["blanks"] and use_llm:
            bank = ocr_word_bank(pdf, r["blanks"][0]["page"])
            print("   보기 낱말:", " ".join(f"{k}{v}" for k, v in bank) or "(못 읽음)")

        rows, order, used = [], 0, set()
        picks = []
        if r["blanks"] and bank and use_llm:
            picks = solve_blank_set([b["turns"] for b in r["blanks"]], bank)
            # 같은 낱말을 두 번 고르면 짝짓기가 깨진 것 — 한 문제씩 다시 묻는다
            got = [a for a in picks if a]
            if len(set(got)) != len(got):
                picks = []
        for bi, b in enumerate(r["blanks"]):
            order += 1
            turns = [dict(t, py=pinyin_of(t["text"]), **_split_blank(t["text"]))
                     for t in b["turns"]]
            joined = " ".join(t["text"] for t in turns)
            ans, why = ("", "")
            if bi < len(picks) and picks[bi]:
                ans = picks[bi]
                why = blank_why(turns, bank, ans) if use_llm else ""
            elif bank and use_llm:
                ans, why = solve_blank(turns, bank, used)
            if ans:
                used.add(ans)
            pk, sk, ok = ("", "", [])
            if use_llm:
                pk, sk, ok = translate(joined, "괄호에 알맞은 낱말 고르기", bank)
            rows.append(dict(
                subject=subject, week=week, class_num=cls, page=b["page"],
                qno=str(order), grp="", qtype="blank", level=4,
                passage=joined, passage_ko=pk, passage_py=pinyin_of(joined),
                stem="", stem_ko="괄호에 들어갈 알맞은 낱말을 고르세요", stem_py="",
                turns=json.dumps(turns, ensure_ascii=False),
                options=json.dumps([v for _, v in bank], ensure_ascii=False),
                options_ko=json.dumps(ok, ensure_ascii=False),
                options_py=json.dumps([pinyin_of(v) for _, v in bank], ensure_ascii=False),
                answer=ans, answer_src="ai" if ans else "",
                explain_ko=why,
                grammar=json.dumps(grammar_points(joined) if use_llm else [], ensure_ascii=False),
                sort_order=order))
            print(f"   빈칸 {order}: 답 {ans or '-'} | {joined[:34]}…")

        for q in r["questions"]:
            order += 1
            opts = [(k, v) for k, v in q["options"]]
            ans, src, why = q.get("answer", ""), q.get("answer_src", ""), ""
            if q["qtype"] == "choice" and not ans and use_llm:
                ans, why = solve_choice(q["passage"], q["stem"], opts)
                src = "ai" if ans else ""
            pk, sk, ok = ("", "", [])
            if use_llm:
                pk, sk, ok = translate(q["passage"], q["stem"], opts)
            rows.append(dict(
                subject=subject, week=week, class_num=cls, page=q["page"],
                qno=q["qno"], grp=q.get("group", ""), qtype=q["qtype"], level=q.get("level", 4),
                passage=q["passage"], passage_ko=pk, passage_py=pinyin_of(q["passage"]),
                stem=q["stem"], stem_ko=sk, stem_py=pinyin_of(q["stem"]), turns="",
                options=json.dumps([v for _, v in opts], ensure_ascii=False),
                options_ko=json.dumps(ok, ensure_ascii=False),
                options_py=json.dumps([pinyin_of(v) for _, v in opts], ensure_ascii=False),
                answer=ans, answer_src=src, explain_ko=why,
                grammar=json.dumps(
                    grammar_points(q["passage"] or " ".join(v for _, v in opts))
                    if use_llm else [], ensure_ascii=False),
                sort_order=order))
            print(f"   {q['qtype']:<6} {q['qno']:>5}: 답 {ans or '-'}({src or '미표기'}) "
                  f"| {(q['stem'] or q['passage'])[:34]}…")
        save(rows)
        n = prune_cards(subject, week, cls, rows)
        if n:
            print(f"   낡은 문제풀이 카드 {n}장을 접었습니다 (hidden=1)")
        allrows += rows
    print(f"\n✅ hsk_questions 에 {len(allrows)}문항 담았습니다")
    return allrows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="신HSK쓰기독해")
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--classes", default="", help="예: 1,2,3 (비우면 전부)")
    ap.add_argument("--no-llm", action="store_true", help="정답·해석 없이 뽑기만")
    ap.add_argument("--text-only", action="store_true",
                    help="이미 담긴 문제의 글만 다시 채운다 (정답·해석은 그대로)")
    a = ap.parse_args()
    cls = [int(x) for x in a.classes.split(",") if x.strip()] if a.classes else []
    if a.text_only:
        refresh_text(a.subject, a.week, cls)
    else:
        build(a.subject, a.week, cls, use_llm=not a.no_llm)
