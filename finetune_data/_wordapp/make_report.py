# -*- coding: utf-8 -*-
"""문제 단어 리스트 생성 → outputs/chinese/word_problems.tsv / .json
다른 도구·AI에 넘겨 일괄 수정할 수 있도록 유형별로 분류한다."""
import os, re, json, sqlite3
os.chdir('/app')
DB='database/voices.db'
OUT_T='outputs/chinese/word_problems.tsv'
OUT_J='outputs/chinese/word_problems.json'

L=re.compile(r'^(\S+)\s+(\S+)\s+\[')
HEAD=set()
for ln in open('database/cedict_full.txt',encoding='utf-8'):
    if ln.startswith('#'): continue
    m=L.match(ln)
    if m: HEAD.add(m.group(2))

MOD={'매우','아주','너무','정말','진짜','조금','약간','많이','잘','더','가장','제일','또','다시',
     '함께','같이','바로','이미','아직','곧','막','점점','서로','모두','다','좀','꽤','상당히'}
PYLEFT=re.compile(r'[a-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüńňǹ]{2,}\s*[—\-–]')
LATIN=re.compile(r'(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])')
NOTA=re.compile(r'(\[|\]|\||변형|variant|CL:|abbr|참조)')
EXPL=re.compile(r'(성조 변화|의 성조|운모|성모|경성 변화|발음이? 동일|언어유희|의문문|동사 형식|'
                r'단위 생략|수식 없는|될 수 없음|입엄력|입말력|입욕력|내 용 을 입|예시|연습)')
REPEAT=re.compile(r'(.{2,})\s*\1')

con=sqlite3.connect('file:'+DB+'?mode=ro',uri=True)
rows=con.execute("""SELECT id,seq,chinese,pinyin,meaning_ko,meaning_en,senses_ko,subject,level,freq
                    FROM words ORDER BY seq""").fetchall()
# 뜻 중복 그룹
from collections import defaultdict
dup=defaultdict(list)
for r in rows:
    k=(r[4] or '').strip()
    if k: dup[k].append(r[2])

probs=[]
for wid,seq,ch,py,ko,en,sen,subj,lv,freq in rows:
    ko=(ko or '').strip(); tags=[]
    if not ko: tags.append('뜻없음')
    if ko in MOD and len(ch)>=3: tags.append('뜻잘림(수식어만)')
    if PYLEFT.search(ko): tags.append('병음섞임')
    if LATIN.search(ko): tags.append('영어잔존')
    if NOTA.search(ko): tags.append('사전표기잔존')
    if EXPL.search(ko): tags.append('설명문(단어아님)')
    if REPEAT.search(ko): tags.append('뜻중복반복')
    if ch not in HEAD and len(ch)>=4: tags.append('사전미등재(장문)')
    if len(dup.get(ko,[]))>1: tags.append(f'뜻겹침({len(dup[ko])}개)')
    if len(ch)>=3 and len(ko)<=2 and ko not in MOD: tags.append('뜻너무짧음')
    if tags:
        probs.append({'seq':seq,'id':wid,'chinese':ch,'pinyin':py,'meaning_ko':ko,
                      'meaning_en':en or '','senses_ko':sen or '','subject':subj,
                      'level':lv,'freq':freq,'problems':';'.join(tags)})

os.makedirs('outputs/chinese', exist_ok=True)
with open(OUT_T,'w',encoding='utf-8') as f:
    f.write('seq\tid\t한자\t병음\t현재뜻\t영어뜻\t전체뜻\t출처\t급수\t빈도\t문제유형\n')
    for p in probs:
        f.write('\t'.join(str(p[k]).replace('\t',' ').replace('\n',' ') for k in
                ['seq','id','chinese','pinyin','meaning_ko','meaning_en','senses_ko',
                 'subject','level','freq','problems'])+'\n')
json.dump(probs, open(OUT_J,'w',encoding='utf-8'), ensure_ascii=False, indent=1)

from collections import Counter
cnt=Counter()
for p in probs:
    for t in p['problems'].split(';'): cnt[t]+=1
print(f"  전체 단어 {len(rows)}개 중 문제 {len(probs)}개")
print(f"  저장: {OUT_T} ({os.path.getsize(OUT_T)//1024}KB)")
print(f"        {OUT_J} ({os.path.getsize(OUT_J)//1024}KB)")
print()
for t,n in cnt.most_common(): print(f"   {t:22s} {n:>5}")
con.close()
