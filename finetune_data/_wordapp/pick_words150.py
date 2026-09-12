# -*- coding: utf-8 -*-
"""견주기용 낱말 150개 — 글자 수·묶음을 고루 섞어 DB 에서 뽑는다."""
import os, re, sqlite3, random
os.chdir('/app')
random.seed(20260909)
con = sqlite3.connect('database/voices.db'); con.row_factory = sqlite3.Row
HAN = re.compile(r'^[一-鿿]+$')
rows = [r for r in con.execute("""SELECT chinese, wordset, hsk FROM words
        WHERE COALESCE(excluded,0)=0""") if HAN.match(r['chinese'] or '')]
seen = set(['哪','哪儿','张','些','那些','很','个','二','会','点','材料','妈妈','什么','一点儿','图书馆'])
def take(cond, n):
    pool = [r['chinese'] for r in rows if cond(r) and r['chinese'] not in seen]
    random.shuffle(pool); got = pool[:n]; seen.update(got); return got
out  = take(lambda r: len(r['chinese']) == 1 and r['wordset'] == '양사', 20)
out += take(lambda r: len(r['chinese']) == 1, 20)
out += take(lambda r: len(r['chinese']) == 2 and (r['hsk'] or 9) <= 2, 30)
out += take(lambda r: len(r['chinese']) == 2 and 3 <= (r['hsk'] or 0) <= 4, 25)
out += take(lambda r: len(r['chinese']) == 2 and (r['hsk'] or 0) >= 5, 15)
out += take(lambda r: len(r['chinese']) == 3, 25)
out += take(lambda r: len(r['chinese']) >= 4, 15)
open('temp/voicesample/w150.txt', 'w', encoding='utf-8').write('\n'.join(out))
from collections import Counter
print(f'{len(out)}개 —', dict(Counter(len(w) for w in out)))
print(' '.join(out[:40]), '…')
