import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    TOKEN_BYTES,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_token,
    hash_password,
    verify_password,
)

PASSWORD = "Str0ng!pass"
URL_SAFE_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")
USER_ID = 5
WRONG_SECRET_KEY = "another-secret-key-that-is-long-enough-0123456789"


def _make_claims(token_type: TokenType) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(USER_ID),
        "type": token_type.value,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    if token_type is TokenType.REFRESH:
        claims["jti"] = uuid.uuid4().hex
    return claims


def _encode(
    claims: dict[str, Any], key: str = settings.jwt_secret_key
) -> str:
    return jwt.encode(claims, key, algorithm=settings.jwt_algorithm)


@pytest.fixture(scope="module")
def hashed_password() -> str:
    return hash_password(PASSWORD)


def test_hash_password_returns_argon2_hash(hashed_password: str) -> None:
    assert hashed_password != PASSWORD
    assert hashed_password.startswith("$argon2id$")


def test_hash_password_uses_random_salt(hashed_password: str) -> None:
    assert hash_password(PASSWORD) != hashed_password


def test_verify_password_accepts_correct_password(hashed_password: str) -> None:
    assert verify_password(PASSWORD, hashed_password) is True


@pytest.mark.parametrize(
    "wrong_password",
    ["Str0ng!pas", "str0ng!pass", ""],
    ids=["one-char-missing", "different-case", "empty"],
)
def test_verify_password_rejects_wrong_password(
    hashed_password: str, wrong_password: str
) -> None:
    assert verify_password(wrong_password, hashed_password) is False


def test_generate_token_is_url_safe_with_expected_length() -> None:
    token = generate_token()

    assert URL_SAFE_CHARS.match(token)
    assert len(token) == math.ceil(TOKEN_BYTES * 4 / 3)


def test_generate_token_returns_unique_values() -> None:
    tokens = {generate_token() for _ in range(1000)}

    assert len(tokens) == 1000


def test_create_access_token_has_expected_claims() -> None:
    payload = decode_token(create_access_token(USER_ID), TokenType.ACCESS)

    assert payload["sub"] == str(USER_ID)
    assert payload["type"] == TokenType.ACCESS.value
    assert payload["exp"] - payload["iat"] == (
        settings.access_token_expire_minutes * 60
    )
    assert "jti" not in payload


def test_create_refresh_token_has_expected_claims() -> None:
    refresh = create_refresh_token(USER_ID)

    payload = decode_token(refresh.token, TokenType.REFRESH)

    assert payload["sub"] == str(USER_ID)
    assert payload["type"] == TokenType.REFRESH.value
    assert payload["jti"] == refresh.jti
    assert payload["exp"] - payload["iat"] == (
        settings.refresh_token_expire_days * 24 * 60 * 60
    )


def test_create_refresh_token_expires_at_matches_exp_claim() -> None:
    refresh = create_refresh_token(USER_ID)

    payload = decode_token(refresh.token, TokenType.REFRESH)

    assert refresh.expires_at.tzinfo == timezone.utc
    assert payload["exp"] == int(refresh.expires_at.timestamp())


def test_create_refresh_token_returns_unique_jti() -> None:
    jtis = {create_refresh_token(USER_ID).jti for _ in range(100)}

    assert len(jtis) == 100


@pytest.mark.parametrize(
    "token_type, expected_type",
    [
        (TokenType.ACCESS, TokenType.REFRESH),
        (TokenType.REFRESH, TokenType.ACCESS),
    ],
    ids=["access-as-refresh", "refresh-as-access"],
)
def test_decode_token_rejects_wrong_token_type(
    token_type: TokenType, expected_type: TokenType
) -> None:
    claims = _make_claims(expected_type)
    claims["type"] = token_type.value

    with pytest.raises(jwt.InvalidTokenError, match="Unexpected token type"):
        decode_token(_encode(claims), expected_type)


def test_decode_token_rejects_expired_token() -> None:
    claims = _make_claims(TokenType.ACCESS)
    claims["exp"] = claims["iat"] - timedelta(seconds=1)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(_encode(claims), TokenType.ACCESS)


def test_decode_token_rejects_wrong_secret_key() -> None:
    token = _encode(_make_claims(TokenType.ACCESS), key=WRONG_SECRET_KEY)

    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token, TokenType.ACCESS)


def test_decode_token_rejects_unsigned_token() -> None:
    token = jwt.encode(_make_claims(TokenType.ACCESS), "", algorithm="none")

    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_token(token, TokenType.ACCESS)


@pytest.mark.parametrize(
    "token_type, missing_claim",
    [
        (TokenType.ACCESS, "sub"),
        (TokenType.ACCESS, "type"),
        (TokenType.ACCESS, "iat"),
        (TokenType.ACCESS, "exp"),
        (TokenType.REFRESH, "jti"),
    ],
)
def test_decode_token_rejects_missing_claim(
    token_type: TokenType, missing_claim: str
) -> None:
    claims = _make_claims(token_type)
    del claims[missing_claim]

    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(_encode(claims), token_type)


def test_decode_token_rejects_malformed_token() -> None:
    with pytest.raises(jwt.DecodeError):
        decode_token("not-a-jwt", TokenType.ACCESS)
