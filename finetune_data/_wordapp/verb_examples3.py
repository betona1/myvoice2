# -*- coding: utf-8 -*-
"""남은 동사 예문. 앞 판에서 빠진 것들 — 뜻이 여럿이거나 홀로 잘 안 쓰이는 말이 섞였다."""
import os, sqlite3
os.chdir('/app')
from pypinyin import pinyin as _py, Style
EX = {
 '满意': ['很满意|아주 만족한다', '对结果满意|결과에 만족하다', '不太满意|그리 만족스럽지 않다'],
 '过去': ['过去看看|가서 보다', '走过去|걸어가다', '过去的事|지난 일'],
 '举行': ['举行会议|회의를 열다', '举行婚礼|결혼식을 올리다', '明天举行|내일 열린다'],
 '比赛': ['参加比赛|시합에 나가다', '看比赛|경기를 보다', '比赛开始了|경기가 시작됐다'],
 '影响': ['影响学习|공부에 지장을 주다', '受到影响|영향을 받다', '影响很大|영향이 크다'],
 '表示': ['表示感谢|감사를 표하다', '表示同意|찬성을 나타내다', '这表示什么|이건 무슨 뜻이죠?'],
 '以为': ['我以为是你|너인 줄 알았다', '别以为我不知道|내가 모르는 줄 알지 마', '以为他会来|그가 올 줄 알았다'],
 '出现': ['出现问题|문제가 생기다', '突然出现|갑자기 나타나다', '他出现了|그가 나타났다'],
 '变化': ['变化很大|변화가 크다', '天气变化|날씨가 바뀌다', '有了变化|달라졌다'],
 '晴':   ['天晴了|날이 갰다', '今天很晴|오늘은 맑다', '晴天|맑은 날'],
 '刮':   ['刮风|바람이 불다', '刮胡子|면도하다', '刮大风|바람이 세게 불다'],
 '刻':   ['三点一刻|3시 15분', '刻字|글자를 새기다', '一刻钟|15분'],
 '敢':   ['我不敢|엄두가 안 난다', '敢说敢做|말도 하고 행동도 하다', '你敢去吗|갈 용기 있어?'],
 '使':   ['使人高兴|사람을 기쁘게 하다', '使用方法|사용법', '使我明白|나를 깨닫게 하다'],
}
con=sqlite3.connect('database/voices.db', timeout=60); con.row_factory=sqlite3.Row
added=0
for ch, items in EX.items():
    r=con.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(ch,)).fetchone()
    if not r: continue
    for k,it in enumerate(items):
        zh, ko = it.split('|',1)
        py=' '.join(x[0] for x in _py(zh, style=Style.TONE))
        try:
            con.execute("""INSERT INTO word_examples(word_id,seq,chinese,pinyin,meaning_ko)
                           VALUES(?,?,?,?,?)""", (r['id'], k, zh, py, ko)); added+=1
        except sqlite3.IntegrityError: pass
con.commit()
print(f"예문 {added}개 넣음")
n=con.execute("""SELECT COUNT(*) FROM word_examples e JOIN words w ON w.id=e.word_id
                 WHERE w.wordset='동사'""").fetchone()[0]
have=con.execute("""SELECT COUNT(DISTINCT word_id) FROM word_examples e JOIN words w ON w.id=e.word_id
                    WHERE w.wordset='동사'""").fetchone()[0]
tot=con.execute("SELECT COUNT(*) FROM words WHERE wordset='동사' AND COALESCE(excluded,0)=0").fetchone()[0]
print(f"동사 {tot}개 중 예문 있는 것 {have}개 · 예문 {n}개")
left=[r[0] for r in con.execute("""SELECT chinese FROM words w WHERE w.wordset='동사'
        AND COALESCE(w.excluded,0)=0
        AND NOT EXISTS(SELECT 1 FROM word_examples e WHERE e.word_id=w.id)""")]
print("아직 없는 것:", ' '.join(left) if left else '없음')
