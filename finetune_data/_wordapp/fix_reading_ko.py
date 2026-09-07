# -*- coding: utf-8 -*-
"""해석에서 숫자·날짜가 어긋난 곳을 손으로 바로잡는다.
   기계 번역이 숫자를 슬쩍 바꾼다 — 370多种→100종, 1963年→2000년, 1月5日→'매달 매일'.
   독해 문제에서는 숫자가 정답을 가르는 열쇠라 그대로 두면 안 된다."""
import json, sqlite3, os
os.chdir('/app')
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row

FIX_PASSAGE = {
 '鲨鱼': ("오랫동안 상어는 영화·텔레비전·책에서 바다의 무서운 살인자로 그려져 왔습니다. "
          "사납고 무시무시하며 바닷속 모든 생물의 목숨을 위협한다고요. 상어가 정말 그렇게 무서울까요? "
          "과학자들이 밝힌 바로는 지구에 상어가 약 370여 종 있는데, 대부분은 사람에게 이롭고 해가 없습니다. "
          "「백상아리」 같은 일부 상어만이 사람을 해칩니다."),
 '哈尔滨': ("하얼빈 빙등(얼음등)은 나라 안팎에 이름났습니다. 이곳에서 얼음등을 크게 조직적으로 만들어 "
            "선보이기 시작한 것은 1963년입니다. 사람들은 대야·통 같은 간단한 틀로 천 개가 넘는 얼음등과 "
            "수십 개의 얼음꽃을 자연히 얼려, 정월대보름에 공원에서 선보여 온 도시를 들썩이게 했습니다. "
            "1985년에는 빙등 유원회를 중심으로 빙설제를 열었고, 그 뒤로 해마다 1월 5일이 "
            "하얼빈 사람들만의 고장 명절이 되었습니다."),
}
FIX_OPTION = {
 '冰雪节的历史有100多年': '빙설제의 역사는 100여 년이다',
 '冰雪节5年举办一次':     '빙설제는 5년에 한 번 열린다',
}
n=0
for r in con.execute('SELECT id,passage,meaning_ko,options,options_ko FROM reading_items').fetchall():
    ko=r['meaning_ko'] or ''
    for key, new in FIX_PASSAGE.items():
        if key in r['passage'] and ko != new:
            con.execute('UPDATE reading_items SET meaning_ko=? WHERE id=?', (new, r['id']))
            print(f"  [{r['id']}] 지문 고침 ({key})"); n+=1
    ops=json.loads(r['options']); ok=json.loads(r['options_ko'] or '[]')
    if len(ok)==len(ops):
        ch=False
        for j,o in enumerate(ops):
            if o in FIX_OPTION and ok[j]!=FIX_OPTION[o]:
                print(f"  [{r['id']}] {'ABCD'[j]} 「{ok[j]}」 → 「{FIX_OPTION[o]}」")
                ok[j]=FIX_OPTION[o]; ch=True; n+=1
        if ch:
            con.execute('UPDATE reading_items SET options_ko=? WHERE id=?',
                        (json.dumps(ok, ensure_ascii=False), r['id']))
con.commit()
print(f"\n고친 곳 {n}군데")
