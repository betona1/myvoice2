"""
auth/email_service.py
이메일 인증 발송
"""

import os
import aiosmtplib
from email.message import EmailMessage


async def send_verification_email(to_email: str, token: str, base_url: str = ""):
    """인증 이메일 발송"""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        print("[AUTH] SMTP 설정 없음 — 이메일 발송 건너뜀")
        return False

    if not base_url:
        base_url = os.getenv("BASE_URL", "https://myvoice.901planner.cloud")

    verify_url = f"{base_url}/api/auth/verify-email?token={token}"

    msg = EmailMessage()
    msg["Subject"] = "[MyVoice] 이메일 인증을 완료해주세요"
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(f"아래 링크를 클릭하여 이메일 인증을 완료해주세요:\n\n{verify_url}\n\n이 링크는 24시간 동안 유효합니다.")
    msg.add_alternative(f"""
    <html>
    <body style="font-family:sans-serif;background:#07090f;color:#eef0f8;padding:2rem;">
      <div style="max-width:480px;margin:0 auto;background:rgba(13,16,32,0.9);border:1px solid rgba(124,109,250,0.3);border-radius:16px;padding:2rem;">
        <h2 style="background:linear-gradient(135deg,#7c6dfa,#4fd1c5);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:1rem;">MyVoice 이메일 인증</h2>
        <p style="color:#7a8299;font-size:0.9rem;line-height:1.6;">아래 버튼을 클릭하여 이메일 인증을 완료해주세요.</p>
        <a href="{verify_url}" style="display:inline-block;margin:1.5rem 0;padding:0.7rem 1.5rem;background:linear-gradient(135deg,#7c6dfa,#4fd1c5);color:#fff;text-decoration:none;border-radius:10px;font-weight:600;">이메일 인증하기</a>
        <p style="color:#4a5270;font-size:0.75rem;">이 링크는 24시간 동안 유효합니다.</p>
      </div>
    </body>
    </html>
    """, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_pass,
            start_tls=True,
        )
        print(f"[AUTH] 인증 이메일 발송: {to_email}")
        return True
    except Exception as e:
        print(f"[AUTH] 이메일 발송 실패: {e}")
        return False
