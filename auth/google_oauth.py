"""
auth/google_oauth.py
Google OAuth2 로그인 처리
"""

import os
import httpx
from urllib.parse import urlencode

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://myvoice.901planner.cloud/api/auth/google/callback")


def get_google_auth_url() -> str:
    """Google OAuth2 인증 URL 생성"""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


async def exchange_google_code(code: str) -> dict:
    """인증 코드를 토큰으로 교환 → 사용자 정보 반환"""
    async with httpx.AsyncClient() as client:
        # 코드 → 토큰 교환
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": GOOGLE_REDIRECT_URI,
            },
        )
        if token_res.status_code != 200:
            return None

        tokens = token_res.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return None

        # 사용자 정보 조회
        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if user_res.status_code != 200:
            return None

        info = user_res.json()
        return {
            "email": info.get("email", ""),
            "name": info.get("name", ""),
            "google_id": info.get("id", ""),
            "picture": info.get("picture", ""),
        }
