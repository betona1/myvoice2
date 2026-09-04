# -*- coding: utf-8 -*-
"""꾸러미를 '앱에 넣을 것'과 '나중에 받을 것'으로 가른다.

   bundled/  앱 안에 그대로 담는다 (pubspec 의 assets). 풀 것도 받을 것도 없다.
   remote/   서버에 올려 두고 앱이 골라 받는다 (하루치 zip).

   앱 쪽 코드가 갈라지지 않도록 **속 모양을 똑같이** 맞춘다 —
   내장이든 내려받은 것이든 '하루치 폴더 하나(data.json + audio/)' 로 보인다.
"""
import os, re, sys, json, shutil, zipfile
os.chdir('/app')
BASE='/app/finetune_data/_export'
BUNDLE=set(sys.argv[1:] or ['양사','hsk1','hsk2','hsk3'])
idx=json.load(open(os.path.join(BASE,'index.json'), encoding='utf-8'))

BD=os.path.join(BASE,'bundled'); RM=os.path.join(BASE,'remote')
shutil.rmtree(BD, ignore_errors=True); shutil.rmtree(RM, ignore_errors=True)
os.makedirs(os.path.join(BD,'packs'), exist_ok=True)
os.makedirs(os.path.join(RM,'packs'), exist_ok=True)

bi={k:v for k,v in idx.items() if k!='courses'}; bi['courses']=[]
ri={k:v for k,v in idx.items() if k!='courses'}; ri['courses']=[]
bi['kind']='앱에 담긴 것';  ri['kind']='내려받는 것'
adirs=[]

for c in idx['courses']:
    if c['id'] in BUNDLE:
        cc={k:v for k,v in c.items() if k!='lessons'}; cc['lessons']=[]
        for l in c['lessons']:
            src=os.path.join(BASE, l['file'])
            day=f"d{l['day']:03d}"
            out=os.path.join(BD,'packs',c['id'],day)
            os.makedirs(out, exist_ok=True)
            with zipfile.ZipFile(src) as z: z.extractall(out)   # 앱에선 풀 필요가 없게 미리 푼다
            rel=f"packs/{c['id']}/{day}"
            adirs += [f"assets/{rel}/", f"assets/{rel}/audio/"]
            ll={k:v for k,v in l.items() if k not in ('file','bytes','sha256')}
            ll['dir']=rel
            cc['lessons'].append(ll)
        bi['courses'].append(cc)
    else:
        cc={k:v for k,v in c.items() if k!='lessons'}; cc['lessons']=[]
        for l in c['lessons']:
            shutil.copy2(os.path.join(BASE,l['file']), os.path.join(RM,'packs',os.path.basename(l['file'])))
            cc['lessons'].append(l)
        ri['courses'].append(cc)

json.dump(bi, open(os.path.join(BD,'index.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(ri, open(os.path.join(RM,'index.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)

# pubspec 에 붙일 조각 — 폴더만 적으면 그 안 파일이 다 들어간다 (하위 폴더는 따로 적어야 한다)
snip=("flutter:\n  assets:\n    - assets/index.json\n"
      + ''.join(f"    - {d}\n" for d in adirs))
open(os.path.join(BD,'pubspec_assets.yaml'),'w',encoding='utf-8').write(snip)

def mb(p): return sum(os.path.getsize(os.path.join(r,f))
                      for r,_,fs in os.walk(p) for f in fs)/1048576
print(f"앱에 담을 것  : {', '.join(c['label'] for c in bi['courses'])}")
print(f"                낱말 {sum(c['words'] for c in bi['courses'])}개 · "
      f"{sum(c['days'] for c in bi['courses'])}일치 · {mb(BD):.1f} MB")
print(f"내려받을 것   : {', '.join(c['label'] for c in ri['courses'])}")
print(f"                낱말 {sum(c['words'] for c in ri['courses'])}개 · "
      f"{sum(c['days'] for c in ri['courses'])}일치 · {mb(RM):.1f} MB")
print(f"\npubspec 조각  : {os.path.join(BD,'pubspec_assets.yaml')} ({len(adirs)}줄)")
