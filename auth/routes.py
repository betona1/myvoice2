"""
auth/routes.py
인증 관련 API 엔드포인트
"""

import re
from fastapi import APIRouter, Form, HTTPException, Response, Request
from fastapi.responses import RedirectResponse

from auth.security import (
    hash_password, verify_password, create_access_token, generate_verification_token
)
from auth.email_service import send_verification_email
from auth.google_oauth import get_google_auth_url, exchange_google_code
from auth.dependencies import get_current_user
from database.db import (
    create_user, get_user_by_email, get_user_by_google_id,
    update_user, save_verification_token, verify_email_token,
    get_all_users, get_user_by_id
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _set_token_cookie(response: Response, token: str):
    """JWT 토큰을 httponly 쿠키에 설정"""
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=False,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 3600,  # 7일
        path="/",
    )


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
):
    """회원가입 (이메일 인증 필요)"""
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "올바른 이메일 형식이 아닙니다")
    if len(password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다")
    if not display_name.strip():
        raise HTTPException(400, "이름을 입력해주세요")

    existing = await get_user_by_email(email)
    if existing:
        raise HTTPException(409, "이미 가입된 이메일입니다")

    hashed = hash_password(password)

    # SMTP 설정 확인 — 없으면 자동 인증 (베타 기간)
    import os
    smtp_configured = bool(os.getenv("SMTP_USER")) and bool(os.getenv("SMTP_PASSWORD"))
    auto_verify = not smtp_configured

    user_id = await create_user(
        email=email,
        password_hash=hashed,
        display_name=display_name.strip(),
        auth_provider="email",
        email_verified=1 if auto_verify else 0,
        level=200,
        plan="free",
        user_type="general",
    )

    if auto_verify:
        # SMTP 미설정 — 자동 인증 (베타 기간)
        return {"message": "가입 완료! 바로 로그인 가능합니다.", "user_id": user_id, "auto_verified": True}

    # 인증 이메일 발송
    token = generate_verification_token()
    await save_verification_token(user_id, token)

    base_url = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    if host:
        base_url = f"{proto}://{host}"

    await send_verification_email(email, token, base_url)

    return {"message": "가입 완료! 이메일 인증 링크를 확인해주세요.", "user_id": user_id}


@router.post("/login")
async def login(
    response: Response,
    email: str = Form(...),
    password: str = Form(...),
):
    """로그인"""
    email = email.strip().lower()
    user = await get_user_by_email(email)
    if not user:
        raise HTTPException(401, "이메일 또는 비밀번호가 잘못되었습니다")

    if not user.get("password_hash"):
        raise HTTPException(401, "Google 계정으로 가입된 이메일입니다. Google 로그인을 이용해주세요.")

    if not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "이메일 또는 비밀번호가 잘못되었습니다")

    if not user.get("email_verified"):
        raise HTTPException(403, "이메일 인증이 완료되지 않았습니다. 메일함을 확인해주세요.")

    if not user.get("is_active"):
        raise HTTPException(403, "비활성화된 계정입니다. 관리자에게 문의하세요.")

    token = create_access_token({
        "sub": str(user["id"]),
        "email": user["email"],
        "level": user["level"],
        "name": user["display_name"],
        "plan": user.get("plan", "free"),
        "user_type": user.get("user_type", "general"),
    })

    _set_token_cookie(response, token)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "level": user["level"],
            "plan": user.get("plan", "free"),
            "user_type": user.get("user_type", "general"),
        }
    }


@router.get("/verify-email")
async def verify_email(token: str):
    """이메일 인증 처리"""
    user_id = await verify_email_token(token)
    if not user_id:
        return RedirectResponse("/login?error=invalid_token", status_code=302)
    return RedirectResponse("/login?verified=true", status_code=302)


@router.post("/resend-verification")
async def resend_verification(request: Request, email: str = Form(...)):
    """인증 이메일 재발송"""
    email = email.strip().lower()
    user = await get_user_by_email(email)
    if not user:
        return {"message": "해당 이메일로 인증 링크를 재발송했습니다."}  # 보안상 존재 여부 노출 안 함
    if user.get("email_verified"):
        return {"message": "이미 인증된 계정입니다."}

    token = generate_verification_token()
    await save_verification_token(user["id"], token)

    base_url = str(request.base_url).rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    if host:
        base_url = f"{proto}://{host}"

    await send_verification_email(email, token, base_url)
    return {"message": "인증 링크를 재발송했습니다."}


@router.get("/google")
async def google_login():
    """Google OAuth2 로그인 시작"""
    url = get_google_auth_url()
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(request: Request, response: Response, code: str = ""):
    """Google OAuth2 콜백"""
    if not code:
        return RedirectResponse("/login?error=google_failed", status_code=302)

    user_info = await exchange_google_code(code)
    if not user_info:
        return RedirectResponse("/login?error=google_failed", status_code=302)

    email = user_info["email"]
    google_id = user_info["google_id"]
    name = user_info["name"] or email.split("@")[0]

    # 기존 사용자 확인
    user = await get_user_by_google_id(google_id)
    if not user:
        user = await get_user_by_email(email)

    if user:
        # 기존 사용자 — Google ID 업데이트
        if not user.get("google_id"):
            await update_user(user["id"], google_id=google_id)
    else:
        # 새 사용자 생성 (Google 계정은 이메일 인증 자동 완료)
        user_id = await create_user(
            email=email,
            password_hash=None,
            display_name=name,
            auth_provider="google",
            google_id=google_id,
            email_verified=1,
            level=200,
            plan="free",
            user_type="general",
        )
        user = {"id": user_id, "email": email, "display_name": name, "level": 200,
                "plan": "free", "user_type": "general"}

    token = create_access_token({
        "sub": str(user["id"]),
        "email": user["email"],
        "level": user["level"],
        "name": user["display_name"],
        "plan": user.get("plan", "free"),
        "user_type": user.get("user_type", "general"),
    })

    redirect = RedirectResponse("/", status_code=302)
    redirect.set_cookie(
        key="access_token",
        value=token,
        httponly=False,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )
    return redirect


@router.get("/me")
async def get_me(request: Request):
    """현재 로그인 사용자 정보"""
    user = await get_current_user(request)
    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "level": user["level"],
            "plan": user.get("plan", "free"),
            "user_type": user.get("user_type", "general"),
            "auth_provider": user.get("auth_provider", "email"),
            "created_at": user.get("created_at"),
        }
    }


@router.post("/logout")
async def logout(response: Response):
    """로그아웃 (쿠키 삭제)"""
    response.delete_cookie("access_token", path="/")
    return {"message": "로그아웃 완료"}


@router.get("/users")
async def list_users(request: Request):
    """전체 사용자 목록 (관리자 전용)"""
    user = await get_current_user(request)
    if user["level"] > 10:
        raise HTTPException(403, "권한이 없습니다")
    users = await get_all_users()
    return {"users": users}


@router.post("/users/{user_id}/status")
async def set_user_status(user_id: int, is_active: int = Form(...), request: Request = None):
    """사용자 활성/비활성 토글 (관리자 전용)"""
    admin = await get_current_user(request)
    if admin["level"] > 10:
        raise HTTPException(403, "권한이 없습니다")
    if is_active not in (0, 1):
        raise HTTPException(400, "is_active는 0 또는 1이어야 합니다")
    await update_user(user_id, is_active=is_active)
    status_text = "활성화" if is_active else "비활성화"
    return {"message": f"사용자 {user_id} {status_text} 완료"}


@router.post("/users/{user_id}/level")
async def set_user_level(user_id: int, level: int = Form(...), request: Request = None):
    """사용자 등급 변경 (관리자 전용)"""
    admin = await get_current_user(request)
    if admin["level"] > 10:
        raise HTTPException(403, "권한이 없습니다")
    if level < 0 or level > 255:
        raise HTTPException(400, "등급은 0~255 사이여야 합니다")
    await update_user(user_id, level=level)
    return {"message": f"사용자 {user_id} 등급을 {level}로 변경"}


VALID_PLANS = {"free", "beta", "basic", "pro", "max"}
VALID_USER_TYPES = {"general", "voice", "group", "vip"}


@router.post("/users/{user_id}/plan")
async def set_user_plan(user_id: int, plan: str = Form(...), request: Request = None):
    """사용자 구독 플랜 변경 (관리자 전용)"""
    admin = await get_current_user(request)
    if admin["level"] > 10:
        raise HTTPException(403, "권한이 없습니다")
    if plan not in VALID_PLANS:
        raise HTTPException(400, f"플랜은 {', '.join(VALID_PLANS)} 중 하나여야 합니다")
    await update_user(user_id, plan=plan)
    return {"message": f"사용자 {user_id} 플랜을 {plan}으로 변경"}


@router.post("/users/{user_id}/user-type")
async def set_user_type(user_id: int, user_type: str = Form(...), request: Request = None):
    """사용자 유형 변경 (관리자 전용)"""
    admin = await get_current_user(request)
    if admin["level"] > 10:
        raise HTTPException(403, "권한이 없습니다")
    if user_type not in VALID_USER_TYPES:
        raise HTTPException(400, f"유형은 {', '.join(VALID_USER_TYPES)} 중 하나여야 합니다")
    await update_user(user_id, user_type=user_type)
    return {"message": f"사용자 {user_id} 유형을 {user_type}으로 변경"}


@router.post("/impersonate/{user_id}")
async def impersonate_user(user_id: int, response: Response, request: Request):
    """대리 로그인 — 슈퍼 관리자(level 0)만 가능"""
    admin = await get_current_user(request)
    if admin["level"] > 10:
        raise HTTPException(403, "관리자만 사용 가능합니다")

    target = await get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")

    token = create_access_token({
        "sub": str(target["id"]),
        "email": target["email"],
        "level": target["level"],
        "name": target["display_name"],
        "plan": target.get("plan", "free"),
        "user_type": target.get("user_type", "general"),
    })

    _set_token_cookie(response, token)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": target["id"],
            "email": target["email"],
            "display_name": target["display_name"],
            "level": target["level"],
        }
    }
