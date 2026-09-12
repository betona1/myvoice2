# -*- coding: utf-8 -*-
"""Whisper 가 로마자로 받아적은 것 중 실제로는 제대로 읽은 것을 걸러낸다."""
import json, re, os, sys
os.chdir('/app')
from pypinyin import pinyin as _py, Style
M={'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e','ī':'i','í':'i','ǐ':'i','ì':'i',
   'ō':'o','ó':'o','ǒ':'o','ò':'o','ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'u','ǘ':'u','ǚ':'u','ǜ':'u','ü':'u'}
def syl(ch): return re.sub(r'[^a-z]','',''.join(M.get(c,c) for c in _py(ch,style=Style.NORMAL)[0][0].lower()))
LET={'a':'ei','b':'bi','c':'xi','d':'di','e':'yi','f':'ef','g':'ji','h':'eichi','i':'ai','j':'jei',
     'k':'kei','l':'el','m':'em','n':'en','o':'ou','p':'pi','q':'kiu','r':'ar','s':'es','t':'ti',
     'u':'you','v':'wei','w':'dabliu','x':'eks','y':'wai','z':'zi'}
def romanize(t):
    t=re.sub(r"[^A-Za-z]",'',t).lower()
    return LET.get(t,t) if len(t)==1 else t
def norm(s): return s.replace('zh','z').replace('ch','c').replace('sh','s')
def close(a,b):
    a,b=norm(a),norm(b)
    if a==b: return True
    v=lambda s: re.sub(r'[^aeiou]','',s)
    return bool(a) and bool(b) and v(a)==v(b) and a[:1]==b[:1]
p='finetune_data/_wordapp/audio_bad.json'
b=json.load(open(p,encoding='utf-8'))
okl,real=[],[]
for x in b:
    h=(x['heard'] or '').strip()
    if h and not re.search(r'[一-鿿]',h) and re.fullmatch(r"[A-Za-z0-9 .,?!'-]+",h) and close(romanize(h),syl(x['ch'])):
        okl.append(x)
    else: real.append(x)
print(f"발음은 맞는데 로마자로 적힌 것 {len(okl)}개:")
print('  '+' '.join(f"{x['ch']}({x['heard'].strip()})" for x in okl))
json.dump(real,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
print(f"\n실제로 다시 만들 것 {len(real)}개")
