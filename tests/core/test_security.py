import math
import re

import pytest

from app.core.security import (
    TOKEN_BYTES,
    generate_token,
    hash_password,
    verify_password,
)

PASSWORD = "Str0ng!pass"
URL_SAFE_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")


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
