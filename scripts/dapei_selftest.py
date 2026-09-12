# -*- coding: utf-8 -*-
"""main.py 의 호응(搭配) SQL 조각을 그대로 뽑아 실제 DB에 돌려 본다 (서버 기동 없이 확인)."""
import ast, random, sqlite3, sys

SRC = open('main.py', encoding='utf-8').read()
tree = ast.parse(SRC)
want_fn = {'_dp_where', '_dp_choices', '_dp_pair_choices', '_dp_blank'}
want_const = {'_DP_SELECT', 'DAPEI_PATTERNS', 'DAPEI_MODES'}
chunks = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in want_fn:
        chunks.append(ast.get_source_segment(SRC, node))
    if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') in want_const:
        chunks.append(ast.get_source_segment(SRC, node))
ns = {'random': random}
exec('\n'.join(chunks), ns)
missing = (want_fn | want_const) - set(ns)
assert not missing, f'main.py 에서 못 찾음: {missing}'

c = sqlite3.connect('database/voices.db')
c.row_factory = sqlite3.Row
UID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
_dp_where, _DP_SELECT = ns['_dp_where'], ns['_DP_SELECT']
PATS = ns['DAPEI_PATTERNS']
fails = 0


def bad(msg):
    global fails
    fails += 1
    print('  ✗', msg)


# 1) 필터 조합이 전부 SQL 로 성립하는지
for pat in [''] + list(PATS):
    for only in ('', 'new', 'wrong', 'star', 'due', 'learning'):
        for ex in (0, 1):
            w, a = _dp_where(pat, '', 0, only, ex)
            n = c.execute(f"""SELECT COUNT(*) FROM dapei d
                LEFT JOIN dapei_progress p ON p.dapei_id=d.id AND p.user_id=? WHERE {w}""",
                          [UID] + a).fetchone()[0]
            c.execute(f"{_DP_SELECT} WHERE {w} LIMIT 3", [UID] + a).fetchall()
            if pat == '' and only == '' and ex == 0 and n == 0:
                bad('전체 범위인데 호응 표현이 0개')
print(f'1) 필터 {len(PATS)+1}유형 × 6범위 × 예문유무 — SQL 통과')

# 2) 주제·HSK 필터
for t in [r[0] for r in c.execute("SELECT DISTINCT topic FROM dapei WHERE topic!=''")]:
    w, a = _dp_where('', t, 0, '', 0)
    c.execute(f"{_DP_SELECT} WHERE {w} LIMIT 2", [UID] + a).fetchall()
for h in (0, 1, 3, 6):
    w, a = _dp_where('', '', h, '', 0)
    c.execute(f"{_DP_SELECT} WHERE {w} LIMIT 2", [UID] + a).fetchall()
print('2) 주제·HSK 필터 — SQL 통과')

# 3) 뜻 고르기 보기
rows = c.execute(f"{_DP_SELECT} ORDER BY RANDOM() LIMIT 600", (UID,)).fetchall()
for r in rows:
    o = ns['_dp_choices'](c, r, 'meaning')
    if len(o) != 4:
        bad(f"{r['chinese']} 뜻 보기가 4개가 아님({len(o)})")
    if sum(1 for x in o if x.get('ok')) != 1:
        bad(f"{r['chinese']} 정답이 1개가 아님")
    if len({x['meaning_ko'] for x in o}) != 4:
        bad(f"{r['chinese']} 보기 뜻이 겹침")
print(f'3) 뜻 고르기 — 표현 {len(rows)}개 검사')

# 4) 짝 고르기 — 오답이 실제로는 붙지 않는 말인지까지 확인 (호응 학습의 핵심)
real = {r[0] for r in c.execute("SELECT chinese FROM dapei")}
for mode in ('coll', 'head'):
    n_ok = 0
    for r in rows:
        o = ns['_dp_pair_choices'](c, r, mode)
        if len(o) != 4:
            bad(f"{r['chinese']} {mode} 보기가 4개가 아님({len(o)})")
            continue
        if sum(1 for x in o if x.get('ok')) != 1:
            bad(f"{r['chinese']} {mode} 정답이 1개가 아님")
        for x in o:
            if not x.get('ok') and x['chinese'] in real:
                bad(f"{r['chinese']} {mode} 오답 {x['chinese']} 이(가) 실제 호응임")
            if x['py'] == '':
                bad(f"{r['chinese']} {mode} 보기 {x['text']} 병음 없음")
        n_ok += 1
    print(f'4) 짝 고르기({mode}) — {n_ok}개 검사')

# 5) 문장 빈칸
exrows = c.execute(f"{_DP_SELECT} WHERE COALESCE(d.example,'')!='' ORDER BY d.seq LIMIT 600",
                   (UID,)).fetchall()
made = 0
for r in exrows:
    b = ns['_dp_blank'](c, r)
    if b is None:
        bad(f"{r['chinese']} 예문 빈칸을 못 만듦: {r['example']}")
        continue
    made += 1
    if '＿' not in b['masked']:
        bad(f"{r['chinese']} 빈칸이 안 뚫림")
    if b['blank_answer'] in b['masked']:
        bad(f"{r['chinese']} 정답이 문장에 그대로 남아 있음")
    if len(b['blank_choices']) != 4 or sum(x['ok'] for x in b['blank_choices']) != 1:
        bad(f"{r['chinese']} 빈칸 보기 이상")
    for x in b['blank_choices']:
        if not x['ok'] and x['text'] in r['example']:
            bad(f"{r['chinese']} 오답 {x['text']} 이(가) 예문에 이미 있음")
print(f'5) 문장 빈칸 — 예문 {len(exrows)}개 중 {made}개 출제 가능')

# 6) 통계·묶음·상세 SQL
c.execute("SELECT COUNT(*) FROM dapei").fetchone()
c.execute("""SELECT COUNT(*), COALESCE(SUM(result),0) FROM dapei_log
             WHERE user_id=? AND date(created_at)=date('now')""", (UID,)).fetchone()
heads = c.execute("""SELECT d.head, COUNT(*) n,
        SUM(CASE WHEN COALESCE(p.box,0)>=5 THEN 1 ELSE 0 END) mastered
    FROM dapei d LEFT JOIN dapei_progress p ON p.dapei_id=d.id AND p.user_id=?
    GROUP BY d.head ORDER BY MIN(d.seq)""", (UID,)).fetchall()
one = c.execute(f"{_DP_SELECT} WHERE d.head=? ORDER BY d.seq", (UID, heads[0]['head'])).fetchall()
c.execute("SELECT id FROM dapei WHERE head=? AND id!=? LIMIT 5", (one[0]['head'], one[0]['id'])).fetchall()
print(f'6) 통계·묶음 {len(heads)}개 · 첫 묶음 {heads[0]["head"]}({len(one)}개) — SQL 통과')

# 7) 검색
for q in ('提高', 'tí', '수준', '높이'):
    w, a = _dp_where('', '', 0, '', 0)
    w += (" AND (d.chinese LIKE ? OR d.head LIKE ? OR d.collocate LIKE ?"
          " OR d.pinyin LIKE ? OR REPLACE(d.pinyin,' ','') LIKE ?"
          " OR d.meaning_ko LIKE ? OR d.head_ko LIKE ?)")
    a += [f"%{q}%"] * 4 + [f"%{q.replace(' ', '')}%"] + [f"%{q}%"] * 2
    n = c.execute(f"SELECT COUNT(*) FROM dapei d LEFT JOIN dapei_progress p "
                  f"ON p.dapei_id=d.id AND p.user_id=? WHERE {w}", [UID] + a).fetchone()[0]
    if n == 0:
        bad(f'검색 "{q}" 결과 0건')
    print(f'7) 검색 "{q}" → {n}건')

# 8) 데이터 위생
for r in c.execute("SELECT chinese, head, collocate, pinyin, meaning_ko FROM dapei"):
    if r['head'] + r['collocate'] != r['chinese']:
        bad(f"{r['chinese']} 앞말+짝이 표현과 다름")
    if not r['pinyin'] or not r['meaning_ko']:
        bad(f"{r['chinese']} 병음/뜻 비어 있음")
print('8) 데이터 위생 검사 완료')

print('\n실패 0건 — 전부 통과' if not fails else f'\n실패 {fails}건')
sys.exit(1 if fails else 0)
