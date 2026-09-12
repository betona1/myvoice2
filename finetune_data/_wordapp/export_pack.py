# -*- coding: utf-8 -*-
"""학습 자료를 '하루치'로 잘라 앱이 내려받을 꾸러미로 만든다.

   왜 이렇게 나누나 — HSK 전 급수를 통째로 담으면 WAV 538MB(mp3 로 줄여도 45MB 남짓)라
   앱에 한꺼번에 싣기 어렵다. 배우는 쪽도 하루 분량씩 받는 편이 자연스럽다.

     index.json            무엇이 있는지 (급수·날짜·낱말 수·크기·해시)
     packs/<이름>_d001.zip  하루치 (data.json + audio/*.mp3)

   앱은 index.json 만 먼저 받아 목록을 보이고, 고른 날짜의 zip 만 내려받아 풀면 된다.
   sha256 을 함께 적어 두니 이미 받은 것은 다시 받지 않아도 된다.
   획순은 담지 않는다 — hanzi-writer-data(MIT)를 앱에서 받아 쓰면 된다.

   쓰기:  python3 export_pack.py 양사 hsk1 hsk2 --per 20
"""
import os
from datetime import datetime, re, sys, json, shutil, sqlite3, subprocess, hashlib, zipfile, math
os.chdir('/app')
import soundfile as sf
# --per 20 처럼 값을 갖는 옵션은 그 값까지 걷어내야 한다 (안 그러면 '20' 이 과정 이름이 된다)
TAKES_VAL={'--per'}
ARGS=[]; OPT={}; i=0; av=sys.argv[1:]
while i < len(av):
    a=av[i]
    if a in TAKES_VAL and i+1 < len(av): OPT[a]=av[i+1]; i+=2; continue
    if a.startswith('--'): OPT[a]=True; i+=1; continue
    ARGS.append(a); i+=1
PER=int(OPT.get('--per', 20))           # 하루에 몇 낱말
WAV='--wav' in OPT
BASE='/app/finetune_data/_export'
PACKS=os.path.join(BASE,'packs')
TMP='/tmp/_pack'
CJK=re.compile(r'[一-鿿]')
os.makedirs(PACKS, exist_ok=True)
con=sqlite3.connect('database/voices.db'); con.row_factory=sqlite3.Row
CK={r['char']:(r['pinyin'],r['ko']) for r in con.execute("SELECT char,pinyin,ko FROM char_ko")}

def pick(name):
    m=re.fullmatch(r'hsk([1-6])', name)
    if m: return ("hsk=? AND COALESCE(excluded,0)=0", (int(m.group(1)),), f"HSK {m.group(1)}급")
    return ("wordset=? AND COALESCE(excluded,0)=0", (name,), name)

def conv(src, stem, aud):
    if not (src and os.path.exists(src)): return None
    if WAV:
        dst=stem+'.wav'; shutil.copy2(src, os.path.join(aud,dst))
    else:
        dst=stem+'.mp3'
        subprocess.run(['ffmpeg','-y','-loglevel','error','-i',src,'-ac','1','-ar','24000',
                        '-b:a','64k', os.path.join(aud,dst)], check=False)
        if not os.path.exists(os.path.join(aud,dst)): return None
    w,sr=sf.read(src)
    return {"file": f"audio/{dst}", "sec": round(len(w)/sr,2)}

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda: f.read(1<<20), b''): h.update(b)
    return h.hexdigest()

index={"version":1, "per_day":PER, "audio":"wav" if WAV else "mp3 64k mono",
       "voices":{"m":"남성 (이한)","f":"여성 (리리)"},
       "note":"audio_say 가 있으면 그 낱말을 읽은 소리입니다 (홀로 읽히지 않는 양사).",
       "courses":[]}
if os.path.exists(os.path.join(BASE,'index.json')):
    try: index=json.load(open(os.path.join(BASE,'index.json'),encoding='utf-8'))
    except Exception: pass
index["courses"]=[c for c in index.get("courses",[]) if c["id"] not in ARGS]

for name in ARGS:
    where, params, label = pick(name)
    rows=con.execute(f"""SELECT id,chinese,pinyin,meaning_ko,senses_ko,audio1,audio2,audio_say,
                                hsk,wordset FROM words WHERE {where} ORDER BY seq,id""", params).fetchall()
    days=math.ceil(len(rows)/PER)
    course={"id":name, "label":label, "words":len(rows), "days":days, "lessons":[]}
    for d in range(days):
        part=rows[d*PER:(d+1)*PER]
        OUT=os.path.join(TMP, f"{name}_d{d+1:03d}"); AUD=os.path.join(OUT,'audio')
        shutil.rmtree(OUT, ignore_errors=True); os.makedirs(AUD, exist_ok=True)
        words=[]; chars=set()
        for r in part:
            wid=r['id']
            w={"id":wid, "chinese":r['chinese'], "pinyin":r['pinyin'] or '',
               "meaning_ko":r['meaning_ko'] or '', "senses_ko":r['senses_ko'] or '',
               "hsk":r['hsk'] or 0, "set":r['wordset'] or '',
               "audio_say":(r['audio_say'] or '').strip() or None,
               "audio":{"m":conv(r['audio1'], f"w{wid}_m", AUD),
                        "f":conv(r['audio2'], f"w{wid}_f", AUD)},
               "examples":[]}
            for e in con.execute("""SELECT id,chinese,pinyin,meaning_ko,audio1,audio2
                                    FROM word_examples WHERE word_id=? ORDER BY seq,id""",(wid,)):
                w["examples"].append({"id":e['id'], "chinese":e['chinese'],
                    "pinyin":e['pinyin'] or '', "meaning_ko":e['meaning_ko'] or '',
                    "audio":{"m":conv(e['audio1'], f"e{e['id']}_m", AUD),
                             "f":conv(e['audio2'], f"e{e['id']}_f", AUD)}})
                chars |= set(CJK.findall(e['chinese']))
            chars |= set(CJK.findall(r['chinese']))
            words.append(w)
        cinfo={}
        for c in sorted(chars):
            rr=con.execute("SELECT pinyin,meaning_ko FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",(c,)).fetchone()
            py=((rr['pinyin'] if rr else '') or '') or (CK.get(c,('',''))[0] or '')
            ko=((rr['meaning_ko'] if rr else '') or '').strip() or (CK.get(c,('',''))[1] or '')
            if py or ko: cinfo[c]={"pinyin":py,"ko":ko}
        json.dump({"version":1,"course":name,"label":label,"day":d+1,"days":days,
                   "words":words,"chars":cinfo},
                  open(os.path.join(OUT,'data.json'),'w',encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        zp=os.path.join(PACKS, f"{name}_d{d+1:03d}.zip")
        with zipfile.ZipFile(zp,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for root,_,fs in os.walk(OUT):
                for f in fs:
                    full=os.path.join(root,f)
                    z.write(full, os.path.relpath(full, OUT))
        shutil.rmtree(OUT, ignore_errors=True)
        course["lessons"].append({
            "day": d+1, "week": (d//7)+1,
            "file": f"packs/{os.path.basename(zp)}",
            "words": len(words),
            "examples": sum(len(w['examples']) for w in words),
            "preview": [w['chinese'] for w in words[:6]],
            "bytes": os.path.getsize(zp), "sha256": sha(zp)})
        print(f"   {label} {d+1}/{days}일차  낱말 {len(words)}  {os.path.getsize(zp)/1048576:.1f} MB", flush=True)
    index["courses"].append(course)
    mb=sum(l["bytes"] for l in course["lessons"])/1048576
    print(f"■ {label}: {len(rows)}낱말 → {days}일치 · 모두 {mb:.1f} MB", flush=True)

# ⚠️ data_version 과 목소리 이름은 **매번 새로 쓴다**.
#    기존 index.json 을 읽어 과목만 갈아 끼우는 구조라, 안 쓰면 옛 값이 남아
#    앱이 「바뀐 게 없다」고 보고 새 음성을 안 받아 간다 (2026-09-11 실제로 그랬다).
index["data_version"] = datetime.now().strftime('%Y%m%d-%H%M')
index["voices"] = {"m": "남성 (이한)", "f": "여성 (리리)"}
json.dump(index, open(os.path.join(BASE,'index.json'),'w',encoding='utf-8'),
          ensure_ascii=False, indent=1)
print(f"\nindex.json 갱신 — 과정 {len(index['courses'])}개")
