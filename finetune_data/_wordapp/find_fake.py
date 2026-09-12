# -*- coding: utf-8 -*-
"""문장에서 잘못 잘려 만들어진 '없는 낱말'을 찾는다.
   歌真(gē zhēn)→「게젠」처럼, 뜻 자리에 **병음을 한글로 옮겨 적은 것**이 들어가 있다.
   뜻을 못 찾아 소리만 적어 둔 것이니, 그 낱말 자체가 없는 말일 가능성이 크다.
   ⚠️ 东大门(동대문)·世界地图(세계지도)처럼 멀쩡한 것도 사전에는 없다 —
      '뜻이 곧 소리인가' 로만 가린다."""
import re, sqlite3, unicodedata
CED=set()
for ln in open('/app/database/cedict_full.txt', encoding='utf-8'):
    if ln.startswith('#'): continue
    try: CED.add(ln.split(' ',2)[1])
    except Exception: pass

# 병음을 한글로 대충 옮긴다 (자음·모음의 첫머리만 맞으면 된다)
INI={'zh':'ㅈ','ch':'ㅊ','sh':'ㅅ','b':'ㅂ','p':'ㅍ','m':'ㅁ','f':'ㅍ','d':'ㄷ','t':'ㅌ',
     'n':'ㄴ','l':'ㄹ','g':'ㄱ','k':'ㅋ','h':'ㅎ','j':'ㅈ','q':'ㅊ','x':'ㅅ','z':'ㅈ',
     'c':'ㅊ','s':'ㅅ','r':'ㄹ','y':'ㅇ','w':'ㅇ'}
CHO='ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
def cho(k):
    if not ('가'<=k<='힣'): return ''
    c=CHO[(ord(k)-0xAC00)//588]
    return {'ㄲ':'ㄱ','ㄸ':'ㄷ','ㅃ':'ㅂ','ㅆ':'ㅅ','ㅉ':'ㅈ'}.get(c,c)
def flat(p):
    return ''.join(c for c in unicodedata.normalize('NFD', (p or '').lower())
                   if not unicodedata.combining(c))
def ini(syl):
    for k in ('zh','ch','sh'):
        if syl.startswith(k): return INI[k]
    return INI.get(syl[:1], '')

con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
HAN=re.compile(r'^[一-鿿]+$'); KO=re.compile(r'^[가-힣]+$')
bad=[]
for r in con.execute("""SELECT id,chinese,pinyin,meaning_ko FROM words
                        WHERE COALESCE(excluded,0)=0 AND length(chinese)>=2"""):
    ch=r['chinese'] or ''
    if not HAN.match(ch) or ch in CED: continue
    ko=(r['meaning_ko'] or '').strip()
    if not (ko and KO.match(ko)): continue
    syls=flat(r['pinyin'] or '').split()
    if len(syls)!=len(ko) or not syls: continue
    # 음절마다 첫소리가 맞아떨어지면 '뜻이 아니라 소리를 적은 것'이다
    if all(ini(s) and ini(s)==cho(k) for s,k in zip(syls, ko)):
        bad.append((r['id'], ch, r['pinyin'], ko))
print(f"뜻 자리에 소리만 적힌 낱말 {len(bad)}개")
for id_,ch,py,ko in bad:
    print(f"  [{id_}] {ch} ({py}) — {ko}")
