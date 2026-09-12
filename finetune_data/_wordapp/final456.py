# -*- coding: utf-8 -*-
import os,re,sqlite3
os.chdir('/app')
DB='database/voices.db'; CIR='①②③④⑤⑥⑦⑧'
FIX=[  # (한자, 병음지정(없으면 None), 대표뜻)
 ('长江',None,'창장강, 양쯔강'), ('重视',None,'중시하다, 중요하게 여기다'),
 ('太极拳',None,'태극권'), ('管子',None,'관, 파이프'), ('面临',None,'직면하다, 당면하다'),
 ('啰唆',None,'말이 많다, 장황하다'), ('重阳节',None,'중양절 (음력 9월 9일)'),
 ('爱不释手',None,'너무 좋아서 손에서 놓지 못하다'), ('固有',None,'고유하다, 본래부터 지니다'),
 ('争气',None,'잘하려고 애쓰다, 기를 펴다'), ('隐患',None,'잠재된 위험, 화근'),
 ('归还',None,'돌려주다, 반환하다'), ('拜托',None,'부탁드립니다, 부탁하다'),
 ('依靠',None,'의지하다, 기대다'),
 ('劳驾',None,'실례합니다 (부탁할 때)'), ('各抒己见',None,'저마다 자기 의견을 말하다'),
 ('搂','lōu','껴안다, 끌어안다'),
 ('提','tí','들다, 제기하다'),
 ('官','guān','관리, 벼슬아치'), ('瘸','qué','절뚝거리다'), ('腥','xīng','비리다, 비린내가 나다'),
 ('巷','xiàng','골목'), ('膜','mó','막, 얇은 막'), ('啥','shá','무엇, 뭐'),
]
con=sqlite3.connect(DB,timeout=60); con.execute('PRAGMA busy_timeout=60000')
n=p=0
for ch,py,ko in FIX:
    for wid,opy,oko,sen in con.execute(
            "SELECT id,pinyin,meaning_ko,senses_ko FROM words WHERE chinese=? AND hsk BETWEEN 4 AND 6",(ch,)).fetchall():
        if py and (opy or '').strip()!=py:
            con.execute("UPDATE words SET pinyin=? WHERE id=?",(py,wid)); p+=1
        parts=[x.strip() for x in re.split(r'[①②③④⑤⑥⑦⑧]\s*',sen or '') if x.strip()]
        parts=[x for x in parts if x!=ko and x!=(oko or '').strip()]
        parts=[ko]+parts
        ns=' '.join(f'{CIR[j]} {x}' for j,x in enumerate(parts[:6])) if len(parts)>1 else ko
        con.execute("UPDATE words SET meaning_ko=?,senses_ko=? WHERE id=?",(ko,ns,wid)); n+=1
        print(f"   {ch}  '{str(oko)[:26]}' → '{ko}'")
con.commit()
print(f"\n최종 교정: 뜻 {n}개 / 병음 {p}개")
r=con.execute("""SELECT count(*) FROM words WHERE hsk BETWEEN 4 AND 6
                 AND (meaning_ko IS NULL OR trim(meaning_ko)='')""").fetchone()[0]
print(f"HSK 4~6급 뜻 없음: {r}개")
con.close()
