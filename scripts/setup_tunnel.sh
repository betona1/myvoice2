#!/usr/bin/env bash
# myvoice.901planner.cloud → 88서버 myvoice2(:9092) Cloudflare Tunnel 구성
# 선행조건: cloudflared tunnel login (브라우저 인증) 완료 → ~/.cloudflared/cert.pem 존재
set -euo pipefail

TUNNEL_NAME="myvoice-88"
DOMAIN="myvoice.901planner.cloud"
ORIGIN="https://localhost:9092"   # myvoice2는 자체서명 HTTPS

[ -f ~/.cloudflared/cert.pem ] || { echo "[FAIL] cert.pem 없음 — 먼저 'cloudflared tunnel login' 실행"; exit 1; }

echo "=== 1. 터널 생성 (이미 있으면 재사용) ==="
if cloudflared tunnel list | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
    echo "  기존 터널 '$TUNNEL_NAME' 재사용"
else
    cloudflared tunnel create "$TUNNEL_NAME"
fi
UUID=$(cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n {print $1}')
echo "  UUID: $UUID"

echo "=== 2. config.yml 작성 ==="
cat > ~/.cloudflared/config.yml <<CFG
tunnel: $UUID
credentials-file: /home/joacham/.cloudflared/$UUID.json

ingress:
  - hostname: $DOMAIN
    service: $ORIGIN
    originRequest:
      noTLSVerify: true       # 자체서명 인증서
      connectTimeout: 30s
      # TTS 합성·학습은 오래 걸림 → 스트리밍 타임아웃 여유
      httpHostHeader: $DOMAIN
  - service: http_status:404
CFG
cat ~/.cloudflared/config.yml

echo "=== 3. DNS 라우팅 ($DOMAIN → 새 터널) ==="
# ⚠️ 기존 터널 '901planner'(2eee5bbb-…)가 901planner.cloud apex 등 다른 호스트네임을
#    정상 서빙 중이다. 여기서는 'myvoice' 레코드 하나만 덮어쓰므로 apex는 영향 없음.
#    (기존 myvoice 레코드는 죽은 80서버를 가리켜 523 반환 중이었음)
cloudflared tunnel route dns --overwrite-dns "$TUNNEL_NAME" "$DOMAIN"

echo "=== 4. systemd 서비스 등록 (부팅 시 자동 기동) ==="
sudo cloudflared --config /home/joacham/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
sleep 5
sudo systemctl status cloudflared --no-pager | head -12

echo
echo "=== 5. 검증 ==="
curl -sS "https://$DOMAIN/" -o /dev/null -w "  https://$DOMAIN/ → %{http_code}\n" || echo "  (DNS 전파 대기중일 수 있음)"
echo "  완료. 구글 로그인 테스트: https://$DOMAIN/login"
