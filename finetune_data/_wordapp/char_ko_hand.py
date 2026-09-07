# -*- coding: utf-8 -*-
"""옮긴 뜻이 어그러진 글자를 손으로 적는다.
   知識(knight-errant)를 「기사 오류」로, 「~습니다」 말투로, 사전 표기([gu3 qin2])를
   그대로 끌고 온 것들. 낱글자 뜻은 짧고 곧아야 쓸모가 있다."""
import sqlite3, os
os.chdir('/app')
HAND = {
 '可':'~할 수 있다, 옳다, 허락하다',
 '琴':'거문고·가야금 같은 현악기',
 '误':'그르치다, 잘못, 놓치다',
 '侠':'협객, 의로운 사람',
 '哉':'~로다 (감탄을 나타내는 옛 조사)',
 '啰':'~라고요 (문장 끝 감탄 조사)',
 '喻':'비유하다, 빗대어 말하다',
 '壬':'천간의 아홉째 (임)',
 '徵':'옛 다섯 음계의 넷째 음 (치)',
 '扇':'부채, 문짝·창문을 세는 말',
 '杭':'항저우(杭州)의 준말',
 '枯':'마르다, 시들다, 메마르다',
 '沈':'선양(瀋陽)의 준말 · 성씨 심',
 '浙':'저장(浙江)의 준말',
 '渭':'웨이수이(渭水) — 산시성을 흐르는 강',
 '町':'마을·거리 이름에 쓰는 글자',
 '秦':'진나라 · 성씨 진',
 '稞':'쌀보리 (青稞)',
 '美':'아름답다, 좋다, 훌륭하다',
 '茄':'가지 (채소) · 외래어 표기에 쓰는 글자',
 '茫':'아득하다, 어렴풋하다',
 '赵':'조나라 · 성씨 조',
 '哉':'~로다 (감탄을 나타내는 옛 조사)',
}
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
n=0
for ch,ko in HAND.items():
    r=con.execute("SELECT ko FROM char_ko WHERE char=?", (ch,)).fetchone()
    if r is None:
        con.execute("INSERT INTO char_ko(char,ko) VALUES(?,?)", (ch,ko)); n+=1; continue
    if (r['ko'] or '') != ko:
        con.execute("UPDATE char_ko SET ko=? WHERE char=?", (ko,ch)); n+=1
con.commit()
print(f"손으로 적은 글자 {n}자")
import re
BAD=re.compile(r'오류|습니다|입니다|하다\.|[A-Za-z]{3,}|\[')
left=[r['char'] for r in con.execute('SELECT char,ko FROM char_ko') if BAD.search(r['ko'] or '')]
empty=[r[0] for r in con.execute("SELECT char FROM char_ko WHERE TRIM(COALESCE(ko,''))=''")]
print(f"아직 어그러진 것 {len(left)}자: {''.join(left)}")
print(f"아직 빈 것 {len(empty)}자: {''.join(empty)}")
print(f"모두 {con.execute('SELECT COUNT(*) FROM char_ko').fetchone()[0]}자")
