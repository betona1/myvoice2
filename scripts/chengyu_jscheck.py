# -*- coding: utf-8 -*-
"""frontend/chengyu.html 의 <script> 블록을 뽑아 node --check 로 문법만 확인한다."""
import re, subprocess, sys, tempfile, os

path = sys.argv[1] if len(sys.argv) > 1 else 'frontend/chengyu.html'
html = open(path, encoding='utf-8').read()
blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
if not blocks:
    raise SystemExit('script 블록을 찾지 못했습니다')
bad = 0
for i, b in enumerate(blocks, 1):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(b); tmp = f.name
    r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
    print(f'script #{i} ({len(b.splitlines())}줄):', 'OK' if r.returncode == 0 else 'FAIL')
    if r.returncode:
        bad += 1
        print(r.stderr[:1500])
    os.unlink(tmp)

# 열고 닫는 태그 수가 맞는지 거칠게 확인
for tag in ('div', 'script', 'style', 'button'):
    o = len(re.findall(rf'<{tag}[\s>]', html)) + (len(re.findall(rf'<{tag}>', html)) if False else 0)
    c = len(re.findall(rf'</{tag}>', html))
    print(f'  <{tag}> {o} / </{tag}> {c}', '←불일치' if o != c else '')
sys.exit(1 if bad else 0)
