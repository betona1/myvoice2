# -*- coding: utf-8 -*-
"""형용사 예문의 「~ 것」 뜻을 제 이름으로 고친다.
   干燥的天气 를 「건조한 것」으로 뭉뚱그려 놓았다 — 「건조한 날씨」라야 한다.
   틀만 찍어내고 명사를 옮기지 않은 탓이다."""
import os, re, sqlite3
os.chdir('/app')
N = {
 '孩子':'아이','事':'일','学生':'학생','态度':'태도','水果':'과일','色的树':'나무',
 '人':'사람','的猫':'고양이','衣服':'옷','色的天':'하늘','的时间':'시간','头发':'머리카락',
 '房间':'방','身体':'몸','样子':'모습','题':'문제','变化':'변화','地方':'곳','答案':'답',
 '天气':'날씨','表情':'표정','故事':'이야기','自由':'자유','心情':'마음','感觉':'느낌',
 '父母':'부모','检查':'검사','时间':'시간','老师':'선생님','笑':'웃음','旅行':'여행',
 '桌子':'책상','电影':'영화','生活':'생활','汉语':'중국어','错误':'실수','工作':'일',
 '考试':'시험','饭店':'호텔','路':'길','菜':'반찬','办法':'방법','书':'책','的床':'침대',
 '女孩':'소녀','帮助':'도움','回答':'대답','问题':'문제','经验':'경험','街道':'거리',
 '气氛':'분위기','班车':'셔틀버스','邻居':'이웃','场合':'자리','风景':'경치','表演':'공연',
 '想法':'생각','现象':'현상','说明':'설명','椅子':'의자','的自由':'자유','的树':'나무',
 '的天':'하늘','的人':'사람','的事':'일','的问题':'문제',
}
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
rows=con.execute("""SELECT e.id,e.chinese,e.meaning_ko,w.chinese ch,w.meaning_ko wko
                    FROM word_examples e JOIN words w ON w.id=e.word_id
                    WHERE w.wordset='형용사' AND e.meaning_ko LIKE '%것'""").fetchall()
n=0; left=[]
for r in rows:
    zh=r['chinese']
    m=re.match(r'^(.+?)的(.+)$', zh) or re.match(r'^(.+?)(色的.+|.+)$', zh)
    if not m: left.append(zh); continue
    noun=zh[len(r['ch']):]                      # 형용사 뒤에 붙은 부분
    ko=None
    for k in (noun, noun.lstrip('的'), noun.replace('色的','')):
        if k in N: ko=N[k]; break
    if not ko: left.append(zh); continue
    adj=(r['wko'] or '').split(',')[0].strip()
    stem=adj[:-1]+'ㄴ' if adj.endswith('다') else adj    # '건조하다' → '건조한'
    stem=re.sub(r'하ㄴ$','한', stem); stem=re.sub(r'([가-힣])ㄴ$', r'\1', stem)
    if adj.endswith('하다'): stem=adj[:-2]+'한'
    elif adj.endswith('다'): stem=adj[:-1]+'은'
    new=f"{stem} {ko}"
    con.execute("UPDATE word_examples SET meaning_ko=? WHERE id=?", (new, r['id'])); n+=1
con.commit()
print(f"뜻을 고친 예문 {n}개")
if left: print("못 고친 것:", ' '.join(left[:20]))
for r in con.execute("""SELECT e.chinese,e.meaning_ko FROM word_examples e JOIN words w ON w.id=e.word_id
                        WHERE w.wordset='형용사' AND e.chinese LIKE '%的%' LIMIT 8"""):
    print(f"  {r['chinese']:12s} — {r['meaning_ko']}")
