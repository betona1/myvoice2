# -*- coding: utf-8 -*-
"""형용사 관형형을 제대로 만든다.
   '귀엽다→귀엽은' '슬프다→슬프은' 처럼 규칙만 대면 틀린다.
   ㅂ불규칙(귀엽다→귀여운)·으탈락(슬프다→슬픈)·ㄹ탈락 같은 게 있어 손으로 적어 둔다."""
import os, re, sqlite3
os.chdir('/app')
ADN = {  # 형용사(사전형) → 관형형
 '귀엽다':'귀여운','슬프다':'슬픈','예쁘다':'예쁜','즐겁다':'즐거운','아름답다':'아름다운',
 '어렵다':'어려운','쉽다':'쉬운','두껍다':'두꺼운','부드럽다':'부드러운','더럽다':'더러운',
 '가볍다':'가벼운','무겁다':'무거운','춥다':'추운','덥다':'더운','맵다':'매운','짜다':'짠',
 '달다':'단','길다':'긴','짧다':'짧은','좁다':'좁은','넓다':'넓은','밝다':'밝은','어둡다':'어두운',
 '작다':'작은','크다':'큰','많다':'많은','적다':'적은','같다':'같은','다르다':'다른',
 '느리다':'느린','빠르다':'빠른','멀다':'먼','가깝다':'가까운','바쁘다':'바쁜','아프다':'아픈',
 '고프다':'고픈','나쁘다':'나쁜','좋다':'좋은','싫다':'싫은','낡다':'낡은','새롭다':'새로운',
 '흐리다':'흐린','맑다':'맑은','푸르다':'푸른','파랗다':'파란','희다':'흰','검다':'검은',
 '마르다':'마른','뚱뚱하다':'뚱뚱한','오래다':'오랜','재미있다':'재미있는','흥미롭다':'흥미로운',
 '심심하다':'심심한','괴롭다':'괴로운','외롭다':'외로운','수줍다':'수줍은','게으르다':'게으른',
 '미련하다':'미련한','부유하다':'부유한','정직하다':'정직한','활발하다':'활발한',
 '순조롭다':'순조로운','홀가분하다':'홀가분한','가지런하다':'가지런한','덤벙대다':'덤벙대는',
 '참을성 있다':'참을성 있는','때맞다':'때맞은','대단하다':'대단한','북적이다':'북적이는',
 '놀랍다':'놀라운','우쭐하다':'우쭐한','뛰어나다':'뛰어난','알맞다':'알맞은','옳다':'옳은',
}
def adn(ko):
    a=(ko or '').split(',')[0].split(';')[0].strip()
    if a in ADN: return ADN[a]
    if a.endswith('하다'): return a[:-2]+'한'
    if a.endswith('있다'): return a[:-2]+'있는'
    if a.endswith('이다'): return a[:-2]+'인'
    if a.endswith('되다'): return a[:-2]+'된'
    if a.endswith('다'):   return a[:-1]+'ㄴ'      # 남으면 아래에서 걸러 다시 본다
    return a
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
rows=con.execute("""SELECT e.id,e.chinese,e.meaning_ko,w.chinese ch,w.meaning_ko wko
                    FROM word_examples e JOIN words w ON w.id=e.word_id
                    WHERE w.wordset='형용사' AND e.chinese LIKE '%的%'""").fetchall()
n=0; odd=[]
for r in rows:
    noun=(r['meaning_ko'] or '').split(' ',1)
    if len(noun)<2: continue
    a=adn(r['wko'])
    if 'ㄴ' in a or 'ㅁ' in a: odd.append((r['chinese'], r['wko'])); continue
    new=f"{a} {noun[1]}"
    if new!=r['meaning_ko']:
        con.execute("UPDATE word_examples SET meaning_ko=? WHERE id=?", (new, r['id'])); n+=1
con.commit()
print(f"관형형을 고친 예문 {n}개")
if odd: print("손이 더 가야 하는 것:", ' '.join(f"{c}({k})" for c,k in odd[:12]))
for r in con.execute("""SELECT e.chinese,e.meaning_ko FROM word_examples e JOIN words w ON w.id=e.word_id
                        WHERE w.wordset='형용사' AND e.chinese LIKE '%的%' LIMIT 10"""):
    print(f"  {r['chinese']:12s} — {r['meaning_ko']}")
