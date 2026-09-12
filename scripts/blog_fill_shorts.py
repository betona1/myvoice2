"""쇼츠를 올린 뒤, 빈 자리([[쇼츠주소]])에 주소를 채워 넣는다.

    # 유튜브에서 목록을 다시 받아 짝을 맞춰 채우기
    python3 scripts/blog_fill_shorts.py --refresh --set 형용사
    # 손으로 한 편만
    python3 scripts/blog_fill_shorts.py --set 형용사 --day 1 --id AbCdEfG
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "temp" / "blog" / "out"
CHANNEL = "https://www.youtube.com/@VAVELINGAPP/shorts"


def refresh_shorts():
    """유튜브 쇼츠 목록을 다시 받아 {(갈래, 시작번호): 영상ID} 로 돌려준다."""
    cmd = ["docker", "exec", "myvoice2-prod", "sh", "-lc",
           f'yt-dlp --flat-playlist --no-warnings --print "%(id)s|%(title)s" "{CHANNEL}"']
    try:
        txt = subprocess.run(cmd, capture_output=True, text=True, timeout=420).stdout
    except Exception as e:
        print("유튜브 목록을 받지 못했습니다:", e)
        return {}
    m = {}
    for line in txt.splitlines():
        if "|" not in line:
            continue
        vid, title = line.split("|", 1)
        g = re.match(r'^(\S+)\s+(.+?)\s+—.*?\((\d+)(?:\s*~\s*\d+)?\s*/\s*\d+\)', title)
        if g:
            m[(g.group(1), int(g.group(3)))] = vid.strip()
    print(f"유튜브에서 {len(m)}편을 읽었습니다")
    return m


def fill(wordset, day, vid):
    ds = sorted((OUT / wordset).glob(f"{day:03d}_*"))
    if not ds:
        print(f"  [!] {wordset} {day}일차 폴더가 없습니다")
        return False
    d = ds[0]
    meta = json.loads((d / "meta.json").read_text())
    url = f"https://www.youtube.com/shorts/{vid}"
    dots = "·".join(w["cn"] for w in meta["words"])
    a, b = meta["range"]
    rng = f"({a}~{b}/{meta['total']})" if a != b else f"({a}/{meta['total']})"
    new_block = (f"🎬 유튜브 쇼츠로 학습하기\n{dots} — 발음·획순·예문 {rng}\n{url}")

    for name in ("본문.txt", "본문.html"):
        p = d / name
        t = p.read_text()
        if "[[쇼츠주소]]" not in t and meta["shorts_id"]:
            continue
        if name == "본문.txt":
            t = re.sub(r'🎬 유튜브 영상으로 학습하기\n\[\[쇼츠주소\]\][^\n]*', new_block, t)
        else:
            t = re.sub(r'<h3>🎬[^<]*</h3>', "<h3>🎬 유튜브 쇼츠로 학습하기</h3>", t)
            t = re.sub(r'<p><strong>\[\[쇼츠주소\]\][^<]*</strong></p>',
                       f'<p>{dots} — 발음·획순·예문 {rng}</p><p><a href="{url}">{url}</a></p>', t)
        p.write_text(t, encoding="utf-8")
    meta["shorts_id"], meta["shorts_url"] = vid, url
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    print(f"  {wordset} {day}일차 {dots} → {url}")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="wordset", required=True)
    ap.add_argument("--refresh", action="store_true", help="유튜브에서 받아 일괄 채우기")
    ap.add_argument("--day", type=int)
    ap.add_argument("--id", help="유튜브 영상 ID")
    a = ap.parse_args()
    if a.refresh:
        m = refresh_shorts()
        n = 0
        for (kind, start), vid in sorted(m.items()):
            if kind != a.wordset:
                continue
            n += fill(a.wordset, (start - 1) // 3 + 1, vid)
        print(f"\n✅ {n}편 채웠습니다")
    elif a.day and a.id:
        fill(a.wordset, a.day, a.id)
    else:
        ap.error("--refresh 또는 (--day 와 --id) 를 주세요")
