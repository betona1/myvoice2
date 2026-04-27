"""
auth/dependencies.py
FastAPI 의존성 — 인증 체크
"""

from fastapi import Request, HTTPException
from auth.security import decode_access_token
from database.db import get_user_by_id


async def get_current_user(request: Request) -> dict:
    """현재 로그인 사용자 반환 (실패 시 401)"""
    token = None

    # 1) Authorization 헤더
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # 2) 쿠키 fallback
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(401, "로그인이 필요합니다")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(401, "토큰이 만료되었거나 유효하지 않습니다")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "유효하지 않은 토큰")

    user = await get_user_by_id(int(user_id))
    if not user:
        raise HTTPException(401, "사용자를 찾을 수 없습니다")

    if not user.get("is_active"):
        raise HTTPException(403, "비활성화된 계정입니다")

    return user


async def get_optional_user(request: Request):
    """로그인 선택적 — 비로그인 시 None 반환"""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
