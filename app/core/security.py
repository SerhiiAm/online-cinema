import enum
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()

# Verified against when the email is unknown, so a failed login takes
# the same time whether or not the user exists (no email enumeration).
DUMMY_PASSWORD_HASH = password_hash.hash("dummy-password")

TOKEN_BYTES = 32


class TokenType(str, enum.Enum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class RefreshTokenData:
    token: str
    jti: str
    expires_at: datetime


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    # sha256 instead of argon2: the token is random (not a guessable password),
    # so a fast unsalted hash is safe, and the same input always gives
    # the same hash, so the DB row can be found by it.
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _encode_token(payload: dict[str, Any]) -> str:
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def create_access_token(user_id: int) -> str:
    now = _now()
    payload = {
        "sub": str(user_id),
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return _encode_token(payload)


def create_refresh_token(user_id: int) -> RefreshTokenData:
    now = _now()
    jti = uuid.uuid4().hex
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": TokenType.REFRESH.value,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    return RefreshTokenData(
        token=_encode_token(payload), jti=jti, expires_at=expires_at
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    required = ["sub", "type", "iat", "exp"]
    if expected_type is TokenType.REFRESH:
        required.append("jti")

    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": required},
    )
    if payload["type"] != expected_type.value:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload
