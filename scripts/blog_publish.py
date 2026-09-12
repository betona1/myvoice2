"""네이버 블로그에 글 올리기 (Playwright 브라우저 자동화).

⚠️ 네이버 '블로그 글쓰기 API' 는 2020년 5월에 없어졌다. 공식 자동 발행 길은 없다.
   그래서 이 스크립트는 사람이 하는 일을 브라우저로 대신한다.
     · 네이버 약관에 걸릴 수 있다. 내 블로그에, 내 글을 올리는 데만 쓸 것.
     · 캡차·2단계 인증이 뜨면 멈춘다. 그때는 창에서 직접 넘겨 주면 이어 간다.
     · 스마트에디터가 바뀌면 단추를 못 찾을 수 있다. 그러면 --dry-run 으로 살펴본다.

    # 준비 (한 번만)
    pip install playwright && playwright install chromium
    # 로그인 상태 저장 — 창이 열리면 직접 로그인한 뒤 창을 닫는다
    python3 scripts/blog_publish.py --login
    # 확인만 (글은 안 올라간다)
    python3 scripts/blog_publish.py --dir temp/blog/out/동사/002_觉得-上班-应该 --dry-run
    # 임시저장까지
    python3 scripts/blog_publish.py --dir ... --save-draft
    # 정말 올리기
    python3 scripts/blog_publish.py --dir ... --publish
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "temp" / "blog" / ".naver_state.json"     # 로그인 쿠키 (git 제외됨)
WRITE_URL = "https://blog.naver.com/{blog_id}?Redirect=Write"


def _need_playwright():
    try:
        from playwright.sync_api import sync_playwright     # noqa: F401
        return True
    except ImportError:
        print("Playwright 가 없습니다.\n"
              "  pip install playwright && playwright install chromium", file=sys.stderr)
        return False


def do_login(blog_id):
    """창을 띄워 직접 로그인하게 하고, 끝난 상태를 담아 둔다."""
    from playwright.sync_api import sync_playwright
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(locale="ko-KR")
        pg = ctx.new_page()
        pg.goto("https://nid.naver.com/nidlogin.login")
        print("창에서 로그인한 뒤, 블로그가 열리면 이 터미널에서 엔터를 누르세요.")
        try:
            input()
        except EOFError:
            time.sleep(120)
        ctx.storage_state(path=str(STATE))
        b.close()
    print(f"로그인 상태를 담았습니다 → {STATE}")


def load_post(d: Path):
    meta = json.loads((d / "meta.json").read_text())
    meta["_dir"] = d
    meta["_title"] = (d / "제목.txt").read_text().strip()
    meta["_body"] = (d / "본문.txt").read_text()
    meta["_cover"] = d / meta["cover"]
    meta["_gifs"] = [d / g for g in meta["gifs"]]
    return meta


def publish(meta, blog_id, mode="dry", headless=False, wait=2.0):
    """글 한 편 올리기. mode: dry | draft | publish"""
    from playwright.sync_api import sync_playwright
    d = meta["_dir"]
    print(f"\n▶ {meta['_title']}")
    print(f"   대표이미지 {meta['_cover'].name} · 획순 {len(meta['_gifs'])}개 · "
          f"쇼츠 {meta['shorts_url'] or '없음'}")
    if mode == "dry":
        print("   (--dry-run: 창만 열어 글쓰기 화면까지 갑니다)")
    if not STATE.exists():
        print("   [!] 로그인 상태가 없습니다 — 먼저 --login 을 하세요")
        return False

    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        ctx = b.new_context(storage_state=str(STATE), locale="ko-KR")
        pg = ctx.new_page()
        pg.goto(WRITE_URL.format(blog_id=blog_id), wait_until="domcontentloaded")
        time.sleep(wait * 2)

        # 스마트에디터는 iframe 안에 있다
        fr = None
        for f in pg.frames:
            if "PostWriteForm" in (f.url or "") or "postwrite" in (f.url or "").lower():
                fr = f
                break
        fr = fr or pg.main_frame

        def click_any(selectors, label):
            for s in selectors:
                try:
                    el = fr.locator(s).first
                    if el.count() and el.is_visible():
                        el.click(timeout=4000)
                        return True
                except Exception:
                    continue
            print(f"   [!] '{label}' 단추를 못 찾았습니다")
            return False

        # 이전 글 이어쓰기 안내창 닫기
        click_any(["button.se-popup-button-cancel", "text=취소"], "이어쓰기 취소")
        time.sleep(wait)

        # 제목
        try:
            fr.locator(".se-section-documentTitle .se-text-paragraph").first.click(timeout=8000)
            pg.keyboard.type(meta["_title"], delay=12)
        except Exception:
            print("   [!] 제목 칸을 못 찾았습니다 — 스마트에디터가 바뀐 듯합니다")
            if mode == "dry":
                input("   창을 살펴본 뒤 엔터를 누르면 닫습니다. ")
            b.close()
            return False

        # 본문 — 줄마다 넣는다. [[...]] 자리에는 그림을 올린다
        fr.locator(".se-component.se-text .se-text-paragraph").last.click()
        for line in meta["_body"].split("\n"):
            s = line.strip()
            if s == "[[대표이미지]]" or s.startswith("[[획순_"):
                name = "대표이미지.png" if "대표" in s else s[2:-2]
                f = d / name
                if f.exists() and mode != "dry":
                    try:
                        pg.set_input_files("input[type=file]", str(f))
                        time.sleep(wait * 1.5)
                    except Exception:
                        pg.keyboard.type(f"[그림: {name}]")
                else:
                    pg.keyboard.type(f"[그림: {name}]")
                pg.keyboard.press("Enter")
                continue
            pg.keyboard.type(s, delay=4)
            pg.keyboard.press("Enter")

        if mode == "draft":
            click_any(["button.save_btn__bzc5B", "text=저장"], "임시저장")
            time.sleep(wait * 2)
            print("   임시저장했습니다 — 블로그에서 확인하고 직접 발행하세요")
        elif mode == "publish":
            if click_any(["button.publish_btn__m9KHH", "text=발행"], "발행"):
                time.sleep(wait)
                click_any(["button.confirm_btn__WEaBq", "text=발행"], "발행 확인")
                time.sleep(wait * 3)
                print(f"   발행했습니다 → {pg.url}")
        else:
            input("   창을 살펴본 뒤 엔터를 누르면 닫습니다. ")
        b.close()
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--blog-id", default="naintoktok")
    ap.add_argument("--login", action="store_true", help="로그인 상태 담아 두기")
    ap.add_argument("--dir", help="글 폴더 (temp/blog/out/동사/002_…)")
    ap.add_argument("--dry-run", action="store_true", help="글쓰기 화면까지만")
    ap.add_argument("--save-draft", action="store_true", help="임시저장")
    ap.add_argument("--publish", action="store_true", help="정말 발행")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    if not _need_playwright():
        sys.exit(1)
    if a.login:
        do_login(a.blog_id)
        sys.exit(0)
    if not a.dir:
        ap.error("--dir 로 글 폴더를 주세요 (또는 --login)")
    mode = "publish" if a.publish else ("draft" if a.save_draft else "dry")
    if mode == "publish":
        print("⚠️ 정말 블로그에 올립니다. 5초 뒤 시작 — 그만두려면 Ctrl+C")
        time.sleep(5)
    publish(load_post(Path(a.dir)), a.blog_id, mode=mode, headless=a.headless)
