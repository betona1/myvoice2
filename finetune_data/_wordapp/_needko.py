"""예문에 나오는 글자 가운데 한글 뜻이 없는 것을 센다 (쓰기 공부에서 글자마다 보여 줘야 한다)."""
import os, re, sqlite3, json
os.chdir('/app')
CJK=re.compile(r'[一-鿿]')
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
chars=set()
for e in con.execute("SELECT chinese FROM word_examples"):
    chars |= set(CJK.findall(e['chinese']))
have={}
for r in con.execute("SELECT chinese,meaning_ko FROM words WHERE length(chinese)=1 AND COALESCE(excluded,0)=0"):
    if (r['meaning_ko'] or '').strip(): have[r['chinese']]=r['meaning_ko']
miss=sorted(chars-set(have))
print(f"예문에 나오는 글자 {len(chars)}자 · 한글 뜻 있는 것 {len(chars)-len(miss)}자 · 없는 것 {len(miss)}자")
print(''.join(miss))
json.dump(miss, open('finetune_data/_wordapp/char_need_ko.json','w',encoding='utf-8'), ensure_ascii=False)
