#!/bin/bash
until grep -aq "예문 끝" /app/temp/ex_loop.log 2>/dev/null; do sleep 60; done
sleep 20
bash /app/temp/selfcheck.sh
