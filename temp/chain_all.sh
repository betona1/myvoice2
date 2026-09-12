#!/bin/bash
until grep -aq "카드 끝" /app/temp/card_loop.log 2>/dev/null; do sleep 120; done
sleep 20; bash /app/temp/ex_loop.sh
sleep 20; bash /app/temp/selfcheck.sh
