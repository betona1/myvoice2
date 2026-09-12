# -*- coding: utf-8 -*-
"""4자성어 데이터를 words + chengyu 테이블에 반영한다.

  python3 scripts/chengyu_import.py [--dry]

- words        : 성어 본체(한자·병음·뜻·HSK). 기존 SRS/진도/오디오 구조를 그대로 쓴다.
- chengyu      : 성어에만 있는 부가정보(직역·대응 한국 사자성어·유래).
이미 있는 단어는 뜻을 덮어쓰지 않고 category만 '성어'로 맞춘다.
"""
import json, os, re, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chengyu_prep import acc, CLASSIC, syl_acc  # noqa: E402

DB = os.environ.get('CHENGYU_DB', 'database/voices.db')
TSV = ['database/chengyu_ko.tsv', 'database/chengyu_ko2.tsv', 'database/chengyu_ko3.tsv']
DRY = '--dry' in sys.argv


def load_ko():
    ko = {}
    for path in TSV:
        for ln, line in enumerate(open(path, encoding='utf-8'), 1):
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#'):
                continue
            f = line.split('\t')
            f += [''] * (6 - len(f))
            cn, py, mean, lit, kid, story = [x.strip() for x in f[:6]]
            if not re.fullmatch(r'[㐀-鿿]{4}', cn):
                raise SystemExit(f'{path}:{ln} 4자 한자가 아님: {cn!r}')
            if not mean:
                raise SystemExit(f'{path}:{ln} 뜻 없음: {cn}')
            if cn in ko:
                raise SystemExit(f'{path}:{ln} 중복: {cn}')
            ko[cn] = {'py': py, 'ko': mean, 'lit': lit, 'kid': kid, 'story': story}
    return ko


def main():
    cand = {r['cn']: r for r in json.load(open(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--')
                                               else os.environ['CHENGYU_CAND'], encoding='utf-8'))}
    ko = load_ko()
    missing = [c for c, r in cand.items() if not r['in_db'] and c not in ko]
    if missing:
        raise SystemExit(f'한국어 데이터 없음 {len(missing)}개: {missing[:20]}')

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS chengyu(
        word_id INTEGER PRIMARY KEY,
        chinese TEXT UNIQUE NOT NULL,
        literal_ko TEXT DEFAULT '',
        ko_idiom  TEXT DEFAULT '',
        story     TEXT DEFAULT '',
        src       TEXT DEFAULT '',
        freq      INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chengyu_cn ON chengyu(chinese)")

    seq0 = c.execute("SELECT COALESCE(MAX(seq),0) FROM words").fetchone()[0]
    added = updated = linked = 0

    for cn, r in sorted(cand.items(), key=lambda kv: -kv[1]['freq']):
        row = c.execute("SELECT * FROM words WHERE chinese=?", (cn,)).fetchone()
        d = ko.get(cn, {})
        py = d.get('py') or r['py']
        if not py and not row:
            print('  병음 없음, 건너뜀:', cn); continue
        num = ' '.join(re.findall(r'[A-Za-z:]+[1-5]', r['num'])) or ''
        hsk_new = r['hsk'] or 0
        lv = 5 if hsk_new >= 7 else (4 if hsk_new >= 5 else 3)
        en = (r['en'] or '').replace('/', '; ').strip('; ')[:400]

        if row:
            wid = row['id']
            sets, args = [], []
            if (row['category'] or '') != '성어':
                sets.append("category='성어'")
            if not row['pinyin'] and py:
                sets.append("pinyin=?"); args.append(py)
            if not (row['hsk_new'] or 0) and hsk_new:
                sets.append("hsk_new=?"); args.append(hsk_new)
            if not (row['meaning_en'] or '') and en:
                sets.append("meaning_en=?"); args.append(en)
            if sets:
                c.execute(f"UPDATE words SET {','.join(sets)} WHERE id=?", args + [wid])
                updated += 1
        else:
            seq0 += 1
            plain = re.sub(r'[^a-z]', '', py.lower().translate(str.maketrans(
                'āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü', 'aaaaeeeeiiiioooouuuuuuuuu')))
            c.execute("""INSERT INTO words
                (chinese,pinyin,tones,meaning_ko,meaning_en,level,freq,seq,subject,
                 created_at,senses_ko,hsk,hsk_new,excluded,category,py_plain,py_num)
                VALUES (?,?,?,?,?,?,?,?,'성어',datetime('now'),?,0,?,0,'성어',?,?)""",
                (cn, py, num, d['ko'], en, lv, r['freq'] // 1000, seq0,
                 d['ko'], hsk_new, plain, num.replace(' ', '')))
            wid = c.execute("SELECT id FROM words WHERE chinese=?", (cn,)).fetchone()[0]
            added += 1

        c.execute("""INSERT INTO chengyu(word_id,chinese,literal_ko,ko_idiom,story,src,freq)
                     VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(chinese) DO UPDATE SET
                       word_id=excluded.word_id,
                       literal_ko=CASE WHEN excluded.literal_ko!='' THEN excluded.literal_ko ELSE chengyu.literal_ko END,
                       ko_idiom =CASE WHEN excluded.ko_idiom !='' THEN excluded.ko_idiom  ELSE chengyu.ko_idiom  END,
                       story    =CASE WHEN excluded.story    !='' THEN excluded.story     ELSE chengyu.story     END,
                       src=excluded.src, freq=excluded.freq""",
                  (wid, cn, d.get('lit', ''), d.get('kid', ''), d.get('story', ''), r['src'], r['freq']))
        linked += 1

    # 후보에 없지만 이미 category='성어'인 것들도 chengyu 에 올려 목록을 하나로 만든다
    for row in c.execute("SELECT id,chinese FROM words WHERE category='성어'").fetchall():
        c.execute("INSERT OR IGNORE INTO chengyu(word_id,chinese,src) VALUES (?,?,'db')",
                  (row['id'], row['chinese']))

    total = c.execute("SELECT COUNT(*) FROM chengyu").fetchone()[0]
    withstory = c.execute("SELECT COUNT(*) FROM chengyu WHERE story!=''").fetchone()[0]
    if DRY:
        c.rollback(); print('[dry-run] 롤백')
    else:
        c.commit()
    print(f'신규 {added} · 보정 {updated} · 연결 {linked} · chengyu 총 {total}(유래 {withstory})')


if __name__ == '__main__':
    main()
