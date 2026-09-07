# -*- coding: utf-8 -*-
"""문제 꾸러미 — 독해 2부분(지문+선택지)과 문장 순서·낱말 배열 문제.
   음성이 없어 아주 가볍다(수십 KB). index.json 에 과정 하나로 얹는다.
   ⚠️ 독해 2부분은 교안에 정답이 없다 — answer 를 담지 않는다(앱에서 채점하면 안 된다)."""
import json, os, sqlite3, hashlib, zipfile, shutil
os.chdir('/app')
BASE='/app/finetune_data/_export'; PACKS=os.path.join(BASE,'packs')
os.makedirs(PACKS, exist_ok=True)
con=sqlite3.connect('database/voices.db'); con.row_factory=sqlite3.Row

reading=[]
for r in con.execute("""SELECT week,class_num,page,no,passage,options,meaning_ko,options_ko,
                               passage_py,options_py FROM reading_items ORDER BY week,page"""):
    reading.append({"week":r['week'], "cls":r['class_num'], "no":r['no'],
                    "passage":r['passage'], "passage_py":r['passage_py'] or '',
                    "passage_ko":r['meaning_ko'] or '',
                    "options":json.loads(r['options']),
                    "options_py":json.loads(r['options_py'] or '[]'),
                    "options_ko":json.loads(r['options_ko'] or '[]')})
puzzles=[]
for r in con.execute("""SELECT week,class_num,level,answer,tokens,meaning_ko,
                               COALESCE(kind,'word') kind
                        FROM writing_puzzles ORDER BY week,class_num,sort_order"""):
    puzzles.append({"week":r['week'], "cls":r['class_num'], "level":r['level'],
                    "kind":r['kind'],              # word=낱말 배열 · order=세 도막 차례
                    "answer":r['answer'],          # 정답 차례로 이어 붙인 글
                    "tokens":json.loads(r['tokens']),   # 정답 차례 — 앱에서 섞어 보여 준다
                    "ko":r['meaning_ko'] or ''})

out=os.path.join('/tmp','_quiz'); shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
json.dump({"version":1, "course":"quiz", "label":"문제 풀기",
           "note":"독해 2부분은 교안에 정답이 없어 answer 가 없습니다. 순서·배열 문제는 tokens 가 정답 차례입니다.",
           "reading":reading, "puzzles":puzzles},
          open(os.path.join(out,'data.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
zp=os.path.join(PACKS,'quiz_d001.zip')
with zipfile.ZipFile(zp,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    z.write(os.path.join(out,'data.json'),'data.json')
shutil.rmtree(out, ignore_errors=True)
h=hashlib.sha256(open(zp,'rb').read()).hexdigest()
idx=json.load(open(os.path.join(BASE,'index.json'),encoding='utf-8'))
idx['courses']=[c for c in idx['courses'] if c['id']!='quiz']
idx['courses'].append({"id":"quiz","label":"문제 풀기","words":0,"days":1,
  "verified":"교안에서 뽑음","built":"",
  "lessons":[{"day":1,"week":1,"file":"packs/quiz_d001.zip",
              "words":len(reading)+len(puzzles),
              "examples":0,
              "preview":["독해 %d문제"%len(reading), "순서·배열 %d문제"%len(puzzles)],
              "bytes":os.path.getsize(zp), "sha256":h, "rev":h[:12]}]})
json.dump(idx, open(os.path.join(BASE,'index.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"독해 {len(reading)}문제 · 순서·배열 {len(puzzles)}문제 · {os.path.getsize(zp)/1024:.0f} KB")
