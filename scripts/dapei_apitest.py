# -*- coding: utf-8 -*-
"""main.py 의 호응(搭配) 엔드포인트 함수를 그대로 꺼내 DB 사본에 대고 호출해 본다.

서버를 띄우지 않고 응답 모양까지 확인하는 것이 목적이라, 쓰기(정답 기록·별표)도
원본이 아니라 사본에만 일어난다.
"""
import ast, asyncio, os, random, shutil, sqlite3, sys, tempfile

SRC = open('main.py', encoding='utf-8').read()
tree = ast.parse(SRC)
FN = {'_dp_where', '_dp_choices', '_dp_pair_choices', '_dp_blank',
      'dapei_stats', 'dapei_list', 'dapei_heads', 'dapei_group', 'dapei_session',
      'dapei_detail', 'dapei_answer', 'dapei_star'}
CONST = {'_DP_SELECT', 'DAPEI_MODES', 'DAPEI_PATTERNS'}
chunks = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FN:
        node.decorator_list = []          # @app.get(...) 은 떼고 순수 함수로 쓴다
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') in CONST:
        chunks.append(ast.unparse(node))

TMP = tempfile.mkdtemp(prefix='dapei_test_')
DBC = os.path.join(TMP, 'voices.db')
shutil.copy('database/voices.db', DBC)


class HTTPException(Exception):
    def __init__(self, code, detail=''):
        super().__init__(f'{code} {detail}')
        self.status_code, self.detail = code, detail


def _wdb():
    c = sqlite3.connect(DBC, timeout=60)
    c.row_factory = sqlite3.Row
    return c


class Req:
    class _S:
        pass

    def __init__(self, uid=1, body=None):
        self.state = Req._S()
        self.state.user_id = uid
        self._body = body or {}

    async def json(self):
        return self._body


ns = {'random': random, 'asyncio': asyncio, 'HTTPException': HTTPException,
      'Request': Req, '_wdb': _wdb, '_SRS_INTERVALS': [0, 1, 2, 4, 8, 16, 32, 64]}
exec('\n'.join(chunks), ns)
miss = (FN | CONST) - set(ns)
assert not miss, f'main.py 에서 못 찾음: {miss}'
_loop = asyncio.new_event_loop()
run = _loop.run_until_complete
UID = 1
fails = 0


def bad(m):
    global fails
    fails += 1
    print('  ✗', m)


# stats
d = run(ns['dapei_stats'](Req(UID)))
print(f"stats  총 {d['total']} · 묶음 {d['heads']} · 예문 {d['examples']} · "
      f"유형 {len(d['patterns'])} · 주제 {len(d['topics'])}")
if d['total'] != 507:
    bad(f"총 개수가 507이 아님: {d['total']}")
if d['seen'] + d['untouched'] != d['total']:
    bad('학습/미학습 합이 총 개수와 다름')

# list
d = run(ns['dapei_list'](Req(UID), q='提高'))
print(f"list   '提高' → {d['total']}건, 첫 항목 {d['items'][0]['chinese']} · {d['items'][0]['meaning_ko']}")
d2 = run(ns['dapei_list'](Req(UID), q='', pattern='AV', limit=5))
print(f"list   유형 AV → {d2['total']}건")
if not d2['total']:
    bad('AV 유형 결과가 0건')

# heads / group
d = run(ns['dapei_heads'](Req(UID)))
print(f"heads  {d['total']}묶음, 첫 묶음 {d['heads'][0]['head']}({d['heads'][0]['n']}개)")
g = run(ns['dapei_group'](Req(UID), '提高'))
print(f"group  提高 {g['head_py']} · {g['pattern_ko']} · {g['count']}개 → "
      + ', '.join(i['collocate'] for i in g['items'][:6]))
try:
    run(ns['dapei_group'](Req(UID), '없는말'))
    bad('없는 묶음인데 404가 안 남')
except HTTPException as e:
    if e.status_code != 404:
        bad(f'없는 묶음 예외 코드가 404가 아님: {e.status_code}')

# session — 모든 모드
for m in ns['DAPEI_MODES']:
    s = run(ns['dapei_session'](Req(UID), mode=m, limit=8))
    it = s['items']
    print(f"sess   {m:8s} {len(it)}문제", end='')
    if m == 'listen':
        print('  (음성 없어 0문제 — 정상)' if not it else '')
        continue
    print()
    if not it:
        bad(f'{m} 모드에서 문제가 안 나옴')
        continue
    for x in it:
        if m in ('meaning',) and len(x['choices']) != 4:
            bad(f"{m}/{x['chinese']} 보기 수 이상")
        if m in ('coll', 'head'):
            if sum(1 for o in x['choices'] if o.get('ok')) != 1:
                bad(f"{m}/{x['chinese']} 정답 수 이상")
        if m == 'blank' and '＿' not in x['masked']:
            bad(f"blank/{x['chinese']} 빈칸 없음")
        if m == 'card' and not x['meaning_ko']:
            bad(f"card/{x['chinese']} 뜻 없음")
one = run(ns['dapei_session'](Req(UID), mode='coll', limit=5, head='提高'))
print(f"sess   head=提高 → {one['count']}문제 "
      + ('(전부 提高)' if all(i['head'] == '提高' for i in one['items']) else '✗ 다른 앞말 섞임'))

# detail
did = one['items'][0]['id']
d = run(ns['dapei_detail'](Req(UID), did))
print(f"detail {d['chinese']} · 같은앞말 {len(d['same_head'])}개 · 같은짝 {len(d['same_coll'])}개")

# answer / star  (사본에만 쓴다)
r = run(ns['dapei_answer'](Req(UID, {'dapei_id': did, 'result': 1, 'mode': 'dp_coll', 'ms': 1200})))
print(f"answer 정답 → box {r['box']} · 다음 복습 {r['next_days']}일 뒤 · 연속 {r['streak']}")
r = run(ns['dapei_answer'](Req(UID, {'dapei_id': did, 'result': 0, 'mode': 'dp_coll'})))
print(f"answer 오답 → box {r['box']} · 연속 {r['streak']}")
if r['box'] != 0 or r['streak'] != 0:
    bad('오답인데 박스/연속이 안 내려감')
run(ns['dapei_star'](Req(UID, {'dapei_id': did, 'on': 1})))
st = run(ns['dapei_stats'](Req(UID)))
print(f"stats  기록 후 → 학습 {st['seen']} · 오늘 {st['today_count']}문제 · 별표 {st['starred']}")
if st['today_count'] != 2 or st['starred'] != 1:
    bad('기록이 통계에 반영되지 않음')
try:
    run(ns['dapei_answer'](Req(UID, {'result': 1})))
    bad('dapei_id 없는데 400이 안 남')
except HTTPException as e:
    if e.status_code != 400:
        bad('dapei_id 누락 예외 코드가 400이 아님')

_loop.close()
shutil.rmtree(TMP, ignore_errors=True)
print('\n실패 0건 — 전부 통과' if not fails else f'\n실패 {fails}건')
sys.exit(1 if fails else 0)
