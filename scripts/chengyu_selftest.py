# -*- coding: utf-8 -*-
"""main.py 의 성어 SQL 조각을 그대로 뽑아 실제 DB에 돌려 본다 (서버 기동 없이 확인)."""
import ast, random, sqlite3, sys, textwrap

SRC = open('main.py', encoding='utf-8').read()
tree = ast.parse(SRC)
want_fn = {'_cy_where', '_cy_choices', '_cy_blank'}
want_const = {'_CY_SELECT'}
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
_cy_where, _CY_SELECT = ns['_cy_where'], ns['_CY_SELECT']

fails = 0
for hsk in (0, 7):
    for only in ('', 'new', 'wrong', 'star', 'due', 'learning'):
        w, a = _cy_where(hsk, only)
        n = c.execute(f"""SELECT COUNT(*) FROM words w JOIN chengyu g ON g.word_id=w.id
            LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=? WHERE {w}""",
                      [UID] + a).fetchone()[0]
        print(f'  where hsk={hsk} only={only or "-":8s} → {n}')

w, a = _cy_where(0, '')
rows = c.execute(f"{_CY_SELECT} WHERE {w} ORDER BY g.freq DESC LIMIT 8", [UID] + a).fetchall()
print(f'\n세션 표본 {len(rows)}개')
for r in rows:
    ch = ns['_cy_choices'](c, r, 'meaning')
    bl = ns['_cy_blank'](c, r)
    okn = sum(1 for o in ch if o.get('ok'))
    dup = len({o['meaning_ko'] for o in ch}) != len(ch)
    bad = (len(ch) != 4 or okn != 1 or dup
           or len(set(bl['blank_choices'])) != 4
           or bl['blank_answer'] not in bl['blank_choices']
           or bl['masked'].replace('＿', bl['blank_answer']) != r['chinese'])
    fails += bad
    print(f"  {r['chinese']} {r['pinyin']:<22} 보기{len(ch)}/정답{okn} 빈칸 {bl['masked']}"
          f" [{' '.join(bl['blank_choices'])}] → {bl['blank_answer']}{'  ← 문제!' if bad else ''}")
    if r['story']:
        print(f"      유래: {r['story'][:60]}…")

# 검색 쿼리도 실제로 돌려 본다
for q in ('守株', 'shou', '기다리다', '새옹'):
    w2, a2 = _cy_where(0, '')
    w2 += (" AND (w.chinese LIKE ? OR w.pinyin LIKE ? OR REPLACE(w.py_plain,' ','') LIKE ?"
           " OR w.meaning_ko LIKE ? OR g.ko_idiom LIKE ? OR g.literal_ko LIKE ?)")
    a2 += [f"%{q}%", f"%{q}%", f"%{q.lower().replace(' ', '')}%"] + [f"%{q}%"] * 3
    hits = c.execute(f"{_CY_SELECT} WHERE {w2} LIMIT 3", [UID] + a2).fetchall()
    print(f"검색 {q!r} → {len(hits)}건 " + ', '.join(h['chinese'] for h in hits))

print('\n결과:', '이상 없음' if not fails else f'{fails}건 문제')
sys.exit(1 if fails else 0)
