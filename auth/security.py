"""
auth/security.py
비밀번호 해싱 + JWT 토큰 관리
"""

import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt, JWTError

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_hex(32))
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7일


def _truncate(password: str) -> str:
    """bcrypt 72바이트 제한 대응 — SHA256 pre-hash"""
    if len(password.encode("utf-8")) > 72:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()
    return password


def hash_password(password: str) -> str:
    """비밀번호 해싱"""
    return pwd_ctx.hash(_truncate(password))


def verify_password(plain: str, hashed: str) -> bool:
    """비밀번호 검증"""
    return pwd_ctx.verify(_truncate(plain), hashed)


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """JWT 액세스 토큰 생성"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """JWT 토큰 디코딩 (실패 시 None)"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def generate_verification_token() -> str:
    """이메일 인증용 랜덤 토큰"""
    return secrets.token_urlsafe(32)
