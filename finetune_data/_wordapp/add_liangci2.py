# -*- coding: utf-8 -*-
"""CC-CEDICT 의 CL: 표기(명사마다 어떤 양사를 쓰는지)를 세어 보니
   상용 양사 23개가 빠져 있었다. 이를 더해 양사 묶음을 채운다.
   양사는 '무엇을 세는가'가 핵심이라 뜻에 그것을 적고 예를 붙인다."""
import os, sqlite3
os.chdir('/app')
from pypinyin import pinyin as _py, Style
con = sqlite3.connect('database/voices.db', timeout=60); con.row_factory = sqlite3.Row

L = [
 ('根','가닥·개 (가늘고 긴 것)','① 막대·끈·머리카락 — 一根头发 머리카락 한 올 / 一根香蕉 바나나 한 개'),
 ('部','편·대 (작품·기계)','① 영화·책·차 — 一部电影 영화 한 편 / 一部手机 휴대폰 한 대'),
 ('颗','알 (둥글고 작은 것)','① 一颗星星 별 하나 / 一颗心 마음 하나 / 一颗子弹 총알 한 발'),
 ('粒','알 (아주 작은 알갱이)','① 一粒米 쌀 한 톨 / 一粒药 알약 한 알  ※颗보다 더 작은 것'),
 ('道','줄기·가지 (선·문제·요리)','① 一道题 문제 한 문항 / 一道菜 요리 한 가지 / 一道光 빛 한 줄기'),
 ('种','가지 (종류)','① 一种方法 한 가지 방법 / 两种颜色 두 가지 색'),
 ('项','항목·가지 (일·규정)','① 一项工作 일 한 가지 / 三项规定 규정 세 항목'),
 ('笔','건 (돈·거래)','① 一笔钱 돈 한 몫 / 一笔生意 거래 한 건'),
 ('门','과목·문 (학문·기술·대포)','① 一门课 수업 한 과목 / 一门外语 외국어 하나'),
 ('枚','매·발 (작고 납작한 것)','① 一枚硬币 동전 한 닢 / 一枚邮票 우표 한 장'),
 ('面','면·폭 (평평한 것)','① 一面镜子 거울 하나 / 一面旗子 깃발 하나'),
 ('股','줄기·가닥 (기운·냄새)','① 一股风 바람 한 줄기 / 一股香味 향기 한 줄기'),
 ('处','곳·군데','① 一处名胜 명승지 한 곳 / 两处伤 상처 두 군데'),
 ('盒','갑·통','① 一盒饼干 과자 한 통 / 一盒药 약 한 갑'),
 ('盘','접시·판','① 一盘菜 요리 한 접시 / 一盘棋 바둑 한 판'),
 ('对','쌍 (짝을 이룬 둘)','① 一对夫妻 부부 한 쌍 / 一对耳环 귀걸이 한 쌍  ※짝이 정해진 것은 双'),
 ('出','편 (연극·희곡)','① 一出戏 연극 한 편 / 一出京剧 경극 한 편'),
 ('顶','개 (꼭대기가 있는 것)','① 一顶帽子 모자 하나 / 一顶帐篷 천막 하나'),
 ('幢','채 (건물)','① 一幢楼房 건물 한 채  ※栋과 같은 뜻'),
 ('栋','채 (건물)','① 一栋房子 집 한 채'),
 ('罐','통·캔','① 一罐可乐 콜라 한 캔 / 一罐茶叶 찻잎 한 통'),
 ('桩','건 (일·사건)','① 一桩事 일 한 건 / 一桩生意 거래 한 건'),
 ('批','무리·차 (한꺼번에)','① 一批货 물건 한 무더기 / 一批学生 학생 한 무리'),
]

seq0 = con.execute("SELECT COALESCE(MAX(seq),0) FROM words").fetchone()[0]
added = marked = 0
for ch, ko, se in L:
    r = con.execute("SELECT id FROM words WHERE chinese=?", (ch,)).fetchone()
    if r:
        con.execute("""UPDATE words SET meaning_ko=?, senses_ko=?, wordset='양사', excluded=0
                       WHERE id=?""", (ko, se, r['id']))
        marked += 1
    else:
        seq0 += 1
        con.execute("""INSERT INTO words(chinese,pinyin,tones,meaning_ko,senses_ko,hsk,
                                         category,subject,seq,level,difficulty,excluded,wordset)
                       VALUES(?,?,?,?,?,0,'단어','기능어',?,1,5,0,'양사')""",
                    (ch, ' '.join(x[0] for x in _py(ch, style=Style.TONE)),
                     ' '.join(x[0] for x in _py(ch, style=Style.TONE3)), ko, se, seq0))
        added += 1
con.commit()
n = con.execute("SELECT COUNT(*) FROM words WHERE wordset='양사' AND COALESCE(excluded,0)=0").fetchone()[0]
print(f"보탠 것 {added}개 · 기존 항목에 표시 {marked}개 → 양사 묶음 {n}개")
na = con.execute("""SELECT COUNT(*) FROM words WHERE wordset='양사'
                    AND (audio1 IS NULL OR audio1='' OR audio2 IS NULL OR audio2='')""").fetchone()[0]
print(f"음성 없는 것 {na}개")
con.close()
