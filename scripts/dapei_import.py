# -*- coding: utf-8 -*-
"""搭配(호응·짝꿍표현) 데이터를 dapei 테이블에 반영한다.

  python3 scripts/dapei_import.py [--dry]

- dapei          : 호응 표현 본체(머리말+짝 / 병음 / 뜻 / 예문)
- dapei_progress : 사용자별 라이트너 박스 진도 (word_progress 와 같은 구조)
- dapei_log      : 사용자별 풀이 기록 (study_log 와 같은 구조)

병음은 words 테이블 → CC-CEDICT 순으로 찾아 붙인다. 둘 다 없으면 경고만 남기고
빈 값으로 넣는다(나중에 사람이 채울 수 있게).
"""
import os, re, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chengyu_prep import acc  # noqa: E402  숫자 성조 → 성조 부호

DB = os.environ.get('DAPEI_DB', 'database/voices.db')
TSV = os.environ.get('DAPEI_TSV', 'database/dapei_ko.tsv')
CEDICT = 'database/cedict_full.txt'
DRY = '--dry' in sys.argv

# 사전(words·CC-CEDICT)에 낱말로 올라 있지 않은 것만 손으로 채운다
EXTRA_PY = {'交道': 'jiāo dao', '夜车': 'yè chē', '独立性': 'dú lì xìng', '用水': 'yòng shuǐ'}

PATTERNS = {'VO': '동사+목적어', 'AN': '형용사+명사', 'NA': '명사+술어',
            'AV': '수식어+동사', 'VC': '동사+보어', 'NN': '명사+명사'}


def parse_tsv(path):
    """@머리말 줄로 그룹을 열고, 이어지는 줄들을 그 그룹의 짝으로 읽는다."""
    groups, cur = [], None
    for ln, line in enumerate(open(path, encoding='utf-8'), 1):
        line = line.rstrip('\n')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        f = [x.strip() for x in line.split('\t')]
        if f[0].startswith('@'):
            f += [''] * (5 - len(f))
            head, hmean, pat, topic, hsk = f[0][1:], f[1], f[2], f[3], f[4]
            if not head or not hmean:
                raise SystemExit(f'{path}:{ln} 머리말/뜻 없음: {line!r}')
            if pat not in PATTERNS:
                raise SystemExit(f'{path}:{ln} 유형이 이상함: {pat!r}')
            cur = {'head': head, 'head_ko': hmean, 'pattern': pat, 'topic': topic,
                   'hsk': int(hsk or 0), 'items': []}
            groups.append(cur)
            continue
        if cur is None:
            raise SystemExit(f'{path}:{ln} 그룹(@…) 밖의 줄: {line!r}')
        f += [''] * (4 - len(f))
        coll, mean, ex, exko = f[0], f[1], f[2], f[3]
        if not coll or not mean:
            raise SystemExit(f'{path}:{ln} 짝/뜻 없음: {line!r}')
        if not re.fullmatch(r'[㐀-鿿]+', coll):
            raise SystemExit(f'{path}:{ln} 한자가 아님: {coll!r}')
        cur['items'].append({'coll': coll, 'ko': mean, 'ex': ex, 'ex_ko': exko})
    return groups


def load_pinyin(need):
    """필요한 낱말의 병음만 골라 모은다 — words 우선, 없으면 CC-CEDICT."""
    py = {k: v for k, v in EXTRA_PY.items() if k in need}
    c = sqlite3.connect(DB)
    for w, p in c.execute("SELECT chinese, pinyin FROM words WHERE pinyin!=''"):
        if w in need and w not in py:
            py[w] = p
    c.close()
    rest = need - set(py)
    if rest:
        pat = re.compile(r'^(\S+) (\S+) \[([^]]+)\]')
        for line in open(CEDICT, encoding='utf-8'):
            if line.startswith('#'):
                continue
            m = pat.match(line)
            if m and m.group(2) in rest and m.group(2) not in py:
                py[m.group(2)] = acc(m.group(3))
    return py


def ensure_tables(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS dapei(
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        chinese    TEXT UNIQUE NOT NULL,   -- 提高水平
        head       TEXT NOT NULL,          -- 提高
        head_py    TEXT DEFAULT '',
        head_ko    TEXT DEFAULT '',
        collocate  TEXT NOT NULL,          -- 水平
        coll_py    TEXT DEFAULT '',
        pinyin     TEXT DEFAULT '',
        meaning_ko TEXT DEFAULT '',
        pattern    TEXT DEFAULT 'VO',
        topic      TEXT DEFAULT '',
        hsk        INTEGER DEFAULT 0,
        example    TEXT DEFAULT '',
        example_ko TEXT DEFAULT '',
        audio1     TEXT DEFAULT '',
        audio2     TEXT DEFAULT '',
        seq        INTEGER DEFAULT 0,
        created_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_dapei_head ON dapei(head);
    CREATE INDEX IF NOT EXISTS idx_dapei_coll ON dapei(collocate);

    CREATE TABLE IF NOT EXISTS dapei_progress(
        user_id INTEGER NOT NULL, dapei_id INTEGER NOT NULL,
        box INTEGER DEFAULT 0, due_at TEXT, interval_days REAL DEFAULT 0,
        seen INTEGER DEFAULT 0, correct INTEGER DEFAULT 0, wrong INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0,
        starred INTEGER DEFAULT 0, last_at TEXT,
        PRIMARY KEY(user_id, dapei_id));

    CREATE TABLE IF NOT EXISTS dapei_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, dapei_id INTEGER, mode TEXT, result INTEGER,
        ms INTEGER DEFAULT 0, created_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_dapei_log_u ON dapei_log(user_id, created_at);
    """)


def main():
    groups = parse_tsv(TSV)
    need = set()
    for g in groups:
        need.add(g['head'])
        need |= {i['coll'] for i in g['items']}
    py = load_pinyin(need)
    missing = sorted(need - set(py))

    rows, seq, dup = [], 0, {}
    for g in groups:
        hpy = py.get(g['head'], '')
        for i in g['items']:
            cn = g['head'] + i['coll']
            if cn in dup:
                raise SystemExit(f'중복된 호응 표현: {cn}')
            dup[cn] = 1
            cpy = py.get(i['coll'], '')
            seq += 1
            rows.append((cn, g['head'], hpy, g['head_ko'], i['coll'], cpy,
                         (hpy + ' ' + cpy).strip(), i['ko'], g['pattern'], g['topic'],
                         g['hsk'], i['ex'], i['ex_ko'], seq))

    print(f'그룹 {len(groups)}개 · 호응 표현 {len(rows)}개 · 예문 '
          f'{sum(1 for r in rows if r[11])}개')
    if missing:
        print(f'병음을 못 찾은 낱말 {len(missing)}개: {" ".join(missing)}')
    if DRY:
        for r in rows[:5]:
            print(' ', r[0], '|', r[6], '|', r[7])
        return

    c = sqlite3.connect(DB, timeout=60)
    c.execute('PRAGMA busy_timeout=60000')
    ensure_tables(c)
    ins = upd = 0
    for r in rows:
        old = c.execute('SELECT id FROM dapei WHERE chinese=?', (r[0],)).fetchone()
        if old:
            # 사람이 손본 음성(audio1/2)은 건드리지 않고 내용만 갱신한다
            c.execute("""UPDATE dapei SET head=?,head_py=?,head_ko=?,collocate=?,coll_py=?,
                         pinyin=?,meaning_ko=?,pattern=?,topic=?,hsk=?,example=?,example_ko=?,seq=?
                         WHERE id=?""", r[1:] + (old[0],))
            upd += 1
        else:
            c.execute("""INSERT INTO dapei
                (chinese,head,head_py,head_ko,collocate,coll_py,pinyin,meaning_ko,
                 pattern,topic,hsk,example,example_ko,seq,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""", r)
            ins += 1
    c.commit()
    total = c.execute('SELECT COUNT(*) FROM dapei').fetchone()[0]
    c.close()
    print(f'새로 넣음 {ins}개 · 갱신 {upd}개 · dapei 총 {total}개')


if __name__ == '__main__':
    main()
