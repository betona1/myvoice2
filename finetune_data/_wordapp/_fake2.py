"""잘못 잘린 낱말인지 가린다.
   歌真 은 「广播里的歌真好听」에서 잘려 나왔다 — 형태소로 나누면 歌 / 真 으로 갈린다.
   반면 明洞·豫园 같은 고유명사는 한 덩이로 잡힌다.
   그래서 '카드 글을 형태소로 나눴을 때 그 낱말이 한 번도 안 나오면' 잘못 잘린 것으로 본다."""
import sqlite3, re, sys
sys.path.insert(0,'/app')
import jieba
con=sqlite3.connect('/app/database/voices.db'); con.row_factory=sqlite3.Row
toks=set()
# 단어 카드는 빼야 한다 — 잘못 잘린 낱말이 그 자체로 카드가 되어 있어 셈이 흐려진다
for r in con.execute("SELECT chinese FROM chinese_cards WHERE group_name<>'단어' AND length(chinese)>=6"):
    for t in jieba.cut(r['chinese'] or ''):
        if len(t)>=2: toks.add(t)
print(f"카드 글에서 나온 낱말 {len(toks)}개")
cand=[l.strip() for l in sys.stdin if l.strip()]
gone=[c for c in cand if c not in toks]
print(f"\n형태소로 나눠도 안 나오는 것 {len(gone)}개:")
print('  ' + ' '.join(gone))
