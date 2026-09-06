# -*- coding: utf-8 -*-
"""독해 문제의 해석을 채운다.
   지문 해석은 카드에 있고, 선택지 해석은 A~D 가 한 덩어리로 붙어 있다 —
   「A. … B. … 다. … D. …」 처럼 C 가 '다'로 옮겨진 것도 있어 표시를 넉넉히 잡는다.
   갈라지지 않으면 그 선택지만 따로 옮긴다."""
import json, re, sqlite3, time, os
os.chdir('/app')
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
cols=[r[1] for r in con.execute('PRAGMA table_info(reading_items)')]
if 'options_ko' not in cols:
    con.execute('ALTER TABLE reading_items ADD COLUMN options_ko TEXT'); con.commit()
    print('options_ko 칸 추가')

def norm(t): return re.sub(r'\s','', t or '')
cards=[(norm(r['chinese']), r['chinese'] or '', r['meaning_ko'] or '') for r in
       con.execute("SELECT chinese,meaning_ko FROM chinese_cards WHERE subject='신HSK쓰기독해'")]

MARK=re.compile(r'(?:^|\s)([ABCD가나다라])\s*[.．)]?\s*')
def split4(ko):
    """A~D 표시를 찾아 넷으로 가른다."""
    pos=[]
    for m in MARK.finditer(ko):
        c=m.group(1)
        k='ABCD'['ABCD가나다라'.index(c) % 4] if c in 'ABCD가나다라' else None
        if k: pos.append((m.start(), m.end(), k))
    want=list('ABCD'); seq=[]; i=0
    for st,en,k in pos:
        if i<4 and k==want[i]: seq.append((st,en)); i+=1
    if i!=4: return None
    out=[]
    for j,(st,en) in enumerate(seq):
        end = seq[j+1][0] if j+1<len(seq) else len(ko)
        out.append(ko[en:end].strip(' .．'))
    return out if all(out) else None

need=[]
for r in con.execute('SELECT * FROM reading_items ORDER BY week,page'):
    ops=json.loads(r['options'])
    # 지문 해석
    pk=''
    for c,_,k in cards:
        if c and k and norm(r['passage']).startswith(c[:20]): pk=k; break
    # 선택지 해석 — 넷이 붙은 카드에서 가른다
    ok=None
    joined=norm(''.join(ops))
    for c,_,k in cards:
        if not k: continue
        cc=re.sub(r'^[ABCD]','',c)
        if norm(c).replace('A','').replace('B','').replace('C','').replace('D','')==joined \
           or joined in norm(c):
            ok=split4(k); break
    if ok is None: need.append((r['id'], ops))
    con.execute("UPDATE reading_items SET meaning_ko=?, options_ko=? WHERE id=?",
                (pk, json.dumps(ok, ensure_ascii=False) if ok else None, r['id']))
con.commit()
print(f"지문 해석 채움 · 선택지 해석 못 가른 것 {len(need)}개")

if need:
    import translators as ts
    for rid, ops in need:
        got=[]
        for o in ops:
            try:
                got.append(ts.translate_text(o, translator='google', from_language='zh', to_language='ko'))
            except Exception:
                try:
                    time.sleep(1.2)
                    got.append(ts.translate_text(o, translator='bing', from_language='zh', to_language='ko'))
                except Exception:
                    got.append('')
        con.execute("UPDATE reading_items SET options_ko=? WHERE id=?",
                    (json.dumps(got, ensure_ascii=False), rid))
        print(f"   [{rid}] 옮김: {got[0][:24]}…", flush=True)
    con.commit()
for r in con.execute('SELECT week,page,meaning_ko,options_ko FROM reading_items ORDER BY week,page'):
    ok=json.loads(r['options_ko']) if r['options_ko'] else []
    print(f"  {r['week']}주 {r['page']}쪽 · 지문 {'O' if r['meaning_ko'] else 'X'} · 선택지 {sum(1 for x in ok if x)}/4")
